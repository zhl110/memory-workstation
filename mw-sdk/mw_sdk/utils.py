"""Agent 路径工具函数 — **这是 MW 数据路径的唯一源头**

所有 SDK 文件的数据目录路径都必须引用本文件定义的 _DEFAULT_DATA_DIR。
**禁止**在其他文件中重复硬编码 D:/MemoryWorkstation/.memory-workstation/。

注意事项：
- 改数据目录只改这里一处即可，其他文件通过 import 使用
- MW_AGENT_ID 环境变量决定哪个 Agent 的数据库被操作（不设则默认 claude）
- 合法的 Agent ID：claude, codex, mimo 等
"""
import os
from pathlib import Path

# ⚠️ 单源路径 — 所有 SDK 文件引用此常量，不要在其他文件重复写
#    优先级：MW_DATA_DIR 环境变量 → 旧版路径（向后兼容）→ ~/.memory-workstation
_DEFAULT_DATA_DIR = os.environ.get("MW_DATA_DIR") or (
    "D:/MemoryWorkstation/.memory-workstation"
    if Path("D:/MemoryWorkstation/.memory-workstation").exists()
    else str(Path.home() / ".memory-workstation")
)


def get_agent_db(name=None):
    """获取 Agent 数据库路径

    所有 Agent 共用 meta_agents.sqlite。
    exe 桌面版继续用 meta.sqlite（通过 get_agents_db_path 直接指定）。

    Returns:
        完整数据库路径字符串
    """
    return f"{_DEFAULT_DATA_DIR}/meta_agents.sqlite"


def get_agent_name():
    """获取当前 Agent 标识（不设环境变量则返回 'claude'）"""
    return os.environ.get("MW_AGENT_ID", "claude")


def get_agents_registry_path():
    """获取 Agent 注册表路径

    注册表位于 MW 数据目录下的 agents.json 文件。
    所有已注册的 Agent 信息都存储在此文件中。

    Returns:
        完整注册表路径字符串
    """
    return f"{_DEFAULT_DATA_DIR}/agents.json"


def validate_utf8(text: str) -> str:
    """校验 UTF-8 编码，拒绝非法序列

    Args:
        text: 要校验的字符串（None 会被当作空字符串处理）

    Returns:
        校验通过的原始字符串

    Raises:
        ValueError: 包含非法 UTF-8 序列或替换字符
    """
    if text is None:
        text = ""
    if not text:
        return text

    if '\ufffd' in text:
        raise ValueError("内容包含替换字符 U+FFFD，可能是编码错误")

    try:
        encoded = text.encode('utf-8', errors='strict')
        decoded = encoded.decode('utf-8', errors='strict')
    except (UnicodeEncodeError, UnicodeDecodeError) as e:
        raise ValueError(f"内容包含非法 UTF-8 序列: {e}")

    return text


def safe_truncate(text: str, max_len: int) -> str:
    """安全截断，避免截断多字节字符（emoji、中文、代理对）

    Args:
        text: 原始字符串
        max_len: 最大字符数

    Returns:
        截断后的字符串，保证最后一个字符完整
    """
    if text is None:
        text = ""
    if not text or len(text) <= max_len:
        return text

    truncated = text[:max_len]

    if len(truncated) > 0:
        last_char = truncated[-1]
        code = ord(last_char)

        if 0xD800 <= code <= 0xDBFF:
            truncated = truncated[:-1]
        elif 0xDC00 <= code <= 0xDFFF:
            truncated = truncated[:-2] if len(truncated) >= 2 else ""

    return truncated


def cpp_to_dict(obj):
    """将 C++ pybind11 对象转换为 Python dict

    用于 Mixin 层委派 C++ 方法后转换返回值。
    """
    return {attr: getattr(obj, attr) for attr in dir(obj) if not attr.startswith('_')}
