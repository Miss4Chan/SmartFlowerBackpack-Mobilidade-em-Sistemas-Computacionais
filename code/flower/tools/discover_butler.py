"""
Discovers butler nodes via mDNS and registers this flower with each one.

When a butler appears (role=butler on _onem2m._tcp.local.) the flower POSTs
a oneM2M CIN to the butler's flower-announcements container on its ACME CSE.
The butler's ACME fires a SUB on that container which triggers full subscription
setup (SAREF fetch, mirror containers, syntactical + semantical subscriptions).

The butler CSE port and announcement path are read from the mDNS TXT record.

Runs until killed — start scripts keep it in the background alongside advertise.py.
"""

import json
import os
import signal
import socket
import sys
import threading
import time
import uuid

import requests
from zeroconf import ServiceBrowser, Zeroconf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))
from config import (
    AE_ORIGINATOR, CSE_ID, CSE_NAME, AE_NAME, FLOWER_NAME,
    MDNS_SERVICE_TYPE, MN_CSE_HOST, RVI,
)

_registered_butlers: set = set()   # cse-ids already contacted
_name_to_cse:        dict = {}     # mDNS service name → butler cse-id
_lock = threading.Lock()

CSE_PORT = int(MN_CSE_HOST.rsplit(":", 1)[-1])


def _get_ip() -> str:
    """
    detects flowers own local ip by opening UDP socket and doing dummy connection to 8.8.8.8.1 (google)
    the OS selects the correct outbnound network interface to route that adress and reads back the local IP.
    Socket is then closed
    """

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 1))
    ip = s.getsockname()[0]
    s.close()
    return ip


def _cin_headers() -> dict:
    """
    builds HTTP header dictionary required by oneM2M contentInstance (ty=4) POST requests
    x-m2m-origin identifies who is making the request (ae originator) to check access permissions
    x-m2m-ri - request id (uuid random) for dedubplication - if ri is seen twice, it discards the dup
    x-m2m-rvi - release version indicator (acme version). tells oneM2M spec version
    content-type - tells ACME what type of resource is being created. resource type
    accept - ACME to return response as json
    """

    return {
        "X-M2M-Origin": AE_ORIGINATOR,
        "X-M2M-RI":     str(uuid.uuid4()),
        "X-M2M-RVI":    RVI,
        "Content-Type": "application/json;ty=4",
        "Accept":       "application/json",
    }


def _do_register(butler_ip: str, cse_port: int, announce_path: str, retries: int = 8, delay: float = 3.0):
    """
    registers the flower with a discovered butler by POSTIing a CIN to the butlers anouncment container on its ACME CSE
    1. gets the flowers own IP to include in the payload so the butler knows how to reach back
    2. payload builds a  dictionary with everything the butler needs to know (ip, cse port, cse id, cse name, ae name,
    flower name, ae originator)
    3. body wraps the payload as m2m:cin contentinstance, json-encoding the payload into the con field
    4. retry loop attemps the POST up to retries times with a delay beween attempts. this handles the case where the butlers
    acme is still starting up when the flower first discovers it. on success, it returns immediately, on failure logs retries
    """
    
    local_ip = _get_ip()
    payload = {
        "flower_ip":       local_ip,
        "flower_port":     CSE_PORT,
        "flower_cse_id":   CSE_ID,
        "flower_cse_name": CSE_NAME,
        "flower_ae_name":  AE_NAME,
        "flower_name":     FLOWER_NAME,
        "ae_originator":   AE_ORIGINATOR,
    }
    url  = f"http://{butler_ip}:{cse_port}{announce_path}"
    body = {
        "m2m:cin": {
            "con": json.dumps(payload),
            "cnf": "application/json:0",
        }
    }
    print(f"[discover_butler] Posting registration CIN to butler ACME: POST {url}")
    print(f"[discover_butler]   flower_cse_id={CSE_ID}  flower_name={FLOWER_NAME}")
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(url, json=body, headers=_cin_headers(), timeout=10)
            if r.status_code in (200, 201):
                print(f"[discover_butler]  Butler accepted registration CIN (attempt {attempt})")
                return
            print(f"[discover_butler]   attempt {attempt} → {r.status_code}: {r.text[:200]}")
        except requests.RequestException as e:
            print(f"[discover_butler]   attempt {attempt} error: {e}")
        if attempt < retries:
            print(f"[discover_butler]   retrying in {delay}s...")
            time.sleep(delay)
    print(f"[discover_butler] ERROR: gave up registering with {butler_ip} after {retries} attempts")


class ButlerListener:
    """
    Zeroconf event listener that reacts to butler nodes appearing and disappearing on th elocal network via mDNS
    - _add_service() - does the work when the butler is found (description inside _add_service)
    - remove_service() - fired when butler disappears from the network. removes it from _registered_butlers and _name_to_cse
    if the butler comes back, the flower will re-register with it
    - update_service() - fired when a service record is updated
    """

    def add_service(self, zc: Zeroconf, type_: str, name: str):
        """
        safety wrapper. catches exceptions when there is a bad TXT record, network erorr, etc
        """

        print(f"[discover_butler] mDNS add_service: '{name}'")
        try:
            self._add_service(zc, type_, name)
        except Exception as e:
            print(f"[discover_butler] Error processing '{name}': {e}")

    def _add_service(self, zc: Zeroconf, type_: str, name: str):
        """
        1. fetches full mDNS service info and discloses TXT record properties
        2. checks if role is butler, ignores anything that isnt a butler
        3. reads butler_cse_id, butler_ip, cse_port, and announce_path from TXT record
        4. checks _registered_butlers under _lock if already registered with this butler, 
        skips it to aboud duplicate registers
        5. adds the butler to _registered_butlers and maps the mDNS name to its CSE ID
        6. spawns a background thread to call _do_register()
        """

        info = zc.get_service_info(type_, name)
        if not info:
            print(f"[discover_butler] No info for '{name}' — skipping")
            return

        props = {
            k.decode() if isinstance(k, bytes) else k:
            v.decode() if isinstance(v, bytes) else v
            for k, v in info.properties.items()
        }
        print(f"[discover_butler] mDNS TXT properties for '{name}':")
        for k, v in props.items():
            print(f"[discover_butler]   {k} = {v}")

        if props.get("role") != "butler":
            print(f"[discover_butler] role={props.get('role')!r} — not a butler, ignoring")
            return

        butler_cse_id  = props.get("cse-id", name)
        butler_ip      = socket.inet_ntoa(info.addresses[0])
        cse_port       = int(props.get("cse-port", "8082"))
        announce_path  = props.get("announce-path")

        if not announce_path:
            print(f"[discover_butler] Butler '{butler_cse_id}' has no announce-path — ignoring")
            return

        print(
            f"[discover_butler] Butler found: cse_id={butler_cse_id}  "
            f"ip={butler_ip}  cse_port={cse_port}  announce_path={announce_path}"
        )

        with _lock:
            if butler_cse_id in _registered_butlers:
                print(f"[discover_butler] Already registered with '{butler_cse_id}' — skipping")
                return
            _registered_butlers.add(butler_cse_id)
            _name_to_cse[name] = butler_cse_id

        threading.Thread(
            target=_do_register,
            args=(butler_ip, cse_port, announce_path),
            daemon=True,
            name=f"butler-reg-{butler_cse_id}",
        ).start()

    def remove_service(self, zc: Zeroconf, type_: str, name: str):
        print(f"[discover_butler] mDNS remove_service: '{name}' — butler left the network")
        with _lock:
            cse_id = _name_to_cse.pop(name, None)
            if cse_id:
                _registered_butlers.discard(cse_id)
                print(f"[discover_butler] '{cse_id}' removed — will re-register on return")

    def update_service(self, zc: Zeroconf, type_: str, name: str):
        print(f"[discover_butler] mDNS update_service: '{name}' — no action")


def main():
    local_ip = _get_ip()
    print(f"[discover_butler] Local IP: {local_ip}")
    print(f"[discover_butler] Flower CSE: {CSE_ID}  AE: {AE_ORIGINATOR}")
    print(f"[discover_butler] Browsing for butler subscribers on '{MDNS_SERVICE_TYPE}'...")

    zc = Zeroconf(interfaces=[local_ip])
    ServiceBrowser(zc, MDNS_SERVICE_TYPE, ButlerListener())

    def shutdown(sig, frame):
        print("[discover_butler] Shutting down...")
        zc.close()
        sys.exit(0)

    signal.signal(signal.SIGINT,  shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print("[discover_butler] Listening for butler — press Ctrl+C to stop")
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
