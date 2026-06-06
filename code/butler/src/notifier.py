"""
oneM2M notification receiver for the butler + web dashboard.

Subscriptions are set up by discovery.py when a flower registers itself.
Two kinds of subscription fire into this server per flower container:

  /notify/<flower_id>/<container>  — syntactical subscription
      Receives every new CIN from the flower's ACME.
      Records the value in memory, mirrors it to the butler's local ACME,
      and pushes it to all connected SSE browser tabs.

  /self-notify/<flower_id>/<container>  — semantical self-subscription
      Fires when a CIN is created in the butler's own mirror container
      (triggered by the mirror step above).
      Evaluates the value against a per-container alert threshold learned
      from the flower's SAREF description, then sends a Telegram message.

Flask also serves:
  /stream          — SSE push on every new reading
  /                — web dashboard (index.html)
  /data            — one-shot JSON snapshot
  /add_flower      — called by discovery.py after flower registration (POST JSON)
  /remove_flower   — available for manual/future use (POST JSON)
"""

import json
import os
import queue
import threading
import time
import uuid
from datetime import datetime

import requests
from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context

from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    BUTLER_CSE_HOST, BUTLER_AE_ORIGINATOR, RVI,
    ALERT_REPEAT_INTERVAL_S, NOTIFIER_PORT,
)

app = Flask(__name__)

_data_lock = threading.Lock()   # guards _state, _history, _alerted, _last_alert
_sse_lock  = threading.Lock()   # guards _clients list

_state:        dict = {}   # flower_id → {container → latest string value}
_history:      dict = {}   # flower_id → {container → [{t, v}, ...]}
_alerted:      dict = {}   # flower_id → {container → bool}
_last_alert:   dict = {}   # flower_id → {container → epoch of last Telegram send}
_last_update:  dict = {}   # flower_id → timestamp of last received reading
_clients:      list = []   # one queue per connected SSE browser tab

_flower_names:  dict = {}  # flower_id → human-readable name (from registration)
_thresholds:    dict = {}  # flower_id → {container → numeric threshold or None}
_containers_ri: dict = {}  # "flower_id/container" → RI (in-memory cache of resources file)

WEB_DIR      = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web"))
HISTORY_MAX  = 30
DEBOUNCE_S   = 0.15

_debounce_timer: threading.Timer | None = None
_debounce_lock  = threading.Lock()


# ── helpers ───────────────────────────────────────────────────────────────────

def _cin_headers() -> dict:
    return {
        "X-M2M-Origin": BUTLER_AE_ORIGINATOR,
        "X-M2M-RI":     str(uuid.uuid4()),
        "X-M2M-RVI":    RVI,
        "Content-Type": "application/json;ty=4",
        "Accept":       "application/json",
    }


def _refresh_container_cache():
    """Reload the RI map and persisted flower metadata from disk into memory."""
    global _containers_ri
    try:
        with open("butler_resources.json") as f:
            data = json.load(f)
        _containers_ri = data.get("containers", {})
        print(f"[notifier] Container RI cache refreshed — {len(_containers_ri)} entries")
        for fid, meta in data.get("flowers", {}).items():
            with _data_lock:
                _flower_names[fid] = meta.get("name", fid)
                _thresholds[fid]   = meta.get("thresholds", {})
                _state.setdefault(fid, {})
                _history.setdefault(fid, {})
                _alerted.setdefault(fid, {})
                _last_alert.setdefault(fid, {})
            print(f"[notifier] Restored flower '{fid}' — thresholds={_thresholds[fid]}")
    except Exception as e:
        print(f"[notifier] Could not refresh container cache: {e}")


_persist_lock = threading.Lock()

def _persist_flower(flower_id: str):
    """Write the flower's name and thresholds into butler_resources.json."""
    with _persist_lock:
        try:
            try:
                with open("butler_resources.json") as f:
                    data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                data = {}
            flowers = data.setdefault("flowers", {})
            flowers[flower_id] = {
                "name":       _flower_names.get(flower_id, flower_id),
                "thresholds": _thresholds.get(flower_id, {}),
            }
            with open("butler_resources.json", "w") as f:
                json.dump(data, f, indent=2)
            print(f"[notifier] Persisted flower '{flower_id}' to butler_resources.json")
        except Exception as e:
            print(f"[notifier] Could not persist flower '{flower_id}': {e}")


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _snapshot() -> dict:
    """Build the full state dict sent to the dashboard (call with _data_lock held)."""
    return {
        fid: {
            "name":        _flower_names.get(fid, fid),
            "state":       dict(state),
            "history":     {c: list(h) for c, h in _history.get(fid, {}).items()},
            "alerted":     any(_alerted.get(fid, {}).values()),
            "last_update": _last_update.get(fid, 0),
        }
        for fid, state in _state.items()
    }


# ── SSE push ──────────────────────────────────────────────────────────────────

def _push_sse():
    with _data_lock:
        payload = json.dumps(_snapshot())
    with _sse_lock:
        dead = []
        for q in _clients:
            try:
                q.put_nowait(payload)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _clients.remove(q)
    active = len(_clients)
    print(f"[notifier] SSE push sent to {active} client(s)")


def _schedule_push():
    global _debounce_timer
    with _debounce_lock:
        if _debounce_timer is not None:
            _debounce_timer.cancel()
        _debounce_timer = threading.Timer(DEBOUNCE_S, _push_sse)
        _debounce_timer.start()


# ── oneM2M mirror ─────────────────────────────────────────────────────────────

def _ri_url(ri: str) -> str:
    return f"{BUTLER_CSE_HOST}/{ri.lstrip('/')}"


def mirror_to_local(flower_id: str, container: str, value: str):
    key = f"{flower_id}/{container}"
    ri  = _containers_ri.get(key)
    if not ri:
        print(f"[notifier] No local RI for '{key}' — mirror skipped (is container cache loaded?)")
        return
    url  = _ri_url(ri)
    body = {"m2m:cin": {"con": value, "cnf": "text/plain:0"}}
    print(f"[notifier] Mirroring {key}={value!r} → {url}")
    try:
        r = requests.post(url, json=body, headers=_cin_headers(), timeout=5)
        if r.status_code in (200, 201):
            cin_ri = r.json().get("m2m:cin", {}).get("ri", "?")
            print(f"[notifier] Mirror  {r.status_code}  CIN ri={cin_ri}  (self-SUB will fire next)")
        else:
            print(f"[notifier] Mirror X {r.status_code}  body={r.text[:200]}")
    except requests.RequestException as e:
        print(f"[notifier] Mirror error for '{key}': {e}")


# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(message: str):
    print(f"[notifier] Sending Telegram message:\n{message}")
    url  = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        r = requests.post(url, data=data, timeout=10)
        if r.status_code == 200:
            print(f"[notifier] Telegram  sent")
        else:
            print(f"[notifier] Telegram X {r.status_code}  body={r.text[:200]}")
    except requests.RequestException as e:
        print(f"[notifier] Telegram error: {e}")


# ── in-memory record ─────────────────────────────────────────────────────────

def _record(flower_id: str, container: str, value: str):
    with _data_lock:
        if flower_id not in _state:
            _state[flower_id]   = {}
            _history[flower_id] = {}
        _state[flower_id][container] = value
        _last_update[flower_id] = time.time()

        hist = _history[flower_id].setdefault(container, [])
        try:
            v = float(value)
        except ValueError:
            v = 0.0
        hist.append({"t": _now(), "v": v})
        if len(hist) > HISTORY_MAX:
            hist.pop(0)

    print(f"[notifier] Recorded {flower_id}/{container}={value!r}  (history len={len(hist)})")
    _schedule_push()


# ── alert check (uses thresholds learned from SAREF) ─────────────────────────

def _check_alert(flower_id: str, container: str, value):
    threshold = _thresholds.get(flower_id, {}).get(container)
    label     = _flower_names.get(flower_id, flower_id)

    print(f"[notifier] Alert check: {flower_id}/{container}={value}  threshold={threshold}")

    if threshold is None:
        print(f"[notifier]   No threshold configured for {container} — skipping alert check")
        return
    try:
        level = float(value)
    except (ValueError, TypeError):
        print(f"[notifier]   Cannot convert value {value!r} to float — skipping alert check")
        return

    now = time.time()

    with _data_lock:
        alerted    = _alerted.get(flower_id, {}).get(container, False)
        last_alert = _last_alert.get(flower_id, {}).get(container, 0.0)

    time_since_last = now - last_alert
    print(
        f"[notifier]   level={level}  threshold={threshold}  "
        f"alerted={alerted}  time_since_last_alert={time_since_last:.0f}s "
        f"(repeat_interval={ALERT_REPEAT_INTERVAL_S}s)"
    )

    if level <= threshold:
        if time_since_last >= ALERT_REPEAT_INTERVAL_S:
            with _data_lock:
                _alerted.setdefault(flower_id, {})[container]    = True
                _last_alert.setdefault(flower_id, {})[container] = now
            repeat = " (reminder)" if alerted else ""
            msg = (
                f"[Alert{repeat}]\n"
                f"Device  : {label}\n"
                f"Sensor  : {container}\n"
                f"Level   : {level}\n"
                f"Action  : Please check the device."
            )
            print(f"[notifier]   ⚠ ALERT{repeat}: {container}={level} ≤ {threshold} — sending Telegram")
            send_telegram(msg)
        else:
            print(
                f"[notifier]   Alert condition active but suppressed "
                f"({time_since_last:.0f}s < {ALERT_REPEAT_INTERVAL_S}s repeat interval)"
            )
    elif alerted:
        with _data_lock:
            _alerted.setdefault(flower_id, {})[container]    = False
            _last_alert.setdefault(flower_id, {})[container] = 0.0
        print(f"[notifier]    Alert CLEARED: {container}={level} > {threshold}")
    else:
        print(f"[notifier]   OK: {container}={level} > {threshold} — no alert")


# ── notification parse helper ─────────────────────────────────────────────────

def _extract_cin_value(body: dict):
    """Pull the CIN content value out of an m2m:sgn notification body."""
    sgn = body.get("m2m:sgn", {})
    rep = sgn.get("nev", {}).get("rep", {})
    cin = rep.get("m2m:cin", {})
    return cin.get("con")


# ── Flask routes ──────────────────────────────────────────────────────────────

@app.route("/notify/<flower_id>/<container>", methods=["POST"])
def notify(flower_id, container):
    """
    Syntactical subscription endpoint.
    Receives every new CIN from the flower's ACME, records it,
    mirrors it to the butler's local ACME (which triggers the self-SUB).
    """
    body = request.get_json(silent=True) or {}

    print(f"\n[notify] ══ /notify/{flower_id}/{container} ══════════════════════════════")
    print(f"[notify] Full m2m:sgn body received:")
    print(json.dumps(body, indent=2))

    value = _extract_cin_value(body)

    if value is None:
        vrq = (body.get("m2m:sgn") or {}).get("vrq", False)
        if vrq:
            print(f"[notify] Verification request — responding 200 OK")
        else:
            print(f"[notify] Empty or unrecognised body — no CIN value found, ignoring")
        return "", 200

    print(f"[notify] Extracted value: {value!r}")
    _record(flower_id, container, str(value))
    mirror_to_local(flower_id, container, str(value))
    print(f"[notify] ══ done ══════════════════════════════════════════════════\n")
    return "", 200


@app.route("/self-notify/<flower_id>/<container>", methods=["POST"])
def self_notify(flower_id, container):
    """
    Semantical self-subscription endpoint.
    Fires when the butler's own ACME creates a CIN in the mirror container.
    Evaluates the value against the SAREF-learned threshold and sends Telegram.
    """
    body = request.get_json(silent=True) or {}

    print(f"\n[self-notify] ══ /self-notify/{flower_id}/{container} ══════════════════")
    print(f"[self-notify] Full m2m:sgn body received:")
    print(json.dumps(body, indent=2))

    value = _extract_cin_value(body)

    if value is None:
        vrq = (body.get("m2m:sgn") or {}).get("vrq", False)
        if vrq:
            print(f"[self-notify] Verification request — responding 200 OK")
        else:
            print(
                f"[self-notify] No CIN value found in body — "
                f"check that self-SUB uses nct=1 (All Attributes)"
            )
        return "", 200

    print(f"[self-notify] Extracted value: {value!r}")
    _check_alert(flower_id, container, value)
    print(f"[self-notify] ══ done ══════════════════════════════════════════════\n")
    return "", 200


@app.route("/stream")
def stream():
    def generate():
        q = queue.Queue(maxsize=20)
        with _sse_lock:
            _clients.append(q)
        print(f"[notifier] SSE client connected — total={len(_clients)}")

        with _data_lock:
            initial = json.dumps(_snapshot())
        yield f"data: {initial}\n\n"

        try:
            while True:
                try:
                    payload = q.get(timeout=25)
                    yield f"data: {payload}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            with _sse_lock:
                try:
                    _clients.remove(q)
                except ValueError:
                    pass
            print(f"[notifier] SSE client disconnected — total={len(_clients)}")

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/data")
def data():
    with _data_lock:
        return jsonify(_snapshot())


@app.route("/")
def dashboard():
    return send_from_directory(WEB_DIR, "index.html")


@app.route("/add_flower", methods=["POST"])
def add_flower_endpoint():
    """
    Called by discovery.py when a new flower is found via mDNS.
    Registers the flower's display name and per-container alert thresholds
    (both learned from the flower's SAREF description — no hardcoding here).
    """
    body        = request.get_json(silent=True) or {}
    flower_id   = body.get("flower_id")
    flower_name = body.get("flower_name")
    containers  = body.get("containers", [])

    print(f"\n[notifier] /add_flower: flower_id={flower_id}  name={flower_name}")
    print(f"[notifier]   containers: {containers}")

    if not flower_id or not flower_name:
        return jsonify({"error": "flower_id and flower_name required"}), 400

    with _data_lock:
        _flower_names[flower_id] = flower_name
        _thresholds[flower_id]   = {
            c["name"]: c["alert_threshold"]
            for c in containers
            if c.get("alert_threshold") is not None
        }
        if flower_id not in _state:
            _state[flower_id]   = {}
            _history[flower_id] = {}
        _alerted.setdefault(flower_id, {})
        _last_alert.setdefault(flower_id, {})

    _refresh_container_cache()
    _persist_flower(flower_id)

    thresholds = _thresholds[flower_id]
    print(
        f"[notifier]  Flower '{flower_id}' ({flower_name}) registered — "
        f"{len(containers)} container(s), thresholds: {thresholds}"
    )
    return jsonify({"status": "ok", "flower_id": flower_id})


@app.route("/remove_flower", methods=["POST"])
def remove_flower_endpoint():
    """Called by discovery.py when a flower leaves the network."""
    body      = request.get_json(silent=True) or {}
    flower_id = body.get("flower_id")
    if not flower_id:
        return jsonify({"error": "flower_id required"}), 400

    # Leave state in place so dashboard shows it as offline until it reconnects.
    # Thresholds and name are kept so a re-discovered flower is immediately usable.
    print(f"[notifier] /remove_flower: '{flower_id}' — marking offline (state preserved)")
    return jsonify({"status": "ok", "flower_id": flower_id})


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _refresh_container_cache()
    print(f"[notifier] ════════════════════════════════════════════════════")
    print(f"[notifier] Dashboard on http://0.0.0.0:{NOTIFIER_PORT}")
    print(f"[notifier] Waiting for flowers via POST /add_flower...")
    print(f"[notifier] ════════════════════════════════════════════════════")
    app.run(host="0.0.0.0", port=NOTIFIER_PORT, threaded=True)
