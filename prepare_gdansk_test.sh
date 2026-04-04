#!/bin/bash
# Generate Gdansk region topology and inject test scenarios.
# Edit the variables below to customize the test.
set -euo pipefail

# --- Output paths ---
TOPO_FILE="simulation/gdansk_topology.json"
TEST_FILE="simulation/gdansk_test.json"

# --- Step 1: Generate topology (ITM propagation model) ---
# Cached API response avoids re-downloading 31K nodes each run.
python3 -m topology_generator \
    --region 53.7,17.3,54.8,19.5 \
    --api-cache /tmp/meshcore_nodes_cache.json \
    --freq-mhz 869.618 \
    --tx-power-dbm 20.0 \
    --antenna-height 5.0 \
    --sf 8 --bw 62500 --cr 4 \
    --max-distance-km 40 \
    --min-snr -10.0 \
    --max-edges-per-node 12 \
    --link-survival 0.4 \
    --clutter-db 6.0 \
    -v -o "$TOPO_FILE"

# --- Step 2: Inject test cases ---
# Auto-place companions on well-connected, geographically spread repeaters.
# Use: python3 tools/inject_test.py --help for all options.
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
python3 tools/topology_stats.py "$TEST_FILE"

echo ""
echo "=== Ready ==="
echo "  Topology:  $TOPO_FILE"
echo "  Test file: $TEST_FILE"
echo ""
echo "Run:  build/orchestrator/orchestrator $TEST_FILE"
