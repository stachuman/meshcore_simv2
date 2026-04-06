#!/usr/bin/env bash
# Generate a DENSE Gdansk network and run delay optimization sweep.
#
# Dense variant: high link survival, many edges per node.
# Compare with run_sparse.sh and run_medium.sh.
#
# Prerequisites:
#   1. MeshCore fork at ../MeshCore-stachuman (or set MESHCORE_DIR)
#   2. Fork build: cmake -S . -B build-fork -DMESHCORE_DIR=$(pwd)/../MeshCore-stachuman
#   3. API cache recommended: first run downloads ~31K nodes
#
# Usage:
#   ./delay_optimization/run_dense.sh                    # Full sweep
#   ./delay_optimization/run_dense.sh --seeds 2 -j 2    # Quick test

set -euo pipefail

VARIANT="dense"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

TOPO_FILE="$SCRIPT_DIR/${VARIANT}_topology.json"
TEST_FILE="$SCRIPT_DIR/${VARIANT}_test.json"
CSV_FILE="$SCRIPT_DIR/results_${VARIANT}_${TIMESTAMP}.csv"

# --- Density parameters (dense) ---
LINK_SURVIVAL=0.7
MAX_EDGES=16
MAX_GOOD=6

echo "=== Delay Optimization: ${VARIANT^^} network ==="
echo "  link-survival:      $LINK_SURVIVAL"
echo "  max-edges-per-node: $MAX_EDGES"
echo "  max-good-links:     $MAX_GOOD"
echo ""

cd "$PROJECT_DIR"

# --- Step 1: Generate topology ---
echo "--- Step 1: Generate topology ---"
python3 -m topology_generator \
    --region 53.7,17.3,54.8,19.5 \
    --api-cache /tmp/meshcore_nodes_cache.json \
    --freq-mhz 869.618 \
    --tx-power-dbm 20.0 \
    --antenna-height 5.0 \
    --sf 8 --bw 62500 --cr 4 \
    --max-distance-km 40 \
    --min-snr -10.0 \
    --max-edges-per-node "$MAX_EDGES" \
    --max-good-links "$MAX_GOOD" \
    --link-survival "$LINK_SURVIVAL" \
    --clutter-db 6.0 \
    -v -o "$TOPO_FILE"

# --- Step 2: Inject test cases ---
echo ""
echo "--- Step 2: Inject test cases ---"
python3 tools/inject_test.py "$TOPO_FILE" \
    --companions 4 \
    --companion-names alice,bob,carol,dave \
    --min-neighbors 2 \
    --auto-schedule --channel \
    --msg-interval 70 --msg-count 5 \
    --chan-interval 80 --chan-count 4 \
    --duration 900000 \
    -v -o "$TEST_FILE"

# --- Step 3: Print statistics ---
echo ""
echo "--- Step 3: Topology statistics ---"
python3 tools/topology_stats.py "$TEST_FILE"

# --- Step 4: Run optimization sweep ---
echo ""
echo "--- Step 4: Optimization sweep ---"
echo "  Output: $CSV_FILE"
echo ""

SWEEP_DEFAULTS=(
  --tx-base  "0.0:1.5:0.5"
  --tx-slope "0.3:0.6:0.3"
  --dtx-base  "0.0:1.5:0.5"
  --dtx-slope "0.3:0.6:0.3"
  --rx-base  "0.0:1.5:0.5"
  --rx-slope "0.3:0.6:0.3"
  --seeds 6
  --build-dir "$PROJECT_DIR/build-fork"
  --meshcore-dir "${MESHCORE_DIR:-$PROJECT_DIR/../MeshCore-stachuman}"
  -j 6
  -o "$CSV_FILE"
)

python3 tools/optimize_tuning.py "$TEST_FILE" "${SWEEP_DEFAULTS[@]}" "$@"

echo ""
echo "=== Done: ${VARIANT^^} ==="
echo "  Config:  $TEST_FILE"
echo "  Results: $CSV_FILE"
