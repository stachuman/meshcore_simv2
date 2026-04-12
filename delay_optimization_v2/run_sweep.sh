#!/usr/bin/env bash
# Lua-based delay optimization sweep.
#
# Generates a Gdansk network at a given density and runs a parameter sweep
# using Lua scripting to set per-repeater delays based on neighbor count.
# No custom MeshCore fork needed — uses standard build with ENABLE_LUA.
#
# Usage:
#   ./delay_optimization_v2/run_sweep.sh sparse                  # Full sweep
#   ./delay_optimization_v2/run_sweep.sh medium --seeds 2 -j 2   # Quick test
#   ./delay_optimization_v2/run_sweep.sh very_dense               # New density
#
# Parallel usage:
#   ./delay_optimization_v2/run_sweep.sh sparse &
#   ./delay_optimization_v2/run_sweep.sh medium &
#   ./delay_optimization_v2/run_sweep.sh dense &
#   ./delay_optimization_v2/run_sweep.sh very_dense &
#   wait

set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <sparse|medium|dense|very_dense> [run_sweep.py args...]"
    exit 1
fi

# PL parameters
#SF=8
#BW=62500
#CR=4
#FREQ=869.0
#REGION="53.7,17.3,54.8,19.5"
# US parameters
SF=10
BW=250000
CR=1
FREQ=915.0
REGION="47.0,-123.0,48.0,-121.5"


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
    very_dense)
        LINK_SURVIVAL=0.9
        MAX_EDGES=24
        MAX_GOOD=10
        ;;
    *)
        echo "Unknown variant: $VARIANT (expected sparse, medium, dense, or very_dense)"
        exit 1
        ;;
esac

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

TOPO_FILE="$SCRIPT_DIR/${VARIANT}_topology.json"
TEST_FILE="$SCRIPT_DIR/${VARIANT}_test.json"
CSV_FILE="$SCRIPT_DIR/results_${VARIANT}_${TIMESTAMP}.csv"

# Orchestrator: prefer build-native (NativeRelease), fall back to build/
if [[ -x "$PROJECT_DIR/build-native/orchestrator/orchestrator" ]]; then
    ORCH_PATH="$PROJECT_DIR/build-native/orchestrator/orchestrator"
elif [[ -x "$PROJECT_DIR/build/orchestrator/orchestrator" ]]; then
    ORCH_PATH="$PROJECT_DIR/build/orchestrator/orchestrator"
else
    echo "ERROR: orchestrator not found"
    echo "  Build with: cmake -S . -B build-native -DCMAKE_BUILD_TYPE=NativeRelease && cmake --build build-native"
    exit 1
fi

echo "=== Lua Delay Sweep: ${VARIANT^^} network ==="
echo "  link-survival:      $LINK_SURVIVAL"
echo "  max-edges-per-node: $MAX_EDGES"
echo "  max-good-links:     $MAX_GOOD"
echo "  orchestrator:       $ORCH_PATH"
echo ""

cd "$PROJECT_DIR"

# --- Step 1: Generate topology ---
echo "--- Step 1: Generate topology ---"
python3 -m topology_generator \
    --region "$REGION" \
    --api-cache /tmp/meshcore_nodes_cache.json \
    --freq-mhz "$FREQ" \
    --tx-power-dbm 20.0 \
    --antenna-height 5.0 \
    --sf "$SF" --bw "$BW" --cr "$CR" \
    --max-distance-km 40 \
    --min-snr -16.0 \
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

# --- Step 4: Run sweep ---
echo ""
echo "--- Step 4: Lua delay sweep ---"
echo "  Output: $CSV_FILE"
echo ""

SWEEP_DEFAULTS=(
    --tx-base  "1.9:2.5:0.2"
    --tx-slope "0.0:0.4:0.2"
    --tx-pow   "0.7:1.8:0.4"
    --dtx-base  "0.9:1.5:0.2"
    --dtx-slope "0.3:0.7:0.2"
    --dtx-pow   "0.7:1.8:0.4"
    --rx-base  "0.0"
    --rx-slope "0.0"
    --rx-pow   "1.0"
    --clamp-max 6.0
    --seeds 6
    --orchestrator "$ORCH_PATH"
    -j 6
    -o "$CSV_FILE"
)

python3 "$SCRIPT_DIR/run_sweep.py" "$TEST_FILE" "${SWEEP_DEFAULTS[@]}" "$@"

echo ""
echo "=== Done: ${VARIANT^^} ==="
echo "  Config:  $TEST_FILE"
echo "  Results: $CSV_FILE"
