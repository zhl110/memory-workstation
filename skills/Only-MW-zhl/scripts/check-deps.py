"""检查 mw-sdk 是否安装及相关数据库文件是否存在

Usage:
    python check-deps.py

Exit code:
    0 — 一切正常
    1 — 有警告（可降级运行）
    2 — 有错误（无法使用）
"""
import pathlib
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def main():
    errors = []
    warnings = []

    try:
        from mw_sdk import MemoryClient  # noqa: F401
        print("✅  mw-sdk 已安装")
    except ImportError:
        errors.append("mw-sdk 未安装。运行: pip install d:/mycode/memory-workstation/mw-sdk/")
    except Exception as e:
        errors.append(f"mw-sdk 导入异常: {e}")

    claude_db = pathlib.Path("D:/MemoryWorkstation/.memory-workstation/meta_agents.sqlite")
    pool_db = pathlib.Path("D:/MemoryWorkstation/.memory-workstation/meta.sqlite")

    if claude_db.exists():
        size_mb = claude_db.stat().st_size / 1024 / 1024
        print(f"✅  meta_agents.sqlite 存在 ({size_mb:.1f} MB)")
    else:
        warnings.append("meta_agents.sqlite 不存在（首次使用需调 init_schema() 建表）")

    if pool_db.exists():
        size_mb = pool_db.stat().st_size / 1024 / 1024
        print(f"✅  meta.sqlite（大池子）存在 ({size_mb:.1f} MB)")
    else:
        warnings.append("meta.sqlite（大池子）不存在，自动兜底不可用（不影响核心功能）")

    try:
        from mw_sdk import __version__ as ver
        print(f"ℹ️   SDK 版本: {ver}")
    except Exception:
        pass

    for w in warnings:
        print(f"⚠️   {w}")
    for e in errors:
        print(f"❌  {e}")

    if errors:
        sys.exit(2)
    if warnings:
        sys.exit(1)
    print("✅  所有检查通过")
    sys.exit(0)


if __name__ == "__main__":
    main()
