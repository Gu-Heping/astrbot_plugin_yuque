"""
NovaBot - NOVA 社团智能助手
以语雀知识库为核心的 AstrBot Plugin
"""

import asyncio
from typing import Optional

from aiohttp import web
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from pathlib import Path as PathlibPath

from .novabot import RAGEngine, YuqueClient, Storage, ProfileGenerator, WebhookHandler, PartnerMatcher, LearningPathRecommender, format_learning_path, ChunkStore, KnowledgeCore
from .novabot.profile import (
    assess_user_domain,
    format_profile_view,
    get_profile_docs,
    refresh_user_profile,
)
from .novabot.subscribe import SubscriptionManager, format_subscription_list
from .novabot.push_notifier import PushNotifier
from .novabot.weekly import WeeklyReporter
from .novabot.search_log import SearchLogger
from .novabot.knowledge_gap import LearningGapAnalyzer
from .novabot.token_monitor import TokenMonitor
from .novabot.token_limiter import TokenLimiter
from .novabot.ask_box import AskBoxManager
from .novabot.agent import NovaBotAgent
from .novabot.sync_workflow import (
    mark_sync_failed,
    refresh_collaboration_artifacts,
    run_background_sync_pipeline,
    select_member_sync_teams,
    select_sync_teams,
    sync_all_team_members,
)
from .novabot.sync_coordinator import syncable_teams
from .novabot.sync_status import format_sync_already_running, format_sync_started, format_sync_status
from .novabot.rag_adapter import RagVectorSearchAdapter
from .novabot.team import TeamRegistry, normalize_yuque_base_url
from .novabot.team_clients import TeamClientManager
from .novabot.chat_scope import (
    event_group_id,
    is_group_chat,
    is_group_chat_allowed,
    normalize_group_ids,
    suppress_default_llm,
)
from .novabot.reply_formatting import markdown_to_plaintext
from .novabot.table_renderer import (
    clean_table_images,
    ensure_cjk_font,
    render_tables_as_images,
)
from .novabot.help_text import format_help_text
from .novabot.rag_commands import RagCommandContext, handle_rag_command
from .novabot.card_commands import generate_card_command, validate_card_request
from .novabot.gap_commands import analyze_gap_command, validate_gap_request
from .novabot.weekly_commands import handle_weekly_command
from .novabot.community_artifacts import (
    init_member_trajectories_from_docs,
    update_collaboration_network_from_docs,
)
from .novabot.account_binding import bind_yuque_account, unbind_yuque_account
from .novabot.collab_commands import (
    build_collab_find_query,
    collab_usage_for_find,
    extract_collab_content,
    format_collaborators,
    format_potential_collaborators,
)
from .novabot.memory_commands import (
    analyze_memory_with_llm,
    build_memory_overview,
    extract_memory_search_keyword,
    format_memory_clear_result,
    format_memory_search_results,
    format_recent_memory,
    format_unknown_memory_action,
    resolve_bound_memory_user,
)
from .novabot.partner_commands import (
    build_partner_agent_query,
    find_partner_fallback,
    partner_missing_profile_message,
)
from .novabot.progress_commands import (
    analyze_progress_with_llm,
    build_progress_overview,
    extract_progress_content,
    format_domain_progress,
    format_progress_overview_without_analysis,
    progress_usage_for_add,
    progress_usage_for_level,
    record_progress_milestone,
    set_progress_level,
)
from .novabot.questions_commands import (
    extract_questions_content,
    find_related_docs_for_questions,
    format_all_questions,
    format_frequent_questions,
    format_resolve_question_result,
    format_unknown_questions_action,
    format_unresolved_questions,
    parse_resolve_args,
    questions_usage_for_resolve,
)
from .novabot.trajectory_commands import (
    analyze_trajectory_with_llm,
    build_trajectory_topic_query,
    extract_trajectory_content,
    find_member_id_by_name,
    format_member_trajectory,
    format_topic_fallback,
    should_analyze_trajectory,
    trajectory_usage_for_topic,
)
from .novabot.tools import ALL_TOOLS
from .novabot.knowledge_base import KnowledgeBaseManager
from .novabot.memory import ConversationMemory, MemberTrajectory, CollaborationNetwork
from .novabot import group_reply_gate


# ============================================================================
# 主插件类
# ============================================================================

@register("astrbot_plugin_yuque", "peace", "NOVA 社团智能助手", "v0.29.3")
class NovaBotPlugin(Star):
    """NovaBot 主插件"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # 配置
        self.yuque_token = config.get("yuque_token", "")
        self.yuque_base_url = normalize_yuque_base_url(
            config.get("yuque_base_url", "https://www.yuque.com/api/v2")
        )
        self.embedding_api_key = config.get("embedding_api_key", "")
        self.embedding_base_url = config.get("embedding_base_url", "")
        self.embedding_model = config.get("embedding_model", "text-embedding-3-small")
        logger.info(f"[Config] Yuque API Base URL: {self.yuque_base_url}")

        # 消息路由配置
        wake_words_str = config.get("wake_words", "novabot,nova,诺瓦")
        self.wake_words = [w.strip().lower() for w in wake_words_str.split(",") if w.strip()]
        self.enable_private_chat = config.get("enable_private_chat", True)
        self.enable_group_at = config.get("enable_group_at", True)
        self.enable_group_whitelist = config.get("enable_group_whitelist", False)
        self.group_whitelist = normalize_group_ids(config.get("group_whitelist", ""))
        self.group_reply_mode = config.get("group_reply_mode", "trigger")
        self.render_tables_as_images = config.get("render_tables_as_images", True)
        self.table_font_path = str(config.get("table_font_path", "") or "")
        self.auto_download_table_font = config.get("auto_download_table_font", True)
        try:
            self.table_font_download_timeout = int(config.get("table_font_download_timeout", 30))
        except (TypeError, ValueError):
            self.table_font_download_timeout = 30
        if not 5 <= self.table_font_download_timeout <= 300:
            logger.warning(
                "[Config] table_font_download_timeout 超出范围，回退为 30 秒"
            )
            self.table_font_download_timeout = 30
        if self.group_reply_mode not in ("trigger", "smart"):
            logger.warning(
                f"[Config] group_reply_mode 无效值 {self.group_reply_mode!r}，回退为 trigger"
            )
            self.group_reply_mode = "trigger"
        logger.info(
            f"[Config] 群聊白名单: enabled={self.enable_group_whitelist}, "
            f"groups={len(self.group_whitelist)}"
        )

        # 获取插件数据目录（AstrBot 标准路径，使用 self.name）
        # self.name 来自 @register 装饰器的第一个参数，需要先调用 super().__init__(context)
        # get_astrbot_data_path() 返回 str，需要转换为 Path
        self.data_dir = PathlibPath(get_astrbot_data_path()) / "plugin_data" / self.name
        self._table_image_dir = self.data_dir / "table_images"
        self._resolved_table_font_path: Optional[str] = None

        # 组件
        self.storage = Storage(data_dir=str(self.data_dir))
        self.token_monitor = TokenMonitor(self.storage.data_dir)  # 必须先初始化
        self.profile_gen = ProfileGenerator(self.token_monitor)
        self.partner_matcher = PartnerMatcher(self.storage)
        self.subscription_manager = SubscriptionManager(self.storage)
        self.search_logger = SearchLogger(self.storage.data_dir)
        self.ask_box = AskBoxManager(self.storage.data_dir)
        self.agent = NovaBotAgent(self)
        self.team_registry = TeamRegistry(config)
        self.memory_manager = ConversationMemory(self.storage.data_dir)  # 长期记忆
        self.trajectory_manager = MemberTrajectory(self.storage.data_dir)  # 成员轨迹
        self.collaboration_manager = CollaborationNetwork(self.storage.data_dir)  # 协作网络
        self.token_limiter = TokenLimiter(
            self.storage.data_dir,
            daily_limit=config.get("token_daily_limit", 50000),
        )  # Token 限流
        self.yuque_clients = TeamClientManager(
            self.team_registry,
            legacy_token=self.yuque_token,
            legacy_base_url=self.yuque_base_url,
        )
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
        self.chunk_store = ChunkStore(self.storage.data_dir / "chunk_index.db")
        self.knowledge_core: Optional[KnowledgeCore] = None

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

        self.knowledge_core = KnowledgeCore(
            self.chunk_store,
            vector_search=RagVectorSearchAdapter(self.rag, self.chunk_store) if self.rag else None,
        )

        # 初始化学习路径推荐器（依赖 RAG）
        self.path_recommender = LearningPathRecommender(self.storage, self.rag, self.token_monitor)

        # 初始化学习缺口分析器（依赖 RAG）
        self.gap_analyzer = LearningGapAnalyzer(self.storage, self.rag, self.token_monitor)

        # 初始化知识库管理器（依赖 DocIndex + RAG + docs_dir）
        self.kb_manager = KnowledgeBaseManager(
            self._get_doc_index(), self.rag, self.storage.docs_dir
        )

        logger.info("NovaBot 插件初始化完成 (v0.29.3)")

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

        # 主动关心任务（v0.27.1）
        self._care_task: Optional[asyncio.Task] = None
        self._care_running = False
        if config.get("proactive_care_enabled", True):
            self._try_start_proactive_care()

    def _try_start_proactive_care(self):
        """尝试启动主动关心任务"""
        try:
            loop = asyncio.get_running_loop()
            self._care_task = loop.create_task(self._proactive_care_loop())
            self._care_running = True
            logger.info("[Care] 主动关心任务已启动")
        except RuntimeError:
            # 事件循环未运行，等待 on_astrbot_loaded 触发
            logger.info("[Care] 事件循环未运行，稍后启动主动关心任务")

    async def _proactive_care_loop(self):
        """主动关心循环"""
        interval_hours = self.config.get("care_interval_hours", 1)
        interval_seconds = interval_hours * 3600

        logger.info(f"[Care] 主动关心循环启动，间隔 {interval_hours} 小时")

        while self._care_running:
            try:
                await asyncio.sleep(interval_seconds)
                await self._check_and_care()
            except asyncio.CancelledError:
                logger.info("[Care] 主动关心任务被取消")
                break
            except Exception as e:
                logger.error(f"[Care] 主动关心任务出错: {e}", exc_info=True)
                await asyncio.sleep(60)  # 出错后等待 1 分钟再重试

    async def _check_and_care(self):
        """检查需要关心的用户"""
        if not self.memory_manager:
            return

        inactive_days = self.config.get("inactive_threshold_days", 7)
        unresolved_days = self.config.get("unresolved_question_days", 7)

        # 获取所有活跃用户
        active_members = []
        if self.trajectory_manager:
            active_members = self.trajectory_manager.get_all_active_members(days=30)

        # 如果没有轨迹数据，从绑定记录获取
        if not active_members:
            bindings = self.storage.get_all_bindings()
            for binding in bindings:
                yuque_id = binding.get("yuque_id")
                if yuque_id:
                    active_members.append({
                        "member_id": str(yuque_id),
                        "last_active": None,
                    })

        cared_count = 0
        for member in active_members[:20]:  # 每次最多检查 20 个用户
            member_id = member.get("member_id")
            if not member_id:
                continue

            try:
                # 检查不活跃
                last_active = member.get("last_active")
                if last_active:
                    from datetime import datetime, timezone
                    try:
                        last_date = datetime.fromisoformat(last_active)
                        # 确保 last_date 有时区信息
                        if last_date.tzinfo is None:
                            last_date = last_date.replace(tzinfo=timezone.utc)
                        days_inactive = (datetime.now(timezone.utc) - last_date).days
                        if days_inactive >= inactive_days:
                            await self._send_care_message(member_id, "inactive", {"days": days_inactive})
                            cared_count += 1
                            continue
                    except ValueError:
                        pass

                # 检查未解决问题
                unresolved = self.memory_manager.get_unresolved_questions(member_id)
                for q in unresolved:
                    first_asked = q.get("first_asked", "")
                    if first_asked:
                        from datetime import datetime, timezone
                        try:
                            ask_date = datetime.fromisoformat(first_asked)
                            # 确保 ask_date 有时区信息
                            if ask_date.tzinfo is None:
                                ask_date = ask_date.replace(tzinfo=timezone.utc)
                            days_since = (datetime.now(timezone.utc) - ask_date).days
                            if days_since >= unresolved_days:
                                await self._send_care_message(member_id, "unresolved", {
                                    "question": q.get("question", ""),
                                    "days": days_since,
                                })
                                cared_count += 1
                                break
                        except ValueError:
                            pass

            except Exception as e:
                logger.warning(f"[Care] 检查用户 {member_id} 时出错: {e}")

        if cared_count > 0:
            logger.info(f"[Care] 本次检查触发了 {cared_count} 次主动关心")

    async def _send_care_message(self, member_id: str, care_type: str, data: dict):
        """发送关心消息

        注意：目前只记录日志，实际发送需要获取用户的平台 ID 并发送主动消息。
        这需要扩展绑定系统来存储 platform_id -> yuque_id 的反向映射。
        """
        if care_type == "inactive":
            days = data.get("days", 7)
            logger.info(f"[Care] 用户 {member_id} 已 {days} 天未活跃，应该关心")
            # TODO: 实现实际的主动消息发送
            # 需要获取用户的平台 ID，然后使用 context.send_message() 发送

        elif care_type == "unresolved":
            question = data.get("question", "")
            days = data.get("days", 7)
            logger.info(f"[Care] 用户 {member_id} 的问题「{question[:20]}」已 {days} 天未解决")

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
        ip_whitelist = self.config.get("webhook_ip_whitelist", [])

        try:
            self._webhook_runner = web.AppRunner(self._webhook_app)
            await self._webhook_runner.setup()
            self._webhook_site = web.TCPSite(self._webhook_runner, "0.0.0.0", port)
            await self._webhook_site.start()
            self._webhook_started = True
            logger.info(f"[Webhook] 服务已启动: http://0.0.0.0:{port}/yuque/webhook")
            logger.info(f"[Webhook] 健康检查: http://0.0.0.0:{port}/health")

            # 安全警告
            if not ip_whitelist:
                logger.warning("=" * 60)
                logger.warning("[安全警告] Webhook 服务绑定在 0.0.0.0 且未配置 IP 白名单！")
                logger.warning("建议操作：")
                logger.warning("1. 在配置中设置 webhook_ip_whitelist（语雀服务器 IP）")
                logger.warning("2. 或通过防火墙/反向代理限制端口访问")
                logger.warning("=" * 60)
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
            trajectory_manager=self.trajectory_manager,
            chunk_store=self.chunk_store,
            cache_clear_callback=self.rag.clear_cache if self.rag else None,
        )
        logger.info("[Webhook] Webhook 应用设置完成")

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self):
        """AstrBot 初始化完成后启动 Webhook 服务（备用触发）"""
        await self._start_webhook_service()
        asyncio.create_task(self._initialize_reply_rendering())

    async def _initialize_reply_rendering(self):
        """Prepare optional Markdown table rendering resources."""
        clean_table_images(self._table_image_dir)
        if not self.render_tables_as_images:
            logger.info("[Reply] 表格图片渲染未启用")
            return
        try:
            self._resolved_table_font_path = await ensure_cjk_font(
                self.data_dir,
                self.table_font_path or None,
                self.auto_download_table_font,
                download_timeout=self.table_font_download_timeout,
            )
            if self._resolved_table_font_path:
                logger.info(f"[Reply] 表格图片字体已就绪: {self._resolved_table_font_path}")
            else:
                logger.warning("[Reply] 未找到可用中文字体，包含中文的表格会回退为纯文本")
        except Exception as e:
            logger.warning(f"[Reply] 表格图片字体初始化失败，回退为纯文本: {e}")
            self._resolved_table_font_path = None

    def _plain_result(self, event: AstrMessageEvent, text: str):
        """Return a plain reply with Markdown markers removed."""
        return event.plain_result(markdown_to_plaintext(text))

    async def _rich_result(self, event: AstrMessageEvent, text: str):
        """Return plain text or a text+image chain for Markdown table replies."""
        if not self.render_tables_as_images:
            logger.info("[Reply] render_tables_as_images=false，使用纯文本回复")
            return self._plain_result(event, text)

        segments = await asyncio.to_thread(
            render_tables_as_images,
            text,
            self._table_image_dir,
            font_path=self._resolved_table_font_path,
        )
        text_segments = sum(1 for seg_type, _ in segments if seg_type == "text")
        image_segments = sum(1 for seg_type, _ in segments if seg_type == "image")
        logger.info(
            f"[Reply] 表格渲染结果: text_segments={text_segments}, "
            f"image_segments={image_segments}"
        )
        return self._build_chain_result(event, segments)

    def _build_chain_result(self, event: AstrMessageEvent, segments: list[tuple[str, str]]):
        """Build an AstrBot message result from text/image segments."""
        import astrbot.api.message_components as comp

        chain: list[object] = []
        for seg_type, content in segments:
            if seg_type == "text" and content.strip():
                chain.append(comp.Plain(content))
            elif seg_type == "image":
                chain.append(comp.Image.fromFileSystem(content))
        logger.info(f"[Reply] 构建消息链: segments={len(segments)}, chain={len(chain)}")
        if not chain:
            return event.plain_result("")
        if len(chain) == 1 and isinstance(chain[0], comp.Plain):
            return event.plain_result(chain[0].text)
        return event.chain_result(chain)

    async def terminate(self):
        """插件卸载时的清理"""
        # 停止主动关心任务
        self._care_running = False
        if self._care_task:
            try:
                self._care_task.cancel()
                await self._care_task
                logger.info("[Care] 主动关心任务已停止")
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning(f"[Care] 停止任务失败: {e}")

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

        # 关闭 RAG 引擎资源
        if self.rag:
            try:
                await self.rag.close()
                logger.info("[RAG] 引擎资源已释放")
            except Exception as e:
                logger.warning(f"[RAG] 关闭资源失败: {e}")

        # 关闭 DocIndex 数据库连接
        if self._doc_index:
            try:
                self._doc_index.close()
            except Exception as e:
                logger.debug(f"[DocIndex] 关闭连接: {e}")

        if self.chunk_store:
            try:
                self.chunk_store.close()
            except Exception as e:
                logger.debug(f"[ChunkStore] 关闭连接: {e}")

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
        # 注意：User-Agent 可被伪造，仅作为辅助检查
        if "Yuque" not in user_agent and "YUQUE" not in user_agent.upper():
            logger.warning(f"[Webhook] 可疑请求 User-Agent: {user_agent}, 来源: {client_host}")
            # 如果未设置 IP 白名单，拒绝请求
            if not ip_whitelist:
                logger.error("[Webhook] 安全警告: 未配置 IP 白名单且 User-Agent 异常，拒绝请求")
                return web.json_response(
                    {"status": "error", "message": "unauthorized"},
                    status=403,
                )

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
            # 不向外部暴露内部错误详情
            return web.json_response(
                {"status": "error", "message": "internal error"},
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

    def _get_client(self, team_id: str = "default") -> YuqueClient:
        """获取语雀客户端（懒加载）"""
        return self.yuque_clients.get(team_id)

    async def _close_client(self):
        await self.yuque_clients.close_all()

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
                # 记录到 Token 监控
                self.token_monitor.log_usage(
                    feature="chat",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )

                # 用户限流由 NovaBotAgent 的“预留+校正”路径统一处理，
                # 避免在 on_llm_response 再次记账导致重复扣减。

                logger.info(f"[LLM] 记录聊天 token: 入 {input_tokens}, 出 {output_tokens}")
        except Exception as e:
            logger.warning(f"[LLM] 记录聊天 token 失败: {e}")

    # ========== 自然语言交互 ==========

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        """处理消息（根据消息路由规则）

        消息路由规则：
        - 私聊：直接响应（可配置）
        - 群聊 trigger 模式：需要 @ 或唤醒词触发
        - 群聊 smart 模式：非显式消息经守门判断是否回复
        - 命令消息：跳过，让命令处理器处理
        """
        msg = event.message_str.strip()

        if not self._is_event_scope_allowed(event):
            suppress_default_llm(event)
            return

        # 跳过命令消息
        if self._is_command(msg):
            return

        # 判断是否应该处理这条消息
        should_handle, query, trigger = self._should_handle_message(event, msg)
        if not should_handle:
            return  # 不处理，让其他插件处理

        # 智能旁听：由守门模块决定是否回复
        if trigger == "smart":
            should_reply, gate_reason = await group_reply_gate.should_reply(
                event, query, self
            )
            if not should_reply:
                logger.info(f"[on_message] 智能旁听跳过: {gate_reason}")
                suppress_default_llm(event)
                return

        # 处理消息
        logger.info(f"[on_message] 处理消息 ({trigger}): {query[:30]}...")
        try:
            response = await self.agent.handle_message(event, query)
            yield await self._rich_result(event, response)
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
            "tokens", "ask", "askreset", "kb", "nova", "card", "persona", "memory", "progress", "questions",
            "trajectory", "collab"
        ]
        first_word = msg.split()[0].lower() if msg.split() else ""
        if first_word in known_commands:
            return True

        return False

    def _is_event_scope_allowed(self, event: AstrMessageEvent) -> bool:
        allowed = is_group_chat_allowed(
            event,
            whitelist_enabled=self.enable_group_whitelist,
            allowed_group_ids=self.group_whitelist,
        )
        if not allowed:
            logger.info(f"[Scope] 忽略非白名单群聊: group_id={event_group_id(event)}")
        return allowed

    def _should_handle_message(self, event: AstrMessageEvent, msg: str) -> tuple:
        """判断是否应该处理这条消息

        Returns:
            (should_handle, processed_query, trigger)
            trigger: "at" | "wake" | "smart" | "private" | ""
        """
        if not self._is_event_scope_allowed(event):
            return False, "", ""

        is_group = is_group_chat(event)

        if is_group:
            # 群聊：显式 @ 触发
            if self.enable_group_at and self._is_at_me(event):
                logger.info("[on_message] 检测到 @ 触发")
                return True, self._remove_at(event, msg), "at"

            import re
            for wake in self.wake_words:
                # 支持唤醒词后有标点（如 "nova，帮我..."）
                pattern = rf'^{re.escape(wake)}[\s,，:：]*'
                if re.match(pattern, msg.lower()):
                    logger.info(f"[on_message] 检测到唤醒词: {wake}")
                    processed = re.sub(
                        pattern, "", msg, count=1, flags=re.IGNORECASE
                    ).strip()
                    return True, processed, "wake"

            # 智能旁听候选
            if self.group_reply_mode == "smart":
                return True, msg, "smart"

            return False, "", ""
        else:
            # 私聊：直接响应（可配置）
            if self.enable_private_chat:
                return True, msg, "private"
            import re
            for wake in self.wake_words:
                pattern = rf'^{re.escape(wake)}[\s,，:：]*'
                if re.match(pattern, msg.lower()):
                    processed = re.sub(
                        pattern, "", msg, count=1, flags=re.IGNORECASE
                    ).strip()
                    return True, processed, "wake"
            return False, "", ""

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
    async def sync_cmd(self, event: AstrMessageEvent, action: str = "", team_id: str = ""):
        """同步语雀知识库

        用法:
        - /sync - 同步全部已启用团队知识库（后台运行）
        - /sync <team_id> 或 /sync team <team_id> - 同步指定团队
        - /sync members - 同步全部已启用团队成员
        - /sync members <team_id> - 同步指定团队成员
        - /sync status - 查看同步状态/进度
        """
        if not self._is_event_scope_allowed(event):
            return
        requested_sync_team_id = ""
        action_lower = action.lower()
        if action_lower == "team":
            requested_sync_team_id = team_id.strip()
        elif action_lower not in ("", "members", "status", "collab"):
            requested_sync_team_id = action.strip()

        if action_lower == "status":
            state = self.storage.load_sync_state()
            yield event.plain_result(format_sync_status(state))
            return

        await self.team_registry.discover_pending()
        sync_teams, sync_error = select_sync_teams(
            self.team_registry.list_enabled(),
            requested_team_id=requested_sync_team_id,
        )
        if sync_error:
            yield event.plain_result(sync_error)
            return
        all_sync_teams = syncable_teams(self.team_registry.list_enabled())
        if not all_sync_teams:
            yield event.plain_result("❌ 未配置语雀 Token")
            return

        # 同步团队成员
        if action_lower == "members":
            selected_teams, error = select_member_sync_teams(
                self.team_registry.list_enabled(),
                requested_team_id=team_id,
            )
            if error:
                yield event.plain_result(error)
                return

            if team_id.strip():
                selected_team = selected_teams[0]
                yield event.plain_result(
                    f"🔄 同步团队成员... ({selected_team.name}, team_id={selected_team.team_id})"
                )
            else:
                yield event.plain_result(f"🔄 同步多团队成员... ({len(selected_teams)} 个团队)")

            try:
                yield event.plain_result(
                    await sync_all_team_members(
                        teams=selected_teams,
                        storage=self.storage,
                        client_factory=self._get_client,
                    )
                )
            except Exception as e:
                logger.error(f"同步团队成员失败: {e}")
                yield event.plain_result(f"❌ 同步失败: {e}")
            return

        # 手动更新协作网络和成员轨迹
        if action_lower == "collab":
            yield event.plain_result(
                refresh_collaboration_artifacts(
                    collaboration_manager=self.collaboration_manager,
                    trajectory_manager=self.trajectory_manager,
                    update_collaboration=self._update_collaboration_network,
                    init_trajectories=self._init_member_trajectories,
                )
            )
            return

        # 检查是否已在同步（使用锁保护）
        state = self.storage.load_sync_state()
        if self._sync_lock.locked():
            yield event.plain_result(format_sync_already_running(state))
            return

        # 启动后台同步
        asyncio.create_task(self._background_sync(requested_team_id=requested_sync_team_id))
        yield event.plain_result(format_sync_started(len(sync_teams), team_id=requested_sync_team_id))

    async def _background_sync(self, requested_team_id: str = ""):
        """后台同步任务"""
        # 使用锁保护，防止并发同步
        async with self._sync_lock:
            try:
                await run_background_sync_pipeline(
                    team_registry=self.team_registry,
                    storage=self.storage,
                    docs_dir=self.storage.docs_dir,
                    rag=self.rag,
                    collaboration_manager=self.collaboration_manager,
                    trajectory_manager=self.trajectory_manager,
                    update_collaboration=self._update_collaboration_network,
                    init_trajectories=self._init_member_trajectories,
                    chunk_store=self.chunk_store,
                    yuque_base_url=self.yuque_base_url,
                    chunk_size=self.config.get("knowledge_chunk_size", 1200),
                    chunk_overlap=self.config.get("knowledge_chunk_overlap", 180),
                    git_enabled=self.config.get("git_enabled", True),
                    requested_team_id=requested_team_id,
                )

            except Exception as e:
                logger.error(f"后台同步失败: {e}", exc_info=True)
                mark_sync_failed(self.storage)

    def _update_collaboration_network(self):
        """从文档元数据更新协作网络

        根据知识库贡献者建立协作关系。
        """
        update_collaboration_network_from_docs(
            doc_index=self._get_doc_index(),
            collaboration_manager=self.collaboration_manager,
        )

    def _init_member_trajectories(self):
        """从文档元数据初始化成员轨迹

        为每个贡献者创建发布/更新文档的轨迹记录。
        """
        init_member_trajectories_from_docs(
            doc_index=self._get_doc_index(),
            trajectory_manager=self.trajectory_manager,
        )

    @filter.command("bind")
    async def bind_cmd(self, event: AstrMessageEvent, arg: str = ""):
        """绑定语雀账号

        用法: /bind <用户名或 login>
        """
        if not self._is_event_scope_allowed(event):
            return
        platform_id = event.get_sender_id()
        yield event.plain_result(
            bind_yuque_account(storage=self.storage, platform_id=platform_id, query=arg)
        )

    @filter.command("unbind")
    async def unbind_cmd(self, event: AstrMessageEvent):
        """解除绑定"""
        if not self._is_event_scope_allowed(event):
            return
        platform_id = event.get_sender_id()
        yield event.plain_result(unbind_yuque_account(storage=self.storage, platform_id=platform_id))

    @filter.command("profile")
    async def profile_cmd(self, event: AstrMessageEvent, action: str = "", domain: str = ""):
        """查看用户画像

        用法:
        - /profile - 查看画像
        - /profile refresh - 使用 AI 深度分析生成画像
        - /profile assess <领域> - 评估某领域的掌握程度
        """
        if not self._is_event_scope_allowed(event):
            return
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
            docs = get_profile_docs(storage=self.storage, binding=binding)
            if not docs:
                yield event.plain_result("⚠️ 未找到你的文档，请先执行 /sync 同步")
                return
            try:
                provider = self.context.get_using_provider(umo=event.unified_msg_origin)
                if not provider:
                    yield event.plain_result("❌ LLM 未配置，请先配置模型 Provider")
                    return
                yield event.plain_result(f"🔍 正在评估你在「{domain}」领域的学习情况...")
                _, result = await assess_user_domain(
                    storage=self.storage,
                    profile_generator=self.profile_gen,
                    binding=binding,
                    domain=domain,
                    provider=provider,
                    docs=docs,
                )
                yield await self._rich_result(event, result)

            except Exception as e:
                logger.error(f"领域评估失败: {e}", exc_info=True)
                yield event.plain_result(f"❌ 评估失败: {e}")
            return

        # 刷新画像（使用 LLM 深度分析）
        if action.lower() == "refresh":
            docs = get_profile_docs(storage=self.storage, binding=binding)
            if not docs:
                yield event.plain_result("⚠️ 未找到你的文档，请先执行 /sync 同步")
                return
            try:
                provider = self.context.get_using_provider(umo=event.unified_msg_origin)
                if not provider:
                    yield event.plain_result("❌ LLM 未配置，请先配置模型 Provider")
                    return
                yield event.plain_result(f"🔍 正在分析 {len(docs)} 篇文档...")
                _, result = await refresh_user_profile(
                    storage=self.storage,
                    profile_generator=self.profile_gen,
                    binding=binding,
                    provider=provider,
                    docs=docs,
                )
                yield await self._rich_result(event, result)
            except Exception as e:
                logger.error(f"生成画像失败: {e}", exc_info=True)
                yield event.plain_result(f"❌ 生成失败: {e}")
            return

        # 显示画像
        profile = self.storage.load_profile(yuque_id)
        yield self._plain_result(event, format_profile_view(binding=binding, profile=profile))

    @filter.command("partner")
    async def partner_cmd(self, event: AstrMessageEvent, topic: str = ""):
        """伙伴推荐"""
        if not self._is_event_scope_allowed(event):
            return
        platform_id = event.get_sender_id()
        binding = self.storage.get_binding(platform_id)

        if not binding:
            yield event.plain_result("请先使用 /bind 绑定账号")
            return

        yuque_id = binding.get("yuque_id")

        # 检查画像
        profile = self.storage.load_profile(yuque_id)
        if not profile:
            yield event.plain_result(partner_missing_profile_message())
            return

        try:
            yield event.plain_result("🔍 正在分析推荐...")
            response = await self.agent.handle_message(event, build_partner_agent_query(topic))
            yield await self._rich_result(event, response)

        except Exception as e:
            logger.error(f"[Partner] Agent 处理失败: {e}", exc_info=True)
            yield event.plain_result(
                find_partner_fallback(
                    matcher=self.partner_matcher,
                    storage=self.storage,
                    yuque_id=yuque_id,
                    topic=topic,
                )
            )

    async def _recommend_answerers(self, question: str) -> str:
        """根据问题推荐潜在回答者

        Args:
            question: 问题内容

        Returns:
            推荐提示文本（可能为空）
        """
        if not self.trajectory_manager and not self._get_doc_index():
            return ""

        try:
            # 从问题中提取关键词（简单分词）
            import re
            # 提取中文词组和英文单词
            keywords = re.findall(r'[\u4e00-\u9fa5]+|[a-zA-Z]+', question)
            keywords = [k for k in keywords if len(k) >= 2][:3]  # 最多3个关键词

            if not keywords:
                return ""

            candidates: dict[str, dict] = {}

            # 1. 从轨迹搜索活跃成员
            if self.trajectory_manager:
                for kw in keywords:
                    try:
                        results = self.trajectory_manager.search_by_topic(kw, days=60)
                        for r in results[:5]:
                            member_id = r.get("member_id", "")
                            if not member_id:
                                continue
                            if member_id not in candidates:
                                candidates[member_id] = {
                                    "match_count": 0,
                                    "keywords": [],
                                }
                            candidates[member_id]["match_count"] += r.get("match_count", 1)
                            if kw not in candidates[member_id]["keywords"]:
                                candidates[member_id]["keywords"].append(kw)
                    except Exception:
                        pass

            # 2. 从文档索引搜索作者
            doc_index = self._get_doc_index()
            if doc_index:
                for kw in keywords:
                    try:
                        docs = doc_index.search(title=kw, limit=10)
                        for doc in docs:
                            author = doc.get("creator_id") or doc.get("author")
                            if not author:
                                continue
                            author_str = str(author)
                            if author_str not in candidates:
                                candidates[author_str] = {
                                    "match_count": 0,
                                    "keywords": [],
                                }
                            candidates[author_str]["match_count"] += 1
                            if kw not in candidates[author_str]["keywords"]:
                                candidates[author_str]["keywords"].append(kw)
                    except Exception:
                        pass

            if not candidates:
                return ""

            # 排序取前3
            sorted_candidates = sorted(
                candidates.items(),
                key=lambda x: x[1]["match_count"],
                reverse=True
            )[:3]

            # 解析成员姓名
            members = self.storage.load_members()
            names = []
            for member_id, info in sorted_candidates:
                member_info = members.get(member_id) or members.get(int(member_id) if member_id.isdigit() else None)
                if member_info:
                    name = member_info.get("name") or member_info.get("login")
                    if name:
                        names.append(name)

            if not names:
                return ""

            return f"\n\n💡 建议邀请：{', '.join(names)}（他们在相关问题领域较活跃）"

        except Exception as e:
            logger.debug(f"[Ask] 推荐回答者失败: {e}")
            return ""

    @filter.command("path")
    async def path_cmd(self, event: AstrMessageEvent, domain: str = ""):
        """学习路径推荐

        用法:
        - /path <领域> - 生成该领域的学习路径
        """
        if not self._is_event_scope_allowed(event):
            return
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
            yield await self._rich_result(event, result)

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
        if not self._is_event_scope_allowed(event):
            return
        umo = event.unified_msg_origin
        platform_id = event.get_sender_id()

        if not sub_type:
            # 显示订阅列表
            subs = self.subscription_manager.get_subscriptions(platform_id, umo)
            result = format_subscription_list(subs)
            yield await self._rich_result(event, result)
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
        if not self._is_event_scope_allowed(event):
            return
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
        if not self._is_event_scope_allowed(event):
            return
        context = RagCommandContext(
            rag=self.rag,
            docs_dir=self.storage.docs_dir,
            embedding_model=self.embedding_model,
            search_logger=self.search_logger,
        )
        try:
            for message in handle_rag_command(
                context,
                action=action,
                query=query,
                user_id=event.get_sender_id(),
            ):
                yield await self._rich_result(event, message)
        except Exception as e:
            logger.error(f"RAG 命令失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ RAG 命令失败: {e}")

    @filter.command("webhook")
    async def webhook_cmd(self, event: AstrMessageEvent):
        """Webhook 服务状态"""
        if not self._is_event_scope_allowed(event):
            return
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
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def weekly_cmd(self, event: AstrMessageEvent, action: str = ""):
        """生成本周知识周报或导出按周原始数据。"""
        if not self._is_event_scope_allowed(event):
            return
        try:
            docs_dir = self.storage.docs_dir
            doc_index = self._get_doc_index()
            reporter = WeeklyReporter(docs_dir, doc_index=doc_index)
            umo = event.unified_msg_origin
            prov_id = await self.context.get_current_chat_provider_id(umo)
            provider = self.context.get_provider_by_id(prov_id) if prov_id else None
            messages = await handle_weekly_command(
                reporter=reporter,
                action=action,
                export_dir=self.storage.data_dir / "exports",
                provider=provider,
                token_monitor=self.token_monitor,
                send_file=lambda path: self._try_send_file(event, path),
            )
            for message in messages:
                yield await self._rich_result(event, message)
        except Exception as e:
            logger.error(f"生成周报失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 生成周报失败: {e}")

    async def _try_send_file(self, event: AstrMessageEvent, file_path: PathlibPath) -> bool:
        """尝试发送文件到会话，不支持则返回 False。"""
        try:
            if hasattr(event, "file_result"):
                yield_result = event.file_result(str(file_path))
                if yield_result:
                    await event.send(yield_result)
                    return True
        except Exception as e:
            logger.debug(f"[weekly] file_result 发送失败: {e}")

        try:
            if hasattr(event, "document_result"):
                yield_result = event.document_result(str(file_path))
                if yield_result:
                    await event.send(yield_result)
                    return True
        except Exception as e:
            logger.debug(f"[weekly] document_result 发送失败: {e}")

        return False

    @filter.command("gap")
    async def gap_cmd(self, event: AstrMessageEvent, target_domain: str = ""):
        """分析个人学习缺口

        用法: /gap [目标领域]
        例如: /gap 爬虫
        如果不指定领域，会根据用户画像自动推断
        """
        if not self._is_event_scope_allowed(event):
            return
        try:
            platform_id = event.get_sender_id()
            provider = self.context.get_using_provider(umo=event.unified_msg_origin)
            yuque_id, error = validate_gap_request(
                storage=self.storage,
                platform_id=platform_id,
                provider=provider,
            )
            if error:
                yield event.plain_result(error)
                return

            yield event.plain_result("📊 正在分析你的学习缺口...")
            result = await analyze_gap_command(
                analyzer=self.gap_analyzer,
                yuque_id=yuque_id,
                target_domain=target_domain,
                provider=provider,
            )
            yield await self._rich_result(event, result)

        except Exception as e:
            logger.error(f"学习缺口分析失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 分析失败: {e}")

    @filter.command("card")
    async def card_cmd(self, event: AstrMessageEvent, topic: str = ""):
        """生成知识卡片

        用法: /card <主题>
        例如: /card 爬虫
        """
        if not self._is_event_scope_allowed(event):
            return
        try:
            # 获取 LLM Provider
            provider = self.context.get_using_provider(umo=event.unified_msg_origin)
            error = validate_card_request(topic, provider, self.rag)
            if error:
                yield event.plain_result(error)
                return

            yield event.plain_result(f"📚 正在生成「{topic}」知识卡片...")
            result = await generate_card_command(
                topic=topic,
                provider=provider,
                rag=self.rag,
                token_monitor=self.token_monitor,
            )
            yield await self._rich_result(event, result)

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
        if not self._is_event_scope_allowed(event):
            return
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
        if not self._is_event_scope_allowed(event):
            return
        try:
            stats = self.token_monitor.get_stats(days=30)
            report = self.token_monitor.format_stats_report(stats)
            yield await self._rich_result(event, report)
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
        if not self._is_event_scope_allowed(event):
            return
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

                    # 推荐潜在回答者
                    answerer_hint = await self._recommend_answerers(content)

                    yield event.plain_result(
                        f"✅ 提问成功 (ID: {qid})\n\n"
                        f"使用 /ask view {qid} 查看回答"
                        f"{answerer_hint}"
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
                yield await self._rich_result(event, result)
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
                            logger.info("[AskBox] 已通知提问者")
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
        if not self._is_event_scope_allowed(event):
            return
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
        - /kb guide <知识库> - 生成新人导航
        """
        if not self._is_event_scope_allowed(event):
            return
        if not self.kb_manager:
            yield event.plain_result("❌ 知识库管理器未初始化")
            return

        # 从消息直接解析，更可靠
        msg = event.message_str.strip()
        # AstrBot 已去掉 / 前缀，直接检查 kb 开头
        # 处理回复消息格式（可能包含 "回复 xxx:" 前缀）
        import re
        # 提取 kb 后面的内容（支持回复格式）
        kb_match = re.search(r'\bkb\s+(.+)$', msg, re.IGNORECASE)
        if kb_match:
            content = kb_match.group(1).strip()
        elif msg.lower() == "kb":
            content = ""
        else:
            content = args.strip()

        try:
            # 无参数：列出知识库
            if not content:
                kbs = self.kb_manager.list_kbs()
                result = self.kb_manager.format_kb_list(kbs)
                yield await self._rich_result(event, result)
                return

            # 检查是否是 guide 子命令
            if content.lower().startswith("guide "):
                kb_name = content[6:].strip()
                if not kb_name:
                    yield event.plain_result("用法: /kb guide <知识库>")
                    return

                yield event.plain_result(f"🔍 正在生成「{kb_name}」新人导航...")
                guide_info = self.kb_manager.get_kb_info(kb_name)
                guide_team_id = str(guide_info.get("team_id") or "") if guide_info else ""
                guide = await self.kb_manager.get_kb_guide(
                    kb_name, self.context, event, self.token_monitor, team_id=guide_team_id or None
                )
                if not guide:
                    yield event.plain_result(f"❌ 未找到知识库「{kb_name}」")
                    return

                result = self.kb_manager.format_kb_guide(guide)
                yield await self._rich_result(event, result)
                return

            # 检查是否是 updates 子命令
            if content.lower().startswith("updates"):
                parts = content.split(maxsplit=2)
                kb_name = parts[1] if len(parts) > 1 else ""
                days = int(parts[2]) if len(parts) > 2 else 7

                if not kb_name:
                    yield event.plain_result("用法: /kb updates <知识库> [天数]")
                    return

                update_info = self.kb_manager.get_kb_info(kb_name)
                update_team_id = str(update_info.get("team_id") or "") if update_info else ""
                result = self.kb_manager.format_kb_updates(
                    kb_name, days, team_id=update_team_id or None
                )
                yield await self._rich_result(event, result)
                return

            # 查找匹配的知识库（支持知识库名包含空格）
            kbs = self.kb_manager.list_kbs()
            matched_kb = None
            matched_name = ""

            # 按名称长度降序，优先匹配最长的
            sorted_kbs = sorted(kbs, key=lambda x: len(x.get("book_name", "")), reverse=True)
            for kb in sorted_kbs:
                kb_name = kb.get("book_name", "")
                # 检查内容是否以知识库名开头（忽略大小写）
                if content.lower().startswith(kb_name.lower()):
                    matched_kb = kb
                    matched_name = kb_name
                    break
                # 也支持模糊匹配
                if kb_name.lower() in content.lower()[:len(kb_name) + 5]:
                    matched_kb = kb
                    matched_name = kb_name
                    break

            if not matched_kb:
                # 没有匹配的知识库，尝试当作知识库名查询
                first_space = content.find(" ")
                if first_space == -1:
                    # 单参数：当作知识库名
                    info = self.kb_manager.get_kb_info(content)
                    if not info:
                        yield event.plain_result(f"❌ 未找到知识库「{content}」")
                        return
                    result = self.kb_manager.format_kb_info(info)
                    yield await self._rich_result(event, result)
                    return
                else:
                    book_name = content[:first_space]
                    info = self.kb_manager.get_kb_info(book_name)
                    if not info:
                        yield event.plain_result(f"❌ 未找到知识库「{book_name}」")
                        return
                    result = self.kb_manager.format_kb_info(info)
                    yield await self._rich_result(event, result)
                    return

            # 找到匹配的知识库，提取查询部分
            query = content[len(matched_name):].strip()
            matched_team_id = str(matched_kb.get("team_id") or "default")

            if not query:
                # 只有知识库名，显示概览
                info = self.kb_manager.get_kb_info(matched_name, team_id=matched_team_id)
                if not info:
                    yield event.plain_result(f"❌ 未找到知识库「{matched_name}」")
                    return
                result = self.kb_manager.format_kb_info(info)
                yield await self._rich_result(event, result)
                return

            # 有查询内容，执行范围检索
            logger.info(f"[KB] 知识库: {matched_name}, team_id={matched_team_id}, 查询: {query}")

            results = self.kb_manager.search_in_kb(matched_name, query, k=5, team_id=matched_team_id)
            if not results:
                yield event.plain_result(f"在「{matched_name}」中未找到相关内容")
                return

            lines = [f"在「{matched_name}」中找到相关内容：", ""]
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
        if not self._is_event_scope_allowed(event):
            return
        yield self._plain_result(event, format_help_text())

    @filter.command("memory")
    async def memory_cmd(self, event: AstrMessageEvent, action: str = "", keyword: str = ""):
        """记忆管理"""
        if not self._is_event_scope_allowed(event):
            return
        platform_id = event.get_sender_id()
        memory_user, error = resolve_bound_memory_user(self.storage, platform_id)
        if error:
            yield event.plain_result(error)
            return

        if not self.memory_manager:
            yield event.plain_result("长期记忆系统未初始化")
            return

        user_id = memory_user.user_id
        yuque_name = memory_user.yuque_name

        try:
            if not action:
                overview = build_memory_overview(self.memory_manager, user_id, yuque_name)
                if overview.sessions_for_analysis:
                    provider = self.context.get_using_provider(umo=event.unified_msg_origin)
                    if provider:
                        yield await self._rich_result(event, overview.text)
                        analysis = await analyze_memory_with_llm(
                            provider=provider,
                            user_name=yuque_name,
                            sessions=overview.sessions_for_analysis,
                            token_monitor=self.token_monitor,
                        )
                        yield await self._rich_result(event, analysis)
                        return
                yield await self._rich_result(event, overview.text)
                return

            action_lower = action.lower()

            if action_lower == "recent":
                sessions = self.memory_manager.get_recent_sessions(user_id, limit=10)
                yield self._plain_result(event, format_recent_memory(yuque_name, sessions))
                return

            if action_lower == "search":
                search_keyword = extract_memory_search_keyword(event.message_str, keyword)
                if not search_keyword:
                    yield event.plain_result("用法: /memory search <关键词>")
                    return

                results = self.memory_manager.search_conversations(user_id, search_keyword, limit=10)
                yield self._plain_result(event, format_memory_search_results(search_keyword, results))
                return

            if action_lower == "clear":
                success = self.memory_manager.clear_user_memory(user_id)
                yield self._plain_result(event, format_memory_clear_result(yuque_name, success))
                return

            yield self._plain_result(event, format_unknown_memory_action(action))

        except Exception as e:
            logger.error(f"[Memory] 操作失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 操作失败: {e}")

    @filter.command("progress")
    async def progress_cmd(self, event: AstrMessageEvent, args: str = ""):
        """学习进度管理"""
        if not self._is_event_scope_allowed(event):
            return
        platform_id = event.get_sender_id()
        memory_user, error = resolve_bound_memory_user(self.storage, platform_id)
        if error:
            yield event.plain_result(error)
            return

        if not self.memory_manager:
            yield event.plain_result("长期记忆系统未初始化")
            return

        user_id = memory_user.user_id
        yuque_name = memory_user.yuque_name
        content = extract_progress_content(event.message_str, args)

        try:
            if not content:
                overview = build_progress_overview(self.memory_manager, user_id, yuque_name)
                if not overview.progress_for_analysis:
                    yield await self._rich_result(event, overview.text)
                    return
                provider = self.context.get_using_provider(umo=event.unified_msg_origin)
                if provider:
                    yield await self._rich_result(event, overview.text)
                    analysis = await analyze_progress_with_llm(
                        provider=provider,
                        user_name=yuque_name,
                        progress=overview.progress_for_analysis,
                        token_monitor=self.token_monitor,
                    )
                    yield await self._rich_result(event, analysis)
                else:
                    yield event.plain_result(
                        format_progress_overview_without_analysis(
                            yuque_name,
                            overview.progress_for_analysis,
                        )
                    )
                return

            parts = content.split(maxsplit=2)
            action = parts[0].lower() if parts else ""

            if action == "add":
                if len(parts) < 3:
                    yield event.plain_result(progress_usage_for_add())
                    return
                yield event.plain_result(
                    record_progress_milestone(self.memory_manager, user_id, parts[1], parts[2])
                )
                return

            if action == "level":
                if len(parts) < 3:
                    yield event.plain_result(progress_usage_for_level())
                    return
                yield event.plain_result(
                    set_progress_level(self.memory_manager, user_id, parts[1], parts[2].lower())
                )
                return

            domain = content.strip()
            progress = self.memory_manager.get_learning_progress(user_id, domain)
            yield self._plain_result(event, format_domain_progress(yuque_name, domain, progress))

        except Exception as e:
            logger.error(f"[Progress] 操作失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 操作失败: {e}")

    @filter.command("questions")
    async def questions_cmd(self, event: AstrMessageEvent, args: str = ""):
        """问题档案管理"""
        if not self._is_event_scope_allowed(event):
            return
        platform_id = event.get_sender_id()
        memory_user, error = resolve_bound_memory_user(self.storage, platform_id)
        if error:
            yield event.plain_result(error)
            return

        if not self.memory_manager:
            yield event.plain_result("长期记忆系统未初始化")
            return

        user_id = memory_user.user_id
        yuque_name = memory_user.yuque_name
        content = extract_questions_content(event.message_str, args)

        try:
            if not content:
                questions = self.memory_manager.get_unresolved_questions(user_id)
                yield self._plain_result(event, format_unresolved_questions(yuque_name, questions))
                return

            parts = content.split(maxsplit=1)
            action = parts[0].lower() if parts else ""

            if action == "all":
                questions = self.memory_manager.get_all_questions(user_id)
                stats = self.memory_manager.get_question_stats(user_id)
                yield self._plain_result(event, format_all_questions(yuque_name, questions, stats))
                return

            if action == "frequent":
                questions = self.memory_manager.get_frequent_questions(user_id, min_ask_count=2)
                related_docs = find_related_docs_for_questions(self._get_doc_index(), questions)
                yield self._plain_result(event, format_frequent_questions(yuque_name, questions, related_docs))
                return

            if action == "resolve":
                if len(parts) < 2:
                    yield event.plain_result(questions_usage_for_resolve())
                    return
                qid, resolution = parse_resolve_args(parts[1])
                if not qid:
                    yield event.plain_result(questions_usage_for_resolve())
                    return
                success = self.memory_manager.resolve_question(user_id, qid, resolution)
                yield self._plain_result(event, format_resolve_question_result(qid, success))
                return

            yield self._plain_result(event, format_unknown_questions_action(action))

        except Exception as e:
            logger.error(f"[Questions] 操作失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 操作失败: {e}")

    # =========================================================================
    # 成员轨迹指令（v0.27.0）
    # =========================================================================

    @filter.command("trajectory")
    async def trajectory_cmd(self, event: AstrMessageEvent, args: str = ""):
        """成员轨迹查询"""
        if not self._is_event_scope_allowed(event):
            return
        platform_id = event.get_sender_id()
        memory_user, error = resolve_bound_memory_user(self.storage, platform_id)
        if error:
            yield event.plain_result(error)
            return

        if not self.trajectory_manager:
            yield event.plain_result("成员轨迹系统未初始化")
            return

        yuque_id = memory_user.user_id
        yuque_name = memory_user.yuque_name
        content = extract_trajectory_content(event.message_str, args)

        try:
            if content.lower().startswith("topic "):
                topic = content[6:].strip()
                if not topic:
                    yield event.plain_result(trajectory_usage_for_topic())
                    return

                yield event.plain_result(f"🔍 正在搜索「{topic}」相关的成员活动...")

                try:
                    response = await self.agent.handle_message(
                        event,
                        build_trajectory_topic_query(topic),
                    )
                    yield await self._rich_result(event, response)
                except Exception as e:
                    logger.error(f"[Trajectory] Agent 处理失败: {e}", exc_info=True)
                    results = self.trajectory_manager.search_by_topic(topic, days=30)
                    yield event.plain_result(
                        format_topic_fallback(topic, results, self.storage.load_members())
                    )
                return

            if content:
                members = self.storage.load_members()
                member_id = find_member_id_by_name(members, content)
                if not member_id:
                    yield event.plain_result(f"未找到成员「{content}」")
                    return
                trajectory = self.trajectory_manager.get_trajectory(member_id, days=30)
                target_name = content
            else:
                trajectory = self.trajectory_manager.get_trajectory(yuque_id, days=30)
                target_name = yuque_name
                member_id = yuque_id

            if not trajectory:
                yield event.plain_result(f"「{target_name}」最近 30 天暂无活动记录")
                return

            response = format_member_trajectory(target_name, trajectory)
            is_self = not content or yuque_id == member_id
            if should_analyze_trajectory(is_self, trajectory):
                provider = self.context.get_using_provider(umo=event.unified_msg_origin)
                if provider:
                    yield await self._rich_result(event, response + "\n🔍 正在分析活动模式...")
                    analysis = await analyze_trajectory_with_llm(
                        provider=provider,
                        user_name=target_name,
                        trajectory=trajectory,
                        token_monitor=self.token_monitor,
                    )
                    yield await self._rich_result(event, analysis)
                    return

            yield await self._rich_result(event, response)

        except Exception as e:
            logger.error(f"[Trajectory] 查询失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 查询失败: {e}")

    # =========================================================================
    # 协作网络指令（v0.27.0）
    # =========================================================================

    @filter.command("collab")
    async def collab_cmd(self, event: AstrMessageEvent, args: str = ""):
        """协作网络查询"""
        if not self._is_event_scope_allowed(event):
            return
        platform_id = event.get_sender_id()
        memory_user, error = resolve_bound_memory_user(self.storage, platform_id)
        if error:
            yield event.plain_result(error)
            return

        if not self.collaboration_manager:
            yield event.plain_result("协作网络系统未初始化")
            return

        yuque_id = memory_user.user_id
        yuque_name = memory_user.yuque_name
        content = extract_collab_content(event.message_str, args)

        try:
            if content.lower().startswith("find "):
                topic = content[5:].strip()
                if not topic:
                    yield event.plain_result(collab_usage_for_find())
                    return

                yield event.plain_result(f"🔍 正在寻找「{topic}」领域的协作伙伴...")

                try:
                    response = await self.agent.handle_message(
                        event,
                        build_collab_find_query(topic),
                    )
                    yield await self._rich_result(event, response)
                except Exception as e:
                    logger.error(f"[Collab] Agent 处理失败: {e}", exc_info=True)
                    potential = self.collaboration_manager.find_potential_collaborators(
                        yuque_id,
                        topic=topic,
                        exclude_existing=True,
                        trajectory_manager=self.trajectory_manager,
                        doc_index=self._get_doc_index(),
                    )
                    if potential:
                        yield event.plain_result(
                            format_potential_collaborators(
                                topic,
                                potential,
                                members=self.storage.load_members(),
                                doc_index=self._get_doc_index(),
                            )
                        )
                    else:
                        yield event.plain_result(f"暂无「{topic}」领域的潜在协作伙伴推荐")
                return

            if content:
                members = self.storage.load_members()
                member_id = find_member_id_by_name(members, content)
                if not member_id:
                    yield event.plain_result(f"未找到成员「{content}」")
                    return
                collaborators = self.collaboration_manager.get_collaborators(member_id)
                target_name = content
            else:
                member_id = yuque_id
                collaborators = self.collaboration_manager.get_collaborators(yuque_id)
                target_name = yuque_name

            stats = self.collaboration_manager.get_member_stats(member_id)
            yield event.plain_result(
                format_collaborators(
                    target_name,
                    collaborators,
                    stats=stats,
                    members=self.storage.load_members(),
                )
            )

        except Exception as e:
            logger.error(f"[Collab] 查询失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 查询失败: {e}")
