#!/usr/bin/env python3
"""Build a complete simulation config from raw topology data.

Thin wrapper: runs convert_topology.py for topology conversion, then
inject_test.py for companion placement and message schedules.

Generate a topology first (not committed to the repo):
  python3 -m topology_generator --region 53.7,17.3,54.8,19.5 -o simulation/topology.json

Usage:
  python3 tools/build_real_sim.py simulation/topology.json \
    --companions 10 --sf 8 --bw 125000 --cr 4 \
    --msg-interval 30 --msg-count 5 \
    -o simulation/real_network.json -v
"""

import argparse
import os
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(
        description="Build simulation config with auto-placed companions and message schedules"
    )
    parser.add_argument("input", help="Path to topology.json")
    parser.add_argument("-o", "--output", required=True, help="Output config file")

    # Companion placement (passed to inject_test.py)
    parser.add_argument("--companions", type=int, default=10,
                        help="Number of companions to auto-place (default: 10)")
    parser.add_argument("--seed", type=int, default=42,
                        help="RNG seed for companion placement (default: 42)")
    parser.add_argument("--companion-snr", type=float, default=10.0,
                        help="SNR for companion-to-repeater links (default: 10.0)")
    parser.add_argument("--companion-rssi", type=float, default=-70.0,
                        help="RSSI for companion-to-repeater links (default: -70.0)")

    # Radio parameters (injected into config after conversion)
    parser.add_argument("--sf", type=int, default=8,
                        help="LoRa spreading factor (default: 8)")
    parser.add_argument("--bw", type=int, default=62500,
                        help="Bandwidth in Hz (default: 62500)")
    parser.add_argument("--cr", type=int, default=4,
                        help="Coding rate (default: 4)")

    # Simulation (passed to convert_topology.py)
    parser.add_argument("--duration", type=int, default=300000,
                        help="Simulation duration in ms (default: 300000)")
    parser.add_argument("--warmup", type=int, default=5000,
                        help="Warmup period in ms (default: 5000)")
    parser.add_argument("--step", type=int, default=5,
                        help="Step size in ms (default: 5)")

    # Message schedule (passed to inject_test.py)
    parser.add_argument("--msg-interval", type=int, default=30,
                        help="Message interval in seconds (default: 30)")
    parser.add_argument("--msg-count", type=int, default=5,
                        help="Messages per schedule entry (default: 5)")

    # Channel schedule (passed to inject_test.py)
    parser.add_argument("--no-channel", action="store_true", default=False,
                        help="Disable channel broadcast messages")
    parser.add_argument("--chan-interval", type=int, default=None,
                        help="Channel message interval in seconds (default: same as --msg-interval)")
    parser.add_argument("--chan-count", type=int, default=None,
                        help="Channel messages per companion (default: same as --msg-count)")

    # convert_topology.py passthrough flags
    parser.add_argument("--min-snr", type=float, default=-10.0)
    parser.add_argument("--min-confidence", type=float, default=0.7)
    parser.add_argument("--include-inferred", action="store_true", default=False)
    parser.add_argument("--max-link-km", type=float, default=80.0)
    parser.add_argument("--dedup-km", type=float, default=0.0)
    parser.add_argument("--no-estimate-coords", action="store_true", default=False)
    parser.add_argument("--merge-bidir", action="store_true", default=False)
    parser.add_argument("--no-fill-gaps", action="store_true", default=False)
    parser.add_argument("--max-gap-km", type=float, default=30.0)
    parser.add_argument("--max-good-links", type=int, default=2)
    parser.add_argument("--max-edges-per-node", type=int, default=8)
    parser.add_argument("--gap-sigma", type=float, default=None)

    parser.add_argument("-v", "--verbose", action="store_true", default=False)

    args = parser.parse_args()

    tools_dir = os.path.dirname(os.path.abspath(__file__))
    convert_script = os.path.join(tools_dir, "convert_topology.py")
    inject_script = os.path.join(tools_dir, "inject_test.py")

    # --- Step 1: Run convert_topology.py to get base config ---
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as tmp:
        tmp_path = tmp.name

    try:
        cmd = [
            sys.executable, convert_script, args.input,
            "-o", tmp_path,
            "--duration", str(args.duration),
            "--warmup", str(args.warmup),
            "--step", str(args.step),
            "--min-snr", str(args.min_snr),
            "--min-confidence", str(args.min_confidence),
            "--max-link-km", str(args.max_link_km),
            "--dedup-km", str(args.dedup_km),
            "--max-gap-km", str(args.max_gap_km),
            "--max-good-links", str(args.max_good_links),
            "--max-edges-per-node", str(args.max_edges_per_node),
        ]
        if args.include_inferred:
            cmd.append("--include-inferred")
        if args.no_estimate_coords:
            cmd.append("--no-estimate-coords")
        if args.merge_bidir:
            cmd.append("--merge-bidir")
        if args.no_fill_gaps:
            cmd.append("--no-fill-gaps")
        if args.gap_sigma is not None:
            cmd.extend(["--gap-sigma", str(args.gap_sigma)])
        if args.verbose:
            cmd.append("-v")

        if args.verbose:
            print(f"Step 1: convert_topology.py", file=sys.stderr)
            print(f"  {' '.join(cmd)}", file=sys.stderr)

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        if result.returncode != 0:
            print(f"ERROR: convert_topology.py failed (exit {result.returncode})",
                  file=sys.stderr)
            sys.exit(1)

        # --- Step 2: Inject radio params (not handled by inject_test.py) ---
        # inject_test.py doesn't set radio params, so we patch the intermediate file
        import json
        with open(tmp_path) as f:
            config = json.load(f)
        config["simulation"]["radio"] = {
            "sf": args.sf,
            "bw": args.bw,
            "cr": args.cr,
        }
        with open(tmp_path, "w") as f:
            json.dump(config, f, indent=2)
            f.write("\n")

        # --- Step 3: Run inject_test.py for companions + schedules ---
        cmd2 = [
            sys.executable, inject_script, tmp_path,
            "--companions", str(args.companions),
            "--seed", str(args.seed),
            "--companion-snr", str(args.companion_snr),
            "--companion-rssi", str(args.companion_rssi),
            "--auto-schedule",
            "--msg-interval", str(args.msg_interval),
            "--msg-count", str(args.msg_count),
            "--duration", str(args.duration),
            "-o", args.output,
        ]
        if not args.no_channel:
            cmd2.append("--channel")
            if args.chan_interval is not None:
                cmd2.extend(["--chan-interval", str(args.chan_interval)])
            if args.chan_count is not None:
                cmd2.extend(["--chan-count", str(args.chan_count)])
        if args.verbose:
            cmd2.append("-v")

        if args.verbose:
            print(f"\nStep 2: inject_test.py", file=sys.stderr)
            print(f"  {' '.join(cmd2)}", file=sys.stderr)

        result2 = subprocess.run(cmd2, capture_output=True, text=True)
        if result2.stderr:
            print(result2.stderr, file=sys.stderr, end="")
        if result2.returncode != 0:
            print(f"ERROR: inject_test.py failed (exit {result2.returncode})",
                  file=sys.stderr)
            sys.exit(1)

    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    print(f"\nWritten to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
