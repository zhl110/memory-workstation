"""MemoryClient核心功能测试"""


def test_insert_and_get(client):
    """测试写入和读取"""
    doc_id = client.insert_classified('test', {'label': 'test', 'summary': 'test'})
    mem = client.get_memory(doc_id)
    assert mem is not None
    assert mem['label'] == 'test'


def test_search(client, sample_data):
    """测试搜索"""
    results = client.search('测试')
    assert len(results) > 0


def test_update_memory(client, sample_data):
    """测试更新"""
    doc_id = sample_data[0]
    client.update_memory(doc_id, 'updated summary')
    mem = client.get_memory(doc_id)
    assert mem['summary'] == 'updated summary'


def test_insert_cross_refs(client, sample_data):
    """测试交叉引用"""
    refs = [{'related_doc_id': sample_data[1], 'relation_type': 'related'}]
    count = client.insert_cross_refs(sample_data[0], refs)
    assert count == 1


def test_auto_cross_ref(client, sample_data):
    """测试自动交叉引用"""
    count = client.auto_cross_ref(sample_data[0], top_k=2)
    assert count >= 0


def test_insert_empty_content(client):
    """测试空内容写入"""
    doc_id = client.insert_classified('', {'label': 'empty', 'summary': ''})
    assert doc_id > 0


def test_insert_long_content(client):
    """测试长文本写入"""
    long_text = 'x' * 100000
    doc_id = client.insert_classified(long_text, {'label': 'long', 'summary': 'long'})
    assert doc_id > 0


def test_insert_special_chars(client):
    """测试特殊字符写入"""
    text = '特殊字符：@#$%^&*()_+{}|:"<>?'
    doc_id = client.insert_classified(text, {'label': 'special', 'summary': 'special'})
    assert doc_id > 0


def test_get_nonexistent_memory(client):
    """测试读取不存在的记忆"""
    mem = client.get_memory(999999)
    assert mem is None


def test_update_nonexistent_memory(client):
    """测试更新不存在的记忆（现在返回False）"""
    result = client.update_memory(999999, 'test')
    assert result == False


def test_update_memory_success(client, sample_data):
    """测试更新存在的记忆返回True"""
    doc_id = sample_data[0]
    result = client.update_memory(doc_id, 'updated summary')
    assert result == True
    mem = client.get_memory(doc_id)
    assert mem['summary'] == 'updated summary'
