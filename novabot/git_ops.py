"""
NovaBot Git 操作封装
用于 Webhook 触发时自动 commit 文档变更
"""

import re
import subprocess
from pathlib import Path
from typing import List, Optional

from astrbot.api import logger


def _sanitize_git_path(path: str) -> str:
    """验证 Git 路径安全性

    防止命令注入和路径遍历攻击

    注意：我们使用 subprocess 列表参数，不会经过 shell 解析，
    所以 &, |, ; 等字符在文件名中是安全的。

    Args:
        path: 文件路径

    Returns:
        验证后的路径

    Raises:
        ValueError: 如果路径不安全
    """
    if not path:
        raise ValueError("Empty path")

    # 检查路径遍历
    if '..' in path:
        raise ValueError(f"Path traversal detected: {path}")

    # 检查绝对路径
    if path.startswith('/') or (len(path) > 1 and path[1] == ':'):
        raise ValueError(f"Absolute path not allowed: {path}")

    # 检查以 - 开头（可能被解释为选项）
    if path.startswith('-'):
        raise ValueError(f"Path cannot start with '-': {path}")

    # 只检查真正危险的字符（换行符可能导致命令分割）
    # 注意：在 subprocess 列表参数中，&, |, ; 等是安全的
    dangerous_chars = ['\n', '\r', '\x00']
    for char in dangerous_chars:
        if char in path:
            raise ValueError(f"Dangerous character in path: {path}")

    return path


def _sanitize_commit_message(message: str) -> str:
    """验证 commit message 安全性

    Args:
        message: commit message

    Returns:
        验证后的 message

    Raises:
        ValueError: 如果 message 不安全
    """
    if not message:
        return "update"

    # 移除危险字符
    dangerous_chars = ['`', '$', '\n', '\r']
    sanitized = message
    for char in dangerous_chars:
        sanitized = sanitized.replace(char, '')

    # 限制长度
    if len(sanitized) > 200:
        sanitized = sanitized[:200] + '...'

    return sanitized or "update"


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
            logger.warning(f"[GitOps] Git init 失败: {result.stderr}")
            return False
        except FileNotFoundError:
            logger.error("[GitOps] Git 未安装，请先安装 Git")
            return False
        except Exception as e:
            logger.error(f"[GitOps] Git init 异常: {e}")
            return False

    def has_user_identity(self) -> bool:
        """检查当前仓库是否已配置 Git 用户身份"""
        try:
            name_result = subprocess.run(
                ["git", "config", "user.name"],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
            )
            email_result = subprocess.run(
                ["git", "config", "user.email"],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
            )
            return bool(name_result.stdout.strip() and email_result.stdout.strip())
        except Exception as e:
            logger.debug(f"[GitOps] 检查用户身份失败: {e}")
            return False

    def add_commit(self, files: List[str], message: str) -> Optional[str]:
        """添加文件并提交

        Args:
            files: 相对路径文件列表（用于日志和验证，实际使用 git add -A）
            message: 提交消息

        Returns:
            成功时返回 commit hash（短格式），失败返回 None
        """
        if not self.is_git_repo():
            logger.warning("[GitOps] 非 Git 仓库，跳过 commit")
            return None

        if not files:
            return None

        if not self.has_user_identity():
            logger.warning("[GitOps] 未配置 user.name/user.email，跳过 commit")
            return None

        safe_message = _sanitize_commit_message(message)

        try:
            # 使用 git add -A 添加所有变更
            # 这避免了路径编码问题（中文、特殊字符等）
            add_result = subprocess.run(
                ["git", "add", "-A"],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
            )
            if add_result.returncode != 0:
                logger.warning(f"[GitOps] git add -A 失败: {add_result.stderr}")
                return None

            commit_result = subprocess.run(
                ["git", "commit", "-m", safe_message],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
            )

            if commit_result.returncode != 0:
                stdout = commit_result.stdout or ""
                stderr = commit_result.stderr or ""
                if "nothing to commit" in stdout or "nothing to commit" in stderr:
                    logger.debug("[GitOps] 没有变更需要提交")
                else:
                    logger.warning(f"[GitOps] git commit 失败: {stderr or stdout}")
                return None

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

    def has_git(self) -> bool:
        """检查是否有 Git 仓库"""
        return self.is_git_repo()

    def get_diff(self, commit1: str, commit2: str = None, file_path: str = None) -> str:
        """获取两个 commit 之间的 diff

        Args:
            commit1: 旧 commit hash
            commit2: 新 commit hash（None 则比较工作区）
            file_path: 相对路径文件（可选）

        Returns:
            diff 文本
        """
        if not self.is_git_repo():
            return ""

        # 验证 commit hash 格式（只允许十六进制字符）
        if not re.match(r'^[0-9a-fA-F]+$', commit1):
            logger.warning(f"[GitOps] 无效的 commit hash: {commit1}")
            return ""
        if commit2 and not re.match(r'^[0-9a-fA-F]+$', commit2):
            logger.warning(f"[GitOps] 无效的 commit hash: {commit2}")
            return ""

        try:
            cmd = ["git", "diff", commit1]
            if commit2:
                cmd.append(commit2)
            if file_path:
                try:
                    safe_path = _sanitize_git_path(file_path)
                    cmd.extend(["--", safe_path])
                except ValueError as e:
                    logger.warning(f"[GitOps] 跳过不安全的文件路径: {e}")

            result = subprocess.run(
                cmd,
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
            )
            return result.stdout
        except Exception as e:
            logger.error(f"[GitOps] get_diff 异常: {e}")
            return ""

    def get_file_diff(self, commit: str, file_path: str) -> str:
        """获取指定 commit 与当前文件的 diff（兼容旧接口）

        Args:
            commit: commit hash
            file_path: 相对路径文件

        Returns:
            diff 文本
        """
        return self.get_diff(commit, None, file_path)

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
