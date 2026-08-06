#!/usr/bin/env bash
set -euo pipefail

RDP_DIR="${RDP_DIR:-$HOME/.config/rdp}"
SERVICE_DIR="${SERVICE_DIR:-$HOME/.config/systemd/user}"
SERVICE_NAME="rdp-profile.service"
SERVICE_PATH="$SERVICE_DIR/$SERVICE_NAME"
VENV_DIR="$RDP_DIR/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [[ ! -d "$RDP_DIR" ]]; then
  echo "RDP directory not found: $RDP_DIR" >&2
  exit 1
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python executable not found: $PYTHON_BIN" >&2
  exit 1
fi

mkdir -p "$SERVICE_DIR"

if [[ ! -d "$VENV_DIR" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

cat > "$SERVICE_PATH" <<'EOF'
[Unit]
Description=KDE Plasma RDP Profile Monitor
After=graphical-session.target

[Service]
Type=simple
WorkingDirectory=%h/.config/rdp
Environment=PYTHONUNBUFFERED=1
ExecStart=%h/.config/rdp/.venv/bin/python %h/.config/rdp/rdp-monitor.py
ExecStop=%h/.config/rdp/.venv/bin/python %h/.config/rdp/rdp-monitor.py stop
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now "$SERVICE_NAME"

echo "Installed and started: $SERVICE_NAME"
systemctl --user --no-pager --full status "$SERVICE_NAME" || true