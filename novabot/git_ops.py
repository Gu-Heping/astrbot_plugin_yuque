"""
NovaBot Git 操作封装
用于 Webhook 触发时自动 commit 文档变更
"""

import subprocess
from pathlib import Path
from typing import List, Optional

from astrbot.api import logger


class GitOps:
    """Git 操作封装"""

    def __init__(self, repo_dir: Path):
        self.repo_dir = Path(repo_dir)

    def is_git_repo(self) -> bool:
        """检查是否是 Git 仓库"""
        return (self.repo_dir / ".git").exists()

    def ensure_git(self) -> bool:
        """确保 Git 仓库已初始化

        Returns:
            是否成功（True 表示仓库已存在或初始化成功）
        """
        if self.is_git_repo():
            return True

        try:
            result = subprocess.run(
                ["git", "init"],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                logger.info(f"[GitOps] Git 仓库初始化成功: {self.repo_dir}")
                return True
            else:
                logger.warning(f"[GitOps] Git init 失败: {result.stderr}")
                return False
        except FileNotFoundError:
            logger.error("[GitOps] Git 未安装，请先安装 Git")
            return False
        except Exception as e:
            logger.error(f"[GitOps] Git init 异常: {e}")
            return False

    def add_commit(self, files: List[str], message: str) -> Optional[str]:
        """添加文件并提交

        Args:
            files: 相对路径文件列表
            message: 提交消息

        Returns:
            成功时返回 commit hash（短格式），失败返回 None
        """
        if not self.is_git_repo():
            logger.warning("[GitOps] 非 Git 仓库，跳过 commit")
            return None

        if not files:
            return None

        try:
            # git add
            add_result = subprocess.run(
                ["git", "add", *files],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
            )
            if add_result.returncode != 0:
                logger.warning(f"[GitOps] git add 失败: {add_result.stderr}")
                return None

            # git commit
            commit_result = subprocess.run(
                ["git", "commit", "-m", message],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
            )

            # commit 可能因为没有变更而失败（nothing to commit）
            if commit_result.returncode != 0:
                if "nothing to commit" in commit_result.stdout:
                    logger.debug("[GitOps] 没有变更需要提交")
                else:
                    logger.warning(f"[GitOps] git commit 失败: {commit_result.stderr}")
                return None

            # 获取 commit hash
            hash_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
            )
            if hash_result.returncode == 0:
                commit_hash = hash_result.stdout.strip()[:7]
                logger.info(f"[GitOps] 提交成功: {commit_hash} - {message}")
                return commit_hash

        except Exception as e:
            logger.error(f"[GitOps] commit 异常: {e}")

        return None

    def get_diff(self, commit: str, file_path: str) -> str:
        """获取指定 commit 与当前文件的 diff

        Args:
            commit: commit hash
            file_path: 相对路径文件

        Returns:
            diff 文本
        """
        if not self.is_git_repo():
            return ""

        try:
            result = subprocess.run(
                ["git", "diff", commit, "--", file_path],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
            )
            return result.stdout
        except Exception as e:
            logger.error(f"[GitOps] get_diff 异常: {e}")
            return ""

    def get_last_commit_hash(self) -> Optional[str]:
        """获取最新 commit hash"""
        if not self.is_git_repo():
            return None

        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return result.stdout.strip()[:7]
        except Exception:
            pass
        return None

    def push(self) -> bool:
        """推送到远程仓库

        Returns:
            是否成功
        """
        if not self.is_git_repo():
            return False

        try:
            result = subprocess.run(
                ["git", "push"],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                logger.info("[GitOps] 推送成功")
                return True
            else:
                logger.warning(f"[GitOps] push 失败: {result.stderr}")
                return False
        except Exception as e:
            logger.error(f"[GitOps] push 异常: {e}")
            return False