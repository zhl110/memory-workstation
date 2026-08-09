# MW Core Engine — C++ 核心引擎

MW (Memory Workstation) 的 C++ 核心搜索引擎，替代 Python 实现。

## 性能对比

| 操作 | Python | C++ | 加速比 |
|------|--------|-----|--------|
| 搜索 | ~50ms | ~0.75ms | **66x** |
| 图构建 (networkx) | ~24000ms | 5.21ms | **4610x** |
| BFS 遍历 | ~10ms | 0.29ms | **34x** |
| FTS5 搜索 | ~5ms | 0.30ms | **16x** |

## 架构

```
mw_sdk/_core/
├── __init__.py          # Python 包装层
└── mw_core.*.pyd        # C++ 编译产物

cpp/
├── CMakeLists.txt       # 构建配置
├── include/
│   ├── mw_core.h        # Storage 类声明
│   ├── search_engine.h  # 融合搜索引擎
│   ├── graph_engine.h   # 图遍历引擎
│   └── rules.h          # 规则引擎
├── src/
│   ├── binding.cpp      # pybind11 绑定（7 个绑定函数，按域拆分）
│   ├── storage.cpp      # Storage 核心：生命周期/schema/stats/health_check
│   ├── storage_search.cpp   # FTS5/LIKE/Entity 搜索
│   ├── storage_ingest.cpp   # CRUD + batch_ingest + access + embedding
│   ├── storage_evolution.cpp # 进化系统 + always_load + cleanup
│   ├── search_engine.cpp    # 4路融合搜索
│   ├── graph_engine.cpp     # BFS/Dijkstra
│   └── rules.cpp            # 规则/实体查询
└── third_party/
    └── sqlite3/         # SQLite amalgamation
```

## 构建

```bash
cd mw-sdk/cpp
build.bat
```

## 使用

```python
from mw_sdk._core import Storage, SearchEngine, GraphEngine, Rules

# 连接数据库（三个Agent共用 meta_agents.sqlite）
storage = Storage("path/to/meta_agents.sqlite")

# 搜索
search = SearchEngine(storage)
results = search.search("query", top_k=10)

# 图遍历
graph = GraphEngine(storage)
graph.build()
neighbors = graph.get_neighbors(14)

# 规则查询
rules = Rules(storage)
entities = rules.get_entities(limit=10)
```

## 依赖

- MSVC (Visual Studio Build Tools)
- CMake 3.15+
- pybind11
- Python 3.13+

## 移除的依赖

- ~~networkx~~ (C++ 自实现 BFS/Dijkstra)
- ~~zvec~~ (C++ 自实现 HNSW)
