"""
Simulates soil drying, pump activation, and water bottle depletion.

Pump behaviour:
  - Pump stays ON for PUMP_DURATION ticks per activation
  - Soil rises gradually over those ticks (not an instant jump)
  - Water decreases steadily across the pump ticks
  - First tick after pump fires shows LOW soil + pump=1 so the
    dashboard correctly reflects the cause and effect

Cycle per activation:
  tick 0:  soil=LOW,  pump=1  (pump just fired, soil still dry)
  tick 1…N-2: soil rising, pump=1
  tick N-1: soil=MOISTURE_AFTER_PUMP, pump=0  (done)
"""

import json
import random
import time
import uuid

import requests
from config import (
    MN_CSE_HOST, AE_ORIGINATOR, RVI,
    TICK_INTERVAL, REFILL_WAIT,
    MOISTURE_DROP, MOISTURE_THRESHOLD, MOISTURE_AFTER_PUMP, MOISTURE_START,
    WATER_START, WATER_PER_PUMP, WATER_EMPTY, MAX_PUMP_CYCLES,
    PUMP_DURATION,
)

with open("resources.json") as f:
    _res = json.load(f)
CONTAINER_RI = _res["containers"]

print(f"[sim] Loaded container RIs:")
for name, ri in CONTAINER_RI.items():
    print(f"[sim]   {name} → {ri}")

# Derived per-tick deltas spread over the full pump duration
_water_per_tick = WATER_PER_PUMP / PUMP_DURATION
_rise_per_tick  = (MOISTURE_AFTER_PUMP - MOISTURE_THRESHOLD) / PUMP_DURATION
print(f"[sim] water_per_tick={_water_per_tick:.2f}%  rise_per_tick={_rise_per_tick:.2f}%")


def _headers() -> dict:
    # ty=4 in Content-Type tells ACME this is a contentInstance resource creation
    return {
        "X-M2M-Origin": AE_ORIGINATOR,
        "X-M2M-RI":     str(uuid.uuid4()),
        "X-M2M-RVI":    RVI,
        "Content-Type": "application/json;ty=4",
        "Accept":       "application/json",
    }


def post_reading(container: str, value: float):
    """Look up the container RI and POST a new CIN to the MN-CSE."""
    ri   = CONTAINER_RI[container]
    url  = f"{MN_CSE_HOST}/{ri}"
    body = {"m2m:cin": {"con": str(round(value, 1)), "cnf": "text/plain:0"}}
    print(f"[sim] POST {container}={round(value, 1)}  url={url}")
    try:
        r = requests.post(url, json=body, headers=_headers(), timeout=5)
        if r.status_code in (200, 201):
            cin_ri = r.json().get("m2m:cin", {}).get("ri", "?")
            print(f"[sim]    {r.status_code}  CIN ri={cin_ri}")
        else:
            print(f"[sim]   X {r.status_code}  body={r.text[:200]}")
    except requests.RequestException as e:
        print(f"[sim]   X request error: {e}")


def run():
    soil            = MOISTURE_START
    water           = WATER_START
    pump_on         = False
    pump_ticks_left = 0    # > 0 while pump is active
    pumps           = 0
    cycle           = 1
    tick            = 0

    print(f"\n[sim] ════════════════════════════════════════════════════")
    print(f"[sim] Starting simulation — cycle={cycle}")
    print(f"[sim]   soil_start={soil}%  water_start={water}%")
    print(f"[sim]   moisture_threshold={MOISTURE_THRESHOLD}%  moisture_after_pump={MOISTURE_AFTER_PUMP}%")
    print(f"[sim]   water_per_pump={WATER_PER_PUMP}%  max_pump_cycles={MAX_PUMP_CYCLES}")
    print(f"[sim]   pump_duration={PUMP_DURATION} ticks  tick_interval={TICK_INTERVAL}s")
    print(f"[sim] ════════════════════════════════════════════════════\n")

    while True:
        tick += 1
        noise = random.uniform(-1.5, 1.5)

        print(f"\n[sim] ── tick #{tick:04d}  cycle={cycle}  pumps={pumps}/{MAX_PUMP_CYCLES} ──────────────")

        # ── Empty bottle ──────────────────────────────────────────────────────
        if water <= WATER_EMPTY:
            pump_on         = False
            pump_ticks_left = 0
            print(f"[sim] BOTTLE EMPTY — posting critical readings and waiting {REFILL_WAIT}s for refill")
            post_reading("pump_status",   0)
            post_reading("soil_moisture", round(soil + noise, 1))
            post_reading("water_level",   WATER_EMPTY)
            print(f"[sim] Sleeping {REFILL_WAIT}s (simulating user refilling the bottle)...")
            time.sleep(REFILL_WAIT)
            water  = WATER_START
            pumps  = 0
            cycle += 1
            tick   = 0
            print(f"[sim] Refill complete — starting cycle {cycle}  water reset to {water}%")
            continue

        # ── Pump is running ───────────────────────────────────────────────────
        if pump_ticks_left > 0:
            pump_ticks_left -= 1
            water = max(WATER_EMPTY, water - _water_per_tick)
            print(f"[sim] Pump active — ticks_left={pump_ticks_left}  water draining → {water:.1f}%")

            if pump_ticks_left == 0:
                soil    = MOISTURE_AFTER_PUMP
                pump_on = False
                print(f"[sim] Pump cycle complete — soil reached {soil}%  pump OFF")
            else:
                soil    = min(MOISTURE_AFTER_PUMP, soil + _rise_per_tick + noise * 0.3)
                pump_on = True
                print(f"[sim] Soil rising → {soil:.1f}%  (target={MOISTURE_AFTER_PUMP}%)")

        # ── Drying ────────────────────────────────────────────────────────────
        else:
            pump_on = False
            old_soil = soil
            soil    = max(0.0, soil - MOISTURE_DROP + noise)
            print(f"[sim] Drying: soil {old_soil:.1f}% → {soil:.1f}%  (drop={MOISTURE_DROP}%  noise={noise:+.1f}%)")

            if soil < MOISTURE_THRESHOLD and pumps < MAX_PUMP_CYCLES:
                pump_on         = True
                pump_ticks_left = PUMP_DURATION
                pumps          += 1
                print(
                    f"[sim] PUMP TRIGGERED #{pumps}: soil={soil:.1f}% < threshold={MOISTURE_THRESHOLD}%"
                    f"  water={water:.1f}%  duration={PUMP_DURATION} ticks"
                )
            elif soil < MOISTURE_THRESHOLD and pumps >= MAX_PUMP_CYCLES:
                print(
                    f"[sim] Soil dry ({soil:.1f}%) but MAX_PUMP_CYCLES={MAX_PUMP_CYCLES} reached "
                    f"— forcing bottle empty"
                )
                water = WATER_EMPTY

        # ── Post to oneM2M ───────────────────────────────────────────────────
        pump_val  = 1 if pump_on else 0
        soil_val  = round(soil + noise, 1)
        water_val = round(water, 1)

        print(
            f"[sim] State: soil={soil:.1f}%  water={water:.1f}%  "
            f"pump={'ON' if pump_on else 'off'}  "
            f"[{pumps}/{MAX_PUMP_CYCLES} pump cycles]"
        )
        print(f"[sim] Posting to oneM2M (pump_status first, then soil, then water)...")
        post_reading("pump_status",   pump_val)
        post_reading("soil_moisture", soil_val)
        post_reading("water_level",   water_val)
        print(f"[sim] Sleeping {TICK_INTERVAL}s until next tick...")

        time.sleep(TICK_INTERVAL)


if __name__ == "__main__":
    run()
