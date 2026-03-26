"""
NovaBot RAG 检索模块
基于 LangChain + ChromaDB
"""

import json
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
        self.persist_directory.mkdir(parents=True, exist_ok=True)

        # 初始化 embedding
        embedding_kwargs = {
            "openai_api_key": embedding_api_key,
            "model": embedding_model,
        }
        if embedding_base_url:
            embedding_kwargs["openai_api_base"] = embedding_base_url

        self.embeddings = OpenAIEmbeddings(**embedding_kwargs)

        # 初始化向量库
        self.vectorstore: Optional[Chroma] = None

    def _load_vectorstore(self) -> Chroma:
        """加载向量库"""
        if self.vectorstore is None:
            try:
                self.vectorstore = Chroma(
                    persist_directory=str(self.persist_directory),
                    embedding_function=self.embeddings,
                )
            except Exception as e:
                print(f"加载向量库失败: {e}")
                # 尝试重新初始化
                self.clear()
                self.vectorstore = Chroma(
                    persist_directory=str(self.persist_directory),
                    embedding_function=self.embeddings,
                )
        return self.vectorstore

    def index_docs(self, docs: list[dict]) -> int:
        """
        索引文档到向量库

        Args:
            docs: 文档列表，每个文档包含 title, content, source 等字段

        Returns:
            索引的文档数量
        """
        if not docs:
            return 0

        # 构建 LangChain Document
        documents = []
        for doc in docs:
            content = doc.get("content", "")

            # 跳过空内容
            if not content or not isinstance(content, str):
                continue

            # 清理内容（移除过多空白，确保是有效字符串）
            content = " ".join(content.split())
            content = content.strip()

            # 跳过清理后为空的内容
            if not content:
                continue

            # 限制内容长度（避免 API 限制）
            if len(content) > 8000:
                content = content[:8000]

            # 构建元数据
            metadata = {
                "id": str(doc.get("id", "")),
                "title": str(doc.get("title", "")),
                "slug": str(doc.get("slug", "")),
                "author": str(doc.get("author", "")),
                "book_name": str(doc.get("book_name", "")),
                "repo_namespace": str(doc.get("repo_namespace", "")),
                "source": f"yuque:{doc.get('repo_namespace', '')}/{doc.get('slug', '')}",
                "created_at": str(doc.get("created_at", "")),
                "updated_at": str(doc.get("updated_at", "")),
            }

            documents.append(Document(page_content=content, metadata=metadata))

        if not documents:
            return 0

        # 添加到向量库
        vectorstore = self._load_vectorstore()
        vectorstore.add_documents(documents)
        vectorstore.persist()

        return len(documents)

    def index_from_sync(self, docs_dir: str) -> int:
        """
        从同步目录读取 Markdown 文件并索引

        Args:
            docs_dir: YuqueSync 同步的文档目录

        Returns:
            索引的文档数量
        """
        docs_path = Path(docs_dir)
        if not docs_path.exists():
            return 0

        all_docs = []

        # 遍历所有 .md 文件
        for md_file in docs_path.rglob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")
                metadata = self._parse_frontmatter(content)

                if metadata:
                    # 移除 frontmatter 后的正文
                    body = self._remove_frontmatter(content)

                    # 跳过空正文
                    if not body or not body.strip():
                        continue

                    all_docs.append({
                        "content": body,
                        "id": metadata.get("id"),
                        "title": metadata.get("title", ""),
                        "slug": metadata.get("slug", ""),
                        "author": metadata.get("author", ""),
                        "book_name": metadata.get("book_name", ""),
                        "repo_namespace": str(md_file.parent.relative_to(docs_path)),
                        "created_at": metadata.get("created_at", ""),
                        "updated_at": metadata.get("updated_at", ""),
                    })
            except Exception as e:
                print(f"读取 {md_file} 失败: {e}")

        return self.index_docs(all_docs)

    def _parse_frontmatter(self, content: str) -> Optional[dict]:
        """解析 Markdown frontmatter"""
        import yaml
        
        if not content.startswith("---"):
            return None
        
        # 找到 frontmatter 结束位置
        end = content.find("\n---", 3)
        if end == -1:
            return None
        
        yaml_content = content[3:end].strip()
        try:
            return yaml.safe_load(yaml_content)
        except Exception:
            return None

    def _remove_frontmatter(self, content: str) -> str:
        """移除 frontmatter，返回正文"""
        if not content.startswith("---"):
            return content
        
        end = content.find("\n---", 3)
        if end == -1:
            return content
        
        return content[end + 4:].strip()

    def search(self, query: str, k: int = 5) -> list[dict]:
        """
        语义检索

        Args:
            query: 查询文本
            k: 返回结果数量

        Returns:
            检索结果列表
        """
        # 确保 query 是有效字符串
        if not query or not isinstance(query, str):
            return []

        query = query.strip()
        if not query:
            return []

        vectorstore = self._load_vectorstore()

        try:
            results = vectorstore.similarity_search(query, k=k)
        except Exception as e:
            print(f"搜索失败: {e}")
            return []

        return [
            {
                "content": doc.page_content[:500] if doc.page_content else "",
                "title": doc.metadata.get("title", ""),
                "source": doc.metadata.get("source", ""),
                "author": doc.metadata.get("author", ""),
                "word_count": doc.metadata.get("word_count", 0),
            }
            for doc in results
        ]

    def search_with_scores(self, query: str, k: int = 5) -> list[dict]:
        """
        语义检索（带相似度分数）

        Args:
            query: 查询文本
            k: 返回结果数量

        Returns:
            检索结果列表（含 score 字段）
        """
        # 确保 query 是有效字符串
        if not query or not isinstance(query, str):
            return []

        query = query.strip()
        if not query:
            return []

        vectorstore = self._load_vectorstore()

        try:
            results = vectorstore.similarity_search_with_score(query, k=k)
        except Exception as e:
            print(f"搜索失败: {e}")
            return []

        return [
            {
                "content": doc.page_content[:500] if doc.page_content else "",
                "title": doc.metadata.get("title", ""),
                "source": doc.metadata.get("source", ""),
                "author": doc.metadata.get("author", ""),
                "word_count": doc.metadata.get("word_count", 0),
                "score": float(score),
            }
            for doc, score in results
        ]

    def clear(self):
        """清空向量库"""
        # 先释放连接
        self.vectorstore = None

        # 删除整个目录
        if self.persist_directory.exists():
            import shutil
            try:
                shutil.rmtree(self.persist_directory)
            except Exception as e:
                print(f"清空向量库失败: {e}")
                return False

        # 重新创建目录
        self.persist_directory.mkdir(parents=True, exist_ok=True)
        return True

    def get_stats(self) -> dict:
        """获取向量库统计信息"""
        try:
            vectorstore = self._load_vectorstore()
            # Chroma 不直接提供文档数量 API，尝试获取
            collection = vectorstore._collection
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