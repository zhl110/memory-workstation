"""写入审计模块 - 记录所有写入操作"""

import json
import os
import shutil
import threading
from datetime import datetime


class AuditLog:
    """审计日志管理器"""

    def __init__(self, log_path: str, max_size_mb: int = 10) -> None:
        """初始化审计日志

        Args:
            log_path: 日志文件路径
            max_size_mb: 日志文件最大大小（MB），超过后自动轮转
        """
        self.log_path = log_path
        self.max_size = max_size_mb * 1024 * 1024
        self._lock = threading.Lock()

    def _rotate_if_needed(self):
        """检查并轮转日志文件"""
        try:
            if os.path.exists(self.log_path) and os.path.getsize(self.log_path) > self.max_size:
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                rotated = f"{self.log_path}.{ts}"
                counter = 1
                while os.path.exists(rotated):
                    rotated = f"{self.log_path}.{ts}.{counter}"
                    counter += 1
                shutil.move(self.log_path, rotated)
                # 创建新的空日志文件
                with open(self.log_path, 'a'):
                    pass
        except Exception:
            pass

    def log(self, action: str, doc_id: int, details: dict | None = None) -> None:
        """记录审计日志

        Args:
            action: 操作类型（insert/update/delete）
            doc_id: 文档ID
            details: 附加详情
        """
        with self._lock:
            self._rotate_if_needed()
            entry = {
                'timestamp': datetime.now().isoformat(),
                'action': action,
                'doc_id': doc_id,
                'details': details or {}
            }
            with open(self.log_path, 'a') as f:
                f.write(json.dumps(entry) + '\n')
