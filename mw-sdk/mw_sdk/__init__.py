"""mw-sdk: Memory Workstation 纯数据引擎

SDK 不再调用任何 LLM API。classify / fuse / rerank 由上层 Agent 本人完成。
SDK 只负责：SQLite 存取 + FTS5 索引 + 交叉引用 + 导出/备份。

架构：
  skill (Claude Code) ─┐
  skill (Codex)      ──┼─ Agent 本人做分类/融合，调 sdk 存取
  exe (桌面软件)      ──┘
                   │ import
                   ▼
              MemoryClient (本文件)

用法：
    from mw_sdk import MemoryClient
    from mw_sdk.utils import get_agent_db

    m = MemoryClient(get_agent_db())
    m.init_schema()
"""

from .client import MemoryClient
from .utils import get_agent_db, get_agent_name, get_agents_registry_path

__version__ = "0.0.17"
__all__ = ["MemoryClient", "get_agent_db", "get_agent_name", "get_agents_registry_path"]
