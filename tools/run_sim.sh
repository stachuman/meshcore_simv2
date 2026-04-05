#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
ORCH="${ROOT_DIR}/build/orchestrator/orchestrator"
VIS="${ROOT_DIR}/visualization/visualize.py"

usage() {
    echo "Usage: $(basename "$0") <config.json> [orchestrator args...]"
    echo ""
    echo "Runs the orchestrator on the given config, then launches the visualizer."
    echo "Extra arguments are forwarded to the orchestrator (e.g. -v, --duration 60000)."
    exit 1
}

if [ $# -lt 1 ] || [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
    usage
fi

CONFIG="$1"
shift

if [ ! -f "$CONFIG" ]; then
    echo "ERROR: config file not found: $CONFIG"
    exit 1
fi

if [ ! -x "$ORCH" ]; then
    echo "ERROR: orchestrator not found at $ORCH — run: cmake --build build"
    exit 1
fi

# Output goes next to the config file
OUTDIR="$(dirname "$CONFIG")"
BASE="$(basename "$CONFIG" .json)"
NDJSON="${OUTDIR}/${BASE}_events.ndjson"

echo "=== Running orchestrator ==="
echo "  Config:  $CONFIG"
echo "  Output:  $NDJSON"
echo ""

ORCH_RC=0
"$ORCH" "$CONFIG" "$@" > "$NDJSON" || ORCH_RC=$?

if [ $ORCH_RC -ne 0 ]; then
    echo ""
    echo "  Orchestrator exited with code $ORCH_RC (assertion failure?)"
    echo "  Launching visualizer anyway — useful for debugging."
fi

echo ""
echo "=== Launching visualizer ==="
python3 "$VIS" "$NDJSON" --config "$CONFIG"
