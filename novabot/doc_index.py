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
                    creator_id INTEGER,
                    created_at TEXT,
                    updated_at TEXT,
                    word_count INTEGER DEFAULT 0,
                    file_path TEXT,
                    indexed_at TEXT
                )
            """)

            # 兼容性：检查 creator_id 列是否存在
            columns = conn.execute("PRAGMA table_info(docs)").fetchall()
            column_names = [col[1] for col in columns]
            if "creator_id" not in column_names:
                logger.info("[DocIndex] 添加 creator_id 列")
                conn.execute("ALTER TABLE docs ADD COLUMN creator_id INTEGER")

            # 创建索引
            conn.execute("CREATE INDEX IF NOT EXISTS idx_author ON docs(author)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_book ON docs(book_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_updated ON docs(updated_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_yuque_id ON docs(yuque_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_creator_id ON docs(creator_id)")

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
                (yuque_id, title, slug, author, book_name, book_namespace, creator_id,
                 created_at, updated_at, word_count, file_path, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                doc.get("yuque_id"),
                doc.get("title", ""),
                doc.get("slug", ""),
                doc.get("author", ""),
                doc.get("book_name", ""),
                doc.get("book_namespace", ""),
                doc.get("creator_id"),
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
                    (yuque_id, title, slug, author, book_name, book_namespace, creator_id,
                     created_at, updated_at, word_count, file_path, indexed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, [
                    (
                        d.get("yuque_id"),
                        d.get("title", ""),
                        d.get("slug", ""),
                        d.get("author", ""),
                        d.get("book_name", ""),
                        d.get("book_namespace", ""),
                        d.get("creator_id"),
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
                            (yuque_id, title, slug, author, book_name, book_namespace, creator_id,
                             created_at, updated_at, word_count, file_path, indexed_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            d.get("yuque_id"),
                            d.get("title", ""),
                            d.get("slug", ""),
                            d.get("author", ""),
                            d.get("book_name", ""),
                            d.get("book_namespace", ""),
                            d.get("creator_id"),
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

    def get_weekly_stats(self, since_date: str) -> dict:
        """获取本周文档统计（基于元数据）

        Args:
            since_date: 起始日期 YYYY-MM-DD

        Returns:
            {
                "new_docs": [{title, author, book_name, word_count}, ...],
                "updated_docs": [{title, author, book_name}, ...],
                "author_stats": [{author, doc_count, total_words}, ...],
                "book_stats": [{book_name, doc_count}, ...],
                "total_new": int,
                "total_updated": int,
                "total_words_new": int,
            }
        """
        result = {
            "new_docs": [],
            "updated_docs": [],
            "author_stats": [],
            "book_stats": [],
            "total_new": 0,
            "total_updated": 0,
            "total_words_new": 0,
        }

        try:
            conn = self._get_conn()

            # 1. 本周新建文档：date(created_at) >= since_date
            new_docs_rows = conn.execute("""
                SELECT title, author, book_name, word_count, created_at
                FROM docs
                WHERE date(created_at) >= date(?)
                ORDER BY created_at DESC
                LIMIT 20
            """, (since_date,)).fetchall()

            for row in new_docs_rows:
                result["new_docs"].append({
                    "title": row["title"] or "",
                    "author": row["author"] or "",
                    "book_name": row["book_name"] or "",
                    "word_count": row["word_count"] or 0,
                    "created_at": row["created_at"] or "",
                })

            result["total_new"] = len(result["new_docs"])
            result["total_words_new"] = sum(d["word_count"] for d in result["new_docs"])

            # 2. 本周更新文档：date(updated_at) >= since_date 且 date(created_at) < since_date
            updated_docs_rows = conn.execute("""
                SELECT title, author, book_name, updated_at
                FROM docs
                WHERE date(updated_at) >= date(?)
                  AND (date(created_at) < date(?) OR created_at IS NULL)
                ORDER BY updated_at DESC
                LIMIT 20
            """, (since_date, since_date)).fetchall()

            for row in updated_docs_rows:
                result["updated_docs"].append({
                    "title": row["title"] or "",
                    "author": row["author"] or "",
                    "book_name": row["book_name"] or "",
                    "updated_at": row["updated_at"] or "",
                })

            result["total_updated"] = len(result["updated_docs"])

            # 3. 本周作者统计
            author_rows = conn.execute("""
                SELECT author, COUNT(*) as doc_count, SUM(word_count) as total_words
                FROM docs
                WHERE date(updated_at) >= date(?)
                AND author != ''
                GROUP BY author
                ORDER BY doc_count DESC
                LIMIT 10
            """, (since_date,)).fetchall()

            for row in author_rows:
                result["author_stats"].append({
                    "author": row["author"],
                    "doc_count": row["doc_count"],
                    "total_words": row["total_words"] or 0,
                })

            # 4. 本周知识库统计
            book_rows = conn.execute("""
                SELECT book_name, COUNT(*) as doc_count
                FROM docs
                WHERE date(updated_at) >= date(?)
                AND book_name != ''
                GROUP BY book_name
                ORDER BY doc_count DESC
                LIMIT 10
            """, (since_date,)).fetchall()

            for row in book_rows:
                result["book_stats"].append({
                    "book_name": row["book_name"],
                    "doc_count": row["doc_count"],
                })

            return result

        except sqlite3.Error as e:
            logger.error(f"[DocIndex] 获取周报统计失败: {e}")
            return result

    def get_weekly_publish_stats_all(self) -> List[Dict]:
        """获取全部文档按周发布的原始统计数据。"""
        try:
            conn = self._get_conn()
            rows = conn.execute(
                """
                SELECT
                    date(created_at, '-' || ((CAST(strftime('%w', created_at) AS INTEGER) + 6) % 7) || ' days') AS week_start,
                    date(created_at, '-' || ((CAST(strftime('%w', created_at) AS INTEGER) + 6) % 7) || ' days', '+6 days') AS week_end,
                    COUNT(*) AS published_docs,
                    COUNT(DISTINCT CASE WHEN TRIM(author) != '' THEN author END) AS authors_count,
                    COALESCE(SUM(word_count), 0) AS total_words
                FROM docs
                WHERE created_at IS NOT NULL AND TRIM(created_at) != ''
                GROUP BY week_start, week_end
                ORDER BY week_start ASC
                """
            ).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"[DocIndex] 获取按周发布统计失败: {e}")
            return []

    def get_all_docs(self, limit: int = 10000) -> List[Dict]:
        """获取所有文档（用于分析）

        Args:
            limit: 最大返回数量，默认 10000

        Returns:
            文档列表 [{yuque_id, title, author, book_name, creator_id, ...}, ...]
        """
        try:
            conn = self._get_conn()
            rows = conn.execute("""
                SELECT yuque_id, title, author, book_name, book_namespace, creator_id,
                       created_at, updated_at, word_count, file_path
                FROM docs
                ORDER BY updated_at DESC
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"[DocIndex] 获取所有文档失败: {e}")
            return []

    def find_docs_by_slug(self, slug: str, limit: int = 5) -> List[Dict]:
        """按 slug 查询文档（用于链接解析）"""
        if not slug:
            return []
        try:
            conn = self._get_conn()
            rows = conn.execute(
                """
                SELECT title, author, book_name, book_namespace, file_path
                FROM docs
                WHERE slug = ?
                LIMIT ?
                """,
                (slug, limit),
            ).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"[DocIndex] 按 slug 查询失败: {e}")
            return []

    def get_docs_by_creator_or_author(
        self,
        creator_id: Optional[int] = None,
        author_name: str = "",
        limit: int = 20,
    ) -> List[Dict]:
        """按 creator_id（优先）或作者名获取文档列表"""
        try:
            conn = self._get_conn()
            if creator_id:
                rows = conn.execute(
                    """
                    SELECT title, book_name, word_count
                    FROM docs
                    WHERE creator_id = ?
                    ORDER BY word_count DESC
                    LIMIT ?
                    """,
                    (creator_id, limit),
                ).fetchall()
            elif author_name:
                rows = conn.execute(
                    """
                    SELECT title, book_name, word_count
                    FROM docs
                    WHERE author = ?
                    ORDER BY word_count DESC
                    LIMIT ?
                    """,
                    (author_name, limit),
                ).fetchall()
            else:
                return []
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"[DocIndex] 按作者查询文档失败: {e}")
            return []

    def get_kb_contributors(self, book_name: str, limit: int = 10) -> List[Dict]:
        """获取知识库贡献者统计"""
        if not book_name:
            return []
        try:
            conn = self._get_conn()
            rows = conn.execute(
                """
                SELECT author, COUNT(*) as doc_count, SUM(word_count) as total_words
                FROM docs
                WHERE book_name = ? AND author != ''
                GROUP BY author
                ORDER BY doc_count DESC
                LIMIT ?
                """,
                (book_name, limit),
            ).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"[DocIndex] 获取知识库贡献者失败: {e}")
            return []

    def get_kb_recent_updates(self, book_name: str, limit: int = 10) -> List[Dict]:
        """获取知识库最近更新"""
        if not book_name:
            return []
        try:
            conn = self._get_conn()
            rows = conn.execute(
                """
                SELECT title, author, updated_at
                FROM docs
                WHERE book_name = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (book_name, limit),
            ).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"[DocIndex] 获取知识库最近更新失败: {e}")
            return []

    def find_doc_for_book_by_title(self, book_name: str, title_keyword: str) -> Optional[Dict]:
        """在知识库中按标题模糊匹配单篇文档"""
        if not book_name or not title_keyword:
            return None
        try:
            conn = self._get_conn()
            row = conn.execute(
                """
                SELECT title, author, book_name, file_path
                FROM docs
                WHERE book_name = ? AND title LIKE ?
                ORDER BY word_count DESC
                LIMIT 1
                """,
                (book_name, f"%{title_keyword}%"),
            ).fetchone()
            return dict(row) if row else None
        except sqlite3.Error as e:
            logger.error(f"[DocIndex] 按标题匹配文档失败: {e}")
            return None

    def get_book_activity(self, book_name: str, since_date: str, limit: int = 10) -> Dict:
        """获取指定知识库在时间区间内的活跃统计"""
        result = {"docs_updated": 0, "active_contributors": []}
        if not book_name or not since_date:
            return result
        try:
            conn = self._get_conn()
            docs_updated_row = conn.execute(
                """
                SELECT COUNT(*) as count
                FROM docs
                WHERE book_name = ? AND date(updated_at) >= date(?)
                """,
                (book_name, since_date),
            ).fetchone()
            active_rows = conn.execute(
                """
                SELECT author, COUNT(*) as doc_count
                FROM docs
                WHERE book_name = ? AND date(updated_at) >= date(?) AND author != ''
                GROUP BY author
                ORDER BY doc_count DESC
                LIMIT ?
                """,
                (book_name, since_date, limit),
            ).fetchall()
            result["docs_updated"] = int(dict(docs_updated_row)["count"]) if docs_updated_row else 0
            result["active_contributors"] = [dict(row) for row in active_rows]
            return result
        except sqlite3.Error as e:
            logger.error(f"[DocIndex] 获取知识库活跃度失败: {e}")
            return result

    def get_top_docs_by_word_count(self, book_name: str, limit: int = 5, min_words: int = 100) -> List[Dict]:
        """获取知识库内字数最多的文档"""
        if not book_name:
            return []
        try:
            conn = self._get_conn()
            rows = conn.execute(
                """
                SELECT title, author, word_count
                FROM docs
                WHERE book_name = ? AND word_count > ?
                ORDER BY word_count DESC
                LIMIT ?
                """,
                (book_name, min_words, limit),
            ).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"[DocIndex] 获取知识库长文档失败: {e}")
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