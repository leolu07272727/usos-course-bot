"""CAS login against logowanie.uw.edu.pl, returning an authenticated Session.

SPEC.md §4.1 / §2: the user confirmed CAS has no captcha / no 2FA for this
account. A standard CAS POST with hidden execution token is enough. If CAS
ever changes shape, we fail loud rather than retry silently.

v1.1 — cookie persistence: we save session cookies to disk after a successful
CAS login, and try to resume from them on the next run. This drops the per-
cycle traffic from "4 HTTP + 1 login" to "1 HTTP" while cookies are valid,
which lets us poll much more aggressively without hammering CAS.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

CAS_LOGIN_URL = (
    "https://logowanie.uw.edu.pl/cas/login"
    "?service=https%3A%2F%2Fusosweb.uw.edu.pl%2Fkontroler.php"
    "%3F_action%3Dlogowaniecas%2Findex"
)

USOSWEB_HOME = "https://usosweb.uw.edu.pl/kontroler.php?_action=news/default"


class CasLoginError(RuntimeError):
    """The user's credentials or the CAS/USOSweb page shape is the problem."""


class UsosUnavailableError(CasLoginError):
    """USOSweb (or CAS) returned a transient server error (5xx).

    Distinct from a real credential failure: we should NOT increment
    consecutive_login_failures for these, because the user can't fix
    them — we just need to wait for USOS to come back online.
    """


def _new_session(user_agent: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": user_agent,
        "Accept-Language": "en-GB,en;q=0.9,pl;q=0.6",
    })
    return s


def _parse_hidden_inputs(html: str) -> dict:
    """Extract every <input type=hidden> from the login form (execution, etc.)."""
    soup = BeautifulSoup(html, "lxml")
    form = soup.select_one("form#fm1, form[action*='login']")
    if not form:
        raise CasLoginError(
            "Could not find CAS login form. The page shape may have changed."
        )
    fields = {}
    for inp in form.find_all("input"):
        if inp.get("type") == "hidden" and inp.get("name"):
            fields[inp["name"]] = inp.get("value", "")
    return fields


def _looks_logged_in(html: str) -> bool:
    # USOSweb home shows the user's display name + a logout link when authed.
    lowered = html.lower()
    return ("wyloguj" in lowered) or ("logout" in lowered and "log out" in lowered) \
        or ("logout" in lowered and "kontroler.php?_action=logmasz" in lowered)


# --- Cookie persistence ------------------------------------------------------

def save_cookies(session: requests.Session, path: Path) -> None:
    """Atomically dump session cookies to a JSON file (mode 0600)."""
    cookies = [
        {
            "name": c.name,
            "value": c.value,
            "domain": c.domain,
            "path": c.path,
            "secure": c.secure,
            "expires": c.expires,
        }
        for c in session.cookies
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".cookies.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(cookies, fp)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def load_cookies(session: requests.Session, path: Path) -> bool:
    if not path.exists():
        return False
    try:
        with path.open("r", encoding="utf-8") as fp:
            cookies = json.load(fp)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Could not read cookies file %s: %s", path, e)
        return False
    if not isinstance(cookies, list):
        return False
    for c in cookies:
        try:
            session.cookies.set(
                c["name"], c["value"],
                domain=c.get("domain"), path=c.get("path", "/"),
                secure=c.get("secure", False),
            )
        except Exception as e:
            log.warning("Skipping bad cookie entry: %s", e)
    return True


def _is_session_alive(session: requests.Session, timeout: int) -> bool:
    """One cheap GET to USOSweb home; True if it still shows a logout link."""
    try:
        r = session.get(USOSWEB_HOME, timeout=timeout, allow_redirects=True)
        if r.status_code != 200:
            return False
        return _looks_logged_in(r.text)
    except requests.RequestException as e:
        log.warning("Session liveness check failed: %s", e)
        return False


# --- Login flows -------------------------------------------------------------

def _raise_for_status_with_5xx(response: requests.Response, where: str) -> None:
    """Like response.raise_for_status, but reclassifies 5xx as UsosUnavailableError."""
    if 500 <= response.status_code < 600:
        raise UsosUnavailableError(
            f"USOS returned HTTP {response.status_code} at {where} "
            f"(url={response.url}). Likely USOSweb maintenance / outage."
        )
    response.raise_for_status()


def _do_cas_login(session: requests.Session, username: str, password: str,
                  timeout: int) -> None:
    log.info("Fetching CAS login page")
    r = session.get(CAS_LOGIN_URL, timeout=timeout, allow_redirects=True)
    _raise_for_status_with_5xx(r, "CAS login page GET")
    hidden = _parse_hidden_inputs(r.text)
    log.info("CAS hidden inputs: %s", sorted(hidden.keys()))

    form_data = {
        "username": username,
        "password": password,
        "_eventId": "submit",
        "geolocation": "",
    }
    form_data.update(hidden)

    log.info("Submitting CAS credentials")
    r2 = session.post(r.url, data=form_data, timeout=timeout, allow_redirects=True)
    _raise_for_status_with_5xx(r2, "CAS credentials POST")

    r3 = session.get(USOSWEB_HOME, timeout=timeout, allow_redirects=True)
    _raise_for_status_with_5xx(r3, "USOSweb home GET")

    if not _looks_logged_in(r3.text):
        snippet = r3.text[:400].replace("\n", " ")
        raise CasLoginError(
            f"Login appears to have failed (USOSweb home does not show logout). "
            f"Page snippet: {snippet!r}"
        )

    log.info("CAS login successful")


def login(
    username: str,
    password: str,
    user_agent: str,
    timeout: int,
) -> requests.Session:
    """Fresh CAS login each call; no cookie reuse."""
    if not username or not password:
        raise CasLoginError("Empty USOS credentials")
    s = _new_session(user_agent)
    _do_cas_login(s, username, password, timeout)
    return s


def login_or_resume(
    username: str,
    password: str,
    user_agent: str,
    timeout: int,
    cookies_path: Path,
) -> requests.Session:
    """Try cached cookies first; do full CAS login only on miss.

    Returns an authenticated Session. Always saves the latest cookies on
    success.
    """
    if not username or not password:
        raise CasLoginError("Empty USOS credentials")

    s = _new_session(user_agent)

    if load_cookies(s, cookies_path):
        log.info("Loaded cached cookies from %s", cookies_path)
        if _is_session_alive(s, timeout):
            log.info("Resumed session from cached cookies — skipping CAS")
            return s
        log.info("Cached cookies no longer valid; re-authenticating")
        s.cookies.clear()

    _do_cas_login(s, username, password, timeout)
    try:
        save_cookies(s, cookies_path)
        log.info("Saved fresh cookies to %s", cookies_path)
    except Exception as e:
        # Persisting cookies is best-effort; the session is still usable.
        log.warning("Failed to persist cookies: %s", e)
    return s
