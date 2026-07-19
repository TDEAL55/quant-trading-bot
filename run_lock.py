from __future__ import annotations

import contextlib
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


class RunLockError(RuntimeError):
    pass


class RunLockBusyError(RunLockError):
    pass


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass
class RunLockState:
    owner: str
    acquired_at: str
    pid: int


class DailyRunLock:
    def __init__(self, lock_path: str | Path, stale_after_seconds: int = 7200, owner: str | None = None):
        self.lock_path = Path(lock_path)
        self.stale_after_seconds = int(stale_after_seconds)
        self.owner = owner or f"{os.getpid()}@{os.uname().nodename if hasattr(os, 'uname') else 'host'}"
        self._acquired = False

    def _read_state(self) -> RunLockState | None:
        if not self.lock_path.exists():
            return None
        try:
            payload = json.loads(self.lock_path.read_text(encoding="utf-8"))
            return RunLockState(
                owner=str(payload.get("owner") or ""),
                acquired_at=str(payload.get("acquired_at") or ""),
                pid=int(payload.get("pid") or 0),
            )
        except Exception:
            return None

    def _is_stale(self, state: RunLockState | None) -> bool:
        if state is None:
            return False
        acquired_at = _parse_iso(state.acquired_at)
        if acquired_at is None:
            return True
        age = (datetime.now(timezone.utc) - acquired_at).total_seconds()
        return age > self.stale_after_seconds

    def acquire(self) -> RunLockState:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        state = self._read_state()
        if state is not None and not self._is_stale(state):
            raise RunLockBusyError(f"Daily run lock is held by {state.owner}")
        if state is not None and self._is_stale(state):
            with contextlib.suppress(Exception):
                self.lock_path.unlink()

        new_state = RunLockState(owner=self.owner, acquired_at=_utc_iso(), pid=os.getpid())
        payload = {"owner": new_state.owner, "acquired_at": new_state.acquired_at, "pid": new_state.pid}
        try:
            fd = os.open(self.lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError as exc:
            raise RunLockBusyError("Daily run lock could not be acquired") from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True)
                handle.write("\n")
        except Exception:
            with contextlib.suppress(Exception):
                self.lock_path.unlink()
            raise
        self._acquired = True
        return new_state

    def release(self) -> None:
        if self._acquired and self.lock_path.exists():
            with contextlib.suppress(Exception):
                self.lock_path.unlink()
        self._acquired = False

    def __enter__(self) -> "DailyRunLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


@contextmanager
def run_lock(lock_path: str | Path, stale_after_seconds: int = 7200, owner: str | None = None) -> Iterator[RunLockState]:
    lock = DailyRunLock(lock_path=lock_path, stale_after_seconds=stale_after_seconds, owner=owner)
    state = lock.acquire()
    try:
        yield state
    finally:
        lock.release()
