"""
NovaBot RAG 检索模块
基于 LangChain + ChromaDB
"""

import gc
import shutil
import traceback
from pathlib import Path
from typing import Optional

from langchain_community.vectorstores import Chroma
from langchain_openai import OpenAIEmbeddings
from langchain_core.documents import Document


def log(msg: str):
    """简单日志"""
    print(f"[RAG] {msg}")


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

        # 延迟初始化向量库
        self._vectorstore: Optional[Chroma] = None

    @property
    def vectorstore(self) -> Chroma:
        """延迟加载向量库"""
        if self._vectorstore is None:
            self._vectorstore = self._create_vectorstore()
        return self._vectorstore

    def _create_vectorstore(self) -> Chroma:
        """创建新的向量库实例"""
        log(f"创建向量库: {self.persist_directory}")

        # 确保目录存在
        self.persist_directory.mkdir(parents=True, exist_ok=True)

        # 尝试加载现有数据库，验证完整性
        try:
            vs = Chroma(
                persist_directory=str(self.persist_directory),
                embedding_function=self.embeddings,
            )
            # 验证数据库可用性
            count = vs._collection.count()
            log(f"加载现有向量库成功，文档数: {count}")
            return vs
        except Exception as e:
            # 数据库损坏，清理后重建
            log(f"向量库损坏，准备重建: {e}")
            log(traceback.format_exc())

            # 清理并重新创建目录
            self._force_clear_directory()
            self.persist_directory.mkdir(parents=True, exist_ok=True)

            log("重建向量库...")
            vs = Chroma(
                persist_directory=str(self.persist_directory),
                embedding_function=self.embeddings,
            )
            log("向量库重建成功")
            return vs

    def _force_clear_directory(self):
        """强制清空向量库目录"""
        log(f"清空向量库目录: {self.persist_directory}")

        # 删除目录
        if self.persist_directory.exists():
            try:
                shutil.rmtree(self.persist_directory)
                log("目录删除成功")
            except PermissionError:
                # Windows 上可能文件被锁定
                log("目录被锁定，等待 1 秒后重试...")
                import time
                time.sleep(1)
                try:
                    shutil.rmtree(self.persist_directory)
                    log("目录删除成功")
                except Exception as e:
                    log(f"无法删除向量库目录: {e}")
                    log(traceback.format_exc())
                    raise
            except Exception as e:
                log(f"删除向量库目录失败: {e}")
                log(traceback.format_exc())
                raise
        else:
            log("目录不存在，无需删除")

    def index_docs(self, docs: list[dict]) -> int:
        """索引文档到向量库"""
        if not docs:
            log("没有文档需要索引")
            return 0

        log(f"开始索引 {len(docs)} 篇文档")

        # 构建 Document 列表
        documents = []
        for doc in docs:
            content = doc.get("content", "")

            # 验证内容
            if not content or not isinstance(content, str):
                continue

            content = " ".join(content.split()).strip()
            if not content:
                continue

            # 限制长度
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
            log("过滤后没有有效文档")
            return 0

        log(f"有效文档数: {len(documents)}")

        # 批量添加
        try:
            self.vectorstore.add_documents(documents)
            log(f"索引成功: {len(documents)} 篇文档")
            return len(documents)
        except Exception as e:
            log(f"索引文档失败: {e}")
            log(traceback.format_exc())
            raise

    def index_from_sync(self, docs_dir: str) -> int:
        """从同步目录读取 Markdown 并索引"""
        import yaml

        log(f"从目录读取文档: {docs_dir}")

        docs_path = Path(docs_dir)
        if not docs_path.exists():
            log(f"文档目录不存在: {docs_dir}")
            return 0

        all_docs = []

        for md_file in docs_path.rglob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")

                # 解析 frontmatter
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
                log(f"读取 {md_file} 失败: {e}")

        log(f"读取到 {len(all_docs)} 篇文档")
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
            log(f"搜索失败: {e}")
            return []

    def clear(self) -> bool:
        """清空向量库"""
        log("清空向量库...")

        # 1. 释放 ChromaDB 连接
        if self._vectorstore is not None:
            try:
                # 关闭底层的 SQLite 连接
                if hasattr(self._vectorstore, '_client'):
                    del self._vectorstore._client
            except:
                pass
            self._vectorstore = None

        # 2. 强制垃圾回收，释放文件句柄
        gc.collect()

        # 3. 删除目录
        try:
            self._force_clear_directory()
        except Exception as e:
            log(f"清空向量库失败: {e}")
            return False

        return True

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
            log(f"获取统计失败: {e}")
            return {
                "docs_count": 0,
                "persist_directory": str(self.persist_directory),
                "error": str(e),
            }