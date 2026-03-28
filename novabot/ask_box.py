"""
NovaBot 匿名提问箱模块
支持匿名提问、管理员回答、问题管理
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from astrbot.api import logger


class AskBoxManager:
    """匿名提问箱管理器

    功能：
    - 匿名提交问题
    - 管理员查看待回答/已回答问题
    - 管理员回答问题

    数据存储：data/plugin_data/astrbot_plugin_yuque/ask_box.json
    """

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.questions_file = self.data_dir / "ask_box.json"
        self._data: dict = {}
        self._loaded = False

    def _ensure_loaded(self):
        """确保数据已加载"""
        if self._loaded:
            return

        if self.questions_file.exists():
            try:
                with open(self.questions_file, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except Exception as e:
                logger.warning(f"[AskBox] 加载数据失败: {e}")
                self._data = {"questions": [], "next_id": 1}
        else:
            self._data = {"questions": [], "next_id": 1}

        self._loaded = True

    def _save(self):
        """保存数据到文件"""
        try:
            with open(self.questions_file, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[AskBox] 保存数据失败: {e}")

    def submit_question(self, content: str, umo: str) -> tuple[int, str]:
        """提交匿名问题

        Args:
            content: 问题内容
            umo: 提交来源（群/私聊标识，用于后续通知）

        Returns:
            (question_id, success_message)
        """
        self._ensure_loaded()

        question_id = self._data["next_id"]
        question = {
            "id": question_id,
            "content": content.strip(),
            "created_at": datetime.now().isoformat(),
            "status": "pending",  # pending / answered
            "umo": umo,
            "answered_at": None,
            "answer": None,
            "answerer_id": None,
        }

        self._data["questions"].append(question)
        self._data["next_id"] = question_id + 1
        self._save()

        logger.info(f"[AskBox] 新问题 #{question_id}: {content[:50]}...")
        return question_id, f"问题已提交 (ID: {question_id})"

    def get_pending_questions(self) -> list[dict]:
        """获取待回答问题列表

        Returns:
            待回答问题列表，按提交时间倒序
        """
        self._ensure_loaded()

        pending = [
            q for q in self._data["questions"]
            if q.get("status") == "pending"
        ]
        # 按提交时间倒序
        pending.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return pending

    def get_answered_questions(self) -> list[dict]:
        """获取已回答问题列表

        Returns:
            已回答问题列表，按回答时间倒序
        """
        self._ensure_loaded()

        answered = [
            q for q in self._data["questions"]
            if q.get("status") == "answered"
        ]
        # 按回答时间倒序
        answered.sort(key=lambda x: x.get("answered_at", "") or "", reverse=True)
        return answered

    def get_all_questions(self, limit: int = 50) -> list[dict]:
        """获取所有问题（用于管理）

        Args:
            limit: 最大返回数量

        Returns:
            问题列表，按时间倒序
        """
        self._ensure_loaded()

        questions = self._data["questions"].copy()
        questions.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return questions[:limit]

    def answer_question(
        self,
        question_id: int,
        answer: str,
        answerer_id: str
    ) -> tuple[bool, str, Optional[dict]]:
        """回答问题

        Args:
            question_id: 问题 ID
            answer: 回答内容
            answerer_id: 回答者 ID

        Returns:
            (success, message, question_info) - question_info 包含 umo 和 content，用于发送通知
        """
        self._ensure_loaded()

        for question in self._data["questions"]:
            if question.get("id") == question_id:
                if question.get("status") == "answered":
                    return False, f"问题 #{question_id} 已被回答", None

                question["status"] = "answered"
                question["answer"] = answer.strip()
                question["answerer_id"] = answerer_id
                question["answered_at"] = datetime.now().isoformat()
                self._save()

                logger.info(f"[AskBox] 问题 #{question_id} 已回答")
                # 返回通知所需的信息
                info = {
                    "umo": question.get("umo"),
                    "content": question.get("content"),
                }
                return True, f"问题 #{question_id} 已回答", info

        return False, f"未找到问题 #{question_id}", None

    def get_question_by_id(self, question_id: int) -> Optional[dict]:
        """根据 ID 获取问题

        Args:
            question_id: 问题 ID

        Returns:
            问题详情，未找到返回 None
        """
        self._ensure_loaded()

        for question in self._data["questions"]:
            if question.get("id") == question_id:
                return question
        return None

    def delete_question(self, question_id: int) -> tuple[bool, str]:
        """删除问题（管理员功能）

        Args:
            question_id: 问题 ID

        Returns:
            (success, message)
        """
        self._ensure_loaded()

        original_count = len(self._data["questions"])
        self._data["questions"] = [
            q for q in self._data["questions"]
            if q.get("id") != question_id
        ]

        if len(self._data["questions"]) < original_count:
            self._save()
            logger.info(f"[AskBox] 问题 #{question_id} 已删除")
            return True, f"问题 #{question_id} 已删除"

        return False, f"未找到问题 #{question_id}"

    def get_stats(self) -> dict:
        """获取统计信息

        Returns:
            {
                "total": int,
                "pending": int,
                "answered": int,
            }
        """
        self._ensure_loaded()

        questions = self._data["questions"]
        return {
            "total": len(questions),
            "pending": sum(1 for q in questions if q.get("status") == "pending"),
            "answered": sum(1 for q in questions if q.get("status") == "answered"),
        }

    def format_questions_list(
        self,
        questions: list[dict],
        show_answered: bool = False
    ) -> str:
        """格式化问题列表

        Args:
            questions: 问题列表
            show_answered: 是否显示已回答的问题

        Returns:
            格式化的文本
        """
        if not questions:
            if show_answered:
                return "暂无已回答问题"
            return "暂无待回答问题"

        lines = []

        for q in questions:
            qid = q.get("id", "?")
            content = q.get("content", "")
            created = q.get("created_at", "")
            status = q.get("status", "pending")

            # 状态图标
            status_icon = "✅" if status == "answered" else "⏳"

            # 截取问题内容（最多 50 字符）
            display_content = content[:50] + "..." if len(content) > 50 else content

            lines.append(f"#{qid} {status_icon} {display_content}")

            # 显示时间
            if created:
                try:
                    dt = datetime.fromisoformat(created)
                    lines.append(f"   提交于 {dt.strftime('%Y-%m-%d %H:%M')}")
                except (ValueError, TypeError):
                    pass

            # 显示回答（如果是已回答状态）
            if status == "answered" and show_answered:
                answer = q.get("answer", "")
                if answer:
                    display_answer = answer[:50] + "..." if len(answer) > 50 else answer
                    lines.append(f"   回答: {display_answer}")

            lines.append("")

        return "\n".join(lines)

    def format_question_detail(self, question: dict) -> str:
        """格式化单个问题详情

        Args:
            question: 问题详情

        Returns:
            格式化的文本
        """
        qid = question.get("id", "?")
        content = question.get("content", "")
        created = question.get("created_at", "")
        status = question.get("status", "pending")

        lines = [f"📝 问题 #{qid}", ""]
        lines.append(f"内容: {content}")
        lines.append(f"状态: {'已回答' if status == 'answered' else '待回答'}")

        if created:
            try:
                dt = datetime.fromisoformat(created)
                lines.append(f"提交于: {dt.strftime('%Y-%m-%d %H:%M')}")
            except (ValueError, TypeError):
                pass

        if status == "answered":
            answer = question.get("answer", "")
            answered_at = question.get("answered_at", "")
            lines.append("")
            lines.append(f"💬 回答: {answer}")

            if answered_at:
                try:
                    dt = datetime.fromisoformat(answered_at)
                    lines.append(f"回答于: {dt.strftime('%Y-%m-%d %H:%M')}")
                except (ValueError, TypeError):
                    pass

        return "\n".join(lines)