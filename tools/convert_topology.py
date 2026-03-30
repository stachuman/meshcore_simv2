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
"""

import argparse
import json
import re
import sys
from collections import defaultdict


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


def convert(args):
    with open(args.input, "r") as f:
        topo = json.load(f)

    nodes_in = topo.get("nodes", {})
    edges_in = topo.get("edges", [])

    # --- Node processing ---
    seen_names: dict[str, int] = {}
    prefix_to_name: dict[str, str] = {}  # prefix -> sanitized name
    node_noise_floor: dict[str, int] = {}  # prefix -> noise_floor

    nodes_out = []
    for prefix, nd in nodes_in.items():
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

    # Resolve repeater names for companion injection (match against sanitized names)
    sanitized_to_prefix = {v: k for k, v in prefix_to_name.items()}
    all_sanitized_names = set(prefix_to_name.values())

    # --- Edge filtering and conversion ---
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
        receiver_nf = node_noise_floor.get(dst_prefix, -111)
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

    # --- Companion injection ---
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

    # --- Assemble output ---
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
    if companions_added:
        print(f"Companions: {len(companions_added)} added", file=sys.stderr)
        for c in companions_added:
            print(f"  {c}", file=sys.stderr)
    print(f"Edges: {total_edges} total, {filtered_edges} filtered, {surviving_edges} surviving",
          file=sys.stderr)
    if msg_schedules:
        print(f"Message schedules: {len(msg_schedules)}", file=sys.stderr)
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
    parser.add_argument("--min-snr", type=float, default=-7.5,
                        help="Drop edges with snr_db below this (default: -7.5)")
    parser.add_argument("--min-confidence", type=float, default=0.7,
                        help="Drop edges with confidence below this (default: 0.7)")
    parser.add_argument("--include-inferred", action="store_true", default=False,
                        help="Include edges with source='inferred' (default: drop them)")

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

    args = parser.parse_args()
    convert(args)


if __name__ == "__main__":
    main()
