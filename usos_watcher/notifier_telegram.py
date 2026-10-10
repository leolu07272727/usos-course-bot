"""Telegram notifier.

Sends one message per call via the Bot API
(https://api.telegram.org/bot<TOKEN>/sendMessage).

Same semantic role as notifier.py (SMTP), but the wire transport differs.
We also flip the message format: subject + body becomes a single text
payload, with the old "[USOS X]" subject prefix becoming the first line
in bold.
"""
from __future__ import annotations

import logging
from typing import Optional

import requests

from .config import Config

log = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org"
_MAX_LEN = 4000  # Telegram hard limit is 4096; leave headroom for our headers.


def _telegram_escape(text: str) -> str:
    """Escape characters that have meaning in Telegram's MarkdownV2."""
    specials = r"_*[]()~`>#+-=|{}.!\\"
    out = []
    for ch in text:
        if ch in specials:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def send(cfg: Config, subject: str, body: str) -> bool:
    """Render subject+body into a Telegram message; return True on 200 OK."""
    token = cfg.secrets.telegram_bot_token
    chat_id = cfg.secrets.telegram_chat_id
    if not token or not chat_id:
        log.error("Telegram credentials missing; skipping notification")
        return False

    text = f"*{_telegram_escape(subject)}*\n\n{_telegram_escape(body)}"
    if len(text) > _MAX_LEN:
        text = text[:_MAX_LEN] + "\n\\.\\.\\. truncated"

    url = f"{_API_BASE}/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "MarkdownV2",
        "disable_web_page_preview": True,
    }
    try:
        log.info("Sending Telegram message subject=%r chat=%s", subject, chat_id)
        r = requests.post(url, data=payload, timeout=15)
        if r.status_code != 200:
            log.error("Telegram API HTTP %d: %s", r.status_code, r.text[:300])
            return False
        return True
    except Exception as e:
        log.error("Telegram send failed: %s", e)
        return False


# --- Pre-canned message bodies ----------------------------------------------
# (Identical text to notifier.py so the rest of the code is unchanged.)

def subject_for(prefix_emoji: str, summary: str) -> str:
    return f"[USOS {prefix_emoji}] {summary}"


def body_state_change(course_code: str, old: Optional[str], new: str,
                      calendar_url: str) -> str:
    return (
        f"USOS Watcher noticed a state change on {course_code}.\n\n"
        f"  Previous: {old}\n"
        f"  Current:  {new}\n\n"
        f"URL: {calendar_url}\n"
    )


def body_success(course_code: str, group_name: str, group_lecturer: str,
                 final_url: str) -> str:
    return (
        f"USOS Watcher successfully registered you for {course_code}.\n\n"
        f"  Group:    {group_name}\n"
        f"  Lecturer: {group_lecturer}\n"
        f"  URL:      {final_url}\n\n"
        f"Reply if you'd like me to keep watching for a better group "
        f"(refill mode). It is OFF by default.\n"
    )


def body_failure_login(course_code: str, attempts: int) -> str:
    return (
        f"USOS Watcher could not log in to USOSweb for the last {attempts} "
        f"consecutive runs while monitoring {course_code}.\n\n"
        f"Likely causes:\n"
        f"  - password changed\n"
        f"  - USOS imposed extra auth (captcha / 2FA)\n"
        f"  - CAS endpoint reshaped its HTML\n\n"
        f"Please check the VPS logs and update the credentials if needed.\n"
    )


def body_failure_http(course_code: str, attempts: int) -> str:
    return (
        f"USOS Watcher received {attempts} consecutive HTTP errors while "
        f"monitoring {course_code}.\n\n"
        f"Possible IP block, USOS outage, or VPS networking issue.\n"
        f"Please inspect the VPS logs.\n"
    )


def body_no_preferred_group(course_code: str, calendar_url: str,
                            register_url: str) -> str:
    return (
        f"USOS Watcher saw that registration opened for {course_code}, but "
        f"could NOT find a group containing the configured keywords (Hensel / "
        f"AI). As agreed, no group was selected automatically.\n\n"
        f"Please pick manually:\n"
        f"  Calendar: {calendar_url}\n"
        f"  Direct:   {register_url}\n"
    )


def body_unknown(course_code: str, calendar_url: str, row_snippet: str) -> str:
    return (
        f"USOS Watcher found the {course_code} row but could not classify its "
        f"status. Please inspect.\n\n"
        f"Calendar URL: {calendar_url}\n\n"
        f"--- Row HTML snippet ---\n{row_snippet}\n"
    )


def body_row_not_found(course_code: str, calendar_url: str) -> str:
    return (
        f"USOS Watcher could no longer find any row for {course_code} on the "
        f"calendar page. The registration round may have ended.\n\n"
        f"URL: {calendar_url}\n"
    )


def body_heartbeat(course_code: str, last_status: Optional[str],
                   last_check_at: Optional[str]) -> str:
    return (
        f"USOS Watcher daily heartbeat.\n\n"
        f"  Monitored:    {course_code}\n"
        f"  Last status:  {last_status}\n"
        f"  Last checked: {last_check_at}\n"
    )
