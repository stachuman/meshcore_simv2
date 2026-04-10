#!/usr/bin/env python3
"""Sweep DelayTuning table parameters to maximize message delivery.

Parameterizes the 13-entry DelayTuning table as a linear model:
  value(n) = clamp(base + slope * n, clamp_min, clamp_max)

For each parameter combination: injects simulation.delay_tuning into the JSON
config and runs the orchestrator (built once with -DDELAY_TUNING_RUNTIME=ON).
No per-variant rebuild required.

Usage:
  python3 tools/optimize_tuning.py simulation/real_network.json \
    --tx-base 0.8:1.2:0.1 --tx-slope 0.02:0.05:0.01 \
    --dtx-base 0.3:0.5:0.05 --dtx-slope 0.01:0.04:0.01 \
    --rx-base 0.3:0.5:0.05 --rx-slope 0.01:0.04:0.01 \
    --seeds 3 --build-dir build-sweep \
    -j 4 -o results.csv
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


TABLE_SIZE = 13


def parse_range(s):
    """Parse 'min:max:step' or a single constant value into list of floats.

    Formats:
        '1.0'           -> [1.0]        (constant)
        '1.0:1.0:0'     -> [1.0]        (step=0 means constant)
        '0.0:5.0:1.0'   -> [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    """
    parts = s.split(":")
    if len(parts) == 1:
        return [float(parts[0])]
    if len(parts) != 3:
        raise ValueError(f"Expected value or min:max:step, got: {s}")
    lo, hi, step = float(parts[0]), float(parts[1]), float(parts[2])
    if step <= 0 or lo == hi:
        return [lo]
    n = max(0, int(round((hi - lo) / step)) + 1)
    return [round(lo + i * step, 4) for i in range(n)]


def generate_table(tx_b, tx_s, dtx_b, dtx_s, rx_b, rx_s, clamp_min, clamp_max):
    """Return list of (tx, dtx, rx) tuples for 0..12 neighbors."""
    table = []
    for n in range(TABLE_SIZE):
        tx  = max(clamp_min, min(clamp_max, round(tx_b  + tx_s  * n, 4)))
        dtx = max(clamp_min, min(clamp_max, round(dtx_b + dtx_s * n, 4)))
        rx  = max(clamp_min, min(clamp_max, round(rx_b  + rx_s  * n, 4)))
        table.append((tx, dtx, rx))
    return table


def format_table(table):
    """Format table for display."""
    lines = []
    for n, (tx, dtx, rx) in enumerate(table):
        lines.append(f"    [{n:2d}] tx={tx:.3f}  dtx={dtx:.3f}  rx={rx:.3f}")
    return "\n".join(lines)


def sanitize_config(config):
    """Strip delay-setting commands and inject autotune on."""
    cfg = copy.deepcopy(config)

    # Strip commands that disable auto-tune
    delay_cmds = {"set rxdelay", "set txdelay", "set direct.txdelay"}
    if "commands" in cfg:
        cfg["commands"] = [
            c for c in cfg["commands"]
            if not any(c.get("command", "").startswith(d) for d in delay_cmds)
        ]

    # Inject 'set autotune on' for all repeaters (using @repeaters shorthand)
    warmup_ms = cfg.get("simulation", {}).get("warmup_ms", 0)
    inject_ms = warmup_ms + 1
    autotune_cmd = {"at_ms": inject_ms, "node": "@repeaters",
                    "command": "set autotune on"}
    cfg["commands"] = [autotune_cmd] + cfg.get("commands", [])

    cfg.pop("expect", None)
    return cfg


def inject_delay_tuning(config_json_str, tx_b, tx_s, dtx_b, dtx_s, rx_b, rx_s,
                        clamp_min, clamp_max):
    """Inject simulation.delay_tuning params into config JSON string."""
    cfg = json.loads(config_json_str)
    cfg.setdefault("simulation", {})["delay_tuning"] = {
        "tx_base": tx_b, "tx_slope": tx_s,
        "dtx_base": dtx_b, "dtx_slope": dtx_s,
        "rx_base": rx_b, "rx_slope": rx_s,
        "clamp_min": clamp_min, "clamp_max": clamp_max,
    }
    return json.dumps(cfg)


def run_single(orchestrator_path, config_json_str, seed, run_id):
    """Run one orchestrator invocation with a specific seed."""
    cfg = json.loads(config_json_str)
    cfg.setdefault("simulation", {})["seed"] = seed

    tmp_path = os.path.join(tempfile.gettempdir(), f"opt_tuning_{run_id}.json")
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
            print(f"WARNING: run_id={run_id} seed={seed} — Delivery regex did not match",
                  file=sys.stderr)

        ack_m = re.search(r"Acks:\s+(\d+)/(\d+)\s+received\s+\((\d+)%\)", stderr)
        ack_pct = int(ack_m.group(3)) if ack_m else -1

        chan_m = re.search(r"Channel:\s+(\d+)/(\d+)\s+receptions\s+\((\d+)%\)", stderr)
        chan_pct = int(chan_m.group(3)) if chan_m else -1

        # Split delivery/ack by routing type (flood vs path)
        flood_del_m = re.search(r"Delivery \(flood\):\s+(\d+)/(\d+)\s+\((\d+)%\)", stderr)
        flood_delivery_pct = int(flood_del_m.group(3)) if flood_del_m else -1
        path_del_m = re.search(r"Delivery \(path\):\s+(\d+)/(\d+)\s+\((\d+)%\)", stderr)
        path_delivery_pct = int(path_del_m.group(3)) if path_del_m else -1
        flood_ack_m = re.search(r"Acks \(flood\):\s+(\d+)/(\d+)\s+\((\d+)%\)", stderr)
        flood_ack_pct = int(flood_ack_m.group(3)) if flood_ack_m else -1
        path_ack_m = re.search(r"Acks \(path\):\s+(\d+)/(\d+)\s+\((\d+)%\)", stderr)
        path_ack_pct = int(path_ack_m.group(3)) if path_ack_m else -1

        # Message fate stats
        fate = {"tracked": 0, "delivered": 0, "lost": 0,
                "del_collision": 0.0, "del_drop": 0.0, "del_ack_copies": 0.0,
                "lost_collision": 0.0, "lost_drop": 0.0, "lost_ack_copies": 0.0}
        fate_m = re.search(r"Message fate \((\d+) tracked, (\d+) delivered, (\d+) lost\)", stderr)
        if fate_m:
            fate["tracked"] = int(fate_m.group(1))
            fate["delivered"] = int(fate_m.group(2))
            fate["lost"] = int(fate_m.group(3))
        del_m = re.search(r"Per delivered message: mean tx=[\d.]+\s+rx=[\d.]+\s+collision=([\d.]+)\s+drop=([\d.]+)\s+ack_copies=([\d.]+)", stderr)
        if del_m:
            fate["del_collision"] = float(del_m.group(1))
            fate["del_drop"] = float(del_m.group(2))
            fate["del_ack_copies"] = float(del_m.group(3))
        lost_m = re.search(r"Per lost message:\s+mean tx=[\d.]+\s+rx=[\d.]+\s+collision=([\d.]+)\s+drop=([\d.]+)\s+ack_copies=([\d.]+)", stderr)
        if lost_m:
            fate["lost_collision"] = float(lost_m.group(1))
            fate["lost_drop"] = float(lost_m.group(2))
            fate["lost_ack_copies"] = float(lost_m.group(3))

        # Radio efficiency metrics
        radio_eff_m = re.search(r"Radio:.*\(([\d.]+)% rx efficiency\)", stderr)
        radio_eff_pct = float(radio_eff_m.group(1)) if radio_eff_m else -1.0
        ackpath_eff_m = re.search(r"ACK\+path radio:.*\(([\d.]+)% rx efficiency\)", stderr)
        ackpath_eff_pct = float(ackpath_eff_m.group(1)) if ackpath_eff_m else -1.0

        summary_match = re.search(r"(=== Simulation Summary.*)", stderr, re.DOTALL)
        summary = summary_match.group(1).strip() if summary_match else ""

        return (seed, delivered, sent, pct, ack_pct, chan_pct,
                flood_delivery_pct, path_delivery_pct, flood_ack_pct, path_ack_pct,
                fate, radio_eff_pct, ackpath_eff_pct, summary)

    except subprocess.TimeoutExpired:
        empty_fate = {"tracked": 0, "delivered": 0, "lost": 0,
                      "del_collision": 0.0, "del_drop": 0.0, "del_ack_copies": 0.0,
                      "lost_collision": 0.0, "lost_drop": 0.0, "lost_ack_copies": 0.0}
        return (seed, 0, 0, 0, -1, -1, -1, -1, -1, -1, empty_fate, -1.0, -1.0, "TIMEOUT")
    except Exception as e:
        empty_fate = {"tracked": 0, "delivered": 0, "lost": 0,
                      "del_collision": 0.0, "del_drop": 0.0, "del_ack_copies": 0.0,
                      "lost_collision": 0.0, "lost_drop": 0.0, "lost_ack_copies": 0.0}
        return (seed, 0, 0, 0, -1, -1, -1, -1, -1, -1, empty_fate, -1.0, -1.0, f"ERROR: {e}")
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def main():
    parser = argparse.ArgumentParser(
        description="Sweep DelayTuning table parameters to maximize delivery"
    )
    parser.add_argument("config", help="Base orchestrator config JSON")

    # Linear model parameters: value(n) = base + slope * n
    parser.add_argument("--tx-base", default="1.0",
                        help="tx_delay intercept (default: 1.0)")
    parser.add_argument("--tx-slope", default="0.02:0.04:0.005",
                        help="tx_delay per-neighbor slope (default: 0.02:0.04:0.005)")
    parser.add_argument("--dtx-base", default="0.4",
                        help="direct_tx_delay intercept (default: 0.4)")
    parser.add_argument("--dtx-slope", default="0.01:0.03:0.005",
                        help="direct_tx_delay slope (default: 0.01:0.03:0.005)")
    parser.add_argument("--rx-base", default="0.4",
                        help="rx_delay_base intercept (default: 0.4)")
    parser.add_argument("--rx-slope", default="0.01:0.03:0.005",
                        help="rx_delay_base slope (default: 0.01:0.03:0.005)")

    # Clamping
    parser.add_argument("--clamp-min", type=float, default=0.0,
                        help="Min value for table entries (default: 0.0)")
    parser.add_argument("--clamp-max", type=float, default=5.0,
                        help="Max value for table entries (default: 5.0)")

    # Execution
    parser.add_argument("--seeds", type=int, default=3,
                        help="Seeds per variant (default: 3)")
    parser.add_argument("--seed-base", type=int, default=42,
                        help="First seed value (default: 42)")
    parser.add_argument("--build-dir", default="build-fork",
                        help="CMake build directory (default: build-fork)")
    parser.add_argument("--meshcore-dir", default="../MeshCore-stachuman",
                        help="Path to MeshCore fork (default: ../MeshCore-stachuman)")
    parser.add_argument("-j", "--jobs", type=int, default=1,
                        help="Parallel runs (default: 1)")
    parser.add_argument("--top", type=int, default=10,
                        help="Show top N results (default: 10)")
    parser.add_argument("-o", "--output", default=None,
                        help="Save full results to CSV")
    parser.add_argument("-v", "--verbose", action="store_true", default=False,
                        help="Show per-run orchestrator summary")

    args = parser.parse_args()

    # --- Validate inputs ---
    if not os.path.isfile(args.config):
        print(f"ERROR: config file not found: {args.config}", file=sys.stderr)
        sys.exit(1)
    if not args.config.endswith(".json"):
        print(f"ERROR: config file does not look like JSON: {args.config}", file=sys.stderr)
        sys.exit(1)

    if not os.path.isdir(args.build_dir):
        print(f"ERROR: build directory not found: {args.build_dir}", file=sys.stderr)
        sys.exit(1)

    orchestrator_path = os.path.join(args.build_dir, "orchestrator", "orchestrator")
    if not os.path.isfile(orchestrator_path):
        print(f"ERROR: orchestrator not found at {orchestrator_path}", file=sys.stderr)
        print(f"  Build first: cmake -S . -B {args.build_dir} "
              f"-DCMAKE_BUILD_TYPE=NativeRelease "
              f"-DMESHCORE_DIR={args.meshcore_dir} "
              f"-DDELAY_TUNING_RUNTIME=ON && "
              f"cmake --build {args.build_dir}", file=sys.stderr)
        sys.exit(1)

    # --- Parse ranges ---
    tx_b_vals  = parse_range(args.tx_base)
    tx_s_vals  = parse_range(args.tx_slope)
    dtx_b_vals = parse_range(args.dtx_base)
    dtx_s_vals = parse_range(args.dtx_slope)
    rx_b_vals  = parse_range(args.rx_base)
    rx_s_vals  = parse_range(args.rx_slope)
    seeds = list(range(args.seed_base, args.seed_base + args.seeds))

    n_combos = (len(tx_b_vals) * len(tx_s_vals) * len(dtx_b_vals)
                * len(dtx_s_vals) * len(rx_b_vals) * len(rx_s_vals))
    total_runs = n_combos * len(seeds)

    with open(args.config) as f:
        config = json.load(f)

    base_cfg = sanitize_config(config)
    base_cfg_json = json.dumps(base_cfg)

    repeaters = [n["name"] for n in config["nodes"]
                 if n.get("role", "repeater") == "repeater"]

    print(f"DelayTuning sweep (runtime): {n_combos} variants x {len(seeds)} seeds = {total_runs} runs",
          file=sys.stderr)
    print(f"  tx_base:         {tx_b_vals}", file=sys.stderr)
    print(f"  tx_slope:        {tx_s_vals}", file=sys.stderr)
    print(f"  dtx_base:        {dtx_b_vals}", file=sys.stderr)
    print(f"  dtx_slope:       {dtx_s_vals}", file=sys.stderr)
    print(f"  rx_base:         {rx_b_vals}", file=sys.stderr)
    print(f"  rx_slope:        {rx_s_vals}", file=sys.stderr)
    print(f"  seeds:           {seeds}", file=sys.stderr)
    print(f"  Repeaters:       {len(repeaters)}", file=sys.stderr)
    print(f"  Parallel jobs:   {args.jobs}", file=sys.stderr)
    print(f"  Build dir:       {args.build_dir}", file=sys.stderr)
    print(f"  MeshCore fork:   {args.meshcore_dir}", file=sys.stderr)
    print(f"  Clamp range:     [{args.clamp_min}, {args.clamp_max}]", file=sys.stderr)
    print(f"  Mode:            runtime (no per-variant rebuild)", file=sys.stderr)
    print(f"", file=sys.stderr)

    # --- Build all variant parameter tuples ---
    variants = []
    for tx_b in tx_b_vals:
        for tx_s in tx_s_vals:
            for dtx_b in dtx_b_vals:
                for dtx_s in dtx_s_vals:
                    for rx_b in rx_b_vals:
                        for rx_s in rx_s_vals:
                            variants.append((tx_b, tx_s, dtx_b, dtx_s, rx_b, rx_s))

    raw_results = []
    completed_runs = 0
    completed_variants = 0
    t_start = time.monotonic()

    CSV_HEADER = ["tx_base", "tx_slope", "dtx_base", "dtx_slope",
                  "rx_base", "rx_slope",
                  "mean_delivery_pct", "std_pct", "min_pct", "max_pct",
                  "total_delivered", "total_sent", "mean_ack_pct",
                  "mean_chan_pct",
                  "mean_flood_delivery_pct", "mean_path_delivery_pct",
                  "mean_flood_ack_pct", "mean_path_ack_pct",
                  "n_seeds",
                  "fate_tracked", "fate_delivered", "fate_lost",
                  "lost_mean_collision", "lost_mean_drop",
                  "mean_radio_eff_pct", "mean_ackpath_eff_pct",
                  "del_mean_ack_copies", "lost_mean_ack_copies"]

    # Open CSV for incremental writes so results survive interruption
    csv_file = None
    csv_writer = None
    if args.output:
        csv_file = open(args.output, "w", newline="")
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow(CSV_HEADER)
        csv_file.flush()

    try:
        for vi, (tx_b, tx_s, dtx_b, dtx_s, rx_b, rx_s) in enumerate(variants):
            # Inject delay_tuning params into config (no rebuild needed)
            variant_cfg_json = inject_delay_tuning(
                base_cfg_json, tx_b, tx_s, dtx_b, dtx_s, rx_b, rx_s,
                args.clamp_min, args.clamp_max)

            elapsed = time.monotonic() - t_start
            print(f"\n  [{vi+1}/{n_combos}] Variant: "
                  f"tx={tx_b}+{tx_s}*n  dtx={dtx_b}+{dtx_s}*n  rx={rx_b}+{rx_s}*n",
                  file=sys.stderr)

            # --- Run seeds ---
            run_id_base = vi * len(seeds)
            if args.jobs == 1:
                for si, seed in enumerate(seeds):
                    r = run_single(orchestrator_path, variant_cfg_json, seed,
                                   run_id_base + si)
                    (seed_r, delivered, sent, pct, ack_pct, chan_pct,
                     flood_del_pct, path_del_pct, flood_ack_pct, path_ack_pct,
                     fate, radio_eff, ackpath_eff, summary) = r
                    raw_results.append((tx_b, tx_s, dtx_b, dtx_s, rx_b, rx_s,
                                        seed_r, delivered, sent, pct, ack_pct, chan_pct,
                                        flood_del_pct, path_del_pct, flood_ack_pct, path_ack_pct,
                                        fate, radio_eff, ackpath_eff, summary))
                    completed_runs += 1

                    elapsed = time.monotonic() - t_start
                    avg = elapsed / completed_runs
                    eta = avg * (total_runs - completed_runs)
                    eta_str = f"{int(eta//60)}m{int(eta%60):02d}s" if eta >= 60 else f"{int(eta)}s"
                    ack_str = f" ack={ack_pct}%" if ack_pct >= 0 else ""
                    chan_str = f" chan={chan_pct}%" if chan_pct >= 0 else ""
                    print(f"    seed={seed} -> {delivered}/{sent} ({pct}%)"
                          f"{ack_str}{chan_str}  [{elapsed:.0f}s, ~{eta_str} left]",
                          file=sys.stderr)
                    if args.verbose and summary:
                        for line in summary.split("\n"):
                            print(f"         {line}", file=sys.stderr)
            else:
                with ProcessPoolExecutor(max_workers=args.jobs) as executor:
                    futures = {}
                    for si, seed in enumerate(seeds):
                        fut = executor.submit(run_single, orchestrator_path,
                                              variant_cfg_json, seed, run_id_base + si)
                        futures[fut] = seed

                    for future in as_completed(futures):
                        r = future.result()
                        (seed_r, delivered, sent, pct, ack_pct, chan_pct,
                         flood_del_pct, path_del_pct, flood_ack_pct, path_ack_pct,
                         fate, radio_eff, ackpath_eff, summary) = r
                        raw_results.append((tx_b, tx_s, dtx_b, dtx_s, rx_b, rx_s,
                                            seed_r, delivered, sent, pct, ack_pct, chan_pct,
                                            flood_del_pct, path_del_pct, flood_ack_pct, path_ack_pct,
                                            fate, radio_eff, ackpath_eff, summary))
                        completed_runs += 1

                        elapsed = time.monotonic() - t_start
                        avg = elapsed / completed_runs
                        eta = avg * (total_runs - completed_runs)
                        eta_str = f"{int(eta//60)}m{int(eta%60):02d}s" if eta >= 60 else f"{int(eta)}s"
                        ack_str = f" ack={ack_pct}%" if ack_pct >= 0 else ""
                        chan_str = f" chan={chan_pct}%" if chan_pct >= 0 else ""
                        print(f"    seed={seed_r} -> {delivered}/{sent} ({pct}%)"
                              f"{ack_str}{chan_str}  [{elapsed:.0f}s, ~{eta_str} left]",
                              file=sys.stderr)
                        if args.verbose and summary:
                            for line in summary.split("\n"):
                                print(f"         {line}", file=sys.stderr)

            completed_variants += 1

            # --- Incremental CSV: aggregate this variant and append ---
            if csv_writer:
                variant_runs = [(d, s, p, a, c, fd, pd, fa, pa, ft, re_, ae) for
                                (tb, ts, db, ds, rb, rs, _, d, s, p, a, c, fd, pd, fa, pa, ft, re_, ae, _)
                                in raw_results[-len(seeds):]
                                if (tb, ts, db, ds, rb, rs) == (tx_b, tx_s, dtx_b, dtx_s, rx_b, rx_s)]
                if variant_runs:
                    v_pcts = [r[2] for r in variant_runs]
                    v_acks = [r[3] for r in variant_runs if r[3] >= 0]
                    v_chans = [r[4] for r in variant_runs if r[4] >= 0]
                    v_flood_dels = [r[5] for r in variant_runs if r[5] >= 0]
                    v_path_dels = [r[6] for r in variant_runs if r[6] >= 0]
                    v_flood_acks = [r[7] for r in variant_runs if r[7] >= 0]
                    v_path_acks = [r[8] for r in variant_runs if r[8] >= 0]
                    v_radio_effs = [r[10] for r in variant_runs if r[10] >= 0]
                    v_ackpath_effs = [r[11] for r in variant_runs if r[11] >= 0]
                    v_mean = sum(v_pcts) / len(v_pcts)
                    v_std = (math.sqrt(sum((p - v_mean) ** 2 for p in v_pcts) / (len(v_pcts) - 1))
                             if len(v_pcts) > 1 else 0.0)
                    v_del = sum(r[0] for r in variant_runs)
                    v_sent = sum(r[1] for r in variant_runs)
                    v_ack = sum(v_acks) / len(v_acks) if v_acks else -1
                    v_chan = sum(v_chans) / len(v_chans) if v_chans else -1
                    v_flood_del = sum(v_flood_dels) / len(v_flood_dels) if v_flood_dels else -1
                    v_path_del = sum(v_path_dels) / len(v_path_dels) if v_path_dels else -1
                    v_flood_ack = sum(v_flood_acks) / len(v_flood_acks) if v_flood_acks else -1
                    v_path_ack = sum(v_path_acks) / len(v_path_acks) if v_path_acks else -1
                    v_radio_eff = sum(v_radio_effs) / len(v_radio_effs) if v_radio_effs else -1
                    v_ackpath_eff = sum(v_ackpath_effs) / len(v_ackpath_effs) if v_ackpath_effs else -1
                    # Fate stats: sum across seeds
                    v_fate_tracked = sum(r[9]["tracked"] for r in variant_runs)
                    v_fate_del = sum(r[9]["delivered"] for r in variant_runs)
                    v_fate_lost = sum(r[9]["lost"] for r in variant_runs)
                    lost_cols = [r[9]["lost_collision"] for r in variant_runs if r[9]["lost"] > 0]
                    lost_drps = [r[9]["lost_drop"] for r in variant_runs if r[9]["lost"] > 0]
                    v_lost_col = sum(lost_cols) / len(lost_cols) if lost_cols else 0.0
                    v_lost_drp = sum(lost_drps) / len(lost_drps) if lost_drps else 0.0
                    del_acks = [r[9]["del_ack_copies"] for r in variant_runs if r[9]["delivered"] > 0]
                    lost_acks = [r[9]["lost_ack_copies"] for r in variant_runs if r[9]["lost"] > 0]
                    v_del_ack = sum(del_acks) / len(del_acks) if del_acks else 0.0
                    v_lost_ack = sum(lost_acks) / len(lost_acks) if lost_acks else 0.0
                    csv_writer.writerow([
                        tx_b, tx_s, dtx_b, dtx_s, rx_b, rx_s,
                        round(v_mean, 1), round(v_std, 1),
                        min(v_pcts), max(v_pcts),
                        v_del, v_sent,
                        round(v_ack, 1) if v_ack >= 0 else "",
                        round(v_chan, 1) if v_chan >= 0 else "",
                        round(v_flood_del, 1) if v_flood_del >= 0 else "",
                        round(v_path_del, 1) if v_path_del >= 0 else "",
                        round(v_flood_ack, 1) if v_flood_ack >= 0 else "",
                        round(v_path_ack, 1) if v_path_ack >= 0 else "",
                        len(variant_runs),
                        v_fate_tracked, v_fate_del, v_fate_lost,
                        round(v_lost_col, 1), round(v_lost_drp, 1),
                        round(v_radio_eff, 1) if v_radio_eff >= 0 else "",
                        round(v_ackpath_eff, 1) if v_ackpath_eff >= 0 else "",
                        round(v_del_ack, 1), round(v_lost_ack, 1),
                    ])
                    csv_file.flush()

    finally:
        if csv_file:
            csv_file.close()

    # --- Aggregate per variant ---
    combos = {}
    for r in raw_results:
        (tx_b, tx_s, dtx_b, dtx_s, rx_b, rx_s, seed, delivered, sent, pct,
         ack_pct, chan_pct, flood_del_pct, path_del_pct, flood_ack_pct, path_ack_pct,
         fate, radio_eff, ackpath_eff, _) = r
        key = (tx_b, tx_s, dtx_b, dtx_s, rx_b, rx_s)
        combos.setdefault(key, []).append(
            (delivered, sent, pct, ack_pct, chan_pct,
             flood_del_pct, path_del_pct, flood_ack_pct, path_ack_pct, fate,
             radio_eff, ackpath_eff))

    aggregated = []
    for (tx_b, tx_s, dtx_b, dtx_s, rx_b, rx_s), runs in combos.items():
        pcts = [r[2] for r in runs]
        ack_pcts = [r[3] for r in runs if r[3] >= 0]
        chan_pcts = [r[4] for r in runs if r[4] >= 0]
        flood_del_pcts = [r[5] for r in runs if r[5] >= 0]
        path_del_pcts = [r[6] for r in runs if r[6] >= 0]
        flood_ack_pcts = [r[7] for r in runs if r[7] >= 0]
        path_ack_pcts = [r[8] for r in runs if r[8] >= 0]
        radio_effs = [r[10] for r in runs if r[10] >= 0]
        ackpath_effs = [r[11] for r in runs if r[11] >= 0]
        total_delivered = sum(r[0] for r in runs)
        total_sent = sum(r[1] for r in runs)

        mean_pct = sum(pcts) / len(pcts)
        std_pct = (math.sqrt(sum((p - mean_pct) ** 2 for p in pcts) / (len(pcts) - 1))
                   if len(pcts) > 1 else 0.0)
        min_pct = min(pcts)
        max_pct = max(pcts)
        mean_ack = sum(ack_pcts) / len(ack_pcts) if ack_pcts else -1
        mean_chan = sum(chan_pcts) / len(chan_pcts) if chan_pcts else -1
        mean_flood_del = sum(flood_del_pcts) / len(flood_del_pcts) if flood_del_pcts else -1
        mean_path_del = sum(path_del_pcts) / len(path_del_pcts) if path_del_pcts else -1
        mean_flood_ack = sum(flood_ack_pcts) / len(flood_ack_pcts) if flood_ack_pcts else -1
        mean_path_ack = sum(path_ack_pcts) / len(path_ack_pcts) if path_ack_pcts else -1
        mean_radio_eff = sum(radio_effs) / len(radio_effs) if radio_effs else -1
        mean_ackpath_eff = sum(ackpath_effs) / len(ackpath_effs) if ackpath_effs else -1

        # Fate stats
        fate_tracked = sum(r[9]["tracked"] for r in runs)
        fate_delivered = sum(r[9]["delivered"] for r in runs)
        fate_lost = sum(r[9]["lost"] for r in runs)
        lost_cols = [r[9]["lost_collision"] for r in runs if r[9]["lost"] > 0]
        lost_drps = [r[9]["lost_drop"] for r in runs if r[9]["lost"] > 0]
        mean_lost_col = sum(lost_cols) / len(lost_cols) if lost_cols else 0.0
        mean_lost_drp = sum(lost_drps) / len(lost_drps) if lost_drps else 0.0
        del_ack_cs = [r[9]["del_ack_copies"] for r in runs if r[9]["delivered"] > 0]
        lost_ack_cs = [r[9]["lost_ack_copies"] for r in runs if r[9]["lost"] > 0]
        mean_del_ack = sum(del_ack_cs) / len(del_ack_cs) if del_ack_cs else 0.0
        mean_lost_ack = sum(lost_ack_cs) / len(lost_ack_cs) if lost_ack_cs else 0.0

        aggregated.append({
            "tx_b": tx_b, "tx_s": tx_s,
            "dtx_b": dtx_b, "dtx_s": dtx_s,
            "rx_b": rx_b, "rx_s": rx_s,
            "mean_pct": mean_pct, "std_pct": std_pct,
            "min_pct": min_pct, "max_pct": max_pct,
            "total_delivered": total_delivered, "total_sent": total_sent,
            "mean_ack": mean_ack, "mean_chan": mean_chan,
            "mean_flood_del": mean_flood_del, "mean_path_del": mean_path_del,
            "mean_flood_ack": mean_flood_ack, "mean_path_ack": mean_path_ack,
            "n_seeds": len(runs),
            "fate_tracked": fate_tracked, "fate_delivered": fate_delivered,
            "fate_lost": fate_lost,
            "mean_lost_col": mean_lost_col, "mean_lost_drp": mean_lost_drp,
            "mean_radio_eff": mean_radio_eff, "mean_ackpath_eff": mean_ackpath_eff,
            "mean_del_ack": mean_del_ack, "mean_lost_ack": mean_lost_ack,
        })

    aggregated.sort(key=lambda a: (a["mean_pct"], -a["std_pct"]), reverse=True)

    t_total = time.monotonic() - t_start
    t_str = f"{int(t_total//60)}m{int(t_total%60):02d}s" if t_total >= 60 else f"{t_total:.1f}s"

    # --- Print results ---
    print(f"\n{'='*125}", file=sys.stderr)
    print(f"  Completed {total_runs} runs ({n_combos} variants x {len(seeds)} seeds) in {t_str}",
          file=sys.stderr)
    print(f"{'='*125}", file=sys.stderr)
    top_n = min(args.top, len(aggregated))
    print(f"  Top {top_n} variants (of {n_combos}):", file=sys.stderr)
    print(f"{'='*125}", file=sys.stderr)
    print(f"  {'tx_b':>6} {'tx_s':>6}  {'dtx_b':>6} {'dtx_s':>6}  {'rx_b':>6} {'rx_s':>6}  "
          f"{'mean':>6}  {'std':>5}  {'min':>4}  {'max':>4}  "
          f"{'delivered':>9}  {'acks':>5}  {'chan':>5}  "
          f"{'F_del':>5}  {'P_del':>5}  {'F_ack':>5}  {'P_ack':>5}  "
          f"{'r_eff':>5}  {'ap_eff':>6}  "
          f"{'col/lost':>8}  {'drp/lost':>8}  {'ack/del':>7}",
          file=sys.stderr)
    print(f"  {'-'*6} {'-'*6}  {'-'*6} {'-'*6}  {'-'*6} {'-'*6}  "
          f"{'-'*6}  {'-'*5}  {'-'*4}  {'-'*4}  "
          f"{'-'*9}  {'-'*5}  {'-'*5}  "
          f"{'-'*5}  {'-'*5}  {'-'*5}  {'-'*5}  "
          f"{'-'*5}  {'-'*6}  "
          f"{'-'*8}  {'-'*8}  {'-'*7}",
          file=sys.stderr)
    for a in aggregated[:top_n]:
        ack_str = f"{a['mean_ack']:.0f}%" if a["mean_ack"] >= 0 else "n/a"
        chan_str = f"{a['mean_chan']:.0f}%" if a["mean_chan"] >= 0 else "n/a"
        fd_str = f"{a['mean_flood_del']:.0f}%" if a["mean_flood_del"] >= 0 else "n/a"
        pd_str = f"{a['mean_path_del']:.0f}%" if a["mean_path_del"] >= 0 else "n/a"
        fa_str = f"{a['mean_flood_ack']:.0f}%" if a["mean_flood_ack"] >= 0 else "n/a"
        pa_str = f"{a['mean_path_ack']:.0f}%" if a["mean_path_ack"] >= 0 else "n/a"
        re_str = f"{a['mean_radio_eff']:.0f}%" if a["mean_radio_eff"] >= 0 else "n/a"
        ae_str = f"{a['mean_ackpath_eff']:.0f}%" if a["mean_ackpath_eff"] >= 0 else "n/a"
        col_str = f"{a['mean_lost_col']:.1f}" if a["fate_lost"] > 0 else "n/a"
        drp_str = f"{a['mean_lost_drp']:.1f}" if a["fate_lost"] > 0 else "n/a"
        ack_c_str = f"{a['mean_del_ack']:.1f}" if a["fate_delivered"] > 0 else "n/a"
        print(f"  {a['tx_b']:6.3f} {a['tx_s']:6.4f}  "
              f"{a['dtx_b']:6.3f} {a['dtx_s']:6.4f}  "
              f"{a['rx_b']:6.3f} {a['rx_s']:6.4f}  "
              f"{a['mean_pct']:5.1f}%  {a['std_pct']:4.1f}%  "
              f"{a['min_pct']:3d}%  {a['max_pct']:3d}%  "
              f"{a['total_delivered']:>4}/{a['total_sent']:<4}  "
              f"{ack_str:>5}  {chan_str:>5}  "
              f"{fd_str:>5}  {pd_str:>5}  {fa_str:>5}  {pa_str:>5}  "
              f"{re_str:>5}  {ae_str:>6}  "
              f"{col_str:>8}  {drp_str:>8}  {ack_c_str:>7}",
              file=sys.stderr)

    # --- Best result with full table ---
    if aggregated:
        best = aggregated[0]
        table = generate_table(best["tx_b"], best["tx_s"],
                               best["dtx_b"], best["dtx_s"],
                               best["rx_b"], best["rx_s"],
                               args.clamp_min, args.clamp_max)
        print(f"\nBest: tx={best['tx_b']}+{best['tx_s']}*n  "
              f"dtx={best['dtx_b']}+{best['dtx_s']}*n  "
              f"rx={best['rx_b']}+{best['rx_s']}*n",
              file=sys.stderr)
        print(f"  -> {best['mean_pct']:.1f}% mean delivery "
              f"(std={best['std_pct']:.1f}%, range {best['min_pct']}-{best['max_pct']}%)",
              file=sys.stderr)
        print(f"\n  Generated table:", file=sys.stderr)
        print(format_table(table), file=sys.stderr)

        # Print copy-paste C array
        print(f"\n  C array for DelayTuning.h:", file=sys.stderr)
        print(f"  static const DelayTuning DELAY_TUNING_TABLE[] = {{", file=sys.stderr)
        for n, (tx, dtx, rx) in enumerate(table):
            print(f"    {{{tx:.3f}f, {dtx:.3f}f, {rx:.3f}f}},  // {n} neighbors",
                  file=sys.stderr)
        print(f"  }};", file=sys.stderr)

    # --- Rewrite CSV sorted by delivery % ---
    if args.output:
        with open(args.output, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADER)
            for a in aggregated:
                writer.writerow([
                    a["tx_b"], a["tx_s"], a["dtx_b"], a["dtx_s"],
                    a["rx_b"], a["rx_s"],
                    round(a["mean_pct"], 1), round(a["std_pct"], 1),
                    a["min_pct"], a["max_pct"],
                    a["total_delivered"], a["total_sent"],
                    round(a["mean_ack"], 1) if a["mean_ack"] >= 0 else "",
                    round(a["mean_chan"], 1) if a["mean_chan"] >= 0 else "",
                    round(a["mean_flood_del"], 1) if a["mean_flood_del"] >= 0 else "",
                    round(a["mean_path_del"], 1) if a["mean_path_del"] >= 0 else "",
                    round(a["mean_flood_ack"], 1) if a["mean_flood_ack"] >= 0 else "",
                    round(a["mean_path_ack"], 1) if a["mean_path_ack"] >= 0 else "",
                    a["n_seeds"],
                    a["fate_tracked"], a["fate_delivered"], a["fate_lost"],
                    round(a["mean_lost_col"], 1), round(a["mean_lost_drp"], 1),
                    round(a["mean_radio_eff"], 1) if a["mean_radio_eff"] >= 0 else "",
                    round(a["mean_ackpath_eff"], 1) if a["mean_ackpath_eff"] >= 0 else "",
                    round(a["mean_del_ack"], 1), round(a["mean_lost_ack"], 1),
                ])
        print(f"Full results saved to {args.output} (sorted by delivery %)", file=sys.stderr)


if __name__ == "__main__":
    main()
