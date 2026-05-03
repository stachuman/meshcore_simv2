#!/usr/bin/env bash
# Mixed-firmware validation experiment
#
# Tests how gradually rolling out fw_stachuman (optimized delays) across the
# dense Seattle topology affects delivery performance.
#
# Requires: fw_stachuman.so built via tools/firmware.py
#
# Usage:
#   bash mixed_firmware_validation/run_experiment.sh [-j JOBS]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
JOBS="${1:--j6}"

# Resolve orchestrator
ORCH="$PROJECT_DIR/build/orchestrator/orchestrator"
if [[ ! -f "$ORCH" ]]; then
    echo "ERROR: orchestrator not found at $ORCH"
    echo "  Build first: python3 tools/firmware.py build"
    exit 1
fi

# Verify fw_stachuman is available
if ! "$ORCH" --list-plugins 2>/dev/null | grep -q fw_stachuman; then
    # Fallback: check .so file directly
    if [[ ! -f "$(dirname "$ORCH")/fw_stachuman.so" ]]; then
        echo "ERROR: fw_stachuman.so not found next to orchestrator"
        echo "  Register and build:"
        echo "    python3 tools/firmware.py add stachuman https://github.com/stachuman/MeshCore.git --branch main"
        echo "    python3 tools/firmware.py build"
        exit 1
    fi
fi

# Base config: reuse dense topology from delay_optimization_v2
BASE_CONFIG="$PROJECT_DIR/delay_optimization_v2/dense_test.json"
if [[ ! -f "$BASE_CONFIG" ]]; then
    echo "ERROR: dense_test.json not found. Run delay_optimization_v2/run_sweep.sh dense first."
    exit 1
fi

echo "=== Mixed-Firmware Validation Experiment ==="
echo "  Base config:  $BASE_CONFIG"
echo "  Orchestrator: $ORCH"
echo "  Timestamp:    $TIMESTAMP"
echo ""

# Step 1: Generate configs (both strategies)
echo "--- Step 1: Generating configs ---"
python3 "$SCRIPT_DIR/generate_configs.py" \
    --base-config "$BASE_CONFIG" \
    --percentages 0,10,30,50,75,100 \
    --seeds 6 --seed-base 42 \
    --strategy both \
    --output-dir "$SCRIPT_DIR/configs" \
    -v
echo ""

# Step 2: Run all configs
echo "--- Step 2: Running simulations ---"
python3 "$SCRIPT_DIR/run_mixed.py" \
    --config-dir "$SCRIPT_DIR/configs" \
    --orchestrator "$ORCH" \
    "$JOBS" \
    -o "$SCRIPT_DIR/results/results_${TIMESTAMP}.csv"

echo ""
echo "Done. Results: $SCRIPT_DIR/results/results_${TIMESTAMP}.csv"
