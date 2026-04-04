#!/usr/bin/env python3
"""Inject rxdelay/txdelay/direct.txdelay set commands into a config.

Usage:
  python3 tools/set_delays.py config.json --rxdelay 1.0 --txdelay 0.5 --direct-txdelay 0.2 -o out.json
  python3 tools/set_delays.py config.json --txdelay 0.8  # only set txdelay, leave others unchanged
"""

import argparse
import json
import sys


def main():
    parser = argparse.ArgumentParser(
        description="Inject delay set commands for all repeaters"
    )
    parser.add_argument("config", help="Input orchestrator config JSON")
    parser.add_argument("--rxdelay", type=float, default=None)
    parser.add_argument("--txdelay", type=float, default=None)
    parser.add_argument("--direct-txdelay", type=float, default=None)
    parser.add_argument("-o", "--output", help="Output file (default: stdout)")

    args = parser.parse_args()

    if args.rxdelay is None and args.txdelay is None and args.direct_txdelay is None:
        parser.error("Specify at least one of --rxdelay, --txdelay, --direct-txdelay")

    with open(args.config) as f:
        config = json.load(f)

    repeaters = [n["name"] for n in config["nodes"]
                 if n.get("role", "repeater") == "repeater"]

    warmup_ms = config.get("simulation", {}).get("warmup_ms", 0)
    inject_ms = warmup_ms + 1

    cmds = []
    for rpt in repeaters:
        if args.rxdelay is not None:
            cmds.append({"at_ms": inject_ms, "node": rpt, "command": f"set rxdelay {args.rxdelay}"})
        if args.txdelay is not None:
            cmds.append({"at_ms": inject_ms, "node": rpt, "command": f"set txdelay {args.txdelay}"})
        if args.direct_txdelay is not None:
            cmds.append({"at_ms": inject_ms, "node": rpt, "command": f"set direct.txdelay {args.direct_txdelay}"})

    config["commands"] = cmds + config.get("commands", [])

    vals = []
    if args.rxdelay is not None:
        vals.append(f"rxdelay={args.rxdelay}")
    if args.txdelay is not None:
        vals.append(f"txdelay={args.txdelay}")
    if args.direct_txdelay is not None:
        vals.append(f"direct.txdelay={args.direct_txdelay}")
    print(f"Injected {len(cmds)} set commands for {len(repeaters)} repeaters: {', '.join(vals)}",
          file=sys.stderr)

    out = json.dumps(config, indent=2) + "\n"
    if args.output:
        with open(args.output, "w") as f:
            f.write(out)
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(out)


if __name__ == "__main__":
    main()
