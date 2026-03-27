"""
NovaBot 数据存储模块
管理绑定关系、团队成员、同步状态、用户画像
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from astrbot.api import logger


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
        self._cache_dirty = {"bindings": False, "members": False}

    # ========== 绑定关系 ==========

    def load_bindings(self) -> dict:
        if self._bindings_cache is not None and not self._cache_dirty.get("bindings", False):
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
        self.bindings_file.write_text(
            json.dumps(bindings, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        self._bindings_cache = bindings

    def get_binding(self, platform_id: str) -> Optional[dict]:
        bindings = self.load_bindings()
        return bindings.get(platform_id)

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
        if self._members_cache is not None and not self._cache_dirty.get("members", False):
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
        profile["updated_at"] = datetime.now().isoformat()
        profile_file.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    # ========== 文档查询 ==========

    def get_docs_by_author(self, author_name: str = None, yuque_id: int = None) -> list[dict]:
        """获取指定作者的文档列表

        优先通过 yuque_id (creator_id) 精确匹配，回退到 author_name 匹配。

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

        for md_file in self.docs_dir.rglob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")
                metadata, body = YuqueClient.parse_frontmatter(content)

                # 优先通过 creator_id 精确匹配
                if yuque_id:
                    doc_creator_id = metadata.get("creator_id")
                    if doc_creator_id and str(doc_creator_id) == str(yuque_id):
                        pass  # 匹配成功
                    elif doc_creator_id:
                        continue  # 有 creator_id 但不匹配，跳过
                    elif author_name:
                        # 没有 creator_id，回退到 author 匹配
                        doc_author = metadata.get("author", "")
                        if doc_author != author_name:
                            continue
                    else:
                        continue
                elif author_name:
                    # 只通过 author_name 匹配
                    doc_author = metadata.get("author", "")
                    if doc_author != author_name:
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

        return docs