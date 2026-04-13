#!/usr/bin/env python3
"""Analyze delay sweep results — find best parameter combos across densities.

Reads results_*.csv files and ranks parameter configurations by user-specified
metrics to maximize (or minimize with '~' prefix).

Usage:
  # Best params for delivery + ack rate (equal weight):
  python3 analyze_results.py mean_delivery_pct mean_ack_pct

  # Maximize delivery, minimize collisions:
  python3 analyze_results.py mean_delivery_pct ~lost_mean_collision

  # Custom weights (2x weight on delivery):
  python3 analyze_results.py mean_delivery_pct:2 mean_ack_pct:1

  # Only specific density:
  python3 analyze_results.py --density very_dense mean_delivery_pct

  # Show Pareto front only:
  python3 analyze_results.py --pareto mean_delivery_pct mean_ack_pct

  # Cross-density: find params that work well everywhere:
  python3 analyze_results.py --cross-density mean_delivery_pct mean_ack_pct
"""

import argparse
import csv
import os
import re
import sys
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

PARAM_COLS = [
    "tx_base", "tx_slope", "tx_pow",
    "dtx_base", "dtx_slope", "dtx_pow",
    "rx_base", "rx_slope", "rx_pow",
]

METRIC_COLS = [
    "mean_delivery_pct", "std_pct", "min_pct", "max_pct",
    "total_delivered", "total_sent", "mean_ack_pct", "mean_chan_pct",
    "mean_flood_delivery_pct", "mean_path_delivery_pct",
    "mean_flood_ack_pct", "mean_path_ack_pct",
    "n_seeds", "fate_tracked", "fate_delivered", "fate_lost",
    "lost_mean_collision", "lost_mean_drop",
    "mean_radio_eff_pct", "mean_ackpath_eff_pct",
    "del_mean_ack_copies", "lost_mean_ack_copies",
]


def parse_metric_spec(spec):
    """Parse 'metric:weight' or '~metric:weight'. ~ means minimize."""
    minimize = spec.startswith("~")
    if minimize:
        spec = spec[1:]
    parts = spec.split(":")
    name = parts[0]
    weight = float(parts[1]) if len(parts) > 1 else 1.0
    return name, weight, minimize


def load_csv(path):
    """Load a results CSV, return list of dicts with float values."""
    rows = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            parsed = {}
            for k, v in row.items():
                try:
                    parsed[k] = float(v) if v != "" else None
                except ValueError:
                    parsed[k] = None
            rows.append(parsed)
    return rows


def extract_density(filename):
    """Extract density label from filename like results_very_dense_20260412_194835.csv."""
    m = re.match(r"results_(.+?)_\d{8}_\d{6}\.csv", os.path.basename(filename))
    return m.group(1) if m else "unknown"


def param_key(row):
    """Extract parameter tuple from row."""
    return tuple(row.get(c, 0.0) or 0.0 for c in PARAM_COLS)


def is_pareto(scores):
    """Given list of (index, [objective_values...]), return set of Pareto-optimal indices.
    All objectives are to be maximized (caller flips minimize metrics)."""
    pareto = set()
    for i, (idx_i, vals_i) in enumerate(scores):
        dominated = False
        for j, (idx_j, vals_j) in enumerate(scores):
            if i == j:
                continue
            # j dominates i if j >= i on all objectives and j > i on at least one
            if all(vj >= vi for vj, vi in zip(vals_j, vals_i)) and \
               any(vj > vi for vj, vi in zip(vals_j, vals_i)):
                dominated = True
                break
        if not dominated:
            pareto.add(idx_i)
    return pareto


def fmt_param(val):
    if val == int(val):
        return f"{int(val)}"
    return f"{val:.2f}" if abs(val) < 10 else f"{val:.1f}"


def fmt_delay_formula(base, slope, power):
    """Format delay(n) = base + slope * n^power compactly."""
    parts = []
    if base != 0:
        parts.append(f"{base:.1f}")
    if slope != 0:
        if power == 1.0:
            parts.append(f"{slope:.1f}*n")
        else:
            parts.append(f"{slope:.1f}*n^{power:.1f}")
    return " + ".join(parts) if parts else "0"


def main():
    parser = argparse.ArgumentParser(
        description="Analyze delay sweep results and find best parameter combos",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Metrics (maximize by default, prefix with ~ to minimize):
  mean_delivery_pct      Overall message delivery rate
  mean_ack_pct           Acknowledgment rate
  mean_chan_pct           Channel message delivery rate
  mean_path_delivery_pct Path-routed delivery rate
  mean_path_ack_pct      Path-routed ack rate
  mean_flood_delivery_pct Flood delivery rate
  mean_flood_ack_pct     Flood ack rate
  mean_radio_eff_pct     Radio efficiency (useful rx / total rx)
  mean_ackpath_eff_pct   Ack-path radio efficiency
  del_mean_ack_copies    Avg ack copies for delivered msgs
  min_pct                Worst-seed delivery rate
  ~std_pct               Minimize variance across seeds
  ~lost_mean_collision   Minimize collision-caused losses
  ~lost_mean_drop        Minimize drop-caused losses

Weights: append :N to give metric N times weight, e.g. mean_delivery_pct:2
        """,
    )
    parser.add_argument("metrics", nargs="*",
                        help="Metrics to optimize: name[:weight], prefix ~ to minimize")
    parser.add_argument("--density", "-d", default=None,
                        help="Filter to specific density (sparse/medium/dense/very_dense)")
    parser.add_argument("--cross-density", action="store_true",
                        help="Rank by worst performance across all densities")
    parser.add_argument("--pareto", action="store_true",
                        help="Show only Pareto-optimal configurations")
    parser.add_argument("--top", "-n", type=int, default=20,
                        help="Number of top results to show (default: 20)")
    parser.add_argument("--csv-dir", default=SCRIPT_DIR,
                        help="Directory containing results_*.csv files")
    parser.add_argument("--files", nargs="*", default=None,
                        help="Specific CSV files to analyze (overrides csv-dir)")
    parser.add_argument("--show-all-metrics", action="store_true",
                        help="Show all metric columns, not just optimized ones")
    args = parser.parse_args()

    if not args.metrics:
        print("Available metrics (prefix with ~ to minimize):\n")
        descriptions = {
            "mean_delivery_pct":       "Overall message delivery rate",
            "std_pct":                 "Std dev of delivery across seeds (minimize with ~)",
            "min_pct":                 "Worst-seed delivery rate",
            "max_pct":                 "Best-seed delivery rate",
            "total_delivered":         "Total messages delivered (across all seeds)",
            "total_sent":              "Total messages sent",
            "mean_ack_pct":            "Acknowledgment rate",
            "mean_chan_pct":           "Channel message delivery rate",
            "mean_flood_delivery_pct": "Flood delivery rate",
            "mean_path_delivery_pct":  "Path-routed delivery rate",
            "mean_flood_ack_pct":      "Flood ack rate",
            "mean_path_ack_pct":       "Path-routed ack rate",
            "n_seeds":                 "Number of seeds used",
            "fate_tracked":            "Total fate-tracked messages",
            "fate_delivered":          "Fate-tracked delivered count",
            "fate_lost":              "Fate-tracked lost count",
            "lost_mean_collision":     "Mean collisions per lost msg (minimize with ~)",
            "lost_mean_drop":          "Mean drops per lost msg (minimize with ~)",
            "mean_radio_eff_pct":      "Radio efficiency (useful rx / total rx)",
            "mean_ackpath_eff_pct":    "Ack-path radio efficiency",
            "del_mean_ack_copies":     "Avg ack copies for delivered msgs",
            "lost_mean_ack_copies":    "Avg ack copies for lost msgs",
        }
        for m in METRIC_COLS:
            desc = descriptions.get(m, "")
            print(f"  {m:<28} {desc}")
        print()
        print("Usage: analyze_results.py metric1[:weight] [metric2[:weight] ...]")
        print("  Examples:")
        print("    analyze_results.py mean_delivery_pct mean_ack_pct")
        print("    analyze_results.py mean_delivery_pct:2 mean_ack_pct:1")
        print("    analyze_results.py mean_delivery_pct ~lost_mean_collision")
        sys.exit(0)

    # Parse metric specs
    objectives = []
    for spec in args.metrics:
        name, weight, minimize = parse_metric_spec(spec)
        if name not in METRIC_COLS:
            print(f"ERROR: unknown metric '{name}'", file=sys.stderr)
            print(f"  Available: {', '.join(METRIC_COLS)}", file=sys.stderr)
            sys.exit(1)
        objectives.append((name, weight, minimize))

    # Find CSV files
    if args.files:
        csv_files = args.files
    else:
        csv_files = sorted(
            os.path.join(args.csv_dir, f)
            for f in os.listdir(args.csv_dir)
            if f.startswith("results_") and f.endswith(".csv")
        )

    if not csv_files:
        print("ERROR: no results_*.csv files found", file=sys.stderr)
        sys.exit(1)

    # Load data, grouped by density
    by_density = defaultdict(list)
    for path in csv_files:
        density = extract_density(path)
        if args.density and density != args.density:
            continue
        rows = load_csv(path)
        for row in rows:
            row["_density"] = density
            row["_file"] = os.path.basename(path)
        by_density[density].extend(rows)

    if not by_density:
        print(f"ERROR: no data found" +
              (f" for density '{args.density}'" if args.density else ""),
              file=sys.stderr)
        sys.exit(1)

    densities = sorted(by_density.keys())
    total_rows = sum(len(v) for v in by_density.values())
    print(f"Loaded {total_rows} rows from {len(csv_files)} files")
    for d in densities:
        print(f"  {d}: {len(by_density[d])} configurations")
    print()

    # --- Scoring ---
    if args.cross_density and len(densities) > 1:
        # Cross-density: for each param combo, take the WORST score across densities
        # then rank by that worst score
        _analyze_cross_density(by_density, densities, objectives, args)
    else:
        # Per-density (or single density)
        for density in densities:
            rows = by_density[density]
            print(f"{'='*100}")
            print(f"  {density.upper()} ({len(rows)} configurations)")
            print(f"{'='*100}")
            _analyze_single(rows, objectives, args, density)
            print()

    # --- Combined proposal ---
    _print_combined_proposal(by_density, densities, objectives)


def _print_combined_proposal(by_density, densities, objectives):
    """Print a single combined proposal across all densities.

    For each param combo, compute its normalized score within each density
    independently (so different densities are on equal footing), then combine:
      combined = mean(per-density scores) * coverage_factor
    where coverage_factor = (densities_present / total_densities)
    This favors params that work well AND appear in many densities.
    """
    n_densities = len(densities)

    # Per-density: map param_key -> normalized score
    per_density_scores = {}   # {density: {pk: score}}
    per_density_rows = {}     # {density: {pk: row}}
    for d in densities:
        rows = by_density[d]
        scores, _ = _compute_scores(rows, objectives)
        pk_map = {}
        row_map = {}
        for i, row in enumerate(rows):
            pk = param_key(row)
            # If duplicate params in same density, keep best score
            if pk not in pk_map or scores[i] > pk_map[pk]:
                pk_map[pk] = scores[i]
                row_map[pk] = row
        per_density_scores[d] = pk_map
        per_density_rows[d] = row_map

    # Collect all param combos
    all_pks = set()
    for d in densities:
        all_pks.update(per_density_scores[d].keys())

    # Compute combined score for each
    combined = []
    for pk in all_pks:
        present_in = [d for d in densities if pk in per_density_scores[d]]
        coverage = len(present_in) / n_densities
        mean_score = sum(per_density_scores[d][pk] for d in present_in) / len(present_in)
        combined_score = mean_score * coverage
        combined.append((pk, combined_score, mean_score, coverage, present_in))

    combined.sort(key=lambda x: x[1], reverse=True)

    # Print
    print(f"{'='*100}")
    print(f"  COMBINED PROPOSAL (across {n_densities} densities: {', '.join(d.upper() for d in densities)})")
    print(f"{'='*100}")

    obj_names = []
    for name, weight, minimize in objectives:
        prefix = "~" if minimize else ""
        w_str = f":{weight:.0f}" if weight != 1.0 else ""
        obj_names.append(f"{prefix}{name}{w_str}")
    print(f"  Optimizing: {', '.join(obj_names)}")
    print(f"  Score = mean(per-density normalized score) * coverage")
    print()

    top_n = min(10, len(combined))

    # Header
    hdr = f"  {'#':>3}  {'score':>6}  {'mean':>5}  {'cov':>5}  "
    hdr += f"{'tx_delay':>18}  {'dtx_delay':>18}  {'rx_delay':>18}  "
    for d in densities:
        for name, _, _ in objectives:
            label = name.replace("mean_", "").replace("_pct", "%").replace("delivery", "del")
            hdr += f"{label[:7]+'_'+d[:2]:>10}  "
    print(hdr)
    sep = f"  {'-'*3}  {'-'*6}  {'-'*5}  {'-'*5}  {'-'*18}  {'-'*18}  {'-'*18}  "
    sep += "  ".join(f"{'-'*10}" for _ in densities for _ in objectives)
    print(sep)

    for rank, (pk, comb_sc, mean_sc, cov, present_in) in enumerate(combined[:top_n]):
        tx_f = fmt_delay_formula(pk[0], pk[1], pk[2])
        dtx_f = fmt_delay_formula(pk[3], pk[4], pk[5])
        rx_f = fmt_delay_formula(pk[6], pk[7], pk[8])

        line = f"  {rank+1:>3}  {comb_sc:>6.3f}  {mean_sc:>5.3f}  {cov:>4.0%}  "
        line += f"{tx_f:>18}  {dtx_f:>18}  {rx_f:>18}  "
        for d in densities:
            for name, _, _ in objectives:
                if d in present_in:
                    v = per_density_rows[d][pk].get(name)
                    if v is None:
                        line += f"{'n/a':>10}  "
                    else:
                        line += f"{v:>10.1f}  "
                else:
                    line += f"{'--':>10}  "
        print(line)

    # Final recommendation
    if combined:
        best_pk, best_comb, best_mean, best_cov, best_present = combined[0]
        print()
        print(f"  RECOMMENDED CONFIGURATION:")
        print(f"    tx_delay(n)  = {fmt_delay_formula(best_pk[0], best_pk[1], best_pk[2])}")
        print(f"    dtx_delay(n) = {fmt_delay_formula(best_pk[3], best_pk[4], best_pk[5])}")
        print(f"    rx_delay(n)  = {fmt_delay_formula(best_pk[6], best_pk[7], best_pk[8])}")
        print(f"    Parameters:  tx_base={best_pk[0]} tx_slope={best_pk[1]} tx_pow={best_pk[2]}"
              f"  dtx_base={best_pk[3]} dtx_slope={best_pk[4]} dtx_pow={best_pk[5]}"
              f"  rx_base={best_pk[6]} rx_slope={best_pk[7]} rx_pow={best_pk[8]}")
        print(f"    Coverage: {best_cov:.0%} ({len(best_present)}/{n_densities} densities)")
        print()
        for d in densities:
            if d in best_present:
                row = per_density_rows[d][best_pk]
                vals = ", ".join(f"{name}={row.get(name, 'n/a')}"
                                for name, _, _ in objectives)
                sc = per_density_scores[d][best_pk]
                print(f"    {d:>12}: score={sc:.3f}  {vals}")
            else:
                print(f"    {d:>12}: (not tested)")


def _compute_scores(rows, objectives):
    """Compute normalized weighted score for each row. Returns (scores, raw_values).
    scores[i] = final weighted score for row i
    raw_values[i] = list of raw objective values (sign-flipped for minimize)
    """
    n = len(rows)

    # Extract raw values per objective, handling None
    obj_raw = []
    for name, weight, minimize in objectives:
        vals = []
        for row in rows:
            v = row.get(name)
            if v is None:
                v = 0.0
            if minimize:
                v = -v  # flip so higher = better
            vals.append(v)
        obj_raw.append(vals)

    # Normalize each objective to [0, 1]
    obj_norm = []
    for vals in obj_raw:
        vmin = min(vals)
        vmax = max(vals)
        rng = vmax - vmin
        if rng == 0:
            obj_norm.append([0.5] * n)
        else:
            obj_norm.append([(v - vmin) / rng for v in vals])

    # Weighted sum
    total_weight = sum(w for _, w, _ in objectives)
    scores = [0.0] * n
    for oi, (name, weight, minimize) in enumerate(objectives):
        w = weight / total_weight
        for i in range(n):
            scores[i] += w * obj_norm[oi][i]

    # raw_values for Pareto: use sign-flipped values (higher = better)
    raw_values = [[obj_raw[oi][i] for oi in range(len(objectives))] for i in range(n)]

    return scores, raw_values


def _analyze_single(rows, objectives, args, density_label=""):
    """Analyze and print results for a single density."""
    scores, raw_values = _compute_scores(rows, objectives)

    # Pareto front
    indexed_raw = [(i, raw_values[i]) for i in range(len(rows))]
    pareto_set = is_pareto(indexed_raw)

    # Build ranked list
    ranked = sorted(range(len(rows)), key=lambda i: scores[i], reverse=True)

    if args.pareto:
        ranked = [i for i in ranked if i in pareto_set]

    top_n = min(args.top, len(ranked))

    # Print objective header
    obj_names = []
    for name, weight, minimize in objectives:
        prefix = "~" if minimize else ""
        w_str = f":{weight:.0f}" if weight != 1.0 else ""
        obj_names.append(f"{prefix}{name}{w_str}")
    print(f"  Optimizing: {', '.join(obj_names)}")
    if args.pareto:
        print(f"  Pareto-optimal: {len(pareto_set)} configurations")
    print()

    # Determine columns to show
    show_metrics = [name for name, _, _ in objectives]
    if args.show_all_metrics:
        extra = [m for m in METRIC_COLS if m not in show_metrics
                 and m not in ("n_seeds", "total_delivered", "total_sent",
                               "fate_tracked", "fate_delivered", "fate_lost")]
        show_metrics.extend(extra)

    # Print header
    hdr = f"  {'#':>3}  {'score':>6}  {'P':>1}  "
    hdr += f"{'tx_delay':>18}  {'dtx_delay':>18}  {'rx_delay':>18}  "
    for m in show_metrics:
        label = m.replace("mean_", "").replace("_pct", "%").replace("delivery", "del")
        hdr += f"{label:>10}  "
    print(hdr)
    print(f"  {'-'*3}  {'-'*6}  {'-'*1}  {'-'*18}  {'-'*18}  {'-'*18}  " +
          "  ".join(f"{'-'*10}" for _ in show_metrics))

    for rank, idx in enumerate(ranked[:top_n]):
        row = rows[idx]
        pk = param_key(row)
        tx_f = fmt_delay_formula(pk[0], pk[1], pk[2])
        dtx_f = fmt_delay_formula(pk[3], pk[4], pk[5])
        rx_f = fmt_delay_formula(pk[6], pk[7], pk[8])
        pareto_mark = "*" if idx in pareto_set else " "

        line = f"  {rank+1:>3}  {scores[idx]:>6.3f}  {pareto_mark}  "
        line += f"{tx_f:>18}  {dtx_f:>18}  {rx_f:>18}  "
        for m in show_metrics:
            v = row.get(m)
            if v is None:
                line += f"{'n/a':>10}  "
            elif abs(v) >= 100:
                line += f"{v:>10.0f}  "
            else:
                line += f"{v:>10.1f}  "
        print(line)

    # Print best config summary
    if ranked:
        best_idx = ranked[0]
        best = rows[best_idx]
        pk = param_key(best)
        print()
        print(f"  BEST configuration:")
        print(f"    tx_delay(n)  = {fmt_delay_formula(pk[0], pk[1], pk[2])}")
        print(f"    dtx_delay(n) = {fmt_delay_formula(pk[3], pk[4], pk[5])}")
        print(f"    rx_delay(n)  = {fmt_delay_formula(pk[6], pk[7], pk[8])}")
        print(f"    Parameters:  tx_base={pk[0]} tx_slope={pk[1]} tx_pow={pk[2]}"
              f"  dtx_base={pk[3]} dtx_slope={pk[4]} dtx_pow={pk[5]}"
              f"  rx_base={pk[6]} rx_slope={pk[7]} rx_pow={pk[8]}")
        for name, weight, minimize in objectives:
            v = best.get(name, "n/a")
            direction = "min" if minimize else "max"
            print(f"    {name} = {v}  ({direction})")


def _analyze_cross_density(by_density, densities, objectives, args):
    """Find params that work well across ALL densities.
    For each param combo, score = worst normalized score across densities."""

    print(f"Cross-density analysis across: {', '.join(d.upper() for d in densities)}")
    print()

    # Collect all unique param combos
    all_params = set()
    density_rows = {}
    for d in densities:
        density_rows[d] = {}
        for row in by_density[d]:
            pk = param_key(row)
            density_rows[d][pk] = row
            all_params.add(pk)

    # Only keep combos present in ALL densities
    common_params = [pk for pk in all_params
                     if all(pk in density_rows[d] for d in densities)]

    if not common_params:
        print("  No parameter configurations found in ALL densities.")
        print("  Available per density:")
        for d in densities:
            print(f"    {d}: {len(density_rows[d])} configs")
        # Fall back: show per-density overlap
        if len(densities) == 2:
            d1, d2 = densities
            s1 = set(density_rows[d1].keys())
            s2 = set(density_rows[d2].keys())
            print(f"  Overlap {d1} & {d2}: {len(s1 & s2)} configs")
        return

    print(f"  {len(common_params)} configurations present in all {len(densities)} densities")
    print()

    # Compute per-density normalized scores
    per_density_scores = {}
    for d in densities:
        rows_for_d = [density_rows[d][pk] for pk in common_params]
        scores, _ = _compute_scores(rows_for_d, objectives)
        per_density_scores[d] = {common_params[i]: scores[i]
                                 for i in range(len(common_params))}

    # Cross-density score = min across densities (robustness)
    cross_scores = {}
    for pk in common_params:
        cross_scores[pk] = min(per_density_scores[d][pk] for d in densities)

    # Rank
    ranked = sorted(common_params, key=lambda pk: cross_scores[pk], reverse=True)
    top_n = min(args.top, len(ranked))

    # Print objective header
    obj_names = []
    for name, weight, minimize in objectives:
        prefix = "~" if minimize else ""
        w_str = f":{weight:.0f}" if weight != 1.0 else ""
        obj_names.append(f"{prefix}{name}{w_str}")
    print(f"  Optimizing: {', '.join(obj_names)}")
    print(f"  Ranking by: worst score across densities (robustness)")
    print()

    # Header
    hdr = f"  {'#':>3}  {'worst':>6}  {'tx_delay':>18}  {'dtx_delay':>18}  {'rx_delay':>18}  "
    for d in densities:
        hdr += f"{'sc_'+d:>10}  "
    for name, _, _ in objectives:
        for d in densities:
            label = name.replace("mean_", "").replace("_pct", "%").replace("delivery", "del")
            hdr += f"{label[:6]+'_'+d[:2]:>10}  "
    print(hdr)
    sep = f"  {'-'*3}  {'-'*6}  {'-'*18}  {'-'*18}  {'-'*18}  "
    sep += "  ".join(f"{'-'*10}" for _ in densities)
    sep += "  "
    sep += "  ".join(f"{'-'*10}" for _ in objectives for _ in densities)
    print(sep)

    for rank, pk in enumerate(ranked[:top_n]):
        tx_f = fmt_delay_formula(pk[0], pk[1], pk[2])
        dtx_f = fmt_delay_formula(pk[3], pk[4], pk[5])
        rx_f = fmt_delay_formula(pk[6], pk[7], pk[8])

        line = f"  {rank+1:>3}  {cross_scores[pk]:>6.3f}  "
        line += f"{tx_f:>18}  {dtx_f:>18}  {rx_f:>18}  "
        for d in densities:
            line += f"{per_density_scores[d][pk]:>10.3f}  "
        for name, _, _ in objectives:
            for d in densities:
                v = density_rows[d][pk].get(name)
                if v is None:
                    line += f"{'n/a':>10}  "
                else:
                    line += f"{v:>10.1f}  "
        print(line)

    # Best summary
    if ranked:
        best_pk = ranked[0]
        print()
        print(f"  BEST robust configuration:")
        print(f"    tx_delay(n)  = {fmt_delay_formula(best_pk[0], best_pk[1], best_pk[2])}")
        print(f"    dtx_delay(n) = {fmt_delay_formula(best_pk[3], best_pk[4], best_pk[5])}")
        print(f"    rx_delay(n)  = {fmt_delay_formula(best_pk[6], best_pk[7], best_pk[8])}")
        print(f"    Parameters:  tx_base={best_pk[0]} tx_slope={best_pk[1]} tx_pow={best_pk[2]}"
              f"  dtx_base={best_pk[3]} dtx_slope={best_pk[4]} dtx_pow={best_pk[5]}"
              f"  rx_base={best_pk[6]} rx_slope={best_pk[7]} rx_pow={best_pk[8]}")
        print()
        for d in densities:
            row = density_rows[d][best_pk]
            vals = ", ".join(f"{name}={row.get(name, 'n/a')}"
                            for name, _, _ in objectives)
            print(f"    {d:>12}: score={per_density_scores[d][best_pk]:.3f}  {vals}")


if __name__ == "__main__":
    main()
