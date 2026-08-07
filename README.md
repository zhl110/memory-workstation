# memory-station 全自动记忆工作台

> 多接口大模型记忆管理系统，自动扫描 Claude/Codex 记忆文件，AI分类归档，供随时调取。
> 支持 DeepSeek / Claude / Ollama / 本地模型等多种接入方式。
>
> 数据与软件分离：记忆数据存储在本地数据目录，程序与数据零耦合。

## 快速开始

### 1. 依赖

```bash
pip install -r requirements.txt
```

核心依赖：`requests`（API调用）、`sqlite3`（内置）、`lancedb`（向量存储）

### 2. 配置

复制 `config.toml.example` 为 `config.toml`，确保 `[llm.classify]` 配置正确：

```toml
[llm.classify]
provider = "custom_api"                      # 接入方式
api_key = "your_api_key"                     # API密钥
api_model = "deepseek-v4-flash"             # 模型名
api_base_url = "http://127.0.0.1:4000/v1/chat/completions"  # LiteLLM网关
```

也支持 cc-switch / mimo serve / Ollama 等多种后端，只需改 `provider` 和对应配置。

### 3. 运行

> 大型二进制（C++ 核心 `.pyd`、嵌入模型、onnxruntime）默认不入库，clone 后首次使用前需构建：
> ```bash
> cd mw-sdk/cpp && build.bat   # Windows，构建 C++ 核心引擎
> ```

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
memory-station/
├── src/
│   ├── main.py              # 主程序入口
│   ├── scanner/scanner.py   # 两阶段扫描器（Gate1 + Gate2）
│   ├── llm/manager.py       # 多后端 LLM 管理器
│   ├── storage/             # SQLite + 向量存储
│   ├── gateway/             # MCP + HTTP API
│   └── tray/tray_app.py     # 系统托盘
├── mw-sdk/                  # 纯数据引擎 SDK（零 LLM 依赖）
├── config.toml.example      # 配置文件模板（复制为 config.toml）
├── memory_storage/          # 运行数据目录（本地）
└── logs/                    # 运行日志（本地）
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
