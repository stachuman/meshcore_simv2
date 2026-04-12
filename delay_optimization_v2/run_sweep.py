#!/usr/bin/env python3
"""Sweep delay parameters using Lua scripting — no custom firmware fork needed.

Uses --lua sweep_delays.lua with --lua-var to inject per-variant parameters.
The Lua script applies a delay model after hot start:
  delay(n) = clamp(base + slope * n^power, clamp_min, clamp_max)

Usage:
  python3 delay_optimization_v2/run_sweep.py config.json \
    --tx-base 0.0:2.0:0.5 --tx-slope 0.0:0.4:0.2 --tx-pow 0.5:1.0:0.5 \
    --dtx-base 0.0:1.0:0.5 --dtx-slope 0.0:0.4:0.2 --dtx-pow 0.5:1.0:0.5 \
    --rx-base 0.0:6.0:2.0 --rx-slope 0.0:0.4:0.2 --rx-pow 0.5:1.0:0.5 \
    --seeds 6 -j 6 -o results.csv
"""

import argparse
import csv
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LUA_SCRIPT = os.path.join(SCRIPT_DIR, "sweep_delays.lua")


def parse_range(s):
    """Parse 'min:max:step' or a single constant into list of floats."""
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


def run_single(orchestrator_path, config_path, seed, run_id,
               tx_b, tx_s, tx_p, dtx_b, dtx_s, dtx_p,
               rx_b, rx_s, rx_p, clamp_min, clamp_max):
    """Run one orchestrator invocation with a specific seed and delay params."""
    # Inject seed into a temp config
    with open(config_path) as f:
        cfg = json.load(f)
    cfg.setdefault("simulation", {})["seed"] = seed
    # Strip any existing delay-setting commands and expect blocks
    delay_cmds = {"set rxdelay", "set txdelay", "set direct.txdelay"}
    if "commands" in cfg:
        cfg["commands"] = [
            c for c in cfg["commands"]
            if not any(c.get("command", "").startswith(d) for d in delay_cmds)
        ]
    cfg.pop("expect", None)

    tmp_path = os.path.join(tempfile.gettempdir(), f"lua_sweep_{run_id}.json")
    empty_fate = {"tracked": 0, "delivered": 0, "lost": 0,
                  "del_collision": 0.0, "del_drop": 0.0, "del_ack_copies": 0.0,
                  "lost_collision": 0.0, "lost_drop": 0.0, "lost_ack_copies": 0.0}
    try:
        with open(tmp_path, "w") as f:
            json.dump(cfg, f)

        cmd = [
            orchestrator_path,
            "--lua", LUA_SCRIPT,
            "--lua-var", f"tx_base={tx_b}",
            "--lua-var", f"tx_slope={tx_s}",
            "--lua-var", f"tx_pow={tx_p}",
            "--lua-var", f"dtx_base={dtx_b}",
            "--lua-var", f"dtx_slope={dtx_s}",
            "--lua-var", f"dtx_pow={dtx_p}",
            "--lua-var", f"rx_base={rx_b}",
            "--lua-var", f"rx_slope={rx_s}",
            "--lua-var", f"rx_pow={rx_p}",
            "--lua-var", f"clamp_min={clamp_min}",
            "--lua-var", f"clamp_max={clamp_max}",
            tmp_path,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        s = _parse_sim_summary(result.stdout)
        if not s:
            print(f"WARNING: run_id={run_id} seed={seed} — no sim_summary",
                  file=sys.stderr)
            return (seed, 0, 0, 0, -1, -1, -1, -1, -1, -1,
                    empty_fate, -1.0, -1.0)

        d = s.get("delivery", {})
        delivered = d.get("received", 0)
        sent = d.get("sent", 0)
        pct = _safe_pct(delivered, sent)

        a = s.get("acks", {})
        ack_pct = _safe_pct(a.get("received", 0), a.get("pending", 0))

        ch = s.get("channel", {})
        chan_pct = _safe_pct(ch.get("received", 0), ch.get("expected", 0))

        df = d.get("flood", {})
        dp = d.get("path", {})
        flood_del_pct = _safe_pct(df.get("received", 0), df.get("sent", 0))
        path_del_pct = _safe_pct(dp.get("received", 0), dp.get("sent", 0))

        af = a.get("flood", {})
        ap = a.get("path", {})
        flood_ack_pct = _safe_pct(af.get("received", 0), af.get("pending", 0))
        path_ack_pct = _safe_pct(ap.get("received", 0), ap.get("pending", 0))

        r = s.get("radio", {})
        radio_eff = r.get("rx_efficiency", -1.0)
        apr = s.get("ackpath_radio", {})
        ackpath_eff = apr.get("rx_efficiency", -1.0)

        fate = dict(empty_fate)
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

        return (seed, delivered, sent, pct, ack_pct, chan_pct,
                flood_del_pct, path_del_pct, flood_ack_pct, path_ack_pct,
                fate, radio_eff, ackpath_eff)

    except subprocess.TimeoutExpired:
        return (seed, 0, 0, 0, -1, -1, -1, -1, -1, -1, empty_fate, -1.0, -1.0)
    except Exception as e:
        print(f"ERROR: run_id={run_id} seed={seed}: {e}", file=sys.stderr)
        return (seed, 0, 0, 0, -1, -1, -1, -1, -1, -1, empty_fate, -1.0, -1.0)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def aggregate_variant(runs):
    """Aggregate results across seeds for one variant."""
    pcts = [r[3] for r in runs]
    ack_pcts = [r[4] for r in runs if r[4] >= 0]
    chan_pcts = [r[5] for r in runs if r[5] >= 0]
    flood_dels = [r[6] for r in runs if r[6] >= 0]
    path_dels = [r[7] for r in runs if r[7] >= 0]
    flood_acks = [r[8] for r in runs if r[8] >= 0]
    path_acks = [r[9] for r in runs if r[9] >= 0]
    radio_effs = [r[11] for r in runs if r[11] >= 0]
    ackpath_effs = [r[12] for r in runs if r[12] >= 0]

    mean_pct = sum(pcts) / len(pcts)
    std_pct = (math.sqrt(sum((p - mean_pct) ** 2 for p in pcts) / (len(pcts) - 1))
               if len(pcts) > 1 else 0.0)

    def safe_mean(lst):
        return sum(lst) / len(lst) if lst else -1

    # Fate stats
    fate_tracked = sum(r[10]["tracked"] for r in runs)
    fate_delivered = sum(r[10]["delivered"] for r in runs)
    fate_lost = sum(r[10]["lost"] for r in runs)
    lost_cols = [r[10]["lost_collision"] for r in runs if r[10]["lost"] > 0]
    lost_drps = [r[10]["lost_drop"] for r in runs if r[10]["lost"] > 0]
    del_acks = [r[10]["del_ack_copies"] for r in runs if r[10]["delivered"] > 0]
    lost_acks = [r[10]["lost_ack_copies"] for r in runs if r[10]["lost"] > 0]

    return {
        "mean_pct": mean_pct, "std_pct": std_pct,
        "min_pct": min(pcts), "max_pct": max(pcts),
        "total_delivered": sum(r[1] for r in runs),
        "total_sent": sum(r[2] for r in runs),
        "mean_ack": safe_mean(ack_pcts),
        "mean_chan": safe_mean(chan_pcts),
        "mean_flood_del": safe_mean(flood_dels),
        "mean_path_del": safe_mean(path_dels),
        "mean_flood_ack": safe_mean(flood_acks),
        "mean_path_ack": safe_mean(path_acks),
        "mean_radio_eff": safe_mean(radio_effs),
        "mean_ackpath_eff": safe_mean(ackpath_effs),
        "n_seeds": len(runs),
        "fate_tracked": fate_tracked, "fate_delivered": fate_delivered,
        "fate_lost": fate_lost,
        "mean_lost_col": safe_mean(lost_cols) if lost_cols else 0.0,
        "mean_lost_drp": safe_mean(lost_drps) if lost_drps else 0.0,
        "mean_del_ack": safe_mean(del_acks) if del_acks else 0.0,
        "mean_lost_ack": safe_mean(lost_acks) if lost_acks else 0.0,
    }


CSV_HEADER = [
    "tx_base", "tx_slope", "tx_pow",
    "dtx_base", "dtx_slope", "dtx_pow",
    "rx_base", "rx_slope", "rx_pow",
    "mean_delivery_pct", "std_pct", "min_pct", "max_pct",
    "total_delivered", "total_sent", "mean_ack_pct", "mean_chan_pct",
    "mean_flood_delivery_pct", "mean_path_delivery_pct",
    "mean_flood_ack_pct", "mean_path_ack_pct",
    "n_seeds",
    "fate_tracked", "fate_delivered", "fate_lost",
    "lost_mean_collision", "lost_mean_drop",
    "mean_radio_eff_pct", "mean_ackpath_eff_pct",
    "del_mean_ack_copies", "lost_mean_ack_copies",
]

# Map CSV column names to aggregation dict keys
CSV_TO_AGG_KEY = {
    "mean_delivery_pct": "mean_pct",
    "std_pct": "std_pct",
    "min_pct": "min_pct",
    "max_pct": "max_pct",
    "total_delivered": "total_delivered",
    "total_sent": "total_sent",
    "mean_ack_pct": "mean_ack",
    "mean_chan_pct": "mean_chan",
    "mean_flood_delivery_pct": "mean_flood_del",
    "mean_path_delivery_pct": "mean_path_del",
    "mean_flood_ack_pct": "mean_flood_ack",
    "mean_path_ack_pct": "mean_path_ack",
    "mean_radio_eff_pct": "mean_radio_eff",
    "mean_ackpath_eff_pct": "mean_ackpath_eff",
    "fate_tracked": "fate_tracked",
    "fate_delivered": "fate_delivered",
    "fate_lost": "fate_lost",
    "lost_mean_collision": "mean_lost_col",
    "lost_mean_drop": "mean_lost_drp",
    "del_mean_ack_copies": "mean_del_ack",
    "lost_mean_ack_copies": "mean_lost_ack",
    "n_seeds": "n_seeds",
}


def write_csv_row(writer, params, agg):
    tx_b, tx_s, tx_p, dtx_b, dtx_s, dtx_p, rx_b, rx_s, rx_p = params
    a = agg
    writer.writerow([
        tx_b, tx_s, tx_p, dtx_b, dtx_s, dtx_p, rx_b, rx_s, rx_p,
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


def _fmt_pow(slope, power):
    """Format slope*n^pow compactly for display."""
    if power == 1.0:
        return f"{slope}*n"
    return f"{slope}*n^{power}"


def print_results_table(aggregated, n_combos, total_runs, n_seeds, elapsed, sort_by_csv):
    t_str = f"{int(elapsed//60)}m{int(elapsed%60):02d}s" if elapsed >= 60 else f"{elapsed:.1f}s"
    top_n = min(20, len(aggregated))

    print(f"\n{'='*150}", file=sys.stderr)
    print(f"  Completed {total_runs} runs ({n_combos} variants x {n_seeds} seeds) in {t_str}",
          file=sys.stderr)
    print(f"  Sorted by: {sort_by_csv}", file=sys.stderr)
    print(f"{'='*150}", file=sys.stderr)
    print(f"  Top {top_n} variants (of {n_combos}):", file=sys.stderr)
    print(f"{'='*150}", file=sys.stderr)
    print(f"  {'tx_b':>6} {'tx_s':>6} {'tx_p':>5}  "
          f"{'dtx_b':>6} {'dtx_s':>6} {'dtx_p':>5}  "
          f"{'rx_b':>6} {'rx_s':>6} {'rx_p':>5}  "
          f"{'mean':>6}  {'std':>5}  {'min':>4}  {'max':>4}  "
          f"{'delivered':>9}  {'acks':>5}  {'chan':>5}  "
          f"{'F_del':>5}  {'P_del':>5}  {'F_ack':>5}  {'P_ack':>5}  "
          f"{'r_eff':>5}  {'ap_eff':>6}  "
          f"{'col/lost':>8}  {'drp/lost':>8}  {'ack/del':>7}",
          file=sys.stderr)
    print(f"  {'-'*6} {'-'*6} {'-'*5}  "
          f"{'-'*6} {'-'*6} {'-'*5}  "
          f"{'-'*6} {'-'*6} {'-'*5}  "
          f"{'-'*6}  {'-'*5}  {'-'*4}  {'-'*4}  "
          f"{'-'*9}  {'-'*5}  {'-'*5}  "
          f"{'-'*5}  {'-'*5}  {'-'*5}  {'-'*5}  "
          f"{'-'*5}  {'-'*6}  "
          f"{'-'*8}  {'-'*8}  {'-'*7}",
          file=sys.stderr)

    for params, a in aggregated[:top_n]:
        tx_b, tx_s, tx_p, dtx_b, dtx_s, dtx_p, rx_b, rx_s, rx_p = params
        ack_s = f"{a['mean_ack']:.0f}%" if a["mean_ack"] >= 0 else "n/a"
        chan_s = f"{a['mean_chan']:.0f}%" if a["mean_chan"] >= 0 else "n/a"
        fd_s = f"{a['mean_flood_del']:.0f}%" if a["mean_flood_del"] >= 0 else "n/a"
        pd_s = f"{a['mean_path_del']:.0f}%" if a["mean_path_del"] >= 0 else "n/a"
        fa_s = f"{a['mean_flood_ack']:.0f}%" if a["mean_flood_ack"] >= 0 else "n/a"
        pa_s = f"{a['mean_path_ack']:.0f}%" if a["mean_path_ack"] >= 0 else "n/a"
        re_s = f"{a['mean_radio_eff']:.0f}%" if a["mean_radio_eff"] >= 0 else "n/a"
        ae_s = f"{a['mean_ackpath_eff']:.0f}%" if a["mean_ackpath_eff"] >= 0 else "n/a"
        col_s = f"{a['mean_lost_col']:.1f}" if a["fate_lost"] > 0 else "n/a"
        drp_s = f"{a['mean_lost_drp']:.1f}" if a["fate_lost"] > 0 else "n/a"
        ack_c = f"{a['mean_del_ack']:.1f}" if a["fate_delivered"] > 0 else "n/a"
        print(f"  {tx_b:6.3f} {tx_s:6.4f} {tx_p:5.2f}  "
              f"{dtx_b:6.3f} {dtx_s:6.4f} {dtx_p:5.2f}  "
              f"{rx_b:6.3f} {rx_s:6.4f} {rx_p:5.2f}  "
              f"{a['mean_pct']:5.1f}%  {a['std_pct']:4.1f}%  "
              f"{a['min_pct']:3d}%  {a['max_pct']:3d}%  "
              f"{a['total_delivered']:>4}/{a['total_sent']:<4}  "
              f"{ack_s:>5}  {chan_s:>5}  "
              f"{fd_s:>5}  {pd_s:>5}  {fa_s:>5}  {pa_s:>5}  "
              f"{re_s:>5}  {ae_s:>6}  "
              f"{col_s:>8}  {drp_s:>8}  {ack_c:>7}",
              file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Sweep delay parameters using Lua scripting (no fork needed)"
    )
    parser.add_argument("config", help="Base orchestrator config JSON")

    parser.add_argument("--tx-base", default="1.5:2.2:0.1")
    parser.add_argument("--tx-slope", default="0.0:0.4:0.2")
    parser.add_argument("--tx-pow", default="1.0")
    parser.add_argument("--dtx-base", default="0.8:1.4:0.1")
    parser.add_argument("--dtx-slope", default="0.0:0.4:0.2")
    parser.add_argument("--dtx-pow", default="1.0")
    parser.add_argument("--rx-base", default="0.0")
    parser.add_argument("--rx-slope", default="0.0")
    parser.add_argument("--rx-pow", default="1.0")

    parser.add_argument("--clamp-min", type=float, default=0.0)
    parser.add_argument("--clamp-max", type=float, default=6.0)

    parser.add_argument("--seeds", type=int, default=6)
    parser.add_argument("--seed-base", type=int, default=42)
    parser.add_argument("--orchestrator", default=None,
                        help="Path to orchestrator binary (default: build/orchestrator/orchestrator)")
    parser.add_argument("-j", "--jobs", type=int, default=1)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--sort-by", default="del_mean_ack_copies",
                        help="CSV column to sort by (default: del_mean_ack_copies)")
    parser.add_argument("-o", "--output", default=None, help="Save results CSV")
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()

    if not os.path.isfile(args.config):
        print(f"ERROR: config not found: {args.config}", file=sys.stderr)
        sys.exit(1)

    if args.sort_by not in CSV_TO_AGG_KEY:
        print(f"ERROR: unknown --sort-by column: {args.sort_by}", file=sys.stderr)
        print(f"  Available: {', '.join(CSV_TO_AGG_KEY.keys())}", file=sys.stderr)
        sys.exit(1)

    # Find orchestrator
    if args.orchestrator:
        orch_path = args.orchestrator
    else:
        project_dir = os.path.dirname(SCRIPT_DIR)
        orch_path = os.path.join(project_dir, "build", "orchestrator", "orchestrator")
    if not os.path.isfile(orch_path):
        print(f"ERROR: orchestrator not found at {orch_path}", file=sys.stderr)
        print(f"  Build first: cmake -S . -B build && cmake --build build", file=sys.stderr)
        sys.exit(1)

    if not os.path.isfile(LUA_SCRIPT):
        print(f"ERROR: Lua script not found: {LUA_SCRIPT}", file=sys.stderr)
        sys.exit(1)

    # Parse ranges
    tx_b_vals  = parse_range(args.tx_base)
    tx_s_vals  = parse_range(args.tx_slope)
    tx_p_vals  = parse_range(args.tx_pow)
    dtx_b_vals = parse_range(args.dtx_base)
    dtx_s_vals = parse_range(args.dtx_slope)
    dtx_p_vals = parse_range(args.dtx_pow)
    rx_b_vals  = parse_range(args.rx_base)
    rx_s_vals  = parse_range(args.rx_slope)
    rx_p_vals  = parse_range(args.rx_pow)

    # Always include the all-zero variant (power=1.0 for zero)
    zero = (0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0)

    seeds = list(range(args.seed_base, args.seed_base + args.seeds))

    # Build variants
    variants = []
    seen = set()
    for tx_b in tx_b_vals:
        for tx_s in tx_s_vals:
            for tx_p in tx_p_vals:
                for dtx_b in dtx_b_vals:
                    for dtx_s in dtx_s_vals:
                        for dtx_p in dtx_p_vals:
                            for rx_b in rx_b_vals:
                                for rx_s in rx_s_vals:
                                    for rx_p in rx_p_vals:
                                        key = (tx_b, tx_s, tx_p,
                                               dtx_b, dtx_s, dtx_p,
                                               rx_b, rx_s, rx_p)
                                        if key not in seen:
                                            variants.append(key)
                                            seen.add(key)
    if zero not in seen:
        variants.insert(0, zero)

    n_combos = len(variants)
    total_runs = n_combos * len(seeds)

    print(f"Lua delay sweep: {n_combos} variants x {len(seeds)} seeds = {total_runs} runs",
          file=sys.stderr)
    print(f"  tx_base:       {tx_b_vals}", file=sys.stderr)
    print(f"  tx_slope:      {tx_s_vals}", file=sys.stderr)
    print(f"  tx_pow:        {tx_p_vals}", file=sys.stderr)
    print(f"  dtx_base:      {dtx_b_vals}", file=sys.stderr)
    print(f"  dtx_slope:     {dtx_s_vals}", file=sys.stderr)
    print(f"  dtx_pow:       {dtx_p_vals}", file=sys.stderr)
    print(f"  rx_base:       {rx_b_vals}", file=sys.stderr)
    print(f"  rx_slope:      {rx_s_vals}", file=sys.stderr)
    print(f"  rx_pow:        {rx_p_vals}", file=sys.stderr)
    print(f"  seeds:         {seeds}", file=sys.stderr)
    print(f"  clamp:         [{args.clamp_min}, {args.clamp_max}]", file=sys.stderr)
    print(f"  orchestrator:  {orch_path}", file=sys.stderr)
    print(f"  lua script:    {LUA_SCRIPT}", file=sys.stderr)
    print(f"  parallel jobs: {args.jobs}", file=sys.stderr)
    print(f"", file=sys.stderr)

    # Open CSV for incremental writes
    csv_file = None
    csv_writer = None
    if args.output:
        csv_file = open(args.output, "w", newline="")
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow(CSV_HEADER)
        csv_file.flush()

    all_aggregated = []
    completed_runs = 0
    t_start = time.monotonic()

    try:
        for vi, params in enumerate(variants):
            tx_b, tx_s, tx_p, dtx_b, dtx_s, dtx_p, rx_b, rx_s, rx_p = params
            elapsed = time.monotonic() - t_start
            print(f"\n  [{vi+1}/{n_combos}] "
                  f"tx={tx_b}+{_fmt_pow(tx_s, tx_p)}  "
                  f"dtx={dtx_b}+{_fmt_pow(dtx_s, dtx_p)}  "
                  f"rx={rx_b}+{_fmt_pow(rx_s, rx_p)}",
                  file=sys.stderr)

            run_id_base = vi * len(seeds)
            variant_results = []

            if args.jobs <= 1:
                for si, seed in enumerate(seeds):
                    r = run_single(orch_path, args.config, seed, run_id_base + si,
                                   tx_b, tx_s, tx_p, dtx_b, dtx_s, dtx_p,
                                   rx_b, rx_s, rx_p,
                                   args.clamp_min, args.clamp_max)
                    variant_results.append(r)
                    completed_runs += 1
                    _print_seed_progress(r, completed_runs, total_runs, t_start)
            else:
                with ProcessPoolExecutor(max_workers=args.jobs) as executor:
                    futures = {}
                    for si, seed in enumerate(seeds):
                        fut = executor.submit(
                            run_single, orch_path, args.config, seed, run_id_base + si,
                            tx_b, tx_s, tx_p, dtx_b, dtx_s, dtx_p,
                            rx_b, rx_s, rx_p,
                            args.clamp_min, args.clamp_max)
                        futures[fut] = seed
                    for future in as_completed(futures):
                        r = future.result()
                        variant_results.append(r)
                        completed_runs += 1
                        _print_seed_progress(r, completed_runs, total_runs, t_start)

            agg = aggregate_variant(variant_results)
            all_aggregated.append((params, agg))

            if csv_writer:
                write_csv_row(csv_writer, params, agg)
                csv_file.flush()

    finally:
        if csv_file:
            csv_file.close()

    # Resolve sort key
    sort_by_csv = args.sort_by
    if sort_by_csv not in CSV_TO_AGG_KEY:
        print(f"ERROR: unknown --sort-by column: {sort_by_csv}", file=sys.stderr)
        print(f"  Available: {', '.join(CSV_TO_AGG_KEY.keys())}", file=sys.stderr)
        sys.exit(1)
    sort_agg_key = CSV_TO_AGG_KEY[sort_by_csv]

    all_aggregated.sort(key=lambda x: x[1].get(sort_agg_key, 0), reverse=True)

    elapsed = time.monotonic() - t_start
    print_results_table(all_aggregated, n_combos, total_runs, len(seeds), elapsed, sort_by_csv)

    # Rewrite CSV sorted
    if args.output:
        with open(args.output, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADER)
            for params, agg in all_aggregated:
                write_csv_row(writer, params, agg)
        print(f"\nResults saved to {args.output} (sorted by {sort_by_csv})", file=sys.stderr)


def _print_seed_progress(r, completed, total, t_start):
    seed, delivered, sent, pct, ack_pct = r[0], r[1], r[2], r[3], r[4]
    chan_pct = r[5]
    elapsed = time.monotonic() - t_start
    avg = elapsed / completed
    eta = avg * (total - completed)
    if eta >= 3600:
        eta_str = f"{int(eta//3600)}h{int((eta%3600)//60):02d}m"
    elif eta >= 60:
        eta_str = f"{int(eta//60)}m{int(eta%60):02d}s"
    else:
        eta_str = f"{int(eta)}s"
    ack_str = f" ack={ack_pct}%" if ack_pct >= 0 else ""
    chan_str = f" chan={chan_pct}%" if chan_pct >= 0 else ""
    print(f"    seed={seed} -> {delivered}/{sent} ({pct}%)"
          f"{ack_str}{chan_str}  [{elapsed:.0f}s, ~{eta_str} left]",
          file=sys.stderr)


if __name__ == "__main__":
    main()
