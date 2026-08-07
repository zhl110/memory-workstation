"""Replenish unclassified docs with LLM"""
import sys, os, re, json, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
os.environ["PYTHONIOENCODING"] = "utf-8"

from src.core.config import load_config
from src.storage.manager import StorageManager
from src.llm.manager import LLMManager
from src.core.enums import DocumentLabel, MemoryTier, LABEL_TO_TIER

cfg = load_config()
storage = StorageManager(cfg.storage.db_path, cfg.storage.vector_path, cfg.storage.snapshot_dir)
storage.init()
c = storage.sqlite._conn

rows = c.execute("""
    SELECT d.id, d.file_path
    FROM document_files d
    LEFT JOIN memory_classify mc ON d.id = mc.doc_id
    WHERE mc.doc_id IS NULL AND d.is_deleted = 0
""").fetchall()
print(f"Unclassified docs: {len(rows)}")

llm = LLMManager(cfg)
llm.load_classify_model()

path_rules = [
    ("skill", DocumentLabel.META_RULE, "P1", "AI专属类", "Skill开发"),
    ("claude.md", DocumentLabel.META_RULE, "P0", "AI专属类", "Agent配置"),
    ("rules.md", DocumentLabel.META_RULE, "P0", "AI专属类", "Agent配置"),
    ("plan", DocumentLabel.PLANNING_DOC, "P1", "流程类", "工作流"),
    ("chat", DocumentLabel.CHAT_LOG, "P2", "交互类", "会话记录"),
]

BATCH_SIZE = 8
ok = 0
batches = []
for i, r in enumerate(rows):
    if i % BATCH_SIZE == 0:
        batches.append([])
    batches[-1].append(r)

for batch_idx, batch in enumerate(batches):
    # 批量读取文件内容
    batch_docs = []  # (content, filepath)
    batch_rows = []
    for r in batch:
        fp = r["file_path"]
        content = None
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()[:15000]
        except:
            pass
        if content:
            batch_docs.append((content, fp))
            batch_rows.append(r)

    if not batch_docs:
        continue

    # 批量 LLM 调用
    batch_results = llm.classify_batch(batch_docs)

    for r, result in zip(batch_rows, batch_results):
        label, importance, summary, cat, sub, depth, ct, kt, appl = result

        if label == DocumentLabel.UNKNOWN:
            fpl = r["file_path"].lower().replace(os.sep, "/")
            for pat, lbl, imp, ccat, csub in path_rules:
                if re.search(pat, fpl):
                    label = lbl; importance = imp; cat = ccat; sub = csub
                    break

        tier = LABEL_TO_TIER.get(label, MemoryTier.SHORT)
        weight = 95 if appl == "通用规则" else (50 if appl == "场景知识" else 20)
        storage.sqlite.set_classification(
            r["id"], label, tier, weight, importance=importance,
            compact_content=(summary or "")[:1000],
            content_category=cat or "", sub_category=sub or "",
            depth=depth or "概述",
            tags=json.dumps([ct or "", kt or ""]),
        )
        ok += 1

    print(f"  Batch {batch_idx + 1}/{len(batches)}: {ok}/{len(rows)} classified")

llm.unload()

total = c.execute("SELECT COUNT(*) FROM document_files").fetchone()[0]
classified = c.execute("SELECT COUNT(*) FROM memory_classify").fetchone()[0]
print(f"\nDone: {ok}/{len(rows)} classified")
print(f"Total docs: {total}, total classified: {classified}")

print("\nCategory distribution:")
for r in c.execute("SELECT content_category, COUNT(*) as cnt FROM memory_classify GROUP BY content_category ORDER BY cnt DESC").fetchall():
    cat = r["content_category"] or "(empty)"
    print(f"  {cat}: {r['cnt']}")

print("\nLabel distribution:")
for r in c.execute("SELECT label, COUNT(*) as cnt FROM memory_classify GROUP BY label ORDER BY cnt DESC").fetchall():
    print(f"  {r['label']}: {r['cnt']}")

storage.close()
