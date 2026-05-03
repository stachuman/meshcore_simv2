#!/usr/bin/env python3
"""Generate mixed-firmware configs for delay optimization validation.

Takes the dense topology and produces config JSONs where a fraction of
repeaters run fw_stachuman (optimized delays) while the rest run fw_default
(stock delays).

Two node selection strategies:
  random  — N repeaters chosen at random (varies per seed)
  degree  — N highest-degree repeaters chosen (deterministic)

Usage:
  python3 generate_configs.py \
    --base-config delay_optimization_v2/dense_test.json \
    --percentages 0,10,30,50,75,100 \
    --seeds 6 --strategy random \
    --output-dir mixed_firmware_validation/configs
"""

import argparse
import json
import os
import random
import sys


def compute_degrees(cfg):
    """Return dict: node_name -> degree (link count)."""
    deg = {}
    for link in cfg["topology"]["links"]:
        f, t = link["from"], link["to"]
        deg[f] = deg.get(f, 0) + 1
        if link.get("bidir", True):
            deg[t] = deg.get(t, 0) + 1
    return deg


def select_nodes_random(repeater_names, n, seed):
    """Select n repeaters at random using the given seed."""
    rng = random.Random(seed)
    return set(rng.sample(repeater_names, min(n, len(repeater_names))))


def select_nodes_degree(repeater_names, degrees, n):
    """Select n highest-degree repeaters (deterministic, ties broken by name)."""
    ranked = sorted(repeater_names, key=lambda name: (-degrees.get(name, 0), name))
    return set(ranked[:n])


def generate_config(base_cfg, optimized_names, pct, strategy, seed):
    """Create a config with per-node firmware assignments."""
    cfg = json.loads(json.dumps(base_cfg))  # deep copy

    cfg["simulation"]["seed"] = seed

    # Metadata
    cfg["_name"] = f"mixed_fw_{strategy}_pct{pct}_seed{seed}"
    cfg["_desc"] = (
        f"Mixed firmware: {pct}% repeaters on fw_stachuman (optimized delays), "
        f"rest on fw_default. Strategy: {strategy}."
    )
    cfg["_requires_plugins"] = ["fw_stachuman"]
    cfg["_mixed_experiment"] = {
        "strategy": strategy,
        "pct_optimized": pct,
        "seed": seed,
        "n_optimized": len(optimized_names),
    }

    # Assign firmware per node
    for node in cfg["nodes"]:
        if node.get("role", "repeater") == "companion":
            node["firmware"] = "fw_default"
        elif node["name"] in optimized_names:
            node["firmware"] = "fw_stachuman"
        else:
            node["firmware"] = "fw_default"

    # Strip expect blocks (we collect metrics from sim_summary)
    cfg.pop("expect", None)

    return cfg


def main():
    parser = argparse.ArgumentParser(description="Generate mixed-firmware configs")
    parser.add_argument("--base-config", required=True, help="Path to dense_test.json")
    parser.add_argument("--percentages", default="0,10,30,50,75,100",
                        help="Comma-separated optimized percentages")
    parser.add_argument("--seeds", type=int, default=6, help="Number of RNG seeds")
    parser.add_argument("--seed-base", type=int, default=42, help="First seed value")
    parser.add_argument("--strategy", choices=["random", "degree", "both"], default="both")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    with open(args.base_config) as f:
        base_cfg = json.load(f)

    repeaters = [n["name"] for n in base_cfg["nodes"]
                 if n.get("role", "repeater") == "repeater"]
    n_rep = len(repeaters)
    degrees = compute_degrees(base_cfg)
    percentages = [int(p) for p in args.percentages.split(",")]
    seeds = list(range(args.seed_base, args.seed_base + args.seeds))
    strategies = ["random", "degree"] if args.strategy == "both" else [args.strategy]

    os.makedirs(args.output_dir, exist_ok=True)

    n_configs = 0
    for strategy in strategies:
        for pct in percentages:
            n_optimized = round(n_rep * pct / 100)
            for seed in seeds:
                if strategy == "random":
                    selected = select_nodes_random(repeaters, n_optimized, seed)
                else:
                    selected = select_nodes_degree(repeaters, degrees, n_optimized)

                cfg = generate_config(base_cfg, selected, pct, strategy, seed)
                fname = f"{strategy}_pct{pct:03d}_seed{seed}.json"
                path = os.path.join(args.output_dir, fname)
                with open(path, "w") as f:
                    json.dump(cfg, f, indent=2)
                n_configs += 1

                if args.verbose:
                    print(f"  {fname}: {n_optimized}/{n_rep} repeaters on fw_stachuman",
                          file=sys.stderr)

    print(f"Generated {n_configs} configs in {args.output_dir}/", file=sys.stderr)
    print(f"  Strategies: {strategies}", file=sys.stderr)
    print(f"  Percentages: {percentages}", file=sys.stderr)
    print(f"  Seeds: {seeds}", file=sys.stderr)
    print(f"  Repeaters: {n_rep}", file=sys.stderr)


if __name__ == "__main__":
    main()
