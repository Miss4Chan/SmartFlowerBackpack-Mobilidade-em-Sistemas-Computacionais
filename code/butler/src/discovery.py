"""
Butler discovery and registration server.

The butler advertises itself on mDNS so flowers can discover its ACME CSE address
and the path of its flower-announcements container.

Registration flow (oneM2M-native):
  - Flower finds the butler via mDNS, reads 'cse-port' and 'announce-path' from the
    TXT record, then POSTs a CIN to that path on the butler's ACME CSE.  The CIN 'con'
    field carries the flower's connection details as a JSON string.
  - The butler's own ACME fires a SUB on the announcements container, delivering the
    notification to /flower-announced on this server.
  - The butler then:
      1. Fetches the flower's SAREF <semanticDescriptor> (m2m:smd, ty=24) directly
         from the flower's ACME CSE to learn container names and thresholds.
      2. Creates per-flower mirror containers on the butler's local MN-CSE.
      3. Creates oneM2M SUB resources on each flower container so the flower's
         ACME will POST every new contentInstance to /notify/<flower_id>/<container>.
      4. Creates self-SUB resources on each butler mirror container so the butler's
         own ACME fires /self-notify/<flower_id>/<container> for threshold checks
         and Telegram alerts.
      5. Registers the flower with the notifier (name + thresholds).
"""

import json
import signal
import socket
import sys
import threading
import time

import requests
from flask import Flask, jsonify, request
from zeroconf import ServiceInfo, Zeroconf

import m2m_http
import saref_parser
from config import (
    BUTLER_CSE_HOST, BUTLER_CSE_ID, BUTLER_CSE_NAME,
    BUTLER_AE_NAME, BUTLER_AE_ORIGINATOR,
    MDNS_SERVICE_TYPE, BUTLER_SERVICE_NAME,
    BUTLER_HOST, BUTLER_CSE_PORT, NOTIFIER_PORT, REGISTRATION_PORT,
)
from butler_setup import ANNOUNCE_CONTAINER, RESOURCES_FILE, ensure_flower_containers

_save_lock = threading.Lock()

app = Flask(__name__)


# ── helpers ───────────────────────────────────────────────────────────────────

def _save_containers(ri_map: dict):
    """Merge new per-flower container RIs into butler_resources.json (thread-safe)."""
    with _save_lock:
        try:
            with open(RESOURCES_FILE) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {"ae": BUTLER_AE_ORIGINATOR, "containers": {}}
        data["containers"].update(ri_map)
        with open(RESOURCES_FILE, "w") as f:
            json.dump(data, f, indent=2)
    print(f"[discovery] Saved {len(ri_map)} container RI(s) to {RESOURCES_FILE}")


def _register_with_notifier(flower_cse_id: str, flower_name: str, containers_info: list) -> bool:
    """Tell the notifier about this flower — name and per-container thresholds."""
    url  = f"http://localhost:{NOTIFIER_PORT}/add_flower"
    body = {"flower_id": flower_cse_id, "flower_name": flower_name, "containers": containers_info}
    print(f"[discovery] Registering '{flower_cse_id}' with notifier at {url}")
    try:
        r = requests.post(url, json=body, timeout=5)
        print(f"[discovery] Notifier registration → {r.status_code}")
        if r.status_code == 200:
            return True
        print(f"[discovery] Notifier registration failed: {r.status_code}  body={r.text[:200]}")
    except requests.RequestException as e:
        print(f"[discovery] Could not reach notifier: {e}")
    return False


def _register_with_notifier_retry(
    flower_cse_id: str, flower_name: str, containers_info: list,
    max_attempts: int = 10, delay_s: float = 3.0,
) -> None:
    for attempt in range(1, max_attempts + 1):
        print(f"[discovery] Notifier registration attempt {attempt}/{max_attempts} for '{flower_cse_id}'...")
        if _register_with_notifier(flower_cse_id, flower_name, containers_info):
            print(f"[discovery]  Flower '{flower_cse_id}' registered with notifier")
            return
        if attempt < max_attempts:
            print(f"[discovery] Notifier not ready — retrying in {delay_s}s")
            time.sleep(delay_s)
    print(f"[discovery] ERROR: gave up registering '{flower_cse_id}' after {max_attempts} attempts")


# ── announcement subscription setup ──────────────────────────────────────────

def _setup_announcement_sub(announce_ri: str, retries: int = 10, delay_s: float = 2.0) -> bool:
    """
    Create a oneM2M SUB on the flower-announcements container.
    Retries because Flask must be listening before ACME sends its verification
    request on subscription creation.
    """
    url        = f"{BUTLER_CSE_HOST}/{announce_ri.lstrip('/')}"
    notify_url = f"http://localhost:{REGISTRATION_PORT}/flower-announced"
    body = {
        "m2m:sub": {
            "rn":  "sub-flower-announce",
            "nu":  [notify_url],
            "enc": {"net": [3]},
            "nct": 1,
        }
    }
    print(f"[discovery] Creating flower-announcement SUB on {url}")
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(url, json=body, headers=m2m_http.headers(23), timeout=5)
            print(f"[discovery] Announcement SUB → {r.status_code}")
            if r.status_code in (200, 201):
                sub_ri = r.json().get("m2m:sub", {}).get("ri", "?")
                print(f"[discovery]  Announcement SUB created — ri={sub_ri}")
                return True
            if r.status_code == 409:
                print("[discovery] Announcement SUB already exists")
                return True
            print(f"[discovery] X Attempt {attempt}/{retries}: {r.status_code}  body={r.text[:100]}")
        except requests.RequestException as e:
            print(f"[discovery] X Attempt {attempt}/{retries}: {e}")
        if attempt < retries:
            time.sleep(delay_s)
    print(f"[discovery] ERROR: Could not create announcement SUB after {retries} attempts")
    return False


# ── flower subscription setup ─────────────────────────────────────────────────

def setup_flower_subscriptions(
    flower_ip: str, flower_port: int,
    flower_cse_id: str, flower_name: str,
    flower_sd_path: str | None,
    ae_originator: str | None = None,
) -> bool:
    """
    Full subscription setup for one flower.  Triggered by the flower contacting
    this butler via POST /register_flower.  Idempotent — ACME returns 409 for
    duplicate subscriptions, which is handled gracefully throughout.
    Returns True on success, False on error.
    """
    if not flower_sd_path:
        print(f"[discovery] No SD path for '{flower_cse_id}' — cannot proceed")
        return False

    # 1. Fetch SAREF <semanticDescriptor> from the flower's ACME CSE
    sd_content = m2m_http.fetch_sd(flower_ip, flower_port, flower_sd_path)
    if not sd_content:
        print(f"[discovery] <semanticDescriptor> unavailable for '{flower_cse_id}' — cannot proceed")
        return False

    containers = saref_parser.extract_containers(sd_content)
    if not containers:
        print(f"[discovery] No containers found in SAREF for '{flower_cse_id}' — skipping")
        return False

    print(f"[discovery] SAREF parsed OK — '{flower_name}' has {len(containers)} container(s):")
    for c in containers:
        print(f"[discovery]   {c['name']}  path={c['resource_path']}  threshold={c['alert_threshold']}")

    # 2. Create mirror containers on butler's local MN-CSE
    try:
        with open(RESOURCES_FILE) as f:
            ae_ri = json.load(f)["ae"]
        print(f"[discovery] Butler AE ri={ae_ri}")
    except Exception as e:
        print(f"[discovery] Cannot read AE ri from {RESOURCES_FILE}: {e} — run butler_setup.py first")
        return False

    container_names = [c["name"] for c in containers]
    print(f"[discovery] Creating mirror containers on butler CSE: {container_names}")
    ri_map = ensure_flower_containers(ae_ri, flower_cse_id, container_names)
    if not ri_map:
        print(f"[discovery] Mirror container setup failed for '{flower_cse_id}'")
        return False

    print(f"[discovery] Mirror containers created ({len(ri_map)}):")
    for key, ri in ri_map.items():
        print(f"[discovery]   {key} → {ri}")
    _save_containers(ri_map)

    # 3. Create syntactical SUBs on each flower container (every CIN → /notify)
    # Use localhost when flower is on the same machine so Windows Firewall doesn't
    # block the verification request ACME sends on subscription creation.
    notify_host    = "localhost" if flower_ip == BUTLER_HOST else BUTLER_HOST
    sub_originator = ae_originator or BUTLER_AE_ORIGINATOR
    print(
        f"[discovery] Creating syntactical SUBs — notify_host={notify_host} "
        f"({'same machine' if flower_ip == BUTLER_HOST else 'remote machine'})"
    )
    print(f"[discovery] Using originator '{sub_originator}' for flower-side SUB creation")
    for c in containers:
        notif_url = f"http://{notify_host}:{NOTIFIER_PORT}/notify/{flower_cse_id}/{c['name']}"
        m2m_http.create_sub_on_flower(flower_ip, flower_port, c["resource_path"], notif_url, sub_originator)

    # 4. Create semantical self-SUBs on butler mirror containers (threshold → Telegram)
    print("[discovery] Creating semantical self-SUBs on butler mirror containers...")
    for c in containers:
        mirror_ri = ri_map.get(f"{flower_cse_id}/{c['name']}")
        if mirror_ri:
            notif_url = f"http://localhost:{NOTIFIER_PORT}/self-notify/{flower_cse_id}/{c['name']}"
            m2m_http.create_self_sub(mirror_ri, notif_url, f"sub-self-{c['name']}")
        else:
            print(f"[discovery] No mirror RI for {flower_cse_id}/{c['name']} — self-SUB skipped")

    print(f"[discovery]  Flower '{flower_cse_id}' fully subscribed.")

    # 5. Register with notifier in background (notifier may not be ready yet)
    containers_info = [{"name": c["name"], "alert_threshold": c["alert_threshold"]} for c in containers]
    threading.Thread(
        target=_register_with_notifier_retry,
        args=(flower_cse_id, flower_name, containers_info),
        daemon=True,
        name=f"notifier-reg-{flower_cse_id}",
    ).start()
    print(f"[discovery] Notifier registration thread started for '{flower_cse_id}'")
    return True


# ── Flask registration endpoint ───────────────────────────────────────────────

@app.route("/flower-announced", methods=["POST"])
def flower_announced():
    """
    oneM2M SUB notification endpoint for the flower-announcements container.
    Fired by the butler's ACME when a flower POSTs a CIN with its registration
    details to <butler-cse>/<butler-ae>/flower-announcements.

    Expected CIN 'con' (JSON string):
      {
        "flower_ip":       "<ip>",
        "flower_port":     <port>,
        "flower_cse_id":   "id-mn-flower-X",
        "flower_cse_name": "cse-mn-flower-X",
        "flower_ae_name":  "SmartFlower",
        "flower_name":     "My Plant",          # optional, falls back to ae_name
        "ae_originator":   "Csmartflower"       # optional but recommended
      }

    The SAREF m2m:smd path is derived by convention:
      /<flower_cse_name>/<flower_ae_name>/saref-descriptor
    """
    body = request.get_json(silent=True) or {}

    vrq = (body.get("m2m:sgn") or {}).get("vrq", False)
    if vrq:
        print("[discovery] /flower-announced: verification — OK")
        return "", 200, {"X-M2M-RSC": "2000"}

    print(f"\n[discovery] /flower-announced ════════════════════════════════")

    con = (body.get("m2m:sgn", {})
               .get("nev", {})
               .get("rep", {})
               .get("m2m:cin", {})
               .get("con"))
    if con is None:
        print("[discovery] No CIN value in notification — ignoring")
        return "", 200, {"X-M2M-RSC": "2000"}

    try:
        reg = json.loads(con)
    except Exception as e:
        print(f"[discovery] CIN con is not valid JSON: {e} — ignoring")
        return "", 200, {"X-M2M-RSC": "2000"}

    flower_ip       = reg.get("flower_ip")
    flower_port     = reg.get("flower_port")
    flower_cse_id   = reg.get("flower_cse_id")
    flower_cse_name = reg.get("flower_cse_name")
    flower_ae_name  = reg.get("flower_ae_name")
    flower_name     = reg.get("flower_name") or flower_ae_name
    ae_originator   = reg.get("ae_originator")

    print(f"[discovery] /flower-announced: cse_id={flower_cse_id}  name={flower_name}  ip={flower_ip}")

    missing = [k for k, v in {
        "flower_ip":       flower_ip,
        "flower_port":     flower_port,
        "flower_cse_id":   flower_cse_id,
        "flower_cse_name": flower_cse_name,
        "flower_ae_name":  flower_ae_name,
    }.items() if not v]
    if missing:
        print(f"[discovery] Missing registration fields: {missing} — ignoring")
        return "", 200, {"X-M2M-RSC": "2000"}

    flower_sd_path = f"/{flower_cse_name}/{flower_ae_name}/saref-descriptor"

    threading.Thread(
        target=setup_flower_subscriptions,
        args=(flower_ip, int(flower_port), flower_cse_id, flower_name, flower_sd_path, ae_originator),
        daemon=True,
        name=f"reg-{flower_cse_id}",
    ).start()

    print(f"[discovery]  Announcement accepted for '{flower_cse_id}' — setup running in background")
    return "", 200, {"X-M2M-RSC": "2000"}


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    print(f"[discovery] Butler host IP: {BUTLER_HOST}")
    print(f"[discovery] Butler CSE: {BUTLER_CSE_ID} on port {BUTLER_CSE_PORT}")
    print(f"[discovery] Notification server on port {REGISTRATION_PORT}")

    try:
        with open(RESOURCES_FILE) as f:
            announce_ri = json.load(f).get("announce_ri")
    except Exception as e:
        print(f"[discovery] Cannot read announce_ri from {RESOURCES_FILE}: {e} — run butler_setup.py first")
        sys.exit(1)
    if not announce_ri:
        print(f"[discovery] No announce_ri in {RESOURCES_FILE} — run butler_setup.py first")
        sys.exit(1)

    announce_path = f"/{BUTLER_CSE_NAME}/{BUTLER_AE_NAME}/{ANNOUNCE_CONTAINER}"
    butler_info = ServiceInfo(
        MDNS_SERVICE_TYPE,
        BUTLER_SERVICE_NAME,
        addresses=[socket.inet_aton(BUTLER_HOST)],
        port=BUTLER_CSE_PORT,
        properties={
            "role":          "butler",
            "cse-id":        BUTLER_CSE_ID,
            "cse-name":      BUTLER_CSE_NAME,
            "cse-port":      str(BUTLER_CSE_PORT),
            "announce-path": announce_path,
        },
    )

    print(f"[discovery] Registering butler on mDNS as '{BUTLER_SERVICE_NAME}'...")
    zc = Zeroconf(interfaces=[BUTLER_HOST])
    # allow_name_change=True lets zeroconf append a suffix if a stale record from a
    # previous run is still alive on the network. The flower filters by role=butler,
    # not by the exact service name, so renaming is transparent.
    zc.register_service(butler_info, allow_name_change=True)
    print(f"[discovery] Butler advertised — announce path: {announce_path}")

    flask_thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=REGISTRATION_PORT, threaded=True),
        daemon=True,
        name="notification-server",
    )
    flask_thread.start()
    print(f"[discovery] Notification server listening on 0.0.0.0:{REGISTRATION_PORT}")

    # Give Flask a moment to bind before ACME sends the SUB verification request.
    time.sleep(1.0)
    _setup_announcement_sub(announce_ri)

    def shutdown(sig, frame):
        print("[discovery] Shutting down — unregistering from mDNS...")
        zc.unregister_service(butler_info)
        zc.close()
        print("[discovery] Stopped.")
        sys.exit(0)

    signal.signal(signal.SIGINT,  shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
