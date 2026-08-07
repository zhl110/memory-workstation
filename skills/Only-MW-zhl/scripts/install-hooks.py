#!/usr/bin/env python3
"""
Only-MW-zhl Hook 安装脚本。

首次运行 skill 时自动执行：
- Claude Code: 写入 ~/.claude/settings.json 的 hooks 区块
- Codex: 写入 ~/.codex/hooks.json 的 UserPromptSubmit | SessionStart
- MiMo: 写入 ~/.mimocode/hooks/ 目录
- 项目级: 写入 .claude/settings.local.json（可选）

幂等：已存在的 hook 不会重复添加。
"""
import json
import os
import sys
from pathlib import Path

# ── hook 配置 ──────────────────────────────────────────────
PROJECT_ROOT = Path("d:/mycode")

CLAUDE_HOOKS = {
    "PreMessage": {
        "matcher": "all",
        "script": 'python "{}" "$(cat)"'.format(
            str(PROJECT_ROOT / ".claude/hooks/pre-message.py")
        ),
    },
    "PostInit": {
        "script": 'python "{}"'.format(
            str(PROJECT_ROOT / ".claude/hooks/post-init.py")
        ),
    },
}

CODEX_PRE_MESSAGE = {
    "matcher": "*",
    "hooks": [
        {
            "type": "command",
            "command": 'python "{}"'.format(
                str(PROJECT_ROOT / ".claude/hooks/pre-message.py")
            ),
        }
    ],
}

CODEX_POST_INIT = {
    "matcher": "*",
    "hooks": [
        {
            "type": "command",
            "command": 'python "{}"'.format(
                str(PROJECT_ROOT / ".claude/hooks/post-init.py")
            ),
        }
    ],
}


# ── Claude Code ────────────────────────────────────────────

def install_claude_global() -> bool:
    """写入 ~/.claude/settings.json"""
    path = Path.home() / ".claude" / "settings.json"
    if not path.exists():
        print("  [skip] ~/.claude/settings.json 不存在")
        return False
    return _merge_claude_hooks(path)


def install_claude_project() -> bool:
    """写入项目 .claude/settings.local.json"""
    path = PROJECT_ROOT / ".claude" / "settings.local.json"
    return _merge_claude_hooks(path)


def _merge_claude_hooks(path: Path) -> bool:
    if not path.exists():
        print(f"  [skip] {path} 不存在")
        return False

    data = json.loads(path.read_text(encoding="utf-8"))
    hooks = data.setdefault("hooks", {})

    changed = False
    for name, cfg in CLAUDE_HOOKS.items():
        if name in hooks:
            print(f"  [ok] Claude {name} 已存在")
            continue
        hooks[name] = cfg
        changed = True
        print(f"  [add] Claude {name}")

    if changed:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return changed


# ── Codex ──────────────────────────────────────────────────

def install_codex() -> bool:
    """写入 ~/.codex/hooks.json"""
    path = Path.home() / ".codex" / "hooks.json"
    if not path.exists():
        print("  [skip] ~/.codex/hooks.json 不存在")
        return False

    data = json.loads(path.read_text(encoding="utf-8"))
    hooks = data.setdefault("hooks", {})
    changed = False

    # UserPromptSubmit ≈ PreMessage
    ups = hooks.setdefault("UserPromptSubmit", [])
    existing_commands = {
        h["hooks"][0]["command"]
        for h in ups
        if h.get("hooks") and "command" in h["hooks"][0]
    }
    cmd = CODEX_PRE_MESSAGE["hooks"][0]["command"]
    if cmd in existing_commands:
        print("  [ok] Codex PreMessage 已存在")
    else:
        ups.append(CODEX_PRE_MESSAGE)
        changed = True
        print("  [add] Codex PreMessage")

    # SessionStart ≈ PostInit
    ss = hooks.setdefault("SessionStart", [])
    existing_commands = {
        h["hooks"][0]["command"]
        for h in ss
        if h.get("hooks") and "command" in h["hooks"][0]
    }
    cmd = CODEX_POST_INIT["hooks"][0]["command"]
    if cmd in existing_commands:
        print("  [ok] Codex PostInit 已存在")
    else:
        ss.append(CODEX_POST_INIT)
        changed = True
        print("  [add] Codex PostInit")

    if changed:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return changed


# ── MiMo ───────────────────────────────────────────────────

def install_mimo() -> bool:
    """写入 ~/.mimocode/hooks/ 目录"""
    hooks_dir = Path.home() / ".mimocode" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    # MiMo hook 是 TypeScript，这里只创建占位说明
    # 实际 hook 需要用户手动编写或由 MiMo 的 self-extend skill 生成
    readme_path = hooks_dir / "README.md"
    if readme_path.exists():
        print("  [ok] MiMo hooks 目录已存在")
        return False

    readme_content = """# MiMo MW Hooks

MW 记忆系统的 MiMo hook 文件。

## 需要的 hooks

1. `pre-message.ts` — 每轮用户消息计数，满5轮输出 DIGEST_OUTDATED 信号
2. `post-init.ts` — 会话启动时读取最新 session-digest

## 创建方式

使用 MiMo 的 self-extend skill 创建，或手动编写 TypeScript 文件。

参考 Python 版本：`d:/mycode/.claude/hooks/pre-message.py`
"""
    readme_path.write_text(readme_content, encoding="utf-8")
    print("  [add] MiMo hooks 目录 + README")
    return True


# ── 主流程 ──────────────────────────────────────────────────

def main():
    print("MW Hook 安装检查\n" + "─" * 40)
    ok = True

    print("\n【Claude Code — 全局】")
    try:
        install_claude_global()
    except Exception as e:
        print(f"  [err] {e}")
        ok = False

    print("\n【Claude Code — 项目级】")
    try:
        install_claude_project()
    except Exception as e:
        print(f"  [err] {e}")
        ok = False

    print("\n【Codex】")
    try:
        install_codex()
    except Exception as e:
        print(f"  [err] {e}")
        ok = False

    print("\n【MiMo】")
    try:
        install_mimo()
    except Exception as e:
        print(f"  [err] {e}")
        ok = False

    print("\n" + "─" * 40)
    if ok:
        print("MW Hook 安装检查完成")
    else:
        print("部分安装失败，请检查上述错误")
        sys.exit(1)


if __name__ == "__main__":
    main()
