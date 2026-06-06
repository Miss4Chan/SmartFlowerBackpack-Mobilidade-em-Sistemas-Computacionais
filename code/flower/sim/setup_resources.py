"""
Run once before starting the simulator (called by start_sim.sh).
Creates the AE, containers, and SAREF <semanticDescriptor> on the flower MN-CSE.
No subscriptions are created here — the butler creates them remotely on discovery.
"""

import json
import uuid
import requests
import saref_desc
from config import (
    MN_CSE_HOST, CSE_ID, CSE_NAME, AE_NAME, AE_ORIGINATOR,
    RVI, CONTAINERS, BUTLER_AE_ORIGINATOR,
)

RESOURCES_FILE = "resources.json"


def _headers(ty: int, originator: str = AE_ORIGINATOR) -> dict:
    # ty=2 → AE, ty=3 → container, ty=4 → contentInstance
    # X-M2M-RI is a per-request UUID used by ACME for deduplication
    return {
        "X-M2M-Origin":  originator,
        "X-M2M-RI":      str(uuid.uuid4()),
        "X-M2M-RVI":     RVI,
        "Content-Type":  f"application/json;ty={ty}",
        "Accept":        "application/json",
    }


def _get_headers(originator: str = AE_ORIGINATOR) -> dict:
    return {
        "X-M2M-Origin": originator,
        "X-M2M-RI":     str(uuid.uuid4()),
        "X-M2M-RVI":    RVI,
        "Accept":       "application/json",
    }


def _create_acp(ae_ri: str, container_name: str) -> str | None:
    """
    Create an Access Control Policy for a container.

    Grants the flower's own AE full access and grants the butler's originator
    the minimum needed to subscribe and read: CREATE(1) + RETRIEVE(2) + DISCOVER(32).
    Returns the ACP resource identifier, or None on failure.
    """
    url  = f"{MN_CSE_HOST}/{ae_ri.lstrip('/')}"
    body = {
        "m2m:acp": {
            "rn": f"acp-{container_name}",
            "pv": {
                "acr": [
                    {"acor": [AE_ORIGINATOR],      "acop": 63},   # full access for the flower
                    {"acor": [BUTLER_AE_ORIGINATOR], "acop": 35},  # create-sub + retrieve + discover
                ]
            },
            "pvs": {
                "acr": [{"acor": [AE_ORIGINATOR], "acop": 63}]
            },
        }
    }
    print(f"[setup] Creating ACP 'acp-{container_name}'  POST {url}")
    r = requests.post(url, json=body, headers=_headers(1, AE_ORIGINATOR))
    print(f"[setup] ACP 'acp-{container_name}' → {r.status_code}")
    if r.status_code in (200, 201):
        ri = r.json().get("m2m:acp", {}).get("ri", "")
        print(f"[setup]  ACP created — ri={ri}")
        return ri
    if r.status_code == 409:
        get_url = f"{MN_CSE_HOST}/{CSE_NAME}/{AE_NAME}/acp-{container_name}"
        r2 = requests.get(get_url, headers=_get_headers(AE_ORIGINATOR))
        if r2.status_code == 200:
            ri = r2.json().get("m2m:acp", {}).get("ri", "")
            print(f"[setup]  ACP already exists — ri={ri}")
            return ri
    print(f"[setup] X ACP 'acp-{container_name}' failed: {r.status_code}  body={r.text[:200]}")
    return None


def create_ae() -> str | None:
    url  = f"{MN_CSE_HOST}/{CSE_ID}"
    body = {
        "m2m:ae": {
            "rn":  AE_NAME,
            "api": f"N{AE_NAME.lower()}",
            "rr":  True,
            "srv": [RVI],
        }
    }
    print(f"[setup] Creating AE '{AE_NAME}'  POST {url}")
    r = requests.post(url, json=body, headers=_headers(2, AE_ORIGINATOR))
    print(f"[setup] AE creation → {r.status_code}")
    _ae_path = f"{CSE_NAME}/{AE_NAME}"
    if r.status_code in (200, 201):
        print(f"[setup]  AE '{AE_NAME}' created — path={_ae_path}")
        return _ae_path
    elif r.status_code in (409, 403):
        print(f"[setup] AE '{AE_NAME}' already exists — path={_ae_path}")
        return _ae_path
    print(f"[setup] X AE creation failed: {r.status_code}  body={r.text[:200]}")
    return None


def create_container(ae_ri: str, name: str, acp_ri: str | None = None) -> str | None:
    url  = f"{MN_CSE_HOST}/{ae_ri.lstrip('/')}"
    cnt  = {"rn": name}
    if acp_ri:
        cnt["acpi"] = [acp_ri]
    body = {"m2m:cnt": cnt}
    print(f"[setup] Creating container '{name}'  POST {url}  acpi={acp_ri}")
    r = requests.post(url, json=body, headers=_headers(3, AE_ORIGINATOR))
    print(f"[setup] Container '{name}' → {r.status_code}")
    if r.status_code in (200, 201):
        ri = r.json().get("m2m:cnt", {}).get("ri", "")
        print(f"[setup]  Container '{name}' created — ri={ri}")
        return ri
    elif r.status_code == 409:
        get_url = f"{MN_CSE_HOST}/{CSE_NAME}/{AE_NAME}/{name}"
        print(f"[setup] Container exists — fetching ri  GET {get_url}")
        r2 = requests.get(get_url, headers=_get_headers(AE_ORIGINATOR))
        print(f"[setup] ri fetch → {r2.status_code}")
        if r2.status_code == 200:
            ri = r2.json().get("m2m:cnt", {}).get("ri", "")
            print(f"[setup]  Container '{name}' already exists — ri={ri}")
            return ri
        print(f"[setup] X Container '{name}' exists but ri fetch failed: {r2.status_code}")
    else:
        print(f"[setup] X Container '{name}' failed: {r.status_code}  body={r.text[:200]}")
    return None


if __name__ == "__main__":
    print(f"[setup] ════════════════════════════════════════════════════")
    print(f"[setup] Connecting to MN-CSE at {MN_CSE_HOST}")
    print(f"[setup] CSE_ID={CSE_ID}  AE_NAME={AE_NAME}  originator={AE_ORIGINATOR}")

    ae_ri = create_ae()
    if not ae_ri:
        print("[setup] Could not get AE — aborting")
        exit(1)

    ri_map = {}
    for c in CONTAINERS:
        acp_ri = _create_acp(ae_ri, c)
        if not acp_ri:
            print(f"[setup] WARNING: ACP for '{c}' not created — container will have no explicit ACL")
        ri = create_container(ae_ri, c, acp_ri)
        if ri:
            ri_map[c] = ri
        else:
            print(f"[setup] WARNING: container '{c}' not created — it will be missing from resources.json")

    with open(RESOURCES_FILE, "w") as f:
        json.dump({"ae": ae_ri, "containers": ri_map}, f, indent=2)

    print(f"\n[setup] Resource IDs saved to {RESOURCES_FILE}:")
    print(f"[setup]   ae={ae_ri}")
    for k, v in ri_map.items():
        print(f"[setup]   {k} -> {v}")

    print("[setup] Creating SAREF <semanticDescriptor> in ACME...")
    saref_desc.create_in_acme(ae_ri)

    print("[setup] Done — butler will create subscriptions remotely on discovery.")
    print(f"[setup] ════════════════════════════════════════════════════")
