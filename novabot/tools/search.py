"""
搜索相关工具：语义搜索、关键词搜索、文档读取、知识卡片
"""

import re
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..doc_utils import (
    doc_record_to_public_dict,
    parse_yuque_doc_url,
    read_document_content,
)
from ..evidence import (
    evidence_from_document_content,
    format_citations,
    format_evidence_block,
    select_grounding_evidence,
)
from .base import BaseTool


def _api_base_url(plugin) -> str:
    return getattr(plugin, "yuque_base_url", "https://www.yuque.com/api/v2")


@dataclass
class ParseYuqueUrlTool(BaseTool):
    """解析语雀链接工具"""

    name: str = "parse_yuque_url"
    description: str = "解析语雀文档链接，定位并读取对应文档。当用户提供语雀链接时调用此工具。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "语雀文档链接，如 https://nova.yuque.com/wg5tth/clekzy/bopur5sxounppd5q"
            }
        },
        "required": ["url"]
    })
    plugin: Any = None

    async def run(self, event: AstrMessageEvent, url: str) -> str:
        docs_dir = self.get_docs_dir()
        if not docs_dir.exists():
            return "文档目录不存在，请先执行 /sync 同步"

        parsed = parse_yuque_doc_url(url)
        if not parsed:
            return f"无法解析语雀链接格式: {url}\n期望格式: https://xxx.yuque.com/namespace/doc-slug"

        namespace, doc_slug = parsed
        logger.info(f"[parse_yuque_url] 解析链接: namespace={namespace}, slug={doc_slug}")

        doc_index = self.get_doc_index()
        if not doc_index:
            return "元数据索引不存在，请先执行 /sync 同步"

        try:
            rows = doc_index.find_docs_by_slug(doc_slug, limit=5)
            if not rows:
                return f"未找到 slug 为「{doc_slug}」的文档"

            matched_row = None
            for row_dict in rows:
                book_ns = row_dict.get("book_namespace", "")
                if namespace in book_ns or book_ns in namespace:
                    matched_row = row_dict
                    break
            if not matched_row:
                matched_row = rows[0]

            pub = doc_record_to_public_dict(matched_row, _api_base_url(self.plugin))
            file_path = matched_row.get("file_path")
            if not file_path:
                return f"文档记录存在但缺少文件路径: {pub['title']}"

            doc_file = docs_dir / file_path
            if not doc_file.exists():
                return f"文档文件不存在: {file_path}"

            content_info = read_document_content(doc_file, offset=0, limit=8000, strip_metadata=True)
            body = content_info["content"]
            meta_lines = [
                f"📄 《{pub['title']}》",
                f"链接：{pub.get('url') or url}",
                f"团队：{pub.get('team_name') or matched_row.get('team_name') or '未知'} ({pub.get('team_id') or matched_row.get('team_id') or 'default'})",
                f"作者：{pub.get('author') or '未知'}",
                f"知识库：{pub.get('book_name') or ''}",
                f"创建：{pub.get('created_at') or '未知'}",
                f"更新：{pub.get('updated_at') or '未知'}",
                f"字数：{pub.get('word_count') or 0}",
            ]
            if content_info.get("has_more"):
                meta_lines.append(
                    f"正文：已返回 {content_info['returned_chars']}/{content_info['total_chars']} 字，"
                    f"继续阅读请调用 get_doc_details(path=\"{file_path}\", include_content=true, content_offset={content_info['next_offset']})"
                )
            evidence = [
                evidence_from_document_content(
                    {**matched_row, "url": pub.get("url") or url},
                    body,
                    evidence_id="E1",
                )
            ]
            meta_lines.extend(["", format_evidence_block(evidence), "", format_citations(evidence), "", body])
            return "\n".join(meta_lines)

        except Exception as e:
            logger.error(f"[parse_yuque_url] 查找文档失败: {e}")
            return f"查找文档失败: {e}"


@dataclass
class SearchKnowledgeBaseTool(BaseTool):
    """知识库语义搜索工具"""

    name: str = "search_knowledge_base"
    description: str = "语义搜索 NOVA 社团语雀知识库。返回可能相关的文档片段。注意：结果可能不够精确，建议先用 list_knowledge_bases 确定知识库，再用 grep_local_docs 精确搜索关键词。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词或问题"
            },
            "top_k": {
                "type": "integer",
                "description": "返回结果数量，默认 5",
                "default": 5
            },
            "team_id": {
                "type": "string",
                "description": "团队 ID 过滤（可选）。不确定时留空。"
            },
            "repository": {
                "type": "string",
                "description": "知识库名称或 namespace 过滤（可选）"
            },
            "path_prefix": {
                "type": "string",
                "description": "目录路径过滤（可选），例如 指南/入门"
            },
            "author": {
                "type": "string",
                "description": "作者过滤（可选）"
            },
            "updated_after": {
                "type": "string",
                "description": "更新时间下限（可选，ISO 日期或 YYYY-MM-DD）"
            },
            "updated_before": {
                "type": "string",
                "description": "更新时间上限（可选，ISO 日期或 YYYY-MM-DD）"
            }
        },
        "required": ["query"]
    })
    plugin: Any = None

    async def run(
        self,
        event: AstrMessageEvent,
        query: str,
        top_k: int = 5,
        team_id: str = "",
        repository: str = "",
        path_prefix: str = "",
        author: str = "",
        updated_after: str = "",
        updated_before: str = "",
    ) -> str:
        if not self.plugin:
            return "知识库未初始化"

        try:
            scope = {
                "team_id": team_id,
                "repository": repository,
                "path_prefix": path_prefix,
                "author": author,
                "updated_after": updated_after,
                "updated_before": updated_before,
            }
            scoped = any(v for v in scope.values())
            core = getattr(self.plugin, "knowledge_core", None)
            chunk_store = getattr(self.plugin, "chunk_store", None)
            if core and chunk_store and chunk_store.chunk_count() > 0:
                results = await core.search(query, top_k=top_k, scope=scope)
                if results or scoped:
                    return self._format_core_results(query, results, scoped)

            if not getattr(self.plugin, "rag", None):
                return "知识库未初始化，请先执行 /sync 同步；如需语义检索，请检查 embedding 配置"

            legacy_k = top_k * 5 if scoped else top_k
            results = self.plugin.rag.search(
                query,
                k=legacy_k,
                book_filter=repository or None,
            )
            if scoped:
                results = self._filter_legacy_rag_results(results, scope)[:top_k]
            if not results:
                scope_hint = "（指定范围内）" if scoped else ""
                return f"【搜索结果】{scope_hint}知识库中未找到与「{query}」相关的内容。请尝试其他关键词，或告知用户知识库中暂无相关信息。"

            output = [f"🔍 语义搜索结果（共 {len(results)} 条）：\n"]
            output.append("⚠️ 注意：以下内容来自旧 RAG 文档级搜索，请根据实际相关性判断是否使用，不要编造不存在的文档。\n")
            for i, r in enumerate(results, 1):
                title = r.get("title", "未知")
                author = r.get("author", "")
                book = r.get("book_name", "")
                content = r.get("content", "")[:500]  # 增加到 500 字符
                output.append(f"【{i}】{title}" + (f" (by {author})" if author else ""))
                if r.get("team_name") or r.get("team_id"):
                    output.append(f"    团队: {r.get('team_name') or r.get('team_id')} ({r.get('team_id') or 'unknown'})")
                if book:
                    output.append(f"    📚 知识库: {book}")
                output.append(f"    内容片段: {content}...")
                output.append("")

            output.append("💡 提示：如果以上结果与问题不相关，请告知用户'知识库中暂未找到相关内容'，不要编造答案。")
            return "\n".join(output)
        except Exception as e:
            return f"搜索失败: {e}"

    def _format_core_results(self, query: str, results: list, scoped: bool) -> str:
        if not results:
            scope_hint = "（指定范围内）" if scoped else ""
            return f"【知识检索】{scope_hint}未找到与「{query}」相关的可靠片段。请尝试放宽团队/知识库/路径/作者/时间范围。"

        evidence = select_grounding_evidence(results, max_evidence=5)
        output = [format_evidence_block(evidence), "", format_citations(evidence), ""]
        output.append(f"🔍 候选检索结果（chunk，{len(results)} 条）：\n")
        output.append("⚠️ 知识事实回答只能使用上方 Grounding Evidence；候选结果仅用于判断是否需要继续检索或阅读原文。\n")
        for i, item in enumerate(results, 1):
            chunk = item.chunk
            methods = ",".join(item.methods) or "unknown"
            output.append(f"【{i}】{chunk.title or '未知'}")
            output.append(
                f"    reliable={str(item.reliable).lower()} score={item.score:.3f} methods={methods}"
            )
            if chunk.team_name or chunk.team_id:
                output.append(f"    团队: {chunk.team_name} ({chunk.team_id})")
            if chunk.repository:
                output.append(f"    知识库: {chunk.repository}")
            if chunk.author:
                output.append(f"    作者: {chunk.author}")
            if chunk.file_path:
                output.append(f"    路径: {chunk.file_path}")
            if chunk.source_url:
                output.append(f"    链接: {chunk.source_url}")
            output.append(f"    内容片段: {chunk.content[:700]}...")
            output.append("")
        output.append("💡 如果 Grounding Evidence 为空，请说明知识库中暂未找到可靠内容，不要编造。")
        return "\n".join(output)

    def _filter_legacy_rag_results(self, results: list[dict], scope: dict) -> list[dict]:
        filtered = []
        for result in results:
            if not self._legacy_rag_result_matches_scope(result, scope):
                continue
            filtered.append(result)
        return filtered

    @staticmethod
    def _legacy_rag_result_matches_scope(result: dict, scope: dict) -> bool:
        team_id = str(scope.get("team_id") or "").strip()
        if team_id and str(result.get("team_id") or "default") != team_id:
            return False

        repository = str(scope.get("repository") or "").strip().casefold()
        if repository:
            repo_text = " ".join(
                str(result.get(key) or "") for key in ("book_name", "source")
            ).casefold()
            if repository not in repo_text:
                return False

        author = str(scope.get("author") or "").strip().casefold()
        if author and author not in str(result.get("author") or "").casefold():
            return False

        path_prefix = str(scope.get("path_prefix") or "").strip().casefold()
        if path_prefix:
            path_text = " ".join(
                str(result.get(key) or "") for key in ("file_path", "source")
            ).casefold()
            if path_prefix not in path_text:
                return False

        if scope.get("updated_after") or scope.get("updated_before"):
            # Legacy RAG documents historically do not carry updated_at. Avoid leaking
            # out-of-scope facts when a time filter cannot be proven.
            updated_at = str(result.get("updated_at") or "").strip()
            if not updated_at:
                return False
            if scope.get("updated_after") and updated_at < str(scope["updated_after"]):
                return False
            if scope.get("updated_before") and updated_at > str(scope["updated_before"]):
                return False

        return True


@dataclass
class GrepLocalDocsTool(BaseTool):
    """本地文档关键词搜索工具"""

    name: str = "grep_local_docs"
    description: str = "在本地同步的语雀文档中进行关键词精确匹配搜索。比语义搜索更精确。返回匹配数最多的文档。找到相关文档后，可用 read_doc 读取完整内容。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "要搜索的关键词"
            },
            "repo_filter": {
                "type": "string",
                "description": "知识库名称过滤（可选），只搜索该知识库"
            },
            "team_id": {
                "type": "string",
                "description": "团队 ID 过滤（可选）"
            },
            "path_prefix": {
                "type": "string",
                "description": "路径前缀/片段过滤（可选）"
            },
            "author": {
                "type": "string",
                "description": "作者过滤（可选）"
            },
            "updated_after": {
                "type": "string",
                "description": "更新时间下界（可选）"
            },
            "updated_before": {
                "type": "string",
                "description": "更新时间上界（可选）"
            },
            "max_results": {
                "type": "integer",
                "description": "最大返回结果数，默认 10",
                "default": 10
            }
        },
        "required": ["keyword"]
    })
    plugin: Any = None

    async def run(
        self,
        event: AstrMessageEvent,
        keyword: str,
        repo_filter: str = "",
        max_results: int = 10,
        team_id: str = "",
        path_prefix: str = "",
        author: str = "",
        updated_after: str = "",
        updated_before: str = "",
    ) -> str:
        docs_dir = self.get_docs_dir()
        if not docs_dir.exists():
            return "文档目录不存在，请先执行 /sync 同步"

        results = []
        pattern = re.compile(re.escape(keyword), re.IGNORECASE)

        # 确定搜索范围
        allowed_paths = self._scoped_grep_paths(
            team_id=team_id,
            repo_filter=repo_filter,
            path_prefix=path_prefix,
            author=author,
            updated_after=updated_after,
            updated_before=updated_before,
        )
        if allowed_paths is not None and not allowed_paths:
            return f"【关键词搜索】指定范围内没有可搜索文档（筛选: {self._grep_filter_summary(team_id, repo_filter, path_prefix, author, updated_after, updated_before)}）"

        search_dirs = []
        if allowed_paths is not None:
            search_dirs = [docs_dir]
        elif repo_filter:
            for d in docs_dir.iterdir():
                if d.is_dir() and repo_filter.lower() in d.name.lower():
                    search_dirs.append(d)
            if not search_dirs:
                return f"未找到匹配「{repo_filter}」的知识库"
        else:
            search_dirs = [docs_dir]

        for search_dir in search_dirs:
            for md_file in search_dir.rglob("*.md"):
                try:
                    rel_posix = str(md_file.relative_to(docs_dir)).replace("\\", "/")
                    if allowed_paths is not None and rel_posix not in allowed_paths:
                        continue
                    content = md_file.read_text(encoding="utf-8")
                    matches = list(pattern.finditer(content))
                    if matches:
                        # 提取标题
                        title = md_file.stem
                        for line in content.split("\n")[:10]:
                            if line.startswith("# "):
                                title = line[2:].strip()
                                break

                        # 提取上下文（高亮匹配词）
                        contexts = []
                        for m in matches[:3]:
                            start = max(0, m.start() - 30)
                            end = min(len(content), m.end() + 70)
                            ctx = content[start:end].replace("\n", " ")
                            ctx = pattern.sub(f"**{keyword}**", ctx, count=1)
                            contexts.append(f"...{ctx}...")

                        # 获取知识库名和相对路径
                        rel_path = md_file.relative_to(docs_dir)
                        repo_name = rel_path.parts[0] if len(rel_path.parts) > 1 else ""

                        results.append({
                            "title": title,
                            "repo": repo_name,
                            "path": str(rel_path).replace("\\", "/"),
                            "count": len(matches),
                            "contexts": contexts
                        })
                except Exception:
                    continue

        if not results:
            filter_hint = f"（筛选: {self._grep_filter_summary(team_id, repo_filter, path_prefix, author, updated_after, updated_before)}）" if any(
                (team_id, repo_filter, path_prefix, author, updated_after, updated_before)
            ) else ""
            return f"【关键词搜索】未找到包含「{keyword}」的文档{filter_hint}。请告知用户知识库中暂无相关内容，不要编造答案。"

        # 按匹配数排序
        results.sort(key=lambda x: x["count"], reverse=True)
        results = results[:max_results]

        output = [f"【关键词搜索】找到 {len(results)} 个文档包含「{keyword}」（按匹配数排序）:\n"]
        for r in results:
            output.append(f"📄 {r['title']} ({r['count']} 处匹配)" + (f" - {r['repo']}" if r.get('repo') else ""))
            output.append(f"   📁 {r['path']}")
            for ctx in r['contexts'][:2]:
                output.append(f"   {ctx}")
            output.append("")

        output.append("💡 提示: 使用 read_doc(path) 读取完整文档内容。回答时请基于实际搜索结果，不要编造不存在的文档。")
        return "\n".join(output)

    def _scoped_grep_paths(
        self,
        *,
        team_id: str = "",
        repo_filter: str = "",
        path_prefix: str = "",
        author: str = "",
        updated_after: str = "",
        updated_before: str = "",
    ) -> set[str] | None:
        has_strict_scope = any((team_id, path_prefix, author, updated_after, updated_before))
        if not has_strict_scope and not repo_filter:
            return None
        doc_index = self.get_doc_index()
        if not doc_index:
            return set() if has_strict_scope else None
        rows = doc_index.search(
            team_id=team_id or None,
            book=repo_filter or None,
            path_prefix=path_prefix or None,
            author=author or None,
            updated_after=updated_after or None,
            updated_before=updated_before or None,
            limit=10000,
        )
        return {
            str(row.get("file_path") or "").replace("\\", "/")
            for row in rows
            if row.get("file_path")
        }

    @staticmethod
    def _grep_filter_summary(
        team_id: str,
        repo_filter: str,
        path_prefix: str,
        author: str,
        updated_after: str,
        updated_before: str,
    ) -> str:
        parts = []
        for label, value in (
            ("team_id", team_id),
            ("知识库", repo_filter),
            ("路径", path_prefix),
            ("作者", author),
            ("更新晚于", updated_after),
            ("更新早于", updated_before),
        ):
            if value:
                parts.append(f"{label}={value}")
        return ", ".join(parts) if parts else "无"


@dataclass
class ReadDocTool(BaseTool):
    """读取文档工具"""

    name: str = "read_doc"
    description: str = (
        "读取指定路径的文档内容。先用 grep_local_docs 或 search_docs 获取 path。"
        "长文可用 offset/limit 分页；strip_metadata=false 时保留 frontmatter。"
    )
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "文档路径（grep 或 search_docs 返回的 file_path）"
            },
            "offset": {
                "type": "integer",
                "description": "正文起始字符偏移，默认 0",
                "default": 0,
            },
            "limit": {
                "type": "integer",
                "description": "最大返回字符数，默认 12000，最大 20000",
                "default": 12000,
            },
            "strip_metadata": {
                "type": "boolean",
                "description": "是否去掉 frontmatter 与元信息表，默认 true",
                "default": True,
            },
        },
        "required": ["path"]
    })
    plugin: Any = None

    async def run(
        self,
        event: AstrMessageEvent,
        path: str,
        offset: int = 0,
        limit: int = 12000,
        strip_metadata: bool = True,
    ) -> str:
        docs_dir = self.get_docs_dir()
        doc_file = docs_dir / path

        if not doc_file.exists():
            return f"文档不存在: {path}"

        if not str(doc_file.resolve()).startswith(str(docs_dir.resolve())):
            return "非法路径"

        try:
            safe_limit = min(max(limit, 1), 20000)
            if strip_metadata:
                info = read_document_content(
                    doc_file, offset=offset, limit=safe_limit, strip_metadata=True
                )
                doc_row = self._find_doc_row(path)
                evidence_doc = doc_row or {
                    "title": doc_file.stem,
                    "file_path": path,
                }
                evidence = [
                    evidence_from_document_content(
                        evidence_doc,
                        info["content"],
                        evidence_type="read_doc",
                    )
                ]
                header = [f"📄 文档: {path}"]
                if info.get("has_more"):
                    header.append(
                        f"（正文 {info['returned_chars']}/{info['total_chars']} 字，"
                        f"offset={info['offset']}；继续: read_doc(path=\"{path}\", offset={info['next_offset']})）"
                    )
                elif info["total_chars"] > 0:
                    header.append(f"（全文共 {info['total_chars']} 字）")
                header.extend(
                    ["", format_evidence_block(evidence), "", info["content"], "", format_citations(evidence)]
                )
                return "\n".join(header)

            raw = doc_file.read_text(encoding="utf-8")
            total = len(raw)
            chunk = raw[offset : offset + safe_limit]
            has_more = offset + len(chunk) < total
            lines = [f"📄 文档: {path}（原始文件 {len(chunk)}/{total} 字，offset={offset}）"]
            if has_more:
                lines.append(f"继续: read_doc(path=\"{path}\", offset={offset + len(chunk)}, strip_metadata=false)")
            lines.extend(["", chunk])
            return "\n".join(lines)
        except Exception as e:
            return f"读取失败: {e}"

    def _find_doc_row(self, path: str) -> dict | None:
        doc_index = self.get_doc_index()
        if not doc_index:
            return None
        try:
            return doc_index.find_doc_by_file_path(path)
        except Exception as e:
            logger.warning(f"[read_doc] 查询文档元数据失败: {e}")
            return None


@dataclass
class KnowledgeCardTool(BaseTool):
    """知识卡片生成工具"""

    name: str = "generate_knowledge_card"
    description: str = "根据主题生成知识卡片，聚合多篇相关文档的核心知识点和个人思考。当用户想学习某个领域或主题时调用此工具。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "要学习的主题或领域，如'爬虫'、'Python基础'、'机器学习'等"
            }
        },
        "required": ["topic"]
    })
    plugin: Any = None

    async def run(self, event: AstrMessageEvent, topic: str) -> str:
        if not self.plugin or not self.plugin.rag:
            return "知识库未初始化，请检查 embedding 配置"

        try:
            # 获取 LLM Provider
            provider = self.plugin.context.get_using_provider(umo=event.unified_msg_origin)
            if not provider:
                return "LLM 未配置，无法生成知识卡片"

            # 导入并使用知识卡片生成器
            from ..knowledge_card import KnowledgeCardGenerator, format_knowledge_card

            generator = KnowledgeCardGenerator(self.plugin.rag, self.plugin.token_monitor)
            card = await generator.generate(topic, provider)

            return format_knowledge_card(card)

        except Exception as e:
            logger.error(f"知识卡片生成失败: {e}", exc_info=True)
            return f"生成失败: {e}"
