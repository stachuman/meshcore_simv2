#!/usr/bin/env python3
"""Print statistics from a raw topology.json (real measurement data).

This is the format exported from MeshCore network scanners, with nodes
keyed by prefix and directed edges with SNR, source, confidence.

Generate a topology first (not committed to the repo):
  python3 -m topology_generator --region 53.7,17.3,54.8,19.5 -o simulation/topology.json

Usage:
  python3 tools/raw_topology_stats.py simulation/topology.json
  python3 tools/raw_topology_stats.py simulation/topology.json --snr-threshold 3 --exclude-inferred
"""

import argparse
import json
import re
import sys
from collections import defaultdict


def sanitize_name(name):
    """Strip emoji and special chars for display."""
    return re.sub(r'[^\x20-\x7E]', '', name).strip().rstrip('*')


def main():
    parser = argparse.ArgumentParser(
        description="Statistics from raw topology.json measurement data"
    )
    parser.add_argument("topology", help="Raw topology.json file")
    parser.add_argument("--snr-threshold", type=float, default=0.0,
                        help="Count neighbors with SNR above this (default: 0.0)")
    parser.add_argument("--exclude-inferred", action="store_true", default=False,
                        help="Exclude inferred edges (default: include all)")
    parser.add_argument("--min-confidence", type=float, default=0.0,
                        help="Minimum edge confidence (default: 0.0)")
    parser.add_argument("--show-edges", action="store_true", default=False,
                        help="List all edges per node")

    args = parser.parse_args()

    with open(args.topology) as f:
        data = json.load(f)

    nodes = data["nodes"]
    edges = data["edges"]

    # Build prefix -> name mapping
    prefix_name = {}
    for prefix, info in nodes.items():
        prefix_name[prefix] = sanitize_name(info.get("name", prefix))

    # Filter edges
    filtered = []
    source_counts = defaultdict(int)
    for e in edges:
        source_counts[e.get("source", "?")] += 1
        if args.exclude_inferred and e.get("source") == "inferred":
            continue
        if e.get("confidence", 1.0) < args.min_confidence:
            continue
        filtered.append(e)

    # Count neighbors per node (directed: A->B means A heard B)
    # "neighbor" = node I can hear with SNR > threshold
    heard_by = defaultdict(list)  # node -> [(neighbor, snr, source)]
    for e in filtered:
        fr = e["from"]
        to = e["to"]
        snr = e.get("snr_db", 0.0)
        source = e.get("source", "?")
        if snr > args.snr_threshold:
            heard_by[fr].append((to, snr, source))

    # Build stats per node
    stats = []
    for prefix, info in nodes.items():
        name = prefix_name[prefix]
        neighbors = heard_by.get(prefix, [])
        n_count = len(neighbors)
        snrs = [s for _, s, _ in neighbors]
        has_coords = info.get("lat", 0) != 0 or info.get("lon", 0) != 0
        has_status = "status" in info

        # Count by source type
        src_counts = defaultdict(int)
        for _, _, src in neighbors:
            src_counts[src] += 1

        stats.append({
            "prefix": prefix,
            "name": name,
            "neighbors": n_count,
            "avg_snr": sum(snrs) / len(snrs) if snrs else 0.0,
            "min_snr": min(snrs) if snrs else 0.0,
            "max_snr": max(snrs) if snrs else 0.0,
            "has_coords": has_coords,
            "has_status": has_status,
            "src_neighbors": src_counts.get("neighbors", 0),
            "src_trace": src_counts.get("trace", 0),
            "src_inferred": src_counts.get("inferred", 0),
            "edges": neighbors,
        })

    stats.sort(key=lambda s: (-s["neighbors"], s["name"]))

    # Print per-node table
    print(f"{'Node':<30} {'Prefix':<10} {'Nbrs':>4} {'Avg SNR':>8} "
          f"{'Min SNR':>8} {'Max SNR':>8}  {'nbr':>3} {'trc':>3} {'inf':>3}  {'GPS':>3} {'Stat':>4}")
    print(f"{'-'*30} {'-'*10} {'-'*4} {'-'*8} "
          f"{'-'*8} {'-'*8}  {'-'*3} {'-'*3} {'-'*3}  {'-'*3} {'-'*4}")
    for s in stats:
        gps = "yes" if s["has_coords"] else " - "
        stat = "yes" if s["has_status"] else "  - "
        print(f"{s['name']:<30} {s['prefix']:<10} {s['neighbors']:>4} "
              f"{s['avg_snr']:>7.1f} {s['min_snr']:>8.1f} {s['max_snr']:>8.1f}  "
              f"{s['src_neighbors']:>3} {s['src_trace']:>3} {s['src_inferred']:>3}  "
              f"{gps:>3} {stat:>4}")

        if args.show_edges and s["edges"]:
            for to_prefix, snr, src in sorted(s["edges"], key=lambda x: -x[1]):
                to_name = prefix_name.get(to_prefix, to_prefix)
                print(f"  -> {to_name:<28} SNR={snr:>6.1f}  ({src})")

    # Histogram
    counts = [s["neighbors"] for s in stats]
    if not counts:
        print("\nNo nodes found.", file=sys.stderr)
        return

    max_count = max(counts)
    hist = defaultdict(int)
    for c in counts:
        hist[c] += 1

    print(f"\n{'='*65}")
    print(f"  Neighbor count distribution (SNR > {args.snr_threshold}"
          f"{', excl. inferred' if args.exclude_inferred else ''}"
          f"{f', conf >= {args.min_confidence}' if args.min_confidence > 0 else ''})")
    print(f"{'='*65}")

    max_bar = 40
    max_freq = max(hist.values())
    for n in range(max_count + 1):
        freq = hist.get(n, 0)
        bar_len = int(freq / max_freq * max_bar) if max_freq > 0 else 0
        bar = '#' * bar_len
        if freq > 0 or n <= 5:
            print(f"  {n:>3} neighbors: {bar} {freq}")

    # Summary
    print(f"\n{'='*65}")
    print(f"  Summary")
    print(f"{'='*65}")
    print(f"  Nodes:           {len(nodes)}")
    print(f"  Total edges:     {len(edges)}")
    for src, cnt in sorted(source_counts.items(), key=lambda x: -x[1]):
        print(f"    {src:<12}     {cnt}")
    print(f"  Filtered edges:  {len(filtered)}")
    print(f"  With GPS:        {sum(1 for s in stats if s['has_coords'])}")
    print(f"  With status:     {sum(1 for s in stats if s['has_status'])}")
    print(f"  SNR threshold:   {args.snr_threshold}")

    avg = sum(counts) / len(counts)
    median = sorted(counts)[len(counts) // 2]
    print(f"  Avg neighbors:   {avg:.1f}")
    print(f"  Median:          {median}")
    print(f"  Min:             {min(counts)} ({sum(1 for c in counts if c == min(counts))} nodes)")
    print(f"  Max:             {max(counts)}")
    print(f"  Isolated (0):    {sum(1 for c in counts if c == 0)}")

    # Bidirectional analysis
    edge_set = set()
    for e in filtered:
        if e.get("snr_db", 0) > args.snr_threshold:
            edge_set.add((e["from"], e["to"]))
    bidir_count = sum(1 for a, b in edge_set if (b, a) in edge_set) // 2
    unidir_count = sum(1 for a, b in edge_set if (b, a) not in edge_set)
    print(f"\n  Bidirectional:   {bidir_count} pairs")
    print(f"  Unidirectional:  {unidir_count} edges")

    # DelayTuning table coverage
    print(f"\n  DelayTuning table index usage:")
    buckets = defaultdict(int)
    for c in counts:
        idx = min(c, 12)
        buckets[idx] += 1
    for idx in range(13):
        n = buckets.get(idx, 0)
        pct = n / len(counts) * 100
        marker = " <--" if n > 0 and pct > 15 else ""
        if n > 0:
            print(f"    [{idx:>2}] {n:>3} nodes ({pct:4.1f}%){marker}")


if __name__ == "__main__":
    main()
