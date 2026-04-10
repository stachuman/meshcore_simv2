#!/usr/bin/env bash
# mcsim Seattle Delay Test — independent topology, mcsim-native radio
#
# Uses pre-computed ITM topology from Brent-A/mcsim (sea.yaml) with the
# original chatter traffic overlay (4 companions, session-based model).
# Compares the same 10 delay variants as run_us_targeted.sh.
#
# Radio params match mcsim defaults: SF7, BW62500, CR 4/5, 910.525 MHz.
# This faithfully reproduces the mcsim test scenario using our engine.
# Fading is i.i.d. Gaussian per-packet (snr_coherence_ms=0), matching mcsim.
#
# Note: 399 repeaters + 15k links — each run takes significantly longer
# than the ~30-80 node topologies from topology_generator.
#
# Usage:
#   bash delay_optimization/run_mcsim_seattle.sh
#   bash delay_optimization/run_mcsim_seattle.sh --seeds 3
#   bash delay_optimization/run_mcsim_seattle.sh --mcsim-dir /path/to/mcsim

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

TOPO_FILE="$SCRIPT_DIR/mcsim_seattle.json"
TEST_FILE="$SCRIPT_DIR/mcsim_seattle_test.json"
ORCH="$PROJECT_DIR/build/orchestrator/orchestrator"

# mcsim default radio parameters (SF7, BW62.5k, CR 4/5, 910.525 MHz)
# These match the mcsim simulator defaults from mcsim-model/properties/definitions.rs
MCSIM_SF=7
MCSIM_BW=62500
MCSIM_CR=1
MCSIM_FREQ=910.525

# mcsim repo location
MCSIM_DIR="/tmp/mcsim_repo"

# Traffic duration
DURATION_S=3600
CHATTER_SEED=42

NUM_SEEDS=6
SEED_BASE=42

while [[ $# -gt 0 ]]; do
    case "$1" in
        --seeds) NUM_SEEDS="${2:?--seeds requires a value}"; shift 2 ;;
        --mcsim-dir) MCSIM_DIR="${2:?--mcsim-dir requires a value}"; shift 2 ;;
        --duration) DURATION_S="${2:?--duration requires a value}"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

SEA_YAML="$MCSIM_DIR/examples/seattle/sea.yaml"
CHATTER_YAML="$MCSIM_DIR/examples/seattle/chatter.yaml"

echo "================================================================="
echo "  mcsim Seattle Delay Test"
echo "  Source: Brent-A/mcsim sea.yaml + chatter.yaml"
echo "  Radio: SF$MCSIM_SF BW$MCSIM_BW CR$MCSIM_CR ${MCSIM_FREQ}MHz (mcsim defaults)"
echo "  Duration: ${DURATION_S}s | Seeds: $NUM_SEEDS"
echo "================================================================="
echo ""

cd "$PROJECT_DIR"

# ======================================================================
# Step 0: Ensure mcsim repo is available
# ======================================================================
if [[ ! -f "$SEA_YAML" ]]; then
    echo "--- Step 0: Cloning mcsim repository ---"
    git clone --depth 1 https://github.com/Brent-A/mcsim.git "$MCSIM_DIR"
    echo ""
fi

if [[ ! -f "$SEA_YAML" ]]; then
    echo "ERROR: sea.yaml not found at $SEA_YAML" >&2
    echo "  Clone mcsim: git clone https://github.com/Brent-A/mcsim.git $MCSIM_DIR" >&2
    exit 1
fi
if [[ ! -f "$CHATTER_YAML" ]]; then
    echo "ERROR: chatter.yaml not found at $CHATTER_YAML" >&2
    exit 1
fi

# ======================================================================
# Step 1: Convert mcsim topology
# ======================================================================
echo "--- Step 1: Convert mcsim topology (sea.yaml, keeping companions) ---"
python3 tools/convert_mcsim.py "$SEA_YAML" \
    --keep-companions \
    -v -o "$TOPO_FILE"

# ======================================================================
# Step 2: Add chatter traffic overlay
# ======================================================================
echo ""
echo "--- Step 2: Add chatter traffic overlay ---"
python3 tools/convert_mcsim_chatter.py "$TOPO_FILE" "$CHATTER_YAML" \
    --duration "$DURATION_S" --seed "$CHATTER_SEED" \
    -v -o "$TEST_FILE"

# ======================================================================
# Step 3: Inject mcsim-native radio params (i.i.d. fading)
# ======================================================================
echo ""
echo "--- Step 3: Inject mcsim radio params ---"
python3 -c "
import json
with open('$TEST_FILE') as f:
    cfg = json.load(f)
sim = cfg.setdefault('simulation', {})
sim.setdefault('radio', {}).update({
    'sf': $MCSIM_SF,
    'bw': $MCSIM_BW,
    'cr': $MCSIM_CR
})
# i.i.d. Gaussian fading per packet (matches mcsim behavior)
sim['snr_coherence_ms'] = 0
with open('$TEST_FILE', 'w') as f:
    json.dump(cfg, f, indent=2)
print('Injected simulation.radio: sf=$MCSIM_SF bw=$MCSIM_BW cr=$MCSIM_CR')
print('Injected snr_coherence_ms=0 (i.i.d. Gaussian fading, matching mcsim)')
"

# ======================================================================
# Step 4: Print topology statistics
# ======================================================================
echo ""
echo "--- Step 4: Topology statistics ---"
python3 tools/topology_stats.py "$TEST_FILE"

# ======================================================================
# Helpers
# ======================================================================

if [[ ! -x "$ORCH" ]]; then
    echo "ERROR: orchestrator not found at $ORCH" >&2
    echo "  Run: cmake -S . -B build && cmake --build build" >&2
    exit 1
fi

# Run a single config+seed, return: delivered,sent,pct,ack,chan,col,drop
run_one() {
    local config_file="$1" seed="$2" txd="$3" dtxd="$4" rxd="$5"
    local tmp_config stderr_file
    tmp_config=$(mktemp /tmp/mcsim_seattle_XXXXXX.json)
    stderr_file=$(mktemp /tmp/mcsim_seattle_stderr_XXXXXX.txt)

    python3 -c "
import json
with open('$config_file') as f:
    cfg = json.load(f)
cfg.setdefault('simulation', {})['seed'] = $seed

# Strip any existing expect blocks
cfg.pop('expect', None)

warmup_ms = cfg.get('simulation', {}).get('warmup_ms', 0)
inject_ms = warmup_ms + 1

txd, dtxd, rxd = '$txd', '$dtxd', '$rxd'
if txd != 'default':
    cmds = [
        {'at_ms': inject_ms, 'node': '@repeaters', 'command': f'set txdelay {txd}'},
        {'at_ms': inject_ms, 'node': '@repeaters', 'command': f'set direct.txdelay {dtxd}'},
        {'at_ms': inject_ms, 'node': '@repeaters', 'command': f'set rxdelay {rxd}'},
    ]
    cfg['commands'] = cmds + cfg.get('commands', [])

with open('$tmp_config', 'w') as f:
    json.dump(cfg, f)
"

    if "$ORCH" "$tmp_config" > /dev/null 2>"$stderr_file"; then
        python3 -c "
import re
stderr = open('$stderr_file').read()
dm = re.search(r'Delivery:\s+(\d+)\s*/\s*(\d+)', stderr)
delivered = int(dm.group(1)) if dm else 0
sent = int(dm.group(2)) if dm else 0
pct = 100.0 * delivered / sent if sent > 0 else 0.0
am = re.search(r'Acks:\s+\d+/\d+\s+received\s+\((\d+)%\)', stderr)
ack_pct = float(am.group(1)) if am else 0.0
cm = re.search(r'Channel:\s+\d+/\d+\s+receptions\s+\((\d+)%\)', stderr)
chan_pct = float(cm.group(1)) if cm else 0.0
lm = re.search(r'Per lost message:\s+mean tx=[\d.]+\s+rx=[\d.]+\s+collision=([\d.]+)\s+drop=([\d.]+)', stderr)
col = float(lm.group(1)) if lm else 0.0
drp = float(lm.group(2)) if lm else 0.0
dam = re.search(r'Per delivered message: mean tx=[\d.]+\s+rx=[\d.]+\s+collision=[\d.]+\s+drop=[\d.]+\s+ack_copies=([\d.]+)', stderr)
ack_c = float(dam.group(1)) if dam else 0.0
print(f'{delivered},{sent},{pct:.1f},{ack_pct:.1f},{chan_pct:.1f},{col:.1f},{drp:.1f},{ack_c:.1f}')
"
    else
        echo "0,0,0.0,0.0,0.0,0.0,0.0,0.0"
    fi

    rm -f "$tmp_config" "$stderr_file"
}

# ======================================================================
# Step 5: Run 10 delay variants
# ======================================================================
echo ""
echo "================================================================="
echo "  Step 5: Running delay variants ($NUM_SEEDS seeds each)"
echo "  NOTE: 399 repeaters — each run may take several minutes"
echo "================================================================="

# Define test variants: label|txdelay|direct.txdelay|rxdelay
# "default" = don't inject any set commands (stock firmware defaults)
VARIANTS=(
    "zero|0|0|0"
    "stock_default|default|default|default"
    "small_0.5|0.5|0.5|0.5"
    "medium_1.0|1.0|1.0|1.0"
    "medium_1.5|1.5|1.5|1.5"
    "large_3.0|3.0|3.0|3.0"
    "large_5.0|5.0|5.0|5.0"
    "hi_tx_lo_rx|3.0|1.5|0.5"
    "lo_tx_hi_rx|0.5|0.5|3.0"
    "best_sweep|1.5|0.0|0.5"
)

# Collect results: label -> "mean_del|std_del|mean_ack|mean_chan|mean_col|mean_drop|total_del|total_sent"
declare -A RESULTS

for variant in "${VARIANTS[@]}"; do
    IFS='|' read -r label txd dtxd rxd <<< "$variant"

    if [[ "$txd" == "default" ]]; then
        echo ""
        echo "--- $label (stock firmware defaults) ---"
    else
        echo ""
        echo "--- $label (tx=$txd dtx=$dtxd rx=$rxd) ---"
    fi

    all_pct="" all_ack="" all_chan="" all_col="" all_drop="" all_ack_copies=""
    total_del=0 total_sent=0

    for i in $(seq 0 $((NUM_SEEDS - 1))); do
        seed=$((SEED_BASE + i))
        printf "  seed %d ... " "$seed"

        result=$(run_one "$TEST_FILE" "$seed" "$txd" "$dtxd" "$rxd")
        IFS=',' read -r del sent pct ack chan col drp ack_c <<< "$result"

        printf "del=%.1f%% ack=%.1f%% chan=%.1f%% col=%.1f drp=%.1f ack_c=%.1f\n" \
            "$pct" "$ack" "$chan" "$col" "$drp" "$ack_c"

        total_del=$((total_del + del))
        total_sent=$((total_sent + sent))
        all_pct="$all_pct $pct"
        all_ack="$all_ack $ack"
        all_chan="$all_chan $chan"
        all_col="$all_col $col"
        all_drop="$all_drop $drp"
        all_ack_copies="$all_ack_copies $ack_c"
    done

    # Aggregate
    agg=$(python3 -c "
import statistics
pcts = [float(x) for x in '$all_pct'.split()]
acks = [float(x) for x in '$all_ack'.split()]
chans = [float(x) for x in '$all_chan'.split()]
cols = [float(x) for x in '$all_col'.split()]
drops = [float(x) for x in '$all_drop'.split()]
ack_copies = [float(x) for x in '$all_ack_copies'.split()]
ack_copies_nz = [a for a in ack_copies if a > 0]
m = statistics.mean(pcts)
s = statistics.stdev(pcts) if len(pcts) > 1 else 0
mac = statistics.mean(ack_copies_nz) if ack_copies_nz else 0.0
print(f'{m:.1f}|{s:.1f}|{statistics.mean(acks):.1f}|{statistics.mean(chans):.1f}|{statistics.mean(cols):.1f}|{statistics.mean(drops):.1f}|$total_del|$total_sent|{mac:.1f}')
")
    RESULTS["$label"]="$agg"
    echo ""
done

# ======================================================================
# Step 6: Print comparison table
# ======================================================================
echo "================================================================="
echo "  RESULTS SUMMARY — mcsim Seattle (SF$MCSIM_SF BW$MCSIM_BW CR$MCSIM_CR ${MCSIM_FREQ}MHz)"
echo "================================================================="
echo ""

python3 -c "
results = {}
$(for variant in "${VARIANTS[@]}"; do
    IFS='|' read -r label txd dtxd rxd <<< "$variant"
    echo "results['$label'] = '${RESULTS[$label]}'"
done)

# Parse
rows = []
for label, data in results.items():
    parts = data.split('|')
    rows.append({
        'label': label,
        'mean_del': float(parts[0]),
        'std_del': float(parts[1]),
        'mean_ack': float(parts[2]),
        'mean_chan': float(parts[3]),
        'mean_col': float(parts[4]),
        'mean_drop': float(parts[5]),
        'total_del': int(parts[6]),
        'total_sent': int(parts[7]),
        'mean_ack_copies': float(parts[8]) if len(parts) > 8 else 0.0,
    })

# Sort by delivery descending
rows.sort(key=lambda r: r['mean_del'], reverse=True)

# Baseline is stock_default
base = next((r for r in rows if r['label'] == 'stock_default'), rows[0])

print(f\"  {'Variant':<18s}  {'Delivery':>12s}  {'vs stock':>8s}  {'Ack':>6s}  {'Chan':>6s}  {'Col/lost':>8s}  {'Drp/lost':>8s}  {'Ack/del':>7s}\")
print(f\"  {'-'*18}  {'-'*12}  {'-'*8}  {'-'*6}  {'-'*6}  {'-'*8}  {'-'*8}  {'-'*7}\")

for r in rows:
    d = r['mean_del'] - base['mean_del']
    sign = '+' if d >= 0 else ''
    delta = f'{sign}{d:.1f}pp'
    if r['label'] == base['label']:
        delta = '(base)'
    print(f\"  {r['label']:<18s}  {r['mean_del']:>5.1f}%+/-{r['std_del']:.1f}  {delta:>8s}  {r['mean_ack']:>5.1f}%  {r['mean_chan']:>5.1f}%  {r['mean_col']:>7.1f}  {r['mean_drop']:>7.1f}  {r['mean_ack_copies']:>6.1f}\")

print()
total_per_seed = rows[0]['total_sent'] // $NUM_SEEDS if $NUM_SEEDS > 0 else 0
print(f'  Seeds: $NUM_SEEDS | Source: mcsim sea.yaml + chatter.yaml')
print(f'  Topology: 399 repeaters, 4 companions | Radio: SF$MCSIM_SF BW$MCSIM_BW CR$MCSIM_CR')
print(f'  Total msgs per seed: {total_per_seed}')
print()
"

echo "================================================================="
echo "  Done: mcsim Seattle Delay Test"
echo "  Topology: $TOPO_FILE"
echo "  Config:   $TEST_FILE"
echo "================================================================="
