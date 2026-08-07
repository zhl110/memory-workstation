# mw-sdk — Memory Workstation 数据引擎

纯数据引擎，零 LLM 依赖。SQLite + FTS5 存取，C++ 核心实现搜索/图/向量加速。

## 安装

```bash
cd mw-sdk
pip install .
```

- **Windows + Python 3.13**：C++ 核心（`mw_core.cp313-win_amd64.pyd` + `onnxruntime.dll`）已随仓库分发，装完即用。
- **其他平台/版本**：需自行编译 C++ 核心，见 `cpp/README.md`。

## 快速开始

```python
from mw_sdk import MemoryClient

client = MemoryClient("path/to/meta_agents.sqlite")

# 搜索（FTS5 + 向量 + 图谱融合）
results = client.search("记忆系统", top_k=10)

# 写入
client.ingest_full("禁止硬编码密钥", scope="global", category="安全类")

# 导出（Obsidian MD）
client.export_md("path/to/export_dir")
```

## CLI

```bash
pip install .
mw --help          # 安装后获得 mw 命令
mw search "关键词"
```

## 可选依赖

```bash
pip install .[cpp]  # C++ 加速（Windows 已有内置，无需重复安装）
```

## 测试

```bash
pip install pytest
python -m pytest tests/
```

> 注意：`MemoryClient()` 依赖 C++ 引擎，未编译或非 Windows 平台会报错，提示先执行 `cpp/build.bat`。