#!/usr/bin/env bash
# Run zero-delay baseline for all three Gdansk densities and append to CSVs.
#
# Uses the STOCK orchestrator with explicit "set txdelay 0 / rxdelay 0 /
# direct.txdelay 0" to override firmware defaults (0.5 / 0.0 / 0.3).
# Appends one row per density to the existing sweep CSV files so the
# zero baseline can be compared directly with all autotune variants.
#
# Prerequisites:
#   Stock build: cmake -S . -B build && cmake --build build
#   Existing test configs: sparse_test.json, medium_test.json, dense_test.json
#
# Usage:
#   bash delay_optimization/run_zero_baseline.sh
#   bash delay_optimization/run_zero_baseline.sh --seeds 3  # quick

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ORCH="$PROJECT_DIR/build/orchestrator/orchestrator"

NUM_SEEDS=6
SEED_BASE=42

while [[ $# -gt 0 ]]; do
    case "$1" in
        --seeds) NUM_SEEDS="${2:?--seeds requires a value}"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

if [[ ! -x "$ORCH" ]]; then
    echo "ERROR: stock orchestrator not found at $ORCH" >&2
    echo "  Run: cmake -S . -B build && cmake --build build" >&2
    exit 1
fi

# Map each density to its test config and CSV file
declare -A TEST_FILES CSV_FILES
TEST_FILES=(
    [sparse]="$SCRIPT_DIR/sparse_test.json"
    [medium]="$SCRIPT_DIR/medium_test.json"
    [dense]="$SCRIPT_DIR/dense_test.json"
)

# Find the CSV files (they have timestamps in the name)
for density in sparse medium dense; do
    csv=$(ls -t "$SCRIPT_DIR"/results_${density}_*.csv 2>/dev/null | head -1)
    if [[ -z "$csv" ]]; then
        echo "WARNING: no CSV found for $density, will create new one" >&2
        csv="$SCRIPT_DIR/results_${density}_zero.csv"
    fi
    CSV_FILES[$density]="$csv"
done

echo "================================================================="
echo "  Zero-Delay Baseline Test (all Gdansk densities)"
echo "  Seeds: $NUM_SEEDS  |  Orchestrator: stock"
echo "================================================================="
echo ""

for density in sparse medium dense; do
    echo "  $density:"
    echo "    test:  ${TEST_FILES[$density]}"
    echo "    csv:   ${CSV_FILES[$density]}"
done
echo ""

cd "$PROJECT_DIR"

# Run one seed with zero delays, return CSV-compatible fields
run_one() {
    local config_file="$1" seed="$2"
    local tmp_config stderr_file
    tmp_config=$(mktemp /tmp/zero_baseline_XXXXXX.json)
    stderr_file=$(mktemp /tmp/zero_baseline_stderr_XXXXXX.txt)

    python3 -c "
import json
with open('$config_file') as f:
    cfg = json.load(f)
cfg.setdefault('simulation', {})['seed'] = $seed

warmup_ms = cfg.get('simulation', {}).get('warmup_ms', 0)
inject_ms = warmup_ms + 1
cmds = [
    {'at_ms': inject_ms, 'node': '@repeaters', 'command': 'set txdelay 0'},
    {'at_ms': inject_ms, 'node': '@repeaters', 'command': 'set direct.txdelay 0'},
    {'at_ms': inject_ms, 'node': '@repeaters', 'command': 'set rxdelay 0'},
]
cfg['commands'] = cmds + cfg.get('commands', [])
cfg.pop('expect', None)

with open('$tmp_config', 'w') as f:
    json.dump(cfg, f)
"

    if "$ORCH" "$tmp_config" > /dev/null 2>"$stderr_file"; then
        python3 -c "
import re
stderr = open('$stderr_file').read()

dm = re.search(r'Delivery:\s+(\d+)/(\d+)\s+messages\s+\((\d+)%\)', stderr)
delivered = int(dm.group(1)) if dm else 0
sent = int(dm.group(2)) if dm else 0
pct = int(dm.group(3)) if dm else 0

am = re.search(r'Acks:\s+\d+/\d+\s+received\s+\((\d+)%\)', stderr)
ack_pct = int(am.group(1)) if am else -1

cm = re.search(r'Channel:\s+\d+/\d+\s+receptions\s+\((\d+)%\)', stderr)
chan_pct = int(cm.group(1)) if cm else -1

fate_tracked = fate_delivered = fate_lost = 0
lost_col = lost_drop = 0.0

fm = re.search(r'Message fate \((\d+) tracked, (\d+) delivered, (\d+) lost\)', stderr)
if fm:
    fate_tracked = int(fm.group(1))
    fate_delivered = int(fm.group(2))
    fate_lost = int(fm.group(3))

lm = re.search(r'Per lost message:\s+mean tx=[\d.]+\s+rx=[\d.]+\s+collision=([\d.]+)\s+drop=([\d.]+)', stderr)
if lm:
    lost_col = float(lm.group(1))
    lost_drop = float(lm.group(2))

print(f'{delivered},{sent},{pct},{ack_pct},{chan_pct},{fate_tracked},{fate_delivered},{fate_lost},{lost_col},{lost_drop}')
"
    else
        echo "0,0,0,-1,-1,0,0,0,0.0,0.0"
    fi

    rm -f "$tmp_config" "$stderr_file"
}

# Process each density
for density in sparse medium dense; do
    test_file="${TEST_FILES[$density]}"
    csv_file="${CSV_FILES[$density]}"

    if [[ ! -f "$test_file" ]]; then
        echo "SKIP: $test_file not found" >&2
        continue
    fi

    echo ""
    echo "--- $density (zero delays, $NUM_SEEDS seeds) ---"

    all_pct="" all_ack="" all_chan=""
    all_col="" all_drop=""
    total_del=0 total_sent=0
    fate_tracked_sum=0 fate_delivered_sum=0 fate_lost_sum=0

    for i in $(seq 0 $((NUM_SEEDS - 1))); do
        seed=$((SEED_BASE + i))
        printf "  seed %d ... " "$seed"

        result=$(run_one "$test_file" "$seed")
        IFS=',' read -r del sent pct ack chan ft fd fl fc fdrop <<< "$result"

        printf "del=%d%% ack=%d%% chan=%d%% col=%.1f drp=%.1f\n" \
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

    # Aggregate and append to CSV
    python3 -c "
import csv, statistics, os

pcts = [float(x) for x in '$all_pct'.split()]
acks = [float(x) for x in '$all_ack'.split()]
chans = [float(x) for x in '$all_chan'.split()]
cols = [float(x) for x in '$all_col'.split()]
drops = [float(x) for x in '$all_drop'.split()]

mean_pct = statistics.mean(pcts)
std_pct = statistics.stdev(pcts) if len(pcts) > 1 else 0
min_pct = int(min(pcts))
max_pct = int(max(pcts))
mean_ack = statistics.mean(acks)
mean_chan = statistics.mean(chans)
lost_cols = [c for c in cols if c > 0]
lost_drops = [d for d in drops if d > 0]
mean_col = statistics.mean(lost_cols) if lost_cols else 0.0
mean_drop = statistics.mean(lost_drops) if lost_drops else 0.0

# Print summary
print()
print(f'  $density ZERO-DELAY:')
print(f'    Delivery:  {mean_pct:.1f}% +/- {std_pct:.1f}%  (min={min_pct}% max={max_pct}%)  {$total_del}/{$total_sent}')
print(f'    Ack:       {mean_ack:.1f}%')
print(f'    Channel:   {mean_chan:.1f}%')
print(f'    Col/lost:  {mean_col:.1f}   Drp/lost: {mean_drop:.1f}')
print(f'    Fate:      {$fate_tracked_sum} tracked, {$fate_delivered_sum} delivered, {$fate_lost_sum} lost')

# Append to CSV (matching optimize_tuning.py format)
csv_path = '$csv_file'
needs_header = not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0

with open(csv_path, 'a', newline='') as f:
    writer = csv.writer(f)
    if needs_header:
        writer.writerow(['tx_base','tx_slope','dtx_base','dtx_slope','rx_base','rx_slope',
                         'mean_delivery_pct','std_pct','min_pct','max_pct',
                         'total_delivered','total_sent','mean_ack_pct','mean_chan_pct','n_seeds',
                         'fate_tracked','fate_delivered','fate_lost',
                         'lost_mean_collision','lost_mean_drop'])
    writer.writerow([
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
        round(mean_pct, 1), round(std_pct, 1),
        min_pct, max_pct,
        $total_del, $total_sent,
        round(mean_ack, 1),
        round(mean_chan, 1),
        len(pcts),
        $fate_tracked_sum, $fate_delivered_sum, $fate_lost_sum,
        round(mean_col, 1), round(mean_drop, 1),
    ])
print(f'  Appended zero-baseline row to {csv_path}')
"
done

echo ""
echo "================================================================="
echo "  Done: Zero-delay baseline appended to all CSVs"
echo "================================================================="
