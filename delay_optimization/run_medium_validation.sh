#!/usr/bin/env bash
# Validate the universal auto-tune candidate with longer, heavier traffic
# on a DIFFERENT region (Netherlands / Groningen+Friesland) than the sweep
# (Gdansk/Pomerania). This tests whether the best cross-density parameters
# generalize to an unseen network topology.
#
# Runs the same topology against (a) default firmware and (b) universal
# candidate firmware, each with 6 seeds. Compared to the sweep tests
# (15min, 4 companions, 50 msgs), this uses 1 hour, 16 companions,
# ~500 direct messages + channels, and correlated fading.
#
# Prerequisites:
#   1. Stock build:  cmake -S . -B build && cmake --build build
#   2. Fork build:   cmake -S . -B build-fork -DMESHCORE_DIR=$(pwd)/../MeshCore-stachuman
#   3. API cache recommended: first run downloads ~31K nodes
#
# Usage:
#   ./delay_optimization/run_medium_validation.sh
#   ./delay_optimization/run_medium_validation.sh --seeds 3   # Quick test

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

TOPO_FILE="$SCRIPT_DIR/validation_topology.json"
TEST_FILE="$SCRIPT_DIR/validation_test.json"

STOCK_ORCH="$PROJECT_DIR/build/orchestrator/orchestrator"
FORK_ORCH="$PROJECT_DIR/build-fork/orchestrator/orchestrator"
MESHCORE_DIR="${MESHCORE_DIR:-$PROJECT_DIR/../MeshCore-stachuman}"

# Universal candidate — best worst-case delivery across all densities
# sparse=48.7% medium=48.0% dense=48.0% (min=48.0%, spread=0.7pp)
TX_B=0.5; TX_S=0.6; DTX_B=0.0; DTX_S=0.3; RX_B=1.0; RX_S=0.6

# Topology density parameters — adjusted for flat NL terrain:
# snr_mid=5 preserves weaker bridge links, max-good=7 compensates
# for NL having mostly high-SNR links (unlike Gdansk's mixed SNR).
# Result: avg ~5.1 neighbors, matching Gdansk medium's avg 5.0.
LINK_SURVIVAL=0.5
SURVIVAL_SNR_MID=5
MAX_EDGES=12
MAX_GOOD=7

NUM_SEEDS=6
SEED_BASE=42

while [[ $# -gt 0 ]]; do
    case "$1" in
        --seeds) NUM_SEEDS="${2:?--seeds requires a value}"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

echo "================================================================="
echo "  Medium Density Validation Test"
echo "  Region: Groningen+Friesland, NL (different from sweep region)"
echo "  Duration: 1 hour | Companions: 16 | Seeds: $NUM_SEEDS"
echo "  Best params: tx_b=$TX_B tx_s=$TX_S dtx_b=$DTX_B dtx_s=$DTX_S rx_b=$RX_B rx_s=$RX_S"
echo "================================================================="
echo ""

cd "$PROJECT_DIR"

# ======================================================================
# Step 1: Generate topology
# ======================================================================
echo "--- Step 1: Generate topology (Groningen+Friesland, NL) ---"
python3 -m topology_generator \
    --region 52.8,5.2,53.5,7.0 \
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
    --survival-snr-mid "$SURVIVAL_SNR_MID" \
    --clutter-db 6.0 \
    -v -o "$TOPO_FILE"

# ======================================================================
# Step 2: Inject test cases (heavy traffic)
# ======================================================================
echo ""
echo "--- Step 2: Inject test cases ---"
python3 tools/inject_test.py "$TOPO_FILE" \
    --companions 16 \
    --min-neighbors 2 \
    --auto-schedule --channel --random-pairs 16 \
    --msg-interval 180 --msg-count 8 \
    --chan-interval 240 --chan-count 6 \
    --duration 3600000 \
    -v -o "$TEST_FILE"

# Inject correlated fading
python3 -c "
import json
with open('$TEST_FILE') as f:
    cfg = json.load(f)
cfg.setdefault('simulation', {}).setdefault('radio', {})['snr_coherence_ms'] = 30000
with open('$TEST_FILE', 'w') as f:
    json.dump(cfg, f, indent=2)
print('Injected snr_coherence_ms=30000 (O-U correlated fading)')
"

# ======================================================================
# Step 3: Print statistics
# ======================================================================
echo ""
echo "--- Step 3: Topology statistics ---"
python3 tools/topology_stats.py "$TEST_FILE"

# ======================================================================
# Shared helpers
# ======================================================================

# Run a single config+seed, parse results from stderr
run_one() {
    local orchestrator="$1"
    local config_file="$2"
    local seed="$3"
    local tmp_config
    tmp_config=$(mktemp /tmp/validation_XXXXXX.json)

    # Inject seed into config
    python3 -c "
import json
with open('$config_file') as f:
    cfg = json.load(f)
cfg.setdefault('simulation', {})['seed'] = $seed
with open('$tmp_config', 'w') as f:
    json.dump(cfg, f)
"

    local stderr_file
    stderr_file=$(mktemp /tmp/validation_stderr_XXXXXX.txt)

    if "$orchestrator" "$tmp_config" > /dev/null 2>"$stderr_file"; then
        local result
        result=$(python3 -c "
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

fate_col = 0.0
fate_drop = 0.0
fate_tracked = 0
fate_delivered = 0
fate_lost = 0
fm = re.search(r'Message fate \((\d+) tracked, (\d+) delivered, (\d+) lost\)', stderr)
if fm:
    fate_tracked = int(fm.group(1))
    fate_delivered = int(fm.group(2))
    fate_lost = int(fm.group(3))
lm = re.search(r'Per lost message:\s+mean tx=[\d.]+\s+rx=[\d.]+\s+collision=([\d.]+)\s+drop=([\d.]+)', stderr)
if lm:
    fate_col = float(lm.group(1))
    fate_drop = float(lm.group(2))

print(f'{delivered},{sent},{pct:.1f},{ack_pct:.1f},{chan_pct:.1f},{fate_tracked},{fate_delivered},{fate_lost},{fate_col},{fate_drop}')
")
        echo "$result"
    else
        echo "0,0,0.0,0.0,0.0,0,0,0,0.0,0.0"
    fi

    rm -f "$tmp_config" "$stderr_file"
}

# Run all seeds for one variant, collecting results
run_variant() {
    local label="$1"
    local orchestrator="$2"
    local config_file="$3"

    echo ""
    echo "--- $label ($NUM_SEEDS seeds) ---"

    local all_pct="" all_ack="" all_chan="" all_col="" all_drop=""
    local total_del=0 total_sent=0
    local fate_tracked_sum=0 fate_delivered_sum=0 fate_lost_sum=0

    for i in $(seq 0 $((NUM_SEEDS - 1))); do
        local seed=$((SEED_BASE + i))
        printf "  seed %d ... " "$seed"

        local result
        result=$(run_one "$orchestrator" "$config_file" "$seed")

        local del sent pct ack chan ft fd fl fc fdrop
        IFS=',' read -r del sent pct ack chan ft fd fl fc fdrop <<< "$result"

        printf "delivery=%.1f%% ack=%.1f%% chan=%.1f%% col/lost=%.1f drp/lost=%.1f\n" \
            "$pct" "$ack" "$chan" "$fc" "$fdrop"

        total_del=$((total_del + del))
        total_sent=$((total_sent + sent))
        all_pct="$all_pct $pct"
        all_ack="$all_ack $ack"
        all_chan="$all_chan $chan"
        all_col="$all_col $fc"
        all_drop="$all_drop $fdrop"
        fate_tracked_sum=$((fate_tracked_sum + ft))
        fate_delivered_sum=$((fate_delivered_sum + fd))
        fate_lost_sum=$((fate_lost_sum + fl))
    done

    # Compute and print aggregates
    python3 -c "
import statistics

pcts = [float(x) for x in '$all_pct'.split()]
acks = [float(x) for x in '$all_ack'.split()]
chans = [float(x) for x in '$all_chan'.split()]
cols = [float(x) for x in '$all_col'.split()]
drops = [float(x) for x in '$all_drop'.split()]
cols_nonzero = [c for c in cols if c > 0]
drops_nonzero = [d for d in drops if d > 0]

mean_pct = statistics.mean(pcts)
std_pct = statistics.stdev(pcts) if len(pcts) > 1 else 0
mean_ack = statistics.mean(acks)
std_ack = statistics.stdev(acks) if len(acks) > 1 else 0
mean_chan = statistics.mean(chans)
std_chan = statistics.stdev(chans) if len(chans) > 1 else 0
mean_col = statistics.mean(cols_nonzero) if cols_nonzero else 0
mean_drop = statistics.mean(drops_nonzero) if drops_nonzero else 0

print()
print(f'  $label:')
print(f'    Delivery:  {mean_pct:.1f}% +/- {std_pct:.1f}%  (min={min(pcts):.0f}% max={max(pcts):.0f}%)  {$total_del}/{$total_sent}')
print(f'    Ack:       {mean_ack:.1f}% +/- {std_ack:.1f}%')
print(f'    Channel:   {mean_chan:.1f}% +/- {std_chan:.1f}%')
print(f'    Col/lost:  {mean_col:.1f}   Drp/lost: {mean_drop:.1f}')
print(f'    Fate:      {$fate_tracked_sum} tracked, {$fate_delivered_sum} delivered, {$fate_lost_sum} lost')
" | tee /tmp/validation_${label// /_}_summary.txt
}

# ======================================================================
# Step 4: Run baseline (stock firmware)
# ======================================================================
echo ""
echo "================================================================="
echo "  Step 4: BASELINE (default firmware)"
echo "================================================================="

if [[ ! -x "$STOCK_ORCH" ]]; then
    echo "ERROR: Stock orchestrator not found at $STOCK_ORCH" >&2
    echo "  Run: cmake -S . -B build && cmake --build build" >&2
    exit 1
fi

run_variant "BASELINE" "$STOCK_ORCH" "$TEST_FILE"

# ======================================================================
# Step 5: Run optimized (fork firmware with best DelayTuning.h)
# ======================================================================
echo ""
echo "================================================================="
echo "  Step 5: OPTIMIZED (fork firmware)"
echo "================================================================="

TUNING_H="$MESHCORE_DIR/src/helpers/DelayTuning.h"

if [[ ! -f "$TUNING_H" ]]; then
    echo "ERROR: DelayTuning.h not found at $TUNING_H" >&2
    echo "  Is MESHCORE_DIR set correctly?" >&2
    exit 1
fi

# Write best medium DelayTuning.h
echo "  Writing DelayTuning.h with best medium parameters..."
python3 -c "
TABLE_SIZE = 13

tx_b, tx_s = $TX_B, $TX_S
dtx_b, dtx_s = $DTX_B, $DTX_S
rx_b, rx_s = $RX_B, $RX_S

lines = [
    '#pragma once',
    '',
    '#include <stdint.h>',
    '',
    'struct DelayTuning {',
    '  float tx_delay;',
    '  float direct_tx_delay;',
    '  float rx_delay_base;',
    '};',
    '',
    '// Generated by run_medium_validation.sh',
    '// Universal candidate: tx_b=$TX_B tx_s=$TX_S dtx_b=$DTX_B dtx_s=$DTX_S rx_b=$RX_B rx_s=$RX_S',
    'static const DelayTuning DELAY_TUNING_TABLE[] = {',
]
for n in range(TABLE_SIZE):
    tx  = max(0.0, min(10.0, round(tx_b  + tx_s  * n, 3)))
    dtx = max(0.0, min(10.0, round(dtx_b + dtx_s * n, 3)))
    rx  = max(0.0, min(10.0, round(rx_b  + rx_s  * n, 3)))
    lines.append(f'  {{{tx:.3f}f, {dtx:.3f}f, {rx:.3f}f}},  // {n} neighbors')
lines.append('};')
lines.append(f'#define DELAY_TUNING_TABLE_SIZE  {TABLE_SIZE}')
lines.append('')
lines.append('static inline const DelayTuning& getDelayTuning(int neighbor_count) {')
lines.append('  int idx = neighbor_count;')
lines.append('  if (idx < 0) idx = 0;')
lines.append('  if (idx >= DELAY_TUNING_TABLE_SIZE) idx = DELAY_TUNING_TABLE_SIZE - 1;')
lines.append('  return DELAY_TUNING_TABLE[idx];')
lines.append('}')
lines.append('')

with open('$TUNING_H', 'w') as f:
    f.write('\n'.join(lines))
print('  DelayTuning.h written.')
"

# Rebuild fork
echo "  Rebuilding fork..."
cmake --build "$PROJECT_DIR/build-fork" 2>&1 | tail -3

if [[ ! -x "$FORK_ORCH" ]]; then
    echo "ERROR: Fork orchestrator not found at $FORK_ORCH" >&2
    exit 1
fi

# Create optimized config: inject 'set autotune on' for all repeaters
OPT_TEST_FILE=$(mktemp /tmp/validation_opt_XXXXXX.json)
python3 -c "
import json
with open('$TEST_FILE') as f:
    cfg = json.load(f)

# Strip existing delay commands
delay_cmds = {'set rxdelay', 'set txdelay', 'set direct.txdelay'}
if 'commands' in cfg:
    cfg['commands'] = [
        c for c in cfg['commands']
        if not any(c.get('command', '').startswith(d) for d in delay_cmds)
    ]

# Inject 'set autotune on' for all repeaters (using @repeaters shorthand)
warmup_ms = cfg.get('simulation', {}).get('warmup_ms', 0)
inject_ms = warmup_ms + 1
autotune_cmd = {'at_ms': inject_ms, 'node': '@repeaters', 'command': 'set autotune on'}
cfg['commands'] = [autotune_cmd] + cfg.get('commands', [])

with open('$OPT_TEST_FILE', 'w') as f:
    json.dump(cfg, f, indent=2)
print('  Injected autotune command for @repeaters')
"

run_variant "OPTIMIZED" "$FORK_ORCH" "$OPT_TEST_FILE"

rm -f "$OPT_TEST_FILE"

# ======================================================================
# Step 6: Comparison table
# ======================================================================
echo ""
echo "================================================================="
echo "  Step 6: COMPARISON"
echo "================================================================="

python3 -c "
import re

def parse_summary(path):
    try:
        text = open(path).read()
    except FileNotFoundError:
        return None
    vals = {}
    m = re.search(r'Delivery:\s+([\d.]+)%\s+\+/-\s+([\d.]+)%\s+\(min=(\d+)%\s+max=(\d+)%\)\s+(\d+)/(\d+)', text)
    if m:
        vals['del_mean'] = float(m.group(1))
        vals['del_std'] = float(m.group(2))
        vals['del_min'] = int(m.group(3))
        vals['del_max'] = int(m.group(4))
        vals['delivered'] = int(m.group(5))
        vals['sent'] = int(m.group(6))
    m = re.search(r'Ack:\s+([\d.]+)%\s+\+/-\s+([\d.]+)%', text)
    if m:
        vals['ack_mean'] = float(m.group(1))
        vals['ack_std'] = float(m.group(2))
    m = re.search(r'Channel:\s+([\d.]+)%\s+\+/-\s+([\d.]+)%', text)
    if m:
        vals['chan_mean'] = float(m.group(1))
        vals['chan_std'] = float(m.group(2))
    m = re.search(r'Col/lost:\s+([\d.]+)\s+Drp/lost:\s+([\d.]+)', text)
    if m:
        vals['col'] = float(m.group(1))
        vals['drp'] = float(m.group(2))
    return vals

base = parse_summary('/tmp/validation_BASELINE_summary.txt')
opt = parse_summary('/tmp/validation_OPTIMIZED_summary.txt')

if not base or not opt:
    print('  ERROR: Could not parse summary files')
    exit(1)

def delta(a, b):
    d = b - a
    sign = '+' if d >= 0 else ''
    return f'{sign}{d:.1f}'

print()
print(f'  {\"Metric\":<20s}  {\"BASELINE\":>15s}  {\"OPTIMIZED\":>15s}  {\"Delta\":>10s}')
print(f'  {\"-\"*20}  {\"-\"*15}  {\"-\"*15}  {\"-\"*10}')

print(f'  {\"Delivery %\":<20s}  {base[\"del_mean\"]:>10.1f}% +/-{base[\"del_std\"]:.1f}  {opt[\"del_mean\"]:>10.1f}% +/-{opt[\"del_std\"]:.1f}  {delta(base[\"del_mean\"], opt[\"del_mean\"]):>10s} pp')
print(f'  {\"  range\":<20s}  {base[\"del_min\"]:>7d}% - {base[\"del_max\"]}%  {opt[\"del_min\"]:>7d}% - {opt[\"del_max\"]}%')
print(f'  {\"Ack %\":<20s}  {base[\"ack_mean\"]:>10.1f}% +/-{base[\"ack_std\"]:.1f}  {opt[\"ack_mean\"]:>10.1f}% +/-{opt[\"ack_std\"]:.1f}  {delta(base[\"ack_mean\"], opt[\"ack_mean\"]):>10s} pp')
print(f'  {\"Channel %\":<20s}  {base[\"chan_mean\"]:>10.1f}% +/-{base[\"chan_std\"]:.1f}  {opt[\"chan_mean\"]:>10.1f}% +/-{opt[\"chan_std\"]:.1f}  {delta(base[\"chan_mean\"], opt[\"chan_mean\"]):>10s} pp')
print(f'  {\"Collision/lost\":<20s}  {base[\"col\"]:>14.1f}  {opt[\"col\"]:>14.1f}  {delta(base[\"col\"], opt[\"col\"]):>10s}')
print(f'  {\"Drop/lost\":<20s}  {base[\"drp\"]:>14.1f}  {opt[\"drp\"]:>14.1f}  {delta(base[\"drp\"], opt[\"drp\"]):>10s}')
print(f'  {\"Msgs delivered\":<20s}  {base[\"delivered\"]:>7d}/{base[\"sent\"]}  {opt[\"delivered\"]:>7d}/{opt[\"sent\"]}')
print()
"

echo ""
echo "================================================================="
echo "  Done: Medium Density Validation"
echo "  Config:   $TEST_FILE"
echo "  Topology: $TOPO_FILE"
echo "================================================================="
