#!/usr/bin/env bash
# Run baseline measurements using default firmware (build/) against all density configs.
#
# This produces reference numbers for comparison with optimized delay tuning results.
# Uses the stock orchestrator (no DelayTuning.h modifications) with 6 seeds per density.
#
# Prerequisites:
#   1. Stock build exists: cmake -S . -B build && cmake --build build
#   2. Density test configs exist (run the density scripts first, or at least their
#      topology generation steps)
#
# Usage:
#   ./delay_optimization/run_baseline.sh
#   ./delay_optimization/run_baseline.sh --seeds 3    # fewer seeds for quick test

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ORCHESTRATOR="$PROJECT_DIR/build/orchestrator/orchestrator"

NUM_SEEDS=6
SEED_BASE=42
while [[ $# -gt 0 ]]; do
    case "$1" in
        --seeds) NUM_SEEDS="${2:?--seeds requires a value}"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

# Check orchestrator exists
if [[ ! -x "$ORCHESTRATOR" ]]; then
    echo "ERROR: Stock orchestrator not found at $ORCHESTRATOR" >&2
    echo "  Run: cmake -S . -B build && cmake --build build" >&2
    exit 1
fi

cd "$PROJECT_DIR"

DENSITIES=(sparse medium dense)
SEPARATOR="-----------------------------------------------------------------------------------------------"

echo "=== Baseline Measurements (default firmware) ==="
echo "  Orchestrator: $ORCHESTRATOR"
echo "  Seeds: $NUM_SEEDS (base=$SEED_BASE)"
echo ""

# --- Run a single config+seed, parse results from stderr ---
run_one() {
    local config_file="$1"
    local seed="$2"
    local tmp_config
    tmp_config=$(mktemp /tmp/baseline_XXXXXX.json)

    # Inject seed into config
    python3 -c "
import json, sys
with open('$config_file') as f:
    cfg = json.load(f)
cfg.setdefault('simulation', {})['seed'] = $seed
with open('$tmp_config', 'w') as f:
    json.dump(cfg, f)
"

    local stderr_file
    stderr_file=$(mktemp /tmp/baseline_stderr_XXXXXX.txt)

    if "$ORCHESTRATOR" "$tmp_config" > /dev/null 2>"$stderr_file"; then
        local result
        result=$(python3 -c "
import re, sys

stderr = open('$stderr_file').read()

# Parse delivery stats
dm = re.search(r'Delivery:\s+(\d+)\s*/\s*(\d+)', stderr)
delivered = int(dm.group(1)) if dm else 0
sent = int(dm.group(2)) if dm else 0
pct = 100.0 * delivered / sent if sent > 0 else 0.0

# Parse ack — format: 'Acks: N/M received (P%)'
am = re.search(r'Acks:\s+\d+/\d+\s+received\s+\((\d+)%\)', stderr)
ack_pct = float(am.group(1)) if am else 0.0

# Parse channel — format: 'Channel: N/M receptions (P%)'
cm = re.search(r'Channel:\s+\d+/\d+\s+receptions\s+\((\d+)%\)', stderr)
chan_pct = float(cm.group(1)) if cm else 0.0

# Parse fate
fate_col = 0.0
fate_drop = 0.0
fate_ack = 0.0
fate_tracked = 0
fate_delivered = 0
fate_lost = 0
fm = re.search(r'Message fate \((\d+) tracked, (\d+) delivered, (\d+) lost\)', stderr)
if fm:
    fate_tracked = int(fm.group(1))
    fate_delivered = int(fm.group(2))
    fate_lost = int(fm.group(3))
dm = re.search(r'Per delivered message: mean tx=[\d.]+\s+rx=[\d.]+\s+collision=[\d.]+\s+drop=[\d.]+\s+ack_copies=([\d.]+)', stderr)
if dm:
    fate_ack = float(dm.group(1))
lm = re.search(r'Per lost message:\s+mean tx=[\d.]+\s+rx=[\d.]+\s+collision=([\d.]+)\s+drop=([\d.]+)', stderr)
if lm:
    fate_col = float(lm.group(1))
    fate_drop = float(lm.group(2))

print(f'{delivered},{sent},{pct:.1f},{ack_pct:.1f},{chan_pct:.1f},{fate_tracked},{fate_delivered},{fate_lost},{fate_col},{fate_drop},{fate_ack}')
")
        echo "$result"
    else
        echo "0,0,0.0,0.0,0.0,0,0,0,0.0,0.0,0.0"
    fi

    rm -f "$tmp_config" "$stderr_file"
}

# --- Aggregate results for one density ---
run_density() {
    local density="$1"
    local config_file="$SCRIPT_DIR/${density}_test.json"

    if [[ ! -f "$config_file" ]]; then
        echo "  SKIP: $config_file not found (run run_${density}.sh first)"
        return
    fi

    echo "--- $density ---"

    local all_pct=""
    local total_del=0
    local total_sent=0
    local all_ack=""
    local all_chan=""
    local all_col=""
    local all_drop=""
    local all_ack_copies=""
    local fate_tracked_sum=0
    local fate_delivered_sum=0
    local fate_lost_sum=0

    for i in $(seq 0 $((NUM_SEEDS - 1))); do
        local seed=$((SEED_BASE + i))
        printf "  seed %d ... " "$seed"

        local result
        result=$(run_one "$config_file" "$seed")

        local del sent pct ack chan ft fd fl fc fdrop fack
        IFS=',' read -r del sent pct ack chan ft fd fl fc fdrop fack <<< "$result"

        printf "delivery=%.1f%% ack=%.1f%% chan=%.1f%% col/lost=%.1f drp/lost=%.1f ack_copies=%.1f\n" \
            "$pct" "$ack" "$chan" "$fc" "$fdrop" "$fack"

        total_del=$((total_del + del))
        total_sent=$((total_sent + sent))
        all_pct="$all_pct $pct"
        all_ack="$all_ack $ack"
        all_chan="$all_chan $chan"
        all_col="$all_col $fc"
        all_drop="$all_drop $fdrop"
        all_ack_copies="$all_ack_copies $fack"
        fate_tracked_sum=$((fate_tracked_sum + ft))
        fate_delivered_sum=$((fate_delivered_sum + fd))
        fate_lost_sum=$((fate_lost_sum + fl))
    done

    # Compute aggregates
    python3 -c "
import statistics

pcts = [float(x) for x in '$all_pct'.split()]
acks = [float(x) for x in '$all_ack'.split()]
chans = [float(x) for x in '$all_chan'.split()]
cols = [float(x) for x in '$all_col'.split()]
drops = [float(x) for x in '$all_drop'.split()]
ack_copies = [float(x) for x in '$all_ack_copies'.split()]
cols_nonzero = [c for c in cols if c > 0]
drops_nonzero = [d for d in drops if d > 0]
ack_copies_nonzero = [a for a in ack_copies if a > 0]

mean_pct = statistics.mean(pcts)
std_pct = statistics.stdev(pcts) if len(pcts) > 1 else 0
mean_ack = statistics.mean(acks)
mean_chan = statistics.mean(chans)
mean_col = statistics.mean(cols_nonzero) if cols_nonzero else 0
mean_drop = statistics.mean(drops_nonzero) if drops_nonzero else 0
mean_ack_copies = statistics.mean(ack_copies_nonzero) if ack_copies_nonzero else 0

print()
print(f'  BASELINE {\"$density\".upper():8s}:  delivery={mean_pct:.1f}% +/-{std_pct:.1f}%  '
      f'(min={min(pcts):.0f}% max={max(pcts):.0f}%)  '
      f'{$total_del}/{$total_sent}')
print(f'                    ack={mean_ack:.1f}%  chan={mean_chan:.1f}%  '
      f'col/lost={mean_col:.1f}  drp/lost={mean_drop:.1f}  ack_copies/del={mean_ack_copies:.1f}')
print(f'                    fate: {$fate_tracked_sum} tracked, {$fate_delivered_sum} delivered, {$fate_lost_sum} lost')
"
    echo ""
}

for density in "${DENSITIES[@]}"; do
    run_density "$density"
done

echo "$SEPARATOR"
echo "Done. Compare these baselines with optimized results in results_*.csv"
