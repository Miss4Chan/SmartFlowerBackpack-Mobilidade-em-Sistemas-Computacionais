"""
Parses SAREF JSON-LD payloads from a flower's self-description.

extract_containers() — called by discovery.py once per flower to learn what
  containers exist, where they live in oneM2M, and what thresholds apply.
"""

from __future__ import annotations


def extract_containers(saref_desc: dict) -> list[dict]:
    """
    Extract per-container info from a flower's SAREF self-description.

    Each returned dict has:
      name            — container name (last segment of onem2m:resourcePath)
      resource_path   — full oneM2M path, e.g. /cse-mn-flower-1/SmartFlower/soil_moisture
      alert_threshold — numeric threshold that triggers an alert, or None
    """
    containers = []
    for component in saref_desc.get("saref:consistsOf", []):
        resource_path = component.get("onem2m:resourcePath")
        if not resource_path:
            continue
        name      = resource_path.rstrip("/").rsplit("/", 1)[-1]
        threshold = component.get("saref:alertThreshold")
        containers.append({
            "name":            name,
            "resource_path":   resource_path,
            "alert_threshold": threshold,
        })
    return containers
