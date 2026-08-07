"""直接对已有文档执行路径规则分类"""
import sqlite3
import re
import os
import sys
sys.stdout.reconfigure(encoding='utf-8')

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_PROJECT_ROOT)
sys.path.insert(0, _PROJECT_ROOT)
from src.core.config import _MEMORY_HOME
from src.core.enums import DocumentLabel, LABEL_TO_TIER

db_path = os.path.join(_MEMORY_HOME, "meta.sqlite")
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
c = conn.cursor()

# 获取所有文档
c.execute("SELECT id, file_path, raw_text_snippet FROM document_files WHERE is_deleted=0")
docs = c.fetchall()
print(f"文档总数: {len(docs)}")

classified = 0
for doc in docs:
    doc_id = doc['id']
    filepath = doc['file_path']
    raw_snippet = doc['raw_text_snippet'] or ''
    
    fp = filepath.lower().replace("\\", "/")
    ext = os.path.splitext(filepath)[1].lower()
    
    if ext in {".jsonl", ".json", ".log", ".lock", ".exe", ".bin"}:
        continue
    
    path_rules = [
        (r"/skill", DocumentLabel.META_RULE, "P1", "AI专属类", "Skill开发"),
        (r"skill.md", DocumentLabel.META_RULE, "P1", "AI专属类", "Skill开发"),
        (r"claude.md", DocumentLabel.META_RULE, "P0", "AI专属类", "Agent配置"),
        (r"rules.md", DocumentLabel.META_RULE, "P0", "AI专属类", "Agent配置"),
        (r"constitution", DocumentLabel.META_RULE, "P0", "AI专属类", "Agent配置"),
        (r"/agent", DocumentLabel.META_RULE, "P1", "AI专属类", "Agent配置"),
        (r"mcp", DocumentLabel.META_RULE, "P1", "AI专属类", "工具链"),
        (r"prompt", DocumentLabel.META_RULE, "P1", "AI专属类", "Prompt工程"),
        (r"plan", DocumentLabel.PLANNING_DOC, "P1", "流程类", "工作流"),
        (r"design", DocumentLabel.PLANNING_DOC, "P1", "流程类", "工作流"),
        (r"/spec", DocumentLabel.PLANNING_DOC, "P1", "业务类", "规格"),
        (r"architecture", DocumentLabel.PLANNING_DOC, "P1", "技术类", "架构"),
        (r"self.improve", DocumentLabel.SELF_IMPROVE_LEARN, "P1", "AI专属类", "调试经验"),
        (r"learn", DocumentLabel.SELF_IMPROVE_LEARN, "P2", "知识类", ""),
        (r"memory", DocumentLabel.MEMORY_LAYER, "P2", "参考类", ""),
        (r"license", DocumentLabel.META_RULE, "P0", "参考类", ""),
        (r"readme", DocumentLabel.COMPACT_ARCHIVE, "P3", "参考类", ""),
    ]
    
    for pattern, label, importance, category, sub_category in path_rules:
        if re.search(pattern, fp):
            tier = LABEL_TO_TIER.get(label, LABEL_TO_TIER[DocumentLabel.UNKNOWN])
            weight = 90 if label == DocumentLabel.META_RULE else 50
            
            # 新逻辑：用raw_text_snippet[:500]作为compact_content
            compact_content = raw_snippet[:500] if raw_snippet else ""
            
            c.execute("""
                INSERT OR REPLACE INTO memory_classify 
                (doc_id, label, memory_tier, weight, importance, namespace,
                 compact_content, content_category, sub_category, depth, tags,
                 classify_record)
                VALUES (?, ?, ?, ?, ?, 'default', ?, ?, ?, '概述', '[]', 
                        json_object('ts', datetime('now')))
            """, (doc_id, label.value, tier.value, weight, importance,
                  compact_content, category, sub_category))
            classified += 1
            break
    
    # .jsonl文件特殊处理
    if filepath.endswith(".jsonl") and classified == 0:
        tier = LABEL_TO_TIER.get(DocumentLabel.CHAT_LOG, LABEL_TO_TIER[DocumentLabel.UNKNOWN])
        c.execute("""
            INSERT OR REPLACE INTO memory_classify 
            (doc_id, label, memory_tier, weight, importance, namespace,
             compact_content, content_category, sub_category, depth, tags,
             classify_record)
            VALUES (?, ?, ?, 50, 'P3', 'default', ?, 'AI专属类', '工具链', '概述', '[]',
                    json_object('ts', datetime('now')))
        """, (doc_id, DocumentLabel.CHAT_LOG.value, tier.value,
              raw_snippet[:500] if raw_snippet else ""))
        classified += 1

conn.commit()
print(f"分类完成: {classified} 条")

# 验证
c.execute("SELECT COUNT(*) FROM memory_classify")
print(f"memory_classify总数: {c.fetchone()[0]}")

c.execute("SELECT COUNT(*) FROM memory_classify WHERE LENGTH(compact_content) > 100")
print(f"compact_content > 100字符: {c.fetchone()[0]}")

c.execute("SELECT compact_content, LENGTH(compact_content) as len FROM memory_classify ORDER BY LENGTH(compact_content) DESC LIMIT 3")
print("\n最长的3条:")
for row in c.fetchall():
    print(f"  [{row['len']}字符] {row['compact_content'][:80]}...")

conn.close()
