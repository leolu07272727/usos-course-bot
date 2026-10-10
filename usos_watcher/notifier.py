"""SMTP email notifier.

SPEC.md §5 enumerates when to send mail. This module only knows *how* to
send; the calling code decides *whether* to send.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import Optional

from .config import Config

log = logging.getLogger(__name__)


def send(cfg: Config, subject: str, body: str) -> bool:
    """Return True on successful SMTP send."""
    s = cfg.secrets
    msg = EmailMessage()
    msg["From"] = s.mail_from
    msg["To"] = s.mail_to
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        log.info("Sending email subject=%r to=%s", subject, s.mail_to)
        ctx = ssl.create_default_context()
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as server:
            server.ehlo()
            server.starttls(context=ctx)
            server.ehlo()
            server.login(s.smtp_username, s.smtp_password)
            server.send_message(msg)
        return True
    except Exception as e:
        log.error("SMTP send failed: %s", e)
        return False


# --- Pre-canned message templates --------------------------------------------

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
