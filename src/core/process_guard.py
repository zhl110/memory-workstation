from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class ProcessGuard:
    def __init__(self, crash_limit: int = 3, cooldown_sec: int = 60):
        self.crash_limit = crash_limit
        self.cooldown_sec = cooldown_sec
        self._crash_times: deque[float] = deque()
        self._watchdog_thread: Optional[threading.Thread] = None
        self._main_pid: int = os.getpid()
        self._running = False
        self._callbacks: list[callable] = []

    def on_crash(self, callback: callable):
        self._callbacks.append(callback)

    def start_watchdog(self):
        self._running = True
        self._watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._watchdog_thread.start()
        logger.info("ProcessGuard watchdog started (pid=%d)", self._main_pid)

    def stop(self):
        self._running = False

    def _watchdog_loop(self):
        """定时执行注册的健康检查回调，回调抛异常则重启进程。
        当前无注册回调（_callbacks为空），此循环为空跑，预留供未来扩展。
        进程crash检测主要靠 signal_handler → check_and_restart() 路径。
        """
        while self._running:
            time.sleep(5)
            for cb in self._callbacks:
                try:
                    cb()
                except Exception as e:
                    logger.error("Watchdog callback error: %s", e)
                    self._record_crash()
                    if self._should_stop_restarting():
                        logger.critical("Crash limit reached, stopping auto-restart")
                        self._running = False
                        return
                    self._restart_process()
                    return

    def _record_crash(self):
        now = time.time()
        self._crash_times.append(now)
        while self._crash_times and now - self._crash_times[0] > self.cooldown_sec:
            self._crash_times.popleft()

    def _should_stop_restarting(self) -> bool:
        return len(self._crash_times) >= self.crash_limit

    def _restart_process(self):
        logger.info("Restarting process...")
        try:
            subprocess.Popen([sys.executable] + sys.argv)
            os._exit(0)
        except Exception as e:
            logger.error("Failed to restart: %s", e)

    def check_and_restart(self):
        self._record_crash()
        if self._should_stop_restarting():
            logger.critical("Crash limit %d reached in %ds, stopping",
                          self.crash_limit, self.cooldown_sec)
            return False
        self._restart_process()
        return True
