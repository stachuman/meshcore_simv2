#!/usr/bin/env bash
# Unified delay optimization sweep runner.
#
# Generates a Gdansk network at a given density and runs a parameter sweep.
# All variants share a single orchestrator build and MeshCore fork.
#
# Prerequisites:
#   1. MeshCore fork at ../MeshCore-stachuman (or set MESHCORE_DIR)
#   2. API cache recommended: first run downloads ~31K nodes
#
# Usage:
#   ./delay_optimization/run_sweep.sh sparse                  # Full sweep
#   ./delay_optimization/run_sweep.sh medium --seeds 2 -j 2   # Quick test
#   ./delay_optimization/run_sweep.sh dense --seeds 4          # Custom seeds
#
# Parallel usage (all three at once):
#   ./delay_optimization/run_sweep.sh sparse &
#   ./delay_optimization/run_sweep.sh medium &
#   ./delay_optimization/run_sweep.sh dense &
#   wait
#
# The build step uses flock to prevent races. The first process builds;
# others wait then skip (already up-to-date). Pass -j to control per-variant
# parallelism (default: nproc/3 when siblings detected, nproc otherwise).

set -euo pipefail

# --- Variant selection ---
if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <sparse|medium|dense> [optimize_tuning.py args...]"
    exit 1
fi

VARIANT="$1"; shift

case "$VARIANT" in
    sparse)
        LINK_SURVIVAL=0.2
        MAX_EDGES=6
        MAX_GOOD=2
        ;;
    medium)
        LINK_SURVIVAL=0.4
        MAX_EDGES=12
        MAX_GOOD=3
        ;;
    dense)
        LINK_SURVIVAL=0.7
        MAX_EDGES=16
        MAX_GOOD=6
        ;;
    *)
        echo "Unknown variant: $VARIANT (expected sparse, medium, or dense)"
        exit 1
        ;;
esac

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

TOPO_FILE="$SCRIPT_DIR/${VARIANT}_topology.json"
TEST_FILE="$SCRIPT_DIR/${VARIANT}_test.json"
CSV_FILE="$SCRIPT_DIR/results_${VARIANT}_${TIMESTAMP}.csv"

# --- Single shared build (all variants use the same fork + binary) ---
BUILD_DIR="$PROJECT_DIR/build-native-delay"
MESHCORE_PATH="${MESHCORE_DIR:-$PROJECT_DIR/../MeshCore-stachuman}"

echo "=== Delay Optimization: ${VARIANT^^} network ==="
echo "  link-survival:      $LINK_SURVIVAL"
echo "  max-edges-per-node: $MAX_EDGES"
echo "  max-good-links:     $MAX_GOOD"
echo "  meshcore:           $MESHCORE_PATH"
echo "  build:              $BUILD_DIR"
echo ""

cd "$PROJECT_DIR"

# --- Step 0: Build orchestrator (flock prevents parallel build races) ---
echo "--- Step 0: Build orchestrator (NativeRelease + DELAY_TUNING_RUNTIME) ---"
LOCK_FILE="$BUILD_DIR.lock"
mkdir -p "$BUILD_DIR"
(
    flock -x 200
    cmake -S . -B "$BUILD_DIR" \
        -DCMAKE_BUILD_TYPE=NativeRelease \
        -DMESHCORE_DIR="$MESHCORE_PATH" \
        -DDELAY_TUNING_RUNTIME=ON
    cmake --build "$BUILD_DIR"
) 200>"$LOCK_FILE"
echo ""

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
    --step 1 \
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
  --tx-base  "0.0:2.1:1.0"
  --tx-slope "0.0:0.6:0.2"
  --dtx-base  "0.0:1.2:1.0"
  --dtx-slope "0.0:0.6:0.2"
  --rx-base  "0.0:6.0:3.0"
  --rx-slope "0.0:0.6:0.2"
  --clamp-max 6.0
  --seeds 6
  --build-dir "$BUILD_DIR"
  --meshcore-dir "$MESHCORE_PATH"
  -j 6
  -o "$CSV_FILE"
)

python3 tools/optimize_tuning.py "$TEST_FILE" "${SWEEP_DEFAULTS[@]}" "$@"

echo ""
echo "=== Done: ${VARIANT^^} ==="
echo "  Config:  $TEST_FILE"
echo "  Results: $CSV_FILE"
