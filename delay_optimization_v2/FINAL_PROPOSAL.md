# Delay Optimization — Final Proposal

**Date:** 2026-04-17
**Objective:** Maximize ACK delivery rate across 4 network densities.

## Recommended Configuration

```
tx_delay(n)  = clamp(1.8 + 0.2 * n^1.9, 0, 15) ms
dtx_delay(n) = clamp(1.1 + 0.4 * n^1.5, 0, 15) ms
rx_delay(n)  = 0
```

Sweep parameters: `tx_base=1.8 tx_slope=0.2 tx_pow=1.9 dtx_base=1.1 dtx_slope=0.4 dtx_pow=1.5 rx_base=0 rx_slope=0 rx_pow=1.0 clamp_max=15.0`

Where **n** = neighbor count for each repeater (measured during runtime).

## Delay Table (per neighbor count)

| Neighbors (n) |  tx_delay (ms)  | dtx_delay (ms)  |
| :-----------: | :-------------: | :-------------: |
|       0       |      1.80       |      1.10       |
|       1       |      2.00       |      1.50       |
|       2       |      2.55       |      2.23       |
|       3       |      3.41       |      3.18       |
|       4       |      4.59       |      4.30       |
|       5       |      6.06       |      5.57       |
|       6       |      7.82       |      6.98       |
|       7       |      9.87       |      8.51       |
|       8       |      12.20      |      10.15      |
|       9       |      14.80      |      11.90      |
|      10       | 15.00 (clamped) |      13.75      |
|      11       | 15.00 (clamped) | 15.00 (clamped) |
|      12+      | 15.00 (clamped) | 15.00 (clamped) |

Note: MeshCore applies these as **factors** to packet airtime, producing a random back-off in `[0, 5 * airtime * factor]`.

## Performance

Measured across 4 Seattle-area topologies (~165 nodes each, 15-min simulations, 6 seeds per configuration):

| Density | Link Survival | Max Edges | ACK Delivery Rate |
|---|:---:|:---:|:---:|
| sparse     | 0.2 | 6  | **44.0%** |
| medium     | 0.4 | 12 | **38.3%** |
| dense      | 0.7 | 16 | **38.0%** |
| very_dense | 0.9 | 24 | **26.0%** |

**Mean:** 36.6% across all densities.

## Justification

### Why these parameters

1. **`tx_pow = 1.9` (super-linear scaling)**
   Appeared in **9 of the top 10** configs in the combined ranking. Strongly indicates that dense nodes need disproportionately more back-off than sparse ones to avoid collisions.

2. **`tx_base = 1.8, tx_slope = 0.2`**
   Appeared in **8 of the top 10** configs. Low slope + high exponent creates a flat response at low neighbor counts (sparse nodes don't need aggressive back-off) but explosive growth as density increases.

3. **`dtx_base = 1.1, dtx_slope = 0.4, dtx_pow = 1.5`**
   Direct (path-routed) packets need more base delay than expected. ACK return paths benefit from moderate back-off even at low density.

4. **`rx_delay = 0`** *(not yet verified — follow-up sweep needed)*
   rx_delay was held fixed at zero throughout this sweep and was never actually varied. Zero matches the current MeshCore firmware default (`_prefs.rx_delay_base = 0.0f; // turn off by default, was 10.0`). A dedicated sweep (`run_sweep_rxdelay.sh`) is planned to test `rx_base ∈ {0, 5, 10, 15, 20}` to confirm whether delivery rate is truly invariant under rx_delay or whether score-based RX queueing can help.

### Comparison to firmware defaults

Current MeshCore defaults: `tx_delay_factor=0.5`, `direct_tx_delay_factor=0.3` (flat, no neighbor scaling).

For a typical repeater with **5 neighbors**:
- Current defaults: tx=0.5, dtx=0.3 (flat)
- Proposed:        tx=6.06, dtx=5.57
- **Back-off increased by ~12x for flood, ~19x for direct**

For an **isolated repeater (n=0)**:
- Current defaults: tx=0.5, dtx=0.3
- Proposed:        tx=1.80, dtx=1.10
- **Back-off increased by ~4x** (still gentle)

### Alternative: maximize ACK redundancy

If you prefer more redundant ACK copies (better reliability at cost of airtime):

```
tx_delay(n)  = 2.4 + 0.4 * n^1.9
dtx_delay(n) = 1.1 + 0.6 * n^1.5
rx_delay(n)  = 0
```

This ranks first for `del_mean_ack_copies` but produces slightly lower per-message ACK delivery rate. Recommended only if ACK loss is critical (e.g., command & control scenarios).

## Methodology

**Sweep setup** (`delay_optimization_v2/run_sweep.sh`):
- 4 density variants: sparse / medium / dense / very_dense
- Parameter grid: `tx_base∈{1.8,2.1,2.4}`, `tx_slope∈{0,0.2,0.4}`, `tx_pow∈{0.7,1.1,1.5,1.9}`, `dtx_base∈{0.8,1.1,1.4}`, `dtx_slope∈{0.2,0.4,0.6}`, `dtx_pow∈{0.7,1.1,1.5,1.9}`
- 1296 combos × 6 seeds = 7776 runs per density
- Topology: 0.10×0.15° region around Seattle (47.60,-122.42 to 47.70,-122.27), ~165 real nodes
- Radio: US 915 MHz, SF10, BW 250 kHz, CR 1

**Scoring:**
- Per-density normalized score (min-max 0-1) for `mean_ack_pct`
- Combined score = mean(per-density scores) × coverage
- Coverage = 100% for all top-10 configs (vs 50% in previous sweep — key fix)

**Data files:**
- `results_sparse_20260417_073338.csv`
- `results_medium_20260417_073338.csv`
- `results_dense_20260417_073338.csv`
- `results_very_dense_20260417_073338.csv`
