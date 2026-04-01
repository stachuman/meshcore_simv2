#!/usr/bin/env python3
"""Sweep rxdelay/txdelay/direct.txdelay to maximize message delivery.

For each parameter combination, injects 'set' commands for all repeaters,
runs the orchestrator with multiple seeds, and reports mean delivery %.

Usage:
  python3 tools/optimize_delays.py simulation/real_network.json \
    --rxdelay 0.0:5.0:1.0 \
    --txdelay 0.0:1.0:0.2 \
    --direct-txdelay 0.0:0.5:0.1 \
    --seeds 3 \
    --orchestrator build/orchestrator/orchestrator \
    -j 4
"""

import argparse
import copy
import csv
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed


def parse_range(s):
    """Parse 'min:max:step' into list of float values."""
    parts = s.split(":")
    if len(parts) != 3:
        raise ValueError(f"Expected min:max:step, got: {s}")
    lo, hi, step = float(parts[0]), float(parts[1]), float(parts[2])
    if step <= 0:
        raise ValueError(f"Step must be > 0, got: {step}")
    vals = []
    v = lo
    while v <= hi + step * 0.01:  # small epsilon for float rounding
        vals.append(round(v, 4))
        v += step
    return vals


def run_single(orchestrator_path, config, rxd, txd, dtxd, seed, run_id):
    """Run one orchestrator invocation with a specific seed."""
    repeaters = [n["name"] for n in config["nodes"]
                 if n.get("role", "repeater") == "repeater"]

    cfg = copy.deepcopy(config)
    cfg.setdefault("simulation", {})["seed"] = seed

    # Inject set commands just after warmup (before first real messages)
    warmup_ms = cfg.get("simulation", {}).get("warmup_ms", 0)
    inject_ms = warmup_ms + 1
    set_cmds = []
    for rpt in repeaters:
        set_cmds.append({"at_ms": inject_ms, "node": rpt, "command": f"set rxdelay {rxd}"})
        set_cmds.append({"at_ms": inject_ms, "node": rpt, "command": f"set txdelay {txd}"})
        set_cmds.append({"at_ms": inject_ms, "node": rpt, "command": f"set direct.txdelay {dtxd}"})

    cfg["commands"] = set_cmds + cfg.get("commands", [])
    cfg.pop("expect", None)

    tmp_path = os.path.join(tempfile.gettempdir(), f"opt_delay_{run_id}.json")
    try:
        with open(tmp_path, "w") as f:
            json.dump(cfg, f)

        result = subprocess.run(
            [orchestrator_path, tmp_path],
            capture_output=True, text=True, timeout=600
        )

        stderr = result.stderr
        m = re.search(r"Delivery:\s+(\d+)/(\d+)\s+messages\s+\((\d+)%\)", stderr)
        if m:
            delivered = int(m.group(1))
            sent = int(m.group(2))
            pct = int(m.group(3))
        else:
            delivered, sent, pct = 0, 0, 0

        ack_m = re.search(r"Acks:\s+(\d+)/(\d+)\s+received\s+\((\d+)%\)", stderr)
        ack_pct = int(ack_m.group(3)) if ack_m else -1

        chan_m = re.search(r"Channel:\s+(\d+)/(\d+)\s+receptions\s+\((\d+)%\)", stderr)
        chan_pct = int(chan_m.group(3)) if chan_m else -1

        summary_match = re.search(r"(=== Simulation Summary.*)", stderr, re.DOTALL)
        summary = summary_match.group(1).strip() if summary_match else ""

        return (rxd, txd, dtxd, seed, delivered, sent, pct, ack_pct, chan_pct, summary)

    except subprocess.TimeoutExpired:
        return (rxd, txd, dtxd, seed, 0, 0, 0, -1, -1, "TIMEOUT")
    except Exception as e:
        return (rxd, txd, dtxd, seed, 0, 0, 0, -1, -1, f"ERROR: {e}")
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def main():
    parser = argparse.ArgumentParser(
        description="Sweep delay parameters to maximize message delivery"
    )
    parser.add_argument("config", help="Base orchestrator config JSON")
    parser.add_argument("--rxdelay", default="0.0:5.0:1.0",
                        help="rxdelay range as min:max:step (default: 0.0:5.0:1.0)")
    parser.add_argument("--txdelay", default="0.0:1.0:0.2",
                        help="txdelay range as min:max:step (default: 0.0:1.0:0.2)")
    parser.add_argument("--direct-txdelay", default="0.0:0.5:0.1",
                        help="direct.txdelay range as min:max:step (default: 0.0:0.5:0.1)")
    parser.add_argument("--seeds", type=int, default=3,
                        help="Seeds per combination (default: 3, seeds 42..42+N-1)")
    parser.add_argument("--seed-base", type=int, default=42,
                        help="First seed value (default: 42)")
    parser.add_argument("--orchestrator", default="build/orchestrator/orchestrator",
                        help="Path to orchestrator binary (default: build/orchestrator/orchestrator)")
    parser.add_argument("-j", "--jobs", type=int, default=1,
                        help="Parallel runs (default: 1)")
    parser.add_argument("--top", type=int, default=10,
                        help="Show top N results (default: 10)")
    parser.add_argument("-o", "--output", default=None,
                        help="Save full results to CSV")
    parser.add_argument("-v", "--verbose", action="store_true", default=False,
                        help="Show per-run orchestrator summary")

    args = parser.parse_args()

    if not os.path.isfile(args.orchestrator):
        print(f"ERROR: orchestrator not found at {args.orchestrator}", file=sys.stderr)
        sys.exit(1)

    with open(args.config) as f:
        config = json.load(f)

    rxd_vals = parse_range(args.rxdelay)
    txd_vals = parse_range(args.txdelay)
    dtxd_vals = parse_range(args.direct_txdelay)
    seeds = list(range(args.seed_base, args.seed_base + args.seeds))

    n_combos = len(rxd_vals) * len(txd_vals) * len(dtxd_vals)
    total_runs = n_combos * len(seeds)
    repeaters = [n["name"] for n in config["nodes"]
                 if n.get("role", "repeater") == "repeater"]

    print(f"Parameter sweep: {n_combos} combinations x {len(seeds)} seeds = {total_runs} runs",
          file=sys.stderr)
    print(f"  rxdelay:         {rxd_vals}", file=sys.stderr)
    print(f"  txdelay:         {txd_vals}", file=sys.stderr)
    print(f"  direct.txdelay:  {dtxd_vals}", file=sys.stderr)
    print(f"  seeds:           {seeds}", file=sys.stderr)
    print(f"  Repeaters:       {len(repeaters)}", file=sys.stderr)
    print(f"  Parallel jobs:   {args.jobs}", file=sys.stderr)
    print(f"", file=sys.stderr)

    # Build work items — all (combo, seed) pairs
    work = []
    run_id = 0
    for rxd in rxd_vals:
        for txd in txd_vals:
            for dtxd in dtxd_vals:
                for seed in seeds:
                    work.append((args.orchestrator, config, rxd, txd, dtxd, seed, run_id))
                    run_id += 1

    # Execute
    raw_results = []
    completed = 0
    t_start = time.monotonic()

    def report_progress(r):
        nonlocal completed
        completed += 1
        rxd, txd, dtxd, seed, delivered, sent, pct, ack_pct, chan_pct, summary = r
        elapsed = time.monotonic() - t_start
        avg = elapsed / completed
        eta = avg * (total_runs - completed)
        ack_str = f" ack={ack_pct}%" if ack_pct >= 0 else ""
        chan_str = f" chan={chan_pct}%" if chan_pct >= 0 else ""
        eta_str = f"{int(eta//60)}m{int(eta%60):02d}s" if eta >= 60 else f"{int(eta)}s"
        print(f"  [{completed:>{len(str(total_runs))}}/{total_runs}] "
              f"rxd={rxd:<5} txd={txd:<5} dtxd={dtxd:<5} seed={seed:<4} "
              f"-> {delivered}/{sent} ({pct}%){ack_str}{chan_str}  "
              f"[{elapsed:.0f}s elapsed, ~{eta_str} remaining]",
              file=sys.stderr)
        if args.verbose and summary:
            for line in summary.split("\n"):
                print(f"         {line}", file=sys.stderr)
            print(file=sys.stderr)

    if args.jobs == 1:
        for w in work:
            r = run_single(*w)
            raw_results.append(r)
            report_progress(r)
    else:
        with ProcessPoolExecutor(max_workers=args.jobs) as executor:
            futures = {executor.submit(run_single, *w): w for w in work}
            for future in as_completed(futures):
                r = future.result()
                raw_results.append(r)
                report_progress(r)

    # Aggregate per combo: group by (rxd, txd, dtxd)
    combos = {}  # (rxd, txd, dtxd) -> list of (delivered, sent, pct, ack_pct, chan_pct)
    for r in raw_results:
        rxd, txd, dtxd, seed, delivered, sent, pct, ack_pct, chan_pct, _ = r
        key = (rxd, txd, dtxd)
        combos.setdefault(key, []).append((delivered, sent, pct, ack_pct, chan_pct))

    aggregated = []
    for (rxd, txd, dtxd), runs in combos.items():
        pcts = [r[2] for r in runs]
        ack_pcts = [r[3] for r in runs if r[3] >= 0]
        chan_pcts = [r[4] for r in runs if r[4] >= 0]
        total_delivered = sum(r[0] for r in runs)
        total_sent = sum(r[1] for r in runs)

        mean_pct = sum(pcts) / len(pcts)
        std_pct = math.sqrt(sum((p - mean_pct) ** 2 for p in pcts) / len(pcts)) if len(pcts) > 1 else 0.0
        min_pct = min(pcts)
        max_pct = max(pcts)
        mean_ack = sum(ack_pcts) / len(ack_pcts) if ack_pcts else -1
        mean_chan = sum(chan_pcts) / len(chan_pcts) if chan_pcts else -1

        aggregated.append({
            "rxd": rxd, "txd": txd, "dtxd": dtxd,
            "mean_pct": mean_pct, "std_pct": std_pct,
            "min_pct": min_pct, "max_pct": max_pct,
            "total_delivered": total_delivered, "total_sent": total_sent,
            "mean_ack": mean_ack,
            "mean_chan": mean_chan,
            "n_seeds": len(runs),
        })

    # Sort by mean delivery % descending, then by std ascending (prefer stable)
    aggregated.sort(key=lambda a: (a["mean_pct"], -a["std_pct"]), reverse=True)

    t_total = time.monotonic() - t_start
    t_str = f"{int(t_total//60)}m{int(t_total%60):02d}s" if t_total >= 60 else f"{t_total:.1f}s"

    # Print results table
    print(f"\n{'='*85}", file=sys.stderr)
    print(f"  Completed {total_runs} runs ({n_combos} combos x {len(seeds)} seeds) in {t_str}",
          file=sys.stderr)
    print(f"{'='*85}", file=sys.stderr)
    top_n = min(args.top, len(aggregated))
    print(f"  Top {top_n} combinations (of {n_combos}):", file=sys.stderr)
    print(f"{'='*85}", file=sys.stderr)
    print(f"  {'rxdelay':>8}  {'txdelay':>8}  {'d.txdelay':>9}  "
          f"{'mean':>6}  {'std':>5}  {'min':>4}  {'max':>4}  "
          f"{'delivered':>9}  {'acks':>5}  {'chan':>5}",
          file=sys.stderr)
    print(f"  {'-'*8}  {'-'*8}  {'-'*9}  "
          f"{'-'*6}  {'-'*5}  {'-'*4}  {'-'*4}  "
          f"{'-'*9}  {'-'*5}  {'-'*5}",
          file=sys.stderr)
    for a in aggregated[:top_n]:
        ack_str = f"{a['mean_ack']:.0f}%" if a["mean_ack"] >= 0 else "n/a"
        chan_str = f"{a['mean_chan']:.0f}%" if a["mean_chan"] >= 0 else "n/a"
        print(f"  {a['rxd']:8.2f}  {a['txd']:8.2f}  {a['dtxd']:9.2f}  "
              f"{a['mean_pct']:5.1f}%  {a['std_pct']:4.1f}%  {a['min_pct']:3d}%  {a['max_pct']:3d}%  "
              f"{a['total_delivered']:>4}/{a['total_sent']:<4}  {ack_str:>5}  {chan_str:>5}",
              file=sys.stderr)

    # Best result
    if aggregated:
        best = aggregated[0]
        print(f"\nBest: rxdelay={best['rxd']}, txdelay={best['txd']}, "
              f"direct.txdelay={best['dtxd']} "
              f"-> {best['mean_pct']:.1f}% mean delivery "
              f"(std={best['std_pct']:.1f}%, range {best['min_pct']}-{best['max_pct']}%)",
              file=sys.stderr)

    # Save CSV
    if args.output:
        with open(args.output, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["rxdelay", "txdelay", "direct_txdelay",
                             "mean_delivery_pct", "std_pct", "min_pct", "max_pct",
                             "total_delivered", "total_sent", "mean_ack_pct",
                             "mean_chan_pct", "n_seeds"])
            for a in aggregated:
                writer.writerow([
                    a["rxd"], a["txd"], a["dtxd"],
                    round(a["mean_pct"], 1), round(a["std_pct"], 1),
                    a["min_pct"], a["max_pct"],
                    a["total_delivered"], a["total_sent"],
                    round(a["mean_ack"], 1) if a["mean_ack"] >= 0 else "",
                    round(a["mean_chan"], 1) if a["mean_chan"] >= 0 else "",
                    a["n_seeds"],
                ])
        print(f"Full results saved to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
