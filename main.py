"""
NovaBot - NOVA 社团智能助手
以语雀知识库为核心的 AstrBot Plugin
"""

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
import yaml
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .novabot.rag import RAGEngine


# ============================================================================
# 语雀 API 客户端
# ============================================================================

class YuqueClient:
    """语雀 API 客户端（带限流和重试）"""

    # 限流配置
    CONCURRENCY = 3          # 最大并发数
    REQUEST_DELAY = 0.25     # 请求间隔（秒）
    MAX_RETRIES = 4          # 最大重试次数

    def __init__(self, token: str, base_url: str = "https://nova.yuque.com/api/v2"):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.headers = {
            "X-Auth-Token": token,
            "User-Agent": "NovaBot/1.0",
            "Content-Type": "application/json",
        }
        self._client: Optional[httpx.AsyncClient] = None
        self._semaphore = asyncio.Semaphore(self.CONCURRENCY)

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(headers=self.headers, timeout=30.0)
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """带限流和重试的请求"""
        last_error = None

        for attempt in range(self.MAX_RETRIES):
            try:
                async with self._semaphore:
                    resp = await self.client.request(method, url, **kwargs)

                    # 429 Rate Limit
                    if resp.status_code == 429:
                        wait = int(resp.headers.get("Retry-After", 2 ** attempt))
                        logger.warning(f"Rate limited (429), wait {wait}s")
                        await asyncio.sleep(wait)
                        continue

                    # 5xx Server Error
                    if 500 <= resp.status_code < 600:
                        wait = 2 ** attempt
                        logger.warning(f"Server error {resp.status_code}, retry in {wait}s")
                        await asyncio.sleep(wait)
                        continue

                    # 请求间隔
                    if self.REQUEST_DELAY > 0:
                        await asyncio.sleep(self.REQUEST_DELAY)

                    return resp

            except (httpx.RequestError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
                wait = 2 ** attempt
                logger.warning(f"Request error: {e}, retry in {wait}s")
                await asyncio.sleep(wait)
                last_error = e

        if last_error:
            raise last_error
        raise RuntimeError("Unexpected retry loop exit")

    async def _get(self, path: str, params: dict = None) -> dict:
        """GET 请求"""
        url = f"{self.base_url}{path}"
        resp = await self._request("GET", url, params=params)
        resp.raise_for_status()
        return resp.json()

    # ========== API 方法 ==========

    async def get_user_info(self) -> dict:
        """获取当前认证用户信息"""
        data = await self._get("/user")
        return data.get("data", {})

    async def get_group_repos(self, group_id: int, limit: int = 100) -> list:
        """获取团队的知识库列表"""
        data = await self._get(f"/groups/{group_id}/repos", {"limit": limit})
        return data.get("data", [])

    async def get_user_repos(self, user_id: int, limit: int = 100) -> list:
        """获取用户的知识库列表"""
        data = await self._get(f"/users/{user_id}/repos", {"limit": limit})
        return data.get("data", [])

    async def get_repo_docs(self, namespace: str, limit: int = 100) -> list:
        """获取知识库的文档列表"""
        data = await self._get(f"/repos/{namespace}/docs", {"limit": limit})
        return data.get("data", [])

    async def get_doc_detail(self, namespace: str, slug: str) -> dict:
        """获取文档详情（含正文）"""
        data = await self._get(f"/repos/{namespace}/docs/{slug}", {"include_content": "true"})
        return data.get("data", {})

    async def get_group_members(self, group_id: int) -> list:
        """获取团队成员（分页）"""
        all_members = []
        page = 1

        while True:
            data = await self._get(
                f"/groups/{group_id}/statistics/members",
                {"page": page}
            )
            members = data.get("data", {}).get("members", [])
            if not members:
                break
            all_members.extend(members)
            page += 1

        return all_members


# ============================================================================
# 数据存储
# ============================================================================

class Storage:
    """数据存储"""

    def __init__(self, data_dir: str = "data/nova"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # 文件路径
        self.bindings_file = self.data_dir / "bindings.json"
        self.members_file = self.data_dir / "yuque-members.json"
        self.sync_state_file = self.data_dir / "sync_state.json"
        self.profiles_dir = self.data_dir / "user_profiles"
        self.profiles_dir.mkdir(parents=True, exist_ok=True)

    # ========== 绑定关系 ==========

    def load_bindings(self) -> dict:
        if self.bindings_file.exists():
            return json.loads(self.bindings_file.read_text(encoding="utf-8"))
        return {}

    def save_bindings(self, bindings: dict):
        self.bindings_file.write_text(
            json.dumps(bindings, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def get_binding(self, platform_id: str) -> Optional[dict]:
        return self.load_bindings().get(platform_id)

    def add_binding(self, platform_id: str, yuque_info: dict):
        bindings = self.load_bindings()
        bindings[platform_id] = {
            **yuque_info,
            "bind_time": datetime.now().isoformat(),
        }
        self.save_bindings(bindings)

    def remove_binding(self, platform_id: str):
        bindings = self.load_bindings()
        if platform_id in bindings:
            del bindings[platform_id]
            self.save_bindings(bindings)

    # ========== 团队成员 ==========

    def load_members(self) -> dict:
        if self.members_file.exists():
            return json.loads(self.members_file.read_text(encoding="utf-8"))
        return {}

    def save_members(self, members: dict):
        self.members_file.write_text(
            json.dumps(members, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def find_member_by_name(self, name_or_login: str) -> Optional[dict]:
        members = self.load_members()
        name_lower = name_or_login.lower()

        # 1. 精确匹配 login
        for uid, info in members.items():
            if info.get("login", "").lower() == name_lower:
                return {"id": int(uid), **info}

        # 2. 精确匹配 name
        for uid, info in members.items():
            if info.get("name", "").lower() == name_lower:
                return {"id": int(uid), **info}

        # 3. 模糊匹配
        for uid, info in members.items():
            if name_lower in info.get("name", "").lower():
                return {"id": int(uid), **info}
            if name_lower in info.get("login", "").lower():
                return {"id": int(uid), **info}

        return None

    # ========== 同步状态 ==========

    def load_sync_state(self) -> dict:
        if self.sync_state_file.exists():
            return json.loads(self.sync_state_file.read_text(encoding="utf-8"))
        return {
            "last_sync": None,
            "repos": {},
            "docs_count": 0,
            "in_progress": False,
            "progress": None,  # {"current": 5, "total": 45, "current_repo": "知识库名"}
        }

    def save_sync_state(self, state: dict):
        self.sync_state_file.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def update_progress(self, current: int, total: int, current_repo: str):
        """更新同步进度"""
        state = self.load_sync_state()
        state["in_progress"] = True
        state["progress"] = {
            "current": current,
            "total": total,
            "current_repo": current_repo
        }
        self.save_sync_state(state)

    def finish_sync(self, state: dict):
        """标记同步完成"""
        state["in_progress"] = False
        state["progress"] = None
        self.save_sync_state(state)

    # ========== 用户画像 ==========

    def load_profile(self, yuque_id: int) -> Optional[dict]:
        profile_file = self.profiles_dir / f"{yuque_id}.json"
        if profile_file.exists():
            return json.loads(profile_file.read_text(encoding="utf-8"))
        return None

    def save_profile(self, yuque_id: int, profile: dict):
        profile_file = self.profiles_dir / f"{yuque_id}.json"
        profile["updated_at"] = datetime.now().isoformat()
        profile_file.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )


# ============================================================================
# 文档同步器
# ============================================================================

class YuqueSync:
    """语雀文档同步器"""

    def __init__(self, storage: Storage):
        self.storage = storage
        self.docs_dir = storage.data_dir / "yuque_docs"
        self.docs_dir.mkdir(parents=True, exist_ok=True)

    async def sync_team_members(self, client: YuqueClient) -> int:
        """同步团队成员"""
        user_info = await client.get_user_info()

        if user_info.get("type") != "Group":
            logger.info("非团队 Token，跳过成员同步")
            return 0

        group_id = user_info.get("id")
        logger.info(f"同步团队成员，团队 ID: {group_id}")

        members_raw = await client.get_group_members(group_id)

        members = {}
        for item in members_raw:
            user = item.get("user", {})
            uid = user.get("id") or item.get("user_id")
            if uid:
                members[str(uid)] = {
                    "name": user.get("name", ""),
                    "login": user.get("login", "")
                }

        if members:
            self.storage.save_members(members)
            logger.info(f"同步团队成员完成，共 {len(members)} 人")

        return len(members)

    async def sync_all_repos(self, client: YuqueClient, with_content: bool = True) -> dict:
        """同步所有知识库（自动判断团队/个人 Token）"""
        user_info = await client.get_user_info()
        is_group = user_info.get("type") == "Group"

        # 获取知识库列表
        if is_group:
            group_id = user_info.get("id")
            logger.info(f"团队 Token，ID: {group_id}")
            repos = await client.get_group_repos(group_id)
        else:
            user_id = user_info.get("id")
            logger.info(f"个人 Token，ID: {user_id}")
            repos = await client.get_user_repos(user_id)

        total_repos = len(repos)
        logger.info(f"获取到 {total_repos} 个知识库，开始同步...")

        # 获取成员映射（用于填充作者名）
        members = self.storage.load_members()

        # 同步每个知识库
        total_docs = 0
        repo_stats = {}

        for i, repo in enumerate(repos):
            namespace = repo.get("namespace", "")
            repo_name = repo.get("name", "")
            if not namespace:
                continue

            # 更新进度
            self.storage.update_progress(i + 1, total_repos, repo_name)
            logger.info(f"[{i+1}/{total_repos}] 同步: {repo_name}")

            try:
                docs = await self._sync_repo_docs(client, namespace, with_content, members)
                total_docs += len(docs)
                repo_stats[namespace] = {
                    "name": repo_name,
                    "docs_count": len(docs),
                    "synced_at": datetime.now().isoformat()
                }
            except Exception as e:
                logger.error(f"同步知识库 {namespace} 失败: {e}", exc_info=True)
                repo_stats[namespace] = {"name": repo_name, "error": str(e)}

        # 保存同步状态
        state = {
            "last_sync": datetime.now().isoformat(),
            "repos": repo_stats,
            "docs_count": total_docs,
            "token_type": "group" if is_group else "user",
            "in_progress": False,
            "progress": None
        }
        self.storage.save_sync_state(state)
        logger.info(f"同步完成，共 {total_docs} 篇文档")

        return {
            "repos_count": len(repos),
            "docs_count": total_docs,
            "repos": repo_stats
        }

    async def _sync_repo_docs(self, client: YuqueClient, namespace: str,
                               with_content: bool, members: dict) -> list:
        """同步单个知识库的文档"""
        docs = await client.get_repo_docs(namespace)

        repo_dir = self.docs_dir / namespace.replace("/", "_")
        repo_dir.mkdir(parents=True, exist_ok=True)

        synced = []
        for doc in docs:
            doc_id = doc.get("id")
            slug = doc.get("slug", str(doc_id))
            title = doc.get("title", "Untitled")

            doc_info = {
                "id": doc_id,
                "slug": slug,
                "title": title,
                "description": doc.get("description", ""),
                "created_at": doc.get("created_at", ""),
                "updated_at": doc.get("updated_at", ""),
                "repo_namespace": namespace,
            }

            if with_content:
                try:
                    detail = await client.get_doc_detail(namespace, slug)
                    doc_info["content"] = detail.get("content", "")
                    doc_info["book"] = detail.get("book", {})

                    # 作者名
                    user_id = detail.get("user_id")
                    if user_id and str(user_id) in members:
                        doc_info["author"] = members[str(user_id)].get("name", "")
                    else:
                        doc_info["author"] = ""

                    # 写入 Markdown
                    md_content = self._build_markdown(doc_info)
                    filename = self._safe_filename(title, slug)
                    (repo_dir / f"{filename}.md").write_text(md_content, encoding="utf-8")

                except Exception as e:
                    logger.warning(f"获取文档详情失败 {slug}: {e}")

            synced.append(doc_info)

        logger.info(f"同步知识库 {namespace}，共 {len(synced)} 篇")
        return synced

    def _build_markdown(self, doc: dict) -> str:
        """构建 Markdown 文件"""
        fm = {
            "id": doc.get("id"),
            "title": doc.get("title", ""),
            "slug": doc.get("slug", ""),
            "created_at": doc.get("created_at", ""),
            "updated_at": doc.get("updated_at", ""),
        }

        if doc.get("author"):
            fm["author"] = doc["author"]
        if doc.get("book", {}).get("name"):
            fm["book_name"] = doc["book"]["name"]
        if doc.get("description"):
            fm["description"] = doc["description"]

        yaml_block = yaml.dump(fm, allow_unicode=True, default_flow_style=False, sort_keys=False).strip()

        md = f"---\n{yaml_block}\n---\n\n"

        # 元信息表格
        author = doc.get("author") or str(doc.get("user_id", ""))
        md += f"| 作者 | 创建时间 | 更新时间 |\n"
        md += f"|------|----------|----------|\n"
        md += f"| {author} | {doc.get('created_at', '')} | {doc.get('updated_at', '')} |\n\n"

        # 正文
        content = doc.get("content", "")
        if content:
            md += content

        return md

    def _safe_filename(self, title: str, slug: str) -> str:
        safe = re.sub(r'[<>:"/\\|?*]', '', title)
        safe = safe.strip()[:50]
        return safe or slug

    def get_docs_by_author(self, author_name: str) -> list[dict]:
        """获取指定作者的文档列表"""
        import yaml

        if not author_name:
            return []

        docs = []
        for md_file in self.docs_dir.rglob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")

                # 解析 frontmatter
                metadata = {}
                body = content

                if content.startswith("---"):
                    end = content.find("\n---", 3)
                    if end != -1:
                        try:
                            metadata = yaml.safe_load(content[3:end].strip()) or {}
                            body = content[end + 4:].strip()
                        except:
                            pass

                # 匹配作者
                doc_author = metadata.get("author", "")
                if doc_author == author_name:
                    docs.append({
                        "id": metadata.get("id"),
                        "title": metadata.get("title", ""),
                        "slug": metadata.get("slug", ""),
                        "description": metadata.get("description", ""),
                        "author": doc_author,
                        "book_name": metadata.get("book_name", ""),
                        "content": body,  # 添加正文内容
                    })
            except Exception as e:
                logger.warning(f"读取文档失败 {md_file}: {e}")

        return docs


# ============================================================================
# 用户画像生成器
# =============================================================================

class ProfileGenerator:
    """用户画像生成器（LLM 驱动）"""

    PROFILE_PROMPT = """你是一个专业的技术能力分析助手。请根据用户的文档信息，生成一份简洁的用户画像。

## 分析维度

1. **技术领域**：识别用户涉足的技术领域（不限于此列表，自由发现）
2. **认知水平**：评估用户在各领域的理解深度
   - beginner：刚开始接触，学习基础概念
   - intermediate：能独立完成项目，理解原理
   - advanced：深入底层，能优化和创新
3. **特点标签**：用户的学习风格、产出特点

## 用户文档信息

{docs_info}

## 输出格式

请严格按以下 JSON 格式输出，不要有多余内容：

```json
{{
  "interests": ["领域1", "领域2", "领域3"],
  "skills": {{
    "领域1": "intermediate",
    "领域2": "beginner",
    "领域3": "advanced"
  }},
  "level": "intermediate",
  "tags": ["标签1", "标签2"],
  "summary": "一句话概括这个用户的技术特点"
}}
```

注意：
- interests 最多 5 个领域
- skills 和 level 的值必须用英文：beginner / intermediate / advanced
- tags 最多 3 个标签
- 所有字段必须有值"""

    def build_docs_info(self, docs: list) -> str:
        """构建文档信息字符串"""
        if not docs:
            return "暂无文档"

        lines = []
        for i, doc in enumerate(docs[:30], 1):  # 最多30篇
            title = doc.get("title", "无标题")
            book = doc.get("book_name", "未知知识库")
            content = doc.get("content", "")[:200] if doc.get("content") else ""
            lines.append(f"{i}. [{book}] {title}")
            if content:
                lines.append(f"   摘要: {content[:100]}...")

        return "\n".join(lines)

    def _normalize_level(self, level: str) -> str:
        """标准化水平值（支持中英文）"""
        mapping = {
            "beginner": "beginner", "入门": "beginner", "初级": "beginner",
            "intermediate": "intermediate", "进阶": "intermediate", "中级": "intermediate",
            "advanced": "advanced", "高级": "advanced", "高级": "advanced",
        }
        return mapping.get(level.lower() if level else "", "beginner")

    async def generate_with_llm(self, docs: list, provider) -> dict:
        """使用 LLM 生成用户画像

        Args:
            docs: 文档列表
            provider: AstrBot LLM Provider

        Returns:
            画像字典
        """
        if not docs:
            return self._empty_profile()

        docs_info = self.build_docs_info(docs)
        prompt = self.PROFILE_PROMPT.format(docs_info=docs_info)

        try:
            resp = await provider.text_chat(
                prompt=prompt,
                context=[],
                system_prompt="你是一个专业的技术能力分析助手，输出格式必须是 JSON。"
            )

            result_text = resp.completion_text.strip()

            # 提取 JSON
            import json
            # 尝试从 markdown 代码块中提取
            if "```json" in result_text:
                start = result_text.find("```json") + 7
                end = result_text.find("```", start)
                result_text = result_text[start:end].strip()
            elif "```" in result_text:
                start = result_text.find("```") + 3
                end = result_text.find("```", start)
                result_text = result_text[start:end].strip()

            profile_data = json.loads(result_text)

            # 标准化水平值
            normalized_skills = {
                k: self._normalize_level(v)
                for k, v in profile_data.get("skills", {}).items()
            }

            # 构建返回格式
            return {
                "profile": {
                    "interests": profile_data.get("interests", []),
                    "level": self._normalize_level(profile_data.get("level", "beginner")),
                    "skills": normalized_skills,
                    "tags": profile_data.get("tags", []),
                    "summary": profile_data.get("summary", ""),
                },
                "stats": {
                    "docs_count": len(docs),
                    "repos": list(set(doc.get("book_name", "") for doc in docs if doc.get("book_name"))),
                }
            }

        except Exception as e:
            logger.error(f"LLM 生成画像失败: {e}")
            return self._empty_profile()

    def _empty_profile(self) -> dict:
        return {
            "profile": {"interests": [], "level": "beginner", "skills": {}, "tags": [], "summary": ""},
            "stats": {"docs_count": 0, "repos": []}
        }


# ============================================================================
# 主插件类
# ============================================================================

@register("novabot", "谷和平", "NOVA 社团智能助手", "0.5.0")
class NovaBotPlugin(Star):
    """NovaBot 主插件"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # 配置
        self.yuque_token = config.get("yuque_token", "")
        self.yuque_base_url = config.get("yuque_base_url", "https://nova.yuque.com/api/v2")
        self.embedding_api_key = config.get("embedding_api_key", "")
        self.embedding_base_url = config.get("embedding_base_url", "")
        self.embedding_model = config.get("embedding_model", "text-embedding-3-small")

        # 组件
        self.storage = Storage()
        self.yuque_sync = YuqueSync(self.storage)
        self.profile_gen = ProfileGenerator()
        self.client: Optional[YuqueClient] = None

        # RAG
        self.rag: Optional[RAGEngine] = None
        if self.embedding_api_key:
            try:
                rag_dir = self.storage.data_dir / "chroma_db"
                self.rag = RAGEngine(
                    persist_directory=str(rag_dir),
                    embedding_api_key=self.embedding_api_key,
                    embedding_base_url=self.embedding_base_url or None,
                    embedding_model=self.embedding_model,
                )
                # 验证数据库是否可用
                try:
                    self.rag.get_stats()
                    logger.info(f"RAG 引擎初始化完成，模型: {self.embedding_model}")
                except Exception as e:
                    logger.warning(f"RAG 数据库损坏，尝试重建: {e}")
                    self.rag.clear()
                    logger.info("RAG 数据库已重置")
            except Exception as e:
                logger.error(f"RAG 引擎初始化失败: {e}")

        logger.info("NovaBot 插件初始化完成 (v0.5.1)")

        # 注册 FunctionTool
        self._register_tools()

    def _register_tools(self):
        """注册 LLM 工具"""
        from dataclasses import dataclass, field
        from astrbot.api import FunctionTool

        @dataclass
        class SearchKnowledgeBaseTool(FunctionTool):
            """知识库搜索工具"""
            name: str = "search_knowledge_base"
            description: str = "搜索 NOVA 社团语雀知识库。当用户询问技术问题、项目信息、文档内容时使用。返回相关文档片段。"
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
            plugin: object = None

            async def run(self, event, query: str, top_k: int = 5):
                if not self.plugin or not self.plugin.rag:
                    return "知识库未初始化，请检查 embedding 配置"

                try:
                    results = self.plugin.rag.search(query, k=top_k)
                    if not results:
                        return f"未找到与「{query}」相关的内容"

                    output = []
                    for i, r in enumerate(results, 1):
                        title = r.get("title", "未知")
                        author = r.get("author", "")
                        content = r.get("content", "")[:300]
                        output.append(f"【{i}】{title}" + (f" (by {author})" if author else ""))
                        output.append(f"    {content}...")
                        output.append("")

                    return "\n".join(output)
                except Exception as e:
                    return f"搜索失败: {e}"

        @dataclass
        class GrepLocalDocsTool(FunctionTool):
            """本地文档关键词搜索工具"""
            name: str = "grep_local_docs"
            description: str = "在本地同步的语雀文档中进行关键词精确匹配搜索。适合查找特定代码、配置、名称等。"
            parameters: dict = field(default_factory=lambda: {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "要搜索的关键词"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最大返回结果数，默认 10",
                        "default": 10
                    }
                },
                "required": ["keyword"]
            })
            plugin: object = None

            async def run(self, event, keyword: str, max_results: int = 10):
                import re
                from pathlib import Path

                docs_dir = self.plugin.storage.data_dir / "yuque_docs"
                if not docs_dir.exists():
                    return "文档目录不存在，请先执行 /sync 同步"

                results = []
                pattern = re.compile(re.escape(keyword), re.IGNORECASE)

                for md_file in docs_dir.rglob("*.md"):
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

                            # 提取上下文
                            contexts = []
                            for m in matches[:3]:  # 每个文件最多3个匹配
                                start = max(0, m.start() - 50)
                                end = min(len(content), m.end() + 100)
                                ctx = content[start:end].replace("\n", " ")
                                contexts.append(f"...{ctx}...")

                            results.append({
                                "title": title,
                                "file": str(md_file.relative_to(docs_dir)),
                                "count": len(matches),
                                "contexts": contexts
                            })

                            if len(results) >= max_results:
                                break
                    except Exception as e:
                        continue

                if not results:
                    return f"未找到包含「{keyword}」的文档"

                output = [f"找到 {len(results)} 个文档包含「{keyword}」:\n"]
                for r in results:
                    output.append(f"📄 {r['title']} ({r['count']} 处)")
                    for ctx in r['contexts'][:1]:
                        output.append(f"   {ctx}")
                    output.append("")

                return "\n".join(output)

        @dataclass
        class ListKnowledgeBasesTool(FunctionTool):
            """列出知识库工具"""
            name: str = "list_knowledge_bases"
            description: str = "列出 NOVA 社团所有语雀知识库。了解有哪些知识库可以帮助你决定去哪个知识库搜索。"
            parameters: dict = field(default_factory=lambda: {
                "type": "object",
                "properties": {},
                "required": []
            })
            plugin: object = None

            async def run(self, event):
                repos_file = self.plugin.storage.data_dir / "yuque_repos.json"
                docs_dir = self.plugin.storage.data_dir / "yuque_docs"

                # 优先从缓存的 repos 文件读取
                if repos_file.exists():
                    try:
                        import json
                        repos = json.loads(repos_file.read_text(encoding="utf-8"))
                        output = ["📚 NOVA 知识库列表:\n"]
                        for repo in repos:
                            name = repo.get("name", "未知")
                            desc = repo.get("description", "") or ""
                            items = repo.get("items_count", 0)
                            output.append(f"• {name} ({items} 篇文档)")
                            if desc:
                                output.append(f"  {desc[:50]}{'...' if len(desc) > 50 else ''}")
                        return "\n".join(output)
                    except Exception as e:
                        logger.warning(f"读取知识库列表失败: {e}")

                # 备选：从目录结构读取
                if docs_dir.exists():
                    output = ["📚 NOVA 知识库列表:\n"]
                    for repo_dir in sorted(docs_dir.iterdir()):
                        if repo_dir.is_dir():
                            md_count = len(list(repo_dir.glob("*.md")))
                            output.append(f"• {repo_dir.name} ({md_count} 篇文档)")
                    return "\n".join(output)

                return "知识库列表为空，请先执行 /sync 同步"

        @dataclass
        class ListRepoDocsTool(FunctionTool):
            """列出知识库文档结构工具"""
            name: str = "list_repo_docs"
            description: str = "列出某个知识库下的所有文档。了解知识库结构后可以更有针对性地搜索。"
            parameters: dict = field(default_factory=lambda: {
                "type": "object",
                "properties": {
                    "repo_name": {
                        "type": "string",
                        "description": "知识库名称，如 'astrbot搭建'、'AI Agent试水'"
                    }
                },
                "required": ["repo_name"]
            })
            plugin: object = None

            async def run(self, event, repo_name: str):
                docs_dir = self.plugin.storage.data_dir / "yuque_docs"
                if not docs_dir.exists():
                    return "文档目录不存在，请先执行 /sync 同步"

                # 模糊匹配知识库目录
                matched_dir = None
                for d in docs_dir.iterdir():
                    if d.is_dir() and repo_name.lower() in d.name.lower():
                        matched_dir = d
                        break

                if not matched_dir:
                    # 列出可用的知识库
                    available = [d.name for d in docs_dir.iterdir() if d.is_dir()]
                    return f"未找到知识库「{repo_name}」\n可用知识库: {', '.join(available[:10])}"

                # 读取文档列表
                output = [f"📖 {matched_dir.name} 文档列表:\n"]
                md_files = sorted(matched_dir.glob("*.md"))

                for md_file in md_files[:30]:  # 最多显示30篇
                    try:
                        content = md_file.read_text(encoding="utf-8")
                        # 提取标题
                        title = md_file.stem
                        for line in content.split("\n")[:10]:
                            if line.startswith("# "):
                                title = line[2:].strip()
                                break
                        output.append(f"• {title}")
                    except:
                        output.append(f"• {md_file.stem}")

                if len(md_files) > 30:
                    output.append(f"\n... 还有 {len(md_files) - 30} 篇文档")

                output.append(f"\n共 {len(md_files)} 篇文档")
                return "\n".join(output)

        # 实例化并注册工具
        rag_tool = SearchKnowledgeBaseTool()
        rag_tool.plugin = self
        self.context.add_llm_tools(rag_tool)

        list_repos_tool = ListKnowledgeBasesTool()
        list_repos_tool.plugin = self
        self.context.add_llm_tools(list_repos_tool)

        list_docs_tool = ListRepoDocsTool()
        list_docs_tool.plugin = self
        self.context.add_llm_tools(list_docs_tool)

        if self.storage.data_dir.exists():
            grep_tool = GrepLocalDocsTool()
            grep_tool.plugin = self
            self.context.add_llm_tools(grep_tool)

        logger.info("LLM 工具注册完成: search_knowledge_base, list_knowledge_bases, list_repo_docs, grep_local_docs")

    def _get_client(self) -> YuqueClient:
        """获取语雀客户端（懒加载）"""
        if self.client is None:
            self.client = YuqueClient(self.yuque_token, self.yuque_base_url)
        return self.client

    async def _close_client(self):
        if self.client:
            await self.client.close()
            self.client = None

    # ========== LLM 钩子 ==========

    @filter.on_llm_request()
    async def on_llm_request(self, event, req):
        req.system_prompt += """

你是 NovaBot，NOVA 社团的智能助手。

【回答风格】
- 有温度，像学习伙伴
- 回答后追问「还想了解什么？」
- 标注来源：「根据《文档名》by 作者...」

【指令引导】
- 用户问「我的画像」→ 引导 /profile
- 用户要同步知识库 → 引导 /sync
"""

    # ========== 指令 ==========

    @filter.command("sync")
    async def sync_cmd(self, event: AstrMessageEvent, action: str = ""):
        """同步语雀知识库

        用法:
        - /sync - 同步所有知识库（后台运行）
        - /sync members - 同步团队成员
        - /sync status - 查看同步状态/进度
        """
        if not self.yuque_token:
            yield event.plain_result("❌ 未配置语雀 Token")
            return

        # 同步团队成员
        if action.lower() == "members":
            yield event.plain_result("🔄 同步团队成员...")

            client = self._get_client()
            try:
                count = await self.yuque_sync.sync_team_members(client)
                if count > 0:
                    yield event.plain_result(
                        f"✅ 团队成员同步完成\n"
                        f"共 {count} 人\n"
                        f"使用 /bind <用户名> 绑定账号"
                    )
                else:
                    yield event.plain_result("⚠️ 未获取到成员，请检查 Token 权限")
            except Exception as e:
                logger.error(f"同步团队成员失败: {e}")
                yield event.plain_result(f"❌ 同步失败: {e}")
            return

        # 查看状态
        if action.lower() == "status":
            state = self.storage.load_sync_state()

            # 检查是否正在同步
            if state.get("in_progress") and state.get("progress"):
                p = state["progress"]
                yield event.plain_result(
                    f"⏳ 同步进行中\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"进度: {p['current']}/{p['total']}\n"
                    f"当前: {p['current_repo']}\n\n"
                    f"使用 /sync status 刷新进度"
                )
                return

            if state.get("last_sync"):
                lines = [
                    f"📊 同步状态",
                    "━━━━━━━━━━━━━━━",
                    f"上次同步: {state['last_sync'][:19]}",
                    f"知识库数: {len(state.get('repos', {}))}",
                    f"文档总数: {state.get('docs_count', 0)}",
                    f"Token 类型: {state.get('token_type', '未知')}",
                ]
                yield event.plain_result("\n".join(lines))
            else:
                yield event.plain_result("尚未同步，使用 /sync 开始")
            return

        # 检查是否已在同步
        state = self.storage.load_sync_state()
        if state.get("in_progress"):
            p = state.get("progress", {})
            yield event.plain_result(
                f"⏳ 同步已在进行中\n"
                f"进度: {p.get('current', 0)}/{p.get('total', 0)}\n"
                f"使用 /sync status 查看进度"
            )
            return

        # 启动后台同步
        asyncio.create_task(self._background_sync())
        yield event.plain_result(
            "🔄 同步已启动（后台运行）\n"
            "使用 /sync status 查看进度"
        )

    async def _background_sync(self):
        """后台同步任务"""
        client = self._get_client()
        try:
            result = await self.yuque_sync.sync_all_repos(client, with_content=True)

            # RAG 索引
            if self.rag:
                try:
                    indexed = self.rag.index_from_sync(str(self.yuque_sync.docs_dir))
                    logger.info(f"RAG 索引完成: {indexed} 篇")
                except Exception as e:
                    logger.error(f"RAG 索引失败: {e}")

            logger.info(f"后台同步完成: {result['docs_count']} 篇文档")

        except Exception as e:
            logger.error(f"后台同步失败: {e}", exc_info=True)
            # 标记同步结束
            state = self.storage.load_sync_state()
            state["in_progress"] = False
            state["progress"] = None
            self.storage.save_sync_state(state)

    @filter.command("bind")
    async def bind_cmd(self, event: AstrMessageEvent, arg: str = ""):
        """绑定语雀账号

        用法: /bind <用户名或 login>
        """
        platform_id = event.get_sender_id()

        # 检查已有绑定
        existing = self.storage.get_binding(platform_id)
        if existing:
            yield event.plain_result(
                f"已绑定 @{existing['yuque_login']}\n"
                f"使用 /unbind 解绑后重新绑定"
            )
            return

        if not arg:
            yield event.plain_result(
                "请提供用户名:\n"
                "/bind <用户名>\n\n"
                "例如: /bind 张三"
            )
            return

        # 检查成员数据
        members = self.storage.load_members()
        if not members:
            yield event.plain_result(
                "❌ 团队成员未同步\n"
                "请先执行 /sync members"
            )
            return

        # 查找用户
        matched = self.storage.find_member_by_name(arg)
        if not matched:
            sample = [info.get("name", "") for info in list(members.values())[:5]]
            yield event.plain_result(
                f"❌ 未找到「{arg}」\n"
                f"成员示例: {', '.join(sample)}"
            )
            return

        # 绑定
        self.storage.add_binding(platform_id, {
            "yuque_id": matched["id"],
            "yuque_login": matched.get("login", ""),
            "yuque_name": matched.get("name", ""),
        })

        yield event.plain_result(
            f"✅ 绑定成功\n"
            f"━━━━━━━━━━━━━━━\n"
            f"账号: @{matched.get('login', '')} ({matched.get('name', '')})\n"
            f"\n"
            f"💡 使用 /profile refresh 生成用户画像"
        )

    @filter.command("unbind")
    async def unbind_cmd(self, event: AstrMessageEvent):
        """解除绑定"""
        platform_id = event.get_sender_id()
        binding = self.storage.get_binding(platform_id)

        if not binding:
            yield event.plain_result("你还没有绑定账号")
            return

        self.storage.remove_binding(platform_id)
        yield event.plain_result(f"✅ 已解除绑定 @{binding.get('yuque_login', '')}")

    @filter.command("profile")
    async def profile_cmd(self, event: AstrMessageEvent, action: str = ""):
        """查看用户画像

        用法:
        - /profile - 查看画像
        - /profile refresh - 使用 AI 深度分析生成画像
        """
        platform_id = event.get_sender_id()
        binding = self.storage.get_binding(platform_id)

        if not binding:
            yield event.plain_result("请先使用 /bind 绑定账号")
            return

        yuque_id = binding.get("yuque_id")
        yuque_name = binding.get("yuque_name", "")
        yuque_login = binding.get("yuque_login", "")

        # 刷新画像（使用 LLM 深度分析）
        if action.lower() == "refresh":
            # 获取文档
            docs = self.yuque_sync.get_docs_by_author(yuque_name)
            if not docs:
                yield event.plain_result("⚠️ 未找到你的文档，请先执行 /sync 同步")
                return

            # 获取 LLM Provider
            try:
                provider = self.context.get_using_provider(umo=event.unified_msg_origin)
                if not provider:
                    yield event.plain_result("❌ LLM 未配置，请先配置模型 Provider")
                    return

                yield event.plain_result(f"🔍 正在分析 {len(docs)} 篇文档...")

                # 使用 LLM 生成画像
                profile = await self.profile_gen.generate_with_llm(docs, provider)
                self.storage.save_profile(yuque_id, profile)

                level_map = {"beginner": "入门", "intermediate": "进阶", "advanced": "高级"}
                p = profile.get("profile", {})
                skills = p.get("skills", {})
                skill_lines = [f"• {k} ({level_map.get(v, v)})" for k, v in skills.items()]

                yield event.plain_result(
                    f"✅ 画像已生成\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"兴趣: {', '.join(p.get('interests', []))}\n"
                    f"水平: {level_map.get(p.get('level', ''), '未知')}\n"
                    f"标签: {', '.join(p.get('tags', []))}\n"
                    f"\n"
                    f"📝 {p.get('summary', '')}"
                )
            except Exception as e:
                logger.error(f"生成画像失败: {e}", exc_info=True)
                yield event.plain_result(f"❌ 生成失败: {e}")
            return

        # 显示画像
        profile = self.storage.load_profile(yuque_id)
        level_map = {"beginner": "入门", "intermediate": "进阶", "advanced": "高级"}

        if profile:
            p = profile.get("profile", {})
            stats = profile.get("stats", {})

            # 构建技能显示
            skills = p.get("skills", {})
            skill_lines = []
            for interest in p.get("interests", []):
                skill_level = skills.get(interest, "beginner")
                skill_lines.append(f"• {interest} ({level_map.get(skill_level, '入门')})")

            # 构建知识库显示
            repos = stats.get("repos", [])
            repos_str = ", ".join(repos[:3])
            if len(repos) > 3:
                repos_str += f" 等 {len(repos)} 个"

            lines = [
                f"📋 用户画像",
                f"━━━━━━━━━━━━━━━",
                f"账号: @{yuque_login} ({yuque_name})",
                "",
                f"🎯 兴趣领域",
            ]
            if skill_lines:
                lines.extend(skill_lines)
            else:
                lines.append("暂无数据")

            # 标签
            tags = p.get("tags", [])
            if tags:
                lines.extend(["", f"🏷️ 标签", f"• {' • '.join(tags)}"])

            lines.extend([
                "",
                f"📊 统计",
                f"• 文档数: {stats.get('docs_count', 0)} 篇",
                f"• 知识库: {repos_str or '暂无'}",
                f"• 整体水平: {level_map.get(p.get('level', ''), '未知')}",
            ])

            # 概括
            summary = p.get("summary", "")
            if summary:
                lines.extend(["", f"📝 {summary}"])

            lines.extend(["", f"💡 使用 /profile refresh 重新分析"])

            yield event.plain_result("\n".join(lines))
        else:
            yield event.plain_result(
                f"📋 用户画像\n"
                f"━━━━━━━━━━━━━━━\n"
                f"账号: @{yuque_login} ({yuque_name})\n"
                f"\n"
                f"画像未生成\n"
                f"使用 /profile refresh 生成画像"
            )

    @filter.command("rag")
    async def rag_cmd(self, event: AstrMessageEvent, action: str = "", query: str = ""):
        """RAG 检索

        用法:
        - /rag status - 查看状态
        - /rag search <关键词> - 搜索
        - /rag rebuild - 重建索引
        """
        if not self.rag:
            yield event.plain_result("❌ RAG 未初始化，请配置 embedding_api_key")
            return

        if action.lower() == "status":
            try:
                stats = self.rag.get_stats()
                yield event.plain_result(
                    f"📊 RAG 状态\n"
                    f"模型: {self.embedding_model}\n"
                    f"文档数: {stats.get('docs_count', 0)}"
                )
            except Exception as e:
                logger.error(f"获取 RAG 状态失败: {e}")
                yield event.plain_result(f"⚠️ RAG 状态异常: {e}")
            return

        if action.lower() == "search" and query:
            try:
                results = self.rag.search(query, k=5)
                if not results:
                    yield event.plain_result(f"未找到相关文档: {query}")
                    return

                lines = [f"🔍 搜索: {query}", "━━━━━━━━━━━━━━━"]
                for i, doc in enumerate(results, 1):
                    lines.append(f"{i}. {doc['title']}")
                    lines.append(f"   {doc['content'][:80]}...")

                yield event.plain_result("\n".join(lines))
            except Exception as e:
                logger.error(f"RAG 搜索失败: {e}")
                yield event.plain_result(f"❌ 搜索失败: {e}")
            return

        if action.lower() == "rebuild":
            try:
                yield event.plain_result("🔄 重建 RAG 索引...")
                if not self.rag.clear():
                    yield event.plain_result("❌ 清空向量库失败")
                    return
                indexed = self.rag.index_from_sync(str(self.yuque_sync.docs_dir))
                yield event.plain_result(f"✅ 重建完成，索引 {indexed} 篇文档")
            except Exception as e:
                logger.error(f"RAG 重建失败: {e}", exc_info=True)
                yield event.plain_result(f"❌ 重建失败: {e}")
            return

        yield event.plain_result(
            "📚 RAG 检索\n"
            "• /rag status - 状态\n"
            "• /rag search <关键词> - 搜索\n"
            "• /rag rebuild - 重建索引"
        )

    @filter.command("novabot")
    async def help_cmd(self, event: AstrMessageEvent):
        """帮助信息"""
        yield event.plain_result(
            "🤖 NovaBot - NOVA 社团智能助手\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📖 知识库\n"
            "  /sync - 同步知识库\n"
            "  /sync members - 同步成员\n"
            "  /sync status - 同步状态\n"
            "\n"
            "👤 账号\n"
            "  /bind <用户名> - 绑定账号\n"
            "  /unbind - 解除绑定\n"
            "  /profile - 查看画像\n"
            "  /profile refresh - 刷新画像\n"
            "\n"
            "🔍 RAG 检索\n"
            "  /rag status - 查看状态\n"
            "  /rag search <关键词> - 搜索\n"
            "  /rag rebuild - 重建索引\n"
            "\n"
            "  /novabot - 帮助"
        )

    async def terminate(self):
        await self._close_client()
        logger.info("NovaBot 插件已卸载")