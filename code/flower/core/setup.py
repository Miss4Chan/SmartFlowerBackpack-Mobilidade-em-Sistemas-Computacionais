"""
Runs once at boot: probes hardware and creates AE + containers on the MN-CSE.
Called from main.py inside a retry loop — returns True only on full success.
"""

import json
import uuid
import requests
import RPi.GPIO as GPIO
import spidev
import smbus2

import saref_desc
from config import (
    MN_CSE_HOST, CSE_ID, CSE_NAME, AE_NAME, AE_ORIGINATOR,
    RVI, CONTAINERS, BUTLER_AE_ORIGINATOR,
    SPI_BUS, SPI_DEVICE, SPI_SPEED_HZ,
    I2C_BUS, WATER_ADDR_LOW,
    PUMP_PIN,
)

RESOURCES_FILE = "resources.json"


def _post_headers(ty: int, originator: str = AE_ORIGINATOR) -> dict:
    return {
        "X-M2M-Origin":  originator,
        "X-M2M-RI":      str(uuid.uuid4()),
        "X-M2M-RVI":     RVI,
        "Content-Type":  f"application/json;ty={ty}",
        "Accept":        "application/json",
    }


def _get_headers() -> dict:
    return {
        "X-M2M-Origin": AE_ORIGINATOR,
        "X-M2M-RI":     str(uuid.uuid4()),
        "X-M2M-RVI":    RVI,
        "Accept":       "application/json",
    }


_AE_PATH = f"{CSE_NAME}/{AE_NAME}"


def _create_ae() -> str | None:
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
    r = requests.post(url, json=body, headers=_post_headers(2, AE_ORIGINATOR), timeout=5)
    print(f"[setup] AE creation → {r.status_code}")
    if r.status_code in (200, 201):
        print(f"[setup] AE '{AE_NAME}' created — path={_AE_PATH}")
        return _AE_PATH
    if r.status_code in (409, 403):
        print(f"[setup] AE '{AE_NAME}' already exists — continuing")
        return _AE_PATH
    print(f"[setup] X AE creation failed: {r.status_code}  body={r.text[:200]}")
    return None


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
                    {"acor": [AE_ORIGINATOR],        "acop": 63},   # full access for the flower
                    {"acor": [BUTLER_AE_ORIGINATOR],  "acop": 35},  # create-sub + retrieve + discover
                ]
            },
            "pvs": {
                "acr": [{"acor": [AE_ORIGINATOR], "acop": 63}]
            },
        }
    }
    print(f"[setup] Creating ACP 'acp-{container_name}'  POST {url}")
    r = requests.post(url, json=body, headers=_post_headers(1), timeout=5)
    print(f"[setup] ACP 'acp-{container_name}' → {r.status_code}")
    if r.status_code in (200, 201):
        ri = r.json().get("m2m:acp", {}).get("ri", "")
        print(f"[setup] ACP created — ri={ri}")
        return ri
    if r.status_code == 409:
        url2 = f"{MN_CSE_HOST}/{CSE_NAME}/{AE_NAME}/acp-{container_name}"
        r2 = requests.get(url2, headers=_get_headers(), timeout=5)
        if r2.status_code == 200:
            ri = r2.json().get("m2m:acp", {}).get("ri", "")
            print(f"[setup] ACP already exists — ri={ri}")
            return ri
    print(f"[setup] X ACP 'acp-{container_name}' failed: {r.status_code}  body={r.text[:200]}")
    return None


def _create_container(ae_ri: str, name: str, acp_ri: str | None = None) -> str | None:
    url  = f"{MN_CSE_HOST}/{ae_ri.lstrip('/')}"
    cnt  = {"rn": name}
    if acp_ri:
        cnt["acpi"] = [acp_ri]
    body = {"m2m:cnt": cnt}
    print(f"[setup] Creating container '{name}'  POST {url}  acpi={acp_ri}")
    r = requests.post(url, json=body, headers=_post_headers(3), timeout=5)
    print(f"[setup] Container '{name}' → {r.status_code}")
    if r.status_code in (200, 201):
        ri = r.json().get("m2m:cnt", {}).get("ri", "")
        print(f"[setup] Container '{name}' created — ri={ri}")
        return ri
    if r.status_code == 409:
        url2 = f"{MN_CSE_HOST}/{CSE_NAME}/{AE_NAME}/{name}"
        print(f"[setup] Container exists — fetching ri  GET {url2}")
        r2 = requests.get(url2, headers=_get_headers(), timeout=5)
        if r2.status_code == 200:
            ri = r2.json().get("m2m:cnt", {}).get("ri", "")
            print(f"[setup] Container '{name}' already exists — ri={ri}")
            return ri
        print(f"[setup] X Container '{name}' ri fetch failed: {r2.status_code}  body={r2.text[:200]}")
        return None
    print(f"[setup] X Container '{name}' failed: {r.status_code}  body={r.text[:200]}")
    return None


def _setup_cse() -> bool:
    """Create the AE and all containers; save their RIs to resources.json."""
    try:
        print(f"[setup] Connecting to MN-CSE at {MN_CSE_HOST}")
        ae_ri = _create_ae()
        if not ae_ri:
            return False
        ri_map = {}
        for name in CONTAINERS:
            acp_ri = _create_acp(ae_ri, name)
            if not acp_ri:
                print(f"[setup] WARNING: ACP for '{name}' not created — container will have no explicit ACL")
            ri = _create_container(ae_ri, name, acp_ri)
            if not ri:
                return False
            ri_map[name] = ri
        with open(RESOURCES_FILE, "w") as f:
            json.dump({"ae": ae_ri, "containers": ri_map}, f, indent=2)
        print(f"[setup] Resources saved -> {RESOURCES_FILE}")
        print(f"[setup]   ae={ae_ri}")
        for k, v in ri_map.items():
            print(f"[setup]   {k} -> {v}")

        print("[setup] Creating SAREF <semanticDescriptor> in ACME...")
        saref_desc.create_in_acme(ae_ri)

        return True
    except requests.RequestException as e:
        print(f"[setup] X CSE unreachable: {e}")
        return False


def _probe_hardware() -> bool:
    """Quick open/close of each bus to confirm the hardware is wired and accessible."""
    print("[setup] ── Hardware probe ──────────────────────────────────")
    try:
        print(f"[setup] Probing SPI (bus={SPI_BUS} device={SPI_DEVICE})...")
        spi = spidev.SpiDev()
        spi.open(SPI_BUS, SPI_DEVICE)
        spi.max_speed_hz = SPI_SPEED_HZ
        spi.close()
        print("[setup] SPI OK")

        print(f"[setup] Probing I2C (bus={I2C_BUS} addr=0x{WATER_ADDR_LOW:02X})...")
        bus = smbus2.SMBus(I2C_BUS)
        bus.read_byte(WATER_ADDR_LOW)
        bus.close()
        print("[setup] I2C OK")

        print(f"[setup] Probing GPIO (pin={PUMP_PIN} BCM)...")
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(PUMP_PIN, GPIO.OUT, initial=GPIO.HIGH)
        GPIO.cleanup()
        print("[setup] GPIO OK")

        print("[setup] All hardware probes passed")
        return True
    except Exception as e:
        print(f"[setup] X Hardware probe failed: {e}")
        return False


def run() -> bool:
    print("[setup] ════════════════════════════════════════════════════")
    ok = _probe_hardware() and _setup_cse()
    if ok:
        print("[setup] Setup complete")
    else:
        print("[setup] X Setup failed")
    print("[setup] ════════════════════════════════════════════════════")
    return ok
