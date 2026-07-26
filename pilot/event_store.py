"""运行时事件存储（A.7.4.5）。

- InMemoryEventStore：测试/演示用；
- JsonlEventStore：本地 append-only JSONL，按 run_id 隔离文件，支持 cursor 续读，写失败 fail-closed。

不得把 UI localStorage 当作科学运行权威。导入时零副作用（不建线程/网络/连接）。
run 期真实 JSONL 不提交仓库（.gitignore 覆盖运行目录）。
"""

from __future__ import annotations

import json
import os
import re
import threading
from typing import Optional

from pilot.runtime_events import RuntimeEvent

_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")   # 防路径穿越：run_id 仅限安全字符


def _check_run_id(run_id: str) -> str:
    if not _SAFE_RUN_ID.match(run_id or ""):
        raise ValueError(f"非法 run_id（仅允许 [A-Za-z0-9_.-]）：{run_id!r}")
    return run_id


def _check_monotonic(existing: list, ev: RuntimeEvent) -> None:
    if any(e.event_id == ev.event_id for e in existing):
        raise ValueError(f"event_id 重复：{ev.event_id}")
    expected = (existing[-1].sequence + 1) if existing else 0
    if ev.sequence != expected:
        raise ValueError(f"sequence 非单调递增：得到 {ev.sequence}，应为 {expected}")


class InMemoryEventStore:
    """线程安全的内存事件存储（按 run_id 隔离）。"""

    def __init__(self):
        self._runs: dict[str, list] = {}
        self._lock = threading.Lock()

    def append(self, event: RuntimeEvent) -> None:
        _check_run_id(event.run_id)
        with self._lock:
            events = self._runs.setdefault(event.run_id, [])
            _check_monotonic(events, event)
            events.append(event)

    def list(self, run_id: str, after_sequence: int = -1) -> list:
        _check_run_id(run_id)
        with self._lock:
            return [e for e in self._runs.get(run_id, []) if e.sequence > after_sequence]

    def run_ids(self) -> list:
        with self._lock:
            return list(self._runs.keys())

    def exists(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._runs


class JsonlEventStore:
    """本地 append-only JSONL 存储；每个 run_id 一个文件；写失败 fail-closed（抛错，不吞）。"""

    def __init__(self, root_dir: str):
        self.root = os.path.abspath(root_dir)
        os.makedirs(self.root, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, run_id: str) -> str:
        return os.path.join(self.root, f"{_check_run_id(run_id)}.jsonl")

    def append(self, event: RuntimeEvent) -> None:
        with self._lock:
            existing = self._read(event.run_id)
            _check_monotonic(existing, event)
            line = json.dumps(event.model_dump(), ensure_ascii=False, sort_keys=True)
            with open(self._path(event.run_id), "a", encoding="utf-8", newline="\n") as f:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())          # 落盘；写失败自然抛错（fail-closed）

    def _read(self, run_id: str) -> list:
        path = self._path(run_id)
        if not os.path.exists(path):
            return []
        out = []
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if raw:
                    out.append(RuntimeEvent.model_validate_json(raw))
        return out

    def list(self, run_id: str, after_sequence: int = -1) -> list:
        with self._lock:
            return [e for e in self._read(run_id) if e.sequence > after_sequence]

    def run_ids(self) -> list:
        with self._lock:
            return [fn[:-6] for fn in os.listdir(self.root) if fn.endswith(".jsonl")]

    def exists(self, run_id: str) -> bool:
        return os.path.exists(self._path(run_id))


__all__ = ["InMemoryEventStore", "JsonlEventStore"]
