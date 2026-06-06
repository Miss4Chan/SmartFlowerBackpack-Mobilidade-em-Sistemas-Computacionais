# Flower

The flower is the sensor node of the SmartFlower system. It runs on a Raspberry Pi (real hardware) or on any machine (simulator). It reads soil moisture and water level, controls a water pump, and publishes all readings to a local ACME MN-CSE. The butler discovers the flower, subscribes to its readings, and handles alerting and the dashboard.

---

## What it does

- Runs an ACME MN-CSE (`id-mn-flower-<N>`) on port `8080 + FLOWER_ID`
- Registers an Application Entity and creates four containers: `pump_status`, `soil_moisture`, `water_level`, `heartbeat`
- Stores a SAREF semantic descriptor so the butler can learn the flower's container layout and alert thresholds without any hardcoded configuration
- Advertises itself on mDNS (`_onem2m._tcp.local.`, `role=publisher`) and browses for the butler (`role=subscriber`); registers with the butler automatically on discovery
- **Main loop** (every 2 s): reads sensors, makes a pump decision, posts all three readings to ACME
- **Side loop** (every 10 s): independent water level safety check; posts a heartbeat timestamp every 120 s

---

## Setup

### First-time

```bash
# from code/flower/
cp .env.example .env          # set FLOWER_ID=1  (use 2 for a second flower)
bash scripts/install.sh       # create venv, install deps, prepare acme-cse-<N>/
```

`FLOWER_ID` must be unique per flower/RPi. It determines the CSE identity and ACME port (`8080 + FLOWER_ID`).

---

## Running

### Simulator (any machine, no hardware)

```bash
bash scripts/start_sim.sh
```

### Hardware — manual (RPi)

```bash
bash scripts/start_rpi.sh
```

### Hardware — systemd service (RPi, auto-starts on boot)

```bash
sudo bash service/install.sh       # install once
sudo systemctl start smartflower   # start immediately
```

---

## Monitoring (RPi)

```bash
# Live logs
journalctl -u smartflower.service -f

# Service status
sudo systemctl status smartflower

# ACME health check  (replace N with FLOWER_ID)
curl -sf http://localhost:808N/cse-mn-flower-N \
  -H "Accept: application/json" \
  -H "X-M2M-Origin: CAdmin" \
  -H "X-M2M-RI: healthcheck" \
  -H "X-M2M-RVI: 3"
```

---

## Service management (RPi)

```bash
sudo systemctl restart smartflower    # restart after config change
sudo systemctl stop smartflower       # stop (pump turned off via cleanup)
sudo systemctl enable smartflower     # auto-start on boot (done by install.sh)
sudo systemctl disable smartflower    # remove auto-start
```

---

## Reset (corrupt ACME database only)

```bash
sudo systemctl stop smartflower       # if running as a service
bash scripts/reset.sh                 # wipe ACME data + resources.json
```

Only use when ACME refuses to start. Normal restarts never need it.

---

## ACME Configuration

Each flower runs its own MN-CSE on port **`8080 + FLOWER_ID`** (flower-1 → 8081, flower-2 → 8082, …). The config template is at `config/acme.ini`. The start scripts patch `cseID`, `cseName`, `cseHost`, and `httpPort` automatically on every boot — do not edit these fields manually.

| Setting | Value | Notes |
|---|---|---|
| `FLOWER_ID` | set in `.env` | Must be unique per flower/RPi |
| `httpPort` | `8080 + FLOWER_ID` | Set automatically; change only if there is a port conflict |
| `logLevel` | `debug` | Switch to `info` if output is too noisy |
| `cseHost` | auto-patched | Set from LAN IP on every start |

`RemoteCSEManager` and `AnnouncementManager` are disabled — they require a registrar IN-CSE that does not exist here and flood the log with `csrMonitor` errors every ~14 s if left enabled.

All resource data is stored in `acme-cse-<N>/data/` (TinyDB). Run `reset.sh` to wipe it.

---

## Python Modules

### core/ — real hardware (RPi only)

**core/main.py** — entry point. Runs two parallel loops after setup completes:
- **Main loop** (2 s): reads soil moisture (MCP3008, SPI, channel 7) and water level (I2C capacitive sensor, 0x77/0x78). Pump decision: water ≤ 10% → pump blocked regardless of soil; soil < 20% → pump on with 5 s safety cutoff; otherwise off. Posts `pump_status → soil_moisture → water_level` in that order so the butler sees the actuator state before the sensor values.
- **Side loop** (10 s, daemon thread): independent water level check — forces pump off and posts immediately if critical. Also posts a heartbeat ISO timestamp to the `heartbeat` container every 120 s.

**core/config.py** — all hardware constants: GPIO pump pin (22), SPI bus/device/channel/speed, I2C bus/addresses, soil calibration values (`SOIL_DRY_RAW=0`, `SOIL_WET_RAW=550`), thresholds (`MOISTURE_THRESHOLD=20%`, `WATER_CRITICAL_THRESHOLD=10%`, `WATER_WARNING_THRESHOLD=30%`), loop timing, mDNS service type.

**core/saref_desc.py** — creates the flower's SAREF JSON-LD self-description as an `m2m:smd` (ty=24) resource on the local MN-CSE. Called once during setup. The butler retrieves it with a standard oneM2M GET — no custom HTTP server needed.

**core/sensors.py** — reads soil moisture from the MCP3008 ADC over SPI and water level from the 20-pad I2C capacitive sensor.

**core/actuators.py** — controls the HW-307 relay on GPIO 22.

**core/cse_client.py** — thin HTTP wrapper that posts `contentInstance` resources to the local ACME containers.

**core/setup.py** — creates the AE, per-container ACPs, and containers on the local MN-CSE. Runs with retry on boot until ACME is ready.

### sim/ — simulator (any machine)

**sim/simulator.py** — simulates the full plant watering lifecycle. Soil dries by `MOISTURE_DROP` per tick; pump fires when dry and stays on for `PUMP_DURATION` ticks; water decreases per pump tick; simulation waits `REFILL_WAIT` seconds when the bottle empties. Posts to ACME on every tick exactly as the real hardware does.

**sim/config.py** — simulator timing and thresholds (`TICK_INTERVAL=2 s`, `MOISTURE_DROP=22%/tick`, `MOISTURE_THRESHOLD=30%`, `WATER_CRITICAL_THRESHOLD=10%`). ACME port derived from `FLOWER_ID` (`8080 + FLOWER_ID`).

**sim/setup_resources.py** — creates the AE, ACPs, and containers for the simulator path. Equivalent to `core/setup.py`.

### tools/ — shared utilities

**tools/advertise.py** — registers the flower on mDNS (`_onem2m._tcp.local.`) with TXT record properties `role=publisher`, `cse-id`, `cse-name`, `ae-id`, `flower-name`. Runs in the background; unregisters cleanly on SIGINT/SIGTERM.

**tools/discover_butler.py** — browses mDNS for the butler (`role=subscriber`). When found, reads the butler's `registration-port` from its TXT record and POSTs the flower's connection details and SAREF path to `/register_flower`. Re-registers automatically if the butler disappears and reappears.
