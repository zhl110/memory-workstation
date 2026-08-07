"""导出记忆为分类结构 — 委托 main.py 的 _export_memories 执行"""
import sys, os

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(_PROJECT_ROOT)
sys.path.insert(0, _PROJECT_ROOT)
sys.stdout.reconfigure(encoding='utf-8')

from src.main import AppContext

ctx = AppContext()
ctx._setup_logging()
ctx.storage = __import__('src.storage.manager', fromlist=['StorageManager']).StorageManager(
    db_path=ctx.config.storage.db_path,
    vector_path=ctx.config.storage.vector_path,
    snapshot_dir=ctx.config.storage.snapshot_dir,
)
ctx.storage.init()
ctx.storage._ctx = ctx

ctx._export_memories()

import json
from pathlib import Path
from src.core.config import _MEMORY_HOME
out = Path(_MEMORY_HOME) / 'memory_export'
meta_path = out / '.export_info.json'
if meta_path.exists():
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    print(f'\n导出完成: {meta["total_documents"]} 条, {len(meta["categories"])} 个分类')
    for cat, stats in meta.get("category_stats", {}).items():
        subs = ', '.join(f'{k}({v})' for k, v in stats["sub_categories"].items())
        print(f'  {cat}: {stats["total"]}条 [{subs}]')

ctx.storage.close()
print('\nDone.')
