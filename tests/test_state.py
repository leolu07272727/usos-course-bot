"""State persistence + locking tests."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from usos_watcher import state


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    st = state.State()
    cs = st.course("X-CODE")
    cs.last_status = state.STATUS_COORDINATOR_ONLY
    cs.consecutive_login_failures = 2

    state.save_state(p, st)
    assert p.exists()

    st2 = state.load_state(p)
    assert st2.course("X-CODE").last_status == state.STATUS_COORDINATOR_ONLY
    assert st2.course("X-CODE").consecutive_login_failures == 2


def test_update_status_change_detection(tmp_path: Path) -> None:
    st = state.State()
    changed_first = state.update_status(st, "X", state.STATUS_COORDINATOR_ONLY)
    changed_second = state.update_status(st, "X", state.STATUS_COORDINATOR_ONLY)
    changed_third = state.update_status(st, "X", state.STATUS_FCFS_OPEN)
    assert changed_first is True
    assert changed_second is False
    assert changed_third is True


def test_atomic_write_leaves_no_temp_files(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    st = state.State()
    state.save_state(p, st)
    tmp_files = list(tmp_path.glob(".state.*.tmp"))
    assert tmp_files == []


def test_corrupted_state_raises(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    p.write_text("{not json")
    with pytest.raises(RuntimeError, match="corrupted"):
        state.load_state(p)


def test_lock_prevents_concurrent_acquisition(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    with state.lock_file(lock):
        # Second acquisition with low wait should time out.
        with pytest.raises(TimeoutError):
            with state.lock_file(lock, wait_seconds=1):
                pass
