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


# ============================================================================
# 用户画像生成器
# ============================================================================

class ProfileGenerator:
    """用户画像生成器"""

    INTEREST_KEYWORDS = {
        "AI Agent": ["agent", "智能体", "autonomous"],
        "Python": ["python", "pip", "django", "flask", "fastapi"],
        "爬虫": ["爬虫", "crawler", "spider", "scrapy"],
        "LLM": ["llm", "gpt", "claude", "prompt", "chatgpt", "大模型"],
        "数据分析": ["数据分析", "pandas", "numpy", "可视化"],
        "前端": ["前端", "react", "vue", "css", "javascript"],
        "后端": ["后端", "api", "server", "database", "mysql"],
        "AstrBot": ["astrbot", "机器人", "bot"],
        "RAG": ["rag", "向量", "embedding", "检索"],
    }

    LEVEL_KEYWORDS = {
        "advanced": ["原理", "源码", "架构", "优化", "性能"],
        "intermediate": ["项目", "实践", "实现", "开发", "实战"],
        "beginner": ["入门", "基础", "教程", "学习", "新手"]
    }

    def generate_from_docs(self, docs: list) -> dict:
        if not docs:
            return self._empty_profile()

        interest_scores = {k: 0 for k in self.INTEREST_KEYWORDS}
        level_scores = {k: 0 for k in self.LEVEL_KEYWORDS}

        for doc in docs:
            text = f"{doc.get('title', '')} {doc.get('description', '')}".lower()

            for interest, keywords in self.INTEREST_KEYWORDS.items():
                for kw in keywords:
                    if kw in text:
                        interest_scores[interest] += 1

            for level, keywords in self.LEVEL_KEYWORDS.items():
                for kw in keywords:
                    if kw in text:
                        level_scores[level] += 1

        interests = [k for k, v in sorted(interest_scores.items(), key=lambda x: -x[1]) if v >= 2][:5]

        if level_scores["advanced"] >= 3:
            level = "advanced"
        elif level_scores["intermediate"] >= 3 or level_scores["advanced"] >= 1:
            level = "intermediate"
        else:
            level = "beginner"

        return {
            "profile": {"interests": interests, "level": level},
            "stats": {"docs_count": len(docs)}
        }

    def _empty_profile(self) -> dict:
        return {
            "profile": {"interests": [], "level": "beginner"},
            "stats": {"docs_count": 0}
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
            f"账号: @{matched.get('login', '')} ({matched.get('name', '')})"
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
    async def profile_cmd(self, event: AstrMessageEvent):
        """查看用户画像"""
        platform_id = event.get_sender_id()
        binding = self.storage.get_binding(platform_id)

        if not binding:
            yield event.plain_result("请先使用 /bind 绑定账号")
            return

        yuque_id = binding.get("yuque_id")
        profile = self.storage.load_profile(yuque_id)

        level_map = {"beginner": "入门", "intermediate": "进阶", "advanced": "高级"}

        if profile:
            p = profile.get("profile", {})
            yield event.plain_result(
                f"📋 用户画像\n"
                f"━━━━━━━━━━━━━━━\n"
                f"账号: @{binding.get('yuque_login', '')}\n"
                f"兴趣: {', '.join(p.get('interests', [])) or '暂无'}\n"
                f"水平: {level_map.get(p.get('level', ''), '未知')}"
            )
        else:
            yield event.plain_result(
                f"📋 用户画像\n"
                f"账号: @{binding.get('yuque_login', '')}\n"
                f"画像未生成，同步后自动生成"
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
            "• /sync - 同步知识库\n"
            "• /sync members - 同步团队成员\n"
            "• /sync status - 查看状态\n"
            "• /bind <用户名> - 绑定账号\n"
            "• /unbind - 解绑\n"
            "• /profile - 用户画像\n"
            "• /rag search <关键词> - 搜索文档\n"
            "• /novabot - 帮助"
        )

    async def terminate(self):
        await self._close_client()
        logger.info("NovaBot 插件已卸载")