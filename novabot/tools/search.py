"""
搜索相关工具：语义搜索、关键词搜索、文档读取
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from astrbot.api import logger

from .base import BaseTool


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
            }
        },
        "required": ["query"]
    })
    plugin: Any = None

    async def run(self, event, query: str, top_k: int = 5):
        if not self.plugin or not self.plugin.rag:
            return "知识库未初始化，请检查 embedding 配置"

        try:
            results = self.plugin.rag.search(query, k=top_k)
            if not results:
                return f"未找到与「{query}」相关的内容"

            output = [f"🔍 语义搜索结果（可能不精确，建议用 grep 精确搜索）:\n"]
            for i, r in enumerate(results, 1):
                title = r.get("title", "未知")
                author = r.get("author", "")
                book = r.get("book_name", "")
                content = r.get("content", "")[:300]
                output.append(f"【{i}】{title}" + (f" (by {author})" if author else ""))
                if book:
                    output.append(f"    📚 知识库: {book}")
                output.append(f"    {content}...")
                output.append("")

            return "\n".join(output)
        except Exception as e:
            return f"搜索失败: {e}"


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
            "max_results": {
                "type": "integer",
                "description": "最大返回结果数，默认 10",
                "default": 10
            }
        },
        "required": ["keyword"]
    })
    plugin: Any = None

    async def run(self, event, keyword: str, repo_filter: str = "", max_results: int = 10):
        docs_dir = self.get_docs_dir()
        if not docs_dir.exists():
            return "文档目录不存在，请先执行 /sync 同步"

        results = []
        pattern = re.compile(re.escape(keyword), re.IGNORECASE)

        # 确定搜索范围
        search_dirs = []
        if repo_filter:
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
                            "path": str(rel_path),
                            "count": len(matches),
                            "contexts": contexts
                        })
                except Exception:
                    continue

        if not results:
            filter_hint = f"（在「{repo_filter}」中）" if repo_filter else ""
            return f"未找到包含「{keyword}」的文档{filter_hint}"

        # 按匹配数排序
        results.sort(key=lambda x: x["count"], reverse=True)
        results = results[:max_results]

        output = [f"找到 {len(results)} 个文档包含「{keyword}」（按匹配数排序）:\n"]
        for r in results:
            output.append(f"📄 {r['title']} ({r['count']} 处匹配)" + (f" - {r['repo']}" if r.get('repo') else ""))
            output.append(f"   📁 {r['path']}")
            for ctx in r['contexts'][:2]:
                output.append(f"   {ctx}")
            output.append("")

        output.append("💡 提示: 使用 read_doc(path) 读取完整文档内容")
        return "\n".join(output)


@dataclass
class ReadDocTool(BaseTool):
    """读取文档工具"""

    name: str = "read_doc"
    description: str = "读取指定路径的文档完整内容。先用 grep_local_docs 找到相关文档，然后用这个工具读取完整内容。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "文档路径（grep 结果中的路径）"
            }
        },
        "required": ["path"]
    })
    plugin: Any = None

    async def run(self, event, path: str):
        docs_dir = self.get_docs_dir()
        doc_file = docs_dir / path

        if not doc_file.exists():
            return f"文档不存在: {path}"

        if not str(doc_file.resolve()).startswith(str(docs_dir.resolve())):
            return "非法路径"

        try:
            content = doc_file.read_text(encoding="utf-8")
            if len(content) > 8000:
                content = content[:8000] + "\n\n... (文档过长，已截断)"
            return f"📄 文档内容:\n\n{content}"
        except Exception as e:
            return f"读取失败: {e}"