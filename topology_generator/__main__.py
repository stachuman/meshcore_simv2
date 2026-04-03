#!/usr/bin/env python3
"""Generate MeshCore simulation topology using ITM propagation model.

Downloads real node positions from the MeshCore map API, SRTM terrain data,
and computes physics-based RF link quality using the Longley-Rice model.

Usage:
    python3 -m topology_generator --region 53.8,17.5,54.8,19.5 -v -o gdansk.json
"""

import argparse
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed

from .api_fetch import (
    DEFAULT_API_URL,
    fetch_nodes,
    filter_nodes,
    parse_bbox,
    sanitize_name,
)
from .config_emitter import build_config, emit_config
from .propagation import compute_link, noise_floor_dbm
from .terrain import get_elevation_data, haversine_km, sample_elevation_profile

# Edge caps (same defaults as convert_topology.py)
_MAX_EDGES_PER_NODE = 8
_MAX_GOOD_LINKS = 3


def _compute_link_worker(args):
    """Worker function for ProcessPoolExecutor (must be top-level picklable)."""
    (profile, dist_km, freq_mhz, antenna_heights,
     tx_power, bw_hz, nf_db, min_snr, climate,
     polarization, eps, sgm, refractivity, clutter_db) = args
    return compute_link(
        profile, dist_km, freq_mhz, antenna_heights,
        tx_power, bw_hz, nf_db, min_snr, climate,
        polarization, eps, sgm, refractivity, clutter_db,
    )


def select_links(raw_links, max_edges, max_good, verbose=False):
    """Select links with per-node edge caps, closest-first priority.

    Sorts all candidate links by distance (ascending), then walks through
    and keeps a link only if both endpoints are below their edge caps.

    This matches convert_topology.py's gap-fill strategy:
    - max_edges: hard cap on total edges per node
    - max_good: cap on SNR > 0 edges per node (prevents over-optimistic density)
    """
    # Sort by distance (closest first)
    sorted_links = sorted(raw_links, key=lambda l: l.get("dist_km", 0))

    total_count = defaultdict(int)
    good_count = defaultdict(int)
    selected = []

    for link in sorted_links:
        a = link["from"]
        b = link["to"]

        # Check total edge caps
        if total_count[a] >= max_edges or total_count[b] >= max_edges:
            continue

        # Check good-link caps (SNR > 0)
        snr = link["snr"]
        if snr > 0.0:
            if good_count[a] >= max_good or good_count[b] >= max_good:
                continue

        selected.append(link)
        total_count[a] += 1
        total_count[b] += 1
        if snr > 0.0:
            good_count[a] += 1
            good_count[b] += 1

    if verbose:
        edges = list(total_count.values())
        if edges:
            edges.sort()
            print(f"  Edges/node: min={edges[0]}, median={edges[len(edges)//2]}, "
                  f"max={edges[-1]}, mean={sum(edges)/len(edges):.1f}",
                  file=sys.stderr)

    return selected


def check_connectivity(links, node_names, verbose=False):
    """Check graph connectivity, warn about islands."""
    adj = defaultdict(set)
    for link in links:
        adj[link["from"]].add(link["to"])
        adj[link["to"]].add(link["from"])

    visited = set()
    components = []
    for name in node_names:
        if name in visited:
            continue
        component = set()
        queue = [name]
        while queue:
            n = queue.pop()
            if n in visited:
                continue
            visited.add(n)
            component.add(n)
            queue.extend(adj.get(n, set()) - visited)
        components.append(component)

    components.sort(key=len, reverse=True)
    if len(components) > 1:
        print(f"  WARNING: {len(components)} disconnected components:", file=sys.stderr)
        for i, comp in enumerate(components[:5]):
            print(f"    Component {i}: {len(comp)} nodes", file=sys.stderr)
        if len(components) > 5:
            print(f"    ... and {len(components)-5} more", file=sys.stderr)
    else:
        print(f"  Graph is connected ({len(node_names)} nodes)", file=sys.stderr)

    return components


def main():
    parser = argparse.ArgumentParser(
        description="Generate MeshCore topology using ITM propagation model"
    )

    # Region
    parser.add_argument("--region", required=True,
                        help="Bounding box: lat_min,lon_min,lat_max,lon_max")

    # API
    parser.add_argument("--api-url", default=DEFAULT_API_URL,
                        help=f"MeshCore map API URL (default: {DEFAULT_API_URL})")
    parser.add_argument("--api-cache", default=None,
                        help="Cache API response to this file")
    parser.add_argument("--node-types", default="2,3",
                        help="Node types to include: comma-separated (1=companion, 2=repeater, 3=gateway)")

    # RF parameters
    parser.add_argument("--freq-mhz", type=float, default=869.618,
                        help="Operating frequency in MHz (default: 869.618 EU ISM)")
    parser.add_argument("--tx-power-dbm", type=float, default=22.0,
                        help="TX power in dBm (default: 22)")
    parser.add_argument("--antenna-height", type=float, default=5.0,
                        help="Antenna height AGL in meters (default: 5)")
    parser.add_argument("--noise-figure-db", type=float, default=6.0,
                        help="Receiver noise figure in dB (default: 6)")
    parser.add_argument("--sf", type=int, default=8,
                        help="LoRa spreading factor (default: 8)")
    parser.add_argument("--bw", type=int, default=62500,
                        help="LoRa bandwidth in Hz (default: 62500)")
    parser.add_argument("--cr", type=int, default=4,
                        help="LoRa coding rate (default: 4)")

    # ITM parameters
    parser.add_argument("--climate", type=int, default=6,
                        help="ITM climate: 1=equat, 2=cont-subtrop, 3=mar-subtrop, "
                             "4=desert, 5=cont-temp, 6=mar-temp-land, 7=mar-temp-sea (default: 6)")
    parser.add_argument("--polarization", type=int, default=1,
                        help="Polarization: 0=horizontal, 1=vertical (default: 1)")
    parser.add_argument("--profile-points", type=int, default=150,
                        help="Elevation samples per link (default: 150)")
    parser.add_argument("--clutter-db", type=float, default=6.0,
                        help="Extra path loss for urban clutter, cables, connectors (default: 6)")

    # Link filtering
    parser.add_argument("--max-distance-km", type=float, default=30.0,
                        help="Max distance for candidate pairs in km (default: 30)")
    parser.add_argument("--min-snr", type=float, default=-10.0,
                        help="Drop links below this SNR in dB (default: -10)")
    parser.add_argument("--max-edges-per-node", type=int, default=_MAX_EDGES_PER_NODE,
                        help=f"Hard cap total edges per node (default: {_MAX_EDGES_PER_NODE})")
    parser.add_argument("--max-good-links", type=int, default=_MAX_GOOD_LINKS,
                        help=f"Cap SNR>0 edges per node (default: {_MAX_GOOD_LINKS})")

    # Simulation
    parser.add_argument("--duration", type=int, default=300000,
                        help="Simulation duration in ms (default: 300000)")
    parser.add_argument("--step", type=int, default=5,
                        help="Simulation step in ms (default: 5)")
    parser.add_argument("--warmup", type=int, default=5000,
                        help="Warmup in ms (default: 5000)")
    parser.add_argument("--hot-start", action="store_true", default=True)
    parser.add_argument("--no-hot-start", action="store_false", dest="hot_start")

    # Parallelism
    parser.add_argument("--workers", type=int, default=1,
                        help="Parallel workers for ITM computation (default: 1)")

    # Output
    parser.add_argument("-o", "--output", help="Output file (default: stdout)")
    parser.add_argument("-v", "--verbose", action="store_true", default=False)

    args = parser.parse_args()

    t0 = time.time()

    # --- Step 1: Fetch nodes ---
    print("Step 1: Fetching nodes from API...", file=sys.stderr)
    raw_nodes = fetch_nodes(args.api_url, cache_path=args.api_cache)
    print(f"  {len(raw_nodes)} nodes total", file=sys.stderr)

    # --- Step 2: Filter by region + type ---
    bbox = parse_bbox(args.region)
    node_types = {int(t) for t in args.node_types.split(",")}
    nodes = filter_nodes(raw_nodes, bbox=bbox, node_types=node_types)
    print(f"Step 2: {len(nodes)} nodes in region (types: {node_types})", file=sys.stderr)

    if not nodes:
        print("ERROR: No nodes found in region. Check --region bounds.", file=sys.stderr)
        sys.exit(1)

    # --- Step 3: Sanitize names ---
    seen_names = {}
    for node in nodes:
        node["_name"] = sanitize_name(node.get("adv_name", "node"), seen_names)

    # Build output node list
    nodes_out = []
    name_set = set()
    for node in nodes:
        ntype = node.get("type", 2)
        role = "companion" if ntype == 1 else "repeater"
        nd = {
            "name": node["_name"],
            "role": role,
            "lat": node["adv_lat"],
            "lon": node["adv_lon"],
        }
        nodes_out.append(nd)
        name_set.add(node["_name"])

    # --- Step 4: Generate candidate pairs ---
    print("Step 3: Computing candidate pairs...", file=sys.stderr)
    pairs = []
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            a, b = nodes[i], nodes[j]
            dist = haversine_km(a["adv_lat"], a["adv_lon"],
                                b["adv_lat"], b["adv_lon"])
            if 0.01 < dist <= args.max_distance_km:
                pairs.append((i, j, dist))
    print(f"  {len(pairs)} candidate pairs (max {args.max_distance_km} km)", file=sys.stderr)

    if not pairs:
        print("WARNING: No candidate pairs. All nodes may be isolated or too far apart.",
              file=sys.stderr)

    # --- Step 5: Sample terrain profiles ---
    print("Step 4: Sampling terrain profiles (SRTM)...", file=sys.stderr)
    elev_data = get_elevation_data()

    profiles = {}
    for idx, (i, j, dist) in enumerate(pairs):
        a, b = nodes[i], nodes[j]
        profile = sample_elevation_profile(
            elev_data, a["adv_lat"], a["adv_lon"],
            b["adv_lat"], b["adv_lon"], args.profile_points,
        )
        profiles[(i, j)] = profile
        if args.verbose and (idx + 1) % 1000 == 0:
            print(f"  Sampled {idx+1}/{len(pairs)} profiles...", file=sys.stderr)

    print(f"  {len(profiles)} terrain profiles sampled", file=sys.stderr)

    # --- Step 6: Run ITM ---
    print("Step 5: Computing ITM propagation...", file=sys.stderr)
    antenna_heights = (args.antenna_height, args.antenna_height)
    nf_val = noise_floor_dbm(float(args.bw), args.noise_figure_db)
    raw_links = []

    if args.workers > 1:
        # Parallel ITM computation
        work_items = []
        pair_indices = []
        for (i, j, dist) in pairs:
            work_items.append((
                profiles[(i, j)], dist, args.freq_mhz, antenna_heights,
                args.tx_power_dbm, float(args.bw), args.noise_figure_db,
                args.min_snr, args.climate, args.polarization,
                15.0, 0.005, 314.0, args.clutter_db,
            ))
            pair_indices.append((i, j))

        completed = 0
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(_compute_link_worker, w): idx
                       for idx, w in enumerate(work_items)}
            for future in as_completed(futures):
                idx = futures[future]
                completed += 1
                if args.verbose and completed % 1000 == 0:
                    print(f"  Computed {completed}/{len(pairs)} links...",
                          file=sys.stderr)
                result = future.result()
                if result is not None:
                    i, j = pair_indices[idx]
                    link = {
                        "from": nodes[i]["_name"],
                        "to": nodes[j]["_name"],
                        **result,
                        "bidir": True,
                    }
                    raw_links.append(link)
    else:
        # Sequential
        for idx, (i, j, dist) in enumerate(pairs):
            result = compute_link(
                profiles[(i, j)], dist, args.freq_mhz, antenna_heights,
                args.tx_power_dbm, float(args.bw), args.noise_figure_db,
                args.min_snr, args.climate, args.polarization,
                clutter_db=args.clutter_db,
            )
            if result is not None:
                link = {
                    "from": nodes[i]["_name"],
                    "to": nodes[j]["_name"],
                    **result,
                    "bidir": True,
                }
                raw_links.append(link)
            if args.verbose and (idx + 1) % 500 == 0:
                print(f"  Computed {idx+1}/{len(pairs)} links...", file=sys.stderr)

    print(f"  {len(raw_links)} viable links (SNR >= {args.min_snr} dB)", file=sys.stderr)

    # --- Step 7: Apply edge caps (closest-first priority) ---
    print("Step 6: Applying edge caps...", file=sys.stderr)
    links_out = select_links(
        raw_links, args.max_edges_per_node, args.max_good_links,
        verbose=args.verbose,
    )
    print(f"  {len(links_out)} links after caps "
          f"(max {args.max_edges_per_node} edges/node, "
          f"max {args.max_good_links} good/node)", file=sys.stderr)

    # --- Step 8: Link statistics ---
    if links_out:
        snrs = [l["snr"] for l in links_out]
        dists = [l.get("dist_km", 0) for l in links_out]
        print(f"  SNR range: {min(snrs):.1f} to {max(snrs):.1f} dB, "
              f"median: {sorted(snrs)[len(snrs)//2]:.1f} dB", file=sys.stderr)
        print(f"  Distance range: {min(dists):.1f} to {max(dists):.1f} km, "
              f"median: {sorted(dists)[len(dists)//2]:.1f} km", file=sys.stderr)

    # Strip dist_km from output links (internal field, not needed by orchestrator)
    for link in links_out:
        link.pop("dist_km", None)

    # --- Step 9: Connectivity check ---
    check_connectivity(links_out, name_set, verbose=args.verbose)

    # --- Step 10: Assemble and emit ---
    config = build_config(
        nodes=nodes_out,
        links=links_out,
        sim_params={
            "duration_ms": args.duration,
            "step_ms": args.step,
            "warmup_ms": args.warmup,
            "hot_start": args.hot_start,
        },
        radio_params={"sf": args.sf, "bw": args.bw, "cr": args.cr},
        source_description=f"ITM topology generator, region={args.region}",
    )

    emit_config(config, args.output)

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
