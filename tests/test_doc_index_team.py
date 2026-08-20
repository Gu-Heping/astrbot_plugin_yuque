from novabot.doc_index import DocIndex


def test_doc_index_adds_default_team_and_filters(tmp_path):
    index = DocIndex(str(tmp_path / "doc_index.db"))
    index.add_doc(
        {
            "yuque_id": 1,
            "title": "默认团队文档",
            "book_name": "工程",
            "book_namespace": "nova/eng",
            "updated_at": "2026-01-02",
            "file_path": "工程/默认.md",
        }
    )
    index.add_doc(
        {
            "yuque_id": 2,
            "title": "其他团队文档",
            "team_id": "other",
            "team_name": "Other",
            "book_name": "工程",
            "book_namespace": "other/eng",
            "updated_at": "2026-01-03",
            "file_path": "工程/其他.md",
        }
    )

    default_docs = index.search(team_id="default")
    other_docs = index.search(team_id="other")

    assert [doc["yuque_id"] for doc in default_docs] == [1]
    assert [doc["yuque_id"] for doc in other_docs] == [2]
    assert default_docs[0]["team_name"] == "NOVA"


def test_doc_index_identity_is_team_scoped(tmp_path):
    index = DocIndex(str(tmp_path / "doc_index.db"))
    index.add_doc(
        {
            "yuque_id": 42,
            "title": "默认同号文档",
            "team_id": "default",
            "team_name": "NOVA",
            "book_name": "工程",
            "updated_at": "2026-01-02",
            "file_path": "工程/默认.md",
        }
    )
    index.add_doc(
        {
            "yuque_id": 42,
            "title": "其他同号文档",
            "team_id": "other",
            "team_name": "Other",
            "book_name": "工程",
            "updated_at": "2026-01-03",
            "file_path": "other/工程/其他.md",
        }
    )

    assert index.get_doc_by_yuque_id(42)["title"] == "默认同号文档"
    assert index.get_doc_by_yuque_id(42, team_id="other")["title"] == "其他同号文档"
    assert [doc["team_id"] for doc in index.find_docs_by_yuque_id(42)] == ["default", "other"]
    assert [doc["title"] for doc in index.search(team_id="default")] == ["默认同号文档"]
    assert [doc["title"] for doc in index.search(team_id="other")] == ["其他同号文档"]


def test_doc_index_migrates_legacy_yuque_unique_schema(tmp_path):
    db_path = tmp_path / "legacy.db"
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE docs (
            id INTEGER PRIMARY KEY,
            yuque_id INTEGER UNIQUE,
            title TEXT,
            slug TEXT,
            author TEXT,
            team_id TEXT DEFAULT 'default',
            team_name TEXT DEFAULT 'NOVA',
            book_name TEXT,
            book_namespace TEXT,
            creator_id INTEGER,
            created_at TEXT,
            updated_at TEXT,
            word_count INTEGER DEFAULT 0,
            file_path TEXT,
            indexed_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO docs (yuque_id, title, team_id, team_name, file_path) VALUES (42, '旧文档', 'default', 'NOVA', '工程/旧.md')"
    )
    conn.commit()
    conn.close()

    index = DocIndex(str(db_path))
    index.add_doc(
        {
            "yuque_id": 42,
            "title": "其他团队新文档",
            "team_id": "other",
            "team_name": "Other",
            "file_path": "other/工程/新.md",
        }
    )

    assert index.get_doc_by_yuque_id(42)["title"] == "旧文档"
    assert index.get_doc_by_yuque_id(42, team_id="other")["title"] == "其他团队新文档"


def test_clear_team_treats_null_team_as_default_and_preserves_other_team(tmp_path):
    index = DocIndex(str(tmp_path / "doc_index.db"))
    conn = index._get_conn()
    conn.execute(
        "INSERT INTO docs (yuque_id, title, team_id, team_name, file_path) VALUES (1, '旧默认', NULL, 'NOVA', '旧/默认.md')"
    )
    conn.execute(
        "INSERT INTO docs (yuque_id, title, team_id, team_name, file_path) VALUES (2, '其他团队', 'other', 'Other', 'other/工程.md')"
    )
    conn.commit()

    deleted = index.clear_team("default")

    assert deleted == 1
    assert index.search(team_id="default") == []
    assert [doc["title"] for doc in index.search(team_id="other")] == ["其他团队"]
