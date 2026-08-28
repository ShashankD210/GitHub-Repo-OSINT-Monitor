#!/usr/bin/env bash
# Installs the GitHub OSINT Monitor as a systemd timer for one repo.
# Usage: sudo ./install.sh owner/name
set -euo pipefail

REPO_ARG="${1:?Usage: sudo ./install.sh owner/name}"
INSTANCE="$(echo "$REPO_ARG" | tr '/' '-')"
INSTALL_DIR=/opt/github-osint-monitor

echo "==> Installing GitHub OSINT Monitor for $REPO_ARG (instance: $INSTANCE)"

mkdir -p "$INSTALL_DIR"/{state,dashboards,venv}
mkdir -p /var/log/github-osint-monitor

cp ../monitor.py "$INSTALL_DIR/monitor.py"
cp ../requirements.txt "$INSTALL_DIR/requirements.txt"

if [ ! -f "$INSTALL_DIR/.env" ]; then
    cp .env.example "$INSTALL_DIR/.env"
    sed -i "s|REPO=owner/name|REPO=$REPO_ARG|" "$INSTALL_DIR/.env"
    echo "==> Wrote $INSTALL_DIR/.env — EDIT IT to add your GITHUB_TOKEN and SLACK_WEBHOOK_URL before starting the timer."
else
    echo "==> $INSTALL_DIR/.env already exists, leaving it as-is."
fi

python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

cp github-osint-monitor@.service /etc/systemd/system/
cp github-osint-monitor@.timer /etc/systemd/system/

systemctl daemon-reload
systemctl enable --now "github-osint-monitor@${INSTANCE}.timer"

echo "==> Done. Check status with:"
echo "    systemctl status github-osint-monitor@${INSTANCE}.timer"
echo "    journalctl -u github-osint-monitor@${INSTANCE}.service -f"
echo "    tail -f /var/log/github-osint-monitor/${INSTANCE}.log"
echo "    open $INSTALL_DIR/dashboards/${INSTANCE}.html"
