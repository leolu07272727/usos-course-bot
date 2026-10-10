"""Entry point.

Usage:
  python -m usos_watcher           # run one watcher cycle (default)
  python -m usos_watcher status    # show current state for all targets
  python -m usos_watcher run       # same as default

SPEC.md §4 describes the cycle.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from typing import Optional

from . import auth, fetcher, parser, registrar, state
from . import notifier_telegram as notifier
from .config import Config, TargetCfg, load_config
from .logger import setup_logging

log = logging.getLogger(__name__)

FAILURE_NOTIFY_COOLDOWN_SEC = 6 * 3600


def _seconds_since(iso_ts: Optional[str]) -> float:
    if not iso_ts:
        return float("inf")
    try:
        dt = datetime.fromisoformat(iso_ts)
    except Exception:
        return float("inf")
    return (datetime.now(timezone.utc).astimezone() - dt).total_seconds()


def _maybe_send_heartbeat(cfg: Config, st: state.State) -> None:
    try:
        hh, mm = [int(x) for x in cfg.email.daily_heartbeat_time.split(":")]
    except Exception:
        log.error("Invalid daily_heartbeat_time: %r", cfg.email.daily_heartbeat_time)
        return

    now_local = datetime.now().astimezone()
    today_target = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if now_local < today_target:
        return

    last = st.last_heartbeat_email_at
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
        except Exception:
            last_dt = None
        if last_dt and last_dt >= today_target:
            return

    # Build a summary across all targets
    lines = []
    for target in cfg.targets:
        cs = st.course(target.registration_code)
        lines.append(
            f"  {target.registration_code}: {cs.last_status or 'init'}"
            + (f" ✅ registered" if cs.registered_at else "")
        )

    ok = notifier.send(
        cfg,
        notifier.subject_for("💓", "watcher heartbeat"),
        "USOS Watcher daily heartbeat.\n\n" + "\n".join(lines) + "\n",
    )
    if ok:
        st.last_heartbeat_email_at = now_local.isoformat(timespec="seconds")


def _handle_status(
    cfg: Config,
    st: state.State,
    session,
    result: parser.ParseResult,
    prior_status: Optional[str],
    target: TargetCfg,
    target_index: int,
) -> None:
    code = target.registration_code
    cs = st.course(code)
    cu = target.calendar_url

    if result.status == state.STATUS_COORDINATOR_ONLY:
        if prior_status and prior_status != result.status:
            notifier.send(cfg,
                          notifier.subject_for("⚠️", f"[{code}] state → {result.status}"),
                          notifier.body_state_change(code, prior_status, result.status, cu))
        return

    if result.status == state.STATUS_CLOSED:
        if prior_status and prior_status != result.status:
            notifier.send(cfg,
                          notifier.subject_for("⚠️", f"[{code}] state → {result.status}"),
                          notifier.body_state_change(code, prior_status, result.status, cu))
        return

    if result.status == state.STATUS_ROW_NOT_FOUND:
        if prior_status and prior_status != result.status:
            notifier.send(cfg,
                          notifier.subject_for("⚠️", f"[{code}] row no longer present"),
                          notifier.body_row_not_found(code, cu))
        return

    if result.status == state.STATUS_PENDING:
        if prior_status and prior_status != result.status:
            notifier.send(cfg,
                          notifier.subject_for("⏳", f"[{code}] state → {result.status}"),
                          notifier.body_state_change(code, prior_status, result.status, cu))
        return

    if result.status == state.STATUS_UNKNOWN:
        notifier.send(cfg,
                      notifier.subject_for("⚠️", f"[{code}] unclassifiable row"),
                      notifier.body_unknown(code, cu, result.row_snippet or ""))
        return

    if result.status == state.STATUS_FCFS_OPEN:
        log.info("[%s] FCFS_OPEN — starting registration flow", code)
        outcome = registrar.register(session, cfg, result.register_url,
                                     target_index=target_index)

        if outcome.reason == "already_registered":
            log.info("[%s] brdg2 detected already registered; marking done", code)
            cs.registered_at = state._now_iso()
            cs.registered_group = "already registered (detected by brdg2 odczyt=1)"
            return

        if outcome.success:
            cs.registered_at = state._now_iso()
            cs.registered_group = (
                outcome.chosen_group.combined_text if outcome.chosen_group else None
            )
            notifier.send(
                cfg,
                notifier.subject_for("✅", f"[{code}] registered"),
                notifier.body_success(
                    code,
                    outcome.chosen_group.name if outcome.chosen_group else "?",
                    outcome.chosen_group.lecturer if outcome.chosen_group else "?",
                    outcome.final_url or cu,
                ),
            )
        elif outcome.reason == "no_preferred_group_found":
            notifier.send(
                cfg,
                notifier.subject_for("🤔", f"[{code}] open but no preferred group"),
                notifier.body_no_preferred_group(code, cu, result.register_url),
            )
        elif outcome.reason == "dry_run":
            notifier.send(
                cfg,
                notifier.subject_for("⚠️", f"[{code}] DRY-RUN reached group submission"),
                notifier.body_state_change(code, prior_status, "FCFS_OPEN (dry-run)", cu)
                + (
                    f"\nWould have submitted group: "
                    f"{outcome.chosen_group.combined_text if outcome.chosen_group else '?'}\n"
                ),
            )
        else:
            notifier.send(
                cfg,
                notifier.subject_for("⚠️", f"[{code}] registration failed: {outcome.reason}"),
                f"Reason: {outcome.reason}\n"
                f"Final URL: {outcome.final_url}\n\n"
                f"Body snippet:\n{outcome.body_snippet or ''}\n",
            )


def run_once(cfg: Config) -> int:
    st = state.load_state(cfg.paths.state_path)

    had_login_failures_any = False
    had_http_failures_any = False

    session = None
    for i, target in enumerate(cfg.targets):
        code = target.registration_code
        cs = st.course(code)

        if cs.registered_at and not cfg.behavior.refill_mode:
            log.info("[%s] Already registered at %s — skipping", code, cs.registered_at)
            continue

        prior_status = cs.last_status
        had_login_failures = cs.consecutive_login_failures > 0
        had_http_failures = cs.consecutive_http_failures > 0
        if had_login_failures:
            had_login_failures_any = True
        if had_http_failures:
            had_http_failures_any = True

        # Login once and reuse session across targets
        if session is None:
            try:
                session = auth.login_or_resume(
                    cfg.secrets.usos_username,
                    cfg.secrets.usos_password,
                    user_agent=cfg.http.user_agent,
                    timeout=cfg.http.request_timeout_sec,
                    cookies_path=cfg.paths.cookies_path,
                )
                for t in cfg.targets:
                    st.course(t.registration_code).consecutive_login_failures = 0
            except auth.UsosUnavailableError as e:
                cs.consecutive_http_failures += 1
                log.warning("[%s] USOS unavailable (%d in a row): %s",
                            code, cs.consecutive_http_failures, e)
                if (cs.consecutive_http_failures >= cfg.email.failure_threshold_http
                        and _seconds_since(cs.last_http_failure_email_at)
                        > FAILURE_NOTIFY_COOLDOWN_SEC):
                    ok = notifier.send(
                        cfg,
                        notifier.subject_for("❌", "USOS appears to be down"),
                        notifier.body_failure_http(code, cs.consecutive_http_failures),
                    )
                    if ok:
                        cs.last_http_failure_email_at = state._now_iso()
                state.save_state(cfg.paths.state_path, st)
                return 2
            except Exception as e:
                cs.consecutive_login_failures += 1
                log.error("[%s] Login failed (%d in a row): %s",
                          code, cs.consecutive_login_failures, e)
                if (cs.consecutive_login_failures >= cfg.email.failure_threshold_login
                        and _seconds_since(cs.last_login_failure_email_at)
                        > FAILURE_NOTIFY_COOLDOWN_SEC):
                    ok = notifier.send(
                        cfg,
                        notifier.subject_for("❌", "repeated CAS login failure"),
                        notifier.body_failure_login(code, cs.consecutive_login_failures),
                    )
                    if ok:
                        cs.last_login_failure_email_at = state._now_iso()
                state.save_state(cfg.paths.state_path, st)
                return 1

        # Fetch calendar
        try:
            r = fetcher.get(
                session,
                target.calendar_url,
                timeout=cfg.http.request_timeout_sec,
                retries=cfg.http.retries,
                jitter_max_sec=cfg.http.jitter_max_sec,
            )
            cs.consecutive_http_failures = 0
        except Exception as e:
            cs.consecutive_http_failures += 1
            log.error("[%s] Calendar fetch failed (%d in a row): %s",
                      code, cs.consecutive_http_failures, e)
            if (cs.consecutive_http_failures >= cfg.email.failure_threshold_http
                    and _seconds_since(cs.last_http_failure_email_at)
                    > FAILURE_NOTIFY_COOLDOWN_SEC):
                ok = notifier.send(
                    cfg,
                    notifier.subject_for("❌", f"[{code}] repeated HTTP failure"),
                    notifier.body_failure_http(code, cs.consecutive_http_failures),
                )
                if ok:
                    cs.last_http_failure_email_at = state._now_iso()
            continue

        # Recovery notifications
        if had_login_failures and cs.last_login_failure_email_at:
            notifier.send(
                cfg,
                notifier.subject_for("✅", "CAS login working again"),
                f"USOS Watcher recovered from CAS login failure. Monitoring {code} resumed.\n",
            )
            cs.last_login_failure_email_at = None
        if had_http_failures and cs.last_http_failure_email_at:
            notifier.send(
                cfg,
                notifier.subject_for("✅", f"[{code}] USOS back online"),
                f"USOS Watcher: USOSweb is responding again, {code} monitoring resumed.\n",
            )
            cs.last_http_failure_email_at = None

        result = parser.parse_calendar(
            r.text,
            base_url=target.calendar_url,
            registration_code=target.registration_code,
            course_name_match=target.course_name_match,
        )
        log.info("[%s] Parsed status: %s", code, result.status)

        state.update_status(st, code, result.status)
        _handle_status(cfg, st, session, result, prior_status, target, i)

    _maybe_send_heartbeat(cfg, st)
    state.save_state(cfg.paths.state_path, st)
    return 0


# ---------------------------------------------------------------------------
# Status command
# ---------------------------------------------------------------------------

def cmd_status(cfg: Config) -> int:
    st = state.load_state(cfg.paths.state_path)
    print(f"{'Course':<30} {'Status':<20} {'Registered':<12} {'Group'}")
    print("-" * 90)
    for target in cfg.targets:
        cs = st.course(target.registration_code)
        status = cs.last_status or "—"
        reg = "✅ yes" if cs.registered_at else "no"
        group = cs.registered_group or "—"
        print(f"{target.registration_code:<30} {status:<20} {reg:<12} {group}")
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(prog="usos_watcher",
                                 description="USOS course registration watcher")
    ap.add_argument("command", nargs="?", default="run",
                    choices=["run", "status"],
                    help="run (default) or status")
    args = ap.parse_args()

    cfg = load_config()
    setup_logging(
        cfg.paths.log_dir,
        cfg.logging.level,
        cfg.logging.rotate_max_bytes,
        cfg.logging.rotate_backups,
        cfg.secret_values_for_scrubbing,
    )

    if args.command == "status":
        return cmd_status(cfg)

    try:
        with state.lock_file(cfg.paths.lock_path, wait_seconds=5):
            return run_once(cfg)
    except TimeoutError as e:
        log.warning("%s — previous run still in flight; exiting clean", e)
        return 0


if __name__ == "__main__":
    sys.exit(main())
