#!/usr/bin/env bash
# Butler startup — MN-CSE → setup (if needed) → notifier → registration server
#
# The butler is passive: it advertises itself on mDNS and waits for flowers to
# contact it.  discovery.py runs a Flask server (port 5001) that flowers POST
# their details to; it then creates mirror containers and subscriptions.
#
# Usage: bash scripts/start.sh  (from code/butler/)
set -e
cd "$(dirname "$0")/.."   # now at code/butler/
export PYTHONIOENCODING=utf-8   # prevent Unicode crashes on Windows cp1252 terminals
export PYTHONUTF8=1             # Python 3.7+ UTF-8 mode — overrides locale/venv activation

# ── Python ────────────────────────────────────────────────────────────────────
VENV="$(cd ../.. && pwd)/.venv"
if   [ -f "$VENV/Scripts/python.exe" ]; then PYTHON="$VENV/Scripts/python.exe"
elif [ -f "$VENV/Scripts/python" ];     then PYTHON="$VENV/Scripts/python"
elif [ -f "$VENV/bin/python" ];         then PYTHON="$VENV/bin/python"
elif command -v python3 &>/dev/null;    then PYTHON="python3"
elif command -v python  &>/dev/null;    then PYTHON="python"
else echo "[butler] Python not found — install Python 3 first."; exit 1; fi
[ -f "$VENV/bin/activate" ]     && source "$VENV/bin/activate"
[ -f "$VENV/Scripts/activate" ] && source "$VENV/Scripts/activate"
echo "[butler] Python: $PYTHON"

ACME_DIR="$(pwd)/acme-cse"
RESOURCES="butler_resources.json"
ACME_PID=""
NOTIFIER_PID=""

cleanup() {
    echo ""
    echo "[butler] Shutting down..."
    [ -n "$ACME_PID" ]     && kill "$ACME_PID"     2>/dev/null
    [ -n "$NOTIFIER_PID" ] && kill "$NOTIFIER_PID" 2>/dev/null
    wait 2>/dev/null
    echo "[butler] Stopped."
}
trap cleanup EXIT INT TERM

# ── MN-CSE ────────────────────────────────────────────────────────────────────
if [ ! -d "$ACME_DIR" ]; then
    echo "[butler] ACME directory not found — run bash scripts/install.sh first."
    exit 1
fi

LOCAL_IP=$($PYTHON -c \
  "import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); \
   s.connect(('8.8.8.8',1)); print(s.getsockname()[0])")

echo "[butler] Patching config for this machine (IP: ${LOCAL_IP})..."
sed \
  -e "s/cseHost *= *[0-9.]*/cseHost             = ${LOCAL_IP}/" \
  config/acme.ini > "$ACME_DIR/acme.ini"

echo "[butler] Effective ACME config:"
grep -nE "cseType|cseID|cseName|cseHost|startWithTUI|enable" "$ACME_DIR/acme.ini"

echo "[butler] Starting MN-CSE (ACME)..."
_ACME_PY="$PYTHON"
(cd "$ACME_DIR" && env -u _ "$_ACME_PY" -m acmecse) &
ACME_PID=$!

echo "[butler] Waiting for MN-CSE (up to 60 s)..."
DEADLINE=$(( $(date +%s) + 60 ))
until curl -sf "http://localhost:8082/id-mn-butler" \
    -H "Accept: application/json" \
    -H "X-M2M-Origin: CAdmin" \
    -H "X-M2M-RI: healthcheck" \
    -H "X-M2M-RVI: 3" > /dev/null 2>&1; do
    if [ "$(date +%s)" -ge "$DEADLINE" ]; then
        echo "[butler] ERROR: MN-CSE did not respond in 60 s."
        echo "         If the database is corrupt, run:  bash scripts/reset.sh"
        exit 1
    fi
    sleep 1
done
echo "[butler] MN-CSE ready."

# ── Local AE + containers ─────────────────────────────────────────────────────
# Re-run setup if resources file is missing, lacks announce_ri (old format),
# OR the butler AE no longer exists in ACME (database was wiped independently).
_butler_ok=false
if $PYTHON - <<'EOF' 2>/dev/null; then
import json, sys
d = json.load(open("butler_resources.json"))
sys.exit(0 if d.get("announce_ri") else 1)
EOF
    if curl -sf "http://localhost:8082/cse-mn-butler/SmartButler" \
        -H "Accept: application/json" \
        -H "X-M2M-Origin: CAdmin" \
        -H "X-M2M-RI: check" \
        -H "X-M2M-RVI: 3" > /dev/null 2>&1; then
        _butler_ok=true
    fi
fi

if [ "$_butler_ok" = "true" ]; then
    echo "[butler] butler_resources.json OK and AE confirmed in ACME — skipping setup."
else
    echo "[butler] Running butler_setup.py (AE + open-inbox ACP + announcement container)..."
    $PYTHON src/butler_setup.py
fi

# ── Notifier ──────────────────────────────────────────────────────────────────
echo "[butler] Starting notifier (dashboard + /add_flower endpoint)..."
$PYTHON src/notifier.py &
NOTIFIER_PID=$!

echo "[butler] Waiting for notifier to be ready..."
for i in $(seq 1 20); do
    sleep 0.5
    curl -sf "http://localhost:5000/data" > /dev/null 2>&1 && break
done
echo "[butler] Notifier ready (pid $NOTIFIER_PID)."

# ── Discovery / announcement server ──────────────────────────────────────────
# Advertises butler on mDNS (role=butler, announce-path).
# Flowers POST a oneM2M CIN to the announcement container; ACME fires a SUB
# to /flower-announced (port 5001) which triggers SAREF fetch + subscriptions.
echo "[butler] Starting discovery server (Ctrl+C to stop everything)..."
$PYTHON src/discovery.py
