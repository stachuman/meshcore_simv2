#!/usr/bin/env python3
"""Generate grid topology test files for the orchestrator.

Usage:
    python3 gen_grid_test.py                          # 10x10 grid, 20 companions
    python3 gen_grid_test.py --rows 5 --cols 5 -n 8   # 5x5 grid, 8 companions
    python3 gen_grid_test.py --rows 3 --cols 20 -n 10  # 3x20 corridor, 10 companions
    python3 gen_grid_test.py -o test/t07_custom.json   # custom output path

Companion placement: corners first, then edge midpoints, then random interior.
Assertions: full discovery (all companions know each other) + cross-grid messaging.
"""
import argparse
import json
import random
import sys


def grid_positions(rows, cols):
    """Return all (row, col) positions in priority order: corners, edges, interior."""
    corners = []
    edges = []
    interior = []
    for r in range(rows):
        for c in range(cols):
            is_edge_r = r == 0 or r == rows - 1
            is_edge_c = c == 0 or c == cols - 1
            if is_edge_r and is_edge_c:
                corners.append((r, c))
            elif is_edge_r or is_edge_c:
                edges.append((r, c))
            else:
                interior.append((r, c))
    return corners, edges, interior


def pick_companion_positions(rows, cols, n_companions, seed=42):
    """Pick n positions for companions, prioritizing corners > edge midpoints > interior."""
    corners, edges, interior = grid_positions(rows, cols)

    rng = random.Random(seed)
    rng.shuffle(edges)
    rng.shuffle(interior)

    pool = corners + edges + interior
    if n_companions > len(pool):
        print(f"Warning: requested {n_companions} companions but grid only has "
              f"{len(pool)} positions, capping at {len(pool)}", file=sys.stderr)
        n_companions = len(pool)

    return pool[:n_companions]


def manhattan(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def auto_duration_ms(max_dist):
    """Auto-scale simulation duration based on max hop distance."""
    return max(60000, max_dist * 5000 + 20000)


def generate_test(rows, cols, n_companions, duration_ms=None, step_ms=5,
                  warmup_ms=5000, snr_grid=8.0, snr_companion=10.0, seed=42):
    nodes = []
    links = []

    # Repeater grid
    for r in range(rows):
        for c in range(cols):
            nodes.append({"name": f"r{r}_{c}", "role": "repeater"})

    # Grid links (4-connected)
    for r in range(rows):
        for c in range(cols):
            if c < cols - 1:
                links.append({"from": f"r{r}_{c}", "to": f"r{r}_{c+1}",
                              "snr": snr_grid, "rssi": -80.0, "bidir": True})
            if r < rows - 1:
                links.append({"from": f"r{r}_{c}", "to": f"r{r+1}_{c}",
                              "snr": snr_grid, "rssi": -80.0, "bidir": True})

    # Place companions
    positions = pick_companion_positions(rows, cols, n_companions, seed)
    comp_names = []
    for idx, (r, c) in enumerate(positions):
        name = f"c{idx+1:02d}"
        comp_names.append(name)
        nodes.append({"name": name, "role": "companion"})
        links.append({"from": name, "to": f"r{r}_{c}",
                      "snr": snr_companion, "rssi": -70.0, "bidir": True})

    # Find the pair with maximum Manhattan distance (for cross-grid message test)
    max_dist = 0
    pair_max = (0, 1)
    for i in range(len(positions)):
        for j in range(i + 1, len(positions)):
            d = manhattan(positions[i], positions[j])
            if d > max_dist:
                max_dist = d
                pair_max = (i, j)

    # Auto-scale duration if not explicitly set
    if duration_ms is None:
        duration_ms = auto_duration_ms(max_dist)

    # Also find a second distant pair (different nodes if possible)
    used = {pair_max[0], pair_max[1]}
    pair2 = None
    best2 = 0
    for i in range(len(positions)):
        for j in range(i + 1, len(positions)):
            if i in used and j in used:
                continue
            d = manhattan(positions[i], positions[j])
            if d > best2:
                best2 = d
                pair2 = (i, j)

    # Commands
    commands = []

    # All companions check neighbors
    for name in comp_names:
        commands.append({"at_ms": 6000, "node": name, "command": "neighbors"})

    # Max-distance message
    a, b = pair_max
    commands.append({"at_ms": 8000, "node": comp_names[a],
                     "command": f"msg {comp_names[b]} hello across {max_dist} hops"})

    # Second message (if we have a second pair)
    if pair2 and len(comp_names) > 2:
        a2, b2 = pair2
        commands.append({"at_ms": 12000, "node": comp_names[a2],
                         "command": f"msg {comp_names[b2]} second route {best2} hops"})

    # Stats from message recipients
    commands.append({"at_ms": duration_ms - 10000, "node": comp_names[pair_max[1]],
                     "command": "stats"})
    if pair2 and len(comp_names) > 2:
        commands.append({"at_ms": duration_ms - 10000, "node": comp_names[pair2[1]],
                         "command": "stats"})

    # Assertions
    expect = []

    # Every companion should discover every other companion
    for i, name_i in enumerate(comp_names):
        for j, name_j in enumerate(comp_names):
            if i == j:
                continue
            expect.append({"type": "cmd_reply_contains",
                           "node": name_i, "command": "neighbors", "value": name_j})

    # Max-distance message succeeds
    expect.append({"type": "cmd_reply_contains",
                   "node": comp_names[pair_max[0]],
                   "command": f"msg {comp_names[pair_max[1]]}",
                   "value": f"msg sent to {comp_names[pair_max[1]]}"})
    expect.append({"type": "cmd_reply_contains",
                   "node": comp_names[pair_max[1]],
                   "command": "stats", "value": "recv: 1 direct"})

    # Second message succeeds
    if pair2 and len(comp_names) > 2:
        expect.append({"type": "cmd_reply_contains",
                       "node": comp_names[pair2[0]],
                       "command": f"msg {comp_names[pair2[1]]}",
                       "value": f"msg sent to {comp_names[pair2[1]]}"})
        expect.append({"type": "cmd_reply_contains",
                       "node": comp_names[pair2[1]],
                       "command": "stats", "value": "recv: 1 direct"})

    grid_label = f"{rows}x{cols}"
    desc = (f"Stress test: {grid_label} repeater grid ({rows*cols} repeaters) + "
            f"{n_companions} companions. "
            f"Max hop distance: {max_dist}. "
            f"Verifies full companion discovery and cross-grid messaging.")

    test = {
        "_name": f"hot_start_grid_{grid_label}_{n_companions}c",
        "_desc": desc,
        "simulation": {
            "duration_ms": duration_ms,
            "step_ms": step_ms,
            "warmup_ms": warmup_ms,
            "hot_start": True
        },
        "nodes": nodes,
        "topology": {"links": links},
        "commands": commands,
        "expect": expect
    }

    return test


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rows", type=int, default=10, help="Grid rows (default: 10)")
    parser.add_argument("--cols", type=int, default=10, help="Grid columns (default: 10)")
    parser.add_argument("-n", "--companions", type=int, default=20,
                        help="Number of companion nodes (default: 20)")
    parser.add_argument("--duration", type=int, default=None,
                        help="Simulation duration in ms (default: auto-scaled by grid size)")
    parser.add_argument("--no-auto-duration", action="store_true",
                        help="Disable auto-duration scaling (use --duration or 60000)")
    parser.add_argument("--step", type=int, default=5,
                        help="Step size in ms (default: 5)")
    parser.add_argument("--warmup", type=int, default=5000,
                        help="Warmup duration in ms (default: 5000)")
    parser.add_argument("--snr-grid", type=float, default=8.0,
                        help="SNR for grid links (default: 8.0)")
    parser.add_argument("--snr-companion", type=float, default=10.0,
                        help="SNR for companion-to-repeater links (default: 10.0)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for companion placement (default: 42)")
    parser.add_argument("-o", "--output", type=str, default=None,
                        help="Output file (default: stdout)")

    args = parser.parse_args()

    duration = args.duration
    if duration is None and args.no_auto_duration:
        duration = 60000  # legacy default when auto is disabled

    test = generate_test(
        rows=args.rows, cols=args.cols,
        n_companions=args.companions,
        duration_ms=duration, step_ms=args.step,
        warmup_ms=args.warmup, snr_grid=args.snr_grid,
        snr_companion=args.snr_companion, seed=args.seed
    )

    output = json.dumps(test, indent=2) + "\n"

    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        n_nodes = len(test["nodes"])
        n_links = len(test["topology"]["links"])
        n_assert = len(test["expect"])
        print(f"Generated {args.output}: {n_nodes} nodes, {n_links} links, "
              f"{n_assert} assertions", file=sys.stderr)
    else:
        sys.stdout.write(output)


if __name__ == "__main__":
    main()
