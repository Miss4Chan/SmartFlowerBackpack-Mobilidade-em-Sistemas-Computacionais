"""
Stores the flower's SAREF self-description on the flower's own MN-CSE as a
first-class m2m:smd (SemanticDescriptor, ty=24) resource named 'saref-descriptor'.

ACME treats the dsp field as a base64-encoded blob, so the JSON-LD payload is
base64-encoded and stored directly in dsp — no external URI or container needed.

The butler retrieves it with a standard oneM2M GET to .../saref-descriptor,
then base64-decodes dsp to recover the JSON-LD dict.

Called once during setup (setup.py / setup_resources.py).
"""

import base64
import json
import uuid

import requests

from config import (
    MN_CSE_HOST, CSE_NAME, AE_NAME,
    AE_ORIGINATOR, BUTLER_AE_ORIGINATOR, RVI,
    FLOWER_NAME, WATER_CRITICAL_THRESHOLD,
)

SD_CONTAINER_NAME = "saref-descriptor"


def _post_headers(ty: int, originator: str = AE_ORIGINATOR) -> dict:
    """
    builds the http header dictionary required bny oneM2M POST requests. 
    x-m2m-origin identifies who is making the request (ae originator) to check access permissions
    x-m2m-ri - request id (uuid random) for dedubplication - if ri is seen twice, it discards the dup
    x-m2m-rvi - release version indicator (acme version). tells oneM2M spec version
    content-type - tells ACME what type of resource is being created. resource type
    accept - ACME to return response as json
    """
    
    return {
        "X-M2M-Origin":  originator,
        "X-M2M-RI":      str(uuid.uuid4()),
        "X-M2M-RVI":     RVI,
        "Content-Type":  f"application/json;ty={ty}",
        "Accept":        "application/json",
    }


def _get_headers(originator: str = AE_ORIGINATOR) -> dict:
    """
    builds the http header dictionary required by oneM2M GET requests. 
    x-m2m-origin identifies who is making the request (ae originator) to check access permissions
    x-m2m-ri - request id (uuid random) for dedubplication - if ri is seen twice, it discards the dup
    x-m2m-rvi - release version indicator (acme version). version of the oneM2M standard the request follows
    accept - ACME to return response as json
    """

    return {
        "X-M2M-Origin": originator,
        "X-M2M-RI":     str(uuid.uuid4()),
        "X-M2M-RVI":    RVI,
        "Accept":       "application/json",
    }


def _build_description() -> dict:
    """
    builds saref json-ld dictionary that describes the flower device and all its sensors/actuators
    in a semantic format.
    @context - defines vocabulary namespaces used in description
    saref - SAREF ontology (smart appliances)
    saref4agri - SAREF agriculture extension (soil, irrigation)
    om - units of measure
    onem2m - extension linking SAREF concepts to oneM2M source paths
    @type, @id, sarf:hasName, onem2m:cseId - identifies device as saref:Device, gives a unique URI, name, and links to it CSE
    saref:consistsOf - lists the 4 componenct the device is made off
    1. SoilMoistureSensor
    2. Sensor (water level)
    3. IrrigationSytem (pump)
    4 Device (heartbeat)
    buttler reads this description on discovery to learn what containers exist, where they are and what thresholds to alert on
    without hardcoded knowledge of flowers structure
    """

    return {
        "@context": {
            "saref":      "https://saref.etsi.org/core/",
            "saref4agri": "https://saref.etsi.org/saref4agri/",
            "om":         "http://www.ontology-of-units-of-measure.org/resource/om-2/",
            "onem2m":     "urn:saref:ext:onem2m:",
        },
        "@type":         "saref:Device",
        "@id":           f"urn:smartflower:{FLOWER_NAME}",
        "saref:hasName": FLOWER_NAME,
        "onem2m:cseId":  CSE_NAME,
        "saref:consistsOf": [
            {
                "@type": "saref4agri:SoilMoistureSensor",
                "@id":   f"urn:smartflower:{FLOWER_NAME}:soil-moisture-sensor",
                "saref:measuresProperty": {"@type": "saref4agri:SoilMoisture"},
                "saref:isMeasuredIn":     {"@type": "om:Percent"},
                "onem2m:resourcePath":    f"/{CSE_NAME}/{AE_NAME}/soil_moisture",
                "saref:alertThreshold":   None,
            },
            {
                "@type": "saref:Sensor",
                "@id":   f"urn:smartflower:{FLOWER_NAME}:water-level-sensor",
                "saref:measuresProperty": {
                    "@type":         "saref:Property",
                    "saref:hasName": "WaterLevel",
                },
                "saref:isMeasuredIn":   {"@type": "om:Percent"},
                "onem2m:resourcePath":  f"/{CSE_NAME}/{AE_NAME}/water_level",
                "saref:alertThreshold": WATER_CRITICAL_THRESHOLD,
            },
            {
                "@type": "saref4agri:IrrigationSystem",
                "@id":   f"urn:smartflower:{FLOWER_NAME}:pump",
                "saref:hasFunction": {
                    "@type":            "saref4agri:IrrigationFunction",
                    "saref:hasCommand": "water",
                },
                "saref:hasState":      {"@type": "saref:OnOffState"},
                "onem2m:resourcePath": f"/{CSE_NAME}/{AE_NAME}/pump_status",
                "saref:alertThreshold": None,
            },
            {
                "@type":               "saref:Device",
                "@id":                 f"urn:smartflower:{FLOWER_NAME}:heartbeat",
                "saref:hasName":       "heartbeat",
                "onem2m:resourcePath": f"/{CSE_NAME}/{AE_NAME}/heartbeat",
                "saref:alertThreshold": None,
            },
        ],
    }


def _create_acp(ae_ri: str) -> str | None:
    """Create an ACP granting the butler RETRIEVE access to the semantic descriptor."""
    url  = f"{MN_CSE_HOST}/{ae_ri.lstrip('/')}"
    body = {
        "m2m:acp": {
            "rn": f"acp-{SD_CONTAINER_NAME}",
            "pv": {
                "acr": [
                    {"acor": [AE_ORIGINATOR],        "acop": 63},
                    {"acor": [BUTLER_AE_ORIGINATOR],  "acop": 2},
                ]
            },
            "pvs": {
                "acr": [{"acor": [AE_ORIGINATOR], "acop": 63}]
            },
        }
    }
    print(f"[saref] Creating ACP  POST {url}")
    r = requests.post(url, json=body, headers=_post_headers(1), timeout=5)
    print(f"[saref] ACP -> {r.status_code}")
    if r.status_code in (200, 201):
        ri = r.json().get("m2m:acp", {}).get("ri", "")
        print(f"[saref] ACP created  ri={ri}")
        return ri
    if r.status_code == 409:
        r2 = requests.get(
            f"{MN_CSE_HOST}/{CSE_NAME}/{AE_NAME}/acp-{SD_CONTAINER_NAME}",
            headers=_get_headers(), timeout=5,
        )
        if r2.status_code == 200:
            ri = r2.json().get("m2m:acp", {}).get("ri", "")
            print(f"[saref] ACP already exists  ri={ri}")
            return ri
    print(f"[saref] ACP creation failed: {r.status_code}  body={r.text[:200]}")
    return None


def _create_smd(ae_ri: str, acp_ri: str | None) -> str | None:
    """
    Create m2m:smd (ty=24) with the JSON-LD payload base64-encoded into dsp.
    ACME validates dsp as a base64 blob, so the content is encoded directly —
    no external URI or container is needed.
    """
    url = f"{MN_CSE_HOST}/{ae_ri.lstrip('/')}"
    dsp = base64.b64encode(json.dumps(_build_description()).encode()).decode()
    smd = {
        "rn":   SD_CONTAINER_NAME,
        "dsp":  dsp,
        "or":   "https://saref.etsi.org/core/",
        "dcrp": 7,
    }
    if acp_ri:
        smd["acpi"] = [acp_ri]
    body = {"m2m:smd": smd}
    print(f"[saref] Creating m2m:smd '{SD_CONTAINER_NAME}'  POST {url}")
    r = requests.post(url, json=body, headers=_post_headers(24), timeout=5)
    print(f"[saref] m2m:smd -> {r.status_code}")
    if r.status_code in (200, 201):
        smd_ri = r.json().get("m2m:smd", {}).get("ri", "")
        print(f"[saref] m2m:smd created  ri={smd_ri}")
        return smd_ri
    if r.status_code == 409:
        r2 = requests.get(
            f"{MN_CSE_HOST}/{CSE_NAME}/{AE_NAME}/{SD_CONTAINER_NAME}",
            headers=_get_headers(), timeout=5,
        )
        if r2.status_code == 200:
            smd_ri = r2.json().get("m2m:smd", {}).get("ri", "")
            print(f"[saref] m2m:smd already exists  ri={smd_ri}")
            return smd_ri
    print(f"[saref] m2m:smd creation failed: {r.status_code}  body={r.text[:200]}")
    return None


def create_in_acme(ae_ri: str) -> str | None:
    """
    Store the SAREF JSON-LD as an m2m:smd (ty=24) resource on the flower's MN-CSE.
    Butler retrieves it via: GET /{cse_name}/{ae_name}/saref-descriptor
    Returns the SMD resource RI on success, None on failure.
    """
    acp_ri = _create_acp(ae_ri)
    smd_ri = _create_smd(ae_ri, acp_ri)
    if not smd_ri:
        return None
    print(f"[saref] Butler retrieves: GET {MN_CSE_HOST}/{CSE_NAME}/{AE_NAME}/{SD_CONTAINER_NAME}")
    return smd_ri
