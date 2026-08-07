"""MW Core Engine — C++ 核心搜索/索引引擎

Python 胶水层，导入 C++ 编译的 _core 模块。

使用方法:
    from mw_sdk._core import Storage, SearchEngine, GraphEngine, Rules

    storage = Storage("path/to/db.sqlite")
    search = SearchEngine(storage)
    results = search.search("query", top_k=10)
"""
try:
    from . import mw_core
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False
    mw_core = None


def is_available() -> bool:
    """C++ 引擎是否可用"""
    return _AVAILABLE


def version() -> str:
    """返回 C++ 引擎版本号"""
    if _AVAILABLE:
        return mw_core.version()
    return "unavailable"


# 导出所有类（C++ 引擎可用时）
if _AVAILABLE:
    Storage = mw_core.Storage
    SearchEngine = mw_core.SearchEngine
    SearchConfig = mw_core.SearchConfig
    SearchMode = mw_core.SearchMode
    GraphEngine = mw_core.GraphEngine
    Rules = mw_core.Rules
    SearchResult = mw_core.SearchResult
    MemoryRecord = mw_core.MemoryRecord
    GraphEdge = mw_core.GraphEdge
    TraverseResult = mw_core.TraverseResult
    GraphStats = mw_core.GraphStats
    Rule = mw_core.Rule
    Entity = mw_core.Entity
    CrossRefCandidate = mw_core.CrossRefCandidate
    HNSWIndex = mw_core.HNSWIndex

    # Helper functions
    storage_get_memory_embedding = mw_core.storage_get_memory_embedding
    storage_get_all_embeddings = mw_core.storage_get_all_embeddings
    storage_load_embedding = mw_core.storage_load_embedding
    storage_has_embedding = mw_core.storage_has_embedding
    storage_embed_text = mw_core.storage_embed_text
    search_engine_build_vector_index = mw_core.search_engine_build_vector_index
    search_engine_add_vector = mw_core.search_engine_add_vector
    search_engine_vector_search = mw_core.search_engine_vector_search
    search_engine_has_vector_index = mw_core.search_engine_has_vector_index
    search_engine_save_vector_index = mw_core.search_engine_save_vector_index
    search_engine_load_vector_index = mw_core.search_engine_load_vector_index
