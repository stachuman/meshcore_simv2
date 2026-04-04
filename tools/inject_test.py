#!/usr/bin/env python3
"""Inject test cases into an existing topology config.

Takes a topology JSON (from topology_generator or convert_topology),
adds companion nodes, message schedules, and delivery assertions.

Two modes for companion placement:
  Manual:  --add-companion alice:GDA_DW_RPT[:snr[:rssi]]
  Auto:    --companions 10  (farthest-point sampling across repeaters)

Two modes for message schedules:
  Manual:  --msg-schedule alice:bob:30[:count]
  Auto:    --auto-schedule  (diverse patterns: 1-to-1, 1-to-many, many-to-1)

Usage:
    # Manual companions + manual schedule
    python3 tools/inject_test.py topology.json \
        --add-companion alice:GDA_DW_RPT \
        --add-companion bob:GD_Swibno_rpt \
        --msg-schedule alice:bob:30 \
        -o test_scenario.json

    # Auto-place 10 companions with diverse schedules
    python3 tools/inject_test.py topology.json \
        --companions 10 --auto-schedule \
        --msg-interval 30 --msg-count 5 \
        --duration 600000 -o test_scenario.json
"""

import argparse
import json
import math
import random
import sys
from collections import defaultdict


# --- Geometry ---

def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


# --- Companion placement ---

def farthest_point_sampling(repeaters, n, rng):
    """Pick n repeaters maximizing geographic spread."""
    if n >= len(repeaters):
        return list(repeaters)

    selected = [rng.choice(repeaters)]
    remaining = [r for r in repeaters if r["name"] != selected[0]["name"]]

    while len(selected) < n:
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


def parse_companion_arg(arg: str) -> dict:
    """Parse --add-companion name:repeater[:snr[:rssi]]"""
    parts = arg.split(":")
    if len(parts) < 2:
        raise ValueError(f"--add-companion requires name:repeater, got: {arg}")
    result = {"name": parts[0], "repeater": parts[1]}
    if len(parts) >= 3:
        result["snr"] = float(parts[2])
    if len(parts) >= 4:
        result["rssi"] = float(parts[3])
    return result


def inject_companions(config, companions, default_snr=10.0, default_rssi=-70.0):
    """Add companion nodes and links. Returns list of added companion names."""
    all_names = {n["name"] for n in config["nodes"]}
    added = []

    for comp in companions:
        comp_name = comp["name"]
        rpt_name = comp["repeater"]

        if rpt_name not in all_names:
            print(f"WARNING: repeater {rpt_name!r} not found, "
                  f"skipping companion {comp_name!r}", file=sys.stderr)
            continue

        if comp_name in all_names:
            comp_name = comp_name + "_c"

        config["nodes"].append({"name": comp_name, "role": "companion"})
        all_names.add(comp_name)
        config["topology"]["links"].append({
            "from": comp_name,
            "to": rpt_name,
            "snr": comp.get("snr", default_snr),
            "rssi": comp.get("rssi", default_rssi),
            "bidir": True,
        })
        added.append(comp_name)

    return added


def _count_good_neighbors(config, min_snr=0.0):
    """Count SNR > min_snr neighbors per node from topology links."""
    counts = defaultdict(int)
    for link in config.get("topology", {}).get("links", []):
        snr = link.get("snr", 0.0)
        if snr > min_snr:
            counts[link["from"]] += 1
            if link.get("bidir", False):
                counts[link["to"]] += 1
    return counts


def _largest_component(config, min_snr=0.0):
    """Find the largest connected component via SNR > min_snr links.

    Returns set of node names in the largest component.
    """
    adj = defaultdict(set)
    for link in config.get("topology", {}).get("links", []):
        if link.get("snr", 0.0) > min_snr:
            adj[link["from"]].add(link["to"])
            if link.get("bidir", False):
                adj[link["to"]].add(link["from"])

    all_nodes = {n["name"] for n in config["nodes"]}
    visited = set()
    best_component = set()

    for name in all_nodes:
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
        if len(component) > len(best_component):
            best_component = component

    return best_component


def auto_place_companions(config, n, rng, snr=10.0, rssi=-70.0,
                          names=None, min_neighbors=2, verbose=False):
    """Auto-place n companions using connectivity-aware farthest-point sampling.

    Only considers repeaters in the largest connected component (SNR > 0)
    with at least `min_neighbors` good neighbors. This ensures all companions
    can theoretically reach each other.

    Returns list of companion names.
    """
    good_counts = _count_good_neighbors(config)
    main_component = _largest_component(config)

    repeaters_with_coords = []
    for node in config["nodes"]:
        if node.get("role") != "repeater":
            continue
        lat = node.get("lat", 0.0)
        lon = node.get("lon", 0.0)
        if lat == 0.0 and lon == 0.0:
            continue
        repeaters_with_coords.append(node)

    # Filter to largest component + min neighbors
    in_component = [r for r in repeaters_with_coords
                    if r["name"] in main_component]
    n_excluded = len(repeaters_with_coords) - len(in_component)
    if n_excluded > 0 and verbose:
        print(f"  Excluded {n_excluded} repeaters outside largest component "
              f"({len(main_component)} nodes)", file=sys.stderr)

    well_connected = [r for r in in_component
                      if good_counts.get(r["name"], 0) >= min_neighbors]

    if len(well_connected) < n:
        print(f"WARNING: only {len(well_connected)} repeaters in main component with "
              f">= {min_neighbors} good neighbors (need {n}). Relaxing to min_neighbors=1.",
              file=sys.stderr)
        well_connected = [r for r in in_component
                          if good_counts.get(r["name"], 0) >= 1]

    if len(well_connected) < n:
        print(f"WARNING: only {len(well_connected)} connected repeaters with coords. "
              f"Using all in main component.", file=sys.stderr)
        well_connected = in_component

    if n > len(well_connected):
        n = len(well_connected)

    if n < 2:
        print("ERROR: need at least 2 repeaters with coords for auto-placement",
              file=sys.stderr)
        sys.exit(1)

    selected = farthest_point_sampling(well_connected, n, rng)

    companion_names = []
    for i, rpt in enumerate(selected):
        if names and i < len(names):
            cname = names[i]
        else:
            cname = f"c{i+1:02d}"
        companion_names.append(cname)
        config["nodes"].append({"name": cname, "role": "companion"})
        config["topology"]["links"].append({
            "from": cname,
            "to": rpt["name"],
            "snr": snr,
            "rssi": rssi,
            "bidir": True,
        })

    if verbose:
        print(f"Companion placement ({n} companions, farthest-point + connectivity):",
              file=sys.stderr)
        for cname, rpt in zip(companion_names, selected):
            gc = good_counts.get(rpt["name"], 0)
            print(f"  {cname} -> {rpt['name']} "
                  f"({rpt.get('lat', '?')}, {rpt.get('lon', '?')}) "
                  f"[{gc} good neighbors]",
                  file=sys.stderr)

    return companion_names


# --- Schedule generation ---

def parse_msg_schedule_arg(arg: str) -> dict:
    """Parse --msg-schedule from:to:interval_s[:count]"""
    parts = arg.split(":")
    if len(parts) < 3:
        raise ValueError(f"--msg-schedule requires from:to:interval_s, got: {arg}")
    result = {"from": parts[0], "to": parts[1], "interval_s": float(parts[2])}
    if len(parts) >= 4:
        result["count"] = int(parts[3])
    return result


def parse_chan_schedule_arg(arg: str) -> dict:
    """Parse --chan-schedule from:channel:interval_s[:count]"""
    parts = arg.split(":")
    if len(parts) < 3:
        raise ValueError(f"--chan-schedule requires from:channel:interval_s, got: {arg}")
    result = {"from": parts[0], "channel": int(parts[1]), "interval_s": float(parts[2])}
    if len(parts) >= 4:
        result["count"] = int(parts[3])
    return result


def inject_manual_schedules(config, schedules, ack=False):
    """Add manually specified message schedules."""
    warmup = config["simulation"].get("warmup_ms", 5000)
    entries = config.get("message_schedule", [])
    for sched in schedules:
        entry = {
            "from": sched["from"],
            "to": sched["to"],
            "start_ms": warmup + 5000,
            "interval_ms": int(sched["interval_s"] * 1000),
        }
        if "count" in sched:
            entry["count"] = sched["count"]
        if ack:
            entry["ack"] = True
        entries.append(entry)
    config["message_schedule"] = entries


def inject_manual_channel_schedules(config, schedules):
    """Add manually specified channel broadcast schedules."""
    warmup = config["simulation"].get("warmup_ms", 5000)
    entries = config.get("channel_schedule", [])
    for sched in schedules:
        entry = {
            "from": sched["from"],
            "channel": sched["channel"],
            "start_ms": warmup + 5000,
            "interval_ms": int(sched["interval_s"] * 1000),
        }
        if "count" in sched:
            entry["count"] = sched["count"]
        entries.append(entry)
    config["channel_schedule"] = entries


def _random_times(rng, phase_base, mean_interval_ms, count):
    """Generate `count` sorted random send times with exponential inter-arrival.

    Models real user behavior: messages arrive as a Poisson process with
    mean inter-arrival time = mean_interval_ms. Each message gets a unique
    random timestamp, naturally clustered and spread.
    """
    times = []
    t = phase_base + rng.randint(0, mean_interval_ms - 1)
    for _ in range(count):
        times.append(int(t))
        # Exponential inter-arrival, clamped to [0.2x, 3x] mean to avoid
        # extreme bunching or gaps
        gap = rng.expovariate(1.0 / mean_interval_ms)
        gap = max(mean_interval_ms * 0.2, min(gap, mean_interval_ms * 3.0))
        t += gap
    return times


def generate_auto_schedules(companions, interval_ms, count, warmup_ms, rng):
    """Generate diverse message schedules with Poisson-distributed timing.

    Each message gets its own random send time (exponential inter-arrival
    around mean_interval_ms). This models realistic user behavior rather
    than fixed-interval robots.

    Patterns (all interleaved):
    - 1-to-1: round-robin pairing (c01->c02, c02->c03, ..., cN->c01)
    - 1-to-many: first companion broadcasts to all others
    - many-to-1: all companions send to last companion
    """
    schedules = []
    phase_base = warmup_ms + 5000
    n = len(companions)

    def _add_pair_schedules(sender, receiver, pattern_tag):
        times = _random_times(rng, phase_base, interval_ms, count)
        for seq, t in enumerate(times, 1):
            schedules.append({
                "from": sender,
                "to": receiver,
                "start_ms": t,
                "interval_ms": 1,
                "count": 1,
                "message": f"{pattern_tag} {sender}->{receiver} seq={seq}",
                "ack": True,
            })

    # 1-to-1: round-robin
    for i in range(n):
        j = (i + 1) % n
        _add_pair_schedules(companions[i], companions[j], "1to1")

    # 1-to-many: first companion sends to all others
    sender = companions[0]
    for i in range(1, n):
        _add_pair_schedules(sender, companions[i], "1toN")

    # many-to-1: all send to last companion
    target = companions[-1]
    for i in range(n - 1):
        _add_pair_schedules(companions[i], target, "Nto1")

    return schedules, phase_base


def generate_channel_schedules(companions, interval_ms, count, phase_base, rng):
    """Generate channel broadcast schedule with Poisson-distributed timing."""
    schedules = []
    for comp in companions:
        times = _random_times(rng, phase_base, interval_ms, count)
        for seq, t in enumerate(times, 1):
            schedules.append({
                "from": comp,
                "channel": 0,
                "start_ms": t,
                "interval_ms": 1,
                "count": 1,
                "message": f"chan {comp} seq={seq}",
            })
    return schedules


def adjust_duration(config, buffer_ms=30000):
    """Ensure duration fits all schedules + buffer."""
    max_end = 0
    for key in ("message_schedule", "channel_schedule"):
        for s in config.get(key, []):
            cnt = s.get("count", 1)
            end = s["start_ms"] + s["interval_ms"] * cnt
            if end > max_end:
                max_end = end

    needed = max_end + buffer_ms
    if needed > config["simulation"]["duration_ms"]:
        config["simulation"]["duration_ms"] = needed
        return needed
    return None


# --- Assertions ---

def generate_assertions(config):
    """Auto-generate delivery assertions from message schedules."""
    schedules = config.get("message_schedule", [])
    if not schedules:
        return

    assertions = config.get("expect", [])

    # Deduplicate sender->receiver pairs for cmd_reply assertions
    seen_pairs = set()
    for sched in schedules:
        sender = sched["from"]
        receiver = sched["to"]
        pair = (sender, receiver)
        if pair not in seen_pairs:
            seen_pairs.add(pair)
            assertions.append({
                "type": "cmd_reply_contains",
                "node": sender,
                "command": f"msg {receiver}",
                "value": f"msg sent to {receiver}",
            })

    # At least some RX events should occur
    assertions.append({
        "type": "event_count_min",
        "value": "rx",
        "count": 1,
    })

    config["expect"] = assertions


# --- Main ---

def main():
    parser = argparse.ArgumentParser(
        description="Inject test cases into a topology config"
    )

    parser.add_argument("input", help="Input topology JSON file")

    # Manual companion placement
    parser.add_argument("--add-companion", action="append",
                        help="Add companion: name:repeater[:snr[:rssi]] (repeatable)")

    # Auto companion placement
    parser.add_argument("--companions", type=int, default=None,
                        help="Auto-place N companions (farthest-point sampling)")
    parser.add_argument("--companion-names", default=None,
                        help="Comma-separated names for auto-placed companions (default: c01,c02,...)")
    parser.add_argument("--companion-snr", type=float, default=10.0,
                        help="SNR for auto-placed companion links (default: 10.0)")
    parser.add_argument("--companion-rssi", type=float, default=-70.0,
                        help="RSSI for auto-placed companion links (default: -70.0)")
    parser.add_argument("--min-neighbors", type=int, default=2,
                        help="Min good neighbors for repeater to be eligible for companion placement (default: 2)")
    parser.add_argument("--seed", type=int, default=42,
                        help="RNG seed for auto-placement and schedule offsets (default: 42)")

    # Manual message schedule
    parser.add_argument("--msg-schedule", action="append",
                        help="Message schedule: from:to:interval_s[:count] (repeatable)")
    parser.add_argument("--ack", action="store_true", default=False,
                        help="Use ack (msga) for manual --msg-schedule entries")

    # Manual channel schedule
    parser.add_argument("--chan-schedule", action="append",
                        help="Channel schedule: from:channel:interval_s[:count] (repeatable)")

    # Auto schedule generation
    parser.add_argument("--auto-schedule", action="store_true", default=False,
                        help="Generate diverse schedule patterns (1-to-1, 1-to-many, many-to-1)")
    parser.add_argument("--msg-interval", type=int, default=30,
                        help="Message interval in seconds for auto-schedule (default: 30)")
    parser.add_argument("--msg-count", type=int, default=5,
                        help="Messages per schedule entry for auto-schedule (default: 5)")

    # Channel schedule
    parser.add_argument("--channel", action="store_true", default=False,
                        help="Add channel broadcast schedule (requires auto-schedule)")
    parser.add_argument("--chan-interval", type=int, default=None,
                        help="Channel interval in seconds (default: same as --msg-interval)")
    parser.add_argument("--chan-count", type=int, default=None,
                        help="Channel messages per companion (default: same as --msg-count)")

    # Simulation overrides
    parser.add_argument("--duration", type=int, default=None,
                        help="Override simulation duration_ms")
    parser.add_argument("--warmup", type=int, default=None,
                        help="Override warmup_ms")

    # Assertions
    parser.add_argument("--auto-assert", action="store_true", default=False,
                        help="Enable auto-generated delivery assertions")

    # Output
    parser.add_argument("-o", "--output", help="Output file (default: stdout)")
    parser.add_argument("-v", "--verbose", action="store_true", default=False)

    args = parser.parse_args()

    # Validate: --auto-schedule requires companions
    if args.auto_schedule and not args.companions and not args.add_companion:
        parser.error("--auto-schedule requires --companions or --add-companion")
    if args.channel and not args.auto_schedule:
        parser.error("--channel requires --auto-schedule")

    # Load input
    with open(args.input) as f:
        config = json.load(f)

    # Override sim params
    if args.duration is not None:
        config["simulation"]["duration_ms"] = args.duration
    if args.warmup is not None:
        config["simulation"]["warmup_ms"] = args.warmup

    # Ensure hot_start
    config["simulation"]["hot_start"] = True

    rng = random.Random(args.seed)
    companion_names = []

    # Auto-place companions
    if args.companions:
        names = args.companion_names.split(",") if args.companion_names else None
        companion_names = auto_place_companions(
            config, args.companions, rng,
            snr=args.companion_snr, rssi=args.companion_rssi,
            names=names, min_neighbors=args.min_neighbors,
            verbose=args.verbose,
        )
        print(f"Auto-placed {len(companion_names)} companions", file=sys.stderr)

    # Manual companions
    if args.add_companion:
        companions = [parse_companion_arg(c) for c in args.add_companion]
        added = inject_companions(
            config, companions,
            default_snr=args.companion_snr, default_rssi=args.companion_rssi,
        )
        companion_names.extend(added)
        print(f"Added {len(added)} manual companions: {', '.join(added)}",
              file=sys.stderr)

    # Auto schedule generation
    if args.auto_schedule and companion_names:
        interval_ms = args.msg_interval * 1000
        warmup_ms = config["simulation"].get("warmup_ms", 5000)

        schedules, phase_base = generate_auto_schedules(
            companion_names, interval_ms, args.msg_count, warmup_ms, rng,
        )
        config["message_schedule"] = config.get("message_schedule", []) + schedules

        n_1to1 = len(companion_names)
        n_1toN = len(companion_names) - 1
        n_Nto1 = len(companion_names) - 1
        total_msgs = len(schedules)
        print(f"Auto-schedule: {total_msgs} messages "
              f"(mean interval {args.msg_interval}s, Poisson-distributed)",
              file=sys.stderr)
        if args.verbose:
            print(f"  1-to-1: {n_1to1} pairs x {args.msg_count} msgs (round-robin)",
                  file=sys.stderr)
            print(f"  1-to-many: {n_1toN} targets x {args.msg_count} msgs from {companion_names[0]}",
                  file=sys.stderr)
            print(f"  many-to-1: {n_Nto1} senders x {args.msg_count} msgs to {companion_names[-1]}",
                  file=sys.stderr)

        # Channel broadcasts
        if args.channel:
            chan_interval_ms = (args.chan_interval or args.msg_interval) * 1000
            chan_count = args.chan_count or args.msg_count
            chan_schedules = generate_channel_schedules(
                companion_names, chan_interval_ms, chan_count, phase_base, rng,
            )
            config["channel_schedule"] = chan_schedules
            print(f"Channel schedule: {len(chan_schedules)} messages "
                  f"(mean interval {args.chan_interval or args.msg_interval}s)",
                  file=sys.stderr)

    # Manual message schedules
    if args.msg_schedule:
        schedules = [parse_msg_schedule_arg(s) for s in args.msg_schedule]
        inject_manual_schedules(config, schedules, ack=args.ack)
        ack_label = " (with ack)" if args.ack else ""
        print(f"Added {len(schedules)} manual schedule(s){ack_label}", file=sys.stderr)

    # Manual channel schedules
    if args.chan_schedule:
        schedules = [parse_chan_schedule_arg(s) for s in args.chan_schedule]
        inject_manual_channel_schedules(config, schedules)
        print(f"Added {len(schedules)} manual channel schedule(s)", file=sys.stderr)

    # Auto-adjust duration to fit all schedules
    adjusted = adjust_duration(config)
    if adjusted:
        print(f"Adjusted duration to {adjusted}ms to fit schedules",
              file=sys.stderr)

    # Auto-assertions
    if args.auto_assert:
        generate_assertions(config)
        n = len(config.get("expect", []))
        if n:
            print(f"Generated {n} assertion(s)", file=sys.stderr)

    # Summary
    n_rpt = len([n for n in config["nodes"] if n.get("role") == "repeater"])
    n_comp = len([n for n in config["nodes"] if n.get("role") == "companion"])
    n_links = len(config["topology"]["links"])
    n_sched = len(config.get("message_schedule", []))
    n_chan = len(config.get("channel_schedule", []))
    print(f"Output: {n_rpt} repeaters, {n_comp} companions, {n_links} links, "
          f"{n_sched} schedules, {n_chan} channel schedules, "
          f"duration {config['simulation']['duration_ms']}ms",
          file=sys.stderr)

    # Emit
    output_str = json.dumps(config, indent=2)
    if args.output:
        with open(args.output, "w") as f:
            f.write(output_str + "\n")
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        print(output_str)


if __name__ == "__main__":
    main()
