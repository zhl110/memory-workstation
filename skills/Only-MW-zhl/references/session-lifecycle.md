# 会话生命周期

> Agent 会话的完整生命周期管理：存档 → 恢复 → 晋升 → 归档。每条 session 快照覆盖"一小段完整工作"。

## 生命周期概览

```
会话开始 ──► 工作中 ──► 存档点 ──► 新会话 ──► 恢复
                    │              │
                    │              └── 找到存档 → 接续工作
                    │              └── 没找到 → 从头开始
                    │
                    └── 晋升（有价值内容 → project/global）
                    └── 老化（不访问 → weight 下降 → 沉底）
                    └── 归档（过期 → cold → 可清理）
```

## 一、会话存档（Save）

### 触发信号

| 信号 | 触发时机 | 写入格式 |
|------|---------|---------|
| 子任务完成 | 一个独立可交付的子任务做完 | `mw ingest "会话快照: {项目} 进度={} 阻碍={} 下一步={}" --scope session --label 会话快照 --keywords 会话快照 {项目}` |
| Context compact 前 | hooks 或即将压缩上下文时 | 同上（必写） |
| 用户说"先到这" | 结束本轮工作 | 同上（包含完整待办清单） |
| 每轮对话 | ❌ 不写 | 太频繁，噪音 |
| 纯探索无结论 | ❌ 不写 | 没价值 |

> **频率控制**：一个子任务 = 一条。不每轮对话写，不每次修小 typo 写。

### 快照内容规范

```json
title: "会话快照: MW 搜索权重修复"
compact_content: "完成了搜索权重的 C++ 编译问题排查，发现 workaround 方案。下一步：测试 workaround 是否正常工作。"
keywords: ["会话快照", "搜索权重", "recall", "0.2 0.6 修正"]
scope: "session"
label: "会话快照"
```

必须包含：
- **项目名**（在 title/compact_content/keywords 中都出现）
- **当前进度**（做了什么 + 到什么阶段了）
- **下一步**（让下个会话能直接接上）

## 二、会话恢复（Restore）

### 恢复流程

新会话启动或 context 压缩后：

```bash
# Step 1: 读最新 3 条会话快照的摘要链
mw list -c "会话快照" -n 3

# Step 2: 读项目当前进展
mw search "project <项目名>" -n 3

# Step 3: 结果不足则展开图谱
mw search "project <项目名>" --graph
```

### 汇报格式

```
上次在修 <X>，进展到 <Y>，下一步是 <Z>
```

找不到 → "找不到之前的上下文，从头开始"

### 摘要链恢复

- 读最新 3 条的 `compact_content`（不读完整 content），形成 `之前做 A → 遇到 B → 现在是 C` 的摘要链
- 如果最新 3 条跨不同项目 → 按时间分段，分别汇报每段

## 三、晋升路径（Promote）

### 判断准则

| 条件 | 动作 |
|------|------|
| 知识点在 session 快照中出现 2+ 次 | → 晋升：持续的是规律 |
| session 快照中有可复用的通用经验 | → 晋升 |
| session 快照中只有一次性上下文 | → 不晋升，等待老化 |

### 晋升命令

```bash
# 单条：session → project（常用）
mw update <doc_id> --scope project

# 批量：筛选达标 project → global
mw promote --min-weight 50 --min-access 5
```

## 四、老化与归档（Age & Archive）

session 记忆默认不需要手动清理：

| 机制 | 效果 |
|------|------|
| `mw decay` 权重衰减 | 不访问 → weight 下降 → 搜不到 |
| `mw evolve` 冷热升降级 | 不访问 → cold → 不参与常规搜索 |
| `mw cleanup` | 清理 cold 且无关联的 session 记忆（手动） |

**核心原则**：session 记忆会自然沉底，重要的转 project/global，不重要的等它自己消失。

## 五、WAL 集成

SKILL.md [WAL 信号表](../SKILL.md) 新增存档信号：

| 信号 | 识别关键词 | 写入动作 |
|------|-----------|---------|
| 📦 会话存档 | 子任务完成 / context compact / "先到这" | `mw ingest "会话快照: {项目} 步骤=Y" --scope session --label 会话快照` |

Agent 检测到这些信号时，**先写快照再回复**，确保关闭前状态已持久化。

---

> 参见：SKILL.md [跨会话上下文恢复协议](../SKILL.md#跨会话上下文恢复协议)
