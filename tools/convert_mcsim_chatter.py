#!/usr/bin/env python3
"""Convert mcsim chatter YAML overlay into orchestrator message/channel schedules.

Takes an already-converted topology JSON (from convert_mcsim.py) and the mcsim
chatter YAML overlay, finds the nearest repeater for each companion, and expands
the stochastic session-based traffic model into deterministic schedule entries.

The traffic expansion uses a seeded RNG to faithfully replicate the mcsim model:
  - Sessions of N messages with random inter-message intervals
  - Gaps between sessions with random durations
  - Each message is DM (with probability) or channel broadcast
  - DM targets chosen uniformly from the companion's target list

Usage:
    python3 tools/convert_mcsim_chatter.py \
        delay_optimization/mcsim_seattle.json \
        /tmp/mcsim_repo/examples/seattle/chatter.yaml \
        -o delay_optimization/mcsim_seattle_chatter.json

    python3 tools/convert_mcsim_chatter.py \
        delay_optimization/mcsim_seattle.json \
        /tmp/mcsim_repo/examples/seattle/chatter.yaml \
        --duration 3600 --seed 42 \
        -o delay_optimization/mcsim_seattle_chatter.json
"""

import argparse
import json
import math
import random
import sys

import yaml


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def find_nearest_repeater(lat, lon, repeaters):
    """Find the nearest repeater node to a given lat/lon."""
    best = None
    best_dist = float("inf")
    for r in repeaters:
        if "lat" not in r or "lon" not in r:
            continue
        d = haversine_km(lat, lon, r["lat"], r["lon"])
        if d < best_dist:
            best_dist = d
            best = r
    return best, best_dist


def expand_traffic(companion, rng, duration_s, warmup_s):
    """Expand a companion's stochastic traffic model into concrete events.

    Returns list of (time_s, type, target_or_channel) tuples.
    type is "dm" or "channel".
    """
    agent = companion.get("agent", {})
    dm_targets = agent.get("dm_targets", [])
    channels = agent.get("channels", [])
    dm_prob = agent.get("dm_probability", 0.5)
    interval_min = agent.get("message_interval_min_s", 60.0)
    interval_max = agent.get("message_interval_max_s", 180.0)
    msgs_per_session = agent.get("messages_per_session", 3)
    gap_min = agent.get("session_gap_min_s", 300.0)
    gap_max = agent.get("session_gap_max_s", 600.0)

    events = []
    t = warmup_s + rng.uniform(0, interval_max)  # initial jitter

    while t < duration_s:
        # Session: send msgs_per_session messages
        for _ in range(msgs_per_session):
            if t >= duration_s:
                break

            if rng.random() < dm_prob and dm_targets:
                target = rng.choice(dm_targets)
                events.append((t, "dm", target))
            elif channels:
                ch = rng.choice(channels)
                events.append((t, "channel", ch))

            t += rng.uniform(interval_min, interval_max)

        # Gap between sessions
        t += rng.uniform(gap_min, gap_max)

    return events


def main():
    parser = argparse.ArgumentParser(
        description="Convert mcsim chatter overlay to orchestrator schedules"
    )
    parser.add_argument("topology", help="Converted topology JSON (from convert_mcsim.py)")
    parser.add_argument("chatter", help="mcsim chatter YAML overlay file")
    parser.add_argument("--duration", type=float, default=3600,
                        help="Simulation duration in seconds (default: 3600)")
    parser.add_argument("--seed", type=int, default=42,
                        help="RNG seed for traffic expansion (default: 42)")
    parser.add_argument("--companion-snr", type=float, default=None,
                        help="Override companion-repeater SNR (default: use chatter values)")
    parser.add_argument("-o", "--output", help="Output JSON file (default: stdout)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    # Load topology
    with open(args.topology) as f:
        config = json.load(f)

    # Load chatter overlay
    with open(args.chatter) as f:
        chatter = yaml.safe_load(f)

    chatter_nodes = chatter.get("nodes", [])
    chatter_edges = chatter.get("edges", [])

    repeaters = [n for n in config["nodes"] if n.get("role", "repeater") == "repeater"]
    existing_names = {n["name"] for n in config["nodes"]}

    # Build edge SNR lookup from chatter (companion→anchor SNR)
    edge_snr = {}
    for e in chatter_edges:
        edge_snr[(e["from"], e["to"])] = e.get("mean_snr_db_at20dbm", 10.0)

    rng = random.Random(args.seed)
    warmup_s = config.get("simulation", {}).get("warmup_ms", 5000) / 1000.0
    duration_s = args.duration

    # Update simulation duration
    config.setdefault("simulation", {})["duration_ms"] = int(duration_s * 1000)

    # Process each companion
    added_companions = []
    all_events = []  # (time_s, companion_name, type, target)

    for cnode in chatter_nodes:
        name = cnode["name"]
        fw_type = cnode.get("firmware", {}).get("type", "Companion")
        if fw_type != "Companion":
            continue

        loc = cnode.get("location", {})
        clat = loc.get("lat")
        clon = loc.get("lon")

        if clat is None or clon is None:
            print(f"WARNING: companion {name!r} has no location, skipping",
                  file=sys.stderr)
            continue

        # Find nearest repeater
        nearest, dist_km = find_nearest_repeater(clat, clon, repeaters)
        if nearest is None:
            print(f"WARNING: no repeater found for {name!r}, skipping",
                  file=sys.stderr)
            continue

        # Get SNR from chatter edges (companion→anchor), or use default
        # The chatter YAML references anchor names like SEA-Downtown;
        # we use the SNR value but connect to the nearest real repeater
        snr = 10.0
        for e in chatter_edges:
            if e["from"] == name:
                snr = e.get("mean_snr_db_at20dbm", 10.0)
                break
        if args.companion_snr is not None:
            snr = args.companion_snr

        # Add companion node
        comp_name = name
        if comp_name in existing_names:
            comp_name = name + "_c"
        config["nodes"].append({
            "name": comp_name,
            "role": "companion",
            "lat": clat,
            "lon": clon,
        })
        existing_names.add(comp_name)

        # Add bidirectional link (using two directed links to match topology style)
        config["topology"]["links"].append({
            "from": comp_name,
            "to": nearest["name"],
            "snr": round(snr, 1),
            "bidir": False,
        })
        config["topology"]["links"].append({
            "from": nearest["name"],
            "to": comp_name,
            "snr": round(snr, 1),
            "bidir": False,
        })

        added_companions.append(comp_name)

        if args.verbose:
            print(f"  {comp_name} -> {nearest['name']} ({dist_km:.1f} km, SNR {snr:.1f} dB)",
                  file=sys.stderr)

        # Expand traffic
        events = expand_traffic(cnode, rng, duration_s, warmup_s)
        for t, etype, target in events:
            all_events.append((t, comp_name, etype, target))

    # Sort all events by time
    all_events.sort(key=lambda e: e[0])

    # Convert events to message_schedule / channel_schedule
    # We use individual entries with count=1 at specific times
    msg_schedules = []
    chan_schedules = []

    for t, comp_name, etype, target in all_events:
        at_ms = int(t * 1000)
        if etype == "dm":
            # Target must be another companion name
            resolved_target = target
            if target not in existing_names and target + "_c" in existing_names:
                resolved_target = target + "_c"
            msg_schedules.append({
                "from": comp_name,
                "to": resolved_target,
                "start_ms": at_ms,
                "interval_ms": 1000,  # irrelevant for count=1
                "count": 1,
                "ack": True,
            })
        elif etype == "channel":
            # Channel 0 = default channel
            chan_schedules.append({
                "from": comp_name,
                "channel": 0,
                "start_ms": at_ms,
                "interval_ms": 1000,
                "count": 1,
            })

    if msg_schedules:
        config["message_schedule"] = config.get("message_schedule", []) + msg_schedules
    if chan_schedules:
        config["channel_schedule"] = config.get("channel_schedule", []) + chan_schedules

    # Enable hot_start for advert exchange
    config.setdefault("simulation", {})["hot_start"] = True

    # Output
    out_str = json.dumps(config, indent=2) + "\n"
    if args.output:
        with open(args.output, "w") as f:
            f.write(out_str)
    else:
        sys.stdout.write(out_str)

    # Stats
    if args.verbose or args.output:
        n_dm = sum(1 for e in all_events if e[2] == "dm")
        n_chan = sum(1 for e in all_events if e[2] == "channel")
        print(f"\nChatter overlay conversion:", file=sys.stderr)
        print(f"  Added {len(added_companions)} companions: {', '.join(added_companions)}",
              file=sys.stderr)
        print(f"  Traffic: {n_dm} DMs + {n_chan} channel = {n_dm + n_chan} total messages",
              file=sys.stderr)
        print(f"  Duration: {duration_s:.0f}s ({duration_s/60:.0f} min)",
              file=sys.stderr)
        print(f"  Seed: {args.seed}", file=sys.stderr)
        if all_events:
            print(f"  Time range: {all_events[0][0]:.0f}s - {all_events[-1][0]:.0f}s",
                  file=sys.stderr)
        per_comp = {}
        for _, cname, etype, _ in all_events:
            per_comp.setdefault(cname, {"dm": 0, "channel": 0})
            per_comp[cname][etype] += 1
        for cname in added_companions:
            c = per_comp.get(cname, {"dm": 0, "channel": 0})
            print(f"    {cname}: {c['dm']} DMs, {c['channel']} chan",
                  file=sys.stderr)
        if args.output:
            print(f"  Written to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
