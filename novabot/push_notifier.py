"""
NovaBot 智能推送模块
基于 tool_loop_agent 判断文档更新价值，智能推送给订阅者
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from astrbot.api import logger

if TYPE_CHECKING:
    from astrbot.api.star import Context

    from .subscribe import SubscriptionManager


# 默认配置值
DEFAULT_MIN_DIFF_CHARS = 100
DEFAULT_MAX_CONTENT_LEN = 2000


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
    ):
        """初始化推送管理器

        Args:
            docs_dir: 文档目录
            data_dir: 数据目录
            context: AstrBot Context
            subscription_manager: 订阅管理器
            config: 配置字典
        """
        self.docs_dir = docs_dir
        self.data_dir = data_dir
        self.context = context
        self.subscription_manager = subscription_manager
        self.config = config

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

    def get_diff(self, doc_id: int, current_commit: str, doc_path: str) -> tuple[str, bool]:
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

        if not last_commit:
            # 首次推送，没有 diff 信息
            return "[这是新发布的文档，首次推送，无历史 diff 信息]", True

        if last_commit == current_commit:
            # commit 相同，跳过
            return "", False

        git = GitOps(self.docs_dir)
        if not git.has_git():
            return "[无 Git 仓库，无法获取 diff]", False

        try:
            diff = git.get_diff(last_commit, current_commit, doc_path)
            return diff or "[无文本变更]", False
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
        is_first_push: bool = False
    ) -> tuple[bool, Optional[dict]]:
        """通过 LLM 判断是否推送

        由于 tool_loop_agent 需要 event 对象，而 Webhook 上下文中没有 event，
        改用简单的 LLM 调用进行判断。

        Args:
            doc_info: 文档信息
            content: 文档内容（首次推送为原文，否则为 diff）
            is_first_push: 是否首次推送

        Returns:
            (should_push, summary) 是否推送，摘要信息
        """
        try:
            # 获取 Provider
            prov = self.context.get_using_provider()
            if not prov:
                logger.warning("[Push] 无可用的 LLM Provider")
                return True, {"highlights": ["文档有更新"], "reason": "无 LLM，默认推送"}

            # 截断内容避免过长
            if len(content) > self.max_content_len:
                content = content[:self.max_content_len] + "\n... (已截断)"

            # 根据是否首次推送使用不同的提示词
            if is_first_push:
                prompt = f"""你是一个文档推送决策助手。

文档信息：
- 标题：{doc_info.get('title', '未知')}
- 作者：{doc_info.get('author', '未知')}
- 知识库：{doc_info.get('book_name', '未知')}

这是新发布的文档，以下是文档内容：
{content}

请分析这篇新文档是否值得推送给订阅者。

判断标准：
- 推送：有实质内容的文档、教程、笔记、项目文档等
- 不推送：空白文档、只有占位内容、测试文档

请严格按以下 JSON 格式返回（不要有其他内容）：
{{
  "should_push": true 或 false,
  "highlights": ["文档要点1", "文档要点2"],
  "reason": "简短说明推送或不推送的原因"
}}
"""
            else:
                prompt = f"""你是一个文档更新推送决策助手。

文档信息：
- 标题：{doc_info.get('title', '未知')}
- 作者：{doc_info.get('author', '未知')}
- 知识库：{doc_info.get('book_name', '未知')}

以下是文档的变更内容（diff）：
{content}

请分析变更是否对读者有实质价值，判断是否值得推送。

判断标准：
- 不推送：错别字/标点、纯格式调整、无关紧要的链接修改
- 推送：新增内容、重写段落、重要结论变更、API 变更

请严格按以下 JSON 格式返回（不要有其他内容）：
{{
  "should_push": true 或 false,
  "highlights": ["变更要点1", "变更要点2"],
  "reason": "简短说明推送或不推送的原因"
}}
"""

            resp = await prov.text_chat(
                prompt=prompt,
                context=[],
                system_prompt="你是一个文档更新推送决策助手，善于判断内容价值。必须返回 JSON 格式。"
            )

            result_text = resp.completion_text.strip()

            # 提取 JSON
            if "```json" in result_text:
                start = result_text.find("```json") + 7
                end = result_text.find("```", start)
                result_text = result_text[start:end].strip()
            elif "```" in result_text:
                start = result_text.find("```") + 3
                end = result_text.find("```", start)
                result_text = result_text[start:end].strip()

            # 解析 JSON
            result = json.loads(result_text)
            should_push = result.get("should_push", True)
            highlights = result.get("highlights", [])
            reason = result.get("reason", "")

            logger.info(f"[Push] LLM 判断: should_push={should_push}, reason={reason}")

            return should_push, {
                "highlights": highlights,
                "reason": reason
            }

        except json.JSONDecodeError as e:
            logger.warning(f"[Push] JSON 解析失败: {e}")
            return True, {"highlights": ["文档有更新"], "reason": "解析失败，默认推送"}
        except Exception as e:
            logger.error(f"[Push] LLM 判断失败: {e}")
            return True, {"highlights": ["文档有更新"], "reason": f"判断失败: {e}"}

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

        lines = [
            f"📄 《{title}》有更新",
            "",
        ]

        if highlights:
            lines.append("📝 变更要点：")
            for h in highlights[:5]:  # 最多 5 条
                lines.append(f"• {h}")
            lines.append("")

        lines.append(f"✍️ {author}")
        if book_name:
            lines.append(f"📚 {book_name}")
        if doc_url:
            lines.append(f"🔗 {doc_url}")

        return "\n".join(lines)

    def mark_pushed(self, doc_id: int, commit: str):
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