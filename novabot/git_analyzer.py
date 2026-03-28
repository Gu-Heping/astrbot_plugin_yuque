"""
NovaBot Git 历史分析器
用于提取 commit 历史中的贡献数据，支持周报、活跃度分析等功能
"""

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from astrbot.api import logger

from .git_ops import GitOps


class GitAnalyzer:
    """Git 历史分析器

    提取 commit 历史中的统计数据，用于：
    - 贡献排行榜
    - 活跃度分析
    - 热门文档识别
    - 周报数据生成
    """

    def __init__(self, docs_dir: Path):
        self.docs_dir = Path(docs_dir)
        self.git_ops = GitOps(self.docs_dir)

    def _run_git(self, args: list[str]) -> str:
        """执行 git 命令并返回输出"""
        if not self.git_ops.is_git_repo():
            return ""

        import subprocess

        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=self.docs_dir,
                capture_output=True,
                text=True,
            )
            return result.stdout
        except Exception as e:
            logger.error(f"[GitAnalyzer] git 命令执行失败: {e}")
            return ""

    def _get_date_range(self, days: int) -> tuple[str, str]:
        """获取日期范围（用于 git log --since/--until）

        Returns:
            (since_date, until_date) 格式为 YYYY-MM-DD
        """
        today = datetime.now()
        since = (today - timedelta(days=days)).strftime("%Y-%m-%d")
        until = today.strftime("%Y-%m-%d")
        return since, until

    def get_author_stats(self, author: str, days: int = 30) -> dict:
        """获取作者的贡献统计

        Args:
            author: 作者名称
            days: 统计天数

        Returns:
            {
                "author": str,
                "commits": int,
                "additions": int,
                "deletions": int,
                "files_changed": set,
                "active_days": int,
                "first_commit": str,
                "last_commit": str,
            }
        """
        result = {
            "author": author,
            "commits": 0,
            "additions": 0,
            "deletions": 0,
            "files_changed": set(),
            "active_days": 0,
            "first_commit": "",
            "last_commit": "",
        }

        if not self.git_ops.is_git_repo():
            return result

        since, _ = self._get_date_range(days)

        # 获取提交次数 - 使用 git log 替代 shortlog
        log_output = self._run_git([
            "log", "--author", author, "--since", since,
            "--format=%H"
        ])
        commits = [line.strip() for line in log_output.split("\n") if line.strip()]
        result["commits"] = len(commits)

        if result["commits"] == 0:
            return result

        # 获取变更统计
        log_output = self._run_git([
            "log", "--author", author,
            "--since", since,
            "--numstat", "--format=%ad",
            "--date=short"
        ])

        active_dates = set()
        first_commit_date = None
        last_commit_date = None

        for line in log_output.split("\n"):
            line = line.strip()
            if not line:
                continue

            # 日期行
            if re.match(r'^\d{4}-\d{2}-\d{2}$', line):
                active_dates.add(line)
                if first_commit_date is None or line < first_commit_date:
                    first_commit_date = line
                if last_commit_date is None or line > last_commit_date:
                    last_commit_date = line
                continue

            # 变更统计行: additions\tdeletions\tfilepath
            parts = line.split("\t")
            if len(parts) >= 3:
                try:
                    add = int(parts[0]) if parts[0] != "-" else 0
                    delete = int(parts[1]) if parts[1] != "-" else 0
                    result["additions"] += add
                    result["deletions"] += delete
                    result["files_changed"].add(parts[2])
                except ValueError:
                    pass

        result["active_days"] = len(active_dates)
        result["first_commit"] = first_commit_date or ""
        result["last_commit"] = last_commit_date or ""
        result["files_changed"] = list(result["files_changed"])

        return result

    def get_weekly_activity(self) -> dict:
        """获取本周活跃度

        Returns:
            {
                "total_commits": int,
                "active_authors": list[dict],
                "hot_files": list[dict],
                "period": str,  # 如 "2026-03-20 ~ 2026-03-27"
            }
        """
        result = {
            "total_commits": 0,
            "active_authors": [],
            "hot_files": [],
            "period": "",
        }

        if not self.git_ops.is_git_repo():
            return result

        # 本周一到今天
        today = datetime.now()
        monday = today - timedelta(days=today.weekday())
        since = monday.strftime("%Y-%m-%d")
        until = today.strftime("%Y-%m-%d")
        result["period"] = f"{since} ~ {until}"

        # 总提交数
        count_output = self._run_git([
            "rev-list", "--count", "HEAD", "--since", since
        ])
        result["total_commits"] = int(count_output.strip()) if count_output.strip() else 0

        if result["total_commits"] == 0:
            return result

        # 活跃作者排行 - 使用 git log 替代 shortlog（更可靠）
        log_output = self._run_git([
            "log", "--since", since,
            "--format=%an"
        ])

        author_counts: dict[str, int] = {}
        for line in log_output.split("\n"):
            author = line.strip()
            if author:
                author_counts[author] = author_counts.get(author, 0) + 1

        result["active_authors"] = [
            {"author": author, "commits": count}
            for author, count in sorted(author_counts.items(), key=lambda x: x[1], reverse=True)
        ]

        # 热门文件（按修改次数）
        log_output = self._run_git([
            "log", "--since", since,
            "--name-only", "--format="
        ])

        file_counts: dict[str, int] = {}
        for line in log_output.split("\n"):
            line = line.strip()
            if line and line.endswith(".md"):
                file_counts[line] = file_counts.get(line, 0) + 1

        # 排序取 TOP 10
        sorted_files = sorted(file_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        result["hot_files"] = [
            {"file": f, "changes": c} for f, c in sorted_files
        ]

        return result

    def get_file_contributors(self, file_path: str) -> list[dict]:
        """获取文档的所有贡献者

        Args:
            file_path: 相对路径文件

        Returns:
            [{"author": str, "commits": int, "last_modified": str}, ...]
        """
        result = []

        if not self.git_ops.is_git_repo():
            return result

        # 安全检查路径
        try:
            from .git_ops import _sanitize_git_path
            safe_path = _sanitize_git_path(file_path)
        except ValueError as e:
            logger.warning(f"[GitAnalyzer] 不安全的文件路径: {e}")
            return result

        log_output = self._run_git([
            "log", "--format=%an|%ad", "--date=short", "--", safe_path
        ])

        author_data: dict[str, dict] = {}
        for line in log_output.split("\n"):
            line = line.strip()
            if not line or "|" not in line:
                continue

            parts = line.split("|")
            if len(parts) >= 2:
                author = parts[0].strip()
                date = parts[1].strip()

                if author not in author_data:
                    author_data[author] = {"commits": 0, "last_modified": ""}
                author_data[author]["commits"] += 1
                if date > author_data[author]["last_modified"]:
                    author_data[author]["last_modified"] = date

        # 按提交数排序
        result = [
            {"author": a, **d} for a, d in author_data.items()
        ]
        result.sort(key=lambda x: x["commits"], reverse=True)

        return result

    def get_trend(self, pattern: str, days: int = 30) -> dict:
        """获取某类文档的提交趋势

        Args:
            pattern: 文件路径模式（如 "AI" 匹配包含 AI 的路径）
            days: 统计天数

        Returns:
            {
                "pattern": str,
                "total_commits": int,
                "files_matched": int,
                "authors": list[str],
                "daily_commits": list[dict],  # [{date, count}, ...]
            }
        """
        result = {
            "pattern": pattern,
            "total_commits": 0,
            "files_matched": 0,
            "authors": [],
            "daily_commits": [],
        }

        if not self.git_ops.is_git_repo():
            return result

        since, _ = self._get_date_range(days)

        # 获取匹配文件的提交
        log_output = self._run_git([
            "log", "--since", since,
            "--name-only", "--format=%ad|%an",
            "--date=short", "--", f"*{pattern}*"
        ])

        files = set()
        authors = set()
        daily: dict[str, int] = {}
        total = 0

        current_date = ""
        current_author = ""

        for line in log_output.split("\n"):
            line = line.strip()
            if not line:
                continue

            # 日期和作者行
            if "|" in line:
                parts = line.split("|")
                if len(parts) >= 2:
                    current_date = parts[0].strip()
                    current_author = parts[1].strip()
                    authors.add(current_author)
                continue

            # 文件行
            if line.endswith(".md"):
                files.add(line)
                total += 1
                if current_date:
                    daily[current_date] = daily.get(current_date, 0) + 1

        result["total_commits"] = total
        result["files_matched"] = len(files)
        result["authors"] = list(authors)

        # 按日期排序
        result["daily_commits"] = [
            {"date": d, "count": c}
            for d, c in sorted(daily.items())
        ]

        return result

    def get_contribution_ranking(self, days: int = 30) -> list[dict]:
        """获取贡献排行榜

        Args:
            days: 统计天数

        Returns:
            [{"author": str, "commits": int, "additions": int, "deletions": int}, ...]
        """
        result = []

        if not self.git_ops.is_git_repo():
            return result

        since, _ = self._get_date_range(days)

        # 先获取所有作者 - 使用 git log 替代 shortlog
        log_output = self._run_git([
            "log", "--since", since,
            "--format=%an"
        ])

        author_counts: dict[str, int] = {}
        for line in log_output.split("\n"):
            author = line.strip()
            if author:
                author_counts[author] = author_counts.get(author, 0) + 1

        authors = list(author_counts.keys())

        # 获取每个作者的统计
        for author in authors:
            stats = self.get_author_stats(author, days)
            result.append({
                "author": author,
                "commits": stats["commits"],
                "additions": stats["additions"],
                "deletions": stats["deletions"],
                "files_changed": len(stats["files_changed"]),
                "active_days": stats["active_days"],
            })

        # 按提交数排序
        result.sort(key=lambda x: x["commits"], reverse=True)

        return result

    def get_recent_commits(self, limit: int = 20) -> list[dict]:
        """获取最近的提交列表

        Args:
            limit: 最大数量

        Returns:
            [{"hash": str, "author": str, "date": str, "message": str, "files": int}, ...]
        """
        result = []

        if not self.git_ops.is_git_repo():
            return result

        log_output = self._run_git([
            "log", f"-{limit}",
            "--format=%h|%an|%ad|%s",
            "--date=short",
            "--stat"
        ])

        current_commit = None

        for line in log_output.split("\n"):
            line = line.strip()
            if not line:
                continue

            # 提交信息行
            if "|" in line and line.count("|") >= 3:
                if current_commit:
                    result.append(current_commit)

                parts = line.split("|")
                current_commit = {
                    "hash": parts[0],
                    "author": parts[1],
                    "date": parts[2],
                    "message": "|".join(parts[3:]),
                    "files": 0,
                }
                continue

            # 统计行: X files changed, Y insertions(+), Z deletions(-)
            if "files changed" in line and current_commit:
                match = re.search(r'(\d+)\s+files? changed', line)
                if match:
                    current_commit["files"] = int(match.group(1))

        if current_commit:
            result.append(current_commit)

        return result