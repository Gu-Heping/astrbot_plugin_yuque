"""
NovaBot 元数据索引模块
同步时构建 SQLite 索引，支持高效元数据查询
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from astrbot.api import logger


class DocIndex:
    """文档元数据索引"""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            try:
                self._conn = sqlite3.connect(str(self.db_path))
                # 启用 WAL 模式提高并发性能
                self._conn.execute("PRAGMA journal_mode=WAL")
                # 返回字典格式
                self._conn.row_factory = sqlite3.Row
            except sqlite3.Error as e:
                logger.error(f"[DocIndex] 数据库连接失败: {e}")
                raise
        return self._conn

    def _init_db(self):
        """初始化数据库表"""
        try:
            conn = self._get_conn()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS docs (
                    id INTEGER PRIMARY KEY,
                    yuque_id INTEGER UNIQUE,
                    title TEXT,
                    slug TEXT,
                    author TEXT,
                    book_name TEXT,
                    book_namespace TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    word_count INTEGER DEFAULT 0,
                    file_path TEXT,
                    indexed_at TEXT
                )
            """)

            # 创建索引
            conn.execute("CREATE INDEX IF NOT EXISTS idx_author ON docs(author)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_book ON docs(book_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_updated ON docs(updated_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_yuque_id ON docs(yuque_id)")

            conn.commit()
            logger.info(f"[DocIndex] 数据库初始化完成: {self.db_path}")
        except sqlite3.Error as e:
            logger.error(f"[DocIndex] 数据库初始化失败: {e}")
            raise

    def clear(self):
        """清空索引"""
        try:
            conn = self._get_conn()
            conn.execute("DELETE FROM docs")
            conn.commit()
            logger.info("[DocIndex] 索引已清空")
        except sqlite3.Error as e:
            logger.error(f"[DocIndex] 清空索引失败: {e}")

    def add_doc(self, doc: Dict):
        """添加或更新文档"""
        try:
            conn = self._get_conn()
            conn.execute("""
                INSERT OR REPLACE INTO docs
                (yuque_id, title, slug, author, book_name, book_namespace,
                 created_at, updated_at, word_count, file_path, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                doc.get("yuque_id"),
                doc.get("title", ""),
                doc.get("slug", ""),
                doc.get("author", ""),
                doc.get("book_name", ""),
                doc.get("book_namespace", ""),
                doc.get("created_at", ""),
                doc.get("updated_at", ""),
                doc.get("word_count", 0),
                doc.get("file_path", ""),
                datetime.now().isoformat(),
            ))
            conn.commit()
        except sqlite3.Error as e:
            logger.error(f"[DocIndex] 添加文档失败: {e}")

    def delete_doc(self, yuque_id: int) -> bool:
        """删除指定文档的索引记录

        Args:
            yuque_id: 语雀文档 ID

        Returns:
            是否删除成功（有记录被删除）
        """
        try:
            conn = self._get_conn()
            cursor = conn.execute("DELETE FROM docs WHERE yuque_id = ?", (yuque_id,))
            conn.commit()
            deleted = cursor.rowcount > 0
            if deleted:
                logger.info(f"[DocIndex] 删除文档索引: yuque_id={yuque_id}")
            return deleted
        except sqlite3.Error as e:
            logger.error(f"[DocIndex] 删除文档失败: {e}")
            return False

    def get_doc_by_yuque_id(self, yuque_id: int) -> Optional[Dict]:
        """根据语雀 ID 获取文档记录"""
        try:
            conn = self._get_conn()
            row = conn.execute("SELECT * FROM docs WHERE yuque_id = ?", (yuque_id,)).fetchone()
            return dict(row) if row else None
        except sqlite3.Error as e:
            logger.error(f"[DocIndex] 查询文档失败: {e}")
            return None

    def add_docs(self, docs: List[Dict]):
        """批量添加文档"""
        try:
            conn = self._get_conn()
            now = datetime.now().isoformat()

            # 尝试批量插入
            try:
                conn.executemany("""
                    INSERT OR REPLACE INTO docs
                    (yuque_id, title, slug, author, book_name, book_namespace,
                     created_at, updated_at, word_count, file_path, indexed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, [
                    (
                        d.get("yuque_id"),
                        d.get("title", ""),
                        d.get("slug", ""),
                        d.get("author", ""),
                        d.get("book_name", ""),
                        d.get("book_namespace", ""),
                        d.get("created_at", ""),
                        d.get("updated_at", ""),
                        d.get("word_count", 0),
                        d.get("file_path", ""),
                        now,
                    )
                    for d in docs
                ])
                conn.commit()
                logger.info(f"[DocIndex] 索引了 {len(docs)} 篇文档")
            except sqlite3.Error as e:
                # 批量失败，逐个重试
                logger.warning(f"[DocIndex] 批量索引失败，逐个重试: {e}")
                success_count = 0
                for d in docs:
                    try:
                        conn.execute("""
                            INSERT OR REPLACE INTO docs
                            (yuque_id, title, slug, author, book_name, book_namespace,
                             created_at, updated_at, word_count, file_path, indexed_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            d.get("yuque_id"),
                            d.get("title", ""),
                            d.get("slug", ""),
                            d.get("author", ""),
                            d.get("book_name", ""),
                            d.get("book_namespace", ""),
                            d.get("created_at", ""),
                            d.get("updated_at", ""),
                            d.get("word_count", 0),
                            d.get("file_path", ""),
                            now,
                        ))
                        conn.commit()
                        success_count += 1
                    except sqlite3.Error as e2:
                        logger.warning(f"[DocIndex] 索引文档失败: {d.get('title', 'unknown')} - {e2}")
                logger.info(f"[DocIndex] 逐个索引完成: {success_count}/{len(docs)}")
        except sqlite3.Error as e:
            logger.error(f"[DocIndex] 批量添加文档失败: {e}")

    def search(
        self,
        author: Optional[str] = None,
        book: Optional[str] = None,
        title: Optional[str] = None,
        order_by: str = "updated_at",
        limit: int = 20,
        offset: int = 0,
    ) -> List[Dict]:
        """搜索文档

        Args:
            author: 作者名（模糊匹配）
            book: 知识库名（模糊匹配）
            title: 标题（模糊匹配）
            order_by: 排序字段 (updated_at, created_at, word_count)
            limit: 返回数量
            offset: 偏移量
        """
        try:
            conn = self._get_conn()

            # 构建查询
            conditions = []
            params = []

            if author:
                conditions.append("author LIKE ?")
                params.append(f"%{author}%")
            if book:
                conditions.append("book_name LIKE ?")
                params.append(f"%{book}%")
            if title:
                conditions.append("title LIKE ?")
                params.append(f"%{title}%")

            where_clause = " AND ".join(conditions) if conditions else "1=1"

            # 验证排序字段
            valid_orders = {"updated_at", "created_at", "word_count", "title"}
            if order_by not in valid_orders:
                order_by = "updated_at"

            sql = f"""
                SELECT * FROM docs
                WHERE {where_clause}
                ORDER BY {order_by} DESC
                LIMIT ? OFFSET ?
            """
            params.extend([limit, offset])

            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"[DocIndex] 搜索失败: {e}")
            return []

    def get_stats(self, author: Optional[str] = None) -> Dict:
        """获取统计信息"""
        try:
            conn = self._get_conn()

            if author:
                row = conn.execute("""
                    SELECT
                        COUNT(*) as doc_count,
                        SUM(word_count) as total_words,
                        COUNT(DISTINCT book_name) as book_count
                    FROM docs WHERE author LIKE ?
                """, (f"%{author}%",)).fetchone()
            else:
                row = conn.execute("""
                    SELECT
                        COUNT(*) as doc_count,
                        SUM(word_count) as total_words,
                        COUNT(DISTINCT book_name) as book_count
                    FROM docs
                """).fetchone()

            return dict(row) if row else {"doc_count": 0, "total_words": 0, "book_count": 0}
        except sqlite3.Error as e:
            logger.error(f"[DocIndex] 获取统计失败: {e}")
            return {"doc_count": 0, "total_words": 0, "book_count": 0}

    def list_authors(self) -> List[Dict]:
        """列出所有作者及其文档数"""
        try:
            conn = self._get_conn()
            rows = conn.execute("""
                SELECT author, COUNT(*) as doc_count, SUM(word_count) as total_words
                FROM docs
                WHERE author != ''
                GROUP BY author
                ORDER BY doc_count DESC
            """).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"[DocIndex] 列出作者失败: {e}")
            return []

    def list_books(self) -> List[Dict]:
        """列出所有知识库及其文档数"""
        try:
            conn = self._get_conn()
            rows = conn.execute("""
                SELECT book_name, book_namespace, COUNT(*) as doc_count, SUM(word_count) as total_words
                FROM docs
                WHERE book_name != ''
                GROUP BY book_name
                ORDER BY doc_count DESC
            """).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"[DocIndex] 列出知识库失败: {e}")
            return []

    def close(self):
        """关闭连接"""
        if self._conn:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None

    def __del__(self):
        """析构时关闭连接"""
        self.close()

    def __enter__(self):
        """支持 with 语句"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出时自动关闭连接"""
        self.close()
        return False