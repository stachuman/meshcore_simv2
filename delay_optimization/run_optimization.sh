#!/usr/bin/env bash
# Run delay parameter optimization sweep against the Gdansk test network.
#
# Prerequisites:
#   1. MeshCore fork checked out at ../MeshCore-stachuman (or set MESHCORE_DIR)
#   2. Fork build directory exists:
#        cmake -S . -B build-fork -DMESHCORE_DIR=$(pwd)/../MeshCore-stachuman
#        cmake --build build-fork
#
# Usage:
#   ./delay_optimization/run_optimization.sh              # Phase 2 defaults
#   ./delay_optimization/run_optimization.sh --seeds 3    # Override any flag
#
# Output CSV is saved to delay_optimization/results_<timestamp>.csv

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG="$SCRIPT_DIR/gdansk_test.json"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
OUTPUT="$SCRIPT_DIR/results_${TIMESTAMP}.csv"

# Defaults from Phase 2 best-performing ranges
DEFAULTS=(
  --tx-base  "0.0:1.5:0.5"
  --tx-slope "0.3:0.6:0.3"
  --dtx-base  "0.0:1.5:0.5"
  --dtx-slope "0.3:0.6:0.3"
  --rx-base  "0.0:1.0:0.5"
  --rx-slope "0.3:0.6:0.3"
  --seeds 6
  --build-dir "$PROJECT_DIR/build-fork"
  --meshcore-dir "${MESHCORE_DIR:-$PROJECT_DIR/../MeshCore-stachuman}"
  -j 6
  -o "$OUTPUT"
)

echo "=== Delay Optimization Sweep ==="
echo "Config:  $CONFIG"
echo "Output:  $OUTPUT"
echo "Project: $PROJECT_DIR"
echo ""

cd "$PROJECT_DIR"
python3 tools/optimize_tuning.py "$CONFIG" "${DEFAULTS[@]}" "$@"

echo ""
echo "Results saved to: $OUTPUT"
