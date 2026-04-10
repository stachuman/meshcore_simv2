#!/usr/bin/env python3
"""Convert mcsim YAML topology to our orchestrator JSON format.

Minimal transformation — preserves the source data as faithfully as possible:
  - SNR values kept as-is (no recalculation)
  - Per-link snr_std_dev preserved
  - Asymmetric links preserved (bidir=false, explicit A→B and B→A)
  - Room_Server nodes mapped to repeater role
  - Existing Companion nodes dropped (use inject_test.py to add our own)
  - No survival filter, no edge pruning

Usage:
    python3 tools/convert_mcsim.py sea.yaml -o mcsim_seattle.json
    python3 tools/convert_mcsim.py sea.yaml --min-snr -15 -o mcsim_seattle.json
"""

import argparse
import json
import sys

import yaml


def main():
    parser = argparse.ArgumentParser(
        description="Convert mcsim YAML topology to orchestrator JSON"
    )
    parser.add_argument("input", help="mcsim YAML topology file")
    parser.add_argument("--min-snr", type=float, default=None,
                        help="Drop links below this SNR (default: keep all)")
    parser.add_argument("--drop-companions", action="store_true", default=True,
                        help="Drop existing companion nodes (default: true)")
    parser.add_argument("--keep-companions", action="store_true",
                        help="Keep existing companion nodes")
    parser.add_argument("-o", "--output", help="Output JSON file (default: stdout)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if args.keep_companions:
        args.drop_companions = False

    # Parse YAML
    with open(args.input) as f:
        data = yaml.safe_load(f)

    src_nodes = data.get("nodes", [])
    src_edges = data.get("edges", [])

    # Map nodes
    nodes = []
    dropped_names = set()
    for n in src_nodes:
        name = n.get("name", "")
        fw_type = n.get("firmware", {}).get("type", "Repeater")
        loc = n.get("location", {})
        lat = loc.get("lat")
        lon = loc.get("lon")

        if fw_type == "Companion" and args.drop_companions:
            dropped_names.add(name)
            continue

        # Room_Server → repeater (they relay packets the same way)
        # Companion → companion (passive network participants)
        role = "companion" if fw_type == "Companion" else "repeater"

        node = {"name": name, "role": role}
        if lat is not None:
            node["lat"] = lat
        if lon is not None:
            node["lon"] = lon
        nodes.append(node)

    node_names = {n["name"] for n in nodes}

    # Map edges — directional, no dedup
    links = []
    dropped_link_snr = 0
    dropped_link_node = 0
    for e in src_edges:
        frm = e.get("from", "")
        to = e.get("to", "")

        # Skip links involving dropped nodes
        if frm not in node_names or to not in node_names:
            dropped_link_node += 1
            continue

        snr = e.get("mean_snr_db_at20dbm", 0.0)
        std_dev = e.get("snr_std_dev", 0.0)

        if args.min_snr is not None and snr < args.min_snr:
            dropped_link_snr += 1
            continue

        link = {
            "from": frm,
            "to": to,
            "snr": round(snr, 1),
            "snr_std_dev": round(std_dev, 1),
            "bidir": False,
        }
        links.append(link)

    # Build config
    config = {
        "_source": f"Converted from mcsim: {args.input}",
        "simulation": {
            "duration_ms": 300000,
            "step_ms": 1,
            "warmup_ms": 5000,
            "hot_start": True,
        },
        "nodes": nodes,
        "topology": {
            "links": links,
        },
        "commands": [],
    }

    # Output
    out_str = json.dumps(config, indent=2) + "\n"
    if args.output:
        with open(args.output, "w") as f:
            f.write(out_str)
    else:
        sys.stdout.write(out_str)

    # Stats
    if args.verbose or args.output:
        n_repeaters = sum(1 for n in nodes if n["role"] == "repeater")
        n_companions = sum(1 for n in nodes if n["role"] == "companion")
        n_links = len(links)
        snr_vals = [lk["snr"] for lk in links]
        print(f"Source: {len(src_nodes)} nodes, {len(src_edges)} edges", file=sys.stderr)
        if dropped_names:
            print(f"  Dropped {len(dropped_names)} companion nodes", file=sys.stderr)
        if dropped_link_node:
            print(f"  Dropped {dropped_link_node} edges (endpoint removed)", file=sys.stderr)
        if dropped_link_snr:
            print(f"  Dropped {dropped_link_snr} edges (SNR < {args.min_snr})", file=sys.stderr)
        node_str = f"{n_repeaters} repeaters"
        if n_companions:
            node_str += f" + {n_companions} companions"
        print(f"Output: {node_str}, {n_links} directed links", file=sys.stderr)
        if snr_vals:
            print(f"  SNR range: {min(snr_vals):.1f} to {max(snr_vals):.1f} dB, "
                  f"median: {sorted(snr_vals)[len(snr_vals)//2]:.1f} dB", file=sys.stderr)
        if args.output:
            print(f"Written to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
