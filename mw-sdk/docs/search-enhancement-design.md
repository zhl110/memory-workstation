# MW 搜索增强方案：关键词扩面

## 一、问题

当前搜索只匹配原始关键词。搜"部署"只匹配含"部署"的记忆，如果记忆里用的是"打包""发布""CI"，就搜不到。

## 二、目标

Agent 搜关键词时，根据场景拓展关联词，组合搜更多词，扩大覆盖范围。

## 三、方案

### 3.1 两层分工

| 层 | 职责 | 说明 |
|----|------|------|
| Agent 层 | 生成关联词 | 根据上下文判断"部署"关联"打包/发布/CI/CD"，拼成组合查询 |
| SDK 层 | 接受额外关键词 | `search()` 新增 `extra_keywords` 参数，传给 C++ 层合并到 FTS5 查询 |

### 3.2 查询格式

FTS5 查询用 **OR 语义**：匹配任一关键词的记忆都会返回，扩大覆盖范围。

```
原始查询：部署
拓展后：部署 OR 打包 OR 发布 OR CI OR Vercel
```

FTS5 原生语法：空格分隔 = AND，`OR` 分隔 = OR。我们用 OR。

### 3.3 调用链（目标状态）

两条搜索路径都支持 extra_keywords：

```
# 路径 1：enable_vector=True（默认）
client.search(query="部署", extra_keywords=["打包", "发布", "CI"])
  → Python 层：extra_str = "打包 OR 发布 OR CI"
  → _cpp_search.search_with_embedding(query, query_vec,
        top_k=..., enable_graph=...,
        graph_expand_top=..., graph_max_hops=...,
        extra_keywords=extra_str)
    → SearchEngine::search(query, embedding,
        top_k, enable_graph, graph_expand_top, graph_max_hops,
        extra_keywords)
      → search_impl(query, embedding,
          top_k, enable_graph, graph_expand_top, graph_max_hops,
          extra_keywords)
        → fts_search(query, fts_limit, extra_keywords)
          → storage_.fts_search(query, limit, extra_keywords)
            → FTS5 MATCH "部署 OR 打包 OR 发布 OR CI"

# 路径 2：enable_vector=False
client.search(query="部署", extra_keywords=["打包", "发布", "CI"])
  → _cpp_search.search(query,
        top_k=..., enable_vector=False, enable_graph=...,
        graph_expand_top=..., graph_max_hops=...,
        extra_keywords=extra_str)
    → SearchEngine::search(query,
        top_k, enable_vector, enable_graph,
        graph_expand_top, graph_max_hops,
        extra_keywords)
      → search(query, top_k, enable_vector, enable_graph,
          graph_expand_top, graph_max_hops, extra_keywords)
        → search_impl(...)
          → fts_search(query, fts_limit, extra_keywords)
            → FTS5 MATCH "部署 OR 打包 OR 发布 OR CI"
```

**注意**：extra_keywords 只作用于 FTS5 搜索，Vector 搜索保持原样（用原始 query 的 embedding）。当前 Vector 和 FTS5 搜索的 query 不一致是现有问题，不在本次改动范围内。

### 3.4 不需要的

- 不需要 boost 排序
- 不需要 label 字段
- 不需要 importance 过滤
- 不需要修改搜索算法

## 四、改动范围

### 4.1 C++ 层改动

| 文件 | 行号 | 改动 | 风险 |
|------|------|------|------|
| `cpp/include/mw_core.h` | 145 | `Storage::fts_search(query, limit)` → 加 `extra_keywords=""` | 低 |
| `cpp/src/storage_search.cpp` | 9 | `fts_search()` sanitize extra_keywords 并合并为 `MATCH query OR extra_keywords` | 低 |
| `cpp/include/search_engine.h` | 33 | `search(query, top_k, ...)` → 加 `graph_expand_top`/`graph_max_hops` 默认值 + `extra_keywords=""` | 低 |
| `cpp/include/search_engine.h` | 40 | `search(query, embedding, ...)` → 加 `graph_expand_top`/`graph_max_hops` 默认值 + `extra_keywords=""` | 低 |
| `cpp/include/search_engine.h` | 70 | `SearchEngine::fts_search(query, limit)` → 加 `extra_keywords=""` | 低 |
| `cpp/include/search_engine.h` | 91 | `search_impl()` → 加 `extra_keywords=""` | 低 |
| `cpp/include/search_engine.h` | 50 | `search()` → 加 `graph_expand_top`/`graph_max_hops` 默认值 + `extra_keywords=""` | 低 |
| `cpp/src/search_engine.cpp` | 86 | `fts_search(query, fts_limit)` → 传 `extra_keywords` | 低 |
| `cpp/src/search_engine.cpp` | 86 | `fts_limit` 从 `top_k + top_k/2` 改为 `top_k * 2`（OR 语义扩大覆盖） | 低 |
| `cpp/src/search_engine.cpp` | 180 | `storage_.fts_search()` 调用 → 传 `extra_keywords` | 低 |
| `cpp/src/search_engine.cpp` | 86 | `fts_limit` 从 `top_k + top_k/2` 改为 `top_k * 2`（OR 语义扩大覆盖） | 低 |
| `cpp/src/binding.cpp` | 84-88 | `fts_search` 绑定加 `extra_keywords` 参数 | 低 |
| `cpp/src/binding.cpp` | 370-378 | `search(query, ...)` 绑定加 `extra_keywords` + `graph_expand_top`/`graph_max_hops` 参数 | 低 |
| `cpp/src/binding.cpp` | 379-395 | `search_with_embedding` 绑定加 `extra_keywords` + `graph_expand_top`/`graph_max_hops` 参数 | 低 |

### 4.2 Python 层改动

| 文件 | 改动 | 风险 |
|------|------|------|
| `mw_sdk/client.py` | `search()` 加 `extra_keywords: list[str] \| None` 参数，合并后传给 C++。同时补齐 `graph_expand_top`/`graph_max_hops` 转发 | 低 |
| `mw_sdk/cli.py` | CLI 加 `--extra` 参数 | 低 |

### 4.3 不需要改的

| 文件 | 原因 |
|------|------|
| Entity 搜索 | 不涉及 |
| Vector 搜索 | 不涉及 |
| 数据库 schema | 不涉及 |
| Ingest 分类 | 另行处理 |

## 五、实现细节

### 5.1 client.py 改动

```python
def search(self, query: str, top_k: int = 10, extra_keywords: list[str] | None = None, ...):
    """搜索记忆
    
    Args:
        query: 搜索关键词
        extra_keywords: 额外关键词列表，传给 C++ 层在 FTS5 中用 OR 合并
    """
    # 合并 extra_keywords 为字符串，传给 C++
    extra_str = " OR ".join(extra_keywords) if extra_keywords else ""
    
    # 用原始 query 搜索，extra_keywords 在 C++ 层合并到 FTS5 查询
    # 注意：graph_expand_top/graph_max_hops 必须显式传值，不依赖 C++ 默认值
    if enable_vector:
        expanded = self._expand_query(query)
        query_vec = _cpp_core.storage_embed_text(self._cpp_storage, expanded)
        cpp_results = self._cpp_search.search_with_embedding(
            query, query_vec, top_k=top_k * 2,
            enable_graph=enable_graph,
            graph_expand_top=graph_expand_top,
            graph_max_hops=graph_max_hops,
            extra_keywords=extra_str
        )
    else:
        cpp_results = self._cpp_search.search(
            query, top_k=top_k * 2, enable_vector=False,
            enable_graph=enable_graph,
            graph_expand_top=graph_expand_top,
            graph_max_hops=graph_max_hops,
            extra_keywords=extra_str
        )
    ...
```

### 5.2 cli.py 改动

```python
p_search.add_argument("--extra", nargs="*", default=[], help="额外关键词列表，OR 语义扩大覆盖")
# ...
results = client.search(args.query, args.top_k, extra_keywords=args.extra or None, ...)
```

### 5.3 C++ 层改动

**Storage::fts_search()**：

```cpp
std::vector<SearchResult> Storage::fts_search(const std::string& query, int limit,
                                               const std::string& extra_keywords = "") {
    // Sanitize query（复用现有逻辑）
    std::string safe = query;
    for (char c : {'"', '\'', '-', '(', ')', ':', '^', '[', ']', '{', '}', '*', '~'}) {
        safe.erase(std::remove(safe.begin(), safe.end(), c), safe.end());
    }
    safe.erase(0, safe.find_first_not_of(" \t\n\r"));
    auto pos = safe.find_last_not_of(" \t\n\r");
    if (pos != std::string::npos) safe.erase(pos + 1);

    // Sanitize extra_keywords（同样去除 FTS5 特殊字符）
    std::string safe_extra = extra_keywords;
    for (char c : {'"', '\'', '-', '(', ')', ':', '^', '[', ']', '{', '}', '*', '~'}) {
        safe_extra.erase(std::remove(safe_extra.begin(), safe_extra.end(), c), safe_extra.end());
    }
    safe_extra.erase(0, safe_extra.find_first_not_of(" \t\n\r"));
    pos = safe_extra.find_last_not_of(" \t\n\r");
    if (pos != std::string::npos) safe_extra.erase(pos + 1);

    // 合并查询词
    std::string fts_query = safe;
    if (!safe_extra.empty()) {
        fts_query = safe + " OR " + safe_extra;
    }

    // FTS5 MATCH
    std::string sql = "SELECT doc_id, bm25(memory_fts, ...) AS score "
                      "FROM memory_fts WHERE memory_fts MATCH ? ORDER BY score LIMIT ?";
    ...
}
```

> **关键**：`extra_keywords` 必须经过与 `query` 相同的 sanitization，防止 FTS5 注入（如用户传入 `*` 或 `()`）。

**SearchEngine::fts_search()**：

```cpp
std::map<int, double> SearchEngine::fts_search(const std::string& query, int limit,
                                                const std::string& extra_keywords = "") {
    auto fts_results = storage_.fts_search(sanitize_fts5_query(query), limit, extra_keywords);
    ...
}
```

**search() 两个重载**：

```cpp
// 重载 1：无 embedding
std::vector<SearchResult> SearchEngine::search(const std::string& query, int top_k,
                                               bool enable_vector, bool enable_graph,
                                               int graph_expand_top = 3,
                                               int graph_max_hops = 2,
                                               const std::string& extra_keywords = "") {
    auto results = search_impl(query, {}, top_k, enable_graph,
                               graph_expand_top, graph_max_hops, extra_keywords);
    ...
}

// 重载 2：有 embedding
std::vector<SearchResult> SearchEngine::search(const std::string& query,
                                               const std::vector<float>& query_embedding,
                                               int top_k, bool enable_graph,
                                               int graph_expand_top = 3,
                                               int graph_max_hops = 2,
                                               const std::string& extra_keywords = "") {
    return search_impl(query, query_embedding, top_k, enable_graph,
                       graph_expand_top, graph_max_hops, extra_keywords);
}
```

**search_impl()**：

```cpp
std::vector<SearchResult> SearchEngine::search_impl(const std::string& query,
                                                     const std::vector<float>& query_embedding,
                                                     int top_k, bool enable_graph,
                                                     int graph_expand_top, int graph_max_hops,
                                                     const std::string& extra_keywords = "") {
    // 1. BM25 search — OR 语义扩大覆盖，适当放大候选集
    int fts_limit = std::max(top_k * 2, top_k + top_k / 2);
    auto bm25_scores = fts_search(query, fts_limit, extra_keywords);
    ...
}
```

> **fts_limit 调整**：OR 语义下 FTS5 匹配更多结果，`top_k * 2` 确保候选集足够大。

**binding.cpp**：

```cpp
// SearchEngine::search 绑定（重载 1：无 embedding）
.def("search", [](mw::SearchEngine& e, const std::string& query, int top_k,
                   bool enable_vector, bool enable_graph,
                   int graph_expand_top, int graph_max_hops,
                   const std::string& extra_keywords) {
    return e.search(query, top_k, enable_vector, enable_graph,
                    graph_expand_top, graph_max_hops, extra_keywords);
}, py::arg("query"), py::arg("top_k") = 10,
   py::arg("enable_vector") = false, py::arg("enable_graph") = false,
   py::arg("graph_expand_top") = 3, py::arg("graph_max_hops") = 2,
   py::arg("extra_keywords") = "")

// SearchEngine::search_with_embedding 绑定（重载 2：有 embedding）
.def("search_with_embedding", [](mw::SearchEngine& e, const std::string& query,
                                   const std::vector<float>& query_embedding,
                                   int top_k, bool enable_graph,
                                   int graph_expand_top, int graph_max_hops,
                                   const std::string& extra_keywords) {
    return e.search(query, query_embedding, top_k, enable_graph,
                    graph_expand_top, graph_max_hops, extra_keywords);
}, py::arg("query"), py::arg("query_embedding"),
   py::arg("top_k") = 10, py::arg("enable_graph") = false,
   py::arg("graph_expand_top") = 3, py::arg("graph_max_hops") = 2,
   py::arg("extra_keywords") = "")
```

### 5.4 Agent 使用方式

Agent 在调用 `mw search` 前，根据上下文生成关联词：

```
# Agent 内部判断
query = "部署"
context = "haixing ai 项目"
extra = ["打包", "发布", "CI", "Vercel"]

# 调用 SDK
mw search "haixing ai 部署" --extra 打包 发布 CI Vercel
```

Agent 的判断逻辑：
1. 识别当前场景（项目开发/调试/架构等）
2. 根据场景生成关联词（通用知识库 + 语义联想）
3. 组合查询，扩大覆盖

**场景→关联词映射**（以下内容必须写入 Only-MW-zhl skill）：

| 场景 | 可能的关联词 |
|------|-------------|
| 部署 | 打包、发布、CI、CD、上线、版本、环境变量 |
| 调试 | 报错、错误、排查、日志、异常、失败 |
| 架构 | 设计、选型、方案、重构、模块、解耦 |
| 写代码 | 编码、命名、注释、规范、函数、类 |
| 数据库 | 迁移、schema、索引、查询、慢查询 |

**Agent 搜索规范**（写入 skill）：
```
搜索时必须使用 --extra 参数拓展关键词。
示例：mw search "部署" --extra 打包 发布 CI CD
```

## 六、Bug Fix：Dedup 跨调用污染（前置条件）

### 6.1 问题

`SearchEngine::is_duplicate()` 使用 `recent_hashes_` 缓存摘要哈希，跨搜索调用持久化（5 分钟 TTL）。同一 SearchEngine 实例上连续两次搜索，第一次命中的摘要会在第二次被判为重复，导致第二次返回 0 结果。

**影响范围**：所有搜索模式（RRF/Hybrid）都受影响，不只是带 extra_keywords 的情况。

### 6.2 根因

```cpp
// search_engine.cpp
bool SearchEngine::is_duplicate(const std::string& summary) {
    auto hash = std::hash<std::string>{}(summary);
    auto now = std::chrono::steady_clock::now();
    // 清理过期条目...
    if (recent_hashes_.count(hash)) return true;  // ← 跨调用污染
    recent_hashes_[hash] = now;
    return false;
}
```

`recent_hashes_` 是成员变量，跨 search 调用不清空。

### 6.3 修复方案

**方案 A（推荐）：每次 search 调用结束后清空**

```cpp
// search_engine.cpp - search_impl() 末尾
std::vector<SearchResult> SearchEngine::search_impl(...) {
    // ... 现有逻辑 ...
    auto results = build_results(...);
    recent_hashes_.clear();  // ← 新增：每次搜索结束后清空
    return results;
}
```

**方案 B：改为单次搜索内集合去重**

在 `search_impl` 中用局部 `std::unordered_set` 替代成员变量 `recent_hashes_`，彻底消除跨调用状态。

**选择方案 A**：改动最小，风险最低。方案 B 更彻底但需要重构 dedup 逻辑。

### 6.4 改动范围

| 文件 | 改动 | 风险 |
|------|------|------|
| `cpp/src/search_engine.cpp` | `search_impl()` 末尾加 `recent_hashes_.clear()` | 低 |

### 6.5 验证

```bash
# 编译
cmake --build build --config Release

# 运行测试（包含 dedup 相关测试）
cd mw-sdk && python -m pytest tests/ -x -v

# 手动验证：连续两次搜索同一关键词，第二次应返回结果
python -c "
from mw_sdk import MemoryClient
c = MemoryClient()
r1 = c.search('测试', top_k=5)
r2 = c.search('测试', top_k=5)
print(f'第一次: {len(r1)} 条, 第二次: {len(r2)} 条')
assert len(r2) > 0, '第二次搜索返回 0 条，dedup 污染未修复'
"
```

## 七、Ingest 分类修复（前置条件）

### 6.1 问题

当前 `_auto_classify` 默认 `applicability="通用规则"`、`importance="P1"`、`weight=95`，几乎所有新记忆都是高权重。

### 6.2 方案

Agent 在调用 `mw ingest` 时，自己判断 applicability，不依赖默认值。

| applicability | 适用场景 | weight |
|---------------|----------|--------|
| 通用规则 | 跨项目适用的编码规范、禁止事项 | 95 |
| 场景知识 | 特定项目/技术栈的经验 | 50 |
| 会话痕迹 | 临时记录、讨论过程 | 20 |

### 6.3 旧记忆重新分类

**脚本位置**：`scripts/reclassify_memory.py`

**功能**：遍历所有记忆，按新标准重新分类 applicability 和 weight。

**运行方式**：
```bash
# 预览模式（不写入）
python scripts/reclassify_memory.py --dry-run

# 执行重新分类
python scripts/reclassify_memory.py --apply

# 验证结果
mw stats
mw search "测试" --explain
```

**判断逻辑**：
```python
UNIVERSAL_KEYWORDS = ["rule", "learn", "经验", "踩坑", "preference", "Bug"]

def is_universal(label: str, importance: str) -> bool:
    if importance not in ("P0", "P1"):
        return False
    return any(kw in label for kw in UNIVERSAL_KEYWORDS)

def reclassify(label: str, importance: str, content: str):
    if is_universal(label, importance):
        return "通用规则", "P1", 95
    elif any(kw in label for kw in ["经验", "踩坑", "Bug"]):
        return "场景知识", "P2", 50
    else:
        return "场景知识", "P2", 50
```

**依赖**：需要 `scripts/` 目录存在，不存在则创建。

## 八、测试

```python
def test_dedup_no_cross_call_contamination():
    """dedup 不应跨调用污染：连续两次搜索同一关键词都应返回结果"""
    r1 = client.search("测试", top_k=5)
    r2 = client.search("测试", top_k=5)
    assert len(r1) > 0, "第一次搜索无结果"
    assert len(r2) > 0, "第二次搜索返回 0 条，dedup 跨调用污染未修复"

def test_extra_keywords():
    """extra_keywords 应扩大搜索覆盖"""
    # 先清空 dedup 缓存，确保不受跨调用污染影响
    r1 = client.search("部署", top_k=10)
    r2 = client.search("部署", top_k=10, extra_keywords=["打包", "发布", "CI"])
    # r2 应该覆盖更多记忆（OR 语义）
    assert len(r2) >= len(r1)

def test_search_backward_compatible():
    """原有调用方式不受影响"""
    r = client.search("测试", 5)
    assert isinstance(r, list)
```

## 九、回滚方案

**C++ 改动回滚**：
- 快速回退：`git checkout` 恢复 C++ 文件，重新编译
- 无数据影响：不涉及数据库 schema 变更

**Ingest 分类回滚**：
- 旧记忆的 applicability/weight 可通过脚本恢复（保留原始值备份）

**部署回滚**：
- 恢复旧 .pyd 到两个位置，清缓存

## 十、执行步骤

### 第零阶段：Bug Fix - Dedup 跨调用污染

0. 修改 `cpp/src/search_engine.cpp`：`search_impl()` 末尾加 `recent_hashes_.clear()`
1. 删除 .obj 文件，重新编译
2. 运行测试验证 dedup 修复
3. 部署 .pyd 到两个位置
4. 清除 __pycache__

### 第一阶段：Ingest 分类修复

1. 更新 Only-MW-zhl skill：明确 Agent 分类职责
2. 更新 CLAUDE.md：MW ingest 规则
3. 创建 `scripts/` 目录（如不存在）
4. 编写 `scripts/reclassify_memory.py`
5. `--dry-run` 预览变更
6. 执行重新分类
7. 验证 weight 分布

### 第二阶段：搜索扩面

8. 修改 `cpp/include/mw_core.h`：`Storage::fts_search` 加 `extra_keywords` 参数
9. 修改 `cpp/src/storage_search.cpp`：`fts_search()` sanitize 并合并 extra_keywords
10. 修改 `cpp/include/search_engine.h`：`SearchEngine::fts_search` / `search` / `search_impl` 加 `extra_keywords` 参数
11. 修改 `cpp/src/search_engine.cpp`：各函数传递 extra_keywords，`search_impl` fts_limit 放大
12. 修改 `cpp/src/binding.cpp`：Python 绑定加 `extra_keywords` + 补齐 graph_expand_top/graph_max_hops 转发
13. 修改 `mw_sdk/client.py`：`search()` 加 `extra_keywords` 参数
14. 修改 `mw_sdk/cli.py`：CLI 加 `--extra` 参数
15. 删除 .obj 文件，重新编译
16. 部署 .pyd 到两个位置
17. 清除 __pycache__
18. 运行测试
19. 同步到工作区
20. 更新 Only-MW-zhl skill：教 Agent 如何拓展关键词
21. 更新描述性文件

## 十一、执行纪律

**每步必须**：
1. 执行前先 `mw search "相关关键词"` 检索相关经验
2. 执行后立即 `mw ingest` 记录踩坑经验
3. 每步完成后更新进度

**记录格式**：
```
mw ingest "MW进度：[步骤X] 已完成 | [具体做了什么] | 当前状态 | 下一步"
```
