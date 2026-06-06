"""
SmartFlower main loop — entry point on RPi boot.

Activity flow (docs/flower-logic-flow.drawio):
  boot → setup (retry on fail) → main_loop + side_loop running in parallel

Main loop  (every MAIN_LOOP_INTERVAL s):
  read soil + water → apply pump decision → post all three readings to MN-CSE

Side loop  (every SIDE_LOOP_INTERVAL s):
  read water → force pump off if critical → post heartbeat every 2 min

Posting order is always: pump_status first, then soil_moisture, then water_level.
This matches the simulator convention so the butler sees the pump decision
before interpreting the sensor values (cause before effect).
"""

import datetime
import sys
import threading
import time

import actuators
import cse_client
import sensors
import setup as _setup
from config import (
    HEARTBEAT_INTERVAL,
    MAIN_LOOP_INTERVAL,
    MOISTURE_THRESHOLD,
    PUMP_MAX_ON_TIME,
    SETUP_RETRY_DELAY,
    SIDE_LOOP_INTERVAL,
    WATER_CRITICAL_THRESHOLD,
    WATER_WARNING_THRESHOLD,
)

_pump_lock  = threading.Lock()  # prevents main loop and side loop to run at the same time
_pump_on    = False             # pump starts as off
_pump_timer: threading.Timer | None = None  # safety mechanism that turns off the pump after some time (PUMP_MAX_ON_TIME)

"""
_start_pump() modifies the module-level variables pump_on and _pump_timer,
aqcuires the _pump_locko at the begining of the block, disabling other functions to enter
the same _pump_lock, when it finishes, releases pump lock.
Within the loop. if the pump is on, it returns its early. Turns actuators on,
creates pump timer that will call _cutoff_pump after PUMP_MAX_ON_TIME.
"""
def _start_pump() -> None:
    global _pump_on, _pump_timer    # declares modification of module level variables for (_pump_on and _pump_timer)
    with _pump_lock:        # acquires _pump_lock stoping other functions to enter their own _pump_lock
        if _pump_on:        # early returns if _pump_on is true
            print("[pump] already ON — ignoring start request")
            return
        actuators.on()      # turns actuators on
        _pump_on = True     # sets _pump_on to True
        _pump_timer = threading.Timer(PUMP_MAX_ON_TIME, _cutoff_pump) # creates pump timer that will call _cutoff_pump after PUMP_MAX_ON_TIME
        _pump_timer.daemon = True   # marks timer thread as a daemon thread, which will be auto killed when program exits
        _pump_timer.start()         # starts_pump_timer
        print(f"[pump] ▶ ON  (safety cutoff in {PUMP_MAX_ON_TIME}s)")

def _stop_pump() -> None:
    """
    stop_pump() modifies module level variables pump_on and _pump_timer,
    acquires the _pump_lock at the begining of the block, disablid other functions to enter
    the same _pump_lock, when it finishes, releases the _pump_lock.
    within the with block, iif the pump is is off, it early returns.
    cancels and clears _pump_timer if one is running
    turns actuator off, sets _pump_on to false
    """

    global _pump_on, _pump_timer    # declares modification of module level variables for (_pump_on and _pump_timer)
    with _pump_lock:                # acquires _pump_lock stoping other functions to enter their own _pump_lock
        if not _pump_on:            # early returns if _pump_on is fakse
            return
        if _pump_timer:             # if pump timer is running, it is canceled
            _pump_timer.cancel()
            _pump_timer = None      # clearing the reference for pump timer
        actuators.off()             # actuator for the pump turned off
        _pump_on = False            # pump on reset to defualt state false
        print("[pump] OFF (manual stop)")

def _cutoff_pump() -> None:
    """
    _cutoff_pump() modifies module level variables pump_on and _pump_timer,
    safety feature that fires from Timer thread when pump runs longer than PUMP_MAX_ON_TIME.
    acquires the _pump_lock at the begining of the block, disabling other functions to enter the same _pump_lock
    and when it finishes, it releases the _pump_lock. turns off actuators, sets _pump_on to false, clears the timer reference,
    and posts the state of the pump so the butlerdashboard reflets the cuttoff
    """

    global _pump_on, _pump_timer    # declares modification of mudule level variables for (_pump_on, _pump_timer)
    with _pump_lock:                # acquires the _pump_lock, stopping other functions to enter their own _pump_lock
        actuators.off()             # turns off actuators
        _pump_on = False            # pump on reset to defualt state false
        _pump_timer = None          # clearing the reference for pump timer
        print(f"[pump] OFF (safety cutoff after {PUMP_MAX_ON_TIME}s)")
    cse_client.post("pump_status", 0)   # Post the updated pump state cse containers so it reflects the cutoff immediately.
    print("[pump] cutoff: pump_status=0 posted to CSE")

def _post(pump: bool, soil: float, water: float) -> None:
    """
    post() accepts variables pump, soil, water, sends the data over via POST to the cse (oneM2M) containers
    """
    
    pump_val  = 1 if pump else 0    # pump on (1), or pump off (0). converts bool to int for oneM2M contentInstance
    soil_val  = round(soil, 1)      # rounds the raw soil value to 1 decimal
    water_val = round(water, 1)     # rounds the raw water value to 1 decimal

    print(f"[post] pump_status={pump_val}  soil_moisture={soil_val}%  water_level={water_val}%")
    cse_client.post("pump_status",   pump_val)  # Post the updated pump  stuatus value to the cse (oneM2M) containers
    cse_client.post("soil_moisture", soil_val)  # Post the updated soil moisture value to the cse (oneM2M) containers
    cse_client.post("water_level",   water_val) # Post the updated water level value to the cse (oneM2M) containers

def main_loop() -> None:
    """
    main_loop() controls pump and posts content isntances to their  respective oneM2M cse containers
    and controls the fixed interval (MAIN_LOOP_INTERVAL)
    """
     
    print("[main] ── loop started ──────────────────────────────────────")
    iteration = 0       # for debugging. counts all iterations
    while True:         
        try:
            iteration += 1                          # adds 1
            soil  = sensors.read_soil_moisture()    # calls read_soil_moisture() to get soil moisture
            water = sensors.read_water_level()      # calls read_water_level() to get water level

            print(f"[main] #{iteration:04d}  soil={soil}%  water={water}%")

            if water <= WATER_CRITICAL_THRESHOLD:   # if water is <= critical thershold, calls _stop_pump, sets pump to false 
                print(f"[main] CRITICAL: water={water}% ≤ threshold={WATER_CRITICAL_THRESHOLD}% → pump BLOCKED")
                _stop_pump()    # calls _stop_pump()
                pump = False    # pump set to false
            elif soil < MOISTURE_THRESHOLD: # else if soil is < moisture threshold, calls _start_pump() and sets pump to true
                print(f"[main] DRY: soil={soil}% < threshold={MOISTURE_THRESHOLD}% → pump ON")
                _start_pump()   # calls _start_pump()
                pump = True     # sets pump to true
            else:               # handles the normal healthy state when water is above critical and soil moisture is at or above the moisture threshold.
                print(f"[main] OK: soil={soil}% ≥ threshold={MOISTURE_THRESHOLD}% → pump off")
                _stop_pump()    # calls _stop_pump()
                pump = False    # sets pump to false

            if WATER_CRITICAL_THRESHOLD < water <= WATER_WARNING_THRESHOLD: # water is above critical but at or below warning threshold — bottle is getting low
                print(f"[main] WARNING: water={water}% — getting low (warning threshold={WATER_WARNING_THRESHOLD}%)")

            _post(pump, soil, water)    # POSTS all content instantes to theri respective oneM2M cse containers

        except Exception as e:
            print(f"[main] ERROR in iteration #{iteration}: {e}")

        time.sleep(MAIN_LOOP_INTERVAL)  # sleeps for MAIN_LOOP_INTERVAL. act as refresh rate.


def side_loop() -> None:
    """
    Monitors water level independently of the main loop.
    Forces pump off and posts the critical level when the bottle is empty -
    the butler receives it via the oneM2M self-subscription and sends a Telegram alert.
    """

    print("[side] ── loop started ──────────────────────────────────────")
    last_hb = 0.0   # tracks the timestamp of the last hearbeat POST. initializard as 0 so the first heartbeat fires imedeatly

    while True:
        try:
            water = sensors.read_water_level()      # reads current water level from sensor
            print(f"[side] water check: {water}%")

            if water <= WATER_CRITICAL_THRESHOLD:   # if water level is at or below threshold, it calls stop_pump, sets water_val to water rounded to 1 decimal point, posts water val to cse client
                print(f"[side] CRITICAL: water={water}% ≤ {WATER_CRITICAL_THRESHOLD}% → forcing pump off and posting")
                _stop_pump()                        # calls stop_pump
                water_val = round(water, 1)         # water rounded to 1 decimal
                cse_client.post("water_level", water_val)   # posts water_val content instance to respective oneM2M cse container
                print(f"[side] posted water_level={water_val} (critical)")

            now = time.time()
            if now - last_hb >= HEARTBEAT_INTERVAL:     # if time - last heartbeat is at or higher than the heartbeat interval 
                ts = datetime.datetime.now().isoformat(timespec="seconds")
                cse_client.post("heartbeat", ts)        # posts ts content instance to respective oneM2M cse container
                last_hb = now                           # last heartbeat is set right after posting
                print(f"[side] heartbeat posted → {ts}")

        except Exception as e:
            print(f"[side] ERROR: {e}")

        time.sleep(SIDE_LOOP_INTERVAL)  # sleeps for SIDE_LOOP_INTEVAL. act as refresh rate for.

def boot() -> None:
    """
    boot()  
    """

    print("[boot] ════════════════════════════════════════════════════")
    print("[boot] SmartFlower starting...")
    print("[boot] ════════════════════════════════════════════════════")

    while True:
        print("[boot] Running setup (hardware probe + CSE resource creation)...")
        if _setup.run():
            print("[boot] setup OK")
            break
        print(f"[boot] setup failed — retrying in {SETUP_RETRY_DELAY}s")
        time.sleep(SETUP_RETRY_DELAY)

    print("[boot] Initialising sensors...")
    sensors.init()
    print("[boot] Initialising actuators...")
    actuators.init()
    print("[boot] Loading CSE resource map from resources.json...")
    cse_client.load()

    print("[boot] Spawning side-loop thread...")
    t = threading.Thread(target=side_loop, daemon=True, name="side-loop")
    t.start()

    print("[boot] Entering main loop (foreground)...")
    try:
        main_loop()
    except KeyboardInterrupt:
        print("[boot] Interrupted by user")
    finally:
        print("[boot] Shutting down — turning pump off and cleaning up...")
        _stop_pump()
        sensors.close()
        actuators.cleanup()
        print("[boot] Goodbye.")
        sys.exit(0)


if __name__ == "__main__":  # entry point - runs boot() only when main is executed direc6tly
    boot()
