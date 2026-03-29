"""
NovaBot - NOVA 社团智能助手
以语雀知识库为核心的 AstrBot Plugin
"""

import asyncio
from datetime import datetime
from typing import Optional

from aiohttp import web
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from pathlib import Path as PathlibPath

from .novabot import RAGEngine, YuqueClient, sync_all_repos, Storage, ProfileGenerator, WebhookHandler, PartnerMatcher, format_partner_result, LearningPathRecommender, format_learning_path
from .novabot.profile import format_domain_assessment
from .novabot.subscribe import SubscriptionManager, format_subscription_list
from .novabot.push_notifier import PushNotifier
from .novabot.weekly import WeeklyReporter
from .novabot.search_log import SearchLogger
from .novabot.knowledge_gap import LearningGapAnalyzer, format_gap_report
from .novabot.token_monitor import TokenMonitor
from .novabot.ask_box import AskBoxManager
from .novabot.agent import NovaBotAgent
from .novabot.tools import ALL_TOOLS
from .novabot.knowledge_base import KnowledgeBaseManager


# ============================================================================
# 主插件类
# ============================================================================

@register("astrbot_plugin_yuque", "peace", "NOVA 社团智能助手", "v0.17.0")
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

        # 消息路由配置
        wake_words_str = config.get("wake_words", "novabot,nova,诺瓦")
        self.wake_words = [w.strip().lower() for w in wake_words_str.split(",") if w.strip()]
        self.enable_private_chat = config.get("enable_private_chat", True)
        self.enable_group_at = config.get("enable_group_at", True)

        # 获取插件数据目录（AstrBot 标准路径，使用 self.name）
        # self.name 来自 @register 装饰器的第一个参数，需要先调用 super().__init__(context)
        # get_astrbot_data_path() 返回 str，需要转换为 Path
        self.data_dir = PathlibPath(get_astrbot_data_path()) / "plugin_data" / self.name

        # 组件
        self.storage = Storage(data_dir=str(self.data_dir))
        self.token_monitor = TokenMonitor(self.storage.data_dir)  # 必须先初始化
        self.profile_gen = ProfileGenerator(self.token_monitor)
        self.partner_matcher = PartnerMatcher(self.storage)
        self.subscription_manager = SubscriptionManager(self.storage)
        self.search_logger = SearchLogger(self.storage.data_dir)
        self.ask_box = AskBoxManager(self.storage.data_dir)
        self.agent = NovaBotAgent(self)
        self.client: Optional[YuqueClient] = None
        self.path_recommender: Optional[LearningPathRecommender] = None
        self.gap_analyzer: Optional[LearningGapAnalyzer] = None
        self.kb_manager: Optional[KnowledgeBaseManager] = None

        # Webhook 服务
        self.webhook_handler: Optional[WebhookHandler] = None
        self.push_notifier: Optional[PushNotifier] = None
        self._webhook_app: Optional[web.Application] = None
        self._webhook_runner: Optional[web.AppRunner] = None
        self._webhook_site: Optional[web.TCPSite] = None
        self._webhook_started: bool = False  # 标记服务是否已启动
        self._sync_lock = asyncio.Lock()  # 保护同步操作，防止并发
        self._doc_index = None  # 懒加载的 DocIndex

        # RAG
        self.rag: Optional[RAGEngine] = None
        if self.embedding_api_key:
            try:
                rag_dir = self.storage.data_dir / "chroma_db"

                # Token 使用回调
                def on_embedding_tokens(tokens: int):
                    if hasattr(self, 'token_monitor') and self.token_monitor:
                        self.token_monitor.log_usage(
                            feature="embedding",
                            input_tokens=tokens,
                            output_tokens=0,
                            model=self.embedding_model,
                        )

                self.rag = RAGEngine(
                    persist_directory=str(rag_dir),
                    embedding_api_key=self.embedding_api_key,
                    embedding_base_url=self.embedding_base_url or None,
                    embedding_model=self.embedding_model,
                    token_usage_callback=on_embedding_tokens,
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

        # 初始化学习路径推荐器（依赖 RAG）
        self.path_recommender = LearningPathRecommender(self.storage, self.rag, self.token_monitor)

        # 初始化学习缺口分析器（依赖 RAG）
        self.gap_analyzer = LearningGapAnalyzer(self.storage, self.rag, self.token_monitor)

        # 初始化知识库管理器（依赖 DocIndex + RAG）
        self.kb_manager = KnowledgeBaseManager(self._get_doc_index(), self.rag)

        logger.info("NovaBot 插件初始化完成 (v0.14.9)")

        # 注册 FunctionTool
        self._register_tools()

        # 初始化 Webhook 服务
        # 注意：热更新时 on_astrbot_loaded 不会触发，所以需要延迟启动
        self._webhook_started = False
        if config.get("webhook_enabled", False):
            logger.info("[Webhook] webhook_enabled=True，初始化 Webhook 服务")
            self._setup_webhook_app()
            # 尝试立即启动（如果事件循环已运行）
            self._try_start_webhook()
        else:
            logger.info(f"[Webhook] webhook_enabled={config.get('webhook_enabled', False)}，跳过初始化")

    def _try_start_webhook(self):
        """尝试启动 Webhook 服务（延迟启动）"""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._start_webhook_service())
            logger.info("[Webhook] 已安排启动任务")
        except RuntimeError:
            # 事件循环未运行，等待 on_astrbot_loaded 触发
            logger.info("[Webhook] 事件循环未运行，等待 on_astrbot_loaded 触发")

    async def _start_webhook_service(self):
        """启动 Webhook 服务"""
        if self._webhook_started:
            return

        if not self._webhook_app:
            return

        port = self.config.get("webhook_port", 8766)
        try:
            self._webhook_runner = web.AppRunner(self._webhook_app)
            await self._webhook_runner.setup()
            self._webhook_site = web.TCPSite(self._webhook_runner, "0.0.0.0", port)
            await self._webhook_site.start()
            self._webhook_started = True
            logger.info(f"[Webhook] 服务已启动: http://0.0.0.0:{port}/yuque/webhook")
            logger.info(f"[Webhook] 健康检查: http://0.0.0.0:{port}/health")
        except Exception as e:
            logger.error(f"[Webhook] 服务启动失败: {e}", exc_info=True)

    def _setup_webhook_app(self):
        """设置 Webhook HTTP 服务"""
        logger.info("[Webhook] 开始设置 Webhook 应用...")
        self._webhook_app = web.Application()
        self._webhook_app.router.add_post("/yuque/webhook", self._handle_webhook_request)
        self._webhook_app.router.add_get("/health", self._health_check)

        # 初始化推送管理器
        self.push_notifier = PushNotifier(
            docs_dir=self.storage.data_dir / "yuque_docs",
            data_dir=self.storage.data_dir,
            context=self.context,
            subscription_manager=self.subscription_manager,
            config=self.config,
            token_monitor=self.token_monitor,
        )

        self.webhook_handler = WebhookHandler(
            docs_dir=self.storage.data_dir / "yuque_docs",
            data_dir=self.storage.data_dir,
            get_client=self._get_client,
            rag=self.rag,
            config=self.config,
            push_notifier=self.push_notifier,
            subscription_manager=self.subscription_manager,
            storage=self.storage,
        )
        logger.info("[Webhook] Webhook 应用设置完成")

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self):
        """AstrBot 初始化完成后启动 Webhook 服务（备用触发）"""
        await self._start_webhook_service()

    async def terminate(self):
        """插件卸载时的清理"""
        # 关闭 Webhook 服务
        if self._webhook_site:
            try:
                await self._webhook_site.stop()
                logger.info("[Webhook] 服务已停止")
            except Exception as e:
                logger.warning(f"[Webhook] 停止服务失败: {e}")

        if self._webhook_runner:
            try:
                await self._webhook_runner.cleanup()
            except Exception as e:
                logger.warning(f"[Webhook] 清理 runner 失败: {e}")

        # 关闭语雀客户端
        await self._close_client()
        logger.info("NovaBot 插件已卸载")

    def _get_doc_index(self):
        """获取 DocIndex 实例（懒加载）"""
        if self._doc_index is None:
            from .novabot.doc_index import DocIndex
            db_path = self.storage.data_dir / "doc_index.db"
            self._doc_index = DocIndex(str(db_path))
        return self._doc_index

    async def _handle_webhook_request(self, request: web.Request) -> web.Response:
        """处理语雀 Webhook 请求"""
        client_host = request.remote or "unknown"
        user_agent = request.headers.get("User-Agent", "")
        logger.info(f"[Webhook] 收到请求: {client_host} -> {request.path}")

        if not self.webhook_handler:
            logger.error("[Webhook] 处理器未初始化")
            return web.json_response(
                {"status": "error", "message": "handler not initialized"},
                status=503,
            )

        # IP 白名单验证
        ip_whitelist = self.config.get("webhook_ip_whitelist", "")
        if ip_whitelist:
            allowed_ips = [ip.strip() for ip in ip_whitelist.split(",") if ip.strip()]
            if allowed_ips and client_host not in allowed_ips:
                logger.warning(f"[Webhook] IP 不在白名单中: {client_host}, 允许: {allowed_ips}")
                return web.json_response(
                    {"status": "error", "message": "forbidden"},
                    status=403,
                )

        # User-Agent 验证（语雀官方请求特征）
        # 语雀 Webhook User-Agent 格式: YUQUE_WEBHOOK
        if "Yuque" not in user_agent and "YUQUE" not in user_agent.upper():
            logger.warning(f"[Webhook] 可疑请求 User-Agent: {user_agent}, 来源: {client_host}")
            # 如果设置了 IP 白名单，则已通过验证；否则只警告不拒绝
            if not ip_whitelist:
                logger.warning("[Webhook] 建议: 设置 webhook_ip_whitelist 配置项增强安全性")

        # 解析 JSON
        try:
            payload = await request.json()
        except Exception as e:
            logger.error(f"[Webhook] JSON 解析失败: {e}")
            return web.json_response(
                {"status": "error", "message": "invalid json"},
                status=400,
            )

        # 处理请求
        try:
            result = await self.webhook_handler.handle(payload)
            action = payload.get("data", {}).get("action_type", "unknown")
            logger.info(f"[Webhook] 处理完成 [{action}]: status={result.get('status')}")

            if result.get("status") == "ok":
                return web.json_response(result, status=200)
            elif result.get("status") == "ignored":
                return web.json_response(result, status=200)
            else:
                return web.json_response(result, status=500)

        except Exception as e:
            logger.error(f"[Webhook] 处理异常: {e}", exc_info=True)
            return web.json_response(
                {"status": "error", "message": str(e)},
                status=500,
            )

    async def _health_check(self, request: web.Request) -> web.Response:
        """健康检查端点"""
        return web.json_response({"status": "ok", "service": "novabot-webhook"})

    def _register_tools(self):
        """注册 LLM 工具"""
        for ToolClass in ALL_TOOLS:
            tool = ToolClass()
            tool.plugin = self
            self.context.add_llm_tools(tool)

        logger.info(f"LLM 工具注册完成: {', '.join(t.name for t in ALL_TOOLS)}")

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

    # 注意：不再使用 @filter.on_llm_request() 全局钩子
    # 因为 NovaBot Agent 已经由 on_message() 处理非命令消息
    # 全局钩子会导致 AstrBot 默认 LLM 也响应，造成重复回复

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp: "LLMResponse"):
        """记录正常聊天的 token 使用

        注意：流式输出模式下 usage 为 None，无法记录 token。
        这是 AstrBot 的已知限制。
        """
        try:
            input_tokens = 0
            output_tokens = 0

            # 尝试从 resp.usage 获取
            if hasattr(resp, "usage") and resp.usage:
                usage = resp.usage
                input_tokens = getattr(usage, "prompt_tokens", 0) or 0
                output_tokens = getattr(usage, "completion_tokens", 0) or 0

            # 尝试从 raw_completion.usage 获取
            if input_tokens == 0 and hasattr(resp, "raw_completion") and resp.raw_completion:
                usage = getattr(resp.raw_completion, "usage", None)
                if usage:
                    input_tokens = getattr(usage, "prompt_tokens", 0) or 0
                    output_tokens = getattr(usage, "completion_tokens", 0) or 0

            if input_tokens > 0 or output_tokens > 0:
                self.token_monitor.log_usage(
                    feature="chat",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
                logger.info(f"[LLM] 记录聊天 token: 入 {input_tokens}, 出 {output_tokens}")
        except Exception as e:
            logger.warning(f"[LLM] 记录聊天 token 失败: {e}")

    # ========== 自然语言交互 ==========

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        """处理消息（根据消息路由规则）

        消息路由规则：
        - 私聊：直接响应（可配置）
        - 群聊：需要 @ 或唤醒词触发
        - 命令消息：跳过，让命令处理器处理
        """
        msg = event.message_str.strip()

        # 跳过命令消息
        if self._is_command(msg):
            return

        # 判断是否应该处理这条消息
        should_handle, query = self._should_handle_message(event, msg)
        if not should_handle:
            return  # 不处理，让其他插件处理

        # 处理消息
        logger.info(f"[on_message] 处理消息: {query[:30]}...")
        try:
            response = await self.agent.handle_message(event, query)
            yield event.plain_result(response)
        except Exception as e:
            logger.error(f"自然语言处理失败: {e}", exc_info=True)
            yield event.plain_result("处理消息时出错，请稍后重试。")

        # 阻止事件继续传播
        event.stop_event()

    def _is_command(self, msg: str) -> bool:
        """判断是否是命令消息"""
        # 检查 / 前缀
        if msg.startswith("/"):
            return True

        # 飞书等平台可能去掉 / 前缀，检查已知命令名
        known_commands = [
            "novabot", "sync", "bind", "unbind", "profile", "partner", "path",
            "subscribe", "unsubscribe", "rag", "webhook", "weekly", "gap",
            "tokens", "ask", "askreset", "kb", "nova"
        ]
        first_word = msg.split()[0].lower() if msg.split() else ""
        if first_word in known_commands:
            return True

        return False

    def _should_handle_message(self, event: AstrMessageEvent, msg: str) -> tuple:
        """判断是否应该处理这条消息

        Returns:
            (should_handle, processed_query)
        """
        is_group = event.get_group_id() is not None

        if is_group:
            # 群聊：检查 @ 或唤醒词
            if self.enable_group_at and self._is_at_me(event):
                logger.info(f"[on_message] 检测到 @ 触发")
                return True, self._remove_at(event, msg)

            import re
            for wake in self.wake_words:
                # 支持唤醒词后有标点（如 "nova，帮我..."）
                pattern = rf'^{re.escape(wake)}[\s,，:：]*'
                if re.match(pattern, msg.lower()):
                    logger.info(f"[on_message] 检测到唤醒词: {wake}")
                    return True, re.sub(pattern, '', msg, count=1, flags=re.IGNORECASE).strip()

            # 群聊中没有触发条件，不处理
            return False, ""
        else:
            # 私聊：直接响应（可配置）
            if self.enable_private_chat:
                return True, msg
            else:
                # 也需要唤醒词
                import re
                for wake in self.wake_words:
                    pattern = rf'^{re.escape(wake)}[\s,，:：]*'
                    if re.match(pattern, msg.lower()):
                        return True, re.sub(pattern, '', msg, count=1, flags=re.IGNORECASE).strip()
                return False, ""

    def _is_at_me(self, event: AstrMessageEvent) -> bool:
        """检查是否 @ 了机器人"""
        import astrbot.api.message_components as Comp
        message_obj = event.message_obj
        if message_obj and message_obj.message:
            for comp in message_obj.message:
                if isinstance(comp, Comp.At):
                    # 检查 @ 的是不是自己
                    if str(comp.qq) == str(event.get_self_id()):
                        return True
        return False

    def _remove_at(self, event: AstrMessageEvent, msg: str) -> str:
        """移除消息中的 @，从消息链中提取纯文本"""
        import astrbot.api.message_components as Comp
        text_parts = []
        if event.message_obj and event.message_obj.message:
            for comp in event.message_obj.message:
                if isinstance(comp, Comp.Plain):
                    text_parts.append(comp.text)
        result = "".join(text_parts).strip()
        return result if result else msg

    # ========== 指令 ==========

    @filter.command("sync")
    @filter.permission_type(filter.PermissionType.ADMIN)
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
                user_info = await client.get_user()
                if user_info.get("type") != "Group":
                    yield event.plain_result("⚠️ 非团队 Token，跳过成员同步")
                    return

                group_id = user_info.get("id")
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
                    yield event.plain_result(
                        f"✅ 团队成员同步完成\n"
                        f"共 {len(members)} 人\n"
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

            # 检查是否正在 RAG 索引
            if state.get("status") == "rag_indexing" and state.get("rag_progress"):
                rp = state["rag_progress"]
                yield event.plain_result(
                    f"⏳ RAG 索引进行中\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"进度: {rp['current']}/{rp['total']}\n\n"
                    f"（Embedding API 调用较慢，请耐心等待）\n"
                    f"使用 /sync status 刷新进度"
                )
                return

            if state.get("last_sync"):
                lines = [
                    f"📊 同步状态",
                    "━━━━━━━━━━━━━━━",
                    f"上次同步: {state['last_sync'][:19]}",
                    f"知识库数: {state.get('repos_count', 0)}",
                    f"文档总数: {state.get('docs_count', 0)}",
                    f"Token 类型: {state.get('token_type', '未知')}",
                ]
                yield event.plain_result("\n".join(lines))
            else:
                yield event.plain_result("尚未同步，使用 /sync 开始")
            return

        # 检查是否已在同步（使用锁保护）
        state = self.storage.load_sync_state()
        if self._sync_lock.locked():
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
        # 使用锁保护，防止并发同步
        async with self._sync_lock:
            client = self._get_client()
            try:
                # 标记开始
                state = self.storage.load_sync_state()
                state["in_progress"] = True
                self.storage.save_sync_state(state)

                # 使用新模块同步
                members = self.storage.load_members()
                result = await sync_all_repos(
                    client=client,
                    output_dir=self.storage.docs_dir,
                    members=members,
                    progress_callback=self.storage.update_progress,
                )

                # 更新同步状态
                state = {
                    "last_sync": datetime.now().isoformat(),
                    "repos_count": result.get("repos_count", 0) if result else 0,
                    "docs_count": result.get("docs", 0) if result else 0,
                    "token_type": result.get("token_type", "未知") if result else "未知",
                    "in_progress": False,
                    "progress": None
                }
                self.storage.save_sync_state(state)

                # RAG 索引
                if self.rag and result and result.get("docs", 0) > 0:
                    try:
                        # RAG 索引进度回调
                        def rag_progress(current, total):
                            state = self.storage.load_sync_state()
                            state["status"] = "rag_indexing"
                            state["rag_progress"] = {"current": current, "total": total}
                            self.storage.save_sync_state(state)

                        # 初始化状态
                        rag_progress(0, result.get("docs", 0))

                        indexed = await asyncio.to_thread(
                            self.rag.index_from_sync,
                            str(self.storage.docs_dir),
                            rag_progress
                        )
                        logger.info(f"RAG 索引完成: {indexed} 篇")
                    except Exception as e:
                        logger.error(f"RAG 索引失败: {e}")

                # 清除 RAG 索引状态
                state = self.storage.load_sync_state()
                state.pop("status", None)
                state.pop("rag_progress", None)
                self.storage.save_sync_state(state)

                docs_count = result.get("docs", 0) if result else 0
                removed_count = result.get("removed", 0) if result else 0
                logger.info(f"后台同步完成: {docs_count} 篇文档, 清理 {removed_count} 个孤儿文件")

                # Git commit（如果启用）
                if self.config.get("git_enabled", True):
                    from .novabot.git_ops import GitOps
                    git = GitOps(self.storage.docs_dir)
                    if git.is_git_repo() and git.has_user_identity():
                        # 获取所有变更的文件
                        import subprocess
                        try:
                            status_result = subprocess.run(
                                ["git", "status", "--porcelain"],
                                cwd=self.storage.docs_dir,
                                capture_output=True,
                                text=True,
                            )
                            changed_files = [
                                line[3:] for line in status_result.stdout.strip().split("\n")
                                if line.strip()
                            ]
                            if changed_files:
                                commit_msg = f"sync: 同步 {docs_count} 篇文档"
                                if removed_count > 0:
                                    commit_msg += f", 清理 {removed_count} 个文件"
                                git.add_commit(changed_files, commit_msg)
                        except Exception as e:
                            logger.warning(f"[Sync] Git commit 失败: {e}")

            except Exception as e:
                logger.error(f"后台同步失败: {e}", exc_info=True)
                # 标记同步结束
                state = self.storage.load_sync_state()
                state["in_progress"] = False
                state["progress"] = None
                state.pop("status", None)
                state.pop("rag_progress", None)
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
    async def profile_cmd(self, event: AstrMessageEvent, action: str = "", domain: str = ""):
        """查看用户画像

        用法:
        - /profile - 查看画像
        - /profile refresh - 使用 AI 深度分析生成画像
        - /profile assess <领域> - 评估某领域的掌握程度
        """
        platform_id = event.get_sender_id()
        binding = self.storage.get_binding(platform_id)

        if not binding:
            yield event.plain_result("请先使用 /bind 绑定账号")
            return

        yuque_id = binding.get("yuque_id")
        yuque_name = binding.get("yuque_name", "")
        yuque_login = binding.get("yuque_login", "")
        logger.info(f"[Profile] 绑定信息: yuque_id={yuque_id}, yuque_name={yuque_name}, yuque_login={yuque_login}")

        # 领域评估
        if action.lower() == "assess" and domain:
            docs = self.storage.get_docs_by_author(yuque_name, yuque_id)
            if not docs:
                yield event.plain_result("⚠️ 未找到你的文档，请先执行 /sync 同步")
                return

            try:
                provider = self.context.get_using_provider(umo=event.unified_msg_origin)
                if not provider:
                    yield event.plain_result("❌ LLM 未配置，请先配置模型 Provider")
                    return

                yield event.plain_result(f"🔍 正在评估你在「{domain}」领域的学习情况...")

                assessment = await self.profile_gen.assess_domain_level(docs, domain, provider)
                result = format_domain_assessment(assessment)
                yield event.plain_result(result)

            except Exception as e:
                logger.error(f"领域评估失败: {e}", exc_info=True)
                yield event.plain_result(f"❌ 评估失败: {e}")
            return

        # 刷新画像（使用 LLM 深度分析）
        if action.lower() == "refresh":
            # 获取文档（优先通过 yuque_id 精确匹配）
            docs = self.storage.get_docs_by_author(yuque_name, yuque_id)
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
                # 精确匹配
                skill_level = skills.get(interest)
                if skill_level:
                    skill_lines.append(f"• {interest} ({level_map.get(skill_level, '入门')})")
                    continue

                # 模糊匹配：检查 skills 中是否有包含兴趣关键词的 key
                interest_lower = interest.lower()
                matched = False
                for skill_name, level in skills.items():
                    skill_lower = skill_name.lower()
                    # 双向包含匹配
                    if interest_lower in skill_lower or skill_lower in interest_lower:
                        skill_lines.append(f"• {interest} ({level_map.get(level, '入门')})")
                        matched = True
                        break

                if not matched:
                    # 没有匹配到，显示默认值
                    skill_lines.append(f"• {interest} (入门)")

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

    @filter.command("partner")
    async def partner_cmd(self, event: AstrMessageEvent, topic: str = ""):
        """伙伴推荐

        用法:
        - /partner - 查看推荐（所有兴趣）
        - /partner 爬虫 - 查找某主题的学习伙伴/导师
        """
        platform_id = event.get_sender_id()
        binding = self.storage.get_binding(platform_id)

        if not binding:
            yield event.plain_result("请先使用 /bind 绑定账号")
            return

        yuque_id = binding.get("yuque_id")

        # 检查画像
        profile = self.storage.load_profile(yuque_id)
        if not profile:
            yield event.plain_result(
                "⚠️ 你还没有画像\n"
                "使用 /profile refresh 生成画像后再来找我推荐伙伴"
            )
            return

        # 查找伙伴和导师
        try:
            partners = self.partner_matcher.find_partners(yuque_id, topic if topic else None)
            mentors = self.partner_matcher.find_mentors(yuque_id, topic if topic else None)

            if not partners and not mentors:
                if topic:
                    yield event.plain_result(
                        f"未找到「{topic}」相关的学习伙伴\n"
                        f"试试其他主题，或使用 /partner 查看所有推荐"
                    )
                else:
                    yield event.plain_result(
                        "暂无匹配的学习伙伴\n"
                        "可能是因为社团成员画像数据不足"
                    )
                return

            result = format_partner_result(partners, mentors, topic if topic else None)
            yield event.plain_result(result)

        except Exception as e:
            logger.error(f"伙伴推荐失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 推荐失败: {e}")

    @filter.command("path")
    async def path_cmd(self, event: AstrMessageEvent, domain: str = ""):
        """学习路径推荐

        用法:
        - /path <领域> - 生成该领域的学习路径
        """
        platform_id = event.get_sender_id()
        binding = self.storage.get_binding(platform_id)

        if not binding:
            yield event.plain_result("请先使用 /bind 绑定账号")
            return

        if not domain:
            yield event.plain_result(
                "请指定要学习的领域\n"
                "用法: /path <领域>\n"
                "例如: /path 爬虫\n"
                "      /path LLM应用开发"
            )
            return

        yuque_id = binding.get("yuque_id")

        # 获取画像
        profile = self.storage.load_profile(yuque_id)
        if not profile:
            yield event.plain_result(
                "⚠️ 你还没有画像\n"
                "使用 /profile refresh 生成画像后才能推荐学习路径"
            )
            return

        # 获取用户已写的文档列表（用于排除）
        user_docs = self.storage.get_docs_by_author(yuque_id=yuque_id)

        # 获取 LLM Provider
        try:
            provider = self.context.get_using_provider(umo=event.unified_msg_origin)
            if not provider:
                yield event.plain_result("❌ LLM 未配置，请先配置模型 Provider")
                return

            yield event.plain_result(f"🔍 正在为「{domain}」规划学习路径...")

            path = await self.path_recommender.recommend(
                profile, domain, provider,
                exclude_author_id=yuque_id,
                exclude_author_name=binding.get("yuque_name"),
                user_docs=user_docs,
            )
            result = format_learning_path(path)
            yield event.plain_result(result)

        except Exception as e:
            logger.error(f"学习路径生成失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 生成失败: {e}")

    @filter.command("subscribe")
    async def subscribe_cmd(self, event: AstrMessageEvent, sub_type: str = "", target: str = ""):
        """订阅管理

        用法:
        - /subscribe - 查看我的订阅
        - /subscribe repo <知识库名> - 订阅知识库
        - /subscribe author <作者名> - 订阅作者
        - /subscribe all - 订阅全部更新
        """
        umo = event.unified_msg_origin
        platform_id = event.get_sender_id()

        if not sub_type:
            # 显示订阅列表
            subs = self.subscription_manager.get_subscriptions(platform_id, umo)
            result = format_subscription_list(subs)
            yield event.plain_result(result)
            return

        sub_type = sub_type.lower()

        if sub_type == "all":
            success, msg = await self.subscription_manager.subscribe(platform_id, umo, "all")
        elif sub_type == "repo":
            if not target:
                yield event.plain_result(
                    "请指定知识库名\n"
                    "用法: /subscribe repo <知识库名>"
                )
                return
            success, msg = await self.subscription_manager.subscribe(platform_id, umo, "repo", target)
        elif sub_type == "author":
            if not target:
                yield event.plain_result(
                    "请指定作者名\n"
                    "用法: /subscribe author <作者名>"
                )
                return
            success, msg = await self.subscription_manager.subscribe(platform_id, umo, "author", target)
        else:
            yield event.plain_result(
                "无效的订阅类型\n"
                "用法: /subscribe [repo|author|all] [目标]"
            )
            return

        yield event.plain_result(f"{'✅' if success else '❌'} {msg}")

    @filter.command("unsubscribe")
    async def unsubscribe_cmd(self, event: AstrMessageEvent, sub_id: str = ""):
        """取消订阅

        用法:
        - /unsubscribe <ID> - 取消指定订阅
        - /unsubscribe all - 取消所有订阅
        """
        umo = event.unified_msg_origin
        platform_id = event.get_sender_id()

        if not sub_id:
            yield event.plain_result(
                "请指定要取消的订阅 ID\n"
                "用法: /unsubscribe <ID>\n"
                "      /unsubscribe all\n"
                "使用 /subscribe 查看订阅列表"
            )
            return

        if sub_id.lower() == "all":
            success, msg = await self.subscription_manager.unsubscribe(platform_id, umo)
        else:
            try:
                sid = int(sub_id)
                success, msg = await self.subscription_manager.unsubscribe(platform_id, umo, sid)
            except ValueError:
                yield event.plain_result("ID 必须是数字")
                return

        yield event.plain_result(f"{'✅' if success else '❌'} {msg}")

    @filter.command("rag")
    @filter.permission_type(filter.PermissionType.ADMIN)
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
                # 记录搜索日志
                self.search_logger.log_search(
                    query=query,
                    results_count=len(results),
                    search_type="rag",
                    user_id=event.get_sender_id(),
                )
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
                indexed = self.rag.index_from_sync(str(self.storage.docs_dir))
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

    @filter.command("webhook")
    async def webhook_cmd(self, event: AstrMessageEvent):
        """Webhook 服务状态"""
        if not self.config.get("webhook_enabled", False):
            yield event.plain_result(
                "Webhook 服务未启用\n"
                "在配置中设置 webhook_enabled: true 启用"
            )
            return

        port = self.config.get("webhook_port", 8766)

        if self._webhook_site:
            yield event.plain_result(
                f"🌐 Webhook 服务\n"
                f"━━━━━━━━━━━━━━━\n"
                f"状态: ✅ 运行中\n"
                f"地址: http://0.0.0.0:{port}/yuque/webhook\n"
                f"\n"
                f"在语雀知识库设置中配置此地址"
            )
        else:
            yield event.plain_result(
                f"🌐 Webhook 服务\n"
                f"━━━━━━━━━━━━━━━\n"
                f"状态: ⚠️ 未启动\n"
                f"端口: {port}"
            )

    @filter.command("weekly")
    async def weekly_cmd(self, event: AstrMessageEvent):
        """生成本周知识周报"""
        try:
            docs_dir = self.storage.docs_dir

            # 获取 DocIndex 实例
            doc_index = self._get_doc_index()

            reporter = WeeklyReporter(docs_dir, doc_index=doc_index)

            # 尝试获取 LLM Provider
            umo = event.unified_msg_origin
            prov_id = await self.context.get_current_chat_provider_id(umo)

            if prov_id:
                prov = self.context.get_provider_by_id(prov_id)
                report = await reporter.generate_weekly_report_with_llm(
                    provider=prov,
                    token_monitor=self.token_monitor,
                )
            else:
                # 无 LLM 时回退到纯统计
                report = reporter.generate_weekly_report()

            yield event.plain_result(report)
        except Exception as e:
            logger.error(f"生成周报失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 生成周报失败: {e}")

    @filter.command("gap")
    async def gap_cmd(self, event: AstrMessageEvent, target_domain: str = ""):
        """分析个人学习缺口

        用法: /gap [目标领域]
        例如: /gap 爬虫
        如果不指定领域，会根据用户画像自动推断
        """
        try:
            # 检查用户是否绑定
            platform_id = event.get_sender_id()
            binding = self.storage.get_binding(platform_id)

            if not binding:
                yield event.plain_result(
                    "❌ 请先绑定语雀账号\n"
                    "使用 /bind <语雀用户名> 绑定后，才能分析你的学习缺口。"
                )
                return

            yuque_id = binding.get("yuque_id")
            if not yuque_id:
                yield event.plain_result("❌ 绑定信息异常，请重新绑定")
                return

            # 获取 LLM Provider
            provider = self.context.get_using_provider(umo=event.unified_msg_origin)
            if not provider:
                yield event.plain_result("❌ LLM 未配置，无法分析")
                return

            yield event.plain_result("📊 正在分析你的学习缺口...")

            # 执行分析
            gap = await self.gap_analyzer.analyze(
                yuque_id=yuque_id,
                target_domain=target_domain or None,
                provider=provider,
            )

            yield event.plain_result(format_gap_report(gap))

        except Exception as e:
            logger.error(f"学习缺口分析失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 分析失败: {e}")

    @filter.command("card")
    async def card_cmd(self, event: AstrMessageEvent, topic: str = ""):
        """生成知识卡片

        用法: /card <主题>
        例如: /card 爬虫
        """
        if not topic:
            yield event.plain_result("用法: /card <主题>\n例如: /card 爬虫")
            return

        try:
            # 获取 LLM Provider
            provider = self.context.get_using_provider(umo=event.unified_msg_origin)
            if not provider:
                yield event.plain_result("❌ LLM 未配置，无法生成知识卡片")
                return

            if not self.rag:
                yield event.plain_result("❌ RAG 引擎未初始化，请先执行 /sync")
                return

            yield event.plain_result(f"📚 正在生成「{topic}」知识卡片...")

            from .novabot.knowledge_card import KnowledgeCardGenerator, format_knowledge_card

            generator = KnowledgeCardGenerator(self.rag, self.token_monitor)
            card = await generator.generate(topic, provider)

            yield event.plain_result(format_knowledge_card(card))

        except Exception as e:
            logger.error(f"知识卡片生成失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 生成失败: {e}")

    @filter.command("persona")
    async def persona_cmd(self, event: AstrMessageEvent, action: str = "", value: str = ""):
        """查看或设置人格偏好

        用法:
        - /persona - 查看当前设置
        - /persona name 小明 - 设置称呼
        - /persona tone 活泼 - 设置语气（温和/活泼/严肃/幽默）
        - /persona style 简洁 - 设置回复风格（简洁/详细）
        - /persona formality 正式 - 设置正式程度（轻松/正式）
        - /persona reset - 重置为默认设置
        """
        platform_id = event.get_sender_id()
        binding = self.storage.get_binding(platform_id)

        if not binding:
            yield event.plain_result("请先绑定账号：/bind <用户名>")
            return

        yuque_id = binding.get("yuque_id")
        yuque_name = binding.get("yuque_name", "未知")
        prefs = self.storage.load_preferences(yuque_id)

        if not action:
            # 显示当前设置
            name = prefs.get("name", "")
            tone = prefs.get("tone", "温和")
            style = prefs.get("style", "详细")
            formality = prefs.get("formality", "轻松")

            lines = [
                f"📊 {yuque_name} 的人格设置",
                "",
                f"• 称呼：{name or '（未设置）'}",
                f"• 语气：{tone}",
                f"• 回复风格：{style}",
                f"• 正式程度：{formality}",
                "",
                "💡 修改方式：",
                "• /persona name 小明",
                "• /persona tone 活泼",
                "• /persona style 简洁",
                "• /persona reset - 重置为默认",
            ]
            yield event.plain_result("\n".join(lines))
            return

        # 重置偏好
        if action == "reset":
            self.storage.save_preferences(yuque_id, Storage.DEFAULT_PREFERENCES.copy())
            yield event.plain_result("✅ 已重置为默认设置")
            return

        # 设置偏好
        valid_actions = {
            "name": "称呼",
            "tone": "语气",
            "style": "回复风格",
            "formality": "正式程度",
        }

        if action not in valid_actions:
            yield event.plain_result(f"未知的设置项：{action}\n可选：{', '.join(valid_actions.keys())}")
            return

        if not value:
            yield event.plain_result(f"请提供值：/persona {action} <值>")
            return

        # 验证值
        valid_values = {
            "tone": ["温和", "活泼", "严肃", "幽默"],
            "style": ["简洁", "详细"],
            "formality": ["轻松", "正式"],
        }

        if action in valid_values and value not in valid_values[action]:
            yield event.plain_result(f"无效的{valid_actions[action]}值：{value}\n可选：{', '.join(valid_values[action])}")
            return

        # 更新偏好
        success = self.storage.update_preference(yuque_id, action, value)
        if success:
            if action == "name":
                yield event.plain_result(f"✅ 已设置称呼为「{value}」")
            else:
                yield event.plain_result(f"✅ 已设置{valid_actions[action]}为「{value}」")
        else:
            yield event.plain_result("❌ 设置失败")

    @filter.command("tokens")
    async def tokens_cmd(self, event: AstrMessageEvent):
        """查看 Token 消耗统计"""
        try:
            stats = self.token_monitor.get_stats(days=30)
            report = self.token_monitor.format_stats_report(stats)
            yield event.plain_result(report)
        except Exception as e:
            logger.error(f"获取 Token 统计失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 获取失败: {e}")

    @filter.command("ask")
    async def ask_cmd(self, event: AstrMessageEvent, args: str = ""):
        """知识问答（实名）

        用法:
        - /ask <问题> - 提问
        - /ask list - 查看问题列表
        - /ask view <ID> - 查看问题详情
        - /ask answer <ID> <回答> - 回答问题（需绑定语雀）
        - /ask like <问题ID> <回答ID> - 点赞回答
        - /ask mine - 查看我的问题
        """
        # 从消息直接解析（AstrBot 的 args 只传第一个参数）
        msg = event.message_str.strip()
        if msg.startswith("ask "):
            content = msg[4:].strip()
        elif msg.startswith("ask"):
            content = msg[3:].strip()
        else:
            content = args.strip()

        parts = content.split(maxsplit=2) if content else []

        try:
            # 无参数：显示帮助
            if not parts:
                stats = self.ask_box.get_stats()
                yield event.plain_result(
                    f"💬 知识问答\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 {stats['total_questions']} 问题, {stats['total_answers']} 回答, {stats['total_likes']} 赞\n"
                    f"\n"
                    f"指令:\n"
                    f"  /ask <问题> - 提问（需绑定）\n"
                    f"  /ask list - 查看问题列表\n"
                    f"  /ask view <ID> - 查看详情\n"
                    f"  /ask answer <ID> <回答> - 回答（需绑定）\n"
                    f"  /ask like <问题ID> <回答ID> - 点赞\n"
                    f"  /ask mine - 我的问题\n"
                    f"  /ask delete <ID> - 删除我的问题\n"
                )
                return

            action = parts[0].lower()

            # 提问（需绑定语雀）
            if action not in ("list", "view", "answer", "like", "mine", "delete"):
                sender_id = event.get_sender_id()

                # 检查是否绑定语雀
                binding = self.storage.get_binding(sender_id)
                if not binding:
                    yield event.plain_result(
                        "❌ 提问需要先绑定语雀\n"
                        "使用 /bind <语雀用户名> 进行绑定"
                    )
                    return

                sender_name = event.get_sender_name() or f"用户{sender_id}"
                umo = event.unified_msg_origin

                try:
                    qid, msg = self.ask_box.submit_question(content, umo, sender_id, sender_name)
                    yield event.plain_result(
                        f"✅ 提问成功 (ID: {qid})\n\n"
                        f"使用 /ask view {qid} 查看回答"
                    )
                except ValueError as e:
                    yield event.plain_result(f"❌ {e}")
                return

            # 查看列表
            if action == "list":
                questions = self.ask_box.get_all_questions(20)
                if not questions:
                    yield event.plain_result("暂无问题，快来提问吧！")
                    return
                result = self.ask_box.format_questions_list(questions)
                yield event.plain_result(
                    f"📋 问题列表\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"{result}\n"
                    f"\n"
                    f"使用 /ask view <ID> 查看详情"
                )
                return

            # 查看详情
            if action == "view":
                if len(parts) < 2:
                    yield event.plain_result("用法: /ask view <ID>")
                    return
                try:
                    qid = int(parts[1])
                except ValueError:
                    yield event.plain_result("❌ 问题 ID 必须是数字")
                    return

                question = self.ask_box.get_question_by_id(qid)
                if not question:
                    yield event.plain_result(f"❌ 未找到问题 #{qid}")
                    return

                result = self.ask_box.format_question_detail(question)
                yield event.plain_result(result)
                return

            # 回答问题（需绑定语雀）
            if action == "answer":
                if len(parts) < 3:
                    yield event.plain_result("用法: /ask answer <ID> <回答>")
                    return
                try:
                    qid = int(parts[1])
                except ValueError:
                    yield event.plain_result("❌ 问题 ID 必须是数字")
                    return

                answer_content = parts[2]

                # 检查是否绑定语雀
                sender_id = event.get_sender_id()
                binding = self.storage.get_binding(sender_id)
                if not binding:
                    yield event.plain_result(
                        "❌ 回答问题需要先绑定语雀\n"
                        "使用 /bind <语雀用户名> 进行绑定"
                    )
                    return

                sender_name = event.get_sender_name() or f"用户{sender_id}"
                yuque_id = binding.get("yuque_id")

                success, msg, notify_info = self.ask_box.submit_answer(
                    qid, answer_content, sender_id, sender_name, yuque_id
                )

                if success:
                    # 通知提问者
                    if notify_info and notify_info.get("umo"):
                        try:
                            from astrbot.api.event import MessageChain
                            umo = notify_info["umo"]
                            question_content = notify_info.get("question_content", "")
                            display = question_content[:50] + "..." if len(question_content) > 50 else question_content

                            notify_msg = (
                                f"📬 你的问题有新回答\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"❓ 问题: {display}\n"
                                f"👤 回答者: {notify_info['answerer_name']}\n"
                                f"\n"
                                f"使用 /ask view {qid} 查看详情"
                            )
                            chain = MessageChain().message(notify_msg)
                            await self.context.send_message(umo, chain)
                            logger.info(f"[AskBox] 已通知提问者")
                        except Exception as e:
                            logger.error(f"[AskBox] 通知提问者失败: {e}")

                    yield event.plain_result(f"✅ {msg}")
                else:
                    yield event.plain_result(f"❌ {msg}")
                return

            # 点赞回答
            if action == "like":
                if len(parts) < 3:
                    yield event.plain_result("用法: /ask like <问题ID> <回答ID>")
                    return
                try:
                    qid = int(parts[1])
                    aid = int(parts[2])
                except ValueError:
                    yield event.plain_result("❌ ID 必须是数字")
                    return

                user_id = event.get_sender_id()
                success, msg = self.ask_box.like_answer(qid, aid, user_id)

                if success:
                    yield event.plain_result(f"👍 {msg}")
                else:
                    yield event.plain_result(f"❌ {msg}")
                return

            # 查看我的问题
            if action == "mine":
                sender_id = event.get_sender_id()
                questions = self.ask_box.get_user_questions(sender_id)
                if not questions:
                    yield event.plain_result("你还没有提问过问题")
                    return
                result = self.ask_box.format_questions_list(questions)
                yield event.plain_result(
                    f"📝 我的问题 ({len(questions)} 条)\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"{result}"
                )
                return

            # 删除问题（仅提问者可删）
            if action == "delete":
                if len(parts) < 2:
                    yield event.plain_result("用法: /ask delete <ID>")
                    return
                try:
                    qid = int(parts[1])
                except ValueError:
                    yield event.plain_result("❌ 问题 ID 必须是数字")
                    return

                sender_id = event.get_sender_id()
                success, msg = self.ask_box.delete_question(qid, sender_id)
                if success:
                    yield event.plain_result(f"✅ {msg}")
                else:
                    yield event.plain_result(f"❌ {msg}")
                return

        except Exception as e:
            logger.error(f"[Ask] 操作失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 操作失败: {e}")

    @filter.command("askreset")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def askreset_cmd(self, event: AstrMessageEvent):
        """重置知识问答数据（管理员）"""
        success, msg = self.ask_box.clear_all()
        if success:
            yield event.plain_result(f"✅ {msg}\n\n知识问答数据已重置")
        else:
            yield event.plain_result(f"❌ {msg}")

    @filter.command("kb")
    async def kb_cmd(self, event: AstrMessageEvent, args: str = ""):
        """知识库管理

        用法:
        - /kb - 列出所有知识库
        - /kb <知识库> - 查看知识库概览
        - /kb <知识库> <问题> - 在知识库范围内问答
        """
        if not self.kb_manager:
            yield event.plain_result("❌ 知识库管理器未初始化")
            return

        # 从消息直接解析，更可靠
        msg = event.message_str.strip()
        # AstrBot 已去掉 / 前缀，直接检查 kb 开头
        if msg.startswith("kb "):
            content = msg[3:].strip()
        elif msg.startswith("kb"):
            content = msg[2:].strip()
        else:
            content = args.strip()

        logger.info(f"[KB] 原始消息: {msg}, 解析内容: {content}")

        try:
            # 无参数：列出知识库
            if not content:
                kbs = self.kb_manager.list_kbs()
                result = self.kb_manager.format_kb_list(kbs)
                yield event.plain_result(result)
                return

            # 查找第一个空格的位置
            first_space = content.find(" ")
            if first_space == -1:
                # 单参数：知识库概览
                book_name = content
                info = self.kb_manager.get_kb_info(book_name)
                if not info:
                    yield event.plain_result(f"❌ 未找到知识库「{book_name}」")
                    return
                result = self.kb_manager.format_kb_info(info)
                yield event.plain_result(result)
                return

            # 双参数：范围检索
            book_name = content[:first_space]
            query = content[first_space + 1:]

            logger.info(f"[KB] 知识库: {book_name}, 查询: {query}")

            # 先验证知识库存在
            info = self.kb_manager.get_kb_info(book_name)
            if not info:
                yield event.plain_result(f"❌ 未找到知识库「{book_name}」")
                return

            # 范围检索
            results = self.kb_manager.search_in_kb(info["book_name"], query, k=5)
            if not results:
                yield event.plain_result(f"在「{info['book_name']}」中未找到相关内容")
                return

            lines = [f"在「{info['book_name']}」中找到相关内容：", ""]
            for i, r in enumerate(results, 1):
                title = r.get("title", "未知")
                author = r.get("author", "")
                content_text = r.get("content", "")[:200]
                lines.append(f"【{i}】《{title}》" + (f" - {author}" if author else ""))
                lines.append(f"   {content_text}...")
                lines.append("")

            lines.append("💡 提示：使用 /rag search 可进行全局搜索")
            yield event.plain_result("\n".join(lines))
            return

        except Exception as e:
            logger.error(f"[KB] 操作失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 操作失败: {e}")

    @filter.command("novabot")
    async def help_cmd(self, event: AstrMessageEvent):
        """帮助信息"""
        yield event.plain_result(
            "🤖 NovaBot - NOVA 社团智能助手\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "💬 自然语言交互\n"
            "  直接说话即可，例如：\n"
            "  \"帮我找爬虫教程\"\n"
            "  \"我想学 Python\"\n"
            "  \"社团有哪些作者\"\n"
            "\n"
            "📖 知识库\n"
            "  /kb - 列出知识库\n"
            "  /kb <知识库> - 查看概览\n"
            "  /kb <知识库> <问题> - 范围检索\n"
            "\n"
            "📖 同步（管理员）\n"
            "  /sync - 同步知识库\n"
            "  /sync members - 同步成员\n"
            "  /sync status - 同步状态\n"
            "  /rag search <关键词> - 语义搜索\n"
            "\n"
            "👤 账号\n"
            "  /bind <用户名> - 绑定账号\n"
            "  /unbind - 解除绑定\n"
            "  /profile - 查看画像\n"
            "  /profile refresh - 刷新画像\n"
            "  /profile assess <领域> - 领域评估\n"
            "\n"
            "👥 伙伴与学习\n"
            "  /partner - 学习伙伴推荐\n"
            "  /partner <主题> - 按主题推荐\n"
            "  /path <领域> - 学习路径推荐\n"
            "  知识卡片 - 直接说\"我想学xxx\"\n"
            "\n"
            "🔔 订阅\n"
            "  /subscribe - 查看订阅\n"
            "  /subscribe repo <知识库> - 订阅知识库\n"
            "  /subscribe author <作者> - 订阅作者\n"
            "  /unsubscribe <ID> - 取消订阅\n"
            "\n"
            "📊 分析\n"
            "  /weekly - 本周知识周报\n"
            "  /gap - 知识缺口分析\n"
            "  /tokens - Token 消耗统计\n"
            "\n"
            "💬 知识问答\n"
            "  /ask <问题> - 提问（需绑定）\n"
            "  /ask list - 查看问题列表\n"
            "  /ask view <ID> - 查看详情\n"
            "  /ask answer <ID> <回答> - 回答（需绑定）\n"
            "  /ask like <问题ID> <回答ID> - 点赞\n"
            "  /ask mine - 我的问题\n"
            "  /ask delete <ID> - 删除我的问题\n"
            "\n"
            "  /novabot - 帮助"
        )