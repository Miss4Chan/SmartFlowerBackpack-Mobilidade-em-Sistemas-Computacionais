#!/usr/bin/env bash
# SmartFlower hardware startup:
#   MN-CSE → mDNS advertiser → main loop
#
# systemd calls this script directly; when main.py exits, systemd restarts it.
set -e
cd "$(dirname "$0")/.."   # run from code/flower/
export PYTHONIOENCODING=utf-8   # prevent Unicode crashes on Windows cp1252 terminals
export PYTHONUTF8=1             # Python 3.7+ UTF-8 mode — overrides locale/venv activation

# ── Identity ───────────────────────────────────────────────────────────────────
if [ -f .env ]; then
    set -o allexport
    source .env
    set +o allexport
fi

if [ -z "$FLOWER_ID" ]; then
    echo "[flower] ERROR: FLOWER_ID not set — copy .env.example to .env"
    exit 1
fi

CSE_ID="id-mn-flower-${FLOWER_ID}"
CSE_NAME="cse-mn-flower-${FLOWER_ID}"
ACME_PORT=$(( 8080 + FLOWER_ID ))

# ── Python ────────────────────────────────────────────────────────────────────
VENV="$(pwd)/.venv"
if   [ -f "$VENV/Scripts/python.exe" ]; then PYTHON="$VENV/Scripts/python.exe"
elif [ -f "$VENV/Scripts/python" ];     then PYTHON="$VENV/Scripts/python"
elif [ -f "$VENV/bin/python" ];         then PYTHON="$VENV/bin/python"
elif command -v python3 &>/dev/null;    then PYTHON="python3"
elif command -v python  &>/dev/null;    then PYTHON="python"
else echo "[flower] Python not found — install Python 3 first."; exit 1; fi
[ -f "$VENV/bin/activate" ] && source "$VENV/bin/activate"
echo "[flower] Python: $PYTHON"

ACME_DIR="$(pwd)/acme-cse-${FLOWER_ID}"
ADVERTISE_PID=""
ACME_PID=""

DISCOVER_PID=""

cleanup() {
    echo "[flower] Shutting down..."
    [ -n "$ADVERTISE_PID" ] && kill "$ADVERTISE_PID" 2>/dev/null
    [ -n "$DISCOVER_PID" ]  && kill "$DISCOVER_PID"  2>/dev/null
    [ -n "$ACME_PID" ]      && kill "$ACME_PID"      2>/dev/null
    wait 2>/dev/null
    echo "[flower] Stopped."
}
trap cleanup EXIT INT TERM

# ── MN-CSE ────────────────────────────────────────────────────────────────────
if [ ! -d "$ACME_DIR" ]; then
    echo "[flower] ACME directory not found — run bash scripts/install.sh first."
    exit 1
fi

LOCAL_IP=$($PYTHON -c \
  "import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); \
   s.connect(('8.8.8.8',1)); print(s.getsockname()[0])")
echo "[flower] Starting MN-CSE for flower ${FLOWER_ID} (IP: ${LOCAL_IP})..."
sed \
  -e "s|cseID *= */*id-mn-flower-[0-9]*|cseID               = ${CSE_ID}|" \
  -e "s|cseName *= *cse-mn-flower-[0-9]*|cseName             = ${CSE_NAME}|" \
  -e "s|cseHost *= *[0-9.]*|cseHost             = ${LOCAL_IP}|" \
  -e "s|httpPort *= *[0-9]*|httpPort            = ${ACME_PORT}|" \
  config/acme.ini > "$ACME_DIR/acme.ini"

echo "[flower] Effective ACME config:"
grep -nE "cseType|cseID|cseName|cseHost|startWithTUI|enable" "$ACME_DIR/acme.ini"

_ACME_PY="$PYTHON"
(cd "$ACME_DIR" && env -u _ "$_ACME_PY" -m acmecse) &
ACME_PID=$!

echo "[flower] Waiting for MN-CSE (up to 60 s)..."
DEADLINE=$(( $(date +%s) + 60 ))
until curl -sf "http://localhost:${ACME_PORT}/${CSE_NAME}" \
    -H "Accept: application/json" \
    -H "X-M2M-Origin: CAdmin" \
    -H "X-M2M-RI: healthcheck" \
    -H "X-M2M-RVI: 3" > /dev/null 2>&1; do
    if [ "$(date +%s)" -ge "$DEADLINE" ]; then
        echo "[flower] ERROR: MN-CSE did not respond in 60 s."
        echo "         If the database is corrupt, run:  bash scripts/reset.sh"
        exit 1
    fi
    sleep 1
done
echo "[flower] MN-CSE ready."

# ── mDNS Advertiser ───────────────────────────────────────────────────────────
echo "[flower] Starting mDNS advertiser..."
$PYTHON tools/advertise.py &
ADVERTISE_PID=$!

# ── Butler Discovery ──────────────────────────────────────────────────────────
echo "[flower] Starting butler discovery (contacts butler when found on mDNS)..."
$PYTHON tools/discover_butler.py &
DISCOVER_PID=$!

# ── Main Loop (SAREF description server starts as a thread inside main.py) ────
echo "[flower] Starting main loop (hardware)..."
cd core && $PYTHON -u main.py
