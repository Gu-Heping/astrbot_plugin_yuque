"""
NovaBot 数据存储模块
管理绑定关系、团队成员、同步状态、用户画像
"""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from astrbot.api import logger


def _now_iso() -> str:
    """获取当前时间的 ISO 格式字符串（使用 UTC 时区）"""
    return datetime.now(timezone.utc).isoformat()


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

        # 文档目录
        self.docs_dir = self.data_dir / "yuque_docs"
        self.docs_dir.mkdir(parents=True, exist_ok=True)

        # 缓存
        self._bindings_cache: Optional[dict] = None
        self._members_cache: Optional[dict] = None

        # 并发锁（保护文件写入）
        self._bindings_lock = threading.Lock()
        self._members_lock = threading.Lock()

    def invalidate_cache(self, cache_type: str = "all"):
        """清除缓存，下次读取时重新加载文件

        Args:
            cache_type: "bindings", "members", 或 "all"
        """
        if cache_type in ("bindings", "all"):
            self._bindings_cache = None
        if cache_type in ("members", "all"):
            self._members_cache = None

    # ========== 绑定关系 ==========

    def load_bindings(self) -> dict:
        with self._bindings_lock:
            if self._bindings_cache is not None:
                return self._bindings_cache
            if self.bindings_file.exists():
                try:
                    self._bindings_cache = json.loads(self.bindings_file.read_text(encoding="utf-8"))
                    return self._bindings_cache
                except json.JSONDecodeError as e:
                    logger.warning(f"绑定文件损坏，已重置: {e}")
                    self._bindings_cache = {}
                    return {}
            self._bindings_cache = {}
            return {}

    def save_bindings(self, bindings: dict):
        with self._bindings_lock:
            self.bindings_file.write_text(
                json.dumps(bindings, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            self._bindings_cache = bindings

    def get_binding(self, platform_id: str) -> Optional[dict]:
        bindings = self.load_bindings()
        return bindings.get(platform_id)

    def get_binding_by_yuque_id(self, yuque_id: str) -> Optional[dict]:
        """通过语雀 ID 获取绑定信息

        Args:
            yuque_id: 语雀用户 ID

        Returns:
            绑定信息字典，包含 platform_id, yuque_name 等
        """
        bindings = self.load_bindings()
        for platform_id, binding in bindings.items():
            # 统一转为字符串比较，避免类型不一致
            if str(binding.get("yuque_id", "")) == str(yuque_id):
                return {"platform_id": platform_id, **binding}
        return None

    def get_all_bindings(self) -> list:
        """获取所有绑定记录

        Returns:
            绑定列表 [{platform_id, yuque_id, yuque_name, ...}, ...]
        """
        bindings = self.load_bindings()
        result = []
        for platform_id, binding in bindings.items():
            result.append({
                "platform_id": platform_id,
                **binding,
            })
        return result

    def add_binding(self, platform_id: str, yuque_info: dict):
        bindings = self.load_bindings()
        bindings[platform_id] = {
            **yuque_info,
            "bind_time": _now_iso(),
        }
        self.save_bindings(bindings)

    def remove_binding(self, platform_id: str):
        bindings = self.load_bindings()
        if platform_id in bindings:
            del bindings[platform_id]
            self.save_bindings(bindings)

    # ========== 团队成员 ==========

    def load_members(self) -> dict:
        with self._members_lock:
            if self._members_cache is not None:
                return self._members_cache
            if self.members_file.exists():
                try:
                    self._members_cache = json.loads(self.members_file.read_text(encoding="utf-8"))
                    return self._members_cache
                except json.JSONDecodeError as e:
                    logger.warning(f"成员文件损坏，已重置: {e}")
                    self._members_cache = {}
                    return {}
            self._members_cache = {}
            return {}

    def save_members(self, members: dict):
        with self._members_lock:
            self.members_file.write_text(
                json.dumps(members, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            self._members_cache = members

    def find_member_by_id(self, user_id: str) -> Optional[dict]:
        """通过用户 ID 精确查找团队成员

        Args:
            user_id: 语雀用户 ID

        Returns:
            成员信息字典，未找到返回 None
        """
        members = self.load_members()
        info = members.get(str(user_id))
        if info:
            return {"id": int(user_id), **info}
        return None

    def find_member_by_name(self, name_or_login: str) -> Optional[dict]:
        """通过名称或 login 查找团队成员

        Args:
            name_or_login: 用户名或登录名

        Returns:
            成员信息字典，未找到返回 None
        """
        # 空值检查和长度限制
        if not name_or_login or not name_or_login.strip():
            return None

        # 限制输入长度，防止过长字符串
        if len(name_or_login) > 100:
            name_or_login = name_or_login[:100]

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
            try:
                return json.loads(self.sync_state_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                logger.warning(f"同步状态文件损坏，已重置: {e}")
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
            try:
                return json.loads(profile_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                logger.warning(f"用户画像文件损坏 ({yuque_id}): {e}")
                return None
        return None

    def save_profile(self, yuque_id: int, profile: dict):
        profile_file = self.profiles_dir / f"{yuque_id}.json"
        profile["updated_at"] = _now_iso()
        profile_file.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    # ========== 用户偏好（人格管理） ==========

    # 偏好验证常量
    VALID_TONES = ["温和", "活泼", "严肃", "幽默"]
    VALID_STYLES = ["简洁", "详细"]
    VALID_FORMALITIES = ["轻松", "正式"]
    VALID_PREFERENCE_KEYS = ["name", "tone", "style", "formality"]

    # 默认偏好
    DEFAULT_PREFERENCES = {
        "name": "",           # 称呼偏好
        "tone": "温和",       # 语气
        "style": "详细",      # 回复风格
        "formality": "轻松",  # 正式程度
    }

    def load_preferences(self, yuque_id) -> dict:
        """加载用户偏好

        Args:
            yuque_id: 语雀用户 ID

        Returns:
            用户偏好字典，未设置时返回默认值
        """
        profile = self.load_profile(yuque_id)
        if not profile:
            return self.DEFAULT_PREFERENCES.copy()

        # 合并用户偏好和默认值
        prefs = profile.get("preferences", {})
        result = self.DEFAULT_PREFERENCES.copy()
        result.update(prefs)
        return result

    def save_preferences(self, yuque_id, preferences: dict):
        """保存用户偏好

        Args:
            yuque_id: 语雀用户 ID
            preferences: 用户偏好字典
        """
        profile = self.load_profile(yuque_id) or {}
        profile["preferences"] = preferences
        self.save_profile(yuque_id, profile)

    def update_preference(self, yuque_id, key: str, value: str) -> bool:
        """更新单个偏好

        Args:
            yuque_id: 语雀用户 ID
            key: 偏好键名
            value: 偏好值

        Returns:
            是否更新成功
        """
        # 验证偏好键
        if key not in self.VALID_PREFERENCE_KEYS:
            return False

        # 验证偏好值
        valid_values = {
            "tone": self.VALID_TONES,
            "style": self.VALID_STYLES,
            "formality": self.VALID_FORMALITIES,
            "name": None,  # name 可以是任意字符串
        }

        if key != "name" and value not in valid_values.get(key, []):
            return False

        # name 字段特殊处理：限制长度和特殊字符
        if key == "name":
            if not isinstance(value, str):
                return False
            # 限制长度（防止 Prompt Injection）
            if len(value) > 50:
                value = value[:50]
            # 过滤可能导致问题的特殊字符
            value = value.replace("\n", " ").replace("\r", " ").strip()

        # 更新偏好
        prefs = self.load_preferences(yuque_id)
        prefs[key] = value
        self.save_preferences(yuque_id, prefs)
        return True

    # ========== 文档查询 ==========

    def get_docs_by_author(self, author_name: str = None, yuque_id: int = None) -> list[dict]:
        """获取指定作者的文档列表

        匹配逻辑：
        1. 如果提供 yuque_id，优先通过 creator_id 精确匹配
        2. 如果 creator_id 不匹配或不存在，回退到 author 名字匹配
        3. 如果只提供 author_name，只用名字匹配

        Args:
            author_name: 作者名（团队成员真实姓名）
            yuque_id: 语雀用户 ID（优先使用）

        Returns:
            文档列表，每个文档包含 id, title, slug, description, author, book_name, content
        """
        if not author_name and not yuque_id:
            return []

        # 延迟导入避免循环依赖
        from .yuque_client import YuqueClient

        docs = []
        seen_ids = set()  # 去重
        match_by_id = 0
        match_by_name = 0

        for md_file in self.docs_dir.rglob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")
                metadata, body = YuqueClient.parse_frontmatter(content)

                matched = False

                # 1. 尝试通过 creator_id 精确匹配
                if yuque_id:
                    doc_creator_id = metadata.get("creator_id")
                    if doc_creator_id and str(doc_creator_id) == str(yuque_id):
                        matched = True
                        match_by_id += 1

                # 2. 如果 creator_id 不匹配，回退到 author 名字匹配
                if not matched and author_name:
                    doc_author = metadata.get("author", "")
                    if doc_author == author_name:
                        matched = True
                        match_by_name += 1

                if not matched:
                    continue

                # 按 yuque_id 去重（可能同一文档在多个位置）
                doc_id = metadata.get("id")
                if doc_id and doc_id in seen_ids:
                    continue
                if doc_id:
                    seen_ids.add(doc_id)

                docs.append({
                    "id": doc_id,
                    "title": metadata.get("title", ""),
                    "slug": metadata.get("slug", ""),
                    "description": metadata.get("description", ""),
                    "author": metadata.get("author", ""),
                    "book_name": metadata.get("book_name", ""),
                    "content": body,
                    "creator_id": metadata.get("creator_id"),
                })
            except Exception as e:
                logger.warning(f"读取文档失败 {md_file}: {e}")

        logger.info(f"[Storage] get_docs_by_author: yuque_id={yuque_id}, author_name={author_name}, "
                    f"match_by_id={match_by_id}, match_by_name={match_by_name}, total={len(docs)}")
        return docs