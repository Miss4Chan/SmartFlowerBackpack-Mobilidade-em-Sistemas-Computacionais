"""
flowers communication layer to its local ACME CSE. takes sensor/actuator values and POST them as contentInstance
(CIN) resources into the correct oneM2M container

Its 3 components are:
1. load() - reads resources.json and populates _ri_map. called once during startup before loops begin
2. _headers() - builds oneM2M HTTP headers for every POST, always with ty=4 (contentInstance)
3. post() - only public funtion the rest of the code calls. looks up the container RI from _ri_map, builds
CIN body, POSTs it to the CSE. If CSE is down, logs the error and returns silently so the main and side loops
keep running
"""

import json
import uuid
import requests
from config import MN_CSE_HOST, AE_ORIGINATOR, RVI

# module level dictionary that maps container names (ex: soil_moisture) to their ACME resource identificers.
_ri_map: dict = {}


def load(path: str = "resources.json") -> None:
    """
    reads resources.json and populates _ri_map. called once during startup before loops begin
    """

    global _ri_map
    with open(path) as f:
        data = json.load(f)
    _ri_map = data["containers"]
    print(f"[cse_client] Loaded {len(_ri_map)} container RI(s) from '{path}':")
    for name, ri in _ri_map.items():
        print(f"[cse_client]   {name} → {ri}")


def _headers() -> dict:
    """
    builds oneM2M HTTP headers for every POST, always with ty=4 (contentInstance)
    x-m2m-origin identifies who is making the request (ae originator) to check access permissions
    x-m2m-ri - request id (uuid random) for dedubplication - if ri is seen twice, it discards the dup
    x-m2m-rvi - release version indicator (acme version). tells acme which version the request follows
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


def post(container: str, value) -> None:
    """Post a contentInstance — never raises, so a CSE outage never kills the loop."""
    ri = _ri_map.get(container)
    if not ri:
        print(f"[cse_client] No RI for '{container}' — skipping post")
        return
    url  = f"{MN_CSE_HOST}/{ri}"
    body = {"m2m:cin": {"con": str(value), "cnf": "text/plain:0"}}
    print(f"[cse_client] POST {container}={value!r}  url={url}")
    try:
        r = requests.post(url, json=body, headers=_headers(), timeout=5)
        if r.status_code in (200, 201):
            cin_ri = r.json().get("m2m:cin", {}).get("ri", "?")
            print(f"[cse_client]    {r.status_code}  CIN ri={cin_ri}")
        else:
            print(f"[cse_client]   X {r.status_code}  body={r.text[:200]}")
    except Exception as e:
        # Catch everything (connection refused, timeout, ACME crash, etc.)
        # The loop must keep running even when the CSE is unreachable.
        print(f"[cse_client]   X request failed: {e}")
