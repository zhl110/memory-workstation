# 贡献指南

感谢你愿意为 memory-station 贡献代码。

## 环境要求

- Python 3.13
- C++ 编译器（用于构建 mw-sdk 的 C++ 核心，可选）
- CMake（可选，仅当修改 C++ 代码时需要）

## 项目结构

```
memory-station/
├── src/          # 桌面程序（GUI + 扫描 + 分类 + API）
├── mw-sdk/       # 纯数据引擎 SDK（零 LLM 依赖）
│   ├── cpp/      # C++ 核心（向量索引等）
│   ├── mw_sdk/   # Python 封装
│   └── tests/    # 单元测试
```

## 开发流程

1. **Fork 本仓库**并克隆到本地
2. 创建功能分支：`git checkout -b feature/xxx`
3. 修改代码，遵守以下原则：
   - 只修目标，不做无关重构
   - 数据与软件分离，不硬编码个人路径/密钥
   - 密钥统一走 `config.toml` / 环境变量
4. **运行测试**：`python -m pytest mw-sdk/tests/`
5. 提交并推送，创建 Pull Request

## 提交信息规范

参考 Conventional Commits：

```
feat: 添加 XX 功能
fix: 修复 XX 问题
docs: 更新文档
refactor: 重构 XX
```

## 注意事项

- 不要提交任何个人记忆数据（`memory_storage/`、`memory_export/`、`logs/` 等已被 .gitignore 排除）
- 不要提交编译产物（`.pyd`、`dist/`、`build/`）
- 不要提交真实 API Key / Token，一律使用占位符