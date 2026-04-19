# Delay Optimization — Final Proposal

**Date:** 2026-04-18
**Objective:** Maximize ACK delivery rate across 4 network densities.

## Recommended Configuration

```
tx_delay(n)  = 2.4 + 0.2 * n^1.9   ms   (no clamp)
dtx_delay(n) = 1.1 + 0.6 * n^1.9   ms   (no clamp)
rx_delay(n)  = 0
```

Sweep parameters: `tx_base=2.4 tx_slope=0.2 tx_pow=1.9 dtx_base=1.1 dtx_slope=0.6 dtx_pow=1.9 rx_base=0 rx_slope=0 rx_pow=1.0` (clamp disabled).

Where **n** = neighbor count for each repeater (measured during runtime).

## Delay Table (per neighbor count)

| Neighbors (n) | tx_delay (ms) | dtx_delay (ms) |
| :-----------: | :-----------: | :------------: |
|       0       |     2.40      |     1.10       |
|       1       |     2.60      |     1.70       |
|       2       |     3.15      |     3.34       |
|       3       |     4.01      |     5.94       |
|       4       |     5.19      |     9.46       |
|       5       |     6.66      |    13.87       |
|       6       |     8.42      |    19.16       |
|       8       |    12.80      |    32.29       |
|      10       |    18.29      |    48.76       |
|      12       |    24.86      |    68.49       |
|      15       |    36.72      |   104.07       |
|      20       |    61.69      |   178.97       |
|      24       |    86.24      |   252.61       |

Note: MeshCore applies these as **factors** to packet airtime, producing a random back-off in `[0, 5 * airtime * factor]`.

## Performance

Measured across 4 Seattle-area topologies (~165 nodes each, 15-min simulations, 6 seeds per configuration):

| Density    | Link Survival | Max Edges | Msg Delivery | Channel Delivery | ACK Delivery | Avg ACK Copies |
| ---------- | :-----------: | :-------: | :----------: | :--------------: | :----------: | :------------: |
| sparse     |      0.2      |     6     |    64.3%     |      62.2%       |  **47.0%**   |      1.40      |
| medium     |      0.4      |    12     |    56.3%     |      79.8%       |  **35.3%**   |      1.70      |
| dense      |      0.7      |    16     |    59.0%     |      69.7%       |  **38.0%**   |      1.80      |
| very_dense |      0.9      |    24     |    55.0%     |      47.0%       |  **30.0%**   |      1.60      |
| **Mean**   |               |           |  **58.7%**   |    **64.7%**     |  **37.6%**   |    **1.63**    |

Metric definitions:
- **Msg Delivery** — fraction of `msg`-type (DM) packets delivered to recipient.
- **Channel Delivery** — fraction of channel broadcasts delivered to all listeners.
- **ACK Delivery** — fraction of sent DMs that produced at least one ACK back at the sender (what the user actually sees as "message confirmed").
- **Avg ACK Copies** — mean number of distinct ACK copies received per delivered message (redundancy; >1 indicates multiple return paths or duplicate relays survived).

## Justification

### Why these parameters

1. **`tx_pow = 1.9` (super-linear scaling)** — confirmed across two independent sweeps (clamped and no-clamp). Dense nodes need disproportionately more back-off than sparse ones to avoid collisions.

2. **`tx_base = 2.4, tx_slope = 0.2`** — low slope × high exponent = flat response at low density, explosive growth as neighbor count rises.

3. **`dtx_pow = 1.9, dtx_slope = 0.6`** — previous (clamped) sweep suggested `dtx_pow = 1.5`, but without the 15 ms ceiling the optimum jumps to `1.9` with a stronger slope. The clamp was actively suppressing the true optimum: direct/ACK traffic in dense meshes benefits from *very* large back-offs (up to ~250 ms at n=24).

4. **`rx_delay = 0`** — verified as non-influential by `run_sweep_rxdelay.sh`: score-based RX queueing (`calcRxDelay`) collapses to 0 for most packets because the Dispatcher's <50 ms threshold + SNR-gated reception means only very-low-score flood packets ever see a non-zero wait, and those rarely exist in our topologies.

### Comparison to firmware defaults

Current MeshCore defaults: `tx_delay_factor=0.5`, `direct_tx_delay_factor=0.3`, `rx_delay_base=0` (flat, no neighbor scaling).

**Measured performance at defaults** (same 4 topologies, 6 seeds each):

| Density    | Msg Delivery | Channel Delivery | ACK Delivery | Avg ACK Copies |
| ---------- | :----------: | :--------------: | :----------: | :------------: |
| sparse     |    67.0%     |      57.5%       |    36.0%     |      0.60      |
| medium     |    62.7%     |      78.8%       |    33.7%     |      0.70      |
| dense      |    65.0%     |      64.5%       |    24.3%     |      0.50      |
| very_dense |    64.3%     |      43.0%       |    13.0%     |      0.20      |
| **Mean**   |  **64.8%**   |    **60.9%**     |  **26.8%**   |    **0.50**    |

**Proposed vs. defaults — delta per density:**

| Density    | Msg Delivery Δ | ACK Delivery Δ | ACK Copies Δ |
| ---------- | :------------: | :------------: | :----------: |
| sparse     |     −2.7pp     |   **+11.0pp**  |    +0.80     |
| medium     |     −6.3pp     |    **+1.6pp**  |    +1.00     |
| dense      |     −6.0pp     |   **+13.7pp**  |    +1.30     |
| very_dense |     −9.3pp     |   **+17.0pp**  |    +1.40     |
| **Mean**   |   **−6.1pp**   |  **+10.9pp**   |   **+1.13**  |

**Interpretation:**
- Defaults win slightly on raw message delivery (flood packets arrive faster with less back-off) but **lose heavily on ACKs** — especially in dense networks where `dtx=0.3` isn't enough to prevent ACK collisions on the return path.
- Proposed config trades ~6pp of msg delivery for ~11pp more ACKs and ~3× more redundant ACK copies.
- Biggest wins are in **very_dense (+17pp ACK)** and **dense (+13.7pp ACK)** — exactly where the firmware defaults break down.

**Per-neighbor back-off comparison:**

| Neighbors | Default tx | Proposed tx | Default dtx | Proposed dtx |
| :-------: | :--------: | :---------: | :---------: | :----------: |
|     0     |    0.5     |    2.40     |     0.3     |     1.10     |
|     5     |    0.5     |    6.66     |     0.3     |    13.87     |
|    10     |    0.5     |   18.29     |     0.3     |    48.76     |
|    24     |    0.5     |   86.24     |     0.3     |   252.61     |

### Alternative: robust (balanced) configuration

Slightly lower combined score but more uniform across densities (less per-density variance):

```
tx_delay(n)  = 1.8 + 0.2 * n^1.9
dtx_delay(n) = 1.4 + 0.4 * n^1.9
rx_delay(n)  = 0
```

Per-density ACK: sparse 44.7 / medium 37.3 / dense 36.3 / very_dense 30.3 (mean 37.2%). Medium density holds up slightly better, at the cost of ~0.4pp overall.

### Comparison to prior clamped proposal

| Metric           |      Clamped (2026-04-17)       | No-clamp (2026-04-18) |
| ---------------- | :-----------------------------: | :-------------------: |
| tx formula       | `clamp(1.8 + 0.2·n^1.9, 0, 15)` |   `2.4 + 0.2·n^1.9`   |
| dtx formula      | `clamp(1.1 + 0.4·n^1.5, 0, 15)` |   `1.1 + 0.6·n^1.9`   |
| ACK — sparse     |              44.0%              |         47.0%         |
| ACK — medium     |              38.3%              |         35.3%         |
| ACK — dense      |              38.0%              |         38.0%         |
| ACK — very_dense |              26.0%              |         30.0%         |
| **ACK — mean**   |            **36.6%**            |       **37.6%**       |

The 15 ms clamp was leaving performance on the table in dense/very_dense regimes, and (more subtly) was hiding the true optimal `dtx_pow` of 1.9.

## Methodology

**Sweep setup** (`delay_optimization_v2/run_sweep.sh`):
- 4 density variants: sparse / medium / dense / very_dense
- Parameter grid: `tx_base∈{1.8,2.1,2.4}`, `tx_slope∈{0,0.2,0.4}`, `tx_pow∈{0.7,1.1,1.5,1.9}`, `dtx_base∈{0.8,1.1,1.4}`, `dtx_slope∈{0.2,0.4,0.6}`, `dtx_pow∈{0.7,1.1,1.5,1.9}`
- **Clamp disabled** (`clamp_max=100000`)
- 1296 combos × 6 seeds = 7776 runs per density
- Topology: 0.10×0.15° region around Seattle (47.60,-122.42 to 47.70,-122.27), ~165 real nodes
- Radio: US 915 MHz, SF10, BW 250 kHz, CR 1

**Scoring:**
- Per-density normalized score (min-max 0–1) for `mean_ack_pct`
- Combined score = mean(per-density scores) × coverage
- Coverage = 100% for all top-10 configs

**Data files:**
- `results_sparse_20260418_054055.csv`
- `results_medium_20260418_054223.csv`
- `results_dense_20260418_054223.csv`
- `results_very_dense_20260418_054223.csv`
