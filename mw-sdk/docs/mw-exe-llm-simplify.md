# exe LLM 精简方案 — 只留 Ollama 本地模型

## 要什么

exe 不再调任何远程 API，只连本地 Ollama 做基础分类。其他 LLM 后端全部砍掉。

---

## 砍什么

### 删掉 7 个后端（只留 ollama.py）

| 文件 | 行数 | 说明 |
|------|------|------|
| `src/llm/backends/anthropic_api.py` | 126 | Anthropic API → 删 |
| `src/llm/backends/openai_compat.py` | 192 | OpenAI / 兼容 API → 删 |
| `src/llm/backends/auto_detected.py` | 90 | 自动检测 Claude/MiMo/Codex 免费通道 → 删 |
| `src/llm/backends/mimo_free.py` | 104 | MiMo 免费通道 → 删 |
| `src/llm/backends/llama_cpp.py` | 111 | 本地 GGUF → 删 |
| `src/llm/backends/onnx.py` | 136 | 本地 ONNX → 删 |
| `src/llm/backends/transformers.py` | 102 | 本地 Transformers → 删 |
| `src/llm/auto_config.py` | 186 | 自动检测系统工具 → 删 |

### 简化

| 文件 | 做什么 |
|------|--------|
| `src/llm/manager.py` | 删 _PROVIDER_TO_BACKEND 的路由表（只走 ollama），删 _resolve_backend 的本地模型分支 |
| `src/llm/classifier.py` | 不动（prompt 构建和 parse 逻辑 AGent 也在用） |
| `src/llm/base.py` | 不动（Protocol 定义，其他模块还可能引用） |
| `src/pipeline/pipeline.py` | step_llm_classify 改为只走 ollama（不走 _PROVIDER_TO_BACKEND） |

### 不动

| 文件 | 说明 |
|------|------|
| `src/llm/classifier.py` | CLASSIFY_PROMPT / build_classify_prompt / parse_classify_json — Agent 也在复用 |
| `src/llm/backends/ollama.py` | 不动，保留 |
| `src/classifier.py` | DynamicClassifier — 关键词分类，不依赖 LLM，保留 |
| `src/pipeline/pipeline.py` | prefilter / keyword_hint / path_fallback / store 步骤保留 |
| `src/optimizer.py` | 24h 定时器，不动 |
| `src/storage/sqlite_store.py` | 不动 |
| `src/scanner/` | 文件扫描，不动 |

---

## 改动后 exe 的文件扫描流程

```
文件扫描 → prefilter（短内容/JSON/纯聊天→过滤）
         → keyword_hint（DynamicClassifier 关键词分类）
         → Ollama 分类（如果 keyword 没命中或置信度低）
         → path_fallback（路径规则兜底）
         → 存库 → MemoryOptimizer 24h 跑权重衰减/去重/候选
```

**不再有**：远程 API 调用、本地 GGUF/ONNX/Transformers 推理、MiMo/Claude/Codex 免费通道检测。

---

## 具体改动

### 1. 删文件

```bash
git rm src/llm/backends/anthropic_api.py
git rm src/llm/backends/openai_compat.py
git rm src/llm/backends/auto_detected.py
git rm src/llm/backends/mimo_free.py
git rm src/llm/backends/llama_cpp.py
git rm src/llm/backends/onnx.py
git rm src/llm/backends/transformers.py
git rm src/llm/auto_config.py
```

### 2. 改 llm/manager.py

简化 _resolve_backend：不再查表找 provider，不再探测本地模型文件，直接连 ollama。

```python
def _resolve_backend(config: Config) -> Optional[LLMBackend]:
    """直接创建 OllamaBackend，不再走 provider 路由"""
    from .backends.ollama import OllamaBackend
    return OllamaBackend()
```

删掉：
- `_PROVIDER_TO_BACKEND` 字典（整个删）
- `_create_backend()` 函数（整个删）
- `auto_config` 相关的 import（如果有）
- `_detect_local_backend()` 相关逻辑（如果有）

### 3. 改 main.py

检查 AppContext 初始化中是否引用了 auto_config 或其他后端，清理 import。

---

## 打包验证

```bash
# 1. 导入不报错
python -c "from src.llm.manager import LLMManager, _resolve_backend; print('OK')"

# 2. pipeline 不报错
python -c "from src.pipeline import ClassifyPipeline; print('OK')"

# 3. ollama 后端能加载
python -c "
import requests
resp = requests.get('http://localhost:11434/api/tags', timeout=3)
print('Ollama:', 'running' if resp.status_code == 200 else 'not running')
"

# 4. optimizer 正常
python -c "from src.optimizer import MemoryOptimizer; print('OK')"
```
