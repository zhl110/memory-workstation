from __future__ import annotations

import logging
import os
import re
import secrets
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ..core.enums import ScanPriority

"""
配置系统说明
============

配置层次：
1. config.toml - 用户配置文件（首次运行自动生成）
2. LLMClassifyConfig/LLMEmbedConfig - 代码中的默认值
3. load_from_dict - 将 config.toml 映射到 dataclass

新增配置项步骤：
1. 在 config.toml 对应 section 中添加字段（用户配置）
2. 在对应的 dataclass 中添加字段及默认值（代码默认值）
3. 如需兼容旧字段名，在 load_from_dict 中添加映射
4. 在代码中通过 self.config.llm.classify.xxx 读取，禁止硬编码

示例：
# 1. config.toml [llm.classify] 中添加：
# mimo_timeout = 30

# 2. LLMClassifyConfig 中添加：
# mimo_timeout: int = 30

# 3. 代码中使用：
# timeout = self.config.llm.classify.mimo_timeout
"""

def _resolve_memory_home() -> str:
    """读取 exe 旁边的 MemoryWorkstation.cfg，里面写数据目录路径。没有则创建默认值。"""
    # ── 环境变量优先（run.py 开发模式用） ──
    dev_home = os.environ.get("MW_DEV_DATA_HOME")
    if dev_home:
        return dev_home

    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cfg_path = os.path.join(base, "MemoryWorkstation.cfg")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r") as f:
                path = f.read().strip()
            if path:
                return path
        except Exception:
            pass
    default = os.path.join("D:\\MemoryWorkstation", ".memory-workstation")
    try:
        os.makedirs(base, exist_ok=True)
        with open(cfg_path, "w") as f:
            f.write(default + "\n")
    except Exception:
        pass
    return default

_MEMORY_HOME = _resolve_memory_home()

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(_MEMORY_HOME) / "config.toml"


@dataclass
class GlobalConfig:
    app_name: str = "MemoryWorkbench"
    run_silence: bool = True
    lock_model_forever: bool = False
    auto_startup: bool = False
    disk_low_warn_threshold: int = 5
    reboot_crash_limit: int = 3


@dataclass
class ScanConfig:
    disk_black_list: list[str] = field(default_factory=lambda: [
        "C:\\Windows", "C:\\Program Files", "C:\\ProgramData"
    ])
    custom_white_path: list[str] = field(default_factory=list)
    agent_paths: list[str] = field(default_factory=lambda: [
        "~/.claude/projects", "~/.codex"
    ])
    single_file_max_size_mb: int = 5
    ignore_suffix: list[str] = field(default_factory=lambda: [
        ".tmp", ".bak", ".swp", ".cache"
    ])
    auto_hdd_speed_limit: int = 20
    auto_ssd_speed_limit: int = 80
    scan_priority: str = ScanPriority.MANUAL_FIRST.value


@dataclass
class LLMClassifyConfig:
    """LLM 分类模型配置（V10：砍到只剩 embed 辅助字段）

    V10 变更：删除 provider/api_key/api_model/api_base_url/classify_prompt/
    summarize_prompt/experience_prompt/n_ctx/n_gpu_layers/max_tokens/temperature/
    timeout_sec/idle_unload_min（exe 不再依赖外部 LLM）
    """
    use_keyword_filter: bool = True  # 启用关键词预筛选（pipeline.py 仍使用）


@dataclass
class LLMEmbedConfig:
    """LLM 嵌入模型配置（用于向量搜索）"""
    model_path: str = "./local_llm/embed/nomic-embed-text-v1.5.Q4_K_M.gguf"  # GGUF 模型路径
    n_ctx: int = 4096  # 上下文窗口大小
    n_gpu_layers: int = 0  # GPU 层数（0=CPU，embed 用 CPU 即可）
    bundled: bool = True  # 是否为内置模型


@dataclass
class LLMConfig:
    classify: LLMClassifyConfig = field(default_factory=LLMClassifyConfig)
    embed: LLMEmbedConfig = field(default_factory=LLMEmbedConfig)


@dataclass
class StorageConfig:
    db_path: str = os.path.join(_MEMORY_HOME, "meta.sqlite")
    vector_path: str = os.path.join(_MEMORY_HOME, "vector.lance")
    snapshot_dir: str = os.path.join(_MEMORY_HOME, "snapshot")
    backup_interval_h: int = 2
    max_snapshot_count: int = 10
    enable_wal: bool = True


@dataclass
class MCPConfig:
    enable: bool = True
    multi_client: bool = True
    heartbeat_interval: int = 3


@dataclass
class APIConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    token: str = "MW1yX2J3Z4Q5L6R7T8S9K0="
    req_limit_per_sec: int = 5


@dataclass
class MemoryLifeConfig:
    chat_log_expire_day: int = 30
    short_memory_expire_day: int = 7
    archive_permanent: bool = True
    self_improve_permanent: bool = True
    planning_permanent: bool = True


@dataclass
class TokenLimitConfig:
    per_entry: int = 200
    total_return: int = 350
    output_max: int = 300


@dataclass
class LogConfig:
    log_level: str = "INFO"
    single_file_max_mb: int = 50
    max_log_file_count: int = 5
    separate_error_log: bool = True


@dataclass
class Config:
    global_: GlobalConfig = field(default_factory=GlobalConfig)
    scan: ScanConfig = field(default_factory=ScanConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    mcp: MCPConfig = field(default_factory=MCPConfig)
    api: APIConfig = field(default_factory=APIConfig)
    memory_life: MemoryLifeConfig = field(default_factory=MemoryLifeConfig)
    token_limit: TokenLimitConfig = field(default_factory=TokenLimitConfig)
    log: LogConfig = field(default_factory=LogConfig)

    def _apply_dict(self, target: object, data: dict):
        """将字典值应用到对象属性
        
        规则：
        1. 字段名中的连字符 '-' 转为下划线 '_'
        2. 只应用 target 中已存在的属性（忽略未知字段）
        3. 类型转换由 dataclass 自动处理
        """
        for key, value in data.items():
            attr = key.replace("-", "_")
            if hasattr(target, attr):
                setattr(target, attr, value)

    def load_from_dict(self, raw: dict):
        """将 config.toml 字典映射到 dataclass
        
        字段映射规则：
        1. 直接映射：字段名相同，直接赋值
        2. 旧字段名映射：如 local_timeout_sec -> timeout_sec
        3. 嵌套映射：llm.classify -> self.llm.classify
        
        新增字段时，只需在 dataclass 中添加，无需修改此方法。
        """
        section_map = {
            "global": "global_",
            "scan": "scan",
            "llm": "llm",
            "storage": "storage",
            "mcp": "mcp",
            "api": "api",
            "memory_life": "memory_life",
            "token_limit": "token_limit",
            "log": "log",
        }
        for section, attr in section_map.items():
            if section in raw:
                target = getattr(self, attr)
                section_data = raw[section]
                if section == "llm":
                    if "classify" in section_data:
                        self._apply_dict(target.classify, section_data["classify"])
                    if "embed" in section_data:
                        self._apply_dict(target.embed, section_data["embed"])
                else:
                    self._apply_dict(target, section_data)


def _cleanup_redundant_config(config_path: Path, raw: dict):
    """自动清理 config.toml 中跟代码默认值重复的配置项，保持 config.toml 最小化"""
    defaults = Config()

    # 收集需要移除的 (section_key, key) 对
    # 只处理标量值（字符串/数字/布尔），跳过数组和嵌套 dict
    SCALAR_TYPES = (str, int, float, bool)
    to_remove = set()

    # 顶层 section 映射
    section_map = {
        "global": "global_", "scan": "scan",
        "storage": "storage", "mcp": "mcp",
        "api": "api", "memory_life": "memory_life",
        "token_limit": "token_limit", "log": "log",
    }
    for sec, attr in section_map.items():
        if sec not in raw:
            continue
        target = getattr(defaults, attr)
        for key, value in raw[sec].items():
            if not isinstance(value, SCALAR_TYPES):
                continue
            py_key = key.replace("-", "_")
            if hasattr(target, py_key) and value == getattr(target, py_key):
                to_remove.add((sec, key))

    # llm.classify / llm.embed 嵌套 section
    if "llm" in raw:
        for subsection in ("classify", "embed"):
            if subsection not in raw["llm"]:
                continue
            target = getattr(getattr(defaults, "llm"), subsection)
            for key, value in raw["llm"][subsection].items():
                if not isinstance(value, SCALAR_TYPES):
                    continue
                py_key = key.replace("-", "_")
                if hasattr(target, py_key) and value == getattr(target, py_key):
                    to_remove.add((subsection, key))

    if not to_remove:
        return

    # 文本级删除对应行
    try:
        lines = config_path.read_text("utf-8").splitlines(keepends=True)
    except Exception:
        return

    # 构建 key→行号映射（忽略注释行和空行）
    key_lines = {}  # (section, key) → set of line indices
    current_sec = ""
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or stripped == "":
            # 检查是否是 section header 后面的注释
            pass
        sec_match = re.match(r'^\[([\w.]+)\]', stripped)
        if sec_match:
            current_sec = sec_match.group(1)
            continue
        if "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            key_lines[(current_sec, key)] = i

    # 找出要删除的行号（包括上方紧邻的注释行）
    remove_lines = set()
    for sec, key in to_remove:
        # 匹配 [llm] 下的 classify 键要映射到 llm.classify section
        for (file_sec, file_key), line_no in key_lines.items():
            if file_key == key and (file_sec == sec or file_sec.endswith("." + sec)):
                remove_lines.add(line_no)
                # 删除上方紧邻的注释行
                j = line_no - 1
                while j >= 0 and (lines[j].strip().startswith("#") or lines[j].strip() == ""):
                    remove_lines.add(j)
                    j -= 1

    if not remove_lines:
        return

    new_lines = [l for i, l in enumerate(lines) if i not in remove_lines]
    config_path.write_text("".join(new_lines), "utf-8")
    logger.info("Config cleaned: removed %d redundant entries (%d -> %d lines)",
                len(to_remove), len(lines), len(new_lines))


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


def _resolve_storage_paths(cfg: Config):
    """V9: 将存储相对路径解析为绝对路径（基于 _MEMORY_HOME）
    
    config.toml 中可能写了 ./memory_storage/... 这样的相对路径，
    exe 运行时 CWD 不一定在数据目录，需要统一解析。
    如果解析后文件不存在，回退到 dataclass 默认值（_MEMORY_HOME 下）。
    """
    home = Path(_MEMORY_HOME)
    defaults = Config()  # 取 dataclass 默认值作为兜底
    for field_name in ("db_path", "vector_path", "snapshot_dir"):
        val = getattr(cfg.storage, field_name, "")
        default_val = getattr(defaults.storage, field_name, "")
        if val and not Path(val).is_absolute():
            resolved = home / val
            # 如果解析后的路径不存在，回退到默认值（直接在 _MEMORY_HOME 下）
            if not resolved.exists() and Path(default_val).exists():
                setattr(cfg.storage, field_name, default_val)
            else:
                setattr(cfg.storage, field_name, str(resolved))


def load_config(path: Path | str | None = None) -> Config:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    cfg = Config()

    if not config_path.exists():
        logger.warning("Config file not found at %s, generating with random token", config_path)
        cfg.api.token = _generate_token()
        _save_default_config(config_path, cfg)
        logger.info("Default config created at %s", config_path)
        return cfg

    try:
        with open(config_path, "rb") as f:
            raw = tomllib.load(f)
        cfg.load_from_dict(raw)

        # V9: 解析存储路径 — 相对路径基于 _MEMORY_HOME，避免 exe 运行时 CWD 不对
        _resolve_storage_paths(cfg)

        # 加载后自动清理跟代码默认值重复的配置项
        _cleanup_redundant_config(config_path, raw)

        if cfg.api.token == "CHANGE_ME_ON_FIRST_RUN" or not cfg.api.token:
            cfg.api.token = _generate_token()
            _save_config_token(config_path, cfg.api.token)
            logger.warning("Insecure default token replaced with random token")

        logger.info("Config loaded from %s", config_path)
    except Exception as e:
        logger.error("Failed to load config from %s: %s", config_path, e)

    return cfg


def _save_default_config(path: Path, cfg: Config):
    content = f"""# Memory Workstation 配置文件
# 首次运行自动生成，重启程序后生效

[api]
host = "127.0.0.1"
port = 8765
token = "{cfg.api.token}"
req_limit_per_sec = 5
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _save_config_token(path: Path, token: str):
    try:
        content = path.read_text(encoding="utf-8")
        old = 'token = "CHANGE_ME_ON_FIRST_RUN"'
        new = f'token = "{token}"'
        if old in content:
            content = content.replace(old, new)
            path.write_text(content, encoding="utf-8")
            logger.info("Config token updated in %s", path)
    except Exception as e:
        logger.error("Failed to update config token: %s", e)
