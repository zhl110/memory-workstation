from enum import Enum


class DocumentLabel(str, Enum):
    CHAT_LOG = "chat_log"
    COMPACT_ARCHIVE = "compact_archive"
    MEMORY_LAYER = "memory_layer"
    PLANNING_DOC = "planning_doc"
    SELF_IMPROVE_LEARN = "self_improve_learn"
    META_RULE = "meta_rule"
    CONFIG_INVENTORY = "config_inventory"
    UNKNOWN = "unknown"


class MemoryTier(str, Enum):
    SHORT = "short"
    WORK = "work"
    LONG = "long"
    ARCHIVE = "archive"
    META = "meta"


class ClientType(str, Enum):
    MCP_CLAUDE = "mcp_claude"
    MCP_CODEX = "mcp_codex"
    API = "api"


class ScanPriority(str, Enum):
    MANUAL_FIRST = "manual_first"
    INCR = "incr"
    BACKUP = "backup"


LABEL_TO_TIER = {
    DocumentLabel.META_RULE: MemoryTier.META,
    DocumentLabel.CONFIG_INVENTORY: MemoryTier.LONG,
    DocumentLabel.PLANNING_DOC: MemoryTier.LONG,
    DocumentLabel.SELF_IMPROVE_LEARN: MemoryTier.LONG,
    DocumentLabel.MEMORY_LAYER: MemoryTier.LONG,
    DocumentLabel.CHAT_LOG: MemoryTier.SHORT,
    DocumentLabel.COMPACT_ARCHIVE: MemoryTier.ARCHIVE,
    DocumentLabel.UNKNOWN: MemoryTier.SHORT,
}
