"""Orchestrator (TASK-10): triggers, coalescing, bounded concurrency, TTL,
budget; holdings re-checks outrank new-position research."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from ..domain.base import new_id, utcnow


@dataclass
class Task:
    task_id: str
    kind: str  # research | holding_review | fill_poll
    instrument_id: str
    priority: int  # lower = more urgent; holding reviews get 0
    created_at: datetime
    trace_id: str
    payload: dict = field(default_factory=dict)


class Orchestrator:
    """Bounded queue with per-instrument coalescing and priority.

    - duplicate triggers for the same instrument+kind coalesce into one task
      (no unbounded model calls per tick);
    - expired theses never enqueue fast-path holding reviews (caller checks);
    - queue-full and rejections are observable (counters);
    - after the deadline a task is dropped with status, never traded late.
    """

    def __init__(self, capacity: int = 200, max_concurrent: int = 4) -> None:
        self.capacity = capacity
        self.max_concurrent = max_concurrent
        self._queue: deque[Task] = deque()
        self._pending: set[tuple[str, str]] = set()
        self._in_flight = 0
        self._lock = threading.RLock()
        self.counters = {"submitted": 0, "coalesced": 0, "rejected_full": 0,
                         "dropped_deadline": 0, "executed": 0, "failed": 0}

    def submit(self, kind: str, instrument_id: str, payload: dict | None = None,
               now: datetime | None = None, priority: int | None = None) -> str | None:
        now = now or utcnow()
        p = priority if priority is not None else (0 if kind == "holding_review" else 10)
        with self._lock:
            key = (kind, instrument_id)
            if key in self._pending:
                self.counters["coalesced"] += 1
                return None
            if len(self._queue) >= self.capacity:
                self.counters["rejected_full"] += 1
                return None
            task = Task(task_id=new_id("task"), kind=kind,
                        instrument_id=instrument_id, priority=p,
                        created_at=now, trace_id=new_id("tr"),
                        payload=payload or {})
            self._pending.add(key)
            self._queue.append(task)
            self.counters["submitted"] += 1
            return task.task_id

    def _pop(self) -> Task | None:
        with self._lock:
            if not self._queue:
                return None
            # holdings first, then FIFO
            task = min(self._queue, key=lambda t: (t.priority, t.created_at))
            self._queue.remove(task)
            return task

    def run_once(self, handler: Callable[[Task], bool], deadline: datetime | None = None,
                 now: datetime | None = None) -> int:
        """Execute up to max_concurrent tasks via handler (sync). Handler
        returns success. Deadline-checked before dispatch, never after."""
        executed = 0
        while executed < self.max_concurrent:
            task = self._pop()
            if task is None:
                break
            checked_at = now or utcnow()
            task_deadline = task.payload.get("deadline", deadline)
            if task_deadline is not None and checked_at > task_deadline:
                self.counters["dropped_deadline"] += 1
                task.payload["dropped"] = True
                with self._lock:
                    self._pending.discard((task.kind, task.instrument_id))
                continue
            try:
                ok = handler(task)
                self.counters["executed" if ok else "failed"] += 1
            except Exception:  # noqa: BLE001
                self.counters["failed"] += 1
            finally:
                with self._lock:
                    self._pending.discard((task.kind, task.instrument_id))
            executed += 1
        return executed

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "queued": len(self._queue),
                "pending_keys": sorted(f"{k}:{i}" for k, i in self._pending),
                "counters": dict(self.counters),
            }
