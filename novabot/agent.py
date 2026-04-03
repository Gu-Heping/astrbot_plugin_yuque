"""
NovaBot Agent 模块
处理自然语言交互，调用 LLM Tool
"""

import json
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.core.agent.tool import ToolSet
from astrbot.core.agent.message import (
    AssistantMessageSegment,
    UserMessageSegment,
    TextPart,
)


# 默认系统提示词
DEFAULT_SYSTEM_PROMPT = """你是 NovaBot，NOVA 社团的智能助手。

【你的职责】
1. 帮助成员找到需要的知识（搜索语雀文档）
2. 连接成员和学习伙伴（基于画像匹配）
3. 支持成员的学习成长（学习路径、进度追踪）
4. 传递社团的温暖（关心、鼓励、陪伴）

【你的性格】
- 温暖但不过度热情
- 专业但不生硬
- 记得用户说过的话
- 主动关心用户的学习状态

【工具使用原则】
在调用工具前，先思考：
1. 用户的核心需求是什么？
2. 是否真的需要工具？有时候用户只是想聊天。
3. 调用哪个工具最合适？

示例：
- 用户: "帮我找一下爬虫教程" → 调用 search_knowledge_base 或 grep_local_docs
- 用户: "我最近很累" → 不调用工具，直接回复
- 用户: "我想学爬虫，怎么入门" → 调用 learning_path 或 generate_knowledge_card
- 用户: "看看社团里有哪些作者" → 调用 list_authors
- 用户: "张三写过哪些文档" → 调用 search_docs 按作者筛选
- 用户: "https://nova.yuque.com/xxx/yyy/zzz" → 调用 parse_yuque_url 解析链接并读取文档
- 用户: "推荐学习伙伴" → 调用 partner_recommend
- 用户: "谁也在学爬虫" → 调用 partner_recommend（指定 topic="爬虫"）
- 用户: "本周周报" → 调用 weekly_report
- 用户: "我的学习缺口" → 调用 knowledge_gap
- 用户: "订阅爬虫知识库" → 调用 subscribe（sub_type="repo", target="爬虫"）
- 用户: "看看我的画像" → 调用 profile_view

【回答风格】
- 有温度，像学习伙伴
- 回答后追问「还想了解什么？」
- 标注来源：「根据《文档名》by 作者...」
"""


class NovaBotAgent:
    """NovaBot Agent 处理自然语言交互"""

    def __init__(self, plugin):
        self.plugin = plugin

    async def handle_message(self, event: AstrMessageEvent, query: str = None) -> str:
        """处理用户消息，返回 Agent 回复

        Args:
            event: 消息事件
            query: 处理后的查询（可选，如移除唤醒词后的内容）

        Returns:
            Agent 回复文本
        """
        umo = event.unified_msg_origin
        # 使用传入的 query 或原始消息
        user_message = query if query else event.message_str

        # 获取 Provider ID
        prov_id = await self.plugin.context.get_current_chat_provider_id(umo)
        if not prov_id:
            return "LLM 未配置，无法处理消息。请联系管理员配置 LLM 服务。"

        # 获取用户画像（如果已绑定）
        user_context = await self._get_user_context(event)

        # 获取对话历史
        conversation_history = await self._get_conversation_history(umo)

        # 构建系统提示词（包含历史）
        system_prompt = self._build_system_prompt(user_context, conversation_history)

        # 获取工具
        tools = self._get_tools()

        if not tools:
            return "工具未初始化，请检查插件配置。"

        # 检查 Token 限额（v0.27.2）
        if self.plugin.token_limiter and user_context.get("bound"):
            yuque_id = user_context.get("yuque_id")
            if yuque_id:
                is_allowed, remaining, used = self.plugin.token_limiter.check_limit(str(yuque_id))
                if not is_allowed:
                    return (
                        f"⚠️ 您的每日 Token 额度已用完。\n"
                        f"已使用: {used:,} / 限额: {self.plugin.token_limiter.daily_limit:,}\n"
                        f"请明天再试，或联系管理员提升额度。"
                    )
                elif remaining < 5000:
                    # 剩余不足 5000 时提醒
                    logger.warning(f"[Agent] 用户 {yuque_id} Token 即将用尽: 剩余 {remaining}")

        # 调用 Agent
        try:
            logger.info(f"[Agent] 开始处理消息: {user_message[:50]}...")
            if conversation_history:
                logger.info(f"[Agent] 携带 {len(conversation_history)} 条历史记录")

            llm_resp = await self.plugin.context.tool_loop_agent(
                event=event,
                chat_provider_id=prov_id,
                prompt=user_message,
                system_prompt=system_prompt,
                tools=ToolSet(tools),
                max_steps=10,
                tool_call_timeout=60,
            )

            # 记录对话到 conversation_manager
            await self._record_conversation(umo, user_message, llm_resp.completion_text)

            # 新增：记录到长期记忆（需已绑定语雀）
            if self.plugin.memory_manager and user_context.get("bound"):
                yuque_id = user_context.get("yuque_id")
                if yuque_id:
                    self.plugin.memory_manager.add_session(
                        user_id=str(yuque_id),
                        umo=umo,
                        user_msg=user_message,
                        assistant_msg=llm_resp.completion_text,
                    )
                    logger.debug(f"[Agent] 已记录到长期记忆: yuque_id={yuque_id}")

            logger.info(f"[Agent] 消息处理完成")
            return llm_resp.completion_text

        except Exception as e:
            logger.error(f"[Agent] 处理消息失败: {e}", exc_info=True)
            return "处理消息时出错，请稍后重试。"

    async def _get_user_context(self, event: AstrMessageEvent) -> dict:
        """获取用户上下文信息

        Args:
            event: 消息事件

        Returns:
            用户上下文字典，包含绑定状态和画像信息
        """
        platform_id = event.get_sender_id()
        binding = self.plugin.storage.get_binding(platform_id)

        if not binding:
            return {"bound": False, "platform_id": platform_id}

        yuque_id = binding.get("yuque_id")
        yuque_name = binding.get("yuque_name", "")

        # 获取用户画像
        profile = None
        preferences = None
        if yuque_id:
            profile = self.plugin.storage.load_profile(yuque_id)
            preferences = self.plugin.storage.load_preferences(yuque_id)

        return {
            "bound": True,
            "platform_id": platform_id,
            "yuque_id": yuque_id,
            "yuque_name": yuque_name,
            "profile": profile.get("profile") if profile else None,
            "stats": profile.get("stats") if profile else None,
            "preferences": preferences,
        }

    async def _get_conversation_history(self, umo: str, max_rounds: int = 5) -> list:
        """获取对话历史

        Args:
            umo: 会话标识
            max_rounds: 最大保留历史轮数

        Returns:
            对话历史列表，每项为 {"role": "user/assistant", "content": "..."}
        """
        try:
            conv_mgr = self.plugin.context.conversation_manager
            if not conv_mgr:
                logger.debug("[Agent] conversation_manager 未初始化")
                return []

            # 获取当前对话 ID
            curr_cid = await conv_mgr.get_curr_conversation_id(umo)
            if not curr_cid:
                logger.debug("[Agent] 无当前对话")
                return []

            # 获取对话
            conversation = await conv_mgr.get_conversation(umo, curr_cid)
            if not conversation or not conversation.history:
                logger.debug("[Agent] 对话历史为空")
                return []

            # 解析历史（JSON 格式）
            try:
                history_data = json.loads(conversation.history)
            except json.JSONDecodeError:
                logger.warning("[Agent] 对话历史解析失败")
                return []

            # 只保留最近的 max_rounds 轮（每轮包含 user + assistant）
            # history_data 格式: [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
            if isinstance(history_data, list):
                # 保留最近的几轮（每轮 2 条消息）
                recent_history = history_data[-(max_rounds * 2):]
                return recent_history

            return []

        except Exception as e:
            logger.warning(f"[Agent] 获取对话历史失败: {e}")
            return []

    async def _record_conversation(self, umo: str, user_msg: str, assistant_msg: str):
        """记录对话到 conversation_manager

        Args:
            umo: 会话标识
            user_msg: 用户消息
            assistant_msg: Agent 回复
        """
        try:
            conv_mgr = self.plugin.context.conversation_manager
            if not conv_mgr:
                logger.debug("[Agent] conversation_manager 未初始化，跳过记录")
                return

            # 获取当前对话 ID
            curr_cid = await conv_mgr.get_curr_conversation_id(umo)
            if not curr_cid:
                logger.debug("[Agent] 无当前对话，跳过记录")
                return

            # 构建消息
            user_msg_segment = UserMessageSegment(content=[TextPart(text=user_msg)])
            assistant_msg_segment = AssistantMessageSegment(content=[TextPart(text=assistant_msg)])

            # 添加到对话历史
            await conv_mgr.add_message_pair(
                cid=curr_cid,
                user_message=user_msg_segment,
                assistant_message=assistant_msg_segment,
            )
            logger.debug("[Agent] 对话已记录到 conversation_manager")

            # 新增：记录到长期记忆
            await self._record_to_long_term_memory(umo, user_msg, assistant_msg)

        except Exception as e:
            logger.warning(f"[Agent] 记录对话失败: {e}")

    async def _record_to_long_term_memory(self, umo: str, user_msg: str, assistant_msg: str):
        """记录对话到长期记忆系统

        Args:
            umo: 会话标识
            user_msg: 用户消息
            assistant_msg: Agent 回复
        """
        try:
            # 检查记忆管理器是否初始化
            if not self.plugin.memory_manager:
                logger.debug("[Agent] 长期记忆系统未初始化，跳过记录")
                return

            # 获取绑定信息（需要语雀 ID 作为用户标识）
            # 从 umo 中提取平台 ID（需要从 event 获取，这里暂时跳过）
            # 实际记录会在 handle_message 中完成，那里可以获取 event
            logger.debug("[Agent] 长期记忆记录需在 handle_message 中完成")

        except Exception as e:
            logger.warning(f"[Agent] 长期记忆记录失败: {e}")

    def _build_system_prompt(self, user_context: dict, conversation_history: list = None) -> str:
        """构建系统提示词

        Args:
            user_context: 用户上下文
            conversation_history: 对话历史

        Returns:
            完整的系统提示词
        """
        prompt = DEFAULT_SYSTEM_PROMPT

        # 添加对话历史
        if conversation_history:
            history_text = "\n".join([
                f"{'用户' if msg['role'] == 'user' else 'NovaBot'}: {msg.get('content', '')}"
                for msg in conversation_history
                if msg.get('role') and msg.get('content')
            ])
            if history_text:
                prompt += f"""

【最近的对话】（请继续这段对话，不要重复回答）
{history_text}

【当前用户的新消息】
（你需要回复这条新消息）"""

        # 如果用户已绑定且有画像，添加个性化信息
        if user_context.get("bound") and user_context.get("profile"):
            profile = user_context["profile"]
            yuque_name = user_context.get("yuque_name", "未知用户")

            interests = profile.get("interests", [])
            level = profile.get("level", "unknown")
            tags = profile.get("tags", [])
            summary = profile.get("summary", "")

            interests_str = ", ".join(interests) if interests else "暂无"
            tags_str = ", ".join(tags) if tags else "暂无"

            prompt += f"""

【当前用户信息】
- 已绑定账号: {yuque_name}
- 兴趣领域: {interests_str}
- 整体水平: {level}
- 标签: {tags_str}
- 概括: {summary}

请根据用户画像提供个性化建议。如果用户问的问题与其兴趣领域相关，可以提供更深入的建议。"""

        elif user_context.get("bound"):
            # 已绑定但无画像
            prompt += f"""

【当前用户信息】
- 已绑定账号: {user_context.get('yuque_name', '未知')}
- 暂无画像数据，建议用户使用 /profile refresh 生成画像"""

        else:
            # 未绑定
            prompt += """

【当前用户信息】
- 未绑定语雀账号
- 如果用户需要个性化服务，引导使用 /bind 绑定账号"""

        # 注入用户偏好
        preferences = user_context.get("preferences", {})
        if preferences and user_context.get("bound"):
            name = preferences.get("name", "")
            tone = preferences.get("tone", "温和")
            style = preferences.get("style", "详细")
            formality = preferences.get("formality", "轻松")

            # 语气指南
            tone_guide = {
                "温和": "语气温和，像朋友一样交流",
                "活泼": "语气活泼，可以用一些语气词如'呢'、'呀'等",
                "严肃": "语气专业、严谨",
                "幽默": "可以适当开玩笑，但不要过度",
            }

            style_guide = {
                "简洁": "回复简洁，不要展开太多",
                "详细": "回复详细，可以展开说明",
            }

            formality_guide = {
                "轻松": "使用口语化表达",
                "正式": "使用书面语，保持礼貌",
            }

            # 构建偏好提示
            pref_lines = []
            if name:
                pref_lines.append(f"- 称呼：{name}（请用这个称呼叫用户）")
            pref_lines.append(f"- 语气：{tone} - {tone_guide.get(tone, '')}")
            pref_lines.append(f"- 回复风格：{style} - {style_guide.get(style, '')}")
            pref_lines.append(f"- 正式程度：{formality} - {formality_guide.get(formality, '')}")

            prompt += f"""

【用户偏好设置】
{chr(10).join(pref_lines)}

请根据用户偏好调整你的回复方式。"""

        # 注入联网搜索提示
        if self.plugin.config.get("web_search_enabled", False):
            prompt += """

【联网搜索】
你具备联网搜索能力，可以在以下情况使用：
- 知识库中没有相关信息时
- 用户询问最新新闻、实时信息时
- 需要验证或补充知识库内容时

使用示例：
- 用户: "最近有什么 AI 大新闻" → 调用 web_search 搜索
- 用户: "Python 3.13 有什么新特性" → 先搜索知识库，找不到再用 web_search
- 用户: "今天天气怎么样" → 调用 web_search 搜索

注意：
- 优先使用知识库内容回答，联网搜索作为补充
- 使用联网搜索后，标注「来源：网络搜索」"""

        return prompt

    def _get_tools(self):
        """获取工具列表

        Returns:
            工具实例列表
        """
        from .tools import ALL_TOOLS

        tools = []
        for ToolClass in ALL_TOOLS:
            tool = ToolClass()
            tool.plugin = self.plugin
            tools.append(tool)

        return tools