"""
NovaBot 伙伴推荐模块
基于用户画像匹配学习伙伴和导师
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

from astrbot.api import logger


class PartnerMatcher:
    """伙伴匹配器"""

    # 匹配权重
    WEIGHT_INTEREST = 0.40      # 兴趣重合
    WEIGHT_LEVEL = 0.30         # 水平相近
    WEIGHT_ACTIVITY = 0.15      # 活跃时间
    WEIGHT_STYLE = 0.15         # 协作风格

    # 水平等级映射（用于比较）
    LEVEL_ORDER = {"beginner": 1, "intermediate": 2, "advanced": 3}

    def __init__(self, storage):
        """初始化匹配器

        Args:
            storage: Storage 实例，用于访问用户画像和成员数据
        """
        self.storage = storage

    def get_all_profiles(self) -> dict:
        """获取所有用户的画像"""
        profiles = {}
        profiles_dir = self.storage.profiles_dir

        if not profiles_dir.exists():
            return profiles

        for profile_file in profiles_dir.glob("*.json"):
            try:
                yuque_id = profile_file.stem
                profile = self.storage.load_profile(int(yuque_id))
                if profile:
                    profiles[yuque_id] = profile
            except Exception as e:
                logger.warning(f"读取画像失败 {profile_file}: {e}")

        return profiles

    def get_member_info(self, yuque_id: int) -> Optional[dict]:
        """获取成员信息（名称、login）"""
        members = self.storage.load_members()
        return members.get(str(yuque_id))

    def calculate_interest_overlap(self, user_interests: list, other_interests: list) -> float:
        """计算兴趣重合度"""
        if not user_interests or not other_interests:
            return 0.0

        user_set = set(i.lower() for i in user_interests)
        other_set = set(i.lower() for i in other_interests)

        intersection = user_set & other_set
        union = user_set | other_set

        return len(intersection) / len(union) if union else 0.0

    def calculate_level_similarity(self, user_level: str, other_level: str) -> float:
        """计算水平相似度（越接近越高）"""
        user_order = self.LEVEL_ORDER.get(user_level, 1)
        other_order = self.LEVEL_ORDER.get(other_level, 1)

        diff = abs(user_order - other_order)
        # 差值为 0 -> 1.0, 差值为 1 -> 0.6, 差值为 2 -> 0.2
        return max(0, 1.0 - diff * 0.4)

    def calculate_level_gap(self, user_level: str, other_level: str) -> int:
        """计算水平差距（正数表示 other 更高）"""
        user_order = self.LEVEL_ORDER.get(user_level, 1)
        other_order = self.LEVEL_ORDER.get(other_level, 1)
        return other_order - user_order

    def get_activity_score(self, yuque_id: int) -> float:
        """获取活跃度分数（基于文档更新时间）"""
        # 从画像中获取文档统计
        profile = self.storage.load_profile(yuque_id)
        if not profile:
            return 0.5

        stats = profile.get("stats", {})
        docs_count = stats.get("docs_count", 0)

        # 简单映射：文档数越多，活跃度越高
        if docs_count >= 20:
            return 1.0
        elif docs_count >= 10:
            return 0.8
        elif docs_count >= 5:
            return 0.6
        elif docs_count >= 2:
            return 0.4
        else:
            return 0.2

    def find_partners(
        self,
        user_yuque_id: int,
        topic: Optional[str] = None,
        limit: int = 5
    ) -> list[dict]:
        """找学习伙伴（水平相近）

        Args:
            user_yuque_id: 当前用户的语雀 ID
            topic: 可选的主题过滤
            limit: 返回数量限制

        Returns:
            匹配的伙伴列表，按匹配度排序
        """
        user_profile = self.storage.load_profile(user_yuque_id)
        if not user_profile:
            return []

        user_p = user_profile.get("profile", {})
        user_interests = user_p.get("interests", [])
        user_level = user_p.get("level", "beginner")

        # 如果指定了主题，添加到兴趣列表
        if topic and topic.lower() not in [i.lower() for i in user_interests]:
            user_interests = user_interests + [topic]

        all_profiles = self.get_all_profiles()
        matches = []

        for yuque_id_str, profile in all_profiles.items():
            yuque_id = int(yuque_id_str)

            # 跳过自己
            if yuque_id == user_yuque_id:
                continue

            other_p = profile.get("profile", {})
            other_interests = other_p.get("interests", [])
            other_level = other_p.get("level", "beginner")

            # 如果指定了主题，检查对方是否有相关兴趣
            if topic:
                topic_lower = topic.lower()
                other_interests_lower = [i.lower() for i in other_interests]
                if topic_lower not in other_interests_lower:
                    continue

            # 计算各项分数
            interest_score = self.calculate_interest_overlap(user_interests, other_interests)
            level_score = self.calculate_level_similarity(user_level, other_level)
            activity_score = self.get_activity_score(yuque_id)

            # 综合分数
            total_score = (
                interest_score * self.WEIGHT_INTEREST +
                level_score * self.WEIGHT_LEVEL +
                activity_score * self.WEIGHT_ACTIVITY
            )

            if total_score > 0:
                member_info = self.get_member_info(yuque_id) or {}
                matches.append({
                    "yuque_id": yuque_id,
                    "name": member_info.get("name", ""),
                    "login": member_info.get("login", ""),
                    "score": total_score,
                    "interests": other_interests,
                    "level": other_level,
                    "common_interests": list(set(i.lower() for i in user_interests) &
                                            set(i.lower() for i in other_interests)),
                })

        # 按分数排序
        matches.sort(key=lambda x: x["score"], reverse=True)
        return matches[:limit]

    def find_mentors(
        self,
        user_yuque_id: int,
        topic: Optional[str] = None,
        limit: int = 3
    ) -> list[dict]:
        """找导师（水平更高）

        Args:
            user_yuque_id: 当前用户的语雀 ID
            topic: 可选的主题过滤
            limit: 返回数量限制

        Returns:
            匹配的导师列表，按水平差距排序
        """
        user_profile = self.storage.load_profile(user_yuque_id)
        if not user_profile:
            return []

        user_p = user_profile.get("profile", {})
        user_interests = user_p.get("interests", [])
        user_level = user_p.get("level", "beginner")
        user_skills = user_p.get("skills", {})

        # 如果指定了主题，获取用户在该主题的水平
        if topic:
            topic_lower = topic.lower()
            for skill, level in user_skills.items():
                if skill.lower() == topic_lower:
                    user_level = level
                    break

        all_profiles = self.get_all_profiles()
        mentors = []

        for yuque_id_str, profile in all_profiles.items():
            yuque_id = int(yuque_id_str)

            # 跳过自己
            if yuque_id == user_yuque_id:
                continue

            other_p = profile.get("profile", {})
            other_interests = other_p.get("interests", [])
            other_skills = other_p.get("skills", {})

            # 检查对方在目标主题的水平
            topic_level = None
            if topic:
                topic_lower = topic.lower()
                for skill, level in other_skills.items():
                    if skill.lower() == topic_lower:
                        topic_level = level
                        break

                # 没有相关技能，跳过
                if not topic_level:
                    continue
            else:
                # 没有指定主题，使用整体水平
                topic_level = other_p.get("level", "beginner")

            # 计算水平差距
            level_gap = self.calculate_level_gap(user_level, topic_level)

            # 导师必须水平更高
            if level_gap <= 0:
                continue

            # 获取相关文档（优先通过 yuque_id 匹配）
            member_info = self.get_member_info(yuque_id) or {}
            docs = self.storage.get_docs_by_author(member_info.get("name", ""), yuque_id)
            related_docs = []
            if topic:
                for doc in docs:
                    if topic.lower() in doc.get("title", "").lower():
                        related_docs.append(doc["title"])

            mentors.append({
                "yuque_id": yuque_id,
                "name": member_info.get("name", ""),
                "login": member_info.get("login", ""),
                "level_gap": level_gap,
                "topic_level": topic_level,
                "interests": other_interests,
                "related_docs": related_docs[:3],
            })

        # 按水平差距排序（差距大的排前面，但不能太大）
        mentors.sort(key=lambda x: abs(x["level_gap"] - 1), reverse=False)
        return mentors[:limit]

    def request_match(
        self,
        from_platform_id: str,
        to_yuque_id: int,
        message: Optional[str] = None
    ) -> dict:
        """请求匹配（人工确认）

        注意：此功能当前只返回匹配状态，未实现推送通知。
        后续可通过 context.send_message() 实现消息推送。

        Args:
            from_platform_id: 发起者的平台 ID
            to_yuque_id: 目标用户的语雀 ID
            message: 可选的留言

        Returns:
            匹配请求状态
        """
        # 获取发起者信息
        from_binding = self.storage.get_binding(from_platform_id)
        if not from_binding:
            return {"status": "error", "message": "你还未绑定账号"}

        # 获取目标用户信息
        to_member = self.get_member_info(to_yuque_id)
        if not to_member:
            return {"status": "error", "message": "目标用户不存在"}

        # 当前只返回状态，后续可扩展为推送消息
        # 需要注入 context 才能使用 context.send_message() 推送通知

        return {
            "status": "pending",
            "from": {
                "yuque_id": from_binding.get("yuque_id"),
                "name": from_binding.get("yuque_name"),
                "login": from_binding.get("yuque_login"),
            },
            "to": {
                "yuque_id": to_yuque_id,
                "name": to_member.get("name"),
                "login": to_member.get("login"),
            },
            "message": message,
            "created_at": datetime.now().isoformat(),
        }


def format_partner_result(partners: list[dict], mentors: list[dict], topic: Optional[str] = None, storage=None) -> str:
    """格式化伙伴推荐结果

    Args:
        partners: 学习伙伴列表
        mentors: 导师列表
        topic: 搜索主题
        storage: Storage 实例（可选，用于获取更多信息）

    Returns:
        格式化的文本结果
    """
    lines = ["👥 学习伙伴推荐"]

    if topic:
        lines[0] += f"：{topic}"

    lines.append("")

    level_map = {"beginner": "入门", "intermediate": "进阶", "advanced": "高级"}

    # 学习伙伴
    if partners:
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("📚 学习伙伴（进度相近）")
        lines.append("")

        for p in partners:
            name = p.get("name", "")
            login = p.get("login", "")
            lines.append(f"👤 {name}" + (f" (@{login})" if login else ""))

            if p.get("common_interests"):
                lines.append(f"   💡 共同兴趣：{', '.join(p['common_interests'][:3])}")
            lines.append(f"   📊 水平：{level_map.get(p['level'], p['level'])}")

            # 获取对方相关文档
            if storage and login:
                try:
                    docs = storage.get_docs_by_author(name, None)
                    if docs:
                        lines.append(f"   📄 已写 {len(docs)} 篇文档")
                except Exception:
                    pass

            # 语雀链接
            if login:
                lines.append(f"   🔗 https://www.yuque.com/{login}")

            lines.append("")
    else:
        lines.append("暂无匹配的学习伙伴")
        lines.append("")

    # 导师
    if mentors:
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("🎯 导师（经验更丰富）")
        lines.append("")

        for m in mentors:
            name = m.get("name", "")
            login = m.get("login", "")
            lines.append(f"👤 {name}" + (f" (@{login})" if login else ""))

            lines.append(f"   📊 水平：{level_map.get(m['topic_level'], m['topic_level'])}")

            if m.get("related_docs"):
                lines.append(f"   📄 相关文档：{m['related_docs'][0][:25]}...")
                if len(m['related_docs']) > 1:
                    lines.append(f"      （共 {len(m['related_docs'])} 篇）")

            # 语雀链接
            if login:
                lines.append(f"   🔗 https://www.yuque.com/{login}")

            lines.append("")

    else:
        if partners:  # 只有没找到导师时才提示
            lines.append("暂无匹配的导师")
            lines.append("")

    lines.append("💡 提示：可以通过语雀主页私信联系，或在群里 @对方 讨论")
    return "\n".join(lines)