"""Persistent state for the watcher.

A single JSON file at config.paths.state_path; atomic writes via tempfile +
os.replace; advisory file lock via fcntl. See SPEC.md §8.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Status enum (SPEC.md §8.1)
STATUS_COORDINATOR_ONLY = "COORDINATOR_ONLY"
STATUS_CLOSED = "CLOSED"
STATUS_FCFS_OPEN = "FCFS_OPEN"
STATUS_PENDING = "PENDING"
STATUS_UNKNOWN = "UNKNOWN"
STATUS_ROW_NOT_FOUND = "ROW_NOT_FOUND"

KNOWN_STATUSES = {
    STATUS_COORDINATOR_ONLY,
    STATUS_CLOSED,
    STATUS_FCFS_OPEN,
    STATUS_PENDING,
    STATUS_UNKNOWN,
    STATUS_ROW_NOT_FOUND,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class CourseState:
    last_check_at: Optional[str] = None
    last_status: Optional[str] = None
    last_status_change_at: Optional[str] = None
    registered_at: Optional[str] = None
    registered_group: Optional[str] = None
    consecutive_login_failures: int = 0
    consecutive_http_failures: int = 0
    # Notification dedup: timestamps of the last failure notification we sent
    # for each kind. Cleared when the failure spell ends (we get a successful
    # cycle), at which point we emit a "✅ recovered" notice once.
    last_login_failure_email_at: Optional[str] = None
    last_http_failure_email_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CourseState":
        out = cls()
        for k in out.__dict__.keys():
            if k in d:
                setattr(out, k, d[k])
        return out


@dataclass
class State:
    courses: Dict[str, CourseState] = field(default_factory=dict)
    last_heartbeat_email_at: Optional[str] = None
    schema_version: int = SCHEMA_VERSION

    def course(self, code: str) -> CourseState:
        if code not in self.courses:
            self.courses[code] = CourseState()
        return self.courses[code]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "courses": {k: v.to_dict() for k, v in self.courses.items()},
            "last_heartbeat_email_at": self.last_heartbeat_email_at,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "State":
        version = d.get("schema_version", SCHEMA_VERSION)
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"state.json schema_version {version} is newer than this build "
                f"({SCHEMA_VERSION}). Refusing to proceed."
            )
        courses_raw = d.get("courses", {}) or {}
        courses = {k: CourseState.from_dict(v) for k, v in courses_raw.items()}
        return cls(
            courses=courses,
            last_heartbeat_email_at=d.get("last_heartbeat_email_at"),
            schema_version=SCHEMA_VERSION,
        )


def load_state(path: Path) -> State:
    if not path.exists():
        log.info("No prior state file; starting fresh at %s", path)
        return State()
    try:
        with path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
    except json.JSONDecodeError as e:
        # Don't silently drop corrupted state — that would hide a serious bug.
        raise RuntimeError(f"state.json at {path} is corrupted: {e}") from e
    return State.from_dict(data)


def save_state(path: Path, state: State) -> None:
    """Atomic write: tmp file + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file in the same directory so os.replace is atomic.
    fd, tmp = tempfile.mkstemp(prefix=".state.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(state.to_dict(), fp, indent=2, sort_keys=True)
            fp.flush()
            os.fsync(fp.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        # Don't leave the tmp file lying around on error.
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


@contextlib.contextmanager
def lock_file(path: Path, wait_seconds: int = 5) -> Iterator[None]:
    """Advisory exclusive file lock. Yields the lock; releases on exit.

    Raises TimeoutError if the lock cannot be acquired in wait_seconds.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fp = open(path, "w")
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                fp.close()
                raise TimeoutError(
                    f"Could not acquire lock {path} within {wait_seconds}s"
                )
            time.sleep(0.2)
    try:
        fp.write(str(os.getpid()))
        fp.flush()
        yield
    finally:
        try:
            fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
        finally:
            fp.close()


def update_status(
    state: State,
    course_code: str,
    new_status: str,
) -> bool:
    """Update last_status, return True if it changed since the previous run."""
    if new_status not in KNOWN_STATUSES:
        raise ValueError(f"Unknown status: {new_status}")
    cs = state.course(course_code)
    changed = cs.last_status != new_status
    cs.last_check_at = _now_iso()
    if changed:
        cs.last_status_change_at = cs.last_check_at
        cs.last_status = new_status
    return changed
