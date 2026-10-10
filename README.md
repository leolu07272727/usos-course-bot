# USOS Watcher

A small Python service that polls the University of Warsaw USOSweb every
30 minutes, and the moment a target course-registration round becomes
self-registerable, logs in and grabs your preferred group.

**Read `SPEC.md` first.** It is the authoritative description of intent,
behaviour, and limits.

---

## Quick start (local dev)

```bash
cd usos-watcher
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # fill in real values
export USOS_WATCHER_CONFIG=$(pwd)/config.yaml
export USOS_WATCHER_STATE=$(pwd)/state.json
export USOS_WATCHER_LOG_DIR=$(pwd)/logs
export USOS_WATCHER_LOCK=$(pwd)/usos-watcher.lock
set -a; source .env; set +a
pytest                       # run tests first
python -m usos_watcher        # run one cycle
```

Logs end up in `./logs/watcher.log`, state in `./state.json`.

---

## Quick start (VPS — Ubuntu 22.04)

```bash
# 1. ship the source
scp -r usos-watcher root@<vps>:/root/usos-watcher

# 2. install
ssh root@<vps>
cd /root/usos-watcher
sudo bash deploy/install.sh

# 3. fill in secrets
sudo nano /etc/usos-watcher/.env

# 4. test one run
sudo systemctl start usos-watcher.service
sudo journalctl -u usos-watcher.service -f

# 5. confirm timer is armed
sudo systemctl list-timers usos-watcher.timer
```

`dry_run: true` is the default. Only flip it to `false` in
`/opt/usos-watcher/app/config.yaml` after the **§12 test plan** from `SPEC.md`
has been completed and you've seen a successful dry-run round-trip in
the logs.

---

## What this does NOT do

- It is **not a daemon**. It runs once per 30 minutes via `systemd.timer`.
- It will **not** click a random group as a fallback. If neither "Hensel"
  nor "AI" matches, you get an email and the watcher exits without
  selecting anything. See `SPEC.md` §3.2.
- It will **not** win a competitive FCFS race that opens at an exact wall
  clock time and saturates in seconds. 30-min cadence is wrong for that;
  see `SPEC.md` §13 (Burst mode — future work).
- It will **not** send "still polling, nothing changed" emails. Mail goes
  out only on the events enumerated in `SPEC.md` §5.

---

## Operational quick reference

```bash
# Force a one-off run
sudo systemctl start usos-watcher.service

# Tail logs
sudo tail -f /var/log/usos-watcher/watcher.log
sudo journalctl -u usos-watcher.service -f

# Inspect state
sudo cat /var/lib/usos-watcher/state.json | jq

# Stop the timer (suspend monitoring)
sudo systemctl stop usos-watcher.timer

# Update code (rsync from your machine, then re-run installer)
sudo bash /opt/usos-watcher/app/deploy/install.sh

# Reset state for a course (e.g., after deliberate manual unregistration)
sudo systemctl stop usos-watcher.timer
sudo -u usoswatch jq 'del(.courses["2600-IBP-MS2026Z"])' \
    /var/lib/usos-watcher/state.json > /tmp/s.json
sudo mv /tmp/s.json /var/lib/usos-watcher/state.json
sudo chown usoswatch:usoswatch /var/lib/usos-watcher/state.json
sudo systemctl start usos-watcher.timer
```

---

## File map

| Path | Purpose |
|---|---|
| `SPEC.md` | **Read this first.** Design and intent. |
| `config.yaml` | Course + group preferences (commit-safe). |
| `.env.example` | Template for secrets (real `.env` lives at `/etc/usos-watcher/.env`). |
| `usos_watcher/` | The package. |
| `usos_watcher/__main__.py` | Single-shot entry point. |
| `tests/` | `pytest` suite + saved HTML fixtures. |
| `deploy/install.sh` | Idempotent Ubuntu installer. |
| `deploy/usos-watcher.service` | systemd oneshot. |
| `deploy/usos-watcher.timer` | systemd timer (30-min cadence). |

---

## When the watcher is wrong

- **Got registered for the wrong group?** Unregister manually in USOSweb, edit
  `state.json` to clear `registered_at` and `registered_group` for that
  course, and review `config.yaml`'s `group_preference` keywords.
- **No emails at all for >24h?** Heartbeat broke. Check the timer is enabled
  (`systemctl status usos-watcher.timer`), and SMTP credentials are valid.
- **Spurious `[USOS ⚠️]` "unclassifiable row" emails?** USOS HTML probably
  changed. Save the offending row from the email, drop it into
  `tests/fixtures/`, update `parser.py`'s regex set, and add a test.
