#!/usr/bin/env bash
# Wipe the flower's ACME database and resources.json.
# Run this only when ACME is genuinely corrupt and won't start.
# Normal restarts should NOT need this.
#
# Usage: bash scripts/reset.sh   (from code/flower/)
set -e
cd "$(dirname "$0")/.."

echo "[reset] Stopping any running ACME process..."
pkill -f "acmecse" 2>/dev/null || true
sleep 1

echo "[reset] Wiping per-flower ACME databases (keeping acme.ini in each)..."
for dir in "$(pwd)"/acme-cse-*; do
    [ -d "$dir" ] || continue
    echo "[reset]   wiping $dir"
    find "$dir" -type f ! -name "acme.ini" -delete 2>/dev/null || true
    find "$dir" -mindepth 1 -type d -exec rm -rf {} + 2>/dev/null || true
done

echo "[reset] Removing resources.json..."
rm -f sim/resources.json
rm -f core/resources.json

echo "[reset] Done. Run  bash scripts/start_rpi.sh  (RPi hardware)"
echo "         or   bash scripts/start_sim.sh  (PC simulator)  to restart."
