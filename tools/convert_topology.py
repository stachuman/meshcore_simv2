#!/usr/bin/env python3
"""Convert MeshCore topology.json into orchestrator config format.

Usage examples:
  # Basic conversion with default filtering
  python3 convert_topology.py topology.json -o real_network.json

  # Add companions and message schedule
  python3 convert_topology.py topology.json \
    --add-companion alice:GD_Swibno_rpt \
    --add-companion bob:RPT_PRG_02 \
    --msg-schedule alice:bob:30 \
    -o real_network.json

  # Include inferred edges, lower SNR threshold
  python3 convert_topology.py topology.json --include-inferred --min-snr -15 -o out.json

  # Full pipeline: estimate coords, merge bidir, fill gaps
  python3 convert_topology.py topology.json \
    --merge-bidir --fill-gaps -v -o real_network.json
"""

import argparse
import json
import math
import random
import re
import sys
from collections import defaultdict

# ---------------------------------------------------------------------------
# Constants (ported from import_topology.py)
# ---------------------------------------------------------------------------

_SNR_FLOOR = -12.0          # LoRa demodulation floor (dB)
_MAX_GAP_KM = 30.0          # max practical LoRa range (km)
_DEFAULT_SIGMA = 8.0        # dB shadow fading
_MIN_EDGES_FOR_FIT = 5      # min measured edges for model fitting
_MAX_GOOD_LINKS = 3         # cap SNR>0 edges/node
_MAX_EDGES_PER_NODE = 12    # hard cap total edges/node
_MAX_RANGE_FACTOR = 1.5     # auto max range = 1.5x max measured
_DEFAULT_NOISE_FLOOR = -111 # dBm for RSSI computation

# Fallback propagation model (log-distance path loss)
_SNR_REF = 10.0             # dB at reference distance
_D_REF_KM = 1.0             # reference distance in km
_PATH_LOSS_N = 3.0          # path loss exponent


# ---------------------------------------------------------------------------
# Geo helpers
# ---------------------------------------------------------------------------

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres (WGS-84 sphere approximation)."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Coordinate estimation
# ---------------------------------------------------------------------------

def estimate_node_coordinates(
    nodes_in: dict,
    edges_in: list,
    verbose: bool = False,
) -> dict[str, tuple[float, float]]:
    """Estimate coordinates for nodes with lat=0/lon=0 and 8+ char prefix.

    Uses weighted centroid of known-coordinate neighbors.
    Weight = max(1.0, snr + 15) — shifts so -12 dB still gets weight 3.0.

    Returns {prefix: (lat, lon)} for estimated nodes.
    """
    # Identify nodes needing estimation
    need_coords = set()
    for prefix, nd in nodes_in.items():
        if len(prefix) < 8:
            continue
        lat = float(nd.get("lat", 0.0))
        lon = float(nd.get("lon", 0.0))
        if lat == 0.0 and lon == 0.0:
            need_coords.add(prefix)

    if not need_coords:
        return {}

    # Build neighbor map from raw edges (best SNR per neighbor pair)
    # Use "neighbors" source edges preferentially over "inferred"
    best_snr: dict[tuple[str, str], tuple[float, str]] = {}  # (a,b) -> (snr, source)
    for edge in edges_in:
        src = edge["from"]
        dst = edge["to"]
        snr = edge.get("snr_db", 0.0)
        source = edge.get("source", "")
        key = (min(src, dst), max(src, dst))
        if key not in best_snr:
            best_snr[key] = (snr, source)
        else:
            old_snr, old_source = best_snr[key]
            # Prefer neighbors > inferred; within same source, prefer higher SNR
            source_rank = {"neighbors": 2, "trace": 1, "inferred": 0}
            old_rank = source_rank.get(old_source, 0)
            new_rank = source_rank.get(source, 0)
            if new_rank > old_rank or (new_rank == old_rank and snr > old_snr):
                best_snr[key] = (snr, source)

    # Build adjacency: node -> [(neighbor, snr)]
    adjacency: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for (a, b), (snr, _) in best_snr.items():
        adjacency[a].append((b, snr))
        adjacency[b].append((a, snr))

    # Sort by count of known-coord neighbors (most first, handles chains)
    def known_neighbor_count(prefix):
        count = 0
        for nbr, _ in adjacency.get(prefix, []):
            if nbr not in need_coords:
                nd = nodes_in.get(nbr, {})
                lat = float(nd.get("lat", 0.0))
                lon = float(nd.get("lon", 0.0))
                if lat != 0.0 or lon != 0.0:
                    count += 1
        return count

    ordered = sorted(need_coords, key=known_neighbor_count, reverse=True)

    estimated = {}
    # Track which nodes now have coords (start with all non-zero nodes)
    has_coords = set()
    for prefix, nd in nodes_in.items():
        lat = float(nd.get("lat", 0.0))
        lon = float(nd.get("lon", 0.0))
        if lat != 0.0 or lon != 0.0:
            has_coords.add(prefix)

    for prefix in ordered:
        neighbors = adjacency.get(prefix, [])
        sum_wlat = 0.0
        sum_wlon = 0.0
        sum_w = 0.0

        for nbr, snr in neighbors:
            if nbr not in has_coords:
                continue
            nd = nodes_in.get(nbr, {})
            nlat = float(nd.get("lat", 0.0))
            nlon = float(nd.get("lon", 0.0))
            if nlat == 0.0 and nlon == 0.0:
                # Check if we estimated this neighbor already
                if nbr in estimated:
                    nlat, nlon = estimated[nbr]
                else:
                    continue

            w = max(1.0, snr + 15.0)
            sum_wlat += w * nlat
            sum_wlon += w * nlon
            sum_w += w

        if sum_w > 0:
            est_lat = round(sum_wlat / sum_w, 6)
            est_lon = round(sum_wlon / sum_w, 6)
            estimated[prefix] = (est_lat, est_lon)
            has_coords.add(prefix)
            if verbose:
                name = nodes_in[prefix].get("name", prefix)
                print(f"  Estimated coords for {prefix} ({name}): "
                      f"({est_lat}, {est_lon}) from {len([n for n, _ in neighbors if n in has_coords])} neighbors",
                      file=sys.stderr)

    return estimated


# ---------------------------------------------------------------------------
# Propagation model fitting
# ---------------------------------------------------------------------------

def fit_propagation_model(
    edges_in: list,
    nodes_in: dict,
    prefix_to_name: dict[str, str],
    sigma_override: float | None = None,
    verbose: bool = False,
) -> tuple[float, float, float, float]:
    """Fit SNR = a + b * log10(dist_km) from measured edges.

    Works with raw topology.json format (prefix-based, snr_db field).
    Falls back to hardcoded model if < 5 measured edges with distance.

    Returns (a, b, sigma, max_dist_km).
    """
    points = []  # (log10_dist, snr)
    max_dist = 0.0

    for edge in edges_in:
        src = edge["from"]
        dst = edge["to"]
        source = edge.get("source", "")

        # Only use high-confidence measured edges
        if source not in ("neighbors", "trace"):
            continue

        if src not in nodes_in or dst not in nodes_in:
            continue

        n1 = nodes_in[src]
        n2 = nodes_in[dst]
        lat1, lon1 = float(n1.get("lat", 0.0)), float(n1.get("lon", 0.0))
        lat2, lon2 = float(n2.get("lat", 0.0)), float(n2.get("lon", 0.0))

        # Skip nodes without valid coordinates
        if (lat1 == 0.0 and lon1 == 0.0) or (lat2 == 0.0 and lon2 == 0.0):
            continue

        dist = haversine_km(lat1, lon1, lat2, lon2)
        if dist <= 0.01:  # skip co-located
            continue

        snr = float(edge.get("snr_db", 0.0))
        points.append((math.log10(dist), snr))
        max_dist = max(max_dist, dist)

    if len(points) < _MIN_EDGES_FOR_FIT:
        a = _SNR_REF
        b = -10.0 * _PATH_LOSS_N  # = -30.0
        sigma = _DEFAULT_SIGMA
        if max_dist == 0.0:
            max_dist = _MAX_GAP_KM / _MAX_RANGE_FACTOR
        if verbose:
            print(f"  Propagation model: FALLBACK (only {len(points)} measured "
                  f"edges with distance, need {_MIN_EDGES_FOR_FIT})",
                  file=sys.stderr)
            print(f"    SNR = {a:.1f} + {b:.1f}*log10(d), sigma={sigma:.1f} dB",
                  file=sys.stderr)
        return (a, b, sigma, max_dist)

    # Linear regression: SNR = a + b * log10(dist)
    n = len(points)
    sum_x = sum(p[0] for p in points)
    sum_y = sum(p[1] for p in points)
    sum_xx = sum(p[0] ** 2 for p in points)
    sum_xy = sum(p[0] * p[1] for p in points)

    denom = n * sum_xx - sum_x ** 2
    if abs(denom) < 1e-12:
        a = sum_y / n
        b = 0.0
    else:
        b = (n * sum_xy - sum_x * sum_y) / denom
        a = (sum_y - b * sum_x) / n

    # Sigma = stddev of residuals
    residuals = [p[1] - (a + b * p[0]) for p in points]
    variance = sum(r ** 2 for r in residuals) / n
    fitted_sigma = math.sqrt(variance)

    sigma = sigma_override if sigma_override is not None else fitted_sigma
    if sigma < 1.0:
        sigma = 1.0

    if verbose:
        print(f"  Propagation model: SNR = {a:.1f} + {b:.1f}*log10(d), "
              f"sigma={sigma:.1f} dB (fitted from {n} edges)",
              file=sys.stderr)
        print(f"    Max measured distance: {max_dist:.1f} km",
              file=sys.stderr)

    return (a, b, sigma, max_dist)


# ---------------------------------------------------------------------------
# Coordinate validation
# ---------------------------------------------------------------------------

def validate_coordinates(
    nodes_in: dict,
    edges_in: list,
    model_a: float,
    model_b: float,
    sigma: float,
    verbose: bool = False,
) -> list[str]:
    """Flag nodes with suspicious coordinates.

    For each node with 3+ edges to known-position nodes:
    - Compute expected SNR from distance, compare with measured
    - If median residual > 2*sigma: flag as suspicious

    Returns list of suspicious prefixes.
    """
    suspicious = []

    # Build per-node residuals from measured edges
    node_residuals: dict[str, list[float]] = defaultdict(list)

    for edge in edges_in:
        src = edge["from"]
        dst = edge["to"]
        source = edge.get("source", "")
        if source not in ("neighbors", "trace"):
            continue

        if src not in nodes_in or dst not in nodes_in:
            continue

        n1 = nodes_in[src]
        n2 = nodes_in[dst]
        lat1, lon1 = float(n1.get("lat", 0.0)), float(n1.get("lon", 0.0))
        lat2, lon2 = float(n2.get("lat", 0.0)), float(n2.get("lon", 0.0))

        if (lat1 == 0.0 and lon1 == 0.0) or (lat2 == 0.0 and lon2 == 0.0):
            continue

        dist = haversine_km(lat1, lon1, lat2, lon2)
        if dist <= 0.01:
            continue

        snr_measured = float(edge.get("snr_db", 0.0))
        snr_expected = model_a + model_b * math.log10(dist)
        residual = abs(snr_measured - snr_expected)

        node_residuals[src].append(residual)
        node_residuals[dst].append(residual)

    threshold = 2.0 * sigma
    for prefix, residuals in node_residuals.items():
        if len(residuals) < 3:
            continue
        sorted_r = sorted(residuals)
        median = sorted_r[len(sorted_r) // 2]
        if median > threshold:
            suspicious.append(prefix)
            if verbose:
                name = nodes_in[prefix].get("name", prefix)
                print(f"  WARNING: suspicious coords for {prefix} ({name}): "
                      f"median residual={median:.1f} dB > threshold={threshold:.1f} dB "
                      f"({len(residuals)} edges)",
                      file=sys.stderr)

    return suspicious


# ---------------------------------------------------------------------------
# Bidirectional edge merging
# ---------------------------------------------------------------------------

def merge_bidir_edges(
    links: list[dict],
    verbose: bool = False,
) -> list[dict]:
    """Merge directed A->B + B->A pairs into single bidir edges.

    - Both exist: average SNR/RSSI, set bidir=True, alphabetical from/to
    - Only one: keep as-is, bidir=False
    - Already-bidir (companion) edges: pass through unchanged
    - snr_std_dev: use max() of the two directions
    """
    # Separate already-bidir from directed
    bidir_pass = []
    directed = []
    for link in links:
        if link.get("bidir", False):
            bidir_pass.append(link)
        else:
            directed.append(link)

    # Group directed by unordered pair
    pairs: dict[frozenset, list[dict]] = defaultdict(list)
    for link in directed:
        key = frozenset([link["from"], link["to"]])
        pairs[key].append(link)

    merged = list(bidir_pass)  # start with pass-through
    n_bidir = 0
    n_unidir = 0

    for key, group in pairs.items():
        endpoints = sorted(key)
        a_name, b_name = endpoints[0], endpoints[1]

        # Separate A->B and B->A
        a_to_b = [l for l in group if l["from"] == a_name and l["to"] == b_name]
        b_to_a = [l for l in group if l["from"] == b_name and l["to"] == a_name]

        if a_to_b and b_to_a:
            # Merge: average SNR and RSSI
            snr_avg = round((a_to_b[0]["snr"] + b_to_a[0]["snr"]) / 2.0, 2)
            rssi_avg = round((a_to_b[0]["rssi"] + b_to_a[0]["rssi"]) / 2.0, 2)

            link = {
                "from": a_name,
                "to": b_name,
                "snr": snr_avg,
                "rssi": rssi_avg,
                "bidir": True,
            }

            # snr_std_dev: max of both directions
            std_a = a_to_b[0].get("snr_std_dev", 0.0)
            std_b = b_to_a[0].get("snr_std_dev", 0.0)
            std_max = max(std_a, std_b)
            if std_max > 0:
                link["snr_std_dev"] = std_max

            merged.append(link)
            n_bidir += 1
        else:
            # Keep as-is (unidir)
            for l in group:
                merged.append(l)
            n_unidir += len(group)

    if verbose:
        print(f"  Merge bidir: {n_bidir} pairs merged, "
              f"{n_unidir} unidir kept, "
              f"{len(bidir_pass)} pass-through",
              file=sys.stderr)

    return merged


# ---------------------------------------------------------------------------
# Gap-fill: add estimated edges for unmeasured nearby pairs
# ---------------------------------------------------------------------------

def fill_gaps(
    links: list[dict],
    nodes_out: list[dict],
    nodes_in: dict,
    prefix_to_name: dict[str, str],
    max_gap_km: float,
    max_good_links: int,
    gap_sigma: float | None,
    model_a: float,
    model_b: float,
    sigma: float,
    max_measured_dist: float,
    verbose: bool = False,
) -> list[dict]:
    """Add estimated edges for unmeasured nearby node pairs.

    Ported from import_topology.py fill-gap logic.
    """
    # Build name -> prefix lookup
    name_to_prefix = {v: k for k, v in prefix_to_name.items()}

    # Auto-derive max range from measured data
    auto_max_km = max_measured_dist * _MAX_RANGE_FACTOR
    effective_max_km = min(max_gap_km, auto_max_km) if auto_max_km > 0 else max_gap_km

    # Use override sigma if provided
    effective_sigma = gap_sigma if gap_sigma is not None else sigma

    # Seeded RNG for reproducible shadow fading
    rng = random.Random(42)

    # Build existing edge set and edge counts
    edge_set: set[frozenset] = set()
    good_count: dict[str, int] = {}   # prefix -> count of SNR>0 edges
    total_count: dict[str, int] = {}  # prefix -> count of all edges

    for link in links:
        from_name = link["from"]
        to_name = link["to"]
        from_pfx = name_to_prefix.get(from_name)
        to_pfx = name_to_prefix.get(to_name)
        if from_pfx and to_pfx:
            edge_set.add(frozenset([from_pfx, to_pfx]))
            for pfx in (from_pfx, to_pfx):
                total_count[pfx] = total_count.get(pfx, 0) + 1
                if float(link.get("snr", 0.0)) > 0.0:
                    good_count[pfx] = good_count.get(pfx, 0) + 1

    # Only consider repeater nodes with valid coordinates
    repeater_prefixes = []
    for node_def in nodes_out:
        if node_def.get("role") != "repeater":
            continue
        name = node_def["name"]
        pfx = name_to_prefix.get(name)
        if pfx is None:
            continue
        lat = node_def.get("lat", 0.0)
        lon = node_def.get("lon", 0.0)
        if lat == 0.0 and lon == 0.0:
            continue
        repeater_prefixes.append(pfx)

    repeater_prefixes.sort()
    gap_filled = 0
    new_links = []

    # Build global candidate list sorted by distance (closest first).
    # This ensures close pairs get connected before distant pairs eat up caps.
    all_candidates = []
    for i, p1 in enumerate(repeater_prefixes):
        n1 = nodes_in[p1]
        lat1 = float(n1.get("lat", 0.0))
        lon1 = float(n1.get("lon", 0.0))
        for p2 in repeater_prefixes[i + 1:]:
            pair = frozenset([p1, p2])
            if pair in edge_set:
                continue
            n2 = nodes_in[p2]
            lat2 = float(n2.get("lat", 0.0))
            lon2 = float(n2.get("lon", 0.0))
            dist = haversine_km(lat1, lon1, lat2, lon2)
            if dist > effective_max_km or dist <= 0.01:
                continue
            all_candidates.append((dist, p1, p2))

    all_candidates.sort()  # closest pairs first globally

    for dist, p1, p2 in all_candidates:
        t1 = total_count.get(p1, 0)
        t2 = total_count.get(p2, 0)
        if t1 >= _MAX_EDGES_PER_NODE or t2 >= _MAX_EDGES_PER_NODE:
            continue

        # Estimate SNR with shadow fading
        snr_est = model_a + model_b * math.log10(dist) \
            + rng.gauss(0.0, effective_sigma)

        if snr_est <= _SNR_FLOOR:
            continue

        # If this would be a "good" link (SNR > 0), check the cap
        if snr_est > 0.0:
            g1 = good_count.get(p1, 0)
            g2 = good_count.get(p2, 0)
            if g1 >= max_good_links or g2 >= max_good_links:
                continue

        # Compute RSSI from noise floor + estimated SNR
        rssi = max(-140.0, min(-20.0, _DEFAULT_NOISE_FLOOR + snr_est))

        new_links.append({
            "from": prefix_to_name[p1],
            "to": prefix_to_name[p2],
            "snr": round(snr_est, 2),
            "rssi": round(rssi, 2),
            "bidir": True,
        })
        edge_set.add(frozenset([p1, p2]))
        total_count[p1] = t1 + 1
        total_count[p2] = t2 + 1
        if snr_est > 0.0:
            good_count[p1] = good_count.get(p1, 0) + 1
            good_count[p2] = good_count.get(p2, 0) + 1
        gap_filled += 1

    if verbose:
        print(f"  Gap-fill: {gap_filled} estimated edges added "
              f"(max {max_good_links} good links/node, "
              f"max {effective_max_km:.1f} km, "
              f"SNR floor {_SNR_FLOOR} dB)",
              file=sys.stderr)

    return links + new_links


# ---------------------------------------------------------------------------
# Existing helpers
# ---------------------------------------------------------------------------

def sanitize_name(name: str, seen: dict[str, int]) -> str:
    """Strip emoji/non-ASCII, replace spaces, truncate, ensure uniqueness."""
    # Remove non-ASCII characters (emoji, special chars)
    clean = name.encode("ascii", "ignore").decode("ascii").strip()
    # Replace spaces and runs of underscores
    clean = re.sub(r"[\s]+", "_", clean)
    # Remove anything that isn't alphanumeric or underscore
    clean = re.sub(r"[^A-Za-z0-9_]", "", clean)
    # Truncate
    clean = clean[:20]
    # Fallback for empty names
    if not clean:
        clean = "node"
    # Ensure uniqueness
    base = clean
    if base in seen:
        seen[base] += 1
        clean = f"{base}_{seen[base]}"
    else:
        seen[base] = 1
    # Re-check after suffix (edge case: "node_2" already exists)
    while clean in seen and seen.get(clean, 0) > 0 and clean != base:
        seen[base] += 1
        clean = f"{base}_{seen[base]}"
    if clean != base:
        seen[clean] = 1
    return clean


def parse_companion_arg(arg: str) -> dict:
    """Parse --add-companion name:repeater[:snr[:rssi]]"""
    parts = arg.split(":")
    if len(parts) < 2:
        raise ValueError(f"--add-companion requires at least name:repeater_name, got: {arg}")
    result = {"name": parts[0], "repeater": parts[1]}
    if len(parts) >= 3:
        result["snr"] = float(parts[2])
    if len(parts) >= 4:
        result["rssi"] = float(parts[3])
    return result


def parse_msg_schedule_arg(arg: str) -> dict:
    """Parse --msg-schedule from:to:interval_s"""
    parts = arg.split(":")
    if len(parts) < 3:
        raise ValueError(f"--msg-schedule requires from:to:interval_s, got: {arg}")
    return {
        "from": parts[0],
        "to": parts[1],
        "interval_s": float(parts[2]),
    }


# ---------------------------------------------------------------------------
# Main conversion pipeline
# ---------------------------------------------------------------------------

def convert(args):
    with open(args.input, "r") as f:
        topo = json.load(f)

    nodes_in = topo.get("nodes", {})
    edges_in = topo.get("edges", [])

    # --- [1] Node processing ---
    seen_names: dict[str, int] = {}
    prefix_to_name: dict[str, str] = {}  # prefix -> sanitized name
    node_noise_floor: dict[str, int] = {}  # prefix -> noise_floor
    skipped_prefix = 0

    nodes_out = []
    for prefix, nd in nodes_in.items():
        # Drop stub nodes with short prefixes
        if len(prefix) < 8:
            skipped_prefix += 1
            continue

        raw_name = nd.get("name", prefix)
        sname = sanitize_name(raw_name, seen_names)
        prefix_to_name[prefix] = sname

        nf = nd.get("status", {}).get("noise_floor")
        if nf is not None:
            node_noise_floor[prefix] = int(nf)

        node_def = {"name": sname, "role": "repeater"}
        if "lat" in nd and "lon" in nd:
            node_def["lat"] = nd["lat"]
            node_def["lon"] = nd["lon"]
        nodes_out.append(node_def)

        if raw_name != sname:
            print(f"  {prefix} : {raw_name!r} -> {sname}", file=sys.stderr)

    if skipped_prefix and args.verbose:
        print(f"  Skipped {skipped_prefix} stub nodes (short prefix)", file=sys.stderr)

    # --- [2] Estimate coordinates (--estimate-coords, default ON) ---
    estimated_coords = {}
    if args.estimate_coords:
        estimated_coords = estimate_node_coordinates(
            nodes_in, edges_in, verbose=args.verbose)

        # Apply estimated coordinates to nodes_in and nodes_out
        for prefix, (est_lat, est_lon) in estimated_coords.items():
            nodes_in[prefix]["lat"] = est_lat
            nodes_in[prefix]["lon"] = est_lon
            # Update nodes_out
            name = prefix_to_name.get(prefix)
            if name:
                for nd in nodes_out:
                    if nd["name"] == name:
                        nd["lat"] = est_lat
                        nd["lon"] = est_lon
                        break

        if estimated_coords and args.verbose:
            print(f"  Coordinate estimation: {len(estimated_coords)} nodes estimated",
                  file=sys.stderr)

    # Remove nodes that still have missing coordinates (0, 0)
    skipped_coords = 0
    filtered_nodes = []
    filtered_names = set()
    for nd in nodes_out:
        lat = nd.get("lat", 0.0)
        lon = nd.get("lon", 0.0)
        if lat == 0.0 and lon == 0.0:
            skipped_coords += 1
            # Also remove from prefix_to_name so edges referencing them get dropped
            name = nd["name"]
            filtered_names.add(name)
            continue
        filtered_nodes.append(nd)
    nodes_out = filtered_nodes

    if filtered_names:
        prefix_to_name = {p: n for p, n in prefix_to_name.items()
                          if n not in filtered_names}

    if skipped_coords and args.verbose:
        print(f"  Dropped {skipped_coords} nodes with missing coordinates",
              file=sys.stderr)

    # Resolve repeater names for companion injection (match against sanitized names)
    sanitized_to_prefix = {v: k for k, v in prefix_to_name.items()}
    all_sanitized_names = set(prefix_to_name.values())

    # --- [3] Edge filtering and conversion ---
    total_edges = len(edges_in)
    filtered_edges = 0
    links_out = []

    for edge in edges_in:
        src_prefix = edge["from"]
        dst_prefix = edge["to"]
        snr_db = edge.get("snr_db", 0.0)
        confidence = edge.get("confidence", 1.0)
        source = edge.get("source", "")
        snr_min_db = edge.get("snr_min_db")

        # Skip unknown nodes
        if src_prefix not in prefix_to_name or dst_prefix not in prefix_to_name:
            filtered_edges += 1
            continue

        # Filter by source
        if source == "inferred" and not args.include_inferred:
            filtered_edges += 1
            continue

        # Filter by confidence
        if confidence < args.min_confidence:
            filtered_edges += 1
            continue

        # Filter by SNR
        if snr_db < args.min_snr:
            filtered_edges += 1
            continue

        # Compute RSSI from receiver noise floor + SNR
        receiver_nf = node_noise_floor.get(dst_prefix, _DEFAULT_NOISE_FLOOR)
        rssi = max(-140.0, min(-20.0, receiver_nf + snr_db))

        # Compute SNR variance
        snr_std_dev = 0.0
        if snr_min_db is not None and snr_min_db != snr_db:
            snr_std_dev = round((snr_db - snr_min_db) / 2.0, 2)
            if snr_std_dev < 0:
                snr_std_dev = 0.0

        link = {
            "from": prefix_to_name[src_prefix],
            "to": prefix_to_name[dst_prefix],
            "snr": round(snr_db, 2),
            "rssi": round(rssi, 2),
            "bidir": False,
        }
        if snr_std_dev > 0:
            link["snr_std_dev"] = snr_std_dev
        links_out.append(link)

    surviving_edges = total_edges - filtered_edges

    # --- [4] Fit propagation model (if --fill-gaps or --validate-coords) ---
    model_a = model_b = sigma = max_measured_dist = 0.0
    if args.fill_gaps or args.validate_coords:
        model_a, model_b, sigma, max_measured_dist = fit_propagation_model(
            edges_in, nodes_in, prefix_to_name,
            sigma_override=args.gap_sigma,
            verbose=args.verbose,
        )

    # --- [5] Validate coordinates (--validate-coords, default OFF) ---
    if args.validate_coords:
        suspicious = validate_coordinates(
            nodes_in, edges_in, model_a, model_b, sigma,
            verbose=args.verbose,
        )
        if suspicious and args.verbose:
            print(f"  Coordinate validation: {len(suspicious)} suspicious nodes",
                  file=sys.stderr)

    # --- [6] Merge bidir edges (--merge-bidir, default OFF) ---
    if args.merge_bidir:
        links_out = merge_bidir_edges(links_out, verbose=args.verbose)

    # --- [7] Fill gaps (--fill-gaps, default ON) ---
    if args.fill_gaps:
        links_out = fill_gaps(
            links_out, nodes_out, nodes_in, prefix_to_name,
            max_gap_km=args.max_gap_km,
            max_good_links=args.max_good_links,
            gap_sigma=args.gap_sigma,
            model_a=model_a,
            model_b=model_b,
            sigma=sigma,
            max_measured_dist=max_measured_dist,
            verbose=args.verbose,
        )

    # --- [8] Companion injection ---
    companions_added = []
    for comp_arg in (args.add_companion or []):
        comp = parse_companion_arg(comp_arg)
        comp_name = comp["name"]
        rpt_name = comp["repeater"]

        # Verify repeater exists (match against original or sanitized names)
        if rpt_name not in all_sanitized_names:
            # Try matching against original names
            found = False
            for prefix, nd in nodes_in.items():
                if nd.get("name", "") == rpt_name:
                    rpt_name = prefix_to_name[prefix]
                    found = True
                    break
            if not found:
                print(f"WARNING: repeater {comp['repeater']!r} not found, skipping companion {comp_name!r}",
                      file=sys.stderr)
                continue

        # Ensure companion name doesn't collide
        if comp_name in all_sanitized_names:
            print(f"WARNING: companion name {comp_name!r} collides with existing node, appending _c",
                  file=sys.stderr)
            comp_name = comp_name + "_c"

        snr = comp.get("snr", 10.0)
        rssi = comp.get("rssi", -70.0)

        nodes_out.append({"name": comp_name, "role": "companion"})
        all_sanitized_names.add(comp_name)
        links_out.append({
            "from": comp_name,
            "to": rpt_name,
            "snr": snr,
            "rssi": rssi,
            "bidir": True,
        })
        companions_added.append(f"{comp_name} -> {rpt_name}")

    # --- Message schedule ---
    warmup_ms = args.warmup
    duration_ms = args.duration
    msg_schedules = []
    for sched_arg in (args.msg_schedule or []):
        sched = parse_msg_schedule_arg(sched_arg)
        start_ms = warmup_ms + 5000
        interval_ms = int(sched["interval_s"] * 1000)
        msg_schedules.append({
            "from": sched["from"],
            "to": sched["to"],
            "start_ms": start_ms,
            "interval_ms": interval_ms,
        })

    # --- [9] Assemble output ---
    config = {
        "_source": f"Converted from {args.input}",
        "simulation": {
            "duration_ms": duration_ms,
            "step_ms": args.step,
            "warmup_ms": warmup_ms,
            "hot_start": args.hot_start,
            "hot_start_settle_ms": args.hot_start_settle,
        },
        "nodes": nodes_out,
        "topology": {
            "links": links_out,
        },
        "commands": [],
    }

    if msg_schedules:
        config["message_schedule"] = msg_schedules

    # --- Summary to stderr ---
    print(f"\n--- Conversion Summary ---", file=sys.stderr)
    print(f"Nodes: {len([n for n in nodes_out if n['role'] == 'repeater'])} repeaters", file=sys.stderr)
    if estimated_coords:
        print(f"Estimated coordinates: {len(estimated_coords)} nodes", file=sys.stderr)
    if companions_added:
        print(f"Companions: {len(companions_added)} added", file=sys.stderr)
        for c in companions_added:
            print(f"  {c}", file=sys.stderr)
    print(f"Edges: {total_edges} total, {filtered_edges} filtered, {surviving_edges} surviving",
          file=sys.stderr)
    if args.merge_bidir:
        print(f"Bidir merge: enabled", file=sys.stderr)
    if args.fill_gaps:
        gap_count = len(links_out) - surviving_edges
        if args.merge_bidir:
            gap_count = len([l for l in links_out if l.get("bidir", False)
                             and l.get("snr", 0) != 10.0]) - surviving_edges
        print(f"Gap-fill: enabled", file=sys.stderr)
    if msg_schedules:
        print(f"Message schedules: {len(msg_schedules)}", file=sys.stderr)
    print(f"Total links in output: {len(links_out)}", file=sys.stderr)
    print(f"Filters: min_snr={args.min_snr}, min_confidence={args.min_confidence}, "
          f"include_inferred={args.include_inferred}", file=sys.stderr)

    # --- Output ---
    output_str = json.dumps(config, indent=2)
    if args.output:
        with open(args.output, "w") as f:
            f.write(output_str + "\n")
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        print(output_str)


def main():
    parser = argparse.ArgumentParser(
        description="Convert MeshCore topology.json to orchestrator config format"
    )
    parser.add_argument("input", help="Path to topology.json")
    parser.add_argument("-o", "--output", help="Output file (default: stdout)")

    # Edge filtering
    parser.add_argument("--min-snr", type=float, default=-10.0,
                        help="Drop edges with snr_db below this (default: -10.0, SF8 threshold)")
    parser.add_argument("--min-confidence", type=float, default=0.7,
                        help="Drop edges with confidence below this (default: 0.7)")
    parser.add_argument("--include-inferred", action="store_true", default=False,
                        help="Include edges with source='inferred' (default: drop them)")

    # Coordinate handling
    parser.add_argument("--estimate-coords", action="store_true", default=True,
                        help="Estimate coords for lat=0/lon=0 nodes (default: on)")
    parser.add_argument("--no-estimate-coords", action="store_false", dest="estimate_coords",
                        help="Disable coordinate estimation")
    parser.add_argument("--validate-coords", action="store_true", default=False,
                        help="Flag nodes with suspicious coordinates")

    # Edge merging
    parser.add_argument("--merge-bidir", action="store_true", default=False,
                        help="Merge A->B + B->A pairs into single bidir edges")

    # Gap-fill
    parser.add_argument("--fill-gaps", action="store_true", default=True,
                        help="Add estimated edges for unmeasured nearby pairs (default: on)")
    parser.add_argument("--no-fill-gaps", action="store_false", dest="fill_gaps",
                        help="Disable gap-fill")
    parser.add_argument("--max-gap-km", type=float, default=_MAX_GAP_KM,
                        help=f"Max distance for gap-fill (default: {_MAX_GAP_KM})")
    parser.add_argument("--max-good-links", type=int, default=_MAX_GOOD_LINKS,
                        help=f"Max SNR>0 edges per node (default: {_MAX_GOOD_LINKS})")
    parser.add_argument("--gap-sigma", type=float, default=None,
                        help="Override shadow fading sigma (dB)")

    # Companion injection
    parser.add_argument("--add-companion", action="append",
                        help="Add companion: name:repeater_name[:snr[:rssi]] (repeatable)")

    # Message schedule
    parser.add_argument("--msg-schedule", action="append",
                        help="Add message schedule: from:to:interval_s (repeatable)")

    # Simulation defaults
    parser.add_argument("--duration", type=int, default=300000,
                        help="Simulation duration in ms (default: 300000)")
    parser.add_argument("--step", type=int, default=5,
                        help="Simulation step in ms (default: 5)")
    parser.add_argument("--warmup", type=int, default=5000,
                        help="Warmup period in ms (default: 5000)")
    parser.add_argument("--hot-start", type=bool, default=True,
                        help="Enable hot start (default: true)")
    parser.add_argument("--hot-start-settle", type=int, default=15000,
                        help="Hot start settle time in ms (default: 15000)")

    # Output
    parser.add_argument("-v", "--verbose", action="store_true", default=False,
                        help="Print detailed statistics to stderr")

    args = parser.parse_args()
    convert(args)


if __name__ == "__main__":
    main()
