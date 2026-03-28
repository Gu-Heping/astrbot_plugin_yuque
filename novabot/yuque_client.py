"""
NovaBot 语雀 API 客户端
基于 yuque2git 实现，支持限流和重试
"""

import asyncio
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import httpx

from astrbot.api import logger

# 默认配置
DEFAULT_CONCURRENCY = 3
DEFAULT_REQUEST_DELAY = 0.25
DEFAULT_MAX_RETRIES = 4
DEFAULT_TIMEZONE = "Asia/Shanghai"


class YuqueClient:
    """语雀 API 客户端（带限流和重试）"""

    def __init__(
        self,
        token: str,
        base_url: str = "https://www.yuque.com/api/v2",
        concurrency: int = DEFAULT_CONCURRENCY,
        request_delay: float = DEFAULT_REQUEST_DELAY,
        max_retries: int = DEFAULT_MAX_RETRIES,
        timezone: str = DEFAULT_TIMEZONE,
    ):
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.concurrency = concurrency
        self.request_delay = request_delay
        self.max_retries = max_retries
        self.timezone = timezone

        self.headers = {
            "X-Auth-Token": token,
            "User-Agent": "NovaBot/1.0",
            "Content-Type": "application/json",
        }

        self._semaphore = asyncio.Semaphore(concurrency)
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """获取 HTTP 客户端（懒加载）"""
        if self._client is None:
            timeout = httpx.Timeout(
                connect=10.0,   # 连接超时
                read=30.0,      # 读取超时
                write=30.0,     # 写入超时
                pool=10.0,      # 连接池超时
            )
            self._client = httpx.AsyncClient(headers=self.headers, timeout=timeout)
        return self._client

    async def close(self):
        """关闭客户端"""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self):
        """支持 async with 语句"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """退出时自动关闭客户端"""
        await self.close()
        return False

    async def _request_with_retry(self, method: str, url: str) -> httpx.Response:
        """带重试的请求"""
        last_exc = None
        client = await self._get_client()

        for attempt in range(self.max_retries):
            try:
                async with self._semaphore:
                    r = await client.request(method, url)

                    # 限流
                    if r.status_code == 429:
                        wait = 2 ** attempt
                        if "Retry-After" in r.headers:
                            try:
                                wait = int(r.headers["Retry-After"])
                            except ValueError:
                                pass
                        logger.warning(f"[YuqueClient] 429 限流，等待 {wait}s")
                        await asyncio.sleep(wait)
                        last_exc = httpx.HTTPStatusError("429", request=r.request, response=r)
                        continue

                    # 服务端错误
                    if 500 <= r.status_code < 600:
                        wait = 2 ** attempt
                        logger.warning(f"[YuqueClient] {r.status_code} 错误，等待 {wait}s")
                        await asyncio.sleep(wait)
                        last_exc = httpx.HTTPStatusError(f"{r.status_code}", request=r.request, response=r)
                        continue

                    # 请求间隔
                    if self.request_delay > 0:
                        await asyncio.sleep(self.request_delay)

                    return r

            except (httpx.RequestError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
                wait = 2 ** attempt
                logger.warning(f"[YuqueClient] 请求错误 {e}，等待 {wait}s")
                await asyncio.sleep(wait)
                last_exc = e

        if last_exc:
            raise last_exc
        raise RuntimeError("Unexpected retry loop exit")

    async def _get(self, path: str, params: dict = None) -> dict:
        """GET 请求"""
        url = f"{self.base_url}{path}"
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
            if qs:
                url += f"?{qs}"

        r = await self._request_with_retry("GET", url)
        r.raise_for_status()
        return r.json()

    # ========== API 方法 ==========

    async def get_user(self) -> Dict:
        """获取当前用户信息"""
        data = await self._get("/user")
        return data.get("data", {})

    async def get_user_repos(self, user_id: int, limit: int = 100) -> List[Dict]:
        """获取用户的知识库列表"""
        data = await self._get(f"/users/{user_id}/repos", {"limit": limit})
        return data.get("data", [])

    async def get_group_repos(self, group_id: int, limit: int = 100) -> List[Dict]:
        """获取团队的知识库列表"""
        data = await self._get(f"/groups/{group_id}/repos", {"limit": limit})
        return data.get("data", [])

    async def get_repo(self, repo_id_or_namespace) -> Dict:
        """获取知识库详情

        Args:
            repo_id_or_namespace: 知识库 ID (int) 或 namespace (str)
        """
        data = await self._get(f"/repos/{repo_id_or_namespace}")
        return data.get("data", {})

    async def get_repo_toc(self, repo_id_or_namespace) -> List[Dict]:
        """获取知识库目录结构

        Args:
            repo_id_or_namespace: 知识库 ID (int) 或 namespace (str)
        """
        data = await self._get(f"/repos/{repo_id_or_namespace}/toc")
        return data.get("data", [])

    async def get_repo_docs(self, repo_id_or_namespace, limit: int = 100) -> List[Dict]:
        """获取知识库的文档列表

        Args:
            repo_id_or_namespace: 知识库 ID (int) 或 namespace (str)
        """
        data = await self._get(f"/repos/{repo_id_or_namespace}/docs", {"limit": limit})
        return data.get("data", [])

    async def get_doc_detail(self, repo_id_or_namespace, slug: str) -> Dict:
        """获取文档详情（含正文）

        Args:
            repo_id_or_namespace: 知识库 ID (int) 或 namespace (str)
            slug: 文档 slug
        """
        data = await self._get(f"/repos/{repo_id_or_namespace}/docs/{slug}", {"include_content": "true"})
        return data.get("data", {})

    async def get_group_members(self, group_id: int) -> List[Dict]:
        """获取团队成员（分页）"""
        all_members = []
        page = 1

        while True:
            data = await self._get(f"/groups/{group_id}/statistics/members", {"page": page})
            members = data.get("data", {}).get("members", [])
            if not members:
                break
            all_members.extend(members)
            page += 1

        return all_members

    # ========== 工具方法 ==========

    @staticmethod
    def normalize_timestamp(ts: Optional[str]) -> str:
        """UTC 时间转为本地可读时间"""
        if not ts or not isinstance(ts, str):
            return str(ts) if ts else ""

        t = ts.strip().replace("Z", "+00:00")
        if "T" not in t:
            return t

        t = re.sub(r"\.\d+", "", t)
        if not re.search(r"[+-]\d{2}:\d{2}$", t):
            t = t + "+00:00"

        try:
            dt = datetime.fromisoformat(t)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            local = dt.astimezone(ZoneInfo(DEFAULT_TIMEZONE))
            return local.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return t

    @staticmethod
    def slug_safe(s: str) -> str:
        """安全文件名"""
        for c in r'/\:*?"<>|':
            s = s.replace(c, "_")
        return s.strip() or "untitled"

    @staticmethod
    def doc_basename(title: Optional[str], slug: str) -> str:
        """文档文件名：标题优先，无标题用 slug"""
        return YuqueClient.slug_safe(title or slug) or "untitled"

    @staticmethod
    def author_name_from_detail(detail: Dict) -> str:
        """从文档详情获取创建者名（不使用 last_editor）

        yuque2git commit 2995580: author 用创建者而非最后编辑者
        """
        for key in ("creator", "user"):
            obj = detail.get(key)
            if isinstance(obj, dict):
                name = (obj.get("name") or obj.get("login") or "").strip()
                if name:
                    return name
        return ""

    @staticmethod
    def parse_frontmatter(content: str) -> tuple[dict, str]:
        """解析 Markdown 文件的 YAML frontmatter

        Args:
            content: Markdown 文件内容

        Returns:
            (metadata, body): 元数据字典和正文内容
        """
        import yaml

        metadata = {}
        body = content

        if content.startswith("---"):
            end = content.find("\n---", 3)
            if end != -1:
                try:
                    metadata = yaml.safe_load(content[3:end].strip()) or {}
                    body = content[end + 4:].strip()
                except yaml.YAMLError:
                    pass

        return metadata, body