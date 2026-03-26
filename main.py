"""
NovaBot - NOVA 社团智能助手
以语雀知识库为核心的 AstrBot Plugin
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .novabot.rag import RAGEngine


class YuqueClient:
    """语雀 API 客户端"""

    def __init__(self, token: str, base_url: str = "https://nova.yuque.com/api/v2"):
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "X-Auth-Token": token,
            "User-Agent": "NovaBot/1.0",
        }
        self.client = httpx.AsyncClient(headers=self.headers, timeout=30.0)

    async def get_user_info(self) -> dict:
        """获取当前认证用户信息"""
        resp = await self.client.get(f"{self.base_url}/user")
        resp.raise_for_status()
        return resp.json().get("data", {})

    async def get_user_docs(self, user_id: int, limit: int = 100) -> list:
        """获取用户的文档列表"""
        resp = await self.client.get(
            f"{self.base_url}/users/{user_id}/docs",
            params={"limit": limit}
        )
        resp.raise_for_status()
        return resp.json().get("data", [])

    async def get_repos(self, user_id: int, limit: int = 100) -> list:
        """获取用户的知识库列表"""
        resp = await self.client.get(
            f"{self.base_url}/users/{user_id}/repos",
            params={"limit": limit}
        )
        resp.raise_for_status()
        return resp.json().get("data", [])

    async def get_repo_docs(self, repo_namespace: str, limit: int = 100) -> list:
        """获取知识库的文档列表"""
        resp = await self.client.get(
            f"{self.base_url}/repos/{repo_namespace}/docs",
            params={"limit": limit}
        )
        resp.raise_for_status()
        return resp.json().get("data", [])

    async def get_doc_detail(self, repo_namespace: str, slug: str) -> dict:
        """获取文档详情（含正文）"""
        resp = await self.client.get(
            f"{self.base_url}/repos/{repo_namespace}/docs/{slug}",
            params={"include_content": "true"}
        )
        resp.raise_for_status()
        return resp.json().get("data", {})

    async def close(self):
        await self.client.aclose()


class YuqueSync:
    """语雀文档同步器"""

    def __init__(self, data_dir: str = "data/nova"):
        self.data_dir = Path(data_dir)
        self.docs_dir = self.data_dir / "yuque_docs"
        self.repos_dir = self.data_dir / "yuque_repos"
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        self.repos_dir.mkdir(parents=True, exist_ok=True)

    def load_sync_state(self) -> dict:
        """加载同步状态"""
        state_file = self.data_dir / "sync_state.json"
        if state_file.exists():
            return json.loads(state_file.read_text(encoding="utf-8"))
        return {"last_sync": None, "repos": {}, "docs_count": 0}

    def save_sync_state(self, state: dict):
        """保存同步状态"""
        state_file = self.data_dir / "sync_state.json"
        state_file.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    async def sync_user_repos(self, client: YuqueClient, user_id: int) -> list:
        """同步用户知识库列表"""
        repos = await client.get_repos(user_id, limit=100)
        
        # 保存知识库列表
        repos_file = self.repos_dir / f"user_{user_id}_repos.json"
        repos_file.write_text(
            json.dumps(repos, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        
        logger.info(f"同步知识库列表完成，共 {len(repos)} 个知识库")
        return repos

    async def sync_repo_docs(self, client: YuqueClient, repo_namespace: str, with_content: bool = False) -> list:
        """同步知识库文档"""
        docs = await client.get_repo_docs(repo_namespace, limit=100)
        
        synced_docs = []
        for doc in docs:
            doc_info = {
                "id": doc.get("id"),
                "slug": doc.get("slug"),
                "title": doc.get("title"),
                "description": doc.get("description", ""),
                "updated_at": doc.get("updated_at"),
                "created_at": doc.get("created_at"),
                "word_count": doc.get("word_count", 0),
                "repo_namespace": repo_namespace,
            }
            
            # 可选：同步正文内容
            if with_content:
                try:
                    detail = await client.get_doc_detail(repo_namespace, doc["slug"])
                    doc_info["content"] = detail.get("content", "")
                    doc_info["content_html"] = detail.get("content_html", "")
                except Exception as e:
                    logger.warning(f"获取文档正文失败 {doc['slug']}: {e}")
            
            synced_docs.append(doc_info)
        
        # 保存文档列表
        docs_file = self.docs_dir / f"{repo_namespace.replace('/', '_')}_docs.json"
        docs_file.write_text(
            json.dumps(synced_docs, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        
        logger.info(f"同步知识库 {repo_namespace} 完成，共 {len(synced_docs)} 篇文档")
        return synced_docs

    async def full_sync(self, client: YuqueClient, user_id: int, with_content: bool = True) -> dict:
        """全量同步用户所有知识库"""
        state = self.load_sync_state()
        
        # 同步知识库列表
        repos = await self.sync_user_repos(client, user_id)
        
        total_docs = 0
        repo_stats = {}
        
        for repo in repos:
            namespace = repo.get("namespace", "")
            if not namespace:
                continue
            
            try:
                docs = await self.sync_repo_docs(client, namespace, with_content=with_content)
                total_docs += len(docs)
                repo_stats[namespace] = {
                    "name": repo.get("name", ""),
                    "docs_count": len(docs),
                    "synced_at": datetime.now().isoformat()
                }
            except Exception as e:
                logger.error(f"同步知识库 {namespace} 失败: {e}")
                repo_stats[namespace] = {"error": str(e)}
        
        # 更新同步状态
        state["last_sync"] = datetime.now().isoformat()
        state["repos"] = repo_stats
        state["docs_count"] = total_docs
        self.save_sync_state(state)
        
        return {
            "repos_count": len(repos),
            "docs_count": total_docs,
            "repos": repo_stats
        }


class Storage:
    """数据存储工具"""

    def __init__(self, data_dir: str = "data/nova"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.bindings_file = self.data_dir / "bindings.json"
        self.profiles_dir = self.data_dir / "user_profiles"
        self.profiles_dir.mkdir(parents=True, exist_ok=True)

    def load_bindings(self) -> dict:
        """加载绑定关系"""
        if self.bindings_file.exists():
            return json.loads(self.bindings_file.read_text(encoding="utf-8"))
        return {}

    def save_bindings(self, bindings: dict):
        """保存绑定关系"""
        self.bindings_file.write_text(
            json.dumps(bindings, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def get_binding(self, platform_id: str) -> Optional[dict]:
        """获取用户的绑定信息"""
        bindings = self.load_bindings()
        return bindings.get(platform_id)

    def add_binding(self, platform_id: str, yuque_info: dict):
        """添加绑定"""
        bindings = self.load_bindings()
        bindings[platform_id] = {
            **yuque_info,
            "bind_time": datetime.now().isoformat(),
            "last_sync": None
        }
        self.save_bindings(bindings)

    def remove_binding(self, platform_id: str):
        """移除绑定"""
        bindings = self.load_bindings()
        if platform_id in bindings:
            del bindings[platform_id]
            self.save_bindings(bindings)

    def find_yuque_binding(self, yuque_id: int) -> Optional[tuple]:
        """查找语雀 ID 被谁绑定"""
        bindings = self.load_bindings()
        for platform_id, info in bindings.items():
            if info.get("yuque_id") == yuque_id:
                return platform_id, info
        return None

    def load_profile(self, yuque_id: int) -> Optional[dict]:
        """加载用户画像"""
        profile_file = self.profiles_dir / f"{yuque_id}.json"
        if profile_file.exists():
            return json.loads(profile_file.read_text(encoding="utf-8"))
        return None

    def save_profile(self, yuque_id: int, profile: dict):
        """保存用户画像"""
        profile_file = self.profiles_dir / f"{yuque_id}.json"
        profile["updated_at"] = datetime.now().isoformat()
        profile_file.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )


class ProfileGenerator:
    """用户画像生成器 - 基于关键词提取"""

    # 兴趣领域关键词映射
    INTEREST_KEYWORDS = {
        "AI Agent": ["agent", "智能体", "autonomous", "agent"],
        "Python": ["python", "pip", "django", "flask", "fastapi"],
        "爬虫": ["爬虫", "crawler", "spider", "scrapy", "requests", "selenium"],
        "LLM": ["llm", "gpt", "claude", "prompt", "chatgpt", "大模型"],
        "数据分析": ["数据分析", "pandas", "numpy", "visualization", "可视化"],
        "前端": ["前端", "react", "vue", "css", "javascript", "typescript"],
        "后端": ["后端", "api", "server", "database", "mysql", "redis"],
        "AstrBot": ["astrbot", "astrbot", "机器人", "bot"],
        "RAG": ["rag", "向量", "embedding", "检索"],
    }

    # 技能水平关键词
    LEVEL_KEYWORDS = {
        "advanced": ["原理", "源码", "架构", "优化", "性能", "深入"],
        "intermediate": ["项目", "实践", "实现", "开发", "实战"],
        "beginner": ["入门", "基础", "教程", "学习", "初学", "新手"]
    }

    def generate_from_docs(self, docs: list) -> dict:
        """
        从文档列表生成画像
        
        Args:
            docs: 文档列表，每个文档包含 title, description 等字段
        
        Returns:
            画像字典
        """
        if not docs:
            return self._empty_profile()

        # 统计关键词
        interest_scores = {k: 0 for k in self.INTEREST_KEYWORDS}
        level_scores = {"advanced": 0, "intermediate": 0, "beginner": 0}
        
        doc_titles = []
        for doc in docs:
            title = doc.get("title", "")
            description = doc.get("description", "")
            combined = f"{title} {description}".lower()
            doc_titles.append(title)
            
            # 统计兴趣关键词
            for interest, keywords in self.INTEREST_KEYWORDS.items():
                for kw in keywords:
                    if kw.lower() in combined:
                        interest_scores[interest] += 1
            
            # 统计水平关键词
            for level, keywords in self.LEVEL_KEYWORDS.items():
                for kw in keywords:
                    if kw in combined:
                        level_scores[level] += 1

        # 提取 top 兴趣（分数 >= 2）
        interests = [
            k for k, v in sorted(interest_scores.items(), key=lambda x: -x[1])
            if v >= 2
        ][:5]

        # 判断水平
        if level_scores["advanced"] >= 3:
            level = "advanced"
        elif level_scores["intermediate"] >= 3 or level_scores["advanced"] >= 1:
            level = "intermediate"
        else:
            level = "beginner"

        return {
            "profile": {
                "interests": interests,
                "level": level,
                "collaboration_style": "solo",  # 默认，后续可分析协作文档
                "learning_pace": "steady"
            },
            "stats": {
                "docs_count": len(docs),
                "docs_titles": doc_titles[:10]  # 只保留前 10 个标题
            }
        }

    def _empty_profile(self) -> dict:
        """返回空画像"""
        return {
            "profile": {
                "interests": [],
                "level": "beginner",
                "collaboration_style": "solo",
                "learning_pace": "steady"
            },
            "stats": {
                "docs_count": 0,
                "docs_titles": []
            }
        }


@register("novabot", "谷和平", "NOVA 社团智能助手，以语雀知识库为核心", "0.2.0")
class NovaBotPlugin(Star):
    """NovaBot 主插件类"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.storage = Storage()
        self.yuque_sync = YuqueSync()
        self.profile_generator = ProfileGenerator()
        self.yuque_token = config.get("yuque_token", "")
        self.yuque_base_url = config.get("yuque_base_url", "https://nova.yuque.com/api/v2")
        
        # RAG 配置
        self.embedding_api_key = config.get("embedding_api_key", "")
        self.embedding_base_url = config.get("embedding_base_url", "")
        self.embedding_model = config.get("embedding_model", "text-embedding-3-small")
        
        # 初始化 RAG 引擎
        self.rag: Optional[RAGEngine] = None
        if self.embedding_api_key:
            try:
                self.rag = RAGEngine(
                    persist_directory=str(self.storage.data_dir / "chroma_db"),
                    embedding_api_key=self.embedding_api_key,
                    embedding_base_url=self.embedding_base_url or None,
                    embedding_model=self.embedding_model,
                )
                logger.info(f"RAG 引擎初始化完成，模型: {self.embedding_model}")
            except Exception as e:
                logger.error(f"RAG 引擎初始化失败: {e}")
        
        logger.info("NovaBot 插件初始化完成 (v0.2.0)")

    @filter.on_llm_request()
    async def on_llm_request(self, event, req):
        """LLM 请求前钩子：添加系统提示引导检索行为"""
        req.system_prompt += """

你是 NovaBot，NOVA 社团的智能助手。语雀知识库是你的知识来源。

【检索指引】
- 用户提问涉及技术文档、教程、项目经验时 → 使用 gno.query 工具搜索
- 用户问「有什么文档」「谁写过」→ 使用 gno.search 工具
- 搜索结果中会包含文档来源，回答时请标注

【回答风格】
- 有温度，像学习伙伴而不是机器
- 回答后追问「还想了解什么？」
- 标注来源：「根据《文档名》by 作者...」

【个人信息】
- 用户问「我的画像」「我写过什么」→ 引导使用 /profile 指令
- 用户要绑定语雀 → 引导使用 /bind 指令
- 用户要同步知识库 → 引导使用 /sync 指令
"""

    @filter.command("sync")
    async def sync(self, event: AstrMessageEvent, action: str = ""):
        """同步语雀知识库
        
        用法: 
        - /sync - 全量同步当前绑定用户的知识库
        - /sync status - 查看同步状态
        """
        platform_id = event.get_sender_id()
        binding = self.storage.get_binding(platform_id)
        
        if not binding:
            yield event.plain_result(
                "你还没有绑定语雀账号\n"
                "请使用 /bind 绑定"
            )
            return
        
        yuque_id = binding.get("yuque_id")
        yuque_login = binding.get("yuque_login", "未知")
        token = binding.get("token", "")
        
        # 查看同步状态
        if action.lower() == "status":
            state = self.yuque_sync.load_sync_state()
            
            if state.get("last_sync"):
                repos_info = []
                for ns, info in state.get("repos", {}).items():
                    if "error" in info:
                        repos_info.append(f"  ❌ {ns}: {info['error']}")
                    else:
                        repos_info.append(f"  ✅ {info.get('name', ns)}: {info.get('docs_count', 0)} 篇")
                
                yield event.plain_result(
                    f"📊 同步状态\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"语雀账号：@{yuque_login}\n"
                    f"上次同步：{state['last_sync'][:19]}\n"
                    f"知识库数：{len(state.get('repos', {}))}\n"
                    f"文档总数：{state.get('docs_count', 0)} 篇\n"
                    f"━━━━━━━━━━━━━━━\n"
                    + "\n".join(repos_info[:10])
                )
            else:
                yield event.plain_result(
                    f"📊 同步状态\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"语雀账号：@{yuque_login}\n"
                    f"尚未同步\n"
                    f"使用 /sync 开始同步"
                )
            return
        
        # 执行全量同步
        if not token:
            yield event.plain_result("无法同步：Token 未保存，请重新绑定")
            return
        
        yield event.plain_result(f"🔄 开始同步 @{yuque_login} 的知识库...")
        
        try:
            client = YuqueClient(token, self.yuque_base_url)
            result = await self.yuque_sync.full_sync(client, yuque_id, with_content=True)
            await client.close()
            
            # 更新绑定中的同步时间
            bindings = self.storage.load_bindings()
            if platform_id in bindings:
                bindings[platform_id]["last_sync"] = datetime.now().isoformat()
                self.storage.save_bindings(bindings)
            
            # 构建结果
            repos_list = []
            for ns, info in result.get("repos", {}).items():
                if "error" not in info:
                    repos_list.append(f"• {info.get('name', ns)}: {info.get('docs_count', 0)} 篇")
            
            # 同步到 RAG 向量库
            rag_status = ""
            if self.rag:
                try:
                    indexed = self.rag.index_from_sync(str(self.yuque_sync.docs_dir))
                    rag_status = f"\n📚 已索引到 RAG: {indexed} 篇文档"
                    logger.info(f"RAG 索引完成: {indexed} 篇文档")
                except Exception as e:
                    logger.error(f"RAG 索引失败: {e}")
                    rag_status = f"\n⚠️ RAG 索引失败: {str(e)}"
            
            yield event.plain_result(
                f"✅ 同步完成！\n"
                f"━━━━━━━━━━━━━━━\n"
                f"知识库：{result['repos_count']} 个\n"
                f"文档数：{result['docs_count']} 篇\n"
                f"━━━━━━━━━━━━━━━\n"
                + "\n".join(repos_list[:10])
                + rag_status
            )
            
        except httpx.HTTPStatusError as e:
            logger.error(f"同步失败: {e}")
            yield event.plain_result("❌ 同步失败：API 请求错误")
        except Exception as e:
            logger.error(f"同步失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 同步失败：{str(e)}")

    @filter.command("bind")
    async def bind(self, event: AstrMessageEvent, arg: str = ""):
        """绑定语雀账号
        
        用法: 
        - /bind <语雀 Token>
        - /bind confirm（确认绑定冲突）
        """
        platform_id = event.get_sender_id()
        
        # 检查是否已有绑定
        existing = self.storage.get_binding(platform_id)
        if existing:
            yield event.plain_result(
                f"❌ 你的账号已绑定语雀账号 @{existing['yuque_login']}\n"
                f"如需更换，请先使用 /unbind 解绑。"
            )
            return

        # 检查是否是确认绑定
        if arg.lower() == "confirm":
            # 从会话状态获取待确认的绑定信息（使用 hash 避免中文属性名问题）
            pending_key = f"_pb_{hash(platform_id)}"
            pending = getattr(self, pending_key, None)
            if not pending:
                yield event.plain_result("没有待确认的绑定请求，请重新执行 /bind")
                return
            
            # 执行绑定
            self.storage.add_binding(platform_id, pending["yuque_info"])
            delattr(self, pending_key)
            
            yield event.plain_result(
                f"✅ 绑定成功！\n"
                f"语雀账号：@{pending['yuque_info']['yuque_login']} "
                f"({pending['yuque_info']['yuque_name']})\n"
                f"使用 /sync 同步知识库"
            )
            return

        # 检查参数
        if not arg:
            yield event.plain_result(
                "请提供语雀 Token：\n"
                "/bind <语雀 Token>\n"
                "\n"
                "Token 获取方式：\n"
                "1. 登录语雀 → 个人设置 → Token\n"
                "2. 创建一个有读取权限的 Token"
            )
            return
        
        # 简单验证：Token 通常是字母数字组成的长字符串
        if len(arg) < 20 or not all(c.isalnum() or c in '-_' for c in arg):
            yield event.plain_result(
                "⚠️ 这看起来不像有效的语雀 Token\n"
                "\n"
                "Token 获取方式：\n"
                "1. 登录语雀 → 个人设置 → Token\n"
                "2. 创建一个有读取权限的 Token\n"
                "3. Token 通常是一串字母和数字"
            )
            return

        # 尝试作为 Token 验证
        try:
            client = YuqueClient(arg, self.yuque_base_url)
            user_info = await client.get_user_info()
            await client.close()
            
            yuque_id = user_info["id"]
            yuque_login = user_info["login"]
            yuque_name = user_info.get("name", yuque_login)
            
            # 检查语雀账号是否被他人绑定
            existing_binding = self.storage.find_yuque_binding(yuque_id)
            if existing_binding:
                bound_platform_id, bound_info = existing_binding
                if bound_platform_id != platform_id:
                    # 需要确认（使用 hash 避免中文属性名问题）
                    pending_key = f"_pb_{hash(platform_id)}"
                    setattr(self, pending_key, {
                        "yuque_info": {
                            "yuque_id": yuque_id,
                            "yuque_login": yuque_login,
                            "yuque_name": yuque_name,
                            "token": arg  # 保存 token 用于后续操作
                        }
                    })
                    yield event.plain_result(
                        f"⚠️ 语雀账号 @{yuque_login} 已被另一个账号绑定。\n"
                        f"确认要绑定吗？（这会解除原绑定）\n"
                        f"\n"
                        f"输入 /bind confirm 确认绑定"
                    )
                    return
            
            # 直接绑定
            self.storage.add_binding(platform_id, {
                "yuque_id": yuque_id,
                "yuque_login": yuque_login,
                "yuque_name": yuque_name,
                "token": arg
            })
            
            # 生成用户画像
            profile = None
            try:
                client = YuqueClient(arg, self.yuque_base_url)
                docs = await client.get_user_docs(yuque_id, limit=50)
                await client.close()
                
                if docs:
                    profile = self.profile_generator.generate_from_docs(docs)
                    self.storage.save_profile(yuque_id, profile)
                    logger.info(f"用户 {yuque_login} 画像生成完成，文档数: {len(docs)}")
            except Exception as e:
                logger.warning(f"画像生成失败: {e}")
            
            # 构建响应
            response = (
                f"✅ 绑定成功！\n"
                f"语雀账号：@{yuque_login} ({yuque_name})\n"
            )
            
            if profile and profile["profile"]["interests"]:
                interests = ", ".join(profile["profile"]["interests"][:3])
                level = profile["profile"]["level"]
                level_zh = {"beginner": "入门", "intermediate": "进阶", "advanced": "高级"}.get(level, level)
                response += f"\n📊 初步画像：\n• 兴趣领域：{interests}\n• 水平：{level_zh}\n• 文档数：{profile['stats']['docs_count']}"
            else:
                response += "\n画像生成中..."
            
            response += "\n\n使用 /sync 同步知识库"
            yield event.plain_result(response)
            
        except httpx.HTTPStatusError as e:
            logger.error(f"语雀 Token 验证失败: {e}")
            yield event.plain_result("❌ Token 验证失败，请检查 Token 是否正确")
        except UnicodeEncodeError as e:
            logger.error(f"编码错误: {e}")
            yield event.plain_result("❌ 处理过程中出现编码错误，请检查输入是否包含特殊字符")
        except Exception as e:
            logger.error(f"绑定过程出错: {e}", exc_info=True)
            yield event.plain_result(f"❌ 绑定失败：{str(e)}")

    @filter.command("unbind")
    async def unbind(self, event: AstrMessageEvent):
        """解除语雀账号绑定
        
        用法: /unbind
        """
        platform_id = event.get_sender_id()
        binding = self.storage.get_binding(platform_id)
        
        if not binding:
            yield event.plain_result("你还没有绑定语雀账号")
            return
        
        yuque_login = binding.get("yuque_login", "未知")
        self.storage.remove_binding(platform_id)
        
        yield event.plain_result(f"✅ 已解除绑定语雀账号 @{yuque_login}")

    @filter.command("profile")
    async def profile(self, event: AstrMessageEvent, action: str = ""):
        """查看用户画像
        
        用法: 
        - /profile - 查看画像
        - /profile refresh - 重新生成画像
        """
        platform_id = event.get_sender_id()
        binding = self.storage.get_binding(platform_id)
        
        if not binding:
            yield event.plain_result(
                "你还没有绑定语雀账号\n"
                "请使用 /bind 绑定"
            )
            return
        
        yuque_id = binding.get("yuque_id")
        yuque_login = binding.get("yuque_login", "未知")
        yuque_name = binding.get("yuque_name", "未知")
        bind_time = binding.get("bind_time", "未知")
        token = binding.get("token", "")
        
        # 重新生成画像
        if action.lower() == "refresh":
            if not token:
                yield event.plain_result("无法刷新画像：Token 未保存")
                return
            
            try:
                client = YuqueClient(token, self.yuque_base_url)
                docs = await client.get_user_docs(yuque_id, limit=50)
                await client.close()
                
                if docs:
                    profile = self.profile_generator.generate_from_docs(docs)
                    self.storage.save_profile(yuque_id, profile)
                    yield event.plain_result(
                        f"✅ 画像已更新！\n"
                        f"分析了 {len(docs)} 篇文档"
                    )
                else:
                    yield event.plain_result("未找到文档，无法生成画像")
            except Exception as e:
                logger.error(f"画像刷新失败: {e}")
                yield event.plain_result(f"画像刷新失败：{str(e)}")
            return
        
        # 加载画像
        profile = self.storage.load_profile(yuque_id)
        
        # 水平中文映射
        level_map = {"beginner": "入门", "intermediate": "进阶", "advanced": "高级"}
        
        if profile:
            p = profile.get("profile", {})
            stats = profile.get("stats", {})
            
            interests = ", ".join(p.get("interests", [])) or "暂无"
            level = level_map.get(p.get("level", ""), p.get("level", "未知"))
            docs_count = stats.get("docs_count", 0)
            
            yield event.plain_result(
                f"📋 用户画像\n"
                f"━━━━━━━━━━━━━━━\n"
                f"语雀账号：@{yuque_login} ({yuque_name})\n"
                f"绑定时间：{bind_time[:10] if bind_time else '未知'}\n"
                f"━━━━━━━━━━━━━━━\n"
                f"兴趣领域：{interests}\n"
                f"整体水平：{level}\n"
                f"文档数量：{docs_count} 篇\n"
                f"━━━━━━━━━━━━━━━\n"
                f"使用 /profile refresh 可重新生成"
            )
        else:
            yield event.plain_result(
                f"📋 用户画像\n"
                f"━━━━━━━━━━━━━━━\n"
                f"语雀账号：@{yuque_login} ({yuque_name})\n"
                f"绑定时间：{bind_time[:10] if bind_time else '未知'}\n"
                f"━━━━━━━━━━━━━━━\n"
                f"画像未生成\n"
                f"使用 /profile refresh 生成画像"
            )

    @filter.command("rag")
    async def rag_cmd(self, event: AstrMessageEvent, action: str = "", query: str = ""):
        """RAG 检索管理
        
        用法: 
        - /rag status - 查看 RAG 状态
        - /rag search <关键词> - 搜索文档
        - /rag rebuild - 重建索引
        """
        if not self.rag:
            yield event.plain_result(
                "❌ RAG 未初始化\n"
                "请在插件配置中设置 embedding_api_key"
            )
            return
        
        # 查看状态
        if action.lower() == "status":
            stats = self.rag.get_stats()
            sync_state = self.yuque_sync.load_sync_state()
            
            yield event.plain_result(
                f"📊 RAG 状态\n"
                f"━━━━━━━━━━━━━━━\n"
                f"Embedding 模型: {self.embedding_model}\n"
                f"索引文档数: {stats.get('docs_count', 0)} 篇\n"
                f"向量库路径: {stats.get('persist_directory', '未知')}\n"
                f"━━━━━━━━━━━━━━━\n"
                f"语雀同步: {sync_state.get('docs_count', 0)} 篇文档\n"
                f"使用 /rag search <关键词> 搜索"
            )
            return
        
        # 搜索
        if action.lower() == "search" and query:
            try:
                results = self.rag.search(query, k=5)
                
                if not results:
                    yield event.plain_result(
                        f"🔍 搜索: {query}\n"
                        f"━━━━━━━━━━━━━━━\n"
                        f"未找到相关文档"
                    )
                    return
                
                output = [
                    f"🔍 搜索: {query}",
                    f"━━━━━━━━━━━━━━━\n"
                ]
                
                for i, doc in enumerate(results, 1):
                    output.append(
                        f"{i}. {doc['title']}\n"
                        f"   来源: {doc['source']}\n"
                        f"   {doc['content'][:100]}..."
                    )
                
                yield event.plain_result("\n".join(output))
            except Exception as e:
                logger.error(f"RAG 搜索失败: {e}")
                yield event.plain_result(f"❌ 搜索失败: {str(e)}")
            return
        
        # 重建索引
        if action.lower() == "rebuild":
            try:
                # 清空现有索引
                self.rag.clear()
                
                # 从同步目录重新索引
                indexed = self.rag.index_from_sync(str(self.yuque_sync.docs_dir))
                
                yield event.plain_result(
                    f"✅ RAG 索引重建完成\n"
                    f"索引文档: {indexed} 篇"
                )
            except Exception as e:
                logger.error(f"RAG 重建失败: {e}")
                yield event.plain_result(f"❌ 重建失败: {str(e)}")
            return
        
        # 帮助信息
        yield event.plain_result(
            "📚 RAG 检索管理\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "用法:\n"
            "• /rag status - 查看状态\n"
            "• /rag search <关键词> - 搜索\n"
            "• /rag rebuild - 重建索引"
        )

    @filter.command("novabot")
    async def novabot_help(self, event: AstrMessageEvent):
        """NovaBot 帮助信息"""
        yield event.plain_result(
            "🤖 NovaBot - NOVA 社团智能助手\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "指令列表：\n"
            "• /bind <Token> - 绑定语雀账号\n"
            "• /unbind - 解除绑定\n"
            "• /profile - 查看用户画像\n"
            "• /sync - 同步语雀知识库\n"
            "• /sync status - 查看同步状态\n"
            "• /rag status - 查看 RAG 状态\n"
            "• /rag search <关键词> - 搜索文档\n"
            "• /novabot - 显示帮助\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "直接提问即可，我会从语雀知识库中检索答案。"
        )

    async def terminate(self):
        """插件销毁时调用"""
        logger.info("NovaBot 插件已卸载")