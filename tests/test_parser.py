"""Parser tests against the fixture HTML files."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from usos_watcher import parser, state

FIX = Path(__file__).parent / "fixtures"
BASE = "https://usosweb.uw.edu.pl/kontroler.php?_action=dla_stud/rejestracja/kalendarz"
CODE = "2600-IBP-MS2026Z"
NAME = "Master Seminar - IBP"


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_coordinator_only_classified() -> None:
    r = parser.parse_calendar(_read("coordinator_only.html"), BASE, CODE, NAME)
    assert r.status == state.STATUS_COORDINATOR_ONLY
    assert r.register_url is None


def test_fcfs_open_classified() -> None:
    r = parser.parse_calendar(_read("fcfs_open.html"), BASE, CODE, NAME)
    assert r.status == state.STATUS_FCFS_OPEN
    assert r.register_url is not None
    assert "zarejestruj" in r.register_url


def test_closed_classified() -> None:
    r = parser.parse_calendar(_read("closed.html"), BASE, CODE, NAME)
    assert r.status == state.STATUS_CLOSED


def test_row_not_found_classified() -> None:
    r = parser.parse_calendar("<html><body>nothing here</body></html>",
                              BASE, CODE, NAME)
    assert r.status == state.STATUS_ROW_NOT_FOUND


def test_pick_group_tier1_prefers_hensel() -> None:
    groups = parser.parse_group_page(_read("group_page.html"), BASE)
    assert len(groups) == 3
    chosen = parser.pick_group(
        groups,
        tier1_keywords=["Hensel"],
        tier2_keywords=["AI"],
    )
    assert chosen is not None
    assert "Hensel" in chosen.combined_text


def test_pick_group_tier2_fallback_to_AI() -> None:
    groups = parser.parse_group_page(_read("group_page.html"), BASE)
    chosen = parser.pick_group(
        groups,
        tier1_keywords=["NoSuchProfessor"],
        tier2_keywords=["AI"],
    )
    assert chosen is not None
    # Group 2 has the only "AI" mention
    assert "AI" in chosen.combined_text


def test_pick_group_tier3_returns_none() -> None:
    groups = parser.parse_group_page(_read("group_page.html"), BASE)
    chosen = parser.pick_group(
        groups,
        tier1_keywords=["Nobody"],
        tier2_keywords=["XYZ"],
    )
    assert chosen is None
