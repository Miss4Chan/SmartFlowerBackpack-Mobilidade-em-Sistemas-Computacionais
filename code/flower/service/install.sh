#!/usr/bin/env bash
# Generates and installs the SmartFlower systemd service for this machine.
# Detects the repo location automatically — no hardcoded paths.
#
# Usage (from code/flower/):
#   sudo bash service/install.sh
#
# After install:
#   sudo systemctl start smartflower
#   sudo systemctl status smartflower
#   journalctl -u smartflower -f
set -e
cd "$(dirname "$0")/.."   # run from code/flower/

REPO_DIR="$(pwd)"
SERVICE_NAME="smartflower"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# Preserve the real user even when invoked via sudo
RUN_USER="${SUDO_USER:-$(whoami)}"

echo "[install] Repo root : ${REPO_DIR}"
echo "[install] Run as    : ${RUN_USER}"
echo "[install] Service   : ${SERVICE_FILE}"

cat > "${SERVICE_FILE}" << EOF
[Unit]
Description=SmartFlower main loop
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${REPO_DIR}
ExecStart=/bin/bash ${REPO_DIR}/scripts/start_rpi.sh
Environment=PYTHONUNBUFFERED=1

# Restart automatically on any failure; wait 10 s before retrying
Restart=on-failure
RestartSec=10

# All stdout/stderr goes to journald:
#   journalctl -u ${SERVICE_NAME}.service -f
StandardOutput=journal
StandardError=journal

# Give Python time to run actuators.cleanup() before killing
KillSignal=SIGTERM
TimeoutStopSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"

echo "[install] Done."
echo "[install] Start  :  sudo systemctl start ${SERVICE_NAME}"
echo "[install] Status :  sudo systemctl status ${SERVICE_NAME}"
echo "[install] Logs   :  journalctl -u ${SERVICE_NAME} -f"
