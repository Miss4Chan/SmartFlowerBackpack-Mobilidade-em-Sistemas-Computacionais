#!/usr/bin/env bash
# Flower simulator startup:
#   MN-CSE → setup (if needed) → mDNS advertiser → simulator
#
# Usage: copy .env.example to .env, set FLOWER_ID=1 (or 2), then:
#   bash scripts/start_sim.sh   (from code/flower/)
set -e
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8   # prevent Unicode crashes on Windows cp1252 terminals
export PYTHONUTF8=1             # Python 3.7+ UTF-8 mode — overrides locale/venv activation

# ── Identity ───────────────────────────────────────────────────────────────────
if [ -f .env ]; then
    set -o allexport
    source .env
    set +o allexport
fi

if [ -z "$FLOWER_ID" ]; then
    echo "[flower] ERROR: FLOWER_ID is not set."
    echo "         Copy .env.example to .env and set FLOWER_ID=1 or FLOWER_ID=2"
    exit 1
fi

CSE_ID="id-mn-flower-${FLOWER_ID}"
CSE_NAME="cse-mn-flower-${FLOWER_ID}"
ACME_PORT=$(( 8080 + FLOWER_ID ))   # flower 1 → 8081, flower 2 → 8082

# ── Python ────────────────────────────────────────────────────────────────────
VENV="$(pwd)/.venv"
if   [ -f "$VENV/Scripts/python.exe" ]; then PYTHON="$VENV/Scripts/python.exe"
elif [ -f "$VENV/Scripts/python" ];     then PYTHON="$VENV/Scripts/python"
elif [ -f "$VENV/bin/python" ];         then PYTHON="$VENV/bin/python"
elif command -v python3 &>/dev/null;    then PYTHON="python3"
elif command -v python  &>/dev/null;    then PYTHON="python"
else echo "[flower] Python not found — install Python 3 first."; exit 1; fi
[ -f "$VENV/bin/activate" ]     && source "$VENV/bin/activate"
[ -f "$VENV/Scripts/activate" ] && source "$VENV/Scripts/activate"
echo "[flower] Python: $PYTHON"

ACME_DIR="$(pwd)/acme-cse-${FLOWER_ID}"    # per-flower directory
RESOURCES="sim/resources.json"
ACME_PID=""
ADVERTISE_PID=""
DISCOVER_PID=""

cleanup() {
    echo ""
    echo "[flower] Shutting down..."
    [ -n "$ACME_PID" ]      && kill "$ACME_PID"      2>/dev/null
    [ -n "$ADVERTISE_PID" ] && kill "$ADVERTISE_PID" 2>/dev/null
    [ -n "$DISCOVER_PID" ]  && kill "$DISCOVER_PID"  2>/dev/null
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

echo "[flower] Copying config for flower ${FLOWER_ID} (IP: ${LOCAL_IP}, port: ${ACME_PORT})..."
sed \
  -e "s|cseID *= */*id-mn-flower-[0-9]*|cseID               = ${CSE_ID}|" \
  -e "s|cseName *= *cse-mn-flower-[0-9]*|cseName             = ${CSE_NAME}|" \
  -e "s|cseHost *= *[0-9.]*|cseHost             = ${LOCAL_IP}|" \
  -e "s|httpPort *= *[0-9]*|httpPort            = ${ACME_PORT}|" \
  config/acme.ini > "$ACME_DIR/acme.ini"

echo "[flower] Starting MN-CSE (ACME)..."
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
echo "[flower] MN-CSE ready (${CSE_ID} on port ${ACME_PORT})."
sleep 3   # give ACME time to finish internal init after first response

# ── Resources ─────────────────────────────────────────────────────────────────
# Verify resources.json exists, AE exists in ACME, AND saref-descriptor SMD
# exists.  A partial previous run may leave the AE but no SMD; without the SMD
# check setup would be skipped and the butler would get 404 on discovery.
_ae_url="http://localhost:${ACME_PORT}/${CSE_NAME}/SmartFlower"
_smd_url="http://localhost:${ACME_PORT}/${CSE_NAME}/SmartFlower/saref-descriptor"
_chk=(-H "Accept: application/json" -H "X-M2M-Origin: CAdmin" -H "X-M2M-RI: check" -H "X-M2M-RVI: 3")
if [ -f "$RESOURCES" ] && \
   curl -sf "$_ae_url"  "${_chk[@]}" > /dev/null 2>&1 && \
   curl -sf "$_smd_url" "${_chk[@]}" > /dev/null 2>&1; then
    echo "[flower] resources.json OK, AE and SMD confirmed in ACME — skipping setup."
else
    echo "[flower] Setting up AE and containers..."
    rm -f "$RESOURCES"
    (cd sim && $PYTHON setup_resources.py)
fi

# ── mDNS Advertiser ───────────────────────────────────────────────────────────
echo "[flower] Starting mDNS advertiser (flower ${FLOWER_ID})..."
$PYTHON tools/advertise.py &
ADVERTISE_PID=$!

# ── Butler Discovery ──────────────────────────────────────────────────────────
echo "[flower] Starting butler discovery (contacts butler when found on mDNS)..."
$PYTHON tools/discover_butler.py &
DISCOVER_PID=$!

# ── Simulator ─────────────────────────────────────────────────────────────────
echo "[flower] Starting simulator (Ctrl+C to stop everything)..."
cd sim && $PYTHON -u simulator.py