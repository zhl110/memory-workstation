# Skill接入规范（Scale）

> 创建时间：2026-06-17
> 状态：方案定稿，待实施
> 本文档定义Skill如何调用Memory Workstation获取记忆数据

---

# 第一部分：接入方式

## 1.1 两种接入方式

| 方式 | 适用场景 | 说明 |
|------|----------|------|
| HTTP API | Python/JS等编程语言 | 调用 `http://127.0.0.1:8765/api/memory/search` |
| CLI命令行 | PowerShell/Shell脚本 | 一行命令查记忆 |

---

# 第二部分：HTTP API接入

## 2.1 核心检索接口

**地址：** `POST http://127.0.0.1:8765/api/memory/search`

**鉴权：** Header传 `token: YOUR_TOKEN`（查看config.toml获取）

### 必填参数（4个）

| 参数 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `query_text` | string | 你要找什么内容 | `"婚礼筹备"` |
| `category_filter` | string[] | 在哪个分类找，不传则搜全部 | `["planning_doc"]` |
| `top_k` | int | 给你几条结果，默认5，最大20 | `5` |
| `namespace` | string | 在哪个命名空间找，默认`"default"` | `"agent_main"` |

### 可选参数（6个，有默认值）

| 参数 | 类型 | 默认值 | 说明 | 什么时候传 |
|------|------|--------|------|------------|
| `valid_days` | int | 30 | 只找最近N天的记忆 | 要找更早的记忆时 |
| `sim_threshold` | float | 0.78 | 相似度低于此值丢弃 | 要更严格/宽松匹配时 |
| `weight_config` | object | `{"sim":0.7,"new":0.3}` | 排序偏好 | 要优先最新或最相关时 |
| `output_mode` | string | `"compact"` | compact=摘要，full=原文 | 要完整内容时 |
| `page` | int | 1 | 分页页码 | 批量导出时 |

### 返回格式

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "list": [
      {
        "id": 1,
        "content": "摘要或原文",
        "label": "planning_doc",
        "importance": "P1",
        "weight": 85,
        "similarity": 0.92,
        "create_time": "2026-06-17T10:00:00Z",
        "namespace": "agent_main",
        "relate_id": "task-001"
      }
    ],
    "total": 3,
    "page": 1,
    "has_more": false
  }
}
```

## 2.2 请求示例

### 简单查询（大部分场景这样用）

```python
import requests

resp = requests.post(
    "http://127.0.0.1:8765/api/memory/search",
    headers={"token": "YOUR_TOKEN"},
    json={
        "query_text": "婚礼筹备",
        "top_k": 5
    }
)
data = resp.json()
for item in data["data"]["list"]:
    print(f"[{item['label']}] {item['content']}")
```

### 高级查询（需要精确控制时）

```python
resp = requests.post(
    "http://127.0.0.1:8765/api/memory/search",
    headers={"token": "YOUR_TOKEN"},
    json={
        "query_text": "React hooks性能优化",
        "category_filter": ["planning_doc", "meta_rule"],
        "top_k": 10,
        "namespace": "agent_main",
        "valid_days": 90,
        "output_mode": "full",
        "weight_config": {"sim": 0.8, "new": 0.2}
    }
)
```

## 2.3 写入记忆

```python
# 单条写入
resp = requests.post(
    "http://127.0.0.1:8765/api/memory/add",
    headers={"token": "YOUR_TOKEN"},
    json={
        "content": "今天确认了V2方案，砍掉IPC和权限管控",
        "layer2_category": "planning_doc",
        "namespace": "agent_main",
        "source": "claude"
    }
)

# 批量写入
resp = requests.post(
    "http://127.0.0.1:8765/api/memory/add_batch",
    headers={"token": "YOUR_TOKEN"},
    json={
        "memories": [
            {"content": "内容1", "layer2_category": "planning_doc", "namespace": "agent_main"},
            {"content": "内容2", "layer2_category": "memory_layer", "namespace": "agent_main"}
        ]
    }
)
```

## 2.4 健康检测

```python
resp = requests.get(
    "http://127.0.0.1:8765/api/memory/health",
    headers={"token": "YOUR_TOKEN"}
)
status = resp.json()["data"]
# status["embed_model"] = "loaded" / "unloaded"
# status["classify_model"] = "loaded" / "unloaded" / "degraded"
```

## 2.5 错误处理

| code | 含义 | Skill应该怎么做 |
|------|------|-----------------|
| 0 | 成功 | 正常处理数据 |
| 401 | Token错误 | 检查config.toml里的token |
| 404 | 无匹配记忆 | 返回"无相关历史记忆" |
| 429 | 限流（每秒10次） | 等1秒重试 |
| 503 | 模型离线 | 已降级返回基础数据，可用但不精确 |

---

# 第三部分：CLI命令行接入

## 3.1 查询记忆

```powershell
# 基础查询
memory-search --query "婚礼清单" --top 5

# 指定分类
memory-search --query "React hooks" --category planning_doc --top 10

# 指定命名空间
memory-search --query "项目计划" --namespace agent_main --top 5

# 要全文
memory-search --query "配置参数" --output full --top 3
```

## 3.2 写入记忆

```powershell
# 单条写入
memory-add --content "今天的工作记录" --category planning_doc --namespace agent_main

# 从文件写入
memory-add --file "D:\notes\meeting.md" --category planning_doc
```

## 3.3 输出格式

CLI输出极简文本，方便脚本解析：

```
【相关历史记录，共3条】
1. [planning_doc] V2方案已定稿，砍掉IPC和权限管控
2. [planning_doc] 扫描范围从全盘缩小到Agent目录
3. [meta_rule] 分类prompt改为两步输出
```

---

# 第四部分：权重配置

## 4.1 weight_config参数

控制搜索结果的排序偏好：

| 设置 | 效果 | 适合场景 |
|------|------|----------|
| `{"sim":0.7,"new":0.3}` | 默认，70%相关度+30%新鲜度 | 通用场景 |
| `{"sim":0.3,"new":0.7}` | 最新的排前面 | 规划类，找最近的计划 |
| `{"sim":0.9,"new":0.1}` | 最相关的排前面 | 知识检索，找精确内容 |

## 4.2 使用示例

```python
# 找最近的规划（优先新鲜度）
resp = requests.post(url, json={
    "query_text": "本周计划",
    "weight_config": {"sim": 0.3, "new": 0.7},
    "top_k": 5
})

# 找最精确的技术文档（优先相关度）
resp = requests.post(url, json={
    "query_text": "LLM配置参数",
    "weight_config": {"sim": 0.9, "new": 0.1},
    "top_k": 3
})
```

---

# 第五部分：关联记忆查询

## 5.1 relate_id字段

同一事件/任务的多条记忆有相同的relate_id。

## 5.2 查询关联记忆

```python
# 先查主记忆
resp = requests.post(url, json={
    "query_text": "V2优化方案",
    "top_k": 1
})
main_memory = resp.json()["data"]["list"][0]
relate_id = main_memory.get("relate_id")

# 再用relate_id查关联记忆
if relate_id:
    resp = requests.post(url, json={
        "query_text": "",
        "namespace": main_memory["namespace"],
        "top_k": 20
    })
    # 过滤出相同relate_id的
    related = [m for m in resp.json()["data"]["list"] if m.get("relate_id") == relate_id]
```

---

# 第六部分：输出模式

## 6.1 compact模式（默认）

返回预压缩摘要，每条几十字，适合日常查询：

```json
{
  "content": "V2在V1基础上新增智能分类管道和HTTP API完善"
}
```

## 6.2 full模式

返回完整原文，适合需要详细信息时：

```json
{
  "content": "# memory-workstation V2 优化方案\n\n## 第一部分：核心问题与目标\n\nV1存在的问题：\n1. 扫描范围过大..."
}
```

## 6.3 选择建议

| 场景 | 用哪个 | 理由 |
|------|--------|------|
| 日常聊天回忆 | compact | 省上下文，快速回忆 |
| 需要具体参数/配置 | full | 需要完整信息 |
| 批量导出/复盘 | full | 需要原文 |

---

# 第七部分：降级策略

## 7.1 模型离线时

| 丢失模型 | API行为 | Skill感知 |
|----------|---------|-----------|
| nomic离线 | 返回404或基础分类结果 | 正常使用，只是没有向量匹配 |
| LLM API离线 | 返回503但附带基础数据 | 可用，摘要可能不精确 |
| 两套全离线 | 返回基础文本片段 | 可用，无语义排序 |

## 7.2 Skill如何检测

```python
# 查询前先检测健康
health = requests.get("http://127.0.0.1:8765/api/memory/health", headers={"token": token})
status = health.json()["data"]

if status["embed_model"] == "unloaded":
    # 降级：不用向量匹配，只用分类过滤
    resp = requests.post(url, json={
        "query_text": query,
        "category_filter": ["planning_doc"],  # 必须指定分类
        "top_k": 5
    })
else:
    # 正常查询
    resp = requests.post(url, json={"query_text": query, "top_k": 5})
```

---

# 第八部分：接入检查清单

Skill接入前确认：

- [ ] 知道Memory Workstation的API地址（默认 `http://127.0.0.1:8765`）
- [ ] 知道Token（查看config.toml的 `[api].token`）
- [ ] 确认Skill需要查询的namespace
- [ ] 确认Skill需要的分类（category_filter）
- [ ] 测试健康检测接口 `/api/memory/health`
- [ ] 测试基础查询接口 `/api/memory/search`
- [ ] 处理好错误码（401/404/429/503）
