"""
Creates the butler's AE on the butler's local MN-CSE.
Mirror containers are created dynamically by discovery.py when each flower is found.
Run once before starting discovery and notifier.
"""

import json
import requests
from config import (
    BUTLER_CSE_HOST, BUTLER_CSE_NAME,
    BUTLER_AE_NAME, BUTLER_AE_ORIGINATOR, RVI,
)
import m2m_http

# Structural path used to address the butler AE in all URLs.
# ACME sets ri = originator (e.g. "Csmartbutler") for AE resources, which
# is not usable as a URL path segment — the structural address must be used.
_AE_PATH = f"{BUTLER_CSE_NAME}/{BUTLER_AE_NAME}"

RESOURCES_FILE      = "butler_resources.json"
ANNOUNCE_CONTAINER  = "flower-announcements"
ANNOUNCE_ACP_NAME   = "acp-flower-announcements"


def _find_ae_ri() -> str | None:
    """Confirm the butler AE exists and return its structural URL path."""
    url = f"{BUTLER_CSE_HOST}/{_AE_PATH}"
    print(f"[butler-setup] Confirming AE exists  GET {url}")
    try:
        r = requests.get(url, headers=m2m_http.headers(originator=BUTLER_AE_ORIGINATOR))
        print(f"[butler-setup] AE GET → {r.status_code}")
        if r.status_code == 200:
            print(f"[butler-setup] AE confirmed at path={_AE_PATH}")
            return _AE_PATH
        print(f"[butler-setup] AE GET returned {r.status_code}: {r.text[:200]}")
    except requests.RequestException as e:
        print(f"[butler-setup] AE GET error: {e}")
    return None


def _discover_container_ri(ae_ri: str, name: str) -> str | None:
    url = f"{BUTLER_CSE_HOST}/{ae_ri.lstrip('/')}?rcn=6&ty=3"
    print(f"[butler-setup] Discovering container ri for '{name}'  GET {url}")
    r   = requests.get(url, headers=m2m_http.headers())
    print(f"[butler-setup] Discovery → {r.status_code}")
    if r.status_code != 200:
        return None
    for ref in r.json().get("m2m:rrl", {}).get("rrf", []):
        if ref.get("nm") == name:
            val = ref.get("val", "").lstrip("/")
            if not val:
                print(f"[butler-setup] Container '{name}' found but val is empty")
                return None
            ri = val.split("/", 1)[1] if "/" in val else val
            print(f"[butler-setup] Container '{name}' discovered — ri={ri}")
            return ri
    print(f"[butler-setup] Container '{name}' not found in discovery results")
    return None


def create_ae() -> str | None:
    url  = f"{BUTLER_CSE_HOST}/{BUTLER_CSE_NAME}"
    body = {
        "m2m:ae": {
            "rn":  BUTLER_AE_NAME,
            "api": f"N{BUTLER_AE_NAME.lower()}",
            "rr":  True,
            "srv": [RVI],
        }
    }
    print(f"[butler-setup] Creating AE '{BUTLER_AE_NAME}'  POST {url}")
    r = requests.post(url, json=body, headers=m2m_http.headers(2, BUTLER_AE_ORIGINATOR))
    print(f"[butler-setup] AE creation → {r.status_code}")
    if r.status_code in (200, 201):
        print(f"[butler-setup]  AE '{BUTLER_AE_NAME}' created — path={_AE_PATH}")
        return _AE_PATH
    elif r.status_code in (409, 403):
        ri = _find_ae_ri()
        if ri:
            print(f"[butler-setup]  AE '{BUTLER_AE_NAME}' already exists — ri={ri}")
            return ri
        print(f"[butler-setup] X AE exists but RI lookup failed")
        return None
    print(f"[butler-setup] X AE creation failed: {r.status_code}  body={r.text[:200]}")
    return None


def create_announce_acp(ae_ri: str) -> str | None:
    """
    Create an open-inbox ACP for the flower-announcements container.
    Any originator may CREATE (acop=1) — the butler does not know flower
    originators before discovery, so the inbox must be publicly writable.
    Only the butler's own AE has full access (acop=63).
    """
    url  = f"{BUTLER_CSE_HOST}/{ae_ri.lstrip('/')}"
    body = {
        "m2m:acp": {
            "rn": ANNOUNCE_ACP_NAME,
            "pv": {
                "acr": [
                    {"acor": ["all"],              "acop": 1},   # any originator: CREATE only
                    {"acor": [BUTLER_AE_ORIGINATOR], "acop": 63}, # butler: full access
                ]
            },
            "pvs": {
                "acr": [{"acor": [BUTLER_AE_ORIGINATOR], "acop": 63}]
            },
        }
    }
    print(f"[butler-setup] Creating open-inbox ACP '{ANNOUNCE_ACP_NAME}'  POST {url}")
    r = requests.post(url, json=body, headers=m2m_http.headers(1, BUTLER_AE_ORIGINATOR))
    print(f"[butler-setup] ACP '{ANNOUNCE_ACP_NAME}' → {r.status_code}")
    if r.status_code in (200, 201):
        acp_ri = r.json().get("m2m:acp", {}).get("ri", "")
        print(f"[butler-setup]  ACP created — ri={acp_ri}")
        return acp_ri
    if r.status_code == 409:
        get_url = f"{BUTLER_CSE_HOST}/{ae_ri.lstrip('/')}/{ANNOUNCE_ACP_NAME}"
        r2 = requests.get(get_url, headers=m2m_http.headers(originator=BUTLER_AE_ORIGINATOR))
        if r2.status_code == 200:
            acp_ri = r2.json().get("m2m:acp", {}).get("ri", "")
            print(f"[butler-setup]  ACP already exists — ri={acp_ri}")
            return acp_ri
    print(f"[butler-setup] X ACP creation failed: {r.status_code}  body={r.text[:200]}")
    return None


def create_container(ae_ri: str, name: str, acp_ri: str | None = None) -> str | None:
    url = f"{BUTLER_CSE_HOST}/{ae_ri.lstrip('/')}"
    cnt = {"rn": name}
    if acp_ri:
        cnt["acpi"] = [acp_ri]
    body = {"m2m:cnt": cnt}
    print(f"[butler-setup] Creating container '{name}'  POST {url}  acpi={acp_ri}")
    r    = requests.post(url, json=body, headers=m2m_http.headers(3))
    print(f"[butler-setup] Container '{name}' → {r.status_code}")
    if r.status_code in (200, 201):
        ri = r.json().get("m2m:cnt", {}).get("ri", "")
        print(f"[butler-setup]  Container '{name}' created — ri={ri}")
        return ri
    elif r.status_code == 409:
        ri = _discover_container_ri(ae_ri, name)
        if ri:
            print(f"[butler-setup]  Container '{name}' already exists — ri={ri}")
            return ri
        print(f"[butler-setup] X Container '{name}' exists but ri fetch failed")
    else:
        print(f"[butler-setup] X Container '{name}' failed: {r.status_code}  body={r.text[:200]}")
    return None


def ensure_flower_containers(ae_ri: str, flower_id: str, container_names: list) -> dict:
    """
    Lazily create per-flower mirror containers on the butler's MN-CSE.
    Container names are prefixed with a sanitised flower ID so two flowers
    never share storage (e.g. 'id-mn-flower-1-water_level').
    container_names comes from the flower's SAREF description — the butler
    has no hardcoded knowledge of what containers a device has.
    Returns a dict keyed by '<flower_id>/<container>' → RI string.
    """
    safe_id = flower_id.replace("/", "-").lstrip("-")
    print(f"[butler-setup] Ensuring {len(container_names)} mirror container(s) for '{flower_id}' (prefix='{safe_id}')")
    ri_map  = {}
    for c in container_names:
        name = f"{safe_id}-{c}"
        ri   = create_container(ae_ri, name)
        if ri:
            ri_map[f"{flower_id}/{c}"] = ri
        else:
            print(f"[butler-setup] WARNING: could not create/find mirror container '{name}'")
    print(f"[butler-setup] ensure_flower_containers: {len(ri_map)}/{len(container_names)} OK")
    return ri_map


if __name__ == "__main__":
    print("---------------------------------------- [butler-setup] ----------------------------------------")
    ae_ri = create_ae()
    if not ae_ri:
        print("[butler-setup] Could not get AE --- aborting")
        exit(1)

    acp_ri = create_announce_acp(ae_ri)
    if not acp_ri:
        print("[butler-setup] Could not create announcement ACP --- aborting")
        exit(1)

    announce_ri = create_container(ae_ri, ANNOUNCE_CONTAINER, acp_ri)
    if not announce_ri:
        print("[butler-setup] Could not create announcement container --- aborting")
        exit(1)

    with open(RESOURCES_FILE, "w") as f:
        json.dump({"ae": ae_ri, "announce_ri": announce_ri, "containers": {}}, f, indent=2)
    print(f"[butler-setup] AE and announcement container ready --- saved to {RESOURCES_FILE}")
    print("[butler-setup] Mirror containers will be created per flower on discovery.")
    print("---------------------------------------- [butler-setup] ----------------------------------------")
    print("[butler-setup] Done.")
