"""
NovaBot 知识问答模块
实名提问 + 多人回答 + 点赞机制
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from astrbot.api import logger


class AskBoxManager:
    """知识问答管理器

    功能：
    - 实名提问：存储提问者身份
    - 绑定用户可回答问题
    - 所有人可查看问题和回答
    - 点赞机制：为回答点赞
    - 回答通知：通知提问者

    数据存储：data/plugin_data/astrbot_plugin_yuque/ask_box.json
    """

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.questions_file = self.data_dir / "ask_box.json"
        self._data: dict = {}
        self._loaded = False

    # 内容长度限制
    MAX_QUESTION_LENGTH = 500
    MAX_ANSWER_LENGTH = 1000

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
                self._data = {"questions": [], "next_question_id": 1, "next_answer_id": 1}
        else:
            self._data = {"questions": [], "next_question_id": 1, "next_answer_id": 1}

        self._loaded = True

    def _save(self):
        """保存数据到文件"""
        try:
            with open(self.questions_file, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[AskBox] 保存数据失败: {e}")

    def submit_question(
        self,
        content: str,
        umo: str,
        sender_id: str,
        sender_name: str
    ) -> tuple[int, str]:
        """提交问题（实名）

        Args:
            content: 问题内容
            umo: 提交来源（用于通知）
            sender_id: 提问者 ID
            sender_name: 提问者名称

        Returns:
            (question_id, success_message)

        Raises:
            ValueError: 内容超长
        """
        # 长度校验
        content = content.strip()
        if not content:
            raise ValueError("问题内容不能为空")
        if len(content) > self.MAX_QUESTION_LENGTH:
            raise ValueError(f"问题内容过长（最多 {self.MAX_QUESTION_LENGTH} 字）")

        self._ensure_loaded()

        question_id = self._data["next_question_id"]
        question = {
            "id": question_id,
            "content": content.strip(),
            "created_at": datetime.now().isoformat(),
            "umo": umo,
            "sender_id": sender_id,
            "sender_name": sender_name,
            "answers": [],  # 多回答列表
            "answer_count": 0,
        }

        self._data["questions"].append(question)
        self._data["next_question_id"] = question_id + 1
        self._save()

        logger.info(f"[AskBox] 新问题 #{question_id} by {sender_name}: {content[:50]}...")
        return question_id, f"问题已提交 (ID: {question_id})"

    def get_all_questions(self, limit: int = 50) -> list[dict]:
        """获取所有问题列表

        Args:
            limit: 最大返回数量

        Returns:
            问题列表，按时间倒序
        """
        self._ensure_loaded()

        questions = self._data["questions"].copy()
        questions.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return questions[:limit]

    def get_question_by_id(self, question_id: int) -> Optional[dict]:
        """根据 ID 获取问题详情

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

    def get_user_questions(self, sender_id: str) -> list[dict]:
        """获取用户自己提问的问题

        Args:
            sender_id: 用户 ID

        Returns:
            问题列表
        """
        self._ensure_loaded()

        questions = [
            q for q in self._data["questions"]
            if q.get("sender_id") == sender_id
        ]
        questions.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return questions

    def submit_answer(
        self,
        question_id: int,
        content: str,
        answerer_id: str,
        answerer_name: str,
        answerer_yuque_id: int
    ) -> tuple[bool, str, Optional[dict]]:
        """回答问题

        Args:
            question_id: 问题 ID
            content: 回答内容
            answerer_id: 回答者平台 ID
            answerer_name: 回答者名称
            answerer_yuque_id: 回答者语雀 ID（用于验证绑定）

        Returns:
            (success, message, notify_info) - notify_info 用于通知提问者
        """
        # 长度校验
        if len(content) > self.MAX_ANSWER_LENGTH:
            return False, f"回答内容过长（最多 {self.MAX_ANSWER_LENGTH} 字）", None

        self._ensure_loaded()

        for question in self._data["questions"]:
            if question.get("id") == question_id:
                # 禁止回答自己的问题
                if answerer_id == question.get("sender_id"):
                    return False, "不能回答自己提出的问题", None

                # 禁止重复回答同一问题
                existing = [a for a in question.get("answers", [])
                            if a.get("answerer_id") == answerer_id]
                if existing:
                    return False, "你已经回答过这个问题了", None

                answer_id = self._data["next_answer_id"]
                answer = {
                    "id": answer_id,
                    "content": content.strip(),
                    "answerer_id": answerer_id,
                    "answerer_name": answerer_name,
                    "answerer_yuque_id": answerer_yuque_id,
                    "created_at": datetime.now().isoformat(),
                    "likes": 0,
                    "liked_by": [],  # 记录点赞者，防止重复
                }

                question["answers"].append(answer)
                question["answer_count"] = len(question["answers"])
                self._data["next_answer_id"] = answer_id + 1
                self._save()

                logger.info(f"[AskBox] 问题 #{question_id} 新回答 by {answerer_name}")

                # 返回通知信息
                notify_info = {
                    "umo": question.get("umo"),
                    "question_content": question.get("content"),
                    "answerer_name": answerer_name,
                }
                return True, f"回答已提交 (ID: {answer_id})", notify_info

        return False, f"未找到问题 #{question_id}", None

    def like_answer(
        self,
        question_id: int,
        answer_id: int,
        user_id: str
    ) -> tuple[bool, str]:
        """为回答点赞

        Args:
            question_id: 问题 ID
            answer_id: 回答 ID
            user_id: 点赞者 ID

        Returns:
            (success, message)
        """
        self._ensure_loaded()

        for question in self._data["questions"]:
            if question.get("id") == question_id:
                for answer in question.get("answers", []):
                    if answer.get("id") == answer_id:
                        # 禁止给自己的回答点赞
                        if user_id == answer.get("answerer_id"):
                            return False, "不能给自己的回答点赞"

                        # 检查是否已点赞
                        if user_id in answer.get("liked_by", []):
                            # 取消点赞
                            answer["liked_by"] = [u for u in answer["liked_by"] if u != user_id]
                            answer["likes"] = len(answer["liked_by"])
                            self._save()
                            return True, "已取消点赞"

                        # 新点赞
                        answer["liked_by"].append(user_id)
                        answer["likes"] = len(answer["liked_by"])
                        self._save()
                        return True, f"点赞成功 (+{answer['likes']})"

                return False, "未找到该回答"

        return False, "未找到该问题"

    def delete_question(self, question_id: int, requester_id: str) -> tuple[bool, str]:
        """删除问题（仅提问者或管理员）

        Args:
            question_id: 问题 ID
            requester_id: 请求者 ID

        Returns:
            (success, message)
        """
        self._ensure_loaded()

        for i, question in enumerate(self._data["questions"]):
            if question.get("id") == question_id:
                # 仅提问者可删除自己的问题
                if question.get("sender_id") == requester_id:
                    self._data["questions"].pop(i)
                    self._save()
                    logger.info(f"[AskBox] 问题 #{question_id} 已删除 by {requester_id}")
                    return True, f"问题 #{question_id} 已删除"
                else:
                    return False, "只能删除自己提出的问题"

        return False, f"未找到问题 #{question_id}"

    def clear_all(self) -> tuple[bool, str]:
        """清空所有数据（管理员功能）

        Returns:
            (success, message)
        """
        self._ensure_loaded()

        old_count = len(self._data.get("questions", []))
        self._data = {
            "questions": [],
            "next_question_id": 1,
            "next_answer_id": 1,
        }
        self._save()
        self._loaded = False  # 强制下次重新加载

        logger.info(f"[AskBox] 已清空 {old_count} 条数据")
        return True, f"已清空 {old_count} 条数据"

    def get_stats(self) -> dict:
        """获取统计信息"""
        self._ensure_loaded()

        questions = self._data["questions"]
        total_answers = sum(q.get("answer_count", 0) for q in questions)
        total_likes = sum(
            sum(a.get("likes", 0) for a in q.get("answers", []))
            for q in questions
        )

        return {
            "total_questions": len(questions),
            "total_answers": total_answers,
            "total_likes": total_likes,
        }

    def format_questions_list(self, questions: list[dict]) -> str:
        """格式化问题列表（公开）

        Args:
            questions: 问题列表

        Returns:
            格式化的文本
        """
        if not questions:
            return "暂无问题"

        lines = []
        for q in questions:
            qid = q.get("id", "?")
            content = q.get("content", "")
            sender_name = q.get("sender_name", "未知")
            answer_count = q.get("answer_count", 0)
            created = q.get("created_at", "")

            # 截取问题内容
            display = content[:40] + "..." if len(content) > 40 else content

            # 状态图标
            status = "✅" if answer_count > 0 else "⏳"

            lines.append(f"#{qid} {status} [{sender_name}] {display}")
            if answer_count > 0:
                lines.append(f"   {answer_count} 个回答")

        return "\n".join(lines)

    def format_question_detail(self, question: dict) -> str:
        """格式化问题详情（含所有回答）

        Args:
            question: 问题详情

        Returns:
            格式化的文本
        """
        qid = question.get("id", "?")
        content = question.get("content", "")
        sender_name = question.get("sender_name", "未知")
        created = question.get("created_at", "")
        answers = question.get("answers", [])

        lines = [f"📝 问题 #{qid}", ""]
        lines.append(f"提问者: {sender_name}")
        lines.append(f"内容: {content}")

        if created:
            try:
                dt = datetime.fromisoformat(created)
                lines.append(f"时间: {dt.strftime('%Y-%m-%d %H:%M')}")
            except (ValueError, TypeError):
                pass

        lines.append("")

        if answers:
            # 按点赞数排序
            sorted_answers = sorted(answers, key=lambda x: x.get("likes", 0), reverse=True)
            lines.append(f"💬 回答 ({len(answers)} 条)")
            lines.append("─" * 20)

            for a in sorted_answers:
                aid = a.get("id", "?")
                answerer = a.get("answerer_name", "未知")
                ans_content = a.get("content", "")
                likes = a.get("likes", 0)
                ans_created = a.get("created_at", "")

                lines.append(f"[#{aid}] {answerer}")
                lines.append(f"{ans_content}")
                lines.append(f"👍 {likes} 赞")

                if ans_created:
                    try:
                        dt = datetime.fromisoformat(ans_created)
                        lines.append(f"   {dt.strftime('%Y-%m-%d %H:%M')}")
                    except (ValueError, TypeError):
                        pass
                lines.append("")
        else:
            lines.append("暂无回答，使用 /ask answer <ID> <内容> 来回答")

        return "\n".join(lines)