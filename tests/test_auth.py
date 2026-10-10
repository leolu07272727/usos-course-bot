"""Tests for cookie persistence in auth.py.

We don't hit live CAS here — those would need real credentials. Instead we
verify the local file I/O contract.
"""
from __future__ import annotations

from pathlib import Path

import requests

from usos_watcher import auth


def test_cookies_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "cookies.json"

    s1 = requests.Session()
    s1.cookies.set("JSESSIONID", "abc123", domain="usosweb.uw.edu.pl", path="/")
    s1.cookies.set("CASTGC", "tgc-xyz", domain="logowanie.uw.edu.pl", path="/")
    auth.save_cookies(s1, path)
    assert path.exists()
    assert oct(path.stat().st_mode)[-3:] == "600"

    s2 = requests.Session()
    loaded = auth.load_cookies(s2, path)
    assert loaded is True
    names = {c.name for c in s2.cookies}
    assert names == {"JSESSIONID", "CASTGC"}


def test_load_cookies_missing_file_returns_false(tmp_path: Path) -> None:
    s = requests.Session()
    assert auth.load_cookies(s, tmp_path / "nope.json") is False


def test_load_cookies_corrupted_file_returns_false(tmp_path: Path) -> None:
    p = tmp_path / "cookies.json"
    p.write_text("{not json")
    s = requests.Session()
    assert auth.load_cookies(s, p) is False


def test_5xx_raises_unavailable_error() -> None:
    """503 from USOSweb must surface as UsosUnavailableError, not a generic
    login failure — otherwise the watcher will misclassify outages as bad
    credentials and spam the user with ❌ notifications during maintenance."""
    import pytest
    import requests

    class _FakeResponse:
        def __init__(self, code: int) -> None:
            self.status_code = code
            self.url = "https://usosweb.uw.edu.pl/foo"
        def raise_for_status(self) -> None:
            raise requests.HTTPError(f"{self.status_code} from test")

    for code in (500, 502, 503, 504):
        with pytest.raises(auth.UsosUnavailableError):
            auth._raise_for_status_with_5xx(_FakeResponse(code), "test")

    # And 4xx still goes through normal raise_for_status (not Unavailable).
    with pytest.raises(requests.HTTPError):
        auth._raise_for_status_with_5xx(_FakeResponse(404), "test")
