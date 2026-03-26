"""
NovaBot RAG 检索模块
基于 LangChain + ChromaDB
"""

import gc
import shutil
from pathlib import Path
from typing import Optional

from langchain_community.vectorstores import Chroma
from langchain_openai import OpenAIEmbeddings
from langchain_core.documents import Document


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
        # 确保目录存在
        self.persist_directory.mkdir(parents=True, exist_ok=True)

        return Chroma(
            persist_directory=str(self.persist_directory),
            embedding_function=self.embeddings,
        )

    def index_docs(self, docs: list[dict]) -> int:
        """索引文档到向量库"""
        if not docs:
            return 0

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
            return 0

        # 批量添加
        try:
            self.vectorstore.add_documents(documents)
            return len(documents)
        except Exception as e:
            print(f"索引文档失败: {e}")
            raise

    def index_from_sync(self, docs_dir: str) -> int:
        """从同步目录读取 Markdown 并索引"""
        import yaml

        docs_path = Path(docs_dir)
        if not docs_path.exists():
            print(f"文档目录不存在: {docs_dir}")
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
                print(f"读取 {md_file} 失败: {e}")

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
            print(f"搜索失败: {e}")
            return []

    def clear(self) -> bool:
        """清空向量库"""
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
        if self.persist_directory.exists():
            try:
                shutil.rmtree(self.persist_directory)
            except PermissionError:
                # Windows 上可能文件被锁定，尝试延迟删除
                print("警告：无法删除向量库目录，可能被其他进程占用")
                return False
            except Exception as e:
                print(f"删除向量库目录失败: {e}")
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
            return {
                "docs_count": 0,
                "persist_directory": str(self.persist_directory),
                "error": str(e),
            }