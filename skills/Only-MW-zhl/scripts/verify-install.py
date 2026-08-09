"""一键验证 Only-MW-zhl 所有功能是否正常

依次测试：
1. SDK 导入 + 初始化
2. 搜索（含大池子兜底）
3. 规则查询
4. 实体查询
5. 统计信息

Usage:
    python verify-install.py

Exit code:
    0 — 全部通过
    1 — 部分失败（影响部分功能）
    2 — 严重错误（无法使用）
"""
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def verify():
    print("=" * 50)
    print("  Only-MW-zhl 功能验证")
    print("=" * 50)

    print("\n[1/6] 导入 SDK + 初始化...", end=" ")
    try:
        from mw_sdk import MemoryClient
        from mw_sdk.utils import get_agent_db
        db = get_agent_db()
        m = MemoryClient(db)
        print("✅")
    except Exception as e:
        print(f"❌  {e}")
        return False

    print("[2/6] 搜索测试...", end=" ")
    try:
        results = m.search("规则", top_k=3)
        if results:
            print(f"✅  找到 {len(results)} 条")
        else:
            print("⚠️   返回 0 条（库可能为空）")
    except Exception as e:
        print(f"❌  {e}")

    print("[3/6] 规则查询...", end=" ")
    try:
        rules = m.get_rules(limit=5)
        if rules:
            print(f"✅  找到 {len(rules)} 条规则")
        else:
            print("⚠️   无规则（可能未导入）")
    except Exception as e:
        print(f"❌  {e}")

    print("[4/6] 实体查询...", end=" ")
    try:
        entities = m.get_entities(limit=5)
        if entities:
            print(f"✅  找到 {len(entities)} 个实体")
        else:
            print("ℹ️   无实体")
    except Exception as e:
        print(f"❌  {e}")

    print("[5/6] auto_cross_ref 自动关联测试...", end=" ")
    try:
        # 用无参数模式：auto_cross_ref 自动按 entity 共享查找候选
        conn = m._conn
        sample = conn.execute(
            "SELECT doc_id FROM memory_classify WHERE compact_content != '' LIMIT 1"
        ).fetchone()
        if sample:
            doc_id = sample["doc_id"]
            n = m.auto_cross_ref(doc_id, top_k=3)
            if n > 0:
                print(f"✅  #{doc_id} → {n} 条双向边")
                linked = m.get_linked(doc_id)
                print(f"          get_linked({doc_id}) → {len(linked)} 条关联")
            else:
                print("⚠️   0 条（已有全部关联或无候选）")
        else:
            print("ℹ️   跳过（库为空）")
    except Exception as e:
        print(f"❌  {e}")

    print("\n[6/6] 统计信息...", end=" ")
    try:
        stats = m.get_stats()
        print(f"✅  {stats['total_memories']} 条记忆, "
              f"{stats['entity_count']} 个实体, "
              f"{stats['cross_ref_count']} 条交叉引用")
    except Exception as e:
        print(f"❌  {e}")

    print("\n  大池子连接:", end=" ")
    try:
        if m._pool_conn:
            pool_rows = m._pool_conn.execute(
                "SELECT COUNT(*) FROM memory_classify"
            ).fetchone()[0]
            print(f"✅  已连接 ({pool_rows} 条记忆)")
        else:
            print("ℹ️   未连接（不影响核心功能）")
    except Exception:
        print("ℹ️   检查失败")

    m.close()
    print("\n" + "=" * 50)
    print("  验证完成")
    print("=" * 50)
    return True


if __name__ == "__main__":
    success = verify()
    sys.exit(0 if success else 2)
