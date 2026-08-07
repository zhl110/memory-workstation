"""测试记忆追加功能"""
import pytest
import sys
import os

# 添加 mw_sdk 到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class TestMerge:
    """测试记忆追加"""

    def test_append_to_memory(self, tmp_path):
        """测试 append_to_memory 方法"""
        from mw_sdk.client import MemoryClient
        from mw_sdk.cli_ingest import ingest_full

        db_path = str(tmp_path / "test.sqlite")
        with MemoryClient(db_path) as m:
            m.init_schema()

            # 写入第一条记忆
            doc_id = ingest_full(
                content='测试原始内容',
                classification={'label': 'experience', 'category': '技术', 'summary': '测试'},
                db_path=db_path,
                silent=True
            )
            assert doc_id > 0

            # 追加内容
            ok = m.append_to_memory(doc_id, '追加的内容', source='test')
            assert ok is True

            # 验证合并结果
            mem = m.get_memory(doc_id)
            assert '测试原始内容' in mem['summary']
            assert '追加的内容' in mem['summary']
            assert '---' in mem['summary']
