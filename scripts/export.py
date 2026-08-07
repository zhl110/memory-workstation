"""手动触发导出（带详细日志）"""
import sys
import os
import traceback

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_PROJECT_ROOT)
sys.path.insert(0, _PROJECT_ROOT)

from src.core.config import load_config, _MEMORY_HOME
from src.storage.manager import StorageManager

config = load_config()
# 使用主数据库（内存工作站配置）
db_path = config.storage.db_path
storage = StorageManager(
    db_path=db_path,
    vector_path=config.storage.vector_path,
    snapshot_dir=config.storage.snapshot_dir,
    max_snapshots=config.storage.max_snapshot_count,
    backup_interval_h=config.storage.backup_interval_h,
    enable_wal=config.storage.enable_wal,
)
storage.init()

from src.pipeline.pipeline import ClassifyResult

# 直接执行导出逻辑
import re
import json
from pathlib import Path
from datetime import datetime

output_dir = Path(_MEMORY_HOME) / "memory_export"
output_dir.mkdir(exist_ok=True)

conn = storage.sqlite._conn
rows = conn.execute('''
    SELECT d.id, d.file_path, d.file_hash, d.create_time, d.modify_time,
           d.raw_text_snippet,
           c.label, c.importance, c.weight, c.namespace, c.compact_content,
           c.content_category, c.sub_category, c.depth, c.confidence, c.tags,
           c.memory_tier
    FROM document_files d
    JOIN memory_classify c ON d.id = c.doc_id
    WHERE d.is_deleted = 0
    ORDER BY c.content_category, c.sub_category, c.weight DESC
''').fetchall()

print(f"查询到 {len(rows)} 条记录")

# 统一通过 ClassifyResult.exportable 过滤
results = []
for r in rows:
    cr = ClassifyResult(
        doc_id=r['id'],
        file_path=r['file_path'] or '',
        label=r['label'] or '',
        importance=r['importance'] or 'P2',
        weight=r['weight'] or 0,
        summary=r['compact_content'] or '',
        category=r['content_category'] or '',
        sub_category=r['sub_category'] or '',
        memory_tier=r['memory_tier'] or '',
        tags=r['tags'] or '',
        create_time=r['create_time'] or '',
        namespace=r['namespace'] or 'default',
    )
    if cr.exportable:
        results.append(cr)

print(f"筛选后: {len(results)} 条")

def clean(text):
    if not text:
        return '(无内容)'
    text = re.sub(r'<system-reminder>.*?</system-reminder>', '', text, flags=re.DOTALL|re.IGNORECASE)
    text = re.sub(r'<document>.*?</document>', '', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'---\nname:.*?---', '', text, flags=re.DOTALL)
    text = re.sub(r'\[INST\].*?\[/INST\]', '', text, flags=re.DOTALL)
    text = re.sub(r'<<SYS>>.*?<</SYS>>', '', text, flags=re.DOTALL)
    text = re.sub(r'Recall content.*?task\(\{[^}]*\}\)', '', text, flags=re.DOTALL)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'^\s*metadata:.*?^\s*---', '', text, flags=re.MULTILINE|re.DOTALL)
    return text.strip() or '(无内容)'

def safe_filename(name):
    return re.sub(r'[\\/:*?"<>|]', '_', name).strip('_') or '未命名'

cat_groups = {}
for r in results:
    cat = r.category or r.label or '未分类'
    cat_groups.setdefault(cat, []).append(r)

print(f"分类数: {len(cat_groups)}")

now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
root_index_lines = [
    f'# Memory Workstation 记忆导出\n',
    f'> 导出时间: {now_str} | 总文档: {len(results)}\n\n',
    '## 目录结构\n\n',
]

for category in sorted(cat_groups.keys()):
    items = cat_groups[category]
    cat_dir = output_dir / safe_filename(category)
    cat_dir.mkdir(exist_ok=True)

    sub_groups = {}
    for r in items:
        sub = r.sub_category or '通用'
        sub_groups.setdefault(sub, []).append(r)

    sub_index_lines = [
        f'# {category}\n',
        f'> 共 {len(items)} 条 | 导出: {now_str}\n\n',
        '## 子分类\n\n',
        '| 子分类 | 文件 | 数量 |\n',
        '|--------|------|------|\n',
    ]

    for sub in sorted(sub_groups.keys()):
        sub_items = sub_groups[sub]
        sub_filename = f'{safe_filename(sub)}.md'
        sub_index_lines.append(f'| {sub} | [{sub_filename}]({sub_filename}) | {len(sub_items)} |\n')

        with open(cat_dir / sub_filename, 'w', encoding='utf-8') as f:
            f.write(f'# {category} — {sub}\n\n> 共 {len(sub_items)} 条\n\n')
            for i, item in enumerate(sub_items, 1):
                content = clean(item.summary or '')
                source = item.file_path.split('\\')[-1] if '\\' in item.file_path else item.file_path.split('/')[-1]
                imp = item.importance or 'P2'
                weight = item.weight or 50
                ns = item.namespace or 'default'

                f.write(f'## {i}. [{imp}] {source}\n\n')
                f.write(f'**权重:** {weight} | **命名空间:** {ns}\n\n')
                f.write(f'{content}\n\n---\n\n')

    sub_index_lines.append('\n## 最近5条\n\n')
    for item in items[:5]:
        preview = clean(item.summary or '')[:80]
        source = item.file_path.split('\\')[-1] if '\\' in item.file_path else item.file_path.split('/')[-1]
        sub_index_lines.append(f'- **{source}**: {preview}...\n')

    with open(cat_dir / '_index.md', 'w', encoding='utf-8') as f:
        f.writelines(sub_index_lines)

    root_index_lines.append(f'- [{category}]({safe_filename(category)}/_index.md) — {len(items)}条\n')

with open(output_dir / 'INDEX.md', 'w', encoding='utf-8') as f:
    f.writelines(root_index_lines)

category_stats = {}
for cat, items in cat_groups.items():
    sub_stats = {}
    for item in items:
        sub = item.sub_category or '通用'
        sub_stats[sub] = sub_stats.get(sub, 0) + 1
    category_stats[cat] = {
        "total": len(items),
        "sub_categories": sub_stats
    }

meta = {
    "version": 1,
    "exported_at": datetime.now().isoformat(),
    "total_documents": len(results),
    "categories": sorted(cat_groups.keys()),
    "category_stats": category_stats,
}
with open(output_dir / '.export_info.json', 'w', encoding='utf-8') as f:
    json.dump(meta, f, ensure_ascii=False, indent=2)

# SQL 格式导出
sql_path = output_dir / 'memory_export.sql'
with open(sql_path, 'w', encoding='utf-8') as f:
    f.write('-- Memory Workstation 导出\n')
    f.write(f'-- 导出时间: {datetime.now().isoformat()}\n')
    f.write(f'-- 总文档: {len(results)}\n\n')
    f.write('CREATE TABLE IF NOT EXISTS memory_export (\n')
    f.write('    id INTEGER,\n')
    f.write('    file_path TEXT,\n')
    f.write('    label TEXT,\n')
    f.write('    importance TEXT,\n')
    f.write('    weight INTEGER,\n')
    f.write('    namespace TEXT,\n')
    f.write('    compact_content TEXT,\n')
    f.write('    content_category TEXT,\n')
    f.write('    sub_category TEXT,\n')
    f.write('    tags TEXT,\n')
    f.write('    memory_tier TEXT,\n')
    f.write('    create_time TEXT\n')
    f.write(');\n\n')
    for r in results:
        v = (
            r.doc_id,
            r.file_path.replace("'", "''").replace('\n', '\\n').replace('\r', ''),
            r.label.replace("'", "''").replace('\n', '\\n').replace('\r', ''),
            r.importance or '',
            r.weight or 0,
            r.namespace.replace("'", "''").replace('\n', '\\n').replace('\r', ''),
            r.summary.replace("'", "''").replace('\n', '\\n').replace('\r', ''),
            r.category.replace("'", "''").replace('\n', '\\n').replace('\r', ''),
            r.sub_category.replace("'", "''").replace('\n', '\\n').replace('\r', ''),
            r.tags.replace("'", "''").replace('\n', '\\n').replace('\r', '') if r.tags else '[]',
            r.memory_tier.replace("'", "''").replace('\n', '\\n').replace('\r', '') if r.memory_tier else '',
            r.create_time.replace("'", "''") if r.create_time else '',
        )
        f.write(f"INSERT INTO memory_export VALUES ({v[0]}, '{v[1]}', '{v[2]}', "
                f"'{v[3]}', {v[4]}, '{v[5]}', '{v[6]}', "
                f"'{v[7]}', '{v[8]}', '{v[9]}', '{v[10]}', '{v[11]}');\n")
print(f"SQL 导出: {sql_path}")

print(f"导出完成: {len(results)} 文档, {len(cat_groups)} 分类")
storage.close()
