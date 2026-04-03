"""
NovaBot RAG 检索模块
基于 LangChain + ChromaDB
"""

import asyncio
import gc
import hashlib
import shutil
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional

import chromadb
from chromadb.config import Settings
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from astrbot.api import logger


class DashScopeEmbeddings(Embeddings):
    """DashScope Embedding 封装（兼容 OpenAI 格式）"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        token_usage_callback: Optional[Callable[[int], None]] = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.model = model
        self._client: Optional[object] = None  # httpx.AsyncClient
        self.token_usage_callback = token_usage_callback

    async def _get_client(self):
        """获取异步 HTTP 客户端"""
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(timeout=120.0)
        return self._client

    async def close(self):
        """关闭客户端"""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _aembed(self, texts: List[str]) -> List[List[float]]:
        """异步嵌入请求"""
        import httpx

        valid_texts = [t if t and t.strip() else " " for t in texts]
        logger.debug(f"[RAG] DashScope 请求嵌入: {len(valid_texts)} 个文本")

        url = f"{self.base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        data = {
            "model": self.model,
            "input": valid_texts,
        }

        client = await self._get_client()
        response = await client.post(url, headers=headers, json=data)
        response.raise_for_status()
        result = response.json()

        # 记录 token 使用
        usage = result.get("usage", {})
        total_tokens = usage.get("total_tokens", 0)
        if total_tokens > 0 and self.token_usage_callback:
            try:
                self.token_usage_callback(total_tokens)
            except Exception as e:
                logger.debug(f"[RAG] Token 回调失败: {e}")

        embeddings = [item["embedding"] for item in result["data"]]
        logger.debug(f"[RAG] 获得 {len(embeddings)} 个嵌入向量, tokens: {total_tokens}")
        return embeddings

    async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
        """异步批量嵌入"""
        return await self._aembed(texts)

    async def aembed_query(self, text: str) -> List[float]:
        """异步单个查询嵌入"""
        result = await self._aembed([text])
        return result[0]

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """同步批量嵌入（兼容 LangChain 接口）

        关键：始终使用同步 HTTP 客户端，避免事件循环问题
        """
        import httpx

        valid_texts = [t if t and t.strip() else " " for t in texts]
        logger.debug(f"[RAG] DashScope 请求嵌入: {len(valid_texts)} 个文本")

        url = f"{self.base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        data = {"model": self.model, "input": valid_texts}

        # 始终使用同步 HTTP 客户端，避免事件循环问题
        try:
            with httpx.Client(timeout=120.0) as client:
                response = client.post(url, headers=headers, json=data)
                response.raise_for_status()
                result = response.json()

            # 记录 token 使用
            usage = result.get("usage", {})
            total_tokens = usage.get("total_tokens", 0)
            if total_tokens > 0 and self.token_usage_callback:
                try:
                    self.token_usage_callback(total_tokens)
                except Exception as e:
                    logger.debug(f"[RAG] Token 回调失败: {e}")

            embeddings = [item["embedding"] for item in result["data"]]
            logger.debug(f"[RAG] 获得 {len(embeddings)} 个嵌入向量, tokens: {total_tokens}")
            return embeddings

        except Exception as e:
            logger.error(f"[RAG] Embedding 请求失败: {e}")
            raise

    def embed_query(self, text: str) -> List[float]:
        """同步单个查询嵌入"""
        result = self.embed_documents([text])
        return result[0]


class RAGEngine:
    """RAG 检索引擎"""

    def __init__(
        self,
        persist_directory: str,
        embedding_api_key: str,
        embedding_base_url: Optional[str] = None,
        embedding_model: str = "text-embedding-3-small",
        token_usage_callback: Optional[Callable[[int], None]] = None,
        cache_ttl: int = 300,  # 缓存有效期（秒）
    ):
        self.persist_directory = Path(persist_directory)
        self.embedding_api_key = embedding_api_key
        self.embedding_base_url = embedding_base_url
        self.embedding_model = embedding_model
        self.token_usage_callback = token_usage_callback
        self._total_embedding_tokens = 0  # 累计 embedding token 数

        # 查询缓存（v0.27.2 性能优化）
        self._query_cache: dict = {}  # {cache_key: {"results": [...], "timestamp": float}}
        self._cache_ttl = cache_ttl
        self._cache_lock = threading.Lock()

        # 初始化 embedding
        if embedding_base_url and "dashscope" in embedding_base_url.lower():
            # DashScope 使用自定义封装
            logger.info("[RAG] 使用 DashScope Embedding")
            self.embeddings = DashScopeEmbeddings(
                api_key=embedding_api_key,
                base_url=embedding_base_url,
                model=embedding_model,
                token_usage_callback=self._on_embedding_tokens,
            )
        else:
            # 其他使用 LangChain OpenAIEmbeddings
            from langchain_openai import OpenAIEmbeddings
            embedding_kwargs = {
                "openai_api_key": embedding_api_key,
                "model": embedding_model,
            }
            if embedding_base_url:
                embedding_kwargs["openai_api_base"] = embedding_base_url
            self.embeddings = OpenAIEmbeddings(**embedding_kwargs)

        # 延迟初始化
        self._vectorstore: Optional[Chroma] = None
        self._client: Optional[chromadb.ClientAPI] = None

    def _on_embedding_tokens(self, tokens: int):
        """Embedding token 使用回调"""
        self._total_embedding_tokens += tokens
        if self.token_usage_callback:
            try:
                self.token_usage_callback(tokens)
            except Exception as e:
                logger.debug(f"[RAG] Token 回调失败: {e}")

    def get_embedding_tokens(self) -> int:
        """获取累计 embedding token 数"""
        return self._total_embedding_tokens

    @property
    def vectorstore(self) -> Chroma:
        """延迟加载向量库"""
        if self._vectorstore is None:
            self._vectorstore = self._create_vectorstore()
        return self._vectorstore

    def _create_vectorstore(self) -> Chroma:
        """创建向量库"""
        logger.info(f"[RAG] 创建向量库: {self.persist_directory}")

        # 确保目录存在
        self.persist_directory.mkdir(parents=True, exist_ok=True)

        # 创建 ChromaDB 客户端
        settings = Settings(
            anonymized_telemetry=False,
            allow_reset=True,
        )

        try:
            # 尝试使用持久化客户端
            self._client = chromadb.PersistentClient(
                path=str(self.persist_directory),
                settings=settings,
            )

            # 尝试获取或创建 collection
            collection = self._client.get_or_create_collection("nova_docs")
            count = collection.count()
            logger.info(f"[RAG] 向量库加载成功，文档数: {count}")

        except Exception as e:
            # 数据库损坏，重置
            logger.warning(f"[RAG] 向量库损坏，重置: {e}")

            # 强制清理
            self._force_reset()

            # 重新创建
            self._client = chromadb.PersistentClient(
                path=str(self.persist_directory),
                settings=settings,
            )
            collection = self._client.get_or_create_collection("nova_docs")
            logger.info("[RAG] 向量库重置成功")

        # 创建 LangChain Chroma 包装
        return Chroma(
            client=self._client,
            collection_name="nova_docs",
            embedding_function=self.embeddings,
        )

    def _force_reset(self):
        """强制重置向量库"""
        import time
        logger.info("[RAG] 强制重置向量库...")

        # 0. 清除 ChromaDB 全局缓存
        try:
            from chromadb.api.client import SharedSystemClient
            cache = SharedSystemClient._identifier_to_system
            keys_to_remove = [k for k in cache.keys() if str(self.persist_directory) in str(k)]
            for k in keys_to_remove:
                del cache[k]
            if keys_to_remove:
                logger.info(f"[RAG] 清除缓存: {len(keys_to_remove)} 个")
        except Exception as e:
            logger.warning(f"[RAG] 清除缓存失败: {e}")

        # 1. 如果有客户端，尝试 reset
        if self._client is not None:
            try:
                self._client.reset()
                logger.info("[RAG] 客户端 reset 成功")
                self._vectorstore = None
                self._client = None
                return
            except Exception as e:
                logger.warning(f"[RAG] 客户端 reset 失败: {e}")

        # 2. 释放引用
        self._vectorstore = None
        self._client = None
        gc.collect()

        # 3. 重命名旧目录（避免 ChromaDB 缓存冲突）
        if self.persist_directory.exists():
            try:
                old_path = self.persist_directory.with_suffix(f".old_{int(time.time())}")
                self.persist_directory.rename(old_path)
                logger.info(f"[RAG] 目录已重命名: {old_path}")

                # 异步删除旧目录（不阻塞）
                import threading
                def cleanup():
                    try:
                        shutil.rmtree(old_path)
                    except OSError as e:
                        logger.debug(f"清理旧目录失败: {e}")
                threading.Thread(target=cleanup, daemon=True).start()

            except Exception as e:
                logger.warning(f"[RAG] 重命名目录失败: {e}")
                # 尝试直接删除
                try:
                    shutil.rmtree(self.persist_directory)
                    logger.info("[RAG] 目录删除成功")
                except Exception as e2:
                    logger.error(f"[RAG] 目录删除失败: {e2}")
                    raise

    def index_docs(self, docs: list[dict], progress_callback: Optional[Callable[[int, int], None]] = None) -> int:
        """索引文档到向量库

        Args:
            docs: 文档列表
            progress_callback: 进度回调函数 (current, total)
        """
        if not docs:
            logger.info("[RAG] 没有文档需要索引")
            return 0

        logger.info(f"[RAG] 开始索引 {len(docs)} 篇文档")

        # 测试 embedding 是否正常工作
        try:
            test_embedding = self.embeddings.embed_query("test")
            if not test_embedding:
                raise ValueError("Embedding 返回空结果")
            logger.info(f"[RAG] Embedding 测试成功，维度: {len(test_embedding)}")
        except Exception as e:
            logger.error(f"[RAG] Embedding 测试失败: {e}")
            raise

        # 构建 Document 列表
        documents = []
        for i, doc in enumerate(docs):
            content = doc.get("content", "")

            # 严格验证内容
            if content is None:
                continue
            if not isinstance(content, str):
                logger.warning(f"[RAG] 文档 {i} 内容类型无效: {type(content)}")
                continue

            # 清理内容
            try:
                content = " ".join(content.split()).strip()
            except Exception as e:
                logger.warning(f"[RAG] 文档 {i} 内容清理失败: {e}")
                continue

            if not content:
                continue

            # 限制长度
            if len(content) > 8000:
                content = content[:8000]

            # 确保内容是有效字符串
            try:
                _ = content.encode('utf-8')
            except Exception as e:
                logger.warning(f"[RAG] 文档 {i} 编码失败: {e}")
                continue

            documents.append(Document(
                page_content=content,
                metadata={
                    "id": str(doc.get("id", "") or ""),
                    "title": str(doc.get("title", "") or ""),
                    "slug": str(doc.get("slug", "") or ""),
                    "author": str(doc.get("author", "") or ""),
                    "book_name": str(doc.get("book_name", "") or ""),
                    "source": f"yuque:{doc.get('repo_namespace', '') or ''}/{doc.get('slug', '') or ''}",
                }
            ))

            # 添加 creator_id（仅当有值时，ChromaDB 不接受 None）
            creator_id = doc.get("creator_id")
            if creator_id is not None:
                documents[-1].metadata["creator_id"] = creator_id

        if not documents:
            logger.info("[RAG] 过滤后没有有效文档")
            return 0

        total_docs = len(documents)
        logger.info(f"[RAG] 有效文档数: {total_docs}")

        # 分批索引，避免一次提交太多
        batch_size = 10  # 减小批量避免 API 超时
        total_indexed = 0

        for i in range(0, len(documents), batch_size):
            batch = documents[i:i + batch_size]
            try:
                self.vectorstore.add_documents(batch)
                total_indexed += len(batch)
                logger.info(f"[RAG] 索引进度: {total_indexed}/{total_docs}")

                # 进度回调
                if progress_callback:
                    try:
                        progress_callback(total_indexed, total_docs)
                    except Exception:
                        pass

            except Exception as e:
                logger.error(f"[RAG] 批次 {i//batch_size} 索引失败: {e}")
                # 尝试逐个索引找出问题文档
                for j, doc in enumerate(batch):
                    try:
                        self.vectorstore.add_documents([doc])
                        total_indexed += 1
                        if progress_callback:
                            try:
                                progress_callback(total_indexed, total_docs)
                            except Exception:
                                pass
                    except Exception as e2:
                        title = doc.metadata.get('title', 'unknown') if doc.metadata else 'unknown'
                        logger.error(f"[RAG] 文档索引失败: {title} - {e2}")

        logger.info(f"[RAG] 索引完成: {total_indexed} 篇文档")
        return total_indexed

    def upsert_doc(self, doc: dict) -> bool:
        """更新或插入单个文档

        Args:
            doc: 文档字典，包含 id, content, title, slug, author, book_name 等字段

        Returns:
            是否成功
        """
        yuque_id = doc.get("id") or doc.get("yuque_id")
        if not yuque_id:
            logger.warning("[RAG] upsert_doc 缺少文档 ID")
            return False

        yuque_id = str(yuque_id)

        # 先删除旧向量
        self.delete_doc(int(yuque_id))

        # 添加新向量
        indexed = self.index_docs([doc])
        if indexed > 0:
            logger.info(f"[RAG] 更新向量成功: yuque_id={yuque_id}")
            return True
        return False

    def delete_doc(self, yuque_id: int) -> bool:
        """删除指定文档的向量

        Args:
            yuque_id: 语雀文档 ID

        Returns:
            是否成功
        """
        try:
            collection = self.vectorstore._collection
            # 使用 where 条件删除
            collection.delete(where={"id": str(yuque_id)})
            logger.info(f"[RAG] 删除向量: yuque_id={yuque_id}")
            return True
        except Exception as e:
            logger.error(f"[RAG] 删除向量失败: {e}")
            return False

    def index_from_sync(self, docs_dir: str, progress_callback: Optional[Callable[[int, int], None]] = None) -> int:
        """从同步目录读取 Markdown 并索引（全量重建）

        Args:
            docs_dir: 文档目录
            progress_callback: 进度回调函数 (current, total)
        """
        import re
        import yaml

        logger.info(f"[RAG] 从目录读取文档: {docs_dir}")

        docs_path = Path(docs_dir)
        if not docs_path.exists():
            logger.warning(f"[RAG] 文档目录不存在: {docs_dir}")
            return 0

        # 全量重建：先清空向量库
        logger.info("[RAG] 清空旧索引...")
        try:
            self.clear()
        except Exception as e:
            logger.warning(f"[RAG] 清空索引失败，继续: {e}")

        all_docs = []

        for md_file in docs_path.rglob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")

                metadata = {}
                body = content

                # 1. 去掉 YAML frontmatter
                if content.startswith("---"):
                    end = content.find("\n---", 3)
                    if end != -1:
                        try:
                            metadata = yaml.safe_load(content[3:end].strip()) or {}
                            body = content[end + 4:].strip()
                        except yaml.YAMLError as e:
                            logger.debug(f"YAML 解析失败: {e}")

                # 2. 去掉文档开头的元信息表格
                # 删除开头所有以 | 开头的行（直到遇到非表格行）
                lines = body.split('\n')
                content_start = 0
                for i, line in enumerate(lines):
                    stripped = line.strip()
                    # 跳过空行
                    if not stripped:
                        content_start = i + 1
                        continue
                    # 跳过表格行（以 | 开头或 |---| 分隔行）
                    if stripped.startswith('|') or re.match(r'^\|[-:\s|]+\|$', stripped):
                        content_start = i + 1
                        continue
                    # 遇到非表格行，停止
                    break

                body = '\n'.join(lines[content_start:]).strip()

                if not body or not body.strip():
                    continue

                # 提取 creator_id（可能为 None）
                creator_id_raw = metadata.get("creator_id")
                creator_id = None
                if creator_id_raw is not None:
                    creator_id = int(creator_id_raw) if isinstance(creator_id_raw, (int, float, str)) and str(creator_id_raw).strip() else None

                all_docs.append({
                    "content": str(body),
                    "id": str(metadata.get("id") or ""),
                    "title": str(metadata.get("title") or ""),
                    "slug": str(metadata.get("slug") or ""),
                    "author": str(metadata.get("author") or ""),
                    "book_name": str(metadata.get("book_name") or ""),
                    "repo_namespace": str(md_file.parent.relative_to(docs_path)),
                    "creator_id": creator_id,  # 创建者 ID
                })

            except Exception as e:
                logger.warning(f"[RAG] 读取 {md_file} 失败: {e}")

        logger.info(f"[RAG] 读取到 {len(all_docs)} 篇文档")
        return self.index_docs(all_docs, progress_callback)

    def search(self, query: str, k: int = 5, book_filter: str = None, use_cache: bool = True) -> list[dict]:
        """语义检索（按文档ID去重）

        Args:
            query: 搜索查询
            k: 返回数量
            book_filter: 知识库过滤（可选）
            use_cache: 是否使用缓存（默认 True）

        Returns:
            搜索结果列表
        """
        if not query or not isinstance(query, str):
            return []

        query = query.strip()
        if not query:
            return []

        # 检查缓存（v0.27.2）
        if use_cache:
            cache_key = self._make_cache_key(query, k, book_filter)
            cached = self._get_from_cache(cache_key)
            if cached is not None:
                logger.debug(f"[RAG] 命中缓存: {query[:30]}")
                return cached

        try:
            # 构建过滤条件
            search_kwargs = {"k": k * 3}
            if book_filter:
                search_kwargs["filter"] = {"book_name": book_filter}

            # 获取更多结果用于去重
            raw_results = self.vectorstore.similarity_search(query, **search_kwargs)

            # 按文档 ID 去重，保留分数最高的
            seen_ids = set()
            unique_results = []
            for doc in raw_results:
                doc_id = doc.metadata.get("id", "")
                # 如果没有 ID，回退到 title
                dedup_key = doc_id if doc_id else doc.metadata.get("title", "")

                if dedup_key and dedup_key in seen_ids:
                    continue
                if dedup_key:
                    seen_ids.add(dedup_key)

                unique_results.append({
                    "content": doc.page_content[:500] if doc.page_content else "",
                    "title": doc.metadata.get("title", ""),
                    "source": doc.metadata.get("source", ""),
                    "author": doc.metadata.get("author", ""),
                    "book_name": doc.metadata.get("book_name", ""),
                    "creator_id": doc.metadata.get("creator_id"),  # 创建者 ID
                    "id": doc_id,
                })

                if len(unique_results) >= k:
                    break

            # 存入缓存
            if use_cache:
                self._set_cache(cache_key, unique_results)

            return unique_results
        except Exception as e:
            logger.error(f"[RAG] 搜索失败: {e}")
            return []

    def _make_cache_key(self, query: str, k: int, book_filter: Optional[str]) -> str:
        """生成缓存键"""
        key_str = f"{query}:{k}:{book_filter or ''}"
        return hashlib.md5(key_str.encode()).hexdigest()

    def _get_from_cache(self, cache_key: str) -> Optional[list]:
        """从缓存获取结果"""
        with self._cache_lock:
            cached = self._query_cache.get(cache_key)
            if cached is None:
                return None

            # 检查是否过期
            if time.time() - cached["timestamp"] > self._cache_ttl:
                del self._query_cache[cache_key]
                return None

            return cached["results"]

    def _set_cache(self, cache_key: str, results: list):
        """存入缓存"""
        with self._cache_lock:
            self._query_cache[cache_key] = {
                "results": results,
                "timestamp": time.time(),
            }

            # 清理过期缓存
            self._cleanup_cache()

    def _cleanup_cache(self):
        """清理过期缓存"""
        now = time.time()
        expired_keys = [
            k for k, v in self._query_cache.items()
            if now - v["timestamp"] > self._cache_ttl
        ]
        for k in expired_keys:
            del self._query_cache[k]

        # 限制缓存大小
        if len(self._query_cache) > 100:
            # 删除最旧的 20 个
            sorted_items = sorted(
                self._query_cache.items(),
                key=lambda x: x[1]["timestamp"]
            )
            for k, _ in sorted_items[:20]:
                del self._query_cache[k]

    def clear_cache(self):
        """清空查询缓存"""
        with self._cache_lock:
            self._query_cache.clear()
        logger.info("[RAG] 查询缓存已清空")

    def clear(self) -> bool:
        """清空向量库"""
        logger.info("[RAG] 清空向量库...")

        try:
            self._force_reset()
            logger.info("[RAG] 清空成功")
            return True
        except Exception as e:
            logger.error(f"[RAG] 清空失败: {e}")
            return False

    def get_stats(self) -> dict:
        """获取向量库统计"""
        try:
            collection = self.vectorstore._collection
            count = collection.count()
            return {
                "docs_count": count,
                "persist_directory": str(self.persist_directory),
            }
        except Exception as e:
            logger.error(f"[RAG] 获取统计失败: {e}")
            return {
                "docs_count": 0,
                "persist_directory": str(self.persist_directory),
                "error": str(e),
            }

    async def close(self):
        """关闭资源（HTTP 客户端等）"""
        try:
            # 关闭 DashScopeEmbeddings 的 HTTP 客户端
            if isinstance(self.embeddings, DashScopeEmbeddings):
                await self.embeddings.close()
                logger.debug("[RAG] Embeddings 客户端已关闭")
        except Exception as e:
            logger.warning(f"[RAG] 关闭资源失败: {e}")