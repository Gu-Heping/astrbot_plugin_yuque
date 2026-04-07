"""
协作网络管理器
追踪成员之间的协作关系，优化伙伴推荐
"""

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any, Set, Tuple
from uuid import uuid4

from astrbot.api import logger


class CollaborationNetwork:
    """协作网络管理器"""

    # 协作关系来源类型
    SOURCE_TYPES = {
        "same_repo": "同一知识库贡献者",
        "question_answer": "问答关系",
        "same_group": "同一兴趣组",
        "explicit": "明确协作记录",
    }

    # 协作强度权重
    STRENGTH_WEIGHTS = {
        "same_repo": 0.3,  # 同一知识库贡献者
        "question_answer": 0.5,  # 问答关系（更强的连接）
        "same_group": 0.2,  # 同一兴趣组
        "explicit": 0.8,  # 明确协作记录
    }

    def __init__(self, data_dir: Path):
        """初始化协作网络管理器

        Args:
            data_dir: 数据存储目录
        """
        self.network_file = data_dir / "collaboration_network.json"
        self.network_file.parent.mkdir(parents=True, exist_ok=True)

        # 并发锁
        self._lock = threading.Lock()

        # 缓存
        self._network: Optional[dict] = None

        logger.info("[CollaborationNetwork] 初始化完成")

    def _load_network(self) -> dict:
        """加载协作网络数据"""
        if self._network is not None:
            return self._network

        with self._lock:
            if self.network_file.exists():
                try:
                    self._network = json.loads(
                        self.network_file.read_text(encoding="utf-8")
                    )
                    if not isinstance(self._network, dict):
                        self._network = self._create_empty_network()
                except json.JSONDecodeError as e:
                    logger.warning(f"[Collaboration] 网络文件损坏: {e}")
                    self._network = self._create_empty_network()
            else:
                self._network = self._create_empty_network()

        return self._network

    def _save_network(self, network: dict):
        """保存协作网络数据"""
        with self._lock:
            self.network_file.write_text(
                json.dumps(network, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        self._network = network

    def _create_empty_network(self) -> dict:
        """创建空的协作网络结构"""
        return {
            "collaborations": [],  # 协作关系列表
            "member_stats": {},  # 成员统计
            "repo_contributors": {},  # 知识库贡献者映射
            "group_members": {},  # 兴趣组成员映射
            "updated_at": None,
        }

    def add_collaboration(
        self,
        member_a: str,
        member_b: str,
        source_type: str,
        context: str = "",
        strength: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """添加协作关系

        Args:
            member_a: 成员 A
            member_b: 成员 B
            source_type: 关系来源类型
            context: 协作上下文（知识库名、问题标题等）
            strength: 协作强度（可选，默认根据 source_type 计算）
            metadata: 附加元数据

        Returns:
            关系 ID
        """
        if not member_a or not member_b or member_a == member_b:
            logger.warning("[Collaboration] 无效参数，跳过记录")
            return ""

        # 验证来源类型
        if source_type not in self.SOURCE_TYPES:
            logger.warning(f"[Collaboration] 未知来源类型: {source_type}")
            source_type = "explicit"

        network = self._load_network()

        # 检查是否已存在相同关系
        for collab in network["collaborations"]:
            existing_pair = {collab["member_a"], collab["member_b"]}
            if {member_a, member_b} == existing_pair:
                # 已存在，更新强度
                old_strength = collab.get("strength", 0)
                new_strength = strength or self.STRENGTH_WEIGHTS.get(source_type, 0.3)
                collab["strength"] = min(old_strength + new_strength, 1.0)
                collab["last_updated"] = datetime.now().isoformat()
                collab["interactions"] = collab.get("interactions", 1) + 1

                # 更新成员统计
                self._update_member_stats(network, member_a, member_b)

                self._save_network(network)
                logger.debug(
                    f"[Collaboration] 更新关系: {member_a}-{member_b}, "
                    f"strength={collab['strength']}"
                )
                return collab["collaboration_id"]

        # 新建关系
        collaboration_id = str(uuid4())[:8]
        strength = strength or self.STRENGTH_WEIGHTS.get(source_type, 0.3)

        collaboration = {
            "collaboration_id": collaboration_id,
            "member_a": member_a,
            "member_b": member_b,
            "source_type": source_type,
            "source_name": self.SOURCE_TYPES.get(source_type, "未知来源"),
            "context": context,
            "strength": strength,
            "interactions": 1,
            "created_at": datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat(),
            "metadata": metadata or {},
        }

        network["collaborations"].append(collaboration)
        network["updated_at"] = datetime.now().isoformat()

        # 更新成员统计
        self._update_member_stats(network, member_a, member_b)

        self._save_network(network)

        logger.debug(
            f"[Collaboration] 新建关系: {member_a}-{member_b}, "
            f"source={source_type}, strength={strength}"
        )
        return collaboration_id

    def _update_member_stats(self, network: dict, member_a: str, member_b: str):
        """更新成员协作统计"""
        for member in [member_a, member_b]:
            stats = network["member_stats"].setdefault(member, {
                "collaborator_count": 0,
                "total_interactions": 0,
                "avg_strength": 0,
            })
            stats["collaborator_count"] = self._count_collaborators(network, member)
            stats["total_interactions"] = sum(
                c.get("interactions", 1)
                for c in network["collaborations"]
                if c["member_a"] == member or c["member_b"] == member
            )

    def _count_collaborators(self, network: dict, member_id: str) -> int:
        """计算成员的协作伙伴数量"""
        collaborators: Set[str] = set()
        for collab in network["collaborations"]:
            if collab["member_a"] == member_id:
                collaborators.add(collab["member_b"])
            elif collab["member_b"] == member_id:
                collaborators.add(collab["member_a"])
        return len(collaborators)

    def get_collaborators(self, member_id: str, min_strength: float = 0.0) -> List[dict]:
        """获取成员的协作伙伴

        Args:
            member_id: 成员标识
            min_strength: 最小强度过滤（可选）

        Returns:
            协作伙伴列表 [{member_id, strength, source_type, context}]
        """
        network = self._load_network()
        collaborators = []

        for collab in network["collaborations"]:
            if collab["member_a"] == member_id:
                partner = collab["member_b"]
            elif collab["member_b"] == member_id:
                partner = collab["member_a"]
            else:
                continue

            strength = collab.get("strength", 0)
            if strength < min_strength:
                continue

            collaborators.append({
                "member_id": partner,
                "strength": strength,
                "source_type": collab.get("source_type"),
                "source_name": collab.get("source_name"),
                "context": collab.get("context"),
                "interactions": collab.get("interactions", 1),
                "last_updated": collab.get("last_updated"),
            })

        # 按强度排序
        collaborators.sort(key=lambda x: x.get("strength", 0), reverse=True)
        return collaborators

    def get_collaboration_strength(self, member_a: str, member_b: str) -> float:
        """计算两个成员的协作强度

        Args:
            member_a: 成员 A
            member_b: 成员 B

        Returns:
            协作强度（0-1）
        """
        network = self._load_network()

        for collab in network["collaborations"]:
            existing_pair = {collab["member_a"], collab["member_b"]}
            if {member_a, member_b} == existing_pair:
                return collab.get("strength", 0)

        return 0.0

    def find_potential_collaborators(
        self,
        member_id: str,
        topic: str = "",
        exclude_existing: bool = True,
        trajectory_manager=None,
        doc_index=None,
    ) -> List[dict]:
        """推荐潜在协作伙伴

        Args:
            member_id: 成员标识
            topic: 匹配主题（可选）
            exclude_existing: 是否排除已有协作关系
            trajectory_manager: 成员轨迹管理器（可选，用于主题搜索）
            doc_index: 文档索引（可选，用于主题搜索）

        Returns:
            推荐列表 [{member_id, match_reason, match_score}]
        """
        network = self._load_network()

        # 获取已有协作伙伴
        existing_collaborators: Set[str] = set()
        if exclude_existing:
            for collab in network["collaborations"]:
                if collab["member_a"] == member_id:
                    existing_collaborators.add(collab["member_b"])
                elif collab["member_b"] == member_id:
                    existing_collaborators.add(collab["member_a"])

        # 收集所有其他成员
        all_members: Set[str] = set()
        for collab in network["collaborations"]:
            all_members.add(collab["member_a"])
            all_members.add(collab["member_b"])

        all_members.discard(member_id)

        # 排除已有协作伙伴
        potential = all_members - existing_collaborators

        # 如果有主题，优先从轨迹和文档索引搜索相关成员
        topic_experts: Dict[str, dict] = {}  # {member_id: {score, reasons, docs}}
        if topic:
            # 分词，支持多关键词搜索
            import re
            keywords = re.findall(r'[\u4e00-\u9fa5]+|[a-zA-Z]+', topic)
            keywords = [k for k in keywords if len(k) >= 2][:5]  # 最多5个关键词

            # 1. 从轨迹搜索（在该领域有活动的成员）
            if trajectory_manager:
                for kw in keywords:
                    try:
                        topic_results = trajectory_manager.search_by_topic(kw, days=90)
                        for result in topic_results:
                            expert_id = result.get("member_id", "")
                            if expert_id and expert_id != member_id and expert_id not in existing_collaborators:
                                match_count = result.get("match_count", 0)
                                events = result.get("matching_events", [])

                                if expert_id not in topic_experts:
                                    topic_experts[expert_id] = {
                                        "score": 0,
                                        "reasons": [],
                                        "docs": [],
                                        "activities": 0,
                                    }

                                # 每次匹配 +0.4 分（高权重）
                                topic_experts[expert_id]["score"] += match_count * 0.4
                                topic_experts[expert_id]["activities"] += match_count

                                # 记录具体活动
                                for evt in events[:2]:
                                    evt_title = evt.get("title", "")[:20]
                                    if evt_title and evt_title not in topic_experts[expert_id]["reasons"]:
                                        topic_experts[expert_id]["reasons"].append(evt_title)

                                potential.add(expert_id)
                    except Exception as e:
                        logger.debug(f"[Collaboration] 轨迹搜索失败: {e}")

            # 2. 从文档索引搜索（写过相关文档的成员）
            if doc_index:
                for kw in keywords:
                    try:
                        # 按标题搜索
                        docs = doc_index.search(title=kw, limit=20)
                        for doc in docs:
                            author = doc.get("creator_id") or doc.get("author")
                            doc_title = doc.get("title", "")
                            if author:
                                author_id = str(author)
                                if author_id != member_id and author_id not in existing_collaborators:
                                    if author_id not in topic_experts:
                                        topic_experts[author_id] = {
                                            "score": 0,
                                            "reasons": [],
                                            "docs": [],
                                            "activities": 0,
                                        }

                                    # 每篇文档 +0.5 分（高权重）
                                    topic_experts[author_id]["score"] += 0.5
                                    if doc_title and doc_title not in topic_experts[author_id]["docs"]:
                                        topic_experts[author_id]["docs"].append(doc_title[:30])

                                    potential.add(author_id)
                    except Exception as e:
                        logger.debug(f"[Collaboration] 文档搜索失败: {e}")

        recommendations = []
        for potential_member in potential:
            # 计算推荐分数
            score = 0.0
            reasons = []
            is_topic_match = False

            # 主题相关性（优先级最高，权重最大）
            if topic and potential_member in topic_experts:
                expert_info = topic_experts[potential_member]
                topic_score = expert_info["score"]

                if topic_score > 0:
                    score = topic_score  # 直接使用主题分数作为主要分数
                    is_topic_match = True

                    # 生成原因
                    docs = expert_info.get("docs", [])
                    activities = expert_info.get("activities", 0)

                    if docs:
                        reasons.append(f"写过「{docs[0]}」等相关文档")
                        if len(docs) > 1:
                            reasons.append(f"共 {len(docs)} 篇相关文档")
                    if activities > 0:
                        reasons.append(f"{activities} 次相关活动")

            # 间接连接（通过共同协作伙伴）- 权重降低
            common_collaborators = self._find_common_collaborators(
                network, member_id, potential_member
            )
            if common_collaborators:
                # 只有在没有主题匹配时，才把共同伙伴作为主要推荐原因
                if not is_topic_match:
                    score += len(common_collaborators) * 0.1  # 降低权重
                    reasons.append(f"与 {len(common_collaborators)} 个共同伙伴协作")

            # 同一兴趣组
            if self._in_same_group(network, member_id, potential_member):
                if not is_topic_match:
                    score += 0.1
                    if not reasons:
                        reasons.append("同一兴趣组")

            # 同一知识库贡献者
            if self._in_same_repo(network, member_id, potential_member):
                if not is_topic_match:
                    score += 0.05
                    if not reasons:
                        reasons.append("同一知识库贡献者")

            if score > 0:
                recommendations.append({
                    "member_id": potential_member,
                    "match_score": min(score, 1.0),
                    "match_reasons": reasons,
                    "is_topic_match": is_topic_match,
                })

        # 排序：主题匹配优先，然后按分数
        recommendations.sort(
            key=lambda x: (x.get("is_topic_match", False), x.get("match_score", 0)),
            reverse=True
        )
        return recommendations[:10]  # 返回前 10 个推荐

    def _find_common_collaborators(
        self, network: dict, member_a: str, member_b: str
    ) -> Set[str]:
        """找出两个成员的共同协作伙伴"""
        collaborators_a: Set[str] = set()
        collaborators_b: Set[str] = set()

        for collab in network["collaborations"]:
            if collab["member_a"] == member_a:
                collaborators_a.add(collab["member_b"])
            elif collab["member_b"] == member_a:
                collaborators_a.add(collab["member_a"])

            if collab["member_a"] == member_b:
                collaborators_b.add(collab["member_b"])
            elif collab["member_b"] == member_b:
                collaborators_b.add(collab["member_a"])

        return collaborators_a & collaborators_b

    def _in_same_group(self, network: dict, member_a: str, member_b: str) -> bool:
        """检查两个成员是否在同一兴趣组"""
        group_members = network.get("group_members", {})
        for group_name, members in group_members.items():
            if member_a in members and member_b in members:
                return True
        return False

    def _in_same_repo(self, network: dict, member_a: str, member_b: str) -> bool:
        """检查两个成员是否是同一知识库贡献者"""
        repo_contributors = network.get("repo_contributors", {})
        for repo_name, contributors in repo_contributors.items():
            if member_a in contributors and member_b in contributors:
                return True
        return False

    def add_repo_contributors(self, repo_name: str, contributors: List[str]):
        """记录知识库贡献者

        Args:
            repo_name: 知识库名称
            contributors: 贡献者列表
        """
        network = self._load_network()
        network.setdefault("repo_contributors", {})[repo_name] = contributors

        # 自动创建同一知识库贡献者的协作关系
        for i, member_a in enumerate(contributors):
            for member_b in contributors[i + 1:]:
                self.add_collaboration(
                    member_a, member_b,
                    "same_repo",
                    context=repo_name,
                )

        self._save_network(network)
        logger.info(
            f"[Collaboration] 记录知识库贡献者: {repo_name}, "
            f"count={len(contributors)}"
        )

    def add_group_members(self, group_name: str, members: List[str]):
        """记录兴趣组成员

        Args:
            group_name: 兴趣组名称
            members: 成员列表
        """
        network = self._load_network()
        network.setdefault("group_members", {})[group_name] = members

        # 自动创建同一兴趣组的协作关系
        for i, member_a in enumerate(members):
            for member_b in members[i + 1:]:
                self.add_collaboration(
                    member_a, member_b,
                    "same_group",
                    context=group_name,
                )

        self._save_network(network)
        logger.info(
            f"[Collaboration] 记录兴趣组成员: {group_name}, "
            f"count={len(members)}"
        )

    def get_member_stats(self, member_id: str) -> dict:
        """获取成员协作统计"""
        network = self._load_network()
        return network.get("member_stats", {}).get(member_id, {
            "collaborator_count": 0,
            "total_interactions": 0,
            "avg_strength": 0,
        })

    def get_network_stats(self) -> dict:
        """获取协作网络整体统计"""
        network = self._load_network()
        total_collabs = len(network.get("collaborations", []))
        total_members = len(network.get("member_stats", {}))

        # 计算平均强度
        strengths = [
            c.get("strength", 0) for c in network.get("collaborations", [])
        ]
        avg_strength = sum(strengths) / len(strengths) if strengths else 0

        return {
            "total_collaborations": total_collabs,
            "total_members": total_members,
            "avg_strength": avg_strength,
            "updated_at": network.get("updated_at"),
        }

    def clear_network(self) -> bool:
        """清除协作网络数据"""
        with self._lock:
            if self.network_file.exists():
                self.network_file.unlink()
        self._network = None
        logger.info("[Collaboration] 清除协作网络数据")
        return True