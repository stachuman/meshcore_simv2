#!/usr/bin/env python3
"""Print topology statistics from an orchestrator config JSON.

Shows per-repeater neighbor counts (SNR > 0), distribution histogram,
and summary statistics useful for understanding DelayTuning table coverage.

Usage:
  python3 tools/topology_stats.py simulation/gdansk_test.json
  python3 tools/topology_stats.py simulation/real_network.json --snr-threshold 3
"""

import argparse
import json
import sys
from collections import defaultdict


def main():
    parser = argparse.ArgumentParser(
        description="Topology statistics from orchestrator config"
    )
    parser.add_argument("config", help="Orchestrator config JSON")
    parser.add_argument("--snr-threshold", type=float, default=0.0,
                        help="Count neighbors with SNR above this (default: 0.0)")
    parser.add_argument("--all", action="store_true", default=False,
                        help="Include companions in listing (default: repeaters only)")

    args = parser.parse_args()

    with open(args.config) as f:
        config = json.load(f)

    nodes = {n["name"]: n.get("role", "repeater") for n in config["nodes"]}
    repeaters = {name for name, role in nodes.items() if role == "repeater"}
    companions = {name for name, role in nodes.items() if role == "companion"}
    links = config.get("topology", {}).get("links", [])

    # Count neighbors with SNR > threshold (directed: count each direction)
    # For bidir links, both A->B and B->A count
    neighbor_snr = defaultdict(list)  # node -> list of (neighbor, snr)

    for link in links:
        fr, to = link["from"], link["to"]
        snr = link.get("snr", 0.0)
        bidir = link.get("bidir", False)

        if snr > args.snr_threshold:
            neighbor_snr[fr].append((to, snr))
            if bidir:
                neighbor_snr[to].append((fr, snr))

    # Build per-node stats
    show_nodes = nodes.keys() if args.all else repeaters
    stats = []
    for name in sorted(show_nodes):
        neighbors = neighbor_snr.get(name, [])
        good_count = len(neighbors)
        snrs = [s for _, s in neighbors]
        avg_snr = sum(snrs) / len(snrs) if snrs else 0.0
        min_snr = min(snrs) if snrs else 0.0
        max_snr = max(snrs) if snrs else 0.0
        role = nodes[name]
        stats.append({
            "name": name, "role": role,
            "neighbors": good_count, "avg_snr": avg_snr,
            "min_snr": min_snr, "max_snr": max_snr,
        })

    # Sort by neighbor count descending
    stats.sort(key=lambda s: (-s["neighbors"], s["name"]))

    # Print per-node table
    print(f"{'Node':<30} {'Role':<10} {'Neighbors':>9} {'Avg SNR':>8} {'Min SNR':>8} {'Max SNR':>8}")
    print(f"{'-'*30} {'-'*10} {'-'*9} {'-'*8} {'-'*8} {'-'*8}")
    for s in stats:
        print(f"{s['name']:<30} {s['role']:<10} {s['neighbors']:>9} "
              f"{s['avg_snr']:>7.1f} {s['min_snr']:>8.1f} {s['max_snr']:>8.1f}")

    # Histogram of neighbor counts (repeaters only)
    rpt_stats = [s for s in stats if s["role"] == "repeater"]
    counts = [s["neighbors"] for s in rpt_stats]

    if not counts:
        print("\nNo repeaters found.", file=sys.stderr)
        return

    max_count = max(counts)
    hist = defaultdict(int)
    for c in counts:
        hist[c] += 1

    print(f"\n{'='*60}")
    print(f"  Neighbor count distribution (repeaters, SNR > {args.snr_threshold})")
    print(f"{'='*60}")

    max_bar = 40
    max_freq = max(hist.values())
    for n in range(max_count + 1):
        freq = hist.get(n, 0)
        bar_len = int(freq / max_freq * max_bar) if max_freq > 0 else 0
        bar = '#' * bar_len
        label = f"12+" if n == max_count and max_count >= 12 else f"{n:>3}"
        if freq > 0 or n <= max_count:
            print(f"  {label} neighbors: {bar} {freq}")

    print(f"\n{'='*60}")
    print(f"  Summary")
    print(f"{'='*60}")
    print(f"  Repeaters:       {len(rpt_stats)}")
    print(f"  Companions:      {len(companions)}")
    print(f"  Links:           {len(links)}")
    print(f"  SNR threshold:   {args.snr_threshold}")
    avg = sum(counts) / len(counts)
    median = sorted(counts)[len(counts) // 2]
    print(f"  Avg neighbors:   {avg:.1f}")
    print(f"  Median:          {median}")
    print(f"  Min:             {min(counts)} ({sum(1 for c in counts if c == min(counts))} nodes)")
    print(f"  Max:             {max(counts)}")
    print(f"  Isolated (0):    {sum(1 for c in counts if c == 0)}")

    # DelayTuning table coverage
    buckets = defaultdict(int)
    for c in counts:
        idx = min(c, 12)
        buckets[idx] += 1
    print(f"\n  DelayTuning table index usage:")
    for idx in range(13):
        n = buckets.get(idx, 0)
        pct = n / len(counts) * 100
        marker = " <--" if n > 0 and pct > 15 else ""
        if n > 0:
            print(f"    [{idx:>2}] {n:>3} repeaters ({pct:4.1f}%){marker}")

    # Message schedule statistics
    msg_scheds = config.get("message_schedule", [])
    chan_scheds = config.get("channel_schedule", [])
    duration_ms = config.get("simulation", {}).get("duration_ms", 0)

    if msg_scheds or chan_scheds:
        print(f"\n{'='*60}")
        print(f"  Message schedules")
        print(f"{'='*60}")

    if msg_scheds:
        total_msgs = 0
        ack_count = 0
        noack_count = 0
        senders = set()
        receivers = set()
        pairs = set()

        for s in msg_scheds:
            cnt = s.get("count", 1)
            if cnt == 0 and duration_ms > 0:
                start = s.get("start_ms", 10000)
                interval = s.get("interval_ms", 1)
                end = max(0, duration_ms - 10000)
                cnt = (end - start) // interval + 1 if end > start and interval > 0 else 1
            total_msgs += cnt
            senders.add(s["from"])
            receivers.add(s["to"])
            pairs.add((s["from"], s["to"]))
            if s.get("ack", False):
                ack_count += cnt
            else:
                noack_count += cnt

        print(f"  Private messages: {len(msg_scheds)} schedule(s), {total_msgs} messages total")
        print(f"    With ack:      {ack_count}")
        print(f"    Without ack:   {noack_count}")
        print(f"    Senders:       {len(senders)} ({', '.join(sorted(senders))})")
        print(f"    Receivers:     {len(receivers)} ({', '.join(sorted(receivers))})")
        print(f"    Unique pairs:  {len(pairs)}")

        # Per-pair breakdown (aggregate)
        pair_msgs = defaultdict(lambda: {"count": 0, "ack": False, "times": []})
        for s in msg_scheds:
            cnt = s.get("count", 1)
            if cnt == 0 and duration_ms > 0:
                start = s.get("start_ms", 10000)
                interval = s.get("interval_ms", 1)
                end = max(0, duration_ms - 10000)
                cnt = (end - start) // interval + 1 if end > start and interval > 0 else 1
            key = (s["from"], s["to"])
            pair_msgs[key]["count"] += cnt
            pair_msgs[key]["times"].append(s.get("start_ms", 0))
            if s.get("ack", False):
                pair_msgs[key]["ack"] = True

        print(f"\n    {'From':<15} {'To':<15} {'Count':>6} {'Ack':>5} {'Span':>10}")
        print(f"    {'-'*15} {'-'*15} {'-'*6} {'-'*5} {'-'*10}")
        for (fr, to), info in sorted(pair_msgs.items()):
            ack = "yes" if info["ack"] else "no"
            times = sorted(info["times"])
            span_s = (times[-1] - times[0]) / 1000 if len(times) > 1 else 0
            print(f"    {fr:<15} {to:<15} {info['count']:>6} {ack:>5} {span_s:>8.0f}s")

    if chan_scheds:
        total_chan = 0
        chan_senders = set()
        channels_used = set()

        for s in chan_scheds:
            cnt = s.get("count", 1)
            if cnt == 0 and duration_ms > 0:
                start = s.get("start_ms", 10000)
                interval = s.get("interval_ms", 1)
                end = max(0, duration_ms - 10000)
                cnt = (end - start) // interval + 1 if end > start and interval > 0 else 1
            total_chan += cnt
            chan_senders.add(s["from"])
            channels_used.add(s.get("channel", 0))

        print(f"\n  Channel broadcasts: {total_chan} messages total")
        print(f"    Channels:      {sorted(channels_used)}")
        print(f"    Senders:       {len(chan_senders)} ({', '.join(sorted(chan_senders))})")

        # Aggregate by (sender, channel)
        chan_agg = defaultdict(lambda: {"count": 0, "times": []})
        for s in chan_scheds:
            cnt = s.get("count", 1)
            if cnt == 0 and duration_ms > 0:
                start = s.get("start_ms", 10000)
                interval = s.get("interval_ms", 1)
                end = max(0, duration_ms - 10000)
                cnt = (end - start) // interval + 1 if end > start and interval > 0 else 1
            key = (s["from"], s.get("channel", 0))
            chan_agg[key]["count"] += cnt
            chan_agg[key]["times"].append(s.get("start_ms", 0))

        print(f"\n    {'From':<15} {'Channel':>8} {'Count':>6} {'Span':>10}")
        print(f"    {'-'*15} {'-'*8} {'-'*6} {'-'*10}")
        for (fr, ch), info in sorted(chan_agg.items()):
            times = sorted(info["times"])
            span_s = (times[-1] - times[0]) / 1000 if len(times) > 1 else 0
            print(f"    {fr:<15} {ch:>8} {info['count']:>6} {span_s:>8.0f}s")

    if msg_scheds or chan_scheds:
        total_all = sum(s.get("count", 1) for s in msg_scheds) + \
                    sum(s.get("count", 1) for s in chan_scheds)
        print(f"\n  Total scheduled messages: {total_all}")
        print(f"  Simulation duration:     {duration_ms/1000:.0f}s")


if __name__ == "__main__":
    main()
