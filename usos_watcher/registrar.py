"""Drive the actual registration flow: follow register link, pick group, submit.

Supports two USOS flows:
  Legacy  — direct zarejestruj link → group form with action*=zarejestruj
  brdg2   — wyborPrzedmiotu → grupyPrzedmiotu → POST (2026+ format)

SPEC.md §9.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Tuple

import requests
from bs4 import BeautifulSoup

from . import fetcher
from .config import Config
from .parser import GroupRow, parse_group_page, parse_group_page_brdg2, pick_group

log = logging.getLogger(__name__)


@dataclass
class RegistrationResult:
    success: bool
    reason: str
    chosen_group: Optional[GroupRow] = None
    final_url: Optional[str] = None
    body_snippet: Optional[str] = None


# ---------------------------------------------------------------------------
# Success detection
# ---------------------------------------------------------------------------

_SUCCESS_PHRASES = [
    "you are registered",
    "you have been registered",
    "jesteś zarejestrowany",
    "zostałeś zarejestrowany",
    "rejestracja zakończona",
    "registered successfully",
    # brdg2 success signals
    "your group",
    "twoja grupa",
]


def _looks_like_success(body: str) -> bool:
    lowered = body.lower()
    return any(p in lowered for p in _SUCCESS_PHRASES)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

import re as _re

def _absolutize(href: str, base: str) -> str:
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        m = _re.match(r"^(https?://[^/]+)", base)
        if m:
            return m.group(1) + href
    base_dir = base.rsplit("/", 1)[0]
    return f"{base_dir}/{href}"


def _find_grupy_link(html: str, base_url: str) -> Tuple[Optional[str], bool]:
    """Find grupyPrzedmiotu link in wyborPrzedmiotu page.

    Returns (absolute_url, already_registered).
    already_registered is True when the link has odczyt=1.
    """
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a"):
        href = a.get("href", "")
        if "grupyPrzedmiotu" in href:
            already_registered = "odczyt=1" in href
            return _absolutize(href, base_url), already_registered
    return None, False


# ---------------------------------------------------------------------------
# brdg2 flow (2026+)
# ---------------------------------------------------------------------------

def _register_brdg2(
    session: requests.Session,
    cfg: Config,
    wyborPrzedmiotu_url: str,
    target_index: int = 0,
) -> RegistrationResult:
    """New brdg2 flow: wyborPrzedmiotu → grupyPrzedmiotu → POST."""
    log.info("brdg2: following wyborPrzedmiotu: %s", wyborPrzedmiotu_url)
    r1 = fetcher.get(
        session, wyborPrzedmiotu_url,
        timeout=cfg.http.request_timeout_sec,
        retries=cfg.http.retries,
        jitter_max_sec=0,
    )

    grupy_url, already_registered = _find_grupy_link(r1.text, r1.url)
    if not grupy_url:
        return RegistrationResult(
            success=False,
            reason="no_grupy_link",
            final_url=r1.url,
            body_snippet=r1.text[:1000],
        )

    if already_registered:
        log.info("brdg2: grupyPrzedmiotu link has odczyt=1 — already registered")
        return RegistrationResult(
            success=False,
            reason="already_registered",
            final_url=grupy_url,
        )

    log.info("brdg2: fetching group page: %s", grupy_url)
    r2 = fetcher.get(
        session, grupy_url,
        timeout=cfg.http.request_timeout_sec,
        retries=cfg.http.retries,
        jitter_max_sec=0,
    )

    groups = parse_group_page_brdg2(r2.text, r2.url)
    log.info("brdg2: parsed %d group(s)", len(groups))
    if not groups:
        return RegistrationResult(
            success=False,
            reason="no_groups_parsed",
            final_url=r2.url,
            body_snippet=r2.text[:1000],
        )

    target = cfg.targets[target_index]
    gp = target.group_preference
    chosen = pick_group(
        groups,
        tier1_keywords=gp.tier1_keywords,
        tier2_keywords=gp.tier2_keywords,
        strategy=gp.strategy,
        prefer_group=gp.prefer_group,
    )

    if chosen is None:
        log.warning("brdg2: no group matched preferences")
        return RegistrationResult(
            success=False,
            reason="no_preferred_group_found",
            final_url=r2.url,
            body_snippet=r2.text[:1500],
        )

    log.info("brdg2: chosen group %r (enrolled %s/%s)",
             chosen.combined_text, chosen.current, chosen.limit)

    if cfg.behavior.dry_run:
        safe_data = {k: v for k, v in chosen.submit_form_data.items()
                     if "csrf" not in k.lower()}
        log.warning("DRY-RUN: would POST to %s data=%s",
                    chosen.submit_action_url, safe_data)
        return RegistrationResult(
            success=False,
            reason="dry_run",
            chosen_group=chosen,
            final_url=chosen.submit_action_url,
        )

    log.info("brdg2: submitting registration to %s", chosen.submit_action_url)
    r3 = fetcher.post(
        session, chosen.submit_action_url,
        data=chosen.submit_form_data,
        timeout=cfg.http.request_timeout_sec,
        retries=cfg.http.retries,
        jitter_max_sec=0,
    )

    if _looks_like_success(r3.text):
        return RegistrationResult(
            success=True,
            reason="confirmation_page",
            chosen_group=chosen,
            final_url=r3.url,
        )

    return RegistrationResult(
        success=False,
        reason="no_success_phrase",
        chosen_group=chosen,
        final_url=r3.url,
        body_snippet=r3.text[:1500],
    )


# ---------------------------------------------------------------------------
# Legacy flow (old zarejestruj-based)
# ---------------------------------------------------------------------------

def _register_legacy(
    session: requests.Session,
    cfg: Config,
    register_url: str,
    target_index: int = 0,
) -> RegistrationResult:
    """Old flow: follow zarejestruj link, find group form, POST."""
    log.info("legacy: following register link: %s", register_url)
    r = fetcher.get(
        session, register_url,
        timeout=cfg.http.request_timeout_sec,
        retries=cfg.http.retries,
        jitter_max_sec=0,
    )

    groups = parse_group_page(r.text, base_url=register_url)
    log.info("legacy: group selection page returned %d candidate group(s)", len(groups))
    if not groups:
        return RegistrationResult(
            success=False,
            reason="no_groups_parsed",
            final_url=r.url,
            body_snippet=r.text[:1000],
        )

    target = cfg.targets[target_index]
    gp = target.group_preference
    chosen = pick_group(
        groups,
        tier1_keywords=gp.tier1_keywords,
        tier2_keywords=gp.tier2_keywords,
        strategy=gp.strategy,
        prefer_group=gp.prefer_group,
    )

    if chosen is None:
        log.warning("legacy: no group matched tier-1 or tier-2 keywords")
        return RegistrationResult(
            success=False,
            reason="no_preferred_group_found",
            final_url=r.url,
            body_snippet=r.text[:1500],
        )

    log.info("legacy: chosen group %r (capacity %s/%s)",
             chosen.combined_text, chosen.current, chosen.limit)

    if cfg.behavior.dry_run:
        log.warning("DRY-RUN: would POST to %s with form data keys=%s",
                    chosen.submit_action_url, sorted(chosen.submit_form_data.keys()))
        return RegistrationResult(
            success=False,
            reason="dry_run",
            chosen_group=chosen,
            final_url=chosen.submit_action_url,
        )

    log.info("legacy: submitting group registration to %s", chosen.submit_action_url)
    r2 = fetcher.post(
        session, chosen.submit_action_url,
        data=chosen.submit_form_data,
        timeout=cfg.http.request_timeout_sec,
        retries=cfg.http.retries,
        jitter_max_sec=0,
    )

    if _looks_like_success(r2.text):
        return RegistrationResult(
            success=True,
            reason="confirmation_page",
            chosen_group=chosen,
            final_url=r2.url,
        )

    return RegistrationResult(
        success=False,
        reason="no_success_phrase",
        chosen_group=chosen,
        final_url=r2.url,
        body_snippet=r2.text[:1500],
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def register(
    session: requests.Session,
    cfg: Config,
    register_url: str,
    target_index: int = 0,
) -> RegistrationResult:
    """Register for a course group. Routes to brdg2 or legacy flow."""
    if "wyborPrzedmiotu" in register_url or "brdg2" in register_url:
        return _register_brdg2(session, cfg, register_url, target_index)
    return _register_legacy(session, cfg, register_url, target_index)
