#!/usr/bin/env python3
"""Run mixed-firmware experiment configs and collect results.

Executes all configs from generate_configs.py, parses sim_summary from
each run, aggregates across seeds, and writes a results CSV.

Usage:
  python3 run_mixed.py \
    --config-dir mixed_firmware_validation/configs \
    --orchestrator build/orchestrator/orchestrator \
    -j 6 -o mixed_firmware_validation/results/results.csv
"""

import argparse
import csv
import json
import math
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed


def _safe_pct(num, den):
    return int(round(num * 100.0 / den)) if den > 0 else -1


def _parse_sim_summary(stdout):
    for line in stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
            if obj.get("type") == "sim_summary":
                return obj
        except json.JSONDecodeError:
            continue
    return None


EMPTY_FATE = {
    "tracked": 0, "delivered": 0, "lost": 0,
    "del_collision": 0.0, "del_drop": 0.0, "del_ack_copies": 0.0,
    "lost_collision": 0.0, "lost_drop": 0.0, "lost_ack_copies": 0.0,
}


def run_single(orchestrator_path, config_path):
    """Run one simulation, return parsed metrics."""
    try:
        result = subprocess.run(
            [orchestrator_path, config_path],
            capture_output=True, text=True, timeout=600)

        s = _parse_sim_summary(result.stdout)
        if not s:
            print(f"WARNING: no sim_summary from {config_path}", file=sys.stderr)
            return None

        d = s.get("delivery", {})
        a = s.get("acks", {})
        ch = s.get("channel", {})
        df = d.get("flood", {})
        dp = d.get("path", {})
        af = a.get("flood", {})
        ap = a.get("path", {})
        r = s.get("radio", {})
        apr = s.get("ackpath_radio", {})

        fate = dict(EMPTY_FATE)
        fm = s.get("fate", {})
        if fm:
            fate["tracked"] = fm.get("tracked", 0)
            fate["delivered"] = fm.get("delivered", 0)
            fate["lost"] = fm.get("lost", 0)
            dm = fm.get("delivered_mean", {})
            fate["del_collision"] = dm.get("collision", 0.0)
            fate["del_drop"] = dm.get("drop", 0.0)
            fate["del_ack_copies"] = dm.get("ack_copies", 0.0)
            lm = fm.get("lost_mean", {})
            fate["lost_collision"] = lm.get("collision", 0.0)
            fate["lost_drop"] = lm.get("drop", 0.0)
            fate["lost_ack_copies"] = lm.get("ack_copies", 0.0)

        return {
            "delivered": d.get("received", 0),
            "sent": d.get("sent", 0),
            "pct": _safe_pct(d.get("received", 0), d.get("sent", 0)),
            "ack_pct": _safe_pct(a.get("received", 0), a.get("pending", 0)),
            "chan_pct": _safe_pct(ch.get("received", 0), ch.get("expected", 0)),
            "flood_del_pct": _safe_pct(df.get("received", 0), df.get("sent", 0)),
            "path_del_pct": _safe_pct(dp.get("received", 0), dp.get("sent", 0)),
            "flood_ack_pct": _safe_pct(af.get("received", 0), af.get("pending", 0)),
            "path_ack_pct": _safe_pct(ap.get("received", 0), ap.get("pending", 0)),
            "radio_eff": r.get("rx_efficiency", -1.0),
            "ackpath_eff": apr.get("rx_efficiency", -1.0),
            "fate": fate,
        }

    except subprocess.TimeoutExpired:
        print(f"TIMEOUT: {config_path}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"ERROR: {config_path}: {e}", file=sys.stderr)
        return None


def aggregate_variant(runs):
    """Aggregate results across seeds for one (strategy, pct) pair."""
    pcts = [r["pct"] for r in runs]
    ack_pcts = [r["ack_pct"] for r in runs if r["ack_pct"] >= 0]
    chan_pcts = [r["chan_pct"] for r in runs if r["chan_pct"] >= 0]
    flood_dels = [r["flood_del_pct"] for r in runs if r["flood_del_pct"] >= 0]
    path_dels = [r["path_del_pct"] for r in runs if r["path_del_pct"] >= 0]
    flood_acks = [r["flood_ack_pct"] for r in runs if r["flood_ack_pct"] >= 0]
    path_acks = [r["path_ack_pct"] for r in runs if r["path_ack_pct"] >= 0]
    radio_effs = [r["radio_eff"] for r in runs if r["radio_eff"] >= 0]
    ackpath_effs = [r["ackpath_eff"] for r in runs if r["ackpath_eff"] >= 0]

    mean_pct = sum(pcts) / len(pcts)
    std_pct = (math.sqrt(sum((p - mean_pct) ** 2 for p in pcts) / (len(pcts) - 1))
               if len(pcts) > 1 else 0.0)

    def safe_mean(lst):
        return sum(lst) / len(lst) if lst else -1

    fate_tracked = sum(r["fate"]["tracked"] for r in runs)
    fate_delivered = sum(r["fate"]["delivered"] for r in runs)
    fate_lost = sum(r["fate"]["lost"] for r in runs)
    lost_cols = [r["fate"]["lost_collision"] for r in runs if r["fate"]["lost"] > 0]
    lost_drps = [r["fate"]["lost_drop"] for r in runs if r["fate"]["lost"] > 0]
    del_acks = [r["fate"]["del_ack_copies"] for r in runs if r["fate"]["delivered"] > 0]
    lost_acks = [r["fate"]["lost_ack_copies"] for r in runs if r["fate"]["lost"] > 0]

    return {
        "mean_pct": mean_pct, "std_pct": std_pct,
        "min_pct": min(pcts), "max_pct": max(pcts),
        "total_delivered": sum(r["delivered"] for r in runs),
        "total_sent": sum(r["sent"] for r in runs),
        "mean_ack": safe_mean(ack_pcts),
        "mean_chan": safe_mean(chan_pcts),
        "mean_flood_del": safe_mean(flood_dels),
        "mean_path_del": safe_mean(path_dels),
        "mean_flood_ack": safe_mean(flood_acks),
        "mean_path_ack": safe_mean(path_acks),
        "mean_radio_eff": safe_mean(radio_effs),
        "mean_ackpath_eff": safe_mean(ackpath_effs),
        "n_seeds": len(runs),
        "fate_tracked": fate_tracked,
        "fate_delivered": fate_delivered,
        "fate_lost": fate_lost,
        "mean_lost_col": safe_mean(lost_cols) if lost_cols else 0.0,
        "mean_lost_drp": safe_mean(lost_drps) if lost_drps else 0.0,
        "mean_del_ack": safe_mean(del_acks) if del_acks else 0.0,
        "mean_lost_ack": safe_mean(lost_acks) if lost_acks else 0.0,
    }


CSV_HEADER = [
    "strategy", "pct_optimized", "n_optimized",
    "mean_delivery_pct", "std_pct", "min_pct", "max_pct",
    "total_delivered", "total_sent",
    "mean_ack_pct", "mean_chan_pct",
    "mean_flood_delivery_pct", "mean_path_delivery_pct",
    "mean_flood_ack_pct", "mean_path_ack_pct",
    "mean_radio_eff_pct", "mean_ackpath_eff_pct",
    "n_seeds",
    "fate_tracked", "fate_delivered", "fate_lost",
    "lost_mean_collision", "lost_mean_drop",
    "del_mean_ack_copies", "lost_mean_ack_copies",
]


def main():
    parser = argparse.ArgumentParser(description="Run mixed-firmware experiment")
    parser.add_argument("--config-dir", required=True, help="Directory with generated configs")
    parser.add_argument("--orchestrator", default=None)
    parser.add_argument("-j", "--jobs", type=int, default=1)
    parser.add_argument("-o", "--output", required=True, help="Output CSV path")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    # Find orchestrator
    if args.orchestrator:
        orch = args.orchestrator
    else:
        project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        orch = os.path.join(project_dir, "build", "orchestrator", "orchestrator")
    if not os.path.isfile(orch):
        print(f"ERROR: orchestrator not found at {orch}", file=sys.stderr)
        sys.exit(1)

    # Discover configs
    configs = []
    for fname in sorted(os.listdir(args.config_dir)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(args.config_dir, fname)
        with open(path) as f:
            meta = json.load(f).get("_mixed_experiment", {})
        if not meta:
            print(f"  SKIP {fname}: no _mixed_experiment metadata", file=sys.stderr)
            continue
        configs.append((path, meta))

    if not configs:
        print("ERROR: no configs found", file=sys.stderr)
        sys.exit(1)

    # Group by (strategy, pct)
    groups = {}
    for path, meta in configs:
        key = (meta["strategy"], meta["pct_optimized"])
        groups.setdefault(key, []).append((path, meta))

    total_runs = len(configs)
    n_groups = len(groups)
    print(f"Mixed-firmware experiment: {n_groups} variants, {total_runs} total runs",
          file=sys.stderr)
    print(f"  orchestrator: {orch}", file=sys.stderr)
    print(f"  parallel jobs: {args.jobs}", file=sys.stderr)

    t_start = time.monotonic()
    completed = 0

    # Run all configs, collect results keyed by config path
    results = {}
    if args.jobs <= 1:
        for path, meta in configs:
            r = run_single(orch, path)
            results[path] = r
            completed += 1
            _print_progress(path, r, completed, total_runs, t_start)
    else:
        with ProcessPoolExecutor(max_workers=args.jobs) as executor:
            futures = {}
            for path, meta in configs:
                fut = executor.submit(run_single, orch, path)
                futures[fut] = (path, meta)
            for future in as_completed(futures):
                path, meta = futures[future]
                r = future.result()
                results[path] = r
                completed += 1
                _print_progress(path, r, completed, total_runs, t_start)

    # Aggregate by (strategy, pct) and write CSV
    aggregated = []
    for (strategy, pct), entries in sorted(groups.items()):
        runs = [results[p] for p, _ in entries if results.get(p) is not None]
        if not runs:
            continue
        n_opt = entries[0][1]["n_optimized"]
        agg = aggregate_variant(runs)
        aggregated.append((strategy, pct, n_opt, agg))

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        for strategy, pct, n_opt, a in aggregated:
            writer.writerow([
                strategy, pct, n_opt,
                round(a["mean_pct"], 1), round(a["std_pct"], 1),
                a["min_pct"], a["max_pct"],
                a["total_delivered"], a["total_sent"],
                _fmt(a["mean_ack"]), _fmt(a["mean_chan"]),
                _fmt(a["mean_flood_del"]), _fmt(a["mean_path_del"]),
                _fmt(a["mean_flood_ack"]), _fmt(a["mean_path_ack"]),
                _fmt(a["mean_radio_eff"]), _fmt(a["mean_ackpath_eff"]),
                a["n_seeds"],
                a["fate_tracked"], a["fate_delivered"], a["fate_lost"],
                round(a["mean_lost_col"], 1), round(a["mean_lost_drp"], 1),
                round(a["mean_del_ack"], 2), round(a["mean_lost_ack"], 2),
            ])

    elapsed = time.monotonic() - t_start
    _print_summary_table(aggregated, elapsed)
    print(f"\nResults saved to {args.output}", file=sys.stderr)


def _fmt(val):
    return round(val, 1) if val >= 0 else ""


def _print_progress(path, result, completed, total, t_start):
    elapsed = time.monotonic() - t_start
    avg = elapsed / completed
    eta = avg * (total - completed)
    eta_s = f"{int(eta//60)}m{int(eta%60):02d}s" if eta >= 60 else f"{int(eta)}s"
    fname = os.path.basename(path)
    if result:
        pct = result["pct"]
        ack = result["ack_pct"]
        ack_s = f" ack={ack}%" if ack >= 0 else ""
        print(f"  [{completed}/{total}] {fname} -> {pct}%{ack_s}  [~{eta_s} left]",
              file=sys.stderr)
    else:
        print(f"  [{completed}/{total}] {fname} -> FAILED  [~{eta_s} left]",
              file=sys.stderr)


def _print_summary_table(aggregated, elapsed):
    t_str = f"{int(elapsed//60)}m{int(elapsed%60):02d}s" if elapsed >= 60 else f"{elapsed:.1f}s"
    print(f"\n{'='*110}", file=sys.stderr)
    print(f"  Mixed-firmware validation results ({t_str})", file=sys.stderr)
    print(f"{'='*110}", file=sys.stderr)
    print(f"  {'strategy':<10} {'pct':>5} {'n_opt':>5}  "
          f"{'delivery':>8} {'std':>5}  {'ack':>5}  {'chan':>5}  "
          f"{'F_del':>5}  {'P_del':>5}  {'F_ack':>5}  {'P_ack':>5}  "
          f"{'col/lost':>8}  {'ack/del':>7}",
          file=sys.stderr)
    print(f"  {'-'*10} {'-'*5} {'-'*5}  "
          f"{'-'*8} {'-'*5}  {'-'*5}  {'-'*5}  "
          f"{'-'*5}  {'-'*5}  {'-'*5}  {'-'*5}  "
          f"{'-'*8}  {'-'*7}",
          file=sys.stderr)

    for strategy, pct, n_opt, a in aggregated:
        ack_s = f"{a['mean_ack']:.0f}%" if a["mean_ack"] >= 0 else "n/a"
        chan_s = f"{a['mean_chan']:.0f}%" if a["mean_chan"] >= 0 else "n/a"
        fd_s = f"{a['mean_flood_del']:.0f}%" if a["mean_flood_del"] >= 0 else "n/a"
        pd_s = f"{a['mean_path_del']:.0f}%" if a["mean_path_del"] >= 0 else "n/a"
        fa_s = f"{a['mean_flood_ack']:.0f}%" if a["mean_flood_ack"] >= 0 else "n/a"
        pa_s = f"{a['mean_path_ack']:.0f}%" if a["mean_path_ack"] >= 0 else "n/a"
        col_s = f"{a['mean_lost_col']:.1f}" if a["fate_lost"] > 0 else "n/a"
        ack_c = f"{a['mean_del_ack']:.1f}" if a["fate_delivered"] > 0 else "n/a"
        print(f"  {strategy:<10} {pct:>4}% {n_opt:>5}  "
              f"{a['mean_pct']:6.1f}% {a['std_pct']:4.1f}%  {ack_s:>5}  {chan_s:>5}  "
              f"{fd_s:>5}  {pd_s:>5}  {fa_s:>5}  {pa_s:>5}  "
              f"{col_s:>8}  {ack_c:>7}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
