"""LLM Manager — 纯 Embed 引擎（V10：砍掉所有 classify/LLM 后端）

V10 变更：
- 删除 _resolve_backend()、ModelStatus 枚举、所有 classify/summarize/batch 方法
- 删除 _start_idle_timer/_reset_idle_timer/_auto_unload/_log_error/get_connection_info
- 保留 load_embed_model()/embed()/has_embed_model/unload()
- __init__ 只尝试加载 embed 模型，不再加载 classify
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from ..core.config import Config

logger = logging.getLogger(__name__)


class LLMManager:
    """LLM 管理器 — 纯 Embed 引擎（V10：无 LLM 后端，零 classify 依赖）"""

    def __init__(self, config: Config):
        self.config = config
        self._lock = threading.Lock()

        # 嵌入模型状态
        self._embed_model = None
        self._embed_available = False

        self._load_config()

    def _load_config(self):
        """加载 embed 相关配置"""
        pass  # embed 配置通过 config.llm.embed 直接读取

    # ==================== 嵌入模型 ====================

    @property
    def has_embed_model(self) -> bool:
        return self._embed_available and self._embed_model is not None

    def load_embed_model(self) -> bool:
        model_path = self.config.llm.embed.model_path
        if not model_path:
            return False

        from pathlib import Path
        embed_path = Path(model_path)
        if not embed_path.is_absolute():
            embed_path = Path(__file__).resolve().parent.parent.parent / embed_path

        if not embed_path.exists():
            logger.warning("Embed model not found: %s", embed_path)
            return False

        try:
            from llama_cpp import Llama
            self._embed_model = Llama(
                model_path=str(embed_path),
                n_ctx=self.config.llm.embed.n_ctx,
                n_gpu_layers=self.config.llm.embed.n_gpu_layers,
                verbose=False,
                embedding=True,
            )
            self._embed_available = True
            logger.info("Embed model loaded: %s (dim=768)", embed_path.name)
            return True
        except Exception as e:
            logger.error("Failed to load embed model: %s", e)
            self._embed_model = None
            self._embed_available = False
            return False

    def embed(self, text: str) -> Optional[list[float]]:
        if not self._embed_available or not self._embed_model or not text or not text.strip():
            return None
        try:
            result = self._embed_model.embed(text.strip())
            vec = result if isinstance(result, list) else result.get("embedding", [])
            if not vec or len(vec) != 768:
                return None
            norm = sum(x * x for x in vec) ** 0.5
            if norm > 0:
                vec = [x / norm for x in vec]
            return vec
        except Exception as e:
            logger.warning("Embed failed: %s", e)
            return None

    # ==================== 通用方法 ====================

    def unload(self):
        with self._lock:
            self._embed_model = None
            self._embed_available = False
            logger.info("Embed model unloaded")
