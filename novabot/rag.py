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
            if not content:
                continue

            # 清理内容（移除过多空白）
            content = " ".join(content.split())

            # 构建元数据
            metadata = {
                "id": doc.get("id", ""),
                "title": doc.get("title", ""),
                "slug": doc.get("slug", ""),
                "author": doc.get("author", ""),
                "book_name": doc.get("book_name", ""),
                "repo_namespace": doc.get("repo_namespace", ""),
                "source": f"yuque:{doc.get('repo_namespace', '')}/{doc.get('slug', '')}",
                "created_at": doc.get("created_at", ""),
                "updated_at": doc.get("updated_at", ""),
                "word_count": doc.get("word_count", len(content)),
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
        vectorstore = self._load_vectorstore()

        results = vectorstore.similarity_search(query, k=k)

        return [
            {
                "content": doc.page_content[:500],  # 截取前 500 字
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
        vectorstore = self._load_vectorstore()

        results = vectorstore.similarity_search_with_score(query, k=k)

        return [
            {
                "content": doc.page_content[:500],
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
        if self.persist_directory.exists():
            import shutil
            shutil.rmtree(self.persist_directory)
            self.persist_directory.mkdir(parents=True, exist_ok=True)
            self.vectorstore = None

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
                "error": str(e),
            }