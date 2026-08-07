"""写入流程集成测试 — 验证完整流程"""
import pytest
import sys
import os
import tempfile
import shutil

# 添加 mw_sdk 到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class TestIngestIntegration:
    """写入流程集成测试"""

    @pytest.fixture
    def temp_dir(self):
        """创建临时目录"""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir)

    def test_full_workflow(self, temp_dir):
        """测试完整工作流程：写入 → 搜索 → 追加 → 验证"""
        from mw_sdk.cli_ingest import ingest_full
        from mw_sdk.client import MemoryClient

        db_path = os.path.join(temp_dir, "test.sqlite")

        # 步骤 1：写入第一条记忆
        doc_id1 = ingest_full(
            content='MW 搜索增强：背景是需要支持 extra_keywords 扩大覆盖',
            classification={
                'label': 'experience',
                'category': '技术',
                'summary': 'MW搜索增强背景'
            },
            db_path=db_path,
            silent=True
        )
        assert doc_id1 > 0
        print(f"步骤 1：写入第一条记忆 #{doc_id1}")

        # 步骤 2：写入第二条记忆（独立记录）
        doc_id2 = ingest_full(
            content='MW 搜索增强：踩坑 LIKE fallback 不触发',
            classification={
                'label': 'experience',
                'category': '技术',
                'summary': 'MW搜索增强踩坑'
            },
            db_path=db_path,
            silent=True
        )
        assert doc_id2 > 0
        print(f"步骤 2：写入第二条记忆 #{doc_id2}")

        # 步骤 3：使用 append_to_memory 追加
        with MemoryClient(db_path) as m:
            ok = m.append_to_memory(doc_id1, '当前状态：功能已完成，待测试', source='test')
            assert ok is True
            print("步骤 3：append_to_memory 追加成功")

            # 步骤 4：验证最终结果
            mem = m.get_memory(doc_id1)
            content = mem['summary']
            print(f"\n最终内容：\n{content}")

            # 验证包含关键信息
            assert '背景' in content
            assert '当前状态' in content

        print("\n✅ 集成测试通过：完整工作流程验证成功")

    def test_search_and_append(self, temp_dir):
        """测试搜索后追加"""
        from mw_sdk.cli_ingest import ingest_full
        from mw_sdk.client import MemoryClient

        db_path = os.path.join(temp_dir, "test.sqlite")

        # 写入几条相关记忆
        for i in range(3):
            ingest_full(
                content=f'MW 测试记忆 {i+1}：测试内容',
                classification={
                    'label': 'test',
                    'category': '测试',
                    'summary': f'测试 {i+1}'
                },
                db_path=db_path,
                silent=True
            )

        # 搜索相关记忆
        with MemoryClient(db_path) as m:
            results = m.search('MW 测试', top_k=5)
            print(f"搜索结果：{len(results)} 条")

            # 验证搜索结果
            assert len(results) > 0

            # 追加到第一条
            if len(results) >= 2:
                doc_id1 = results[0]['doc_id']
                doc_id2 = results[1]['doc_id']

                mem2 = m.get_memory(doc_id2)
                ok = m.append_to_memory(doc_id1, mem2['summary'], source='test')
                assert ok is True
                print(f"追加 #{doc_id2} 到 #{doc_id1} 成功")

        print("\n✅ 搜索后追加测试通过")
