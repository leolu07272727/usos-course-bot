#!/usr/bin/env python3
"""
shoot.py — 等到華沙時間 21:00:00，立刻進 USOS 搶 Master Seminar (Hensel)。

用法：
  python shoot.py              # 等到今天 21:00 Warsaw 時間再跑
  python shoot.py --now        # 立刻跑（測試用）
  python shoot.py --target 1   # 只搶第二個 target（index=1，預設 index=0）

必要環境變數（放在 .env 或 export）：
  USOS_USERNAME     你的 UW USOS 帳號
  USOS_PASSWORD     你的 UW USOS 密碼
  TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID  （選用，有才送通知）
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

HERE = Path(__file__).parent

TG_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Warsaw time offset — CEST = UTC+2 in summer, CET = UTC+1 in winter.
# Update WARSAW_TZ_OFFSET if running outside daylight-saving period.
WARSAW_TZ_OFFSET = int(os.getenv("WARSAW_TZ_OFFSET", "2"))
WARSAW_TZ   = timezone(timedelta(hours=WARSAW_TZ_OFFSET))
TARGET_HOUR = int(os.getenv("SHOOT_HOUR", "21"))
TARGET_MIN  = int(os.getenv("SHOOT_MIN", "0"))

COOKIES_PATH = HERE / ".shoot-cookies.json"
RETRY_COUNT  = 10   # how many times to retry if button not found yet
RETRY_DELAY  = 8    # seconds between retries

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def tg(text: str) -> None:
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        log.warning("Telegram error: %s", e)


def wait_until_target() -> None:
    now    = datetime.now(WARSAW_TZ)
    target = now.replace(hour=TARGET_HOUR, minute=TARGET_MIN, second=0, microsecond=0)
    secs   = (target - now).total_seconds()
    if secs <= 0:
        log.info("Past %02d:%02d Warsaw — running immediately", TARGET_HOUR, TARGET_MIN)
        return

    log.info("Warsaw time now %s, waiting %02d:%02d until %02d:%02d",
             now.strftime("%H:%M:%S"), int(secs)//60, int(secs)%60,
             TARGET_HOUR, TARGET_MIN)
    tg(f"⏰ <b>shoot.py launched</b>\n"
       f"Waiting {int(secs)//60}m {int(secs)%60}s until {TARGET_HOUR:02d}:{TARGET_MIN:02d} Warsaw\n")

    while True:
        now  = datetime.now(WARSAW_TZ)
        secs = (target - now).total_seconds()
        if secs <= 5:
            break
        log.info("%02d:%02d until registration opens", int(secs)//60, int(secs)%60)
        time.sleep(min(60, secs - 5))

    remaining = (target - datetime.now(WARSAW_TZ)).total_seconds()
    if remaining > 0:
        log.info("Final %.1f seconds...", remaining)
        time.sleep(remaining)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", action="store_true", help="skip countdown and run immediately")
    ap.add_argument("--target", type=int, default=0, metavar="N",
                    help="which target index to use (default: 0 = first)")
    args = ap.parse_args()

    from usos_watcher import auth, parser, registrar
    from usos_watcher.config import load_config

    cfg = load_config()

    if args.target >= len(cfg.targets):
        sys.exit(f"Error: --target {args.target} is out of range "
                 f"(config has {len(cfg.targets)} target(s))")

    target = cfg.targets[args.target]
    log.info("Target [%d]: %s — %s", args.target,
             target.registration_code, target.course_name_match)

    if not args.now:
        wait_until_target()

    log.info("===== Starting registration =====")
    tg(f"🚀 <b>shoot.py starting</b>\nTarget: {target.registration_code}")

    LOGIN_RETRIES = 12
    LOGIN_RETRY_DELAY = 5
    session = None
    for attempt in range(1, LOGIN_RETRIES + 1):
        try:
            session = auth.login_or_resume(
                cfg.secrets.usos_username, cfg.secrets.usos_password,
                user_agent=cfg.http.user_agent,
                timeout=cfg.http.request_timeout_sec,
                cookies_path=COOKIES_PATH,
            )
            log.info("Login successful")
            break
        except auth.CasLoginError as e:
            msg = f"❌ Login failed (credentials/CAS): {e}"
            log.error(msg); tg(msg); sys.exit(1)
        except Exception as e:
            if attempt < LOGIN_RETRIES:
                log.warning("Login timeout (%d/%d), retrying in %ds: %s",
                            attempt, LOGIN_RETRIES, LOGIN_RETRY_DELAY, e)
                time.sleep(LOGIN_RETRY_DELAY)
            else:
                msg = f"❌ Login failed after {LOGIN_RETRIES} attempts: {e}"
                log.error(msg); tg(msg); sys.exit(1)
    assert session is not None

    for attempt in range(1, RETRY_COUNT + 1):
        log.info("── Attempt %d/%d ──", attempt, RETRY_COUNT)
        try:
            r = session.get(target.calendar_url, timeout=cfg.http.request_timeout_sec)
            result = parser.parse_calendar(
                r.text, r.url,
                registration_code=target.registration_code,
                course_name_match=target.course_name_match,
            )
        except Exception as e:
            log.error("Calendar fetch failed: %s", e)
            time.sleep(RETRY_DELAY)
            continue

        log.info("Status: %s", result.status)

        if result.status == "FCFS_OPEN":
            log.info("Registration open — attempting to register...")
            try:
                outcome = registrar.register(session, cfg, result.register_url,
                                             target_index=args.target)
            except Exception as e:
                msg = f"❌ Registration error: {e}"
                log.error(msg); tg(msg); sys.exit(1)

            if outcome.reason == "already_registered":
                msg = "ℹ️ Already registered (detected by USOS odczyt=1)"
                log.info(msg); tg(msg)
                return

            if outcome.success:
                group_text = outcome.chosen_group.combined_text if outcome.chosen_group else "?"
                msg = (f"✅ <b>Registered!</b>\n"
                       f"Group: {group_text}\n"
                       f"URL: {outcome.final_url}")
                log.info(msg); tg(msg)
            elif outcome.reason == "no_preferred_group_found":
                msg = (f"⚠️ Open but no preferred group found — please register manually!\n"
                       f"URL: {result.register_url}")
                log.warning(msg); tg(msg)
            elif outcome.reason == "dry_run":
                log.warning("dry_run mode — no POST sent. Set dry_run: false in config.yaml to register for real.")
            else:
                msg = f"⚠️ Registration failed: {outcome.reason}\nURL: {outcome.final_url}"
                log.warning(msg); tg(msg)
            return

        if attempt < RETRY_COUNT:
            log.info("Not open yet (%s), retrying in %ds...", result.status, RETRY_DELAY)
            time.sleep(RETRY_DELAY)

    msg = f"❌ Registration button not found after {RETRY_COUNT} attempts — please check manually\n{target.calendar_url}"
    log.error(msg); tg(msg)


if __name__ == "__main__":
    main()
