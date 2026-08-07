"""测试配置 — 基于当前 MemoryClient API"""
import pytest
from mw_sdk import MemoryClient


@pytest.fixture
def client(tmp_path):
    """文件数据库客户端（避免 :memory: 隔离问题）"""
    db_path = str(tmp_path / "test.db")
    m = MemoryClient(db_path)
    m.init_schema()
    yield m
    m.close()


@pytest.fixture
def sample_data(client):
    """写入 3 条示例数据，返回 doc_id 列表"""
    items = [
        ("测试内容：前端设计规范", {"label": "rule", "summary": "前端规范摘要", "importance": "P1", "content_category": "前端设计"}),
        ("测试内容：数据库迁移流程", {"label": "config", "summary": "数据库迁移摘要", "importance": "P2", "content_category": "工具类"}),
        ("测试内容：Bug修复记录", {"label": "bug", "summary": "Bug修复摘要", "importance": "P0", "content_category": "排错规范"}),
    ]
    doc_ids = []
    for content, cls in items:
        did = client.insert_classified(content, cls)
        doc_ids.append(did)
    return doc_ids
