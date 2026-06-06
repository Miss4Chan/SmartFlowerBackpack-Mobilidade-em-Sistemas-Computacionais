#!/usr/bin/env bash
# One-time setup for the butler laptop.
# Creates the project venv, installs dependencies, and prepares the ACME directory.
# Usage: bash scripts/install.sh  (from code/butler/)
set -e
cd "$(dirname "$0")/.."

VENV="$(cd ../.. && pwd)/.venv"
ACME_DIR="$(pwd)/acme-cse"

# ── Python ────────────────────────────────────────────────────────────────────
if   [ -f "$VENV/Scripts/python.exe" ]; then SYS_PYTHON="$VENV/Scripts/python.exe"
elif command -v python3 &>/dev/null;    then SYS_PYTHON="python3"
elif command -v python  &>/dev/null;    then SYS_PYTHON="python"
else echo "[install] Python not found."; exit 1; fi

# Use system python to create the venv if it doesn't exist yet
if ! command -v "$VENV/Scripts/python.exe" &>/dev/null && ! command -v "$VENV/bin/python" &>/dev/null; then
    if   command -v python3 &>/dev/null; then SYS_PYTHON="python3"
    elif command -v python  &>/dev/null; then SYS_PYTHON="python"
    fi
fi
echo "[install] Using Python: $($SYS_PYTHON --version 2>&1)"

# ── Venv ──────────────────────────────────────────────────────────────────────
if [ ! -d "$VENV" ]; then
    echo "[install] Creating virtualenv at $VENV..."
    $SYS_PYTHON -m venv "$VENV"
else
    echo "[install] Virtualenv already exists at $VENV — skipping creation."
fi

# Resolve the venv Python (use python -m pip to avoid Windows pip.exe restriction)
if   [ -f "$VENV/Scripts/python.exe" ]; then VPYTHON="$VENV/Scripts/python.exe"
elif [ -f "$VENV/Scripts/python" ];     then VPYTHON="$VENV/Scripts/python"
elif [ -f "$VENV/bin/python" ];         then VPYTHON="$VENV/bin/python"
else VPYTHON="python"; fi

# ── Dependencies ──────────────────────────────────────────────────────────────
echo "[install] Installing butler dependencies..."
"$VPYTHON" -m pip install --upgrade pip -q
"$VPYTHON" -m pip install -r requirements.txt

# ── ACME directory ────────────────────────────────────────────────────────────
if [ -d "$ACME_DIR" ]; then
    echo "[install] ACME directory already exists at $ACME_DIR — skipping."
else
    echo "[install] Creating ACME directory at $ACME_DIR..."
    mkdir -p "$ACME_DIR"
fi

echo "[install] Copying butler ACME config..."
cp config/acme.ini "$ACME_DIR/acme.ini"

echo ""
echo "[install] Done. Run: bash scripts/start.sh"
