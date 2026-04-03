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
    --region 53.8,17.5,54.8,19.5 \
    --api-cache /tmp/meshcore_nodes_cache.json \
    --freq-mhz 869.618 \
    --tx-power-dbm 20.0 \
    --antenna-height 5.0 \
    --sf 8 --bw 62500 --cr 4 \
    --max-distance-km 40 \
    --min-snr -10.0 \
    --max-edges-per-node 9 \
    --max-good-links 4 \
    --clutter-db 6.0 \
    -v -o "$TOPO_FILE"

# --- Step 2: Inject test cases ---
# Manual companions attached to specific repeaters.
# Use: python3 tools/inject_test.py --help for all options.
#
# To find repeater names, run:
#   python3 -c "import json; [print(n['name']) for n in json.load(open('$TOPO_FILE'))['nodes']]"
python3 tools/inject_test.py "$TOPO_FILE" \
    --add-companion alice:GDA_DW_RPT \
    --add-companion bob:GD_Swibno_rpt \
    --add-companion carol:GTC_Rokicka_RPT \
    --add-companion dave:ELBp_ELBz_ATK10_BRID \
    --msg-schedule alice:bob:70:5 \
    --msg-schedule carol:dave:65:5 \
    --msg-schedule alice:carol:92:5 \
    --duration 900000 \
    -v -o "$TEST_FILE"

echo ""
echo "=== Ready ==="
echo "  Topology:  $TOPO_FILE"
echo "  Test file: $TEST_FILE"
echo ""
echo "Run:  build/orchestrator/orchestrator $TEST_FILE"
