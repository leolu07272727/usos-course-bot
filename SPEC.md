# USOS Watcher — Specification

> **Audience**: Another Claude Code instance running on the VPS, and the human owner (呂維霖 / Lu Wei-Lin).
>
> **Read this top-to-bottom before touching code.** It encodes the user's intent, constraints, and the design decisions already agreed upon.

---

## 1. What this is

A small Python service that **monitors a course-registration page on the University of Warsaw's USOSweb** (https://usosweb.uw.edu.pl) and, the moment the page goes from "you can't act on it" to "you can register yourself", logs in and grabs a seat in the user's preferred group.

The user is currently looking at the Master Seminar registration `2600-IBP-MS2026Z`, which is in "Coordinated groups exchange" mode — only the course coordinator can move students between groups. The watcher must keep polling, and the instant any of the following becomes true:

- A. coordinator switches the round to FCFS (direct registration becomes available),
- B. a **new** registration round (FCFS) for the same course opens,
- C. a slot opens up because another student dropped out,

… the watcher logs in, registers the user, picks the preferred group, and emails them.

---

## 2. Owner & accounts

- **Owner**: Lu Wei-Lin (呂維霖). Email: `lex0321.01@gmail.com`.
- **USOS username**: `00700471597` (student ID). Stored on VPS as env var `USOS_USERNAME`.
- **USOS password**: stored on VPS as env var `USOS_PASSWORD`. **Never** log, email, or commit it.
- **SMTP**: iCloud SMTP with an app-specific password. Sender = owner's iCloud address, receiver = `lex0321.01@gmail.com`.

The CAS login at `https://logowanie.uw.edu.pl/cas/login` has **no captcha and no 2FA** for this user (confirmed manually). Plain username/password POST is enough.

---

## 3. Target & strategy

### 3.1 Target course

- **Course code**: `2600-IBP-MS2026Z` (Master Seminar — IBP — 2026Z)
- **Calendar URL** (where the row lives):
  `https://usosweb.uw.edu.pl/kontroler.php?_action=dla_stud/rejestracja/kalendarz&lang=en`
- **What "able to register" looks like**: the row contains an `<img src="...zarejestruj.svg">` and/or `<a href*="zarejestruj">`. That's the only signal we treat as actionable.

### 3.2 Group preference (priority order)

1. **Tier 1 — preferred**: group containing `Hensel` in its name/lecturer (Prof. Przemysław Hensel — AI-related topics). This is the goal.
2. **Tier 2 — fallback**: group whose title contains `AI` or `Artificial Intelligence`.
3. **Tier 3 — no auto-pick**: if neither tier matches, **do not pick any group**. Instead, send an email saying "registration is open but I couldn't find Hensel — please pick manually within X minutes" and exit. **Do not** click a random group as a fallback. The user has been burnt by that before.

### 3.3 After successful registration

- Stop all polling for that course code.
- Persist a "registered" flag in state so future cron runs don't try again.
- Send a **success email** including:
  - timestamp,
  - the group name and lecturer the watcher landed in,
  - direct USOS link to verify,
  - a one-liner: *"Reply if you'd like me to keep watching for a better group (refill mode)."*
- Refill mode is **off by default**. The user must explicitly opt in (set `refill_mode: true` in config).

---

## 4. Run mode (important — this is *not* a daemon)

The user **does not** want a long-running daemon. The service runs as a **systemd timer firing every 30 minutes** (and at boot). Each firing is a one-shot:

```
[load config + state]
   ↓
[skip if already registered]
   ↓
[CAS login → session cookies]
   ↓
[fetch calendar page]
   ↓
[parse target row]
   ↓
[branch on state]:
   ├─ ACTIONABLE → register → pick group → email success → mark done
   ├─ STATE_CHANGED (e.g. COORDINATOR_ONLY → something new) → email notice
   ├─ UNCHANGED → silent exit (no email)
   └─ ERROR → increment failure counter; email if threshold exceeded
   ↓
[persist state → exit 0]
```

### 4.1 Rationale for 30-minute cadence

- The user accepts the trade-off: cron is simple, survives reboots, and 30 min is fine for scenarios A and C (coordinator changes are not race-against-time).
- **Known limitation**: if a competitive FCFS round opens at an exact wall-clock time and fills within seconds, a 30-minute cron will likely miss it. This is documented under **§8 Open risks** and a `burst mode` is sketched in **§13 Future work** but not implemented in v1.

### 4.2 Daily heartbeat

Once per day (target 09:00 Warsaw time), send a one-line "watcher alive, last status = X" email. This is so the user notices if the VPS dies or USOS quietly bans the account.

Heartbeat is sent by whichever cron run first crosses 09:00 since the last heartbeat (tracked in state).

---

## 5. Email behaviour

Send mail in these and **only** these situations:

| Event | Send mail? | Subject prefix |
|---|---|---|
| Registration succeeded | ✅ | `[USOS ✅]` |
| State changed but not actionable (e.g. coordinator round closed, no new round yet) | ✅ | `[USOS ⚠️]` |
| Registration tried but Hensel/AI group unavailable | ✅ | `[USOS 🤔]` |
| CAS login failed once | ❌ (just log) | — |
| CAS login failed ≥ 3 consecutive runs | ✅ | `[USOS ❌]` |
| HTTP / parsing error once | ❌ | — |
| HTTP / parsing error ≥ 5 consecutive runs | ✅ | `[USOS ❌]` |
| Account seems blocked (CAS redirects to error page, IP banned 4xx for >1h) | ✅ | `[USOS 🚨]` |
| Daily heartbeat | ✅ | `[USOS 💓]` |
| Same state as last run (e.g. still COORDINATOR_ONLY) | ❌ | — |

Each email body MUST include the absolute USOS URL the watcher saw, so the user can click and verify.

---

## 6. File / module layout

```
usos-watcher/
├── README.md                 # human-facing, 1-screen quickstart
├── SPEC.md                   # this file — design + intent
├── requirements.txt
├── .env.example              # template; real .env lives outside git
├── config.yaml               # course + group preferences (committable)
├── usos_watcher/
│   ├── __init__.py
│   ├── __main__.py           # `python -m usos_watcher` entry point
│   ├── config.py             # load .env + config.yaml, validate
│   ├── state.py              # read/write state.json with file lock
│   ├── auth.py               # CAS login → requests.Session with cookies
│   ├── fetcher.py            # HTTP GET wrappers, retries, jitter
│   ├── parser.py             # BeautifulSoup; row → enum status
│   ├── registrar.py          # the actual click+confirm flow
│   ├── notifier.py           # SMTP email send
│   └── logger.py             # rotating file logs, no secrets
├── tests/
│   ├── fixtures/             # saved HTML samples for parser tests
│   │   ├── coordinator_only.html
│   │   ├── fcfs_open.html
│   │   └── closed.html
│   ├── test_parser.py
│   └── test_state.py
└── deploy/
    ├── usos-watcher.service  # oneshot systemd unit
    ├── usos-watcher.timer    # systemd timer (every 30 min)
    └── install.sh            # idempotent installer
```

### 6.1 Why two cron mechanisms aren't needed

systemd timers are sufficient; do not also add a crontab entry. The installer must be idempotent so re-running it is safe.

---

## 7. Config & secrets

### 7.1 `.env` (lives at `/etc/usos-watcher/.env`, mode 0600, owner root)

```
USOS_USERNAME=00700471597
USOS_PASSWORD=<set on VPS only, never in repo>
SMTP_HOST=smtp.mail.me.com
SMTP_PORT=587
SMTP_USERNAME=<owner iCloud address>
SMTP_PASSWORD=<iCloud app-specific password>
MAIL_FROM=<owner iCloud address>
MAIL_TO=lex0321.01@gmail.com
```

### 7.2 `config.yaml`

```yaml
target:
  registration_code: "2600-IBP-MS2026Z"
  course_name_match: "Master Seminar - IBP"
  calendar_url: "https://usosweb.uw.edu.pl/kontroler.php?_action=dla_stud/rejestracja/kalendarz&lang=en"

group_preference:
  tier1_keywords: ["Hensel", "Przemysław Hensel", "Przemyslaw Hensel"]
  tier2_keywords: ["AI", "Artificial Intelligence"]
  on_no_match: "email_and_exit"   # NEVER "pick_first"

behavior:
  refill_mode: false              # keep searching for a better group after success
  dry_run: true                   # ← start TRUE; flip to false after first verified run

email:
  daily_heartbeat_time: "09:00"
  failure_threshold_login: 3
  failure_threshold_http: 5

logging:
  level: INFO
  path: /var/log/usos-watcher/watcher.log
  rotate_max_bytes: 5242880       # 5 MB
  rotate_backups: 5
```

### 7.3 Multi-course support

`target` may also be a **list** of objects. Single-object form is sugar for a one-element list. Each entry gets independent state under its registration_code in `state.json`.

For now only one entry will be used, but the parser/registrar logic must already handle a list. Don't paint yourself into a corner.

---

## 8. State machine & state.json

```
state.json (path: /var/lib/usos-watcher/state.json, mode 0600)

{
  "courses": {
    "2600-IBP-MS2026Z": {
      "last_check_at": "2026-06-04T21:30:00+02:00",
      "last_status": "COORDINATOR_ONLY",
      "last_status_change_at": "2026-06-04T20:55:00+02:00",
      "registered_at": null,
      "registered_group": null,
      "consecutive_login_failures": 0,
      "consecutive_http_failures": 0
    }
  },
  "last_heartbeat_email_at": "2026-06-04T09:00:00+02:00",
  "schema_version": 1
}
```

### 8.1 Status enum

- `COORDINATOR_ONLY` — row has "Coordinated groups exchange" / "wymian grup" / "koordynowan" / "no actions" / "brak akcji". Skip; do nothing.
- `CLOSED` — row labelled closed. No action.
- `FCFS_OPEN` — row has `img[src*="zarejestruj.svg"]` or `a[href*="zarejestruj"]`. **This is the signal to act.**
- `UNKNOWN` — row found but matched none of the above. **Email the user** and dump the raw HTML to logs.
- `ROW_NOT_FOUND` — calendar page loaded but the target course code is not present (e.g. round ended). Email once on the transition, then go quiet.

### 8.2 Concurrency

systemd already serialises the timer firings (`OnUnitActiveSec=30min`), but as a belt-and-braces measure acquire a `flock` on `/var/lock/usos-watcher.lock` at process start. If we can't acquire it within 5 seconds, log "previous run still in flight" and exit 0.

---

## 9. Registration flow (the part that actually clicks)

When status becomes `FCFS_OPEN`:

1. **Re-fetch the calendar page**, just-in-time (don't trust an earlier scrape — the page might have changed in the seconds since).
2. **Resolve the register link**: extract the `href` of the `<a>` around `zarejestruj.svg` in the target row.
3. **POST / follow it** while keeping cookies. USOS often redirects through a confirm intermediate; follow redirects.
4. **Land on the group selection page** (`grupyPrzedmiotu` or a form with `action*="zarejestruj"`).
5. **Pick the group** using §3.2 tier rules.
   - Parse each group row's text (group title + lecturer column).
   - Match tier 1 first (Hensel). If unique → that one.
   - Otherwise tier 2. If unique → that one. If multiple → prefer the one with most free spots (parse `current/limit` like `5/15`).
   - Otherwise: stop, email user with the page URL.
6. **Submit the group form**. Detect success by either:
   - HTTP 200 + presence of "You are registered" / "Jesteś zarejestrowany" / a green tick on the next page, OR
   - the calendar page row now showing the user as registered.
7. **Email success** (§5) and persist `registered_at` + `registered_group`.

### 9.1 Dry-run mode

When `behavior.dry_run: true`, perform steps 1–5 but NEVER fire the final POST in step 6. Email the user with what *would* have happened. This is how we verify the flow before going live.

---

## 10. Operational safety

| Concern | Mitigation |
|---|---|
| USOS rate-limits or bans the account | 30-min cadence + per-run single fetch keeps traffic minimal. On HTTP 429 or repeated 403/5xx, set `consecutive_http_failures++`, back off (skip next run via state flag), and email after 5. |
| Plain password in logs | `logger.py` MUST scrub any env-var value from formatted records. Never `log.info(f"...{password}...")`. |
| Crash mid-registration leaves user double-registered | Idempotency: before registering, re-parse and verify status is still `FCFS_OPEN`. If a registration POST succeeds, **immediately** persist `registered_at` to state. |
| systemd restart loop hammering USOS | Use `Type=oneshot` (not `simple`) so the unit completes after each run. No `Restart=` directive. |
| State file corruption | Write to `state.json.tmp` then `os.replace()`. Validate `schema_version` on read; refuse to run if it's higher than we understand. |
| Secrets in git | `.env` and `state.json` are in `.gitignore`. `pre-commit` hook (optional) greps for the username before commit. |

---

## 11. Deployment to Ubuntu 22.04 VPS

### 11.1 One-time setup (run as root)

```bash
# 1. Install
sudo apt update && sudo apt install -y python3.10-venv git
sudo useradd -r -s /usr/sbin/nologin -d /opt/usos-watcher usoswatch
sudo mkdir -p /opt/usos-watcher /etc/usos-watcher /var/log/usos-watcher /var/lib/usos-watcher
sudo chown -R usoswatch:usoswatch /opt/usos-watcher /var/log/usos-watcher /var/lib/usos-watcher
sudo chmod 0700 /etc/usos-watcher

# 2. Copy code
sudo -u usoswatch git clone <repo> /opt/usos-watcher/app
# or: scp -r the folder

# 3. venv + deps
sudo -u usoswatch python3 -m venv /opt/usos-watcher/venv
sudo -u usoswatch /opt/usos-watcher/venv/bin/pip install -r /opt/usos-watcher/app/requirements.txt

# 4. .env
sudo cp /opt/usos-watcher/app/.env.example /etc/usos-watcher/.env
sudo nano /etc/usos-watcher/.env       # fill in real secrets
sudo chmod 0600 /etc/usos-watcher/.env
sudo chown root:usoswatch /etc/usos-watcher/.env

# 5. systemd
sudo cp /opt/usos-watcher/app/deploy/usos-watcher.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now usos-watcher.timer
```

### 11.2 systemd unit (oneshot — runs once per timer firing)

```ini
# /etc/systemd/system/usos-watcher.service
[Unit]
Description=USOS Watcher (oneshot)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=usoswatch
EnvironmentFile=/etc/usos-watcher/.env
WorkingDirectory=/opt/usos-watcher/app
ExecStart=/opt/usos-watcher/venv/bin/python -m usos_watcher
Nice=10
PrivateTmp=true
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/var/lib/usos-watcher /var/log/usos-watcher
ProtectHome=true
```

### 11.3 systemd timer

```ini
# /etc/systemd/system/usos-watcher.timer
[Unit]
Description=Run USOS Watcher every 30 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=30min
Persistent=true
AccuracySec=1min

[Install]
WantedBy=timers.target
```

### 11.4 Verifying it works

```bash
sudo systemctl list-timers usos-watcher.timer
sudo systemctl start usos-watcher.service     # force a run now
sudo journalctl -u usos-watcher.service -f    # follow logs
sudo tail -f /var/log/usos-watcher/watcher.log
sudo cat /var/lib/usos-watcher/state.json
```

---

## 12. Test plan (must pass before flipping `dry_run` to false)

1. **Unit**: parser correctly classifies the three fixture HTML files into the right status enum.
2. **Unit**: state writes survive simulated crash (kill -9 mid-write should leave a valid prior version).
3. **Integration (dry-run, real USOS)**:
   - First run logs in successfully, finds the Master Seminar row, classifies it as `COORDINATOR_ONLY`, sends **no email**, exits 0.
   - Re-run within 30 minutes: lock prevents overlap (verifiable by spawning two simultaneously).
   - Force-trigger a state change by editing state.json's `last_status` to `CLOSED`; next run should email a state-change notice.
4. **Dry-run simulated FCFS**: temporarily patch the parser to always return `FCFS_OPEN` for the target row. Run the full flow, confirm it reaches step 5 (group selection) and stops without firing step 6. Inspect the email it sends.
5. **Heartbeat**: set `daily_heartbeat_time` to a minute in the future, run, confirm heartbeat email arrives once and not again on the next run.
6. **Failure thresholds**: set `USOS_PASSWORD` wrong, run 3 times, confirm third run sends the `[USOS ❌]` email.

Only after **all** the above pass do we change `behavior.dry_run` to `false` in `/opt/usos-watcher/config.yaml` and restart the timer.

---

## 13. Future work (NOT in v1)

- **Burst mode**: when a known FCFS round is scheduled (e.g. registration opens 2026-06-15 21:00), allow a per-window config that switches polling to 3–5 second cadence for `±15 min` around that wall-clock time. Implementation: a sibling systemd timer triggered by a calendar file.
- **Telegram notifications** (the owner already has a Claude Code Telegram bridge — could plug into it).
- **Web UI** to show current state without SSH.
- **Cookie-jar mode**: if CAS ever adds 2FA/captcha, accept a manually-exported cookie jar and just refresh it.

---

## 14. Open risks (acknowledged, not fixed)

1. **30-min cadence misses competitive FCFS races**. Mitigated only by burst mode (§13).
2. **USOS may change HTML markup** between semesters. Parser tests use saved fixtures; if the live page diverges, the parser will emit `UNKNOWN` and the user will be emailed.
3. **CAS may add 2FA without notice** (university IT changes). Mitigation: cookie-jar mode (§13). Current code should fail loudly with a clear error if CAS responds with a new page shape, not silently retry.
4. **Account ban risk** if USOS treats the polling as abuse. 30-min cadence is conservative; we additionally back off on 429/403 (§10).

---

## 15. Don't-do list (for whoever picks this up next)

- ❌ Don't add a "fallback: pick the first group" path. Tier 3 = email, full stop.
- ❌ Don't log the password. Don't email the password. Don't write the password to state.json.
- ❌ Don't commit `.env` or `state.json` to git.
- ❌ Don't poll faster than every 30 minutes without owner sign-off.
- ❌ Don't run as root.
- ❌ Don't add a "convenience" mode that disables `dry_run` automatically.

---

## 16. Hand-off checklist for VPS Claude Code

When picking this up on the VPS, before any change:

1. Read this `SPEC.md` end-to-end.
2. Read `state.json` to know what's already happened.
3. Read the last 200 lines of `/var/log/usos-watcher/watcher.log`.
4. Confirm `behavior.dry_run` value in `config.yaml`. If it's `true`, that's intentional.
5. If you need to change behaviour, edit `config.yaml`. Code changes go through a PR / commit; never patch live `/opt/usos-watcher/app` files in place without committing the change.

End of spec.
