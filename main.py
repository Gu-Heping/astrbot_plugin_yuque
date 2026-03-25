"""
NovaBot - NOVA 社团智能助手
以语雀知识库为核心的 AstrBot Plugin
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register


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

    async def close(self):
        await self.client.aclose()


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


@register("novabot", "谷和平", "NOVA 社团智能助手，以语雀知识库为核心", "0.1.0")
class NovaBotPlugin(Star):
    """NovaBot 主插件类"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.storage = Storage()
        self.profile_generator = ProfileGenerator()
        self.yuque_token = config.get("yuque_token", "")
        self.yuque_base_url = config.get("yuque_base_url", "https://nova.yuque.com/api/v2")
        self.docs_path = Path(config.get("docs_path", "/home/admin/yuque-docs"))
        logger.info("NovaBot 插件初始化完成")

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
"""

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
            # 从会话状态获取待确认的绑定信息
            pending = getattr(self, f"_pending_bind_{platform_id}", None)
            if not pending:
                yield event.plain_result("没有待确认的绑定请求，请重新执行 /bind")
                return
            
            # 执行绑定
            self.storage.add_binding(platform_id, pending["yuque_info"])
            delattr(self, f"_pending_bind_{platform_id}")
            
            yield event.plain_result(
                f"✅ 绑定成功！\n"
                f"语雀账号：@{pending['yuque_info']['yuque_login']} "
                f"({pending['yuque_info']['yuque_name']})\n"
                f"正在同步你的文档数据..."
            )
            return

        # 检查参数
        if not arg:
            yield event.plain_result(
                "请提供语雀 Token 或用户名：\n"
                "/bind <语雀 Token>\n"
                "\n"
                "Token 获取方式：\n"
                "1. 登录语雀 → 个人设置 → Token\n"
                "2. 创建一个有读取权限的 Token"
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
                    # 需要确认
                    setattr(self, f"_pending_bind_{platform_id}", {
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
            
            yield event.plain_result(response)
            
        except httpx.HTTPStatusError as e:
            logger.error(f"语雀 Token 验证失败: {e}")
            yield event.plain_result(f"❌ Token 验证失败，请检查 Token 是否正确")
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
            "• /novabot - 显示帮助\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "直接提问即可，我会从语雀知识库中检索答案。"
        )

    async def terminate(self):
        """插件销毁时调用"""
        logger.info("NovaBot 插件已卸载")