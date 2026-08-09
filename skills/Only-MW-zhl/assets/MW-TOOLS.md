# MW-TOOLS.md — CLI 工具用法与陷阱

> mw CLI 的常见用法、参数坑和最佳实践。每次会话启动必读。

## 通用

- 数据库路径由 SDK 自动确定（`get_agent_db()`），不用手动指定
- 所有命令支持 `--help` 查看完整参数
- **MW SDK 必须用 Python 3.13 运行**：`C:/Users/<USERNAME>/AppData/Local/Programs/Python/Python313/python.exe`。PATH 上的 `python` 是 3.12，加载不了 cp313 编译的 `mw_core.cp313-win_amd64.pyd`

## mw ingest — 写入

```
mw ingest "内容" --label 规则 --scope global --summary "一句话总结"
```

- `--label` 可选值：规则/偏好/架构决策/项目记录/bug-fix/经验/配置
- `--scope` 可选值：global/project/session
- 写入前**必须**先 `mw search` 判断是否合并
- 用户消息中出现纠正/决策/偏好信号时**立即写入，不要等**

## mw search — 搜索

- `mw search "关键词"` — FTS5 + vector + graph 三路融合
- 结果太少 → `--graph` 展开图谱
- 准确率优先：确切关键词先 grep MD 文件
- 模糊/关联优先：不确定关键词用 `mw search`

## mw update — 更新

```
mw update <doc_id> --scope project --category xxx
```

- 只支持 `--scope`/`--category`/`--keywords`，**没有** `--label`
- 更新内容用 `mw ingest` 重新 ingest 后用 doc_id 关联

## mw promote — 批量晋升

```
mw promote --min-weight 100 --min-access 10
```

- 这是**批量**命令，不是单文档操作
- 单条晋升用 `mw update <doc_id> --scope project`

## 常见陷阱

| 陷阱 | 后果 | 正确做法 |
|------|------|---------|
| 用了 `--label` 但拼错 | label 写错，搜不到 | 检查 label 可选值 |
| 以为 promote 是单条命令 | 无效果 | 单条用 update，批量用 promote |
| session 内容不晋升 | 跨会话后搜不到 | 有价值的内容立刻 `mw update --scope project` |
| 写入前不搜索 | 重复记忆 | 走搜索优先协议 |
| 新写入记忆默认 `mw search` 搜不到 | 只跑 `mw rebuild-fts5` 不够，向量索引未包含新 doc | 验证失败时执行 `mw vector-build`，再 `mw search` 确认 |
| 用 PATH 上的 `python`（3.12）跑 SDK 源码 | C++ 引擎 `DLL load failed` / `is_available()=False`，向量搜索不可用 | 用 Python 3.13：`C:/Users/<USERNAME>/AppData/Local/Programs/Python/Python313/python.exe` |
| 用 `raw_text_snippet` 判断内容是否写坏 | 该列对所有文档都是坏字节（非 UTF-8），误判内容损坏而误删 | 看 `memory_classify.compact_content`，含 U+FFFD 才算真损坏 |
| `mw forget` 删过某 doc 后重新 ingest 复用同一 doc_id | 新文档 `document_files.is_deleted=1`，C++ 引擎搜索完全隐形（但 SQLite FTS5 直查 MATCH 能命中） | 手动 `UPDATE document_files SET is_deleted=0 WHERE id=<doc_id>` 复位；误删重写优先用全新 doc_id，重写后必须 `mw search` 验证 |
