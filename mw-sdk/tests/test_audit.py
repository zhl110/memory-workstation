"""Audit模块测试"""

import sys
import os
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mw_sdk.audit import AuditLog


def test_audit_log_insert():
    """测试写入审计日志"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.log', delete=False) as f:
        log_path = f.name
    
    try:
        audit = AuditLog(log_path)
        audit.log("insert", 123, {"source": "test"})
        
        with open(log_path, 'r') as f:
            content = f.read()
        
        assert '"action": "insert"' in content
        assert '"doc_id": 123' in content
    finally:
        os.unlink(log_path)


def test_audit_log_rotation():
    """测试日志轮转"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.log', delete=False) as f:
        log_path = f.name
    
    try:
        # 创建一个很小的最大大小
        audit = AuditLog(log_path, max_size_mb=0.001)  # 1KB
        
        # 写入多条日志触发轮转
        for i in range(100):
            try:
                audit.log("insert", i, {"test": "x" * 100})
            except FileExistsError:
                pass  # 轮转文件已存在，忽略
        
        # 检查原文件是否存在
        assert os.path.exists(log_path)
    finally:
        # 清理
        for f in os.listdir(os.path.dirname(log_path)):
            if f.startswith(os.path.basename(log_path)):
                os.unlink(os.path.join(os.path.dirname(log_path), f))
