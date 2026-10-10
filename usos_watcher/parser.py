"""HTML parsing for the USOS registration calendar page.

Outputs a status enum per SPEC.md §8.1.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional, Tuple, List

from bs4 import BeautifulSoup, Tag

from .state import (
    STATUS_CLOSED,
    STATUS_COORDINATOR_ONLY,
    STATUS_PENDING,
    STATUS_FCFS_OPEN,
    STATUS_ROW_NOT_FOUND,
    STATUS_UNKNOWN,
)

log = logging.getLogger(__name__)


@dataclass
class ParseResult:
    status: str
    register_url: Optional[str] = None      # absolute URL to follow when FCFS_OPEN
    row_snippet: Optional[str] = None       # for UNKNOWN dumps


_COORD_PATTERNS = [
    re.compile(r"coordinated\s+groups?\s+exchange", re.IGNORECASE),
    re.compile(r"wymian\s*grup", re.IGNORECASE),
    re.compile(r"koordynowan", re.IGNORECASE),
]
_NO_ACTIONS_PATTERNS = [
    re.compile(r"\bno\s+actions\b", re.IGNORECASE),
    re.compile(r"\bbrak\s+akcji\b", re.IGNORECASE),
]
_CLOSED_PATTERNS = [
    re.compile(r"\bclosed\b", re.IGNORECASE),
    re.compile(r"\bzamkni", re.IGNORECASE),
]
_PENDING_PATTERNS = [
    re.compile(r"will\s+start\s+in\b", re.IGNORECASE),
    re.compile(r"rozpocznie\s+się\s+za\b", re.IGNORECASE),
]


def _absolutize(href: str, base: str) -> str:
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        m = re.match(r"^(https?://[^/]+)", base)
        if m:
            return m.group(1) + href
    base_dir = base.rsplit("/", 1)[0]
    return f"{base_dir}/{href}"


def _find_target_row(soup: BeautifulSoup, registration_code: str,
                     course_name_match: str) -> Optional[Tag]:
    code_node = soup.find(string=lambda s: s and registration_code in s)
    if code_node:
        cur = code_node.parent
        while cur is not None:
            sibling = cur.find_next("table")
            if sibling is not None:
                rows = sibling.find_all("tr")
                for row in rows:
                    if row.find("th") and not row.find("td"):
                        continue
                    return row
                break
            cur = cur.parent

    for row in soup.find_all("tr"):
        text = row.get_text(" ", strip=True)
        if course_name_match and course_name_match.lower() in text.lower():
            return row

    return None


def _is_unactionable(row_text: str) -> bool:
    for pat in _COORD_PATTERNS + _NO_ACTIONS_PATTERNS:
        if pat.search(row_text):
            return True
    return False


def _is_closed(row_text: str) -> bool:
    return any(pat.search(row_text) for pat in _CLOSED_PATTERNS)


def _find_register_link(row: Tag, base_url: str) -> Optional[str]:
    """Return absolute URL to register, or None if no register action present."""
    # Legacy format: zarejestruj.svg icon or zarejestruj in href.
    img = row.select_one('img[src*="zarejestruj.svg"]')
    if img is not None:
        link = img.find_parent("a")
        if link and link.get("href"):
            return _absolutize(link["href"], base_url)
    link = row.select_one('a[href*="zarejestruj"]')
    if link and link.get("href"):
        return _absolutize(link["href"], base_url)
    # New format (2026): brdg2/wyborPrzedmiotu bridge URL.
    link = row.select_one('a[href*="wyborPrzedmiotu"]')
    if link and link.get("href"):
        return _absolutize(link["href"], base_url)
    return None


def parse_calendar(html: str, base_url: str, registration_code: str,
                   course_name_match: str) -> ParseResult:
    soup = BeautifulSoup(html, "lxml")
    row = _find_target_row(soup, registration_code, course_name_match)
    if row is None:
        return ParseResult(status=STATUS_ROW_NOT_FOUND)

    row_text = row.get_text(" ", strip=True)
    log.debug("Target row text: %r", row_text[:200])

    register_url = _find_register_link(row, base_url)
    if register_url:
        return ParseResult(status=STATUS_FCFS_OPEN, register_url=register_url)

    if _is_closed(row_text):
        return ParseResult(status=STATUS_CLOSED)

    if _is_unactionable(row_text):
        return ParseResult(status=STATUS_COORDINATOR_ONLY)

    if any(p.search(row_text) for p in _PENDING_PATTERNS):
        return ParseResult(status=STATUS_PENDING)

    return ParseResult(
        status=STATUS_UNKNOWN,
        row_snippet=str(row)[:2000],
    )


# ---------------------------------------------------------------------------
# Group row dataclass
# ---------------------------------------------------------------------------

@dataclass
class GroupRow:
    name: str
    lecturer: str
    current: Optional[int]
    limit: Optional[int]
    submit_form_data: dict
    submit_action_url: str
    group_no: Optional[int] = None          # group number from table (brdg2)

    @property
    def available(self) -> Optional[int]:
        if self.current is None or self.limit is None:
            return None
        return self.limit - self.current

    @property
    def combined_text(self) -> str:
        return f"{self.name} {self.lecturer}".strip()


# ---------------------------------------------------------------------------
# Legacy group page parser (old zarejestruj-based flow)
# ---------------------------------------------------------------------------

def _extract_capacity(text: str) -> Tuple[Optional[int], Optional[int]]:
    m = re.search(r"(\d+)\s*/\s*(\d+)", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def parse_group_page(html: str, base_url: str) -> List[GroupRow]:
    """Legacy: find groups via forms whose action contains 'zarejestruj'."""
    soup = BeautifulSoup(html, "lxml")
    out: List[GroupRow] = []

    for form in soup.find_all("form"):
        action = (form.get("action") or "")
        if "zarejestruj" not in action.lower():
            continue
        row = form.find_parent("tr") or form.find_parent("li") or form
        text = row.get_text(" ", strip=True)
        cur, lim = _extract_capacity(text)

        name = text[:120]
        lecturer = ""
        m = re.search(r"\b([A-ZŁŚŻŹĆŃÓ][\w\-]+(?:\s+[A-ZŁŚŻŹĆŃÓ][\w\-]+)+)\b", text)
        if m:
            lecturer = m.group(1)

        data = {}
        for inp in form.find_all("input"):
            n = inp.get("name")
            if n:
                data[n] = inp.get("value", "")

        out.append(GroupRow(
            name=name,
            lecturer=lecturer,
            current=cur,
            limit=lim,
            submit_form_data=data,
            submit_action_url=_absolutize(action, base_url),
        ))

    return out


# ---------------------------------------------------------------------------
# New brdg2 group page parser (grupyPrzedmiotu)
# ---------------------------------------------------------------------------

def _extract_class_id(html: str) -> Optional[str]:
    """Extract USOS class ID from div IDs like opis_skrot_{classId}_{groupNo}."""
    m = re.search(r"opis_skrot_(\d+)_\d+", html)
    return m.group(1) if m else None


def parse_group_page_brdg2(html: str, page_url: str) -> List[GroupRow]:
    """Parse the brdg2 grupyPrzedmiotu page.

    Works whether or not the user is already registered (odczyt=0 or odczyt=1).
    Returns all groups; caller filters by availability via pick_group.
    """
    soup = BeautifulSoup(html, "lxml")

    class_id = _extract_class_id(html)

    csrf_input = soup.find("input", {"name": "csrftoken"})
    csrf_token = csrf_input.get("value", "") if csrf_input else ""

    form = soup.find("form", {"name": "grupy"})
    if not form:
        log.debug("parse_group_page_brdg2: no form[name=grupy] on page")
        return []

    out: List[GroupRow] = []

    for tr in form.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue

        group_no_text = tds[0].get_text(strip=True)
        if not group_no_text.isdigit():
            continue
        group_no = int(group_no_text)

        # enrolled count — strip the "full" icon tooltip text, keep only digits
        enrolled_raw = re.sub(r"[^\d]", "", tds[1].get_text(strip=True))
        current = int(enrolled_raw) if enrolled_raw else None

        upper_raw = tds[3].get_text(strip=True)
        limit = int(upper_raw) if upper_raw.isdigit() else None

        teacher = tds[4].get_text(strip=True)

        desc = ""
        if len(tds) > 5:
            desc_div = tds[5].find("div")
            if desc_div:
                desc = desc_div.get_text(strip=True)

        name = desc or f"Group {group_no}"

        form_data: dict = {"csrftoken": csrf_token}
        if class_id:
            form_data[f"zajecia[{class_id}][]"] = str(group_no)

        out.append(GroupRow(
            name=name,
            lecturer=teacher,
            current=current,
            limit=limit,
            submit_form_data=form_data,
            submit_action_url=page_url,
            group_no=group_no,
        ))

    log.debug("parse_group_page_brdg2: %d groups (class_id=%s)", len(out), class_id)
    return out


# ---------------------------------------------------------------------------
# Group selection
# ---------------------------------------------------------------------------

def pick_group(
    groups: List[GroupRow],
    tier1_keywords: List[str],
    tier2_keywords: List[str],
    strategy: str = "keyword",
    prefer_group: Optional[int] = None,
) -> Optional[GroupRow]:
    """Select a group per strategy. Only considers groups with available space.

    Strategies:
      keyword      — tier1 keyword match first, tier2 fallback (default)
      group_number — pick the specific group number in prefer_group
      any_available — pick the group with the most free spots
    """
    if not groups:
        return None

    selectable = [g for g in groups if g.available is None or g.available > 0]
    if not selectable:
        log.debug("pick_group: all %d groups are full", len(groups))
        return None

    def best_by_availability(candidates: List[GroupRow]) -> GroupRow:
        def key(g: GroupRow):
            return (g.available is None, -(g.available or 0))
        return sorted(candidates, key=key)[0]

    if strategy == "group_number" and prefer_group is not None:
        matches = [g for g in selectable if g.group_no == prefer_group]
        if not matches:
            log.debug("pick_group: group_number=%d not found or full", prefer_group)
        return matches[0] if matches else None

    if strategy == "any_available":
        return best_by_availability(selectable)

    # keyword strategy (default)
    def kw_match(g: GroupRow, kws: List[str]) -> bool:
        haystack = g.combined_text.lower()
        return any(k.lower() in haystack for k in kws if k)

    tier1 = [g for g in selectable if kw_match(g, tier1_keywords)]
    if tier1:
        return best_by_availability(tier1)

    tier2 = [g for g in selectable if kw_match(g, tier2_keywords)]
    if tier2:
        return best_by_availability(tier2)

    return None
