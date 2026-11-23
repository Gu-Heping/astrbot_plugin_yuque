"""
NovaBot 智能推送模块
基于 LLM 判断文档更新价值，智能推送给订阅者
"""

import json
import re
from collections import deque
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from astrbot.api import logger

from .llm_utils import call_llm
from .prompts import FIRST_PUSH_PROMPT, UPDATE_PUSH_PROMPT
from .token_monitor import FEATURE_PUSH

if TYPE_CHECKING:
    from astrbot.api.star import Context

    from .subscribe import SubscriptionManager
    from .token_monitor import TokenMonitor


# 默认配置值
DEFAULT_MIN_DIFF_CHARS = 100
DEFAULT_MAX_CONTENT_LEN = 2000
FIRST_PUSH_NORMAL_MIN_CHARS = 50
NO_BODY_CHANGE = "[无正文变更]"
DIFF_UNAVAILABLE_PREFIXES = ("[无 Git 仓库", "[获取 diff 失败")
UPDATE_CONTEXT_UNCLEAR = "[更新上下文不足]"

class PushNotifier:
    """智能推送管理器

    核心流程：
    1. 预处理检查（跳过无意义的变更）
    2. tool_loop_agent 子会话判断推送价值
    3. 生成高信息密度摘要
    4. 推送给匹配的订阅者
    """

    def __init__(
        self,
        docs_dir: Path,
        data_dir: Path,
        context: "Context",
        subscription_manager: "SubscriptionManager",
        config: dict,
        token_monitor: Optional["TokenMonitor"] = None,
    ):
        """初始化推送管理器

        Args:
            docs_dir: 文档目录
            data_dir: 数据目录
            context: AstrBot Context
            subscription_manager: 订阅管理器
            config: 配置字典
            token_monitor: Token 监控器（可选）
        """
        self.docs_dir = docs_dir
        self.data_dir = data_dir
        self.context = context
        self.subscription_manager = subscription_manager
        self.config = config
        self.token_monitor = token_monitor

        self.last_push_file = data_dir / "last_push.json"

        # 从配置读取参数
        self.min_diff_chars = config.get("push_min_diff_chars", DEFAULT_MIN_DIFF_CHARS)
        self.max_content_len = config.get("push_max_content_len", DEFAULT_MAX_CONTENT_LEN)

    def load_last_push(self) -> dict[str, str]:
        """加载上次推送记录

        Returns:
            {yuque_id: commit_hash} 映射
        """
        if self.last_push_file.exists():
            try:
                return json.loads(self.last_push_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                logger.warning(f"[Push] 推送记录文件损坏: {e}")
                return {}
        return {}

    def save_last_push(self, data: dict):
        """保存推送记录"""
        self.last_push_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def _format_diff_for_paths(self, diff: str, doc_path) -> str:
        """为路径变更 diff 添加上下文，避免被误读为新建整篇文档。"""
        if isinstance(doc_path, str):
            return diff
        paths = [str(path) for path in doc_path if path]
        if len(set(paths)) <= 1 or not diff:
            return diff
        return (
            "[文档路径或标题发生变化；以下是同一篇文档在旧路径与新路径之间的 diff，"
            "不代表从空文件新建]\n\n"
            f"{diff}"
        )

    @staticmethod
    def _is_diff_file_header(raw_line: str, sign: str) -> bool:
        """Return whether a diff line is a file header, not body content."""
        if sign == "+":
            return raw_line.startswith("+++ b/") or raw_line == "+++ /dev/null"
        if sign == "-":
            return raw_line.startswith("--- a/") or raw_line == "--- /dev/null"
        return False

    def _is_non_body_diff_line(
        self,
        line: str,
        *,
        in_frontmatter: bool = False,
        in_generated_metadata_table: bool = False,
        in_html_comment: bool = False,
    ) -> bool:
        """判断 diff 中的变更行是否只是元数据或结构信息。"""
        stripped = line.strip()
        if not stripped or stripped == "---":
            return True
        if in_html_comment or stripped.startswith(("<!--", "-->")):
            return True
        if in_generated_metadata_table:
            return True
        if in_frontmatter:
            return True
        return False

    @staticmethod
    def _hunk_has_frontmatter_delimiter(hunk_lines: list[str]) -> bool:
        """Check whether a hunk shows part of the generated frontmatter fence."""
        return any(
            line[:1] in ("+", "-", " ") and line[1:].strip() == "---"
            for line in hunk_lines
        )

    @staticmethod
    def _looks_like_frontmatter_line(line: str) -> bool:
        stripped = line.strip()
        return bool(
            not stripped
            or re.match(r"^[A-Za-z_][A-Za-z0-9_-]*:", stripped)
            or stripped.startswith(("- ", "  ", "'"))
        )

    @staticmethod
    def _is_generated_metadata_header(line: str) -> bool:
        stripped = line.strip()
        return (
            stripped.startswith("|")
            and stripped.endswith("|")
            and "作者" in stripped
            and "创建时间" in stripped
            and "更新时间" in stripped
        )

    def _hunk_starts_in_frontmatter(self, hunk_lines: list[str], start_line: int) -> bool:
        """Infer whether a partial hunk starts inside NovaBot frontmatter."""
        before_fence: list[str] = []
        after_fence: list[str] = []
        saw_fence = False

        for raw_line in hunk_lines[1:]:
            if not raw_line or raw_line[0] not in ("+", "-", " "):
                continue
            line = raw_line[1:]
            stripped = line.strip()
            if stripped == "---":
                saw_fence = True
                continue
            if saw_fence:
                if stripped:
                    after_fence.append(line)
            else:
                before_fence.append(line)

        if not saw_fence:
            return False
        if start_line <= 1:
            return True

        visible_before = [line for line in before_fence if line.strip()]
        if visible_before:
            return all(self._looks_like_frontmatter_line(line) for line in visible_before)

        return any(self._is_generated_metadata_header(line) for line in after_fence[:4])

    @staticmethod
    def _split_diff_hunks(diff: str) -> list[list[str]]:
        """Split a unified diff into hunks with their hunk headers."""
        hunks: list[list[str]] = []
        current: list[str] = []
        for line in diff.splitlines():
            if line.startswith("@@"):
                if current:
                    hunks.append(current)
                current = [line]
            elif current:
                current.append(line)
        if current:
            hunks.append(current)
        return hunks

    def _extract_body_change_lines(self, diff: str, sign: str, *, include_context: bool = True) -> str:
        """从 Git diff 中提取正文变更行，跳过 diff 头和开头 frontmatter。"""
        lines: list[str] = []
        for hunk in self._split_diff_hunks(diff):
            header = hunk[0]
            match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", header)
            if not match:
                continue
            old_line = int(match.group(1))
            new_line = int(match.group(2))
            hunk_start_line = min(old_line, new_line)
            frontmatter_possible = (
                hunk_start_line <= 30
                and self._hunk_has_frontmatter_delimiter(hunk)
                and self._hunk_starts_in_frontmatter(hunk, hunk_start_line)
            )
            in_frontmatter = frontmatter_possible
            frontmatter_delimiters = 1 if frontmatter_possible and min(old_line, new_line) > 1 else 0
            in_generated_metadata_table_region = False
            allow_generated_metadata_table = False
            html_comment_active = False
            recent_context: deque[str] = deque(maxlen=2)
            recent_heading: Optional[str] = None
            context_emitted_for_block = False

            def append_context() -> None:
                nonlocal context_emitted_for_block
                if context_emitted_for_block:
                    return
                for context_line in ([recent_heading] if recent_heading else []) + list(recent_context):
                    if context_line and (not lines or lines[-1] != context_line):
                        lines.append(context_line)
                context_emitted_for_block = True

            for raw_line in hunk[1:]:
                if not raw_line:
                    continue

                prefix = raw_line[0]
                if prefix not in ("+", "-", " "):
                    continue

                line = raw_line[1:]
                if prefix in ("-", " "):
                    old_line += 1
                if prefix in ("+", " "):
                    new_line += 1

                stripped_line = line.strip()
                if frontmatter_possible and stripped_line == "---" and frontmatter_delimiters < 2:
                    frontmatter_delimiters += 1
                    in_frontmatter = frontmatter_delimiters == 1
                    if frontmatter_delimiters == 2:
                        in_frontmatter = False
                        allow_generated_metadata_table = True

                starts_html_comment = stripped_line.startswith("<!--")
                in_html_comment = html_comment_active or starts_html_comment
                if starts_html_comment and "-->" not in stripped_line:
                    html_comment_active = True
                if html_comment_active and "-->" in stripped_line:
                    html_comment_active = False

                is_metadata_header = allow_generated_metadata_table and self._is_generated_metadata_header(line)
                in_generated_metadata_table = False
                if not in_frontmatter and is_metadata_header:
                    in_generated_metadata_table_region = True
                    in_generated_metadata_table = True
                elif (
                    not in_frontmatter
                    and in_generated_metadata_table_region
                    and stripped_line.startswith("|")
                    and stripped_line.endswith("|")
                ):
                    in_generated_metadata_table = True
                elif not (stripped_line.startswith("|") and stripped_line.endswith("|")):
                    in_generated_metadata_table_region = False

                is_non_body = self._is_non_body_diff_line(
                    line,
                    in_frontmatter=in_frontmatter,
                    in_generated_metadata_table=in_generated_metadata_table,
                    in_html_comment=in_html_comment,
                )

                if prefix == " " and not is_non_body:
                    context_emitted_for_block = False
                    if stripped_line.startswith("#"):
                        recent_heading = line
                        recent_context.clear()
                    else:
                        recent_context.append(line)
                    continue

                if prefix != sign or self._is_diff_file_header(raw_line, sign):
                    continue

                if is_non_body:
                    continue
                if include_context:
                    append_context()
                lines.append(line)
        return "\n".join(lines).strip()

    @staticmethod
    def _clip_text(text: str, limit: int) -> str:
        """按字符预算裁剪文本。"""
        if limit <= 0:
            return ""
        if len(text) <= limit:
            return text
        suffix = "\n... (正文变更已截断)"
        if limit <= len(suffix):
            return suffix[-limit:]
        return text[: max(0, limit - len(suffix))].rstrip() + suffix

    def _prepare_update_diff_for_llm(self, diff: str) -> str:
        """为更新推送准备正文优先的 diff 输入。"""
        if diff.startswith(DIFF_UNAVAILABLE_PREFIXES):
            return diff

        added = self._extract_body_change_lines(diff, "+")
        removed = self._extract_body_change_lines(diff, "-")

        if not added and not removed:
            return NO_BODY_CHANGE

        parts = [
            "以下内容从 Git diff 中抽取，已忽略路径移动、文件名变化、frontmatter、作者、时间等非正文信息。",
            "请只基于正文内容总结主要变更；不要把路径移动、目录调整、元数据同步作为主要变更。",
        ]
        fixed_text_len = len("\n".join(parts)) + len("\n\n新增或修改后的正文片段：\n")
        if removed:
            fixed_text_len += len("\n\n被替换或删除的旧正文片段：\n")
        content_budget = max(0, self.max_content_len - fixed_text_len)
        removed_budget = min(max(80, content_budget // 3), len(removed), content_budget) if removed else 0
        added_budget = max(0, content_budget - removed_budget)

        if added:
            parts.extend(["", "新增或修改后的正文片段：", self._clip_text(added, added_budget)])
        if removed:
            parts.extend(["", "被替换或删除的旧正文片段：", self._clip_text(removed, removed_budget)])
        return "\n".join(parts)

    @staticmethod
    def _plain_text_len(text: str) -> int:
        return len(re.sub(r"\s+", "", text or ""))

    @staticmethod
    def effective_char_count(text: str) -> int:
        """Count non-whitespace characters in already-cleaned document body."""
        return len(re.sub(r"\s+", "", text or ""))

    @staticmethod
    def _normalized_evidence(text: str) -> str:
        return re.sub(r"\s+", "", str(text or "")).strip()

    @staticmethod
    def _string_list(value: object) -> list[str]:
        """Normalize only JSON arrays; scalar strings are invalid model output."""
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    def _validate_first_push_result(
        self,
        result: dict,
        body: str,
        *,
        is_short_body: bool,
    ) -> tuple[bool, dict]:
        """Require directly verifiable body evidence for a first-push decision."""
        highlights = self._string_list(result.get("highlights", []))
        evidence = self._string_list(result.get("evidence", []))
        change_type = str(result.get("change_type", "unclear") or "unclear")
        confidence = str(result.get("confidence", "low") or "low").lower()
        reason = str(result.get("reason", "") or "")
        should_push = result.get("should_push") is True

        summary = {
            "highlights": highlights,
            "evidence": evidence,
            "reason": reason,
            "change_type": change_type,
            "confidence": confidence,
        }
        if not should_push:
            return False, summary

        rejection_reason = ""
        if not highlights or not evidence:
            rejection_reason = "首次推送缺少可验证的正文摘要或证据"
        elif confidence == "low":
            rejection_reason = "首次推送判断置信度低"
        elif is_short_body and change_type != "short_announcement":
            rejection_reason = "短正文未被识别为包含具体事实的简短公告"
        else:
            normalized_body = self._normalized_evidence(body)
            invalid_evidence = [
                item
                for item in evidence
                if self._normalized_evidence(item) not in normalized_body
            ]
            if invalid_evidence:
                rejection_reason = "首次推送证据无法在正文中验证"

        if rejection_reason:
            summary.update(
                {
                    "highlights": [],
                    "reason": rejection_reason,
                    "change_type": "unclear",
                    "confidence": "low",
                }
            )
            return False, summary
        return True, summary

    @staticmethod
    def _has_draft_marker(text: str) -> bool:
        return bool(re.search(r"(TODO|todo|待写|待补充|未完成|占位|草稿|WIP)", text or ""))

    @staticmethod
    def _has_completion_marker(text: str) -> bool:
        return bool(re.search(r"(结论|总结|步骤|流程|完成|确定|产出|成果|实现|示例)", text or ""))

    @staticmethod
    def _has_structural_addition(text: str) -> bool:
        return bool(
            re.search(r"(^|\n)\s{0,3}#{1,6}\s+\S", text or "")
            or re.search(r"(^|\n)\s*```", text or "")
            or re.search(r"(^|\n)\s*[-*+]\s+\S", text or "")
            or re.search(r"(^|\n)\s*\d+[.、]\s+\S", text or "")
            or re.search(r"(^|\n)\s*\|.+\|", text or "")
        )

    @staticmethod
    def _document_outline(body: str, limit: int = 20) -> str:
        headings = []
        for line in (body or "").splitlines():
            match = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$", line)
            if not match:
                continue
            level = len(match.group(1))
            title = match.group(2).strip()
            headings.append(f"{'  ' * (level - 1)}- {title}")
            if len(headings) >= limit:
                break
        return "\n".join(headings) if headings else "（未发现标题结构）"

    def _changed_sections(self, diff: str, limit: int = 8) -> str:
        sections: list[str] = []
        for hunk in self._split_diff_hunks(diff):
            recent_heading = ""
            for raw_line in hunk[1:]:
                if not raw_line or raw_line[0] not in (" ", "+", "-"):
                    continue
                line = raw_line[1:].strip()
                if line.startswith("#"):
                    recent_heading = line.lstrip("#").strip()
                if raw_line[0] in ("+", "-") and recent_heading and recent_heading not in sections:
                    sections.append(recent_heading)
                    if len(sections) >= limit:
                        return "\n".join(f"- {s}" for s in sections)
        return "\n".join(f"- {s}" for s in sections) if sections else "（无法从 diff 判断章节）"

    def classify_update_change(self, diff: str, current_body: str = "") -> tuple[str, str]:
        """保守分类更新性质，避免把旧段落润色误判为新进展。"""
        prepared = self._prepare_update_diff_for_llm(diff)
        if prepared == NO_BODY_CHANGE:
            return "minor_revision", "只有路径、元数据或无正文变化"
        if prepared.startswith(DIFF_UNAVAILABLE_PREFIXES):
            return "unclear", "无法取得可靠 diff"

        added = self._extract_body_change_lines(diff, "+", include_context=False)
        removed = self._extract_body_change_lines(diff, "-", include_context=False)
        added_len = self._plain_text_len(added)
        removed_len = self._plain_text_len(removed)

        if not added and not removed:
            return "unclear", "没有可判断的正文变更"
        if not current_body.strip():
            return "unclear", "缺少当前全文语境"

        if self._has_draft_marker(current_body) and not self._has_completion_marker(added):
            return "draft_update", "当前全文仍含草稿/待补充标记，本次变更未显示阶段性完成"

        if added and removed and not self._has_structural_addition(added):
            similarity = SequenceMatcher(None, removed, added).ratio()
            if similarity >= 0.58 and added_len <= max(removed_len * 2, removed_len + 120):
                return "minor_revision", "主要是对既有段落的措辞、补句或局部调整"

        if added and self._has_structural_addition(added):
            if removed:
                return "mixed_update", "同时包含旧内容调整和新增结构化内容"
            return "substantive_addition", "新增了结构化正文内容"

        if added_len < max(self.min_diff_chars, 120):
            return "minor_revision", "新增正文较少，倾向局部修订"
        if added and not removed:
            return "substantive_addition", "新增了较多正文内容"
        return "unclear", "变更主要是替换或删除，缺少可确认的新增进展"

    def build_update_context(self, diff: str, current_body: str = "") -> str:
        """构造给 LLM 的更新上下文：全文大纲 + 变更证据。"""
        prepared = self._prepare_update_diff_for_llm(diff)
        if prepared == NO_BODY_CHANGE:
            return NO_BODY_CHANGE
        if prepared.startswith(DIFF_UNAVAILABLE_PREFIXES) and not current_body.strip():
            return UPDATE_CONTEXT_UNCLEAR

        added = self._extract_body_change_lines(diff, "+")
        removed = self._extract_body_change_lines(diff, "-")
        change_type, reason = self.classify_update_change(diff, current_body)

        header = [
            f"预分类：{change_type}",
            f"预分类理由：{reason}",
            "",
            "当前文档大纲：",
            self._document_outline(current_body),
            "",
            "本次变更所在章节：",
            self._changed_sections(diff),
            "",
            "新增或修改后的正文证据：",
            self._clip_text(added or "（无可见新增正文）", self.max_content_len // 2),
        ]
        if removed:
            header.extend([
                "",
                "被替换或删除的旧正文证据（不能当作新增内容总结）：",
                self._clip_text(removed, max(120, self.max_content_len // 4)),
            ])
        header.extend(["", "过滤后的 diff 证据：", prepared])
        return self._clip_text("\n".join(header), max(self.max_content_len, 1200))

    def get_diff(self, doc_id: object, current_commit: str, doc_path) -> tuple[str, bool]:
        """获取与上次推送的 diff

        Args:
            doc_id: 文档 ID
            current_commit: 当前 commit hash
            doc_path: 文档相对路径

        Returns:
            (diff 文本, 是否首次推送)
        """
        from .git_ops import GitOps

        last_push = self.load_last_push()
        last_commit = last_push.get(str(doc_id))

        git = GitOps(self.docs_dir)
        if not last_commit:
            # 推送记录可能因旧版本迁移、写盘失败或 key 变化缺失。
            # 若 Git 中已有父提交，仍可用本次提交的父提交作为保守基线，
            # 避免把已有文档误判成“从空文件新建”。
            if git.has_git():
                parent_commit = git.get_parent_commit(current_commit)
                if parent_commit and git.has_any_path_at_commit(parent_commit, doc_path):
                    try:
                        diff = git.get_diff(parent_commit, current_commit, doc_path, context_lines=100000)
                        logger.info(
                            "[Push] 未找到推送记录，使用当前提交父提交作为 diff 基线: "
                            f"doc_id={doc_id}, parent={parent_commit[:8]}, current={current_commit[:8]}"
                        )
                        return self._format_diff_for_paths(diff or "[无文本变更]", doc_path), False
                    except Exception as e:
                        logger.warning(f"[Push] 使用父提交获取 diff 失败: {e}")

            # Git 无可用父提交时才视为真正首次推送。
            return "[这是新发布的文档，首次推送，无历史 diff 信息]", True

        if last_commit == current_commit:
            # commit 相同，跳过
            return "", False

        if not git.has_git():
            return "[无 Git 仓库，无法获取 diff]", False

        try:
            diff = git.get_diff(last_commit, current_commit, doc_path, context_lines=100000)
            return self._format_diff_for_paths(diff or "[无文本变更]", doc_path), False
        except Exception as e:
            logger.warning(f"[Push] 获取 diff 失败: {e}")
            return f"[获取 diff 失败: {e}]", False

    def pre_check(self, diff: str, is_first_push: bool = False) -> tuple[bool, str]:
        """预处理检查

        Args:
            diff: diff 文本
            is_first_push: 是否首次推送

        Returns:
            (should_skip, reason) 是否跳过，原因
        """
        # 首次推送，直接让 LLM 判断
        if is_first_push:
            return False, ""

        # 正文完全没变化
        if not diff.strip() or diff == "[无文本变更]":
            return True, "正文无变化"
        if diff == NO_BODY_CHANGE:
            return True, "无正文变更"
        if diff.startswith(DIFF_UNAVAILABLE_PREFIXES):
            return False, ""

        # diff 太小
        # 只计算实际变更内容（去掉 diff 元数据）
        actual_diff = re.sub(r'^[+-]{3}.*$', '', diff, flags=re.MULTILINE)
        actual_diff = re.sub(r'^@@.*@@$', '', actual_diff, flags=re.MULTILINE)
        actual_diff = re.sub(r'^[+-]\s*$', '', actual_diff, flags=re.MULTILINE)
        actual_diff = actual_diff.strip()

        if len(actual_diff) < self.min_diff_chars:
            return True, f"变更太小 ({len(actual_diff)} 字符 < {self.min_diff_chars})"

        return False, ""

    async def agent_should_push(
        self,
        doc_info: dict,
        content: str,
        is_first_push: bool = False,
        current_body: str = "",
    ) -> tuple[bool, Optional[dict]]:
        """通过 LLM 判断是否推送

        使用统一的 call_llm 封装和新提示词模板。

        Args:
            doc_info: 文档信息
            content: 文档内容（首次推送为原文，否则为 diff）
            is_first_push: 是否首次推送

        Returns:
            (should_push, summary) 是否推送，摘要信息
        """
        try:
            first_push_chars = 0
            is_short_first_push = False
            if is_first_push:
                first_push_chars = self.effective_char_count(content)
                if first_push_chars == 0:
                    return False, {
                        "highlights": [],
                        "evidence": [],
                        "reason": "有效正文为空，仅包含标题或生成的元数据",
                        "change_type": "empty_body",
                        "confidence": "high",
                    }
                is_short_first_push = first_push_chars < FIRST_PUSH_NORMAL_MIN_CHARS
            else:
                change_type, change_reason = self.classify_update_change(content, current_body)
                if change_type in ("minor_revision", "draft_update", "unclear"):
                    return False, {
                        "highlights": [],
                        "reason": change_reason,
                        "change_type": change_type,
                        "confidence": "medium" if change_type != "unclear" else "low",
                    }
                content = self.build_update_context(content, current_body)
                if content in (NO_BODY_CHANGE, UPDATE_CONTEXT_UNCLEAR):
                    return False, {
                        "highlights": [],
                        "reason": "只有结构或元数据变化，正文信息不足",
                        "change_type": "unclear",
                        "confidence": "low",
                    }

            # 获取 Provider
            prov = self.context.get_using_provider()
            if not prov:
                logger.warning("[Push] 无可用的 LLM Provider")
                return False, {
                    "highlights": [],
                    "evidence": [],
                    "reason": "无可用 LLM，已按保守策略跳过推送",
                    "change_type": "unclear",
                    "confidence": "low",
                }

            # 截断内容避免过长
            if len(content) > self.max_content_len:
                content = content[:self.max_content_len] + "\n... (已截断)"

            # 根据是否首次推送使用不同的提示词
            if is_first_push:
                short_body_rule = (
                    "当前正文少于 50 个非空白字符，默认不推送；只有正文直接给出具体通知、"
                    "时间、行动项、结论或可独立理解的说明时，才可判定为 short_announcement"
                    if is_short_first_push
                    else "当前正文已达到常规长度，但仍须依据正文完成度和具体证据判断"
                )
                prompt = FIRST_PUSH_PROMPT.format(
                    title=doc_info.get("title", "未知"),
                    author=doc_info.get("author", "未知"),
                    book_name=doc_info.get("book_name", "未知"),
                    body_chars=first_push_chars,
                    short_body_rule=short_body_rule,
                    content=content,
                )
                system_prompt = "你是一个知识推送决策专家，服务于 NOVA 社团。"
            else:
                prompt = UPDATE_PUSH_PROMPT.format(
                    title=doc_info.get("title", "未知"),
                    author=doc_info.get("author", "未知"),
                    diff=content
                )
                system_prompt = "你是一个知识文档更新判断专家，服务于 NOVA 社团。"

            result = await call_llm(
                provider=prov,
                prompt=prompt,
                system_prompt=system_prompt,
                require_json=True,
                token_monitor=self.token_monitor,
                feature=FEATURE_PUSH,
            )

            if is_first_push:
                should_push, summary = self._validate_first_push_result(
                    result,
                    content,
                    is_short_body=is_short_first_push,
                )
                logger.info(
                    f"[Push] LLM 判断: should_push={should_push}, "
                    f"reason={summary.get('reason', '')}"
                )
                return should_push, summary

            should_push = result.get("should_push") is True
            highlights = result.get("observable_changes") or result.get("highlights", [])
            reason = result.get("reason", "")
            change_type = result.get("change_type", "substantive_addition" if should_push else "unclear")
            confidence = result.get("confidence", "medium")

            logger.info(f"[Push] LLM 判断: should_push={should_push}, reason={reason}")

            return should_push, {
                "highlights": highlights,
                "reason": reason,
                "change_type": change_type,
                "confidence": confidence,
            }

        except Exception as e:
            logger.error(f"[Push] LLM 判断失败: {e}")
            return False, {
                "highlights": [],
                "reason": f"判断失败，已按保守策略跳过推送: {e}",
                "change_type": "unclear",
                "confidence": "low",
            }

    async def notify_subscribers(self, doc_info: dict, summary: dict):
        """推送通知给订阅者

        Args:
            doc_info: 文档信息
            summary: 摘要信息
        """
        from astrbot.api.event import MessageChain

        # 获取订阅者
        subscribers = self.subscription_manager.get_subscribers(doc_info)
        if not subscribers:
            logger.info("[Push] 无匹配的订阅者")
            return

        # 构建推送消息
        msg = self._format_push_message(doc_info, summary)
        chain = MessageChain().message(msg)

        # 推送到所有目标
        pushed = 0
        for umo, platform_id in subscribers:
            try:
                await self.context.send_message(umo, chain)
                logger.info(f"[Push] 已推送到 {umo}")
                pushed += 1
            except Exception as e:
                logger.error(f"[Push] 推送失败 {umo}: {e}")

        logger.info(f"[Push] 推送完成: {pushed}/{len(subscribers)} 成功")

    def _format_push_message(self, doc_info: dict, summary: dict) -> str:
        """格式化推送消息

        Args:
            doc_info: 文档信息
            summary: 摘要信息

        Returns:
            格式化的消息文本
        """
        title = doc_info.get("title", "未知文档")
        author = doc_info.get("author", "未知作者")
        book_name = doc_info.get("book_name", "")
        highlights = summary.get("highlights", [])
        doc_url = doc_info.get("url", "")

        # 首行：更新者 + 文档 + 知识库
        if book_name:
            header = f"{author} 更新了《{title}》（知识库：{book_name}）"
        else:
            header = f"{author} 更新了《{title}》"

        lines = [header, ""]

        # 变更要点
        if highlights:
            lines.append("主要变更如下：")
            lines.append("")
            for i, h in enumerate(highlights[:5], 1):  # 最多 5 条
                lines.append(f"{i}. {h}")
            lines.append("")

        # 原文链接
        if doc_url:
            lines.append(f"原文：{doc_url}")

        return "\n".join(lines)

    def mark_pushed(self, doc_id: object, commit: str):
        """标记已推送

        Args:
            doc_id: 文档 ID
            commit: commit hash
        """
        data = self.load_last_push()
        data[str(doc_id)] = commit
        self.save_last_push(data)
        logger.info(f"[Push] 已记录推送: doc_id={doc_id}, commit={commit[:8]}")

    def should_enable(self) -> bool:
        """检查是否启用推送"""
        return self.config.get("push_enabled", True)
