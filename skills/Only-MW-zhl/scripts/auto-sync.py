#!/usr/bin/env python3
"""
Only-MW-zhl 自动同步脚本。

检测核心依赖文件（rules.md / client.py / hook 等）有无变化，
自动更新 skill 目录下的派生文件（README.md 等），
并报告哪些手动文件需要同步。
"""
import hashlib
import json
import os
import sys
from pathlib import Path

SKILL_DIR = Path("d:/mycode/agent-hub/skills/Only-MW-zhl")
MANIFEST = SKILL_DIR / "_sync_manifest.json"
PROJECT_ROOT = Path("d:/mycode")

def hash_file(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]

def resolve_dep(rel: str) -> Path:
    return (PROJECT_ROOT / rel).resolve()

def auto_update_readme(target: Path):
    """从 SKILL.md 自动生成 README.md 摘要"""
    skill_md = SKILL_DIR / "SKILL.md"
    if not skill_md.exists():
        return False
    text = skill_md.read_text(encoding="utf-8")

    lines = []
    in_layer0 = False
    in_core = False
    for line in text.splitlines():
        if line.startswith("## Layer 0: 双脑身份"):
            in_layer0 = True
        if in_layer0 and line.startswith("## "):
            in_layer0 = False
        if line.startswith("## 会话快照+项目里程碑三件套"):
            in_core = False
        if in_core:
            lines.append(line)
        if line.startswith("### 触发条件"):
            in_core = False

    target.write_text(
        f"""# Only-MW-zhl

MW（Memory Workstation）—— Agent 的长期记忆脑区。双脑协作：主脑推理+生成，MW 记忆+回忆。

> 自动生成 — 核心内容见 [SKILL.md](./SKILL.md)

## 核心能力

| 操作 | 命令 | 功能 |
|------|------|------|
| 自动检索 | `📖MW` 信号 | 意图驱动，Agent 收到信号后自行构造查询搜索 |
| 摄入 | `/mw-ingest` | 分类 + 融合 + 写索引 + 交叉引用 |
| 查询 | `/mw-query` | 优先自己库，不够自动去大池子兜底 |
| 体检 | `mw lint` | 知识健康度检查（纯算法，零token） |
| 路由 | `mw index` | 查看知识库路由表 |
| 反思 | `mw reflect --pattern --summary` | 自我反思记录纠正模式 |
| 进化 | `mw evolve` | 权重衰减 + 冷热升降级 + 纠正固化 |
| 日志 | `mw log` | 进化历史查看（支持 --type 过滤） |

## Layer 0: 双脑身份

MW 不是工具，是你的长期记忆脑区。理解问题时自然联想到 MW 的记忆。

- 检索结果用"你回忆起…"的格式呈现
- `<need_memory:关键词>` —— Agent 主动伸手要记忆
- MW 异常时主脑仍然独立工作（只是回忆稍模糊）

## 关键功能

- **Core Memory** — 最多 5 条 always_load 记忆自动注入会话
- **会话快照** — 每 5 轮归档 session-digest，保留 10 个轮换
- **项目里程碑** — 每次 digest 同步更新 MW 永久里程碑记忆，跨 compact 不丢失
- **知识归档** — digest 时同步提取重要知识摄入 MW
- **星级遗忘** — 低频记忆 Agent 自评（保留/展示/推荐删除）

## 架构

```
Agent (Claude) → 自己做 classify/fuse/rerank
       ↓ 调 SDK
mw-sdk (纯数据引擎) → SQLite 存取 + FTS5 索引
```

详见 [SKILL.md](./SKILL.md) 完整说明书。
""",
        encoding="utf-8",
    )
    return True


def main():
    if not MANIFEST.exists():
        print("⚠️  _sync_manifest.json 不存在，跳过同步")
        return

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    changed = []
    stale_files = []

    # 1. 检查外部依赖是否有变化
    for dep_name, dep_rel in manifest.get("dependencies", {}).items():
        dep_path = resolve_dep(dep_rel)
        current = hash_file(dep_path)
        stored = manifest.get("_dep_hashes", {}).get(dep_name)
        if current != stored:
            changed.append(dep_name)
            if "dep_hashes" not in manifest:
                manifest["_dep_hashes"] = {}
            manifest["_dep_hashes"][dep_name] = current

    # 2. 检查 skill 自文件 hash
    for fname, finfo in manifest.get("files", {}).items():
        fpath = SKILL_DIR / fname
        current = hash_file(fpath)
        stored = finfo.get("hash")
        # 如果外部依赖变了，标记关联文件
        deps_changed = [c for c in changed if c in [d.split("/")[-1].replace(".py","").replace(".md","") for d in finfo.get("deps", [])]]
        if deps_changed:
            stale_files.append(fname)
        if current != stored:
            stale_files.append(fname)
        finfo["hash"] = current

    # 3. 自动更新 auto_gen 文件
    auto_updated = []
    for fname, finfo in manifest.get("files", {}).items():
        if finfo.get("auto_gen") and finfo.get("source"):
            source = finfo["source"]
            if source == "SKILL.md":
                if auto_update_readme(SKILL_DIR / fname):
                    finfo["hash"] = hash_file(SKILL_DIR / fname)
                    auto_updated.append(fname)

    # 4. 保存 manifest
    manifest["_last_sync"] = __import__("datetime").datetime.now().isoformat()
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    # 5. 报告
    if stale_files:
        print(f"[sync] Only-MW-zhl skill 需要手动同步：{', '.join(stale_files)}")
    if auto_updated:
        print(f"[sync] 自动更新：{', '.join(auto_updated)}")
    if not stale_files and not auto_updated and not changed:
        print("[sync] Only-MW-zhl skill 全部同步")


if __name__ == "__main__":
    main()
