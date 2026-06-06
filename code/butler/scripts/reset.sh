#!/usr/bin/env bash
# Wipe the butler's ACME database and butler_resources.json.
# Run this only when ACME is genuinely corrupt and won't start.
# Normal restarts should NOT need this.
#
# Usage: bash scripts/reset.sh   (from code/butler/)
set -e
cd "$(dirname "$0")/.."

ACME_DIR="$(pwd)/acme-cse"   # code/butler/acme-cse — matches start.sh

echo "[reset] Stopping any running ACME process..."
pkill -f "acmecse" 2>/dev/null || true
sleep 1

echo "[reset] Wiping ACME database (keeping acme.ini)..."
find "$ACME_DIR" -type f ! -name "acme.ini" -delete 2>/dev/null || true
find "$ACME_DIR" -mindepth 1 -type d -exec rm -rf {} + 2>/dev/null || true

echo "[reset] Removing butler_resources.json..."
rm -f butler_resources.json

echo "[reset] Done. Run bash scripts/start.sh to restart."
