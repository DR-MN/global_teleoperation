"""In-process transport: delivers payloads to subscribers via a background
dispatch thread. Used for the hardware-free demo and unit tests. Mimics the
async, lossy nature of a real link via an optional simulated delay / drop rate
so safety/watchdog behaviour can be exercised deterministically.
"""
from __future__ import annotations

import copy
import logging
import queue
import random
import threading
import time
from typing import Any, Dict, List

from .base import Transport, Handler

log = logging.getLogger(__name__)


class InProcTransport(Transport):
    def __init__(self, latency_s: float = 0.0, drop_rate: float = 0.0) -> None:
        self._subs: Dict[str, List[Handler]] = {}
        self._q: "queue.Queue[tuple[str, Dict[str, Any], float]]" = queue.Queue()
        self._latency_s = latency_s
        self._drop_rate = drop_rate
        self._running = True
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._dispatch, name="inproc-tx", daemon=True)
        self._thread.start()

    def publish(self, key: str, payload: Dict[str, Any]) -> None:
        if not self._running:
            return
        if self._drop_rate and random.random() < self._drop_rate:
            log.debug("inproc: dropped message on %s (simulated loss)", key)
            return
        # Deep-copy so the publisher can mutate its object after publishing.
        deliver_at = time.time() + self._latency_s
        self._q.put((key, copy.deepcopy(payload), deliver_at))

    def subscribe(self, key: str, handler: Handler) -> None:
        with self._lock:
            self._subs.setdefault(key, []).append(handler)

    def _dispatch(self) -> None:
        while self._running:
            try:
                key, payload, deliver_at = self._q.get(timeout=0.1)
            except queue.Empty:
                continue
            wait = deliver_at - time.time()
            if wait > 0:
                time.sleep(wait)
            with self._lock:
                handlers = list(self._subs.get(key, ()))
            for h in handlers:
                try:
                    h(payload)
                except Exception:  # never let a bad subscriber kill dispatch
                    log.exception("inproc: subscriber error on %s", key)

    def close(self) -> None:
        self._running = False
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)
