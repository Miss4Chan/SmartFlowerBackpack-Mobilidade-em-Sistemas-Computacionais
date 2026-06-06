# Butler

The butler is the server-side node of the SmartFlower system. It runs on a laptop or a second Raspberry Pi and is responsible for discovering flowers on the local network, mirroring their sensor data, evaluating alert thresholds, serving the live web dashboard, and sending Telegram alerts when a plant needs attention.

---

## What it does

- Advertises itself on mDNS (`_onem2m._tcp.local.`, `role=subscriber`) so flowers can find it
- When a flower registers, fetches its SAREF self-description to learn container names and alert thresholds
- Creates mirror containers on the butler's own MN-CSE (`id-mn-butler`, port 8082)
- Creates syntactical subscriptions on each flower container — the flower's ACME fires a notification to the butler on every new sensor reading
- Creates self-subscriptions on each mirror container — the butler's own ACME fires back to the notifier for threshold evaluation
- Serves a live web dashboard over SSE on port 5000
- Sends Telegram alerts when a sensor value crosses a threshold; repeats every 30 min while active and clears automatically on recovery

---

## Setup

### First-time

```bash
# from code/butler/
bash scripts/install.sh
```

Creates the venv at `SmartFlowerBackpack/.venv`, installs `requirements.txt`, creates `acme-cse/` and copies the ACME config template in.

### Environment file

```bash
cp .env.example .env
# fill in:
# TELEGRAM_BOT_TOKEN=...
# TELEGRAM_CHAT_ID=...
```

---

## Running

```bash
# from code/butler/
bash scripts/start.sh
```

Startup sequence:
1. Auto-detects the machine's LAN IP and patches `cseHost` into `acme-cse/acme.ini`
2. Starts ACME MN-CSE (`id-mn-butler`) on port 8082; waits up to 60 s
3. Runs `src/butler_setup.py` to create the butler's AE if `butler_resources.json` is missing
4. Starts `src/notifier.py` in the background (dashboard + Telegram alerts on port 5000)
5. Runs `src/discovery.py` in the foreground — advertises the butler on mDNS and listens for flower registrations on port 5001

Ctrl+C shuts everything down cleanly.

> **Boot order**: start the butler before the flowers so its mDNS record is visible when flowers come online. If the butler starts after a flower, the flower detects it as soon as the mDNS record appears and registers automatically.

---

## Reset (corrupt ACME database only)

```bash
bash scripts/reset.sh
```

Kills ACME, wipes `acme-cse/data/` and `butler_resources.json`. Only needed when ACME refuses to start. After reset, run `start.sh` — everything is recreated automatically.

---

## ACME Configuration

The butler's ACME MN-CSE (`id-mn-butler`) runs on port **8082**. The config template is at `config/acme.ini`. `start.sh` patches `cseHost` automatically on every boot — do not edit it manually.

| Setting | Default | Notes |
|---|---|---|
| `httpPort` | `8082` | Change only if there is a port conflict |
| `logLevel` | `debug` | Switch to `info` if output is too noisy |
| `cseHost` | auto-patched | Set from LAN IP on every start |

`RemoteCSEManager` and `AnnouncementManager` are disabled in the plugin config — they require a registrar IN-CSE that does not exist in this setup and flood the log with `csrMonitor` errors every ~14 s if left enabled.

All resource data is stored in `acme-cse/data/` (TinyDB). Run `reset.sh` to wipe it.

---

## Python Modules (`src/`)

### config.py
Central config imported by all modules.

| Name | Value |
|---|---|
| `BUTLER_CSE_HOST` | `http://localhost:8082` |
| `BUTLER_CSE_ID` | `id-mn-butler` |
| `BUTLER_CSE_NAME` | `cse-mn-butler` |
| `BUTLER_AE_NAME` | `SmartButler` |
| `NOTIFIER_PORT` | `5000` |
| `ALERT_REPEAT_INTERVAL_S` | `1800` (30 min) |
| `BUTLER_HOST` | auto-detected at import via UDP socket |

`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are loaded from `.env`. There is no hardcoded container list or alert threshold — those are learned from each flower's SAREF descriptor at discovery time.

### butler_setup.py
Creates the butler's AE (`SmartButler`) on the local MN-CSE and writes `butler_resources.json`. Mirror containers are not created here — `discovery.py` creates them dynamically per flower when it registers. Also exports `ensure_flower_containers(ae_ri, flower_id, container_names)`, which `discovery.py` calls to lazily create per-flower mirror containers (prefixed with the flower ID, e.g. `id-mn-flower-1-water_level`).

### discovery.py
Flask server on port 5001. Advertises the butler on mDNS and handles flower registrations.

**When a flower POSTs to `/register_flower`:**
1. Receives the path to the flower's `saref-descriptor` resource
2. Fetches it via oneM2M GET — learns container names and alert thresholds
3. Creates per-flower mirror containers on the butler's MN-CSE
4. Creates syntactical subscriptions on each flower container (flower ACME → butler notifier)
5. Creates self-subscriptions on each butler mirror container (butler ACME → butler notifier)
6. Registers the flower and its thresholds with the notifier (`POST /add_flower`)

All operations are idempotent — if a flower reboots and re-registers, the full setup reruns safely via ACME 409 handling.

### notifier.py
Flask app on port 5000. Receives oneM2M notifications and serves the web dashboard.

| Route | Purpose |
|---|---|
| `POST /notify/<flower_id>/<container>` | Receives new CIN from flower's ACME; mirrors to butler ACME; schedules SSE push |
| `POST /self-notify/<flower_id>/<container>` | Threshold check; sends Telegram alert if value ≤ threshold |
| `GET /` | Web dashboard |
| `GET /stream` | SSE stream (150 ms debounced) |
| `GET /data` | One-shot JSON snapshot of current state |
| `POST /add_flower` | Called by `discovery.py` to register a flower and its thresholds |
| `POST /remove_flower` | Called by `discovery.py` to mark a flower offline |

State (latest values + 30-reading history per container per flower) is held in memory. SSE clients receive an immediate snapshot on connect so the dashboard is never blank.

### saref_parser.py
Single function: `extract_containers(saref_desc)` — walks `saref:consistsOf` in the flower's SAREF JSON-LD and extracts `onem2m:resourcePath` and `saref:alertThreshold` per component. Returns a list of dicts with keys `name`, `resource_path`, `alert_threshold`.
