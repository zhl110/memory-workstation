"""C++ ONNX embedding — thin Python wrapper.

Usage:
    from mw_sdk.embed import init_embedding, MODELS_DIR
    from mw_sdk._core import storage_load_embedding
    init_embedding(storage)  # auto-finds MODELS_DIR
    # or: init_embedding(storage, r"path/to/models")
"""

from pathlib import Path

MODELS_DIR = str(Path(__file__).parent / "_core" / "models")

_initialized = False


def init_embedding(storage, model_dir=None) -> bool:
    """Initialize C++ ONNX engine."""
    from mw_sdk._core import storage_load_embedding
    global _initialized
    if model_dir is None:
        model_dir = MODELS_DIR
    ok = storage_load_embedding(storage, model_dir)
    if ok:
        _initialized = True
    return ok


def is_initialized() -> bool:
    return _initialized
