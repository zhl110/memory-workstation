"""persistent subprocess wrapper for CLI tools — keeps process alive, avoids spawn overhead per request"""
import json
import logging
import subprocess
import threading
import time
from queue import Empty, Queue
from typing import Optional

logger = logging.getLogger(__name__)


class PersistentCLIProcess:
    """Persistent CLI subprocess that reads prompts from stdin in a loop.

    Spawns `mimo run --model <model> --no-continue` once and keeps it alive.
    Each call sends a prompt and reads the result, avoiding subprocess spawn overhead.
    
    进程管理：通过 self._proc 引用管理唯一子进程，确保启动/停止/重启都在同一个引用上操作，
    避免进程泄漏（旧版本曾因重复 Popen 导致无引用进程残留）。
    """

    def __init__(self, cmd: list[str], model: str = "mimo/mimo-auto", timeout: float = 60.0):
        self._cmd_base = cmd
        self._model = model
        self._timeout = timeout
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._req_queue: Queue = Queue()
        self._resp_map: dict[int, Queue] = {}
        self._req_id = 0
        self._reader_thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> bool:
        """Start the persistent process."""
        with self._lock:
            if self._proc and self._proc.poll() is None:
                return True

            cmd = self._cmd_base + [
                "run", "--model", self._model,
                "--no-continue",
            ]

            try:
                # 启动持久化进程：通过 stdin/stdout 通信，避免每次请求重新 spawn
                # 注意：此处是唯一的进程启动点，之前版本曾有重复 Popen 导致进程泄漏，已修复
                self._proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
                )
                self._running = True
                logger.info("Persistent CLI process started (pid=%d)", self._proc.pid)
                return True
            except Exception as e:
                logger.error("Failed to start persistent CLI: %s", e)
                self._running = False
                return False

    def run(self, prompt: str) -> Optional[str]:
        """Send prompt and get result.

        Falls back to one-shot subprocess if persistent mode fails.
        
        进程管理：优先使用持久化进程（self._proc），若进程未运行则自动重启，
        若持久化通信失败则降级为单次 subprocess 调用。
        """
        if not self._running or not self._proc or self._proc.poll() is not None:
            logger.warning("Persistent process not running, starting...")
            if not self.start():
                return self._oneshot(prompt)

        # Try persistent: write prompt to stdin, read from stdout
        try:
            with self._lock:
                if self._proc and self._proc.stdin and self._proc.stdout:
                    self._proc.stdin.write(prompt + "\n---END---\n")
                    self._proc.stdin.flush()

                    lines = []
                    while True:
                        line = self._proc.stdout.readline()
                        if not line or line.strip() == "---END---":
                            break
                        lines.append(line)

                    output = "".join(lines).strip()
                    if output:
                        return output

            # If no output, fallback to oneshot
            logger.debug("Persistent CLI produced no output, falling back to oneshot")
            return self._oneshot(prompt)

        except (BrokenPipeError, OSError) as e:
            logger.warning("Persistent CLI pipe broken (%s), restarting...", e)
            self.stop()
            if self.start():
                return self._oneshot(prompt)
            return None

    def _oneshot(self, prompt: str) -> Optional[str]:
        """Fallback: one-shot subprocess call."""
        cmd = self._cmd_base + [
            "run", "--model", self._model, "--no-continue", prompt
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True, timeout=self._timeout,
            )
            output = (result.stdout or b"").decode("utf-8", errors="replace").strip()
            return output if output else None
        except subprocess.TimeoutExpired:
            logger.warning("CLI oneshot timeout after %.0fs", self._timeout)
            return None
        except Exception as e:
            logger.error("CLI oneshot failed: %s", e)
            return None

    def stop(self):
        """Stop the persistent process.
        
        确保进程被终止并释放资源，避免僵尸进程。
        """
        with self._lock:
            self._running = False
            if self._proc:
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=5)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
                self._proc = None
            logger.info("Persistent CLI process stopped")


# Global cache of persistent processes keyed by (cmd_tuple, model)
_process_pool: dict[str, "PersistentCLIProcess"] = {}
_pool_lock = threading.Lock()


def get_persistent_cli(cmd: list[str], model: str = "mimo/mimo-auto") -> "PersistentCLIProcess":
    """Get or create a persistent CLI process."""
    key = f"{' '.join(cmd)}|{model}"
    with _pool_lock:
        if key not in _process_pool:
            _process_pool[key] = PersistentCLIProcess(cmd, model)
        return _process_pool[key]
