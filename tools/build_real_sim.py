#!/usr/bin/env python3
"""Build a complete simulation config from raw topology data.

Automates: topology conversion + companion auto-placement + message schedules.

Usage:
  python3 tools/build_real_sim.py simulation/topology.json \
    --companions 10 --sf 8 --bw 125000 --cr 4 \
    --msg-interval 30 --msg-count 5 \
    -o simulation/real_network.json -v
"""

import argparse
import json
import math
import os
import random
import subprocess
import sys
import tempfile


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def farthest_point_sampling(repeaters, n, rng):
    """Pick n repeaters maximizing geographic spread.

    repeaters: list of {"name": ..., "lat": ..., "lon": ...}
    Returns list of n selected repeaters.
    """
    if n >= len(repeaters):
        return list(repeaters)

    # Start with a random repeater
    selected = [rng.choice(repeaters)]
    remaining = [r for r in repeaters if r["name"] != selected[0]["name"]]

    while len(selected) < n:
        # For each remaining repeater, compute min distance to any selected
        best = None
        best_dist = -1
        for r in remaining:
            min_d = min(
                haversine_km(r["lat"], r["lon"], s["lat"], s["lon"])
                for s in selected
            )
            if min_d > best_dist:
                best_dist = min_d
                best = r

        selected.append(best)
        remaining = [r for r in remaining if r["name"] != best["name"]]

    return selected


def generate_schedules(companions, interval_ms, count, warmup_ms, rng):
    """Generate diverse message schedules with random start offsets.

    All patterns run concurrently from the same base time (warmup + 5s).
    Each schedule gets a random offset within [0, interval_ms) for
    desynchronization.

    Patterns (all interleaved):
    - 1-to-1: round-robin pairing (c01->c02, c02->c03, ..., cN->c01)
    - 1-to-many: first companion broadcasts to all others
    - many-to-1: all companions send to last companion

    Returns (schedules, phase_base) — phase_base for channel schedule generation.
    """
    schedules = []
    phase_base = warmup_ms + 5000
    n = len(companions)

    # 1-to-1: round-robin
    for i in range(n):
        j = (i + 1) % n
        offset = rng.randint(0, interval_ms - 1)
        schedules.append({
            "from": companions[i],
            "to": companions[j],
            "start_ms": phase_base + offset,
            "interval_ms": interval_ms,
            "count": count,
            "message": f"1to1 {companions[i]}->{companions[j]} seq={{n}} direct msg status ok signal good position hold steady",
            "ack": True,
        })

    # 1-to-many: first companion sends to all others (same base time)
    sender = companions[0]
    for i in range(1, n):
        offset = rng.randint(0, interval_ms - 1)
        schedules.append({
            "from": sender,
            "to": companions[i],
            "start_ms": phase_base + offset,
            "interval_ms": interval_ms,
            "count": count,
            "message": f"1toN {sender}->{companions[i]} seq={{n}} broadcast update all stations check in report status",
            "ack": True,
        })

    # many-to-1: all send to last companion (same base time)
    target = companions[-1]
    for i in range(n - 1):
        offset = rng.randint(0, interval_ms - 1)
        schedules.append({
            "from": companions[i],
            "to": target,
            "start_ms": phase_base + offset,
            "interval_ms": interval_ms,
            "count": count,
            "message": f"Nto1 {companions[i]}->{target} seq={{n}} converge report mesh node active all links nominal",
            "ack": True,
        })

    return schedules, phase_base


def generate_channel_schedules(companions, interval_ms, count, phase_base, rng):
    """Generate channel broadcast schedule — runs concurrently with direct messages.

    Each companion sends count messages on the public channel (channel 0).
    """
    schedules = []
    n = len(companions)
    for i in range(n):
        offset = rng.randint(0, interval_ms - 1)
        schedules.append({
            "from": companions[i],
            "channel": 0,
            "start_ms": phase_base + offset,
            "interval_ms": interval_ms,
            "count": count,
            "message": f"chan {companions[i]} seq={{n}} public channel broadcast mesh network status check all nodes",
        })
    return schedules


def main():
    parser = argparse.ArgumentParser(
        description="Build simulation config with auto-placed companions and message schedules"
    )
    parser.add_argument("input", help="Path to topology.json")
    parser.add_argument("-o", "--output", required=True, help="Output config file")

    # Companion placement
    parser.add_argument("--companions", type=int, default=10,
                        help="Number of companions to auto-place (default: 10)")
    parser.add_argument("--seed", type=int, default=42,
                        help="RNG seed for companion placement (default: 42)")
    parser.add_argument("--companion-snr", type=float, default=10.0,
                        help="SNR for companion-to-repeater links (default: 10.0)")
    parser.add_argument("--companion-rssi", type=float, default=-70.0,
                        help="RSSI for companion-to-repeater links (default: -70.0)")

    # Radio parameters
    parser.add_argument("--sf", type=int, default=8,
                        help="LoRa spreading factor (default: 8)")
    parser.add_argument("--bw", type=int, default=62500,
                        help="Bandwidth in Hz (default: 62500)")
    parser.add_argument("--cr", type=int, default=4,
                        help="Coding rate (default: 4)")

    # Simulation
    parser.add_argument("--duration", type=int, default=300000,
                        help="Simulation duration in ms (default: 300000)")
    parser.add_argument("--warmup", type=int, default=5000,
                        help="Warmup period in ms (default: 5000)")
    parser.add_argument("--step", type=int, default=5,
                        help="Step size in ms (default: 5)")

    # Message schedule
    parser.add_argument("--msg-interval", type=int, default=30,
                        help="Message interval in seconds (default: 30)")
    parser.add_argument("--msg-count", type=int, default=5,
                        help="Messages per schedule entry (default: 5)")

    # Channel schedule
    parser.add_argument("--no-channel", action="store_true", default=False,
                        help="Disable channel broadcast messages")
    parser.add_argument("--chan-interval", type=int, default=None,
                        help="Channel message interval in seconds (default: same as --msg-interval)")
    parser.add_argument("--chan-count", type=int, default=None,
                        help="Channel messages per companion (default: same as --msg-count)")

    # convert_topology.py passthrough flags
    parser.add_argument("--min-snr", type=float, default=-10.0)
    parser.add_argument("--min-confidence", type=float, default=0.7)
    parser.add_argument("--include-inferred", action="store_true", default=False)
    parser.add_argument("--max-link-km", type=float, default=80.0)
    parser.add_argument("--dedup-km", type=float, default=0.0)
    parser.add_argument("--no-estimate-coords", action="store_true", default=False)
    parser.add_argument("--merge-bidir", action="store_true", default=False)
    parser.add_argument("--no-fill-gaps", action="store_true", default=False)
    parser.add_argument("--max-gap-km", type=float, default=30.0)
    parser.add_argument("--max-good-links", type=int, default=2)
    parser.add_argument("--max-edges-per-node", type=int, default=8)
    parser.add_argument("--gap-sigma", type=float, default=None)

    parser.add_argument("-v", "--verbose", action="store_true", default=False)

    args = parser.parse_args()

    # Step 1: Run convert_topology.py to get base config
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    convert_script = os.path.join(tools_dir, "convert_topology.py")

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as tmp:
        tmp_path = tmp.name

    try:
        cmd = [
            sys.executable, convert_script, args.input,
            "-o", tmp_path,
            "--duration", str(args.duration),
            "--warmup", str(args.warmup),
            "--step", str(args.step),
            "--min-snr", str(args.min_snr),
            "--min-confidence", str(args.min_confidence),
            "--max-link-km", str(args.max_link_km),
            "--dedup-km", str(args.dedup_km),
            "--max-gap-km", str(args.max_gap_km),
            "--max-good-links", str(args.max_good_links),
            "--max-edges-per-node", str(args.max_edges_per_node),
        ]
        if args.include_inferred:
            cmd.append("--include-inferred")
        if args.no_estimate_coords:
            cmd.append("--no-estimate-coords")
        if args.merge_bidir:
            cmd.append("--merge-bidir")
        if args.no_fill_gaps:
            cmd.append("--no-fill-gaps")
        if args.gap_sigma is not None:
            cmd.extend(["--gap-sigma", str(args.gap_sigma)])
        if args.verbose:
            cmd.append("-v")

        if args.verbose:
            print(f"Running: {' '.join(cmd)}", file=sys.stderr)

        result = subprocess.run(cmd, capture_output=True, text=True)
        # convert_topology prints summary to stderr
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        if result.returncode != 0:
            print(f"ERROR: convert_topology.py failed (exit {result.returncode})",
                  file=sys.stderr)
            sys.exit(1)

        with open(tmp_path) as f:
            config = json.load(f)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    # Step 2: Extract repeaters with coordinates
    repeaters_with_coords = []
    for node in config["nodes"]:
        if node.get("role") != "repeater":
            continue
        lat = node.get("lat", 0.0)
        lon = node.get("lon", 0.0)
        if lat != 0.0 or lon != 0.0:
            repeaters_with_coords.append(node)

    n_companions = args.companions
    if n_companions > len(repeaters_with_coords):
        print(f"WARNING: requested {n_companions} companions but only "
              f"{len(repeaters_with_coords)} repeaters with coords available. "
              f"Using {len(repeaters_with_coords)}.", file=sys.stderr)
        n_companions = len(repeaters_with_coords)

    if n_companions < 2:
        print("ERROR: need at least 2 companions for message schedules",
              file=sys.stderr)
        sys.exit(1)

    # Step 3: Farthest-point sampling
    rng = random.Random(args.seed)
    selected = farthest_point_sampling(repeaters_with_coords, n_companions, rng)

    companion_names = []
    for i, rpt in enumerate(selected):
        cname = f"c{i+1:02d}"
        companion_names.append(cname)
        config["nodes"].append({"name": cname, "role": "companion"})
        config["topology"]["links"].append({
            "from": cname,
            "to": rpt["name"],
            "snr": args.companion_snr,
            "rssi": args.companion_rssi,
            "bidir": True,
        })

    if args.verbose:
        print(f"\nCompanion placement ({n_companions} companions):", file=sys.stderr)
        for i, (cname, rpt) in enumerate(zip(companion_names, selected)):
            print(f"  {cname} -> {rpt['name']} "
                  f"({rpt.get('lat', '?')}, {rpt.get('lon', '?')})",
                  file=sys.stderr)

    # Step 4: Add radio parameters
    config["simulation"]["radio"] = {
        "sf": args.sf,
        "bw": args.bw,
        "cr": args.cr,
    }

    # Step 5: Generate message schedules
    interval_ms = args.msg_interval * 1000
    schedules, phase_end_ms = generate_schedules(
        companion_names, interval_ms, args.msg_count, args.warmup, rng
    )
    config["message_schedule"] = schedules

    # Step 5b: Generate channel broadcast schedule
    chan_schedules = []
    if not args.no_channel:
        chan_interval_ms = (args.chan_interval or args.msg_interval) * 1000
        chan_count = args.chan_count or args.msg_count
        chan_schedules = generate_channel_schedules(
            companion_names, chan_interval_ms, chan_count, phase_end_ms, rng
        )
        config["channel_schedule"] = chan_schedules

    # Ensure duration is long enough for all schedules
    all_schedules = schedules + chan_schedules
    max_end = 0
    for s in all_schedules:
        end = s["start_ms"] + s["interval_ms"] * s["count"]
        if end > max_end:
            max_end = end
    # Add 30s buffer for delivery
    needed = max_end + 30000
    if needed > config["simulation"]["duration_ms"]:
        config["simulation"]["duration_ms"] = needed
        if args.verbose:
            print(f"Adjusted duration to {needed}ms to fit all schedules",
                  file=sys.stderr)

    # Step 6: Write output
    with open(args.output, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")

    # Summary
    n_schedules = len(schedules)
    total_msgs = sum(s["count"] for s in schedules)
    n_chan_schedules = len(chan_schedules)
    total_chan_msgs = sum(s["count"] for s in chan_schedules)
    print(f"\n--- Build Summary ---", file=sys.stderr)
    print(f"Repeaters: {len([n for n in config['nodes'] if n.get('role') == 'repeater'])}",
          file=sys.stderr)
    print(f"Companions: {n_companions} (farthest-point sampling, seed={args.seed})",
          file=sys.stderr)
    print(f"Radio: SF{args.sf} BW{args.bw} CR{args.cr}", file=sys.stderr)
    print(f"Direct schedules: {n_schedules} entries, {total_msgs} total messages",
          file=sys.stderr)
    print(f"  1-to-1: {n_companions} pairs (round-robin)", file=sys.stderr)
    print(f"  1-to-many: {n_companions - 1} targets from {companion_names[0]}",
          file=sys.stderr)
    print(f"  many-to-1: {n_companions - 1} senders to {companion_names[-1]}",
          file=sys.stderr)
    if chan_schedules:
        print(f"Channel schedules: {n_chan_schedules} entries, {total_chan_msgs} total messages",
              file=sys.stderr)
        print(f"  channel broadcast: {n_companions} senders on channel 0",
              file=sys.stderr)
    else:
        print(f"Channel schedules: disabled", file=sys.stderr)
    print(f"Duration: {config['simulation']['duration_ms']}ms", file=sys.stderr)
    print(f"Links: {len(config['topology']['links'])}", file=sys.stderr)
    print(f"Written to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
