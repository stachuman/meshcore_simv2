"""MeshCore map API client and node filtering."""

import json
import os
import re
import sys
import time

import requests


DEFAULT_API_URL = "https://map.meshcore.io/api/v1/nodes"


def fetch_nodes(
    api_url: str = DEFAULT_API_URL,
    cache_path: str | None = None,
    cache_max_age_s: int = 86400,
) -> list[dict]:
    """Fetch all nodes from the MeshCore map API.

    If cache_path is set and fresh (< cache_max_age_s), loads from cache.
    Otherwise fetches from API and saves to cache.
    """
    if cache_path and os.path.exists(cache_path):
        age = time.time() - os.path.getmtime(cache_path)
        if age < cache_max_age_s:
            print(f"  Loading cached nodes from {cache_path} "
                  f"(age: {age/3600:.1f}h)", file=sys.stderr)
            with open(cache_path) as f:
                return json.load(f)

    print(f"  Fetching nodes from {api_url}...", file=sys.stderr)
    resp = requests.get(api_url, timeout=60, allow_redirects=True)
    resp.raise_for_status()
    nodes = resp.json()

    if cache_path:
        os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(nodes, f)
        print(f"  Cached {len(nodes)} nodes to {cache_path}", file=sys.stderr)

    return nodes


def filter_nodes(
    nodes: list[dict],
    bbox: tuple[float, float, float, float] | None = None,
    node_types: set[int] | None = None,
    require_coords: bool = True,
) -> list[dict]:
    """Filter nodes by bounding box, type, and coordinate validity.

    bbox: (lat_min, lon_min, lat_max, lon_max)
    node_types: set of API type codes (1=companion, 2=repeater, 3=gateway)
    """
    if node_types is None:
        node_types = {2, 3}  # repeaters + gateways

    result = []
    for node in nodes:
        ntype = node.get("type")
        if ntype not in node_types:
            continue

        lat = node.get("adv_lat")
        lon = node.get("adv_lon")
        if require_coords and (lat is None or lon is None or
                               (lat == 0.0 and lon == 0.0)):
            continue

        if bbox:
            lat_min, lon_min, lat_max, lon_max = bbox
            if not (lat_min <= lat <= lat_max and lon_min <= lon <= lon_max):
                continue

        result.append(node)

    return result


def sanitize_name(name: str, seen: dict[str, int]) -> str:
    """Strip emoji/non-ASCII, replace spaces, truncate, ensure uniqueness."""
    clean = name.encode("ascii", "ignore").decode("ascii").strip()
    clean = re.sub(r"[\s]+", "_", clean)
    clean = re.sub(r"[^A-Za-z0-9_]", "", clean)
    clean = clean[:20]
    if not clean:
        clean = "node"
    base = clean
    if base in seen:
        seen[base] += 1
        clean = f"{base}_{seen[base]}"
    else:
        seen[base] = 1
    if clean != base:
        seen[clean] = 1
    return clean


def parse_bbox(region_str: str) -> tuple[float, float, float, float]:
    """Parse 'lat_min,lon_min,lat_max,lon_max' string to tuple."""
    parts = region_str.split(",")
    if len(parts) != 4:
        raise ValueError(f"--region must be lat_min,lon_min,lat_max,lon_max, got: {region_str}")
    return tuple(float(p) for p in parts)
