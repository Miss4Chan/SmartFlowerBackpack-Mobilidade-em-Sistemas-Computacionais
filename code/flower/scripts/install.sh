#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."

# ── Guard ─────────────────────────────────────────────────────────────────────
if [ ! -f "requirements.txt" ]; then
    echo "[install] Must be run from the flower/ directory: bash scripts/install.sh"
    exit 1
fi

# ── Load FLOWER_ID ────────────────────────────────────────────────────────────
if [ -f .env ]; then
    set -o allexport
    source .env
    set +o allexport
fi

if [ -z "$FLOWER_ID" ]; then
    echo "[install] ERROR: FLOWER_ID is not set."
    echo "         Copy .env.example to .env and set FLOWER_ID=1 or FLOWER_ID=2"
    exit 1
fi

VENV="$(pwd)/.venv"
ACME_DIR="$(pwd)/acme-cse-${FLOWER_ID}"   # lives inside the project, per flower

# ── Python ────────────────────────────────────────────────────────────────────
find_python() {
    for cmd in python3 python py; do
        if command -v "$cmd" &>/dev/null; then
            version=$("$cmd" --version 2>&1) || continue
            echo "$version" | grep -q "Python 3" && { echo "$cmd"; return 0; }
        fi
    done
    return 1
}

if ! SYS_PYTHON=$(find_python); then
    echo "[install] Python 3 not found."
    echo "  Windows: install from https://python.org (check 'Add to PATH')"
    echo "  Linux:   sudo apt install python3 python3-venv"
    exit 1
fi

echo "[install] Using system Python: $($SYS_PYTHON --version)"

# ── Venv ──────────────────────────────────────────────────────────────────────
if [ ! -d "$VENV" ]; then
    echo "[install] Creating virtualenv at $VENV..."
    $SYS_PYTHON -m venv "$VENV"
else
    echo "[install] Virtualenv already exists at $VENV — skipping creation."
fi

if   [ -f "$VENV/Scripts/python.exe" ]; then VPYTHON="$VENV/Scripts/python.exe"
elif [ -f "$VENV/Scripts/python" ];     then VPYTHON="$VENV/Scripts/python"
elif [ -f "$VENV/bin/python" ];         then VPYTHON="$VENV/bin/python"
else echo "[install] Python not found in venv."; exit 1; fi

# ── Dependencies ──────────────────────────────────────────────────────────────
echo "[install] Installing shared flower dependencies..."
"$VPYTHON" -m pip install --upgrade pip -q
"$VPYTHON" -m pip install -r requirements.txt

if [ "$(uname)" = "Linux" ]; then
    echo "[install] Installing hardware dependencies (RPi.GPIO, spidev, smbus2)..."
    "$VPYTHON" -m pip install -r core/requirements.txt
else
    echo "[install] Skipping hardware dependencies (not Linux)."
fi

# ── ACME directory ────────────────────────────────────────────────────────────
if [ -d "$ACME_DIR" ]; then
    echo "[install] ACME directory already exists at $ACME_DIR — skipping."
else
    echo "[install] Creating ACME directory at $ACME_DIR..."
    mkdir -p "$ACME_DIR"
fi

echo "[install] Copying flower ACME config..."
cp config/acme.ini "$ACME_DIR/acme.ini"

echo ""
if [ "$(uname)" = "Linux" ]; then
    echo "[install] Done. Set FLOWER_ID in .env then run: bash scripts/start_rpi.sh"
else
    echo "[install] Done. Set FLOWER_ID in .env then run: bash scripts/start_sim.sh"
fi