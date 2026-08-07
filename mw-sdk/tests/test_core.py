"""test_core.py — 核心存储操作：init/insert/get/update/count/cleanup/transactions"""
import pytest


class TestInitSchema:
    def test_init_creates_tables(self, client):
        """init_schema 创建所有核心表"""
        import sqlite3
        conn = client.get_conn()
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        for expected in ["document_files", "memory_classify", "memory_fts",
                         "memory_entity", "memory_cross_ref", "system_meta"]:
            assert expected in tables, f"Missing table: {expected}"

    def test_init_idempotent(self, client):
        """重复 init_schema 不报错"""
        client.init_schema()
        client.init_schema()


class TestInsertAndGet:
    def test_insert_and_get(self, client):
        """insert_classified → get_memory 往返"""
        doc_id = client.insert_classified("hello world", {
            "label": "rule", "summary": "test summary", "importance": "P1"
        })
        assert doc_id > 0
        mem = client.get_memory(doc_id)
        assert mem is not None
        assert "hello world" in mem.get("summary", "") or "hello world" in mem.get("compact_content", "")

    def test_insert_empty_content(self, client):
        """空内容也能插入"""
        doc_id = client.insert_classified("", {"label": "rule", "summary": "", "importance": "P2"})
        assert doc_id > 0

    def test_insert_special_chars(self, client):
        """特殊字符不崩溃"""
        doc_id = client.insert_classified(
            "引号\"和'单引号'and<>&符号",
            {"label": "rule", "summary": "special", "importance": "P2"}
        )
        assert doc_id > 0

    def test_insert_long_content(self, client):
        """长内容正常处理"""
        long = "x" * 10000
        doc_id = client.insert_classified(long, {"label": "rule", "summary": "long", "importance": "P2"})
        assert doc_id > 0


class TestUpdateMemory:
    def test_update_memory(self, client):
        doc_id = client.insert_classified("original", {"label": "rule", "summary": "orig", "importance": "P2"})
        client.update_memory(doc_id, "updated summary", "P1", 90)
        mem = client.get_memory(doc_id)
        assert mem["summary"] == "updated summary"
        assert mem["importance"] == "P1"

    def test_update_nonexistent(self, client):
        """更新不存在的 doc_id 不崩溃"""
        client.update_memory(99999, "nope", "P2", 50)


class TestCountAndStats:
    def test_count(self, client, sample_data):
        assert client._cpp_storage.count_memories() >= 3

    def test_get_stats(self, client, sample_data):
        stats = client.get_stats()
        assert "total_memories" in stats or "count" in str(stats)


class TestTransactions:
    def test_begin_commit(self, client):
        client._cpp_storage.begin_transaction()
        client._cpp_storage.commit_transaction()

    def test_begin_rollback(self, client):
        client._cpp_storage.begin_transaction()
        client._cpp_storage.rollback_transaction()


class TestCleanup:
    def test_cleanup_empty_db(self, client):
        """空库 cleanup 不崩溃"""
        result = client.cleanup_memories()
        assert isinstance(result, (int, dict))
