#!/usr/bin/env bash
# Idempotent installer for the USOS Watcher on Ubuntu 22.04.
# Run as root (or with sudo). Safe to re-run.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root (sudo)." >&2
  exit 1
fi

APP_DIR="/opt/usos-watcher/app"
VENV_DIR="/opt/usos-watcher/venv"
ETC_DIR="/etc/usos-watcher"
STATE_DIR="/var/lib/usos-watcher"
LOG_DIR="/var/log/usos-watcher"
USER_NAME="usoswatch"

echo "==> apt install python deps"
apt update
apt install -y python3 python3-venv python3-pip ca-certificates

echo "==> creating dedicated user"
if ! id -u "$USER_NAME" >/dev/null 2>&1; then
  useradd -r -s /usr/sbin/nologin -d /opt/usos-watcher "$USER_NAME"
fi

echo "==> creating directories"
install -d -m 0755 -o "$USER_NAME" -g "$USER_NAME" /opt/usos-watcher
install -d -m 0755 -o "$USER_NAME" -g "$USER_NAME" "$STATE_DIR"
install -d -m 0755 -o "$USER_NAME" -g "$USER_NAME" "$LOG_DIR"
install -d -m 0700 -o root         -g "$USER_NAME" "$ETC_DIR"

echo "==> copying application"
# Assumes this install.sh is run from within the unpacked source tree.
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$APP_DIR"
rsync -a --delete \
    --exclude '.git' \
    --exclude '.env' \
    --exclude '__pycache__' \
    --exclude '.venv' \
    --exclude 'venv' \
    "$SRC_DIR"/ "$APP_DIR"/
chown -R "$USER_NAME":"$USER_NAME" "$APP_DIR"

echo "==> python venv"
if [[ ! -d "$VENV_DIR" ]]; then
  sudo -u "$USER_NAME" python3 -m venv "$VENV_DIR"
fi
sudo -u "$USER_NAME" "$VENV_DIR/bin/pip" install --upgrade pip
sudo -u "$USER_NAME" "$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "==> .env template"
if [[ ! -f "$ETC_DIR/.env" ]]; then
  cp "$APP_DIR/.env.example" "$ETC_DIR/.env"
  chown root:"$USER_NAME" "$ETC_DIR/.env"
  chmod 0640 "$ETC_DIR/.env"
  echo "    Edit $ETC_DIR/.env with real secrets, then re-run this script."
fi

echo "==> systemd units"
install -m 0644 "$APP_DIR/deploy/usos-watcher.service" /etc/systemd/system/usos-watcher.service
install -m 0644 "$APP_DIR/deploy/usos-watcher.timer"   /etc/systemd/system/usos-watcher.timer
systemctl daemon-reload
systemctl enable --now usos-watcher.timer

echo "==> done"
echo
echo "Next steps:"
echo "  1. Verify $ETC_DIR/.env has all secrets filled."
echo "  2. Trigger a one-off run:    sudo systemctl start usos-watcher.service"
echo "  3. Follow logs:              sudo journalctl -u usos-watcher.service -f"
echo "  4. Tail file log:            sudo tail -f $LOG_DIR/watcher.log"
echo "  5. Inspect state:            sudo cat $STATE_DIR/state.json"
echo "  6. After SPEC §12 tests pass, edit $APP_DIR/config.yaml -> dry_run: false"
