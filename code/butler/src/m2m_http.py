"""
oneM2M HTTP primitives shared across butler modules.

Provides:
  headers()             — standard oneM2M request headers
  fetch_sd()            — retrieve a flower's SAREF descriptor from its ACME CSE
  create_sub_on_flower()— syntactical subscription on a flower container
  create_self_sub()     — semantical self-subscription on a butler mirror container
"""

import base64
import json
import time
import uuid

import requests

from config import BUTLER_AE_ORIGINATOR, BUTLER_CSE_HOST, RVI


def headers(ty: int = None, originator: str = BUTLER_AE_ORIGINATOR) -> dict:
    h = {
        "X-M2M-Origin": originator,
        "X-M2M-RI":     str(uuid.uuid4()),
        "X-M2M-RVI":    RVI,
        "Accept":       "application/json",
    }
    if ty is not None:
        h["Content-Type"] = f"application/json;ty={ty}"
    return h


def fetch_sd(
    flower_ip: str, flower_port: int, sd_path: str,
    retries: int = 4, delay: float = 3.0,
) -> dict | None:
    """
    Retrieve the flower's SAREF descriptor from its m2m:smd (ty=24) resource.
    sd_path is the path to the semanticDescriptor resource directly,
    e.g. /cse-mn-flower-1/SmartFlower/saref-descriptor
    """
    url = f"http://{flower_ip}:{flower_port}{sd_path}"
    for attempt in range(1, retries + 1):
        print(f"[discovery] Fetching SAREF descriptor (m2m:smd): {url} (attempt {attempt}/{retries})")
        try:
            r = requests.get(url, headers=headers(), timeout=10)
            print(f"[discovery] SAREF fetch -> {r.status_code}")
            if r.status_code == 200:
                dsp = r.json().get("m2m:smd", {}).get("dsp")
                if not dsp:
                    print(f"[discovery] SMD response missing 'dsp': {r.text[:200]}")
                else:
                    try:
                        # ACME stores dsp as a base64-encoded blob.
                        return json.loads(base64.b64decode(dsp).decode())
                    except Exception as e:
                        print(f"[discovery] SAREF parse error: {e}")
                        return None
            else:
                print(f"[discovery] SAREF fetch failed: {r.status_code}  body={r.text[:200]}")
        except requests.RequestException as e:
            print(f"[discovery] SAREF fetch error: {e}")
        if attempt < retries:
            print(f"[discovery] Retrying in {delay}s...")
            time.sleep(delay)
    return None


def create_sub_on_flower(
    flower_ip: str, flower_port: int,
    resource_path: str, notification_url: str, originator: str,
) -> bool:
    """
    Syntactical subscription on a flower container.
    nct=1: notification body contains the full new CIN.
    enc.net=[3]: only fire on child-resource creation (new CIN posted).
    originator must be the flower's own AE originator.
    Returns True if created or already exists.
    """
    url  = f"http://{flower_ip}:{flower_port}{resource_path}"
    body = {
        "m2m:sub": {
            "rn":  "sub-butler",
            "nu":  [notification_url],
            "enc": {"net": [3]},
            "nct": 1,
        }
    }
    print(f"[discovery] Creating syntactical SUB on flower:")
    print(f"[discovery]   resource  : {url}")
    print(f"[discovery]   notify_url: {notification_url}")
    print(f"[discovery]   originator: {originator}")
    print(f"[discovery]   nct=1 (All Attributes)  enc.net=[3] (child created)")
    try:
        r = requests.post(url, json=body, headers=headers(23, originator=originator), timeout=10)
        print(f"[discovery]   → {r.status_code}")
        if r.status_code in (200, 201):
            sub_ri = r.json().get("m2m:sub", {}).get("ri", "?")
            print(f"[discovery]    SUB created — ri={sub_ri}")
            return True
        if r.status_code == 409:
            print(f"[discovery]   SUB already exists on {resource_path}")
            return True
        print(f"[discovery]   X SUB creation failed: {r.status_code}  body={r.text[:200]}")
    except requests.RequestException as e:
        print(f"[discovery]   X Could not create SUB: {e}")
    return False


def create_self_sub(mirror_ri: str, notification_url: str, sub_name: str) -> bool:
    """
    Semantical self-subscription on a butler mirror container.
    nct=1: notification body contains the full new CIN for threshold evaluation.
    enc.net=[3]: only fire when a new CIN is mirrored into the container.
    Returns True if OK or already exists.
    """
    url  = f"{BUTLER_CSE_HOST}/{mirror_ri.lstrip('/')}"
    body = {
        "m2m:sub": {
            "rn":  sub_name,
            "nu":  [notification_url],
            "enc": {"net": [3]},
            "nct": 1,
        }
    }
    print(f"[discovery] Creating semantical self-SUB on butler mirror:")
    print(f"[discovery]   mirror_ri : {mirror_ri}")
    print(f"[discovery]   url       : {url}")
    print(f"[discovery]   notify_url: {notification_url}")
    print(f"[discovery]   nct=1 (All Attributes)  enc.net=[3] (child created)")
    try:
        r = requests.post(url, json=body, headers=headers(23), timeout=5)
        print(f"[discovery]   → {r.status_code}")
        if r.status_code in (200, 201):
            sub_ri = r.json().get("m2m:sub", {}).get("ri", "?")
            print(f"[discovery]    Self-SUB created — ri={sub_ri}")
            return True
        if r.status_code == 409:
            print(f"[discovery]   Self-SUB already exists on mirror {mirror_ri}")
            return True
        print(f"[discovery]   X Self-SUB failed: {r.status_code}  body={r.text[:200]}")
    except requests.RequestException as e:
        print(f"[discovery]   X Could not create self-SUB: {e}")
    return False


