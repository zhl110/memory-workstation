"""test_dataset.py — 数据集操作：batch_ingest / FTS5 索引 / 跨表一致性"""
import pytest
import sqlite3


class TestBatchIngest:
    def test_batch_ingest(self, client):
        """batch_ingest 写入 + FTS5 索引"""
        result = client._cpp_storage.batch_ingest(
            "batch test content for search",
            {"label": "rule", "importance": "P1", "weight": "80",
             "summary": "batch summary", "content_category": "tools",
             "compact_content": "override", "keywords": "batch test"},
            [], auto_refs=False
        )
        assert result.doc_id > 0

    def test_batch_compact_content(self, client):
        """batch_ingest 的 compact_content 存原始内容而非 summary"""
        result = client._cpp_storage.batch_ingest(
            "full content here for verification",
            {"label": "rule", "importance": "P1", "weight": "80",
             "summary": "short summary", "content_category": "tools",
             "compact_content": "override", "keywords": "test"},
            [], auto_refs=False
        )
        conn = client.get_conn()
        row = conn.execute(
            "SELECT compact_content FROM memory_classify WHERE doc_id=?",
            (result.doc_id,)
        ).fetchone()
        assert "full content here" in row[0]


class TestFTS5Index:
    def test_fts5_count_matches_classify(self, client, sample_data):
        """FTS5 索引数 = classify 行数"""
        conn = client.get_conn()
        cls_count = conn.execute("SELECT COUNT(*) FROM memory_classify").fetchone()[0]
        fts_count = conn.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0]
        assert cls_count == fts_count, f"classify={cls_count} != fts={fts_count}"

    def test_rebuild_fts5(self, client, sample_data):
        """rebuild_fts5 重建后数量一致"""
        client._cpp_storage.rebuild_fts5()
        conn = client.get_conn()
        cls_count = conn.execute("SELECT COUNT(*) FROM memory_classify").fetchone()[0]
        fts_count = conn.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0]
        assert cls_count == fts_count


class TestCrossTableConsistency:
    def test_entity_count(self, client, sample_data):
        """entity 数量正确"""
        client._cpp_storage.insert_entities(sample_data[0], [("MW", "system")])
        count = client._cpp_storage.count_entities()
        assert count >= 1

    def test_cross_ref_count(self, client, sample_data):
        """cross_ref 数量正确 — 用同一个 C++ 连接写和查"""
        n = client._cpp_storage.insert_cross_refs(sample_data[0], [
            {"related_doc_id": str(sample_data[1]), "relation_type": "related", "note": ""}
        ])
        count = client._cpp_storage.count_all_cross_refs()
        assert count >= 1, f"insert returned {n}, count_all_cross_refs={count}"
