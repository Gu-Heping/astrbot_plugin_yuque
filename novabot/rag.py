"""
NovaBot RAG 检索模块
基于 LangChain + ChromaDB
"""

import gc
import shutil
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings
from langchain_community.vectorstores import Chroma
from langchain_openai import OpenAIEmbeddings
from langchain_core.documents import Document

from astrbot.api import logger


class RAGEngine:
    """RAG 检索引擎"""

    def __init__(
        self,
        persist_directory: str,
        embedding_api_key: str,
        embedding_base_url: Optional[str] = None,
        embedding_model: str = "text-embedding-3-small"
    ):
        self.persist_directory = Path(persist_directory)
        self.embedding_api_key = embedding_api_key
        self.embedding_base_url = embedding_base_url
        self.embedding_model = embedding_model

        # 初始化 embedding
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
                    except:
                        pass
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

    def index_docs(self, docs: list[dict]) -> int:
        """索引文档到向量库"""
        if not docs:
            logger.info("[RAG] 没有文档需要索引")
            return 0

        logger.info(f"[RAG] 开始索引 {len(docs)} 篇文档")

        # 构建 Document 列表
        documents = []
        for doc in docs:
            content = doc.get("content", "")

            if not content or not isinstance(content, str):
                continue

            content = " ".join(content.split()).strip()
            if not content:
                continue

            if len(content) > 8000:
                content = content[:8000]

            documents.append(Document(
                page_content=content,
                metadata={
                    "id": str(doc.get("id", "")),
                    "title": str(doc.get("title", "")),
                    "slug": str(doc.get("slug", "")),
                    "author": str(doc.get("author", "")),
                    "book_name": str(doc.get("book_name", "")),
                    "source": f"yuque:{doc.get('repo_namespace', '')}/{doc.get('slug', '')}",
                }
            ))

        if not documents:
            logger.info("[RAG] 过滤后没有有效文档")
            return 0

        logger.info(f"[RAG] 有效文档数: {len(documents)}")

        try:
            self.vectorstore.add_documents(documents)
            logger.info(f"[RAG] 索引成功: {len(documents)} 篇文档")
            return len(documents)
        except Exception as e:
            logger.error(f"[RAG] 索引失败: {e}")
            raise

    def index_from_sync(self, docs_dir: str) -> int:
        """从同步目录读取 Markdown 并索引"""
        import yaml

        logger.info(f"[RAG] 从目录读取文档: {docs_dir}")

        docs_path = Path(docs_dir)
        if not docs_path.exists():
            logger.warning(f"[RAG] 文档目录不存在: {docs_dir}")
            return 0

        all_docs = []

        for md_file in docs_path.rglob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")

                metadata = {}
                body = content

                if content.startswith("---"):
                    end = content.find("\n---", 3)
                    if end != -1:
                        try:
                            metadata = yaml.safe_load(content[3:end].strip()) or {}
                            body = content[end + 4:].strip()
                        except:
                            pass

                if not body.strip():
                    continue

                all_docs.append({
                    "content": body,
                    "id": metadata.get("id"),
                    "title": metadata.get("title", ""),
                    "slug": metadata.get("slug", ""),
                    "author": metadata.get("author", ""),
                    "book_name": metadata.get("book_name", ""),
                    "repo_namespace": str(md_file.parent.relative_to(docs_path)),
                })

            except Exception as e:
                logger.warning(f"[RAG] 读取 {md_file} 失败: {e}")

        logger.info(f"[RAG] 读取到 {len(all_docs)} 篇文档")
        return self.index_docs(all_docs)

    def search(self, query: str, k: int = 5) -> list[dict]:
        """语义检索"""
        if not query or not isinstance(query, str):
            return []

        query = query.strip()
        if not query:
            return []

        try:
            results = self.vectorstore.similarity_search(query, k=k)
            return [
                {
                    "content": doc.page_content[:500] if doc.page_content else "",
                    "title": doc.metadata.get("title", ""),
                    "source": doc.metadata.get("source", ""),
                    "author": doc.metadata.get("author", ""),
                }
                for doc in results
            ]
        except Exception as e:
            logger.error(f"[RAG] 搜索失败: {e}")
            return []

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