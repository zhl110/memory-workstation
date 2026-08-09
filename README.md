# Memory Workstation 全自动记忆工作台

> 多接口大模型记忆管理系统，自动扫描 Claude/Codex 记忆文件，AI分类归档，供随时调取。
> 支持 DeepSeek / Claude / Ollama / 本地模型等多种接入方式。

---

## 首次部署（新机器全链路）

部署 = 三件套：**exe（桌面软件）+ skill（Agent 记忆技能）+ SDK（数据引擎）**。以下按顺序执行。

### 前提

- **Python 3.13**（铁律）：全程用 `C:/Users/周海龙/AppData/Local/Programs/Python/Python313/python.exe`，严禁用 PATH 上默认的 3.12（无法加载 cp313 的 `.pyd`）
- **网络**：安装 pip 依赖、下载向量模型时需联网

### 第一步：安装 SDK（数据引擎）

```bash
# 源码区安装（唯一可改代码的位置）
pip install -e D:\mycode\memory-workstation\mw-sdk\
# 或仅运行时安装
pip install -r D:\mycode\memory-workstation\requirements.txt
```

验证：

```bash
# 用 3.13，勿用 PATH 默认 python
C:\Users\周海龙\AppData\Local\Programs\Python\Python313\python.exe -c "from mw_sdk import MemoryClient; print('ok')"
```

> 装机注意：SDK 是纯 Python + C++ 编译产物（`mw_core.cp313-win_amd64.pyd`），**零 LLM 依赖**。LLM 由上层（Claude/Codex/exe）各自提供。

### 第二步：初始化数据库

```bash
C:\Users\周海龙\AppData\Local\Programs\Python\Python313\python.exe -c "from mw_sdk import MemoryClient; c=MemoryClient(r'D:\MemoryWorkstation\.memory-workstation\meta_agents.sqlite'); c.init_schema(); c.close(); print('db ok')"
```

- 数据库位置：`D:\MemoryWorkstation\.memory-workstation\meta_agents.sqlite`（Agent 共享库）
- exe 专用：`meta.sqlite`（不同库，互不跨搜）

### 第三步：部署 skill（Agent 技能）

```bash
# Claude Code
# 已存在的 junction 指向 D:\mycode\agent-hub\skills\Only-MW-zhl，新机器需重建：
New-Item -ItemType Junction -Path "$HOME\.claude\skills\Only-MW-zhl" `
  -Target "D:\mycode\agent-hub\skills\Only-MW-zhl"

# 安装记忆 Hooks（可选但推荐，自动触发记忆写入）
python D:\mycode\agent-hub\skills\Only-MW-zhl\scripts\install-hooks.py
```

### 第四步：验证部署

```bash
# 环境检查（依赖 + 数据库文件存在性），退出码 0=正常
python D:\mycode\agent-hub\skills\Only-MW-zhl\scripts\check-deps.py

# 功能验证
mw search "测试" -n 1      # 搜索正常
mw stats                    # 统计正常
mw vector-status            # 向量索引状态（可选，无索引时自动建）
```

### 首次部署常见卡点

| 现象 | 原因 | 解决 |
|------|------|------|
| `DLL load failed` / `is_available()=False` | 用了 Python 3.12 跑 3.13 编译的 `.pyd` | 改用 3.13 绝对路径执行 |
| `mw: command not found` | SDK 未 pip install 或 PATH 未含 Scripts | `pip install -e D:\...\mw-sdk`（`mw` 命令由 entry point 提供） |
| `meta_agents.sqlite` 不存在 | 未执行 init_schema() | 跑第二步建库 |
| skill 目录空 / 读不到 | junction 未建立 | 重建第三步的 Junction，并确认 `D:\mycode\agent-hub\skills\Only-MW-zhl` 存在 |
| 向量搜索无结果 | 向量索引未构建 | `mw vector-build`（或 search 时自动建） |

---

## 快速开始

### 1. 依赖

```bash
pip install -r requirements.txt   # 第三方依赖全集见文件内注释
```

| 类别 | 依赖 | 用途 |
|------|------|------|
| 核心框架 | `pystray` / `watchdog` | 系统托盘 / 文件监控 |
| HTTP | `fastapi` / `uvicorn` / `requests` | API 服务 / 请求 |
| 向量存储 | `lancedb` / `pyarrow` | 向量数据库 |
| 本地 LLM | `llama-cpp-python` | GGUF 本地模型（云端接入可省略） |
| NLP | `jieba` / `tiktoken` / `transformers` / `onnxruntime` | 中文分词 / token 计数 / 可选模型 |
| MCP | `mcp` | MCP 服务器 |
| 其他 | `Pillow` / `tomli` / `ttkbootstrap` | 图片 / TOML / UI 主题 |

环境可用性检查（自动扫描依赖 + 数据库文件）：

```bash
python ~/.claude/skills/Only-MW-zhl/scripts/check-deps.py
# 退出码 0=正常 / 1=有警告可降级 / 2=有错误无法使用
```

### 2. 配置

复制 `config.toml`，确保 `[llm.classify]` 配置正确：

```toml
[llm.classify]
provider = "custom_api"                      # 接入方式
api_key = "sk-你的密钥"                     # API密钥（填入真实密钥）
api_model = "deepseek-v4-flash"              # 模型名
api_base_url = "http://127.0.0.1:4000/v1/chat/completions"  # LiteLLM网关
```

也支持 cc-switch / mimo serve / Ollama 等多种后端，只需改 `provider` 和对应配置。

### 3. 运行

```bash
python -m src.main
```

程序自动：
- 两阶段扫描：Phase 1 Gate 快速筛选 → Phase 2 LLM 批量分类
- 生成记忆导出到 `memory_export/`
- 启动 MCP + HTTP API 服务
- 系统托盘显示

### 4. 使用 API

```bash
# 查询记忆
curl -H "token: YOUR_TOKEN" http://127.0.0.1:8765/api/memory/long

# 查看状态
curl -H "token: YOUR_TOKEN" http://127.0.0.1:8765/api/system/status
```

Token 在 `config.toml` 中查看，首次运行自动生成。

---

## 架构说明

### 两阶段扫描管线

```
文件变更 ──→ Gate 1（记忆文件检测）
                │ 通过
                ↓
           Gate 2（有效内容检查）
                │ 通过
                ↓
           入库（SQLite + hash去重）
                │
                ↓
           Phase 2: LLM 批量分类 + 摘要 + 向量嵌入
                │
                ↓
           导出 memory_export/ + 规则提取
```

### 支持的后端

| 后端 | provider | 说明 |
|------|----------|------|
| LiteLLM 网关 | `custom_api` | 推荐。统一管理多模型，localhost:4000 |
| Claude/Codex 内部 | `claude_free` / `codex_free` | 复用内部认证，无需 Key |
| cc-switch | `cc_switch` | 本地代理，127.0.0.1:15721 |
| mimo serve | `mimo_free` | 本地 HTTP 服务，4096 端口 |
| Ollama | `ollama` | 本地 Ollama 实例 |
| 本地模型 | 配置 model_path | 支持 GGUF/ONNX/Transformers |

---

## 目录结构

```
MemoryWorkstation/
├── src/
│   ├── main.py              # 主程序入口
│   ├── scanner/scanner.py   # 两阶段扫描器（Gate1 + Gate2）
│   ├── llm/manager.py       # 多后端 LLM 管理器
│   ├── storage/             # SQLite + LanceDB 存储
│   ├── gateway/             # MCP + HTTP API
│   └── tray/tray_app.py     # 系统托盘
├── config.toml              # 配置文件
├── local_llm/embed/         # 向量嵌入模型（可选，nomic-embed-text）
├── memory_export/           # 记忆导出目录（Markdown 树）
└── logs/                    # 运行日志
```

---

## 系统托盘

右键托盘图标可执行：

| 菜单项 | 功能 |
|--------|------|
| 重启服务 | 软重启工作台 |
| 手动全盘扫描 | 强制重新AI分类 |
| 锁定模型常驻 | 防止模型自动卸载 |
| 手动卸载模型 | 临时释放显存 |
| 重载配置 | 热加载 config.toml |
| 资源回收 | 清理内存缓存 |
| 导出迁移包 | 生成快照备份 |

---

## API 接口

### 鉴权

所有接口需在 Header 中传入 Token：

```bash
-H "token: YOUR_TOKEN"
```

### 检索类

| 端点 | 参数 | 说明 |
|------|------|------|
| `GET /api/memory/short` | keyword, limit | 短期记忆 |
| `GET /api/memory/long` | keyword, limit | 长期记忆 |
| `GET /api/memory/planning` | keyword, limit | 规划文档 |
| `GET /api/memory/selfimprove` | keyword, limit | 学习文档 |
| `GET /api/memory/archive` | keyword, limit | 归档文档 |

### 文件管理

| 端点 | 参数 | 说明 |
|------|------|------|
| `GET /api/files/all` | limit, offset | 全部文档 |
| `GET /api/files/unknown` | — | 未分类文档 |
| `PUT /api/files/classify` | doc_id, label | 修正分类 |

### 系统

| 端点 | 说明 |
|------|------|
| `GET /api/system/status` | 系统状态 |

---

## 故障排查

| 问题 | 解决方案 |
|------|----------|
| LLM 分类不工作 | 检查 `config.toml` 中 `[llm.classify]` 的 API 配置 |
| API 返回 401 | 检查 `config.toml` 中的 token |
| 磁盘空间不足 | 清理 `memory_export/` 旧导出和 `logs/` |
| 托盘图标不显示 | 安装 pystray：`pip install pystray` |
| 数据库损坏 | 程序自动从快照恢复，检查 `logs/error.log` |

---

## 注意事项

### 环境铁律

- **Python 版本**：MW 相关命令一律用 **Python 3.13**（`C:/Users/周海龙/AppData/Local/Programs/Python/Python313/python.exe`）。PATH 上的 3.12 加载不了 cp313 编译的 `.pyd`，会 `DLL load failed` 或 `is_available()=False`。
- **源码区 vs 工作区**：改代码只在 `D:\mycode\memory-workstation\mw-sdk\`；`D:\MemoryWorkstation\mw-sdk\` 是 Agent 日常使用区，只接收源码区验证通过的成品，禁止直接改/编译。
- **数据与软件分离**：源码跑 run.py 写 `.memory-workstation-dev/`，打包软件写 `D:\MemoryWorkstation\.memory-workstation/`，互不干扰。

### 核心功能保护

以下功能修改有铁律：导入（ingest）、导出（export）、搜索（search）、知识图谱（cross_ref/crawl）、FTS5（reindex）、向量（build_vector）、五张表 schema（memory_classify/memory_fts/memory_vector/memory_entity/memory_cross_ref）。禁止降级实现、阉割功能、静默失败。

### 搜索模式（rrf vs hybrid）

| 模式 | 特点 | 适用 |
|------|------|------|
| `rrf`（默认） | 只按 FTS5+Entity+Vector 三路 RRF 排名，**不排 weight** | 常规搜索，稳定 |
| `hybrid` | weight 参与排序（+Ebbinghaus 遗忘曲线） | 新写入/高权重记忆默认 rrf 搜不到时 |

```bash
mw search "刚写入的关键词" --mode hybrid -n 1   # 写入后验证
```

注意事项：`tier`（层级）与 `--crawl`（建关联边）**不影响搜索排位**，只用于管理/协作。

### 搜索优先级协议（写给 Agent）

```
确切关键词（如"WAL协议"）→ grep MD 文件 或 mw search --include-md
模糊/相关（如"上次那个用户说的问题"）→ mw search（语义匹配广）
需要关联（如"这个决策影响了什么"）→ mw search --graph
刚写入搜不到 → mw search --mode hybrid（权重浮出，勿盲目 rebuild-fts5）
```

### 多 Agent 共享

Claude / MiMo / Codex 共用 `meta_agents.sqlite`，`mw search "关键词"` 即搜全部，无需 `--agent`。数据目录可经软件目录下 `MemoryWorkstation.cfg`（一行路径）随时改，无需重打包。

### 修改后必须

1. 改 C++ → 重编译 + 部署新 `.pyd` + 清 `__pycache__`
2. 改 Python → `python -c "from mw_sdk import MemoryClient"` 确认导入
3. 全部修改 → 在源码区 `pytest tests/` 通过后才允许同步到工作区
4. 涉及文档 → 检查 `ARCHITECTURE.md` / `CLAUDE.md` / skill 是否同步更新
