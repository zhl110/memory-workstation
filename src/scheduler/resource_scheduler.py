from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

BATCH_SIZE = 5
BATCH_TIMEOUT = 2


class ResourceScheduler:
    def __init__(self):
        self._classify_queue: queue.Queue = queue.Queue()
        self._classify_worker: Optional[threading.Thread] = None
        self._running = False
        self._process_fn: Optional[Callable[[int, str], None]] = None
        self._total_submitted = 0
        self._completed_count = 0
        self._current_item: Optional[dict] = None

    def set_process_fn(self, fn: Callable[[int, str], None]):
        self._process_fn = fn

    def start(self):
        self._running = True
        self._classify_worker = threading.Thread(target=self._classify_loop, daemon=True)
        self._classify_worker.start()
        logger.info("ResourceScheduler started")

    def stop(self):
        self._running = False

    def submit_classify(self, doc_id: int, content: str, filepath: str = ""):
        self._classify_queue.put((doc_id, content, filepath))
        self._total_submitted += 1

    def mark_completed(self):
        self._completed_count += 1
        self._current_item = None

    def get_progress(self) -> dict:
        return {
            "total": self._total_submitted,
            "completed": self._completed_count,
            "pending": self._classify_queue.qsize(),
            "current_item": self._current_item,
        }

    def reset_progress(self):
        self._total_submitted = 0
        self._completed_count = 0

    def _classify_loop(self):
        while self._running:
            batch = []
            try:
                item = self._classify_queue.get(timeout=BATCH_TIMEOUT)
                if len(item) == 3:
                    batch.append(item)
                else:
                    batch.append((item[0], item[1], ""))
            except queue.Empty:
                continue
            
            while len(batch) < BATCH_SIZE:
                try:
                    item = self._classify_queue.get_nowait()
                    if len(item) == 3:
                        batch.append(item)
                    else:
                        batch.append((item[0], item[1], ""))
                except queue.Empty:
                    break
            
            for doc_id, content, filepath in batch:
                if self._process_fn:
                    try:
                        self._current_item = {"doc_id": doc_id, "filepath": filepath}
                        self._process_fn(doc_id, content, filepath)
                    except Exception as e:
                        logger.error("Classify task failed for doc %d: %s", doc_id, e)
                    finally:
                        self.mark_completed()

    @property
    def classify_queue_size(self) -> int:
        return self._classify_queue.qsize()
