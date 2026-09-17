#!/usr/bin/env bash
# deploy/setup.sh -- app-level provisioning. Run as root INSIDE the target
# LXC (CT303) after the application code has already been placed at
# /opt/crypto-sim (see docs/superpowers/plans/2026-09-17-crypto-sim-deployment.md,
# Task 6, for how the code gets there). Safe to re-run: every step below is
# idempotent.
set -euo pipefail

APP_DIR=/opt/crypto-sim
APP_USER=cryptosim

if [ "$(id -u)" -ne 0 ]; then
  echo "setup.sh must run as root" >&2
  exit 1
fi

if [ ! -d "$APP_DIR" ]; then
  echo "$APP_DIR does not exist -- copy the application code there first" >&2
  exit 1
fi

apt-get update
apt-get install -y python3 python3-venv python3-pip tzdata

if ! id -u "$APP_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

if [ ! -d "$APP_DIR/.venv" ]; then
  sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
fi
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

if [ ! -f "$APP_DIR/.env" ]; then
  echo "WARNING: $APP_DIR/.env is missing -- Discord notifications will stay" \
       "silent until it is created (see .env.example). Not blocking the rest of setup." >&2
fi

# cd first: the DB path in config.yaml (db_path: crypto_sim.db) is relative,
# matching every other path in this app (see the plan's CWD-hardening
# decision) -- it must resolve against APP_DIR, not wherever setup.sh itself
# was invoked from.
( cd "$APP_DIR" && sudo -u "$APP_USER" "$APP_DIR/.venv/bin/python" -c \
    "from db.migrate import init_db; init_db('crypto_sim.db')" )

install -m 0644 "$APP_DIR/deploy/crypto-sim.service" /etc/systemd/system/crypto-sim.service
install -m 0644 "$APP_DIR/deploy/crypto-sim-dashboard.service" /etc/systemd/system/crypto-sim-dashboard.service
systemctl daemon-reload
systemctl enable --now crypto-sim.service
systemctl enable --now crypto-sim-dashboard.service

echo "Deploiement termine. Verifier avec :"
echo "  systemctl status crypto-sim.service crypto-sim-dashboard.service"
echo "  journalctl -u crypto-sim.service -f"
