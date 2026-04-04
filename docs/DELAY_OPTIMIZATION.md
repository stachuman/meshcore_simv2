# Delay Parameter Optimization — Methodology

## Overview

This document describes how we set up simulations to find optimal `rxdelay`, `txdelay`, and `direct.txdelay` values for a real MeshCore repeater network. The pipeline: real topology data -> simulated network -> automated message delivery tests -> parameter sweep.

## 1. Test Topology

### Source data

Starting point is `simulation/topology.json` — a snapshot exported from a live MeshCore network (~74 nodes in the Gdansk/Gdynia area, Poland). Each node has:
- GPS coordinates (lat/lon), though some nodes report (0,0)
- Per-neighbor SNR measurements (directional — A hearing B differs from B hearing A)
- Confidence scores and measurement source (direct neighbor table, packet trace, or inferred)

### Processing pipeline (`tools/convert_topology.py`)

The raw data goes through several stages:

1. **Node filtering** — drops stub nodes (short prefix), sanitizes names (strips emoji/special chars)
2. **Coordinate estimation** — nodes with missing GPS get positions estimated via weighted centroid of known neighbors (weight proportional to SNR)
3. **Edge filtering** — drops edges below SNR threshold (default -10 dB for SF8), low confidence (<0.7), inferred source, or excessive distance (>80 km)
4. **Gap-fill** — adds estimated links for nearby repeater pairs that lack measured edges, using a log-distance propagation model fitted from real measurements, with Gaussian shadow fading. Capped at 2 "good" (SNR>0) edges and 8 total edges per node
5. **Loss model** — each link gets a packet loss probability via logistic sigmoid mapping SNR to loss (50% loss at -6 dB, near-zero above 0 dB)

After processing, a typical run yields ~71 repeaters with ~357 links (220 measured + 137 gap-filled).

### Limitations

- **Coordinate accuracy**: ~5 nodes have estimated (not measured) coordinates. Gap-filled links for these nodes may not reflect real RF paths.
- **SNR snapshot**: Measurements are from a single point in time. Real SNR varies with weather, interference, antenna movement.
- **Missing nodes**: The snapshot may not include all active repeaters. Some nodes with short prefixes are dropped.
- **Gap-fill assumptions**: The propagation model (log-distance + shadow fading) is a rough approximation. Real terrain effects (hills, buildings) are not modeled.
- **Symmetric gap-fill**: Gap-filled links use `bidir: true` with averaged SNR. Real links are often asymmetric.

## 2. Companion Placement

### Method (`tools/build_real_sim.py`)

Companion nodes (message endpoints) are placed automatically using **farthest-point sampling**:

1. Pick a random repeater as the first companion's anchor (seeded RNG for reproducibility)
2. For each subsequent companion: select the repeater that maximizes the minimum distance to all already-chosen repeaters

This produces geographically spread companions that stress-test multi-hop routing rather than clustering in one area.

Each companion connects to its anchor repeater with a strong link (SNR=10 dB, no loss) — simulating a user standing next to a repeater.

### Limitations

- **Uniform strong links**: Real companion-to-repeater links vary in quality. A companion indoors or far from its repeater would have worse SNR.
- **No mobility**: Companions are static for the entire simulation.
- **Anchor selection ignores connectivity**: A geographically distant repeater might be poorly connected to the mesh. The algorithm optimizes geographic spread, not routing quality.

## 3. Message Simulation


### Hot start

Simulation always start with hot start phase - network stabilization. In this phase all collision detections are off, the purpose is to let each
repeater knows its surronding AND each companion knows all the other companions.

### Simulation length

Test simulation period is 22 minutes with the following setup:

- **12 companions** placed via farthest-point sampling across 71 repeaters
- **Message interval**: 200s mean (each schedule gets a random offset within [0, interval) for desynchronization)
- **4 concurrent patterns**: 1-to-1, 1-to-many, many-to-1, and channel broadcast — all interleaved from the same base time
- **Deterministic randomization**: 3 different seeds per parameter combination (seeds 42, 43, 44)


### Schedule patterns

Four patterns run concurrently (interleaved), each with random start offsets per schedule to avoid artificial synchronization:

| Pattern | Command | Description | Purpose |
|---|---|---|---|
| **1-to-1** | `msga` | Round-robin pairs (c01->c02, c02->c03, ..., cN->c01) | Baseline point-to-point delivery |
| **1-to-many** | `msga` | First companion broadcasts to all others | Tests fan-out / flood routing |
| **many-to-1** | `msga` | All companions send to last companion | Tests convergent traffic / congestion |
| **channel broadcast** | `msgc` | Each companion sends on public channel (channel 0) | Tests flood routing under mixed traffic |

Direct messages (first three patterns) use `msga` (message with ack tracking) so both delivery and acknowledgement rates are measured. Channel messages use `msgc` (flood, no ack) — delivery is tracked by counting receptions at all other companions.

### Timing

- All four patterns start from the same base time: warmup + 5 seconds (after hot-start advert exchange completes)
- Each individual schedule gets a random offset within [0, interval) to desynchronize
- All patterns compete for airtime simultaneously — creating realistic mixed traffic load
- Duration auto-extends to fit all schedules + 30s delivery buffer

### Message size

Test messages are ~70-75 characters each, approximating realistic MeshCore message length. Examples:
- Direct: `"1to1 c01->c02 seq=3 direct msg status ok signal good position hold steady"`
- Channel: `"chan c01 seq=3 public channel broadcast mesh network status check all nodes"`

### Limitations

- **Artificial traffic patterns**: Real networks see irregular, bursty messaging — not periodic schedules.
- **Fixed message size**: All messages are ~75 chars. Real traffic includes short status pings and longer payloads.

## 4. Radio Physics

The orchestrator simulates LoRa radio physics:

- **Collision detection**: 3-stage model with timing-dependent capture (1 dB locked / 6 dB unlocked), preamble grace, FEC tolerance. See [RADIO_MODEL.md](RADIO_MODEL.md) for full details.
- **Half-duplex**: nodes cannot TX while receiving (RX blocks TX, TX aborts active RX)
- **Listen-before-talk**: channel activity detection with preamble sensing delay
- **SNR variance**: per-link Gaussian jitter on each reception
- **Stochastic link loss**: per-link drop probability applied after collision checks

### Limitations

- **No multipath/fading dynamics**: SNR variance is static Gaussian, not correlated over time (no slow fading, no Doppler)
- **No duty cycle**: Real LoRa is subject to regulatory duty-cycle limits (1% in EU). The simulator does not enforce this, hence, it is easy to turn on
- **No frequency-offset modeling**: All nodes assumed on the same channel. Real LoRa receivers have a frequency-dependent capture margin (+-30kHz for BW125).
- **No near-far effect**: All SNR values come from the link table. A node receiving a weak distant signal next to a strong nearby transmitter doesn't experience additional desensitization beyond what the collision model captures.
- **Clock stagger only**: Node desynchronization uses random clock offsets (0-120s). Real networks have drift, GPS sync, and power-cycle patterns.

## 5. Delay Parameter Sweep

### What we're optimizing

Three MeshCore repeater parameters:

| Parameter | Default | Effect |
|---|---|---|
| `rxdelay` | 0.0 | Base delay before processing received packets. Higher values give more time for better routes to arrive (SNR-weighted exponential backoff) |
| `txdelay` | 0.5 | Random backoff factor for flood (broadcast) retransmissions. Higher = more spread = fewer collisions but slower delivery |
| `direct.txdelay` | 0.3 | Random backoff factor for direct (point-to-point) retransmissions |

### Method (`tools/optimize_delays.py`)

1. Define parameter grid (e.g., rxdelay 0-5 step 1, txdelay 0-1 step 0.2, direct.txdelay 0-0.5 step 0.1)
2. For each combination, inject `set` commands to all repeaters just after warmup
3. Run each combination with multiple seeds (default 3) to account for stochastic variation
4. Parse delivery percentage from orchestrator output
5. Report mean, standard deviation, min, max per combination

### Metrics

- **Delivery %**: direct messages received / direct messages sent (primary metric)
- **Ack %**: acknowledgements received / acks expected (measures round-trip success)
- **Channel %**: channel receptions / expected receptions. Expected = sent_group * (N_companions - 1), since each channel message should reach all other companions via flood routing.
- **Stability**: standard deviation across seeds (lower = more reliable)

### Limitations

- **Set command side-effect**: MeshCore's `set` command triggers a preference write/reload even when setting the same value as the default. This means optimizer runs are not directly comparable to an unmodified baseline — but all runs within the sweep are affected equally, so relative rankings are valid.
- **Grid resolution**: Coarse grids may miss narrow optima. Fine grids are expensive (each run takes ~5s for a 71-node network).
- **Single topology**: Results are specific to this network topology. Different network shapes/sizes may have different optimal delays.
- **Fixed radio params**: The sweep holds SF/BW/CR constant. Optimal delays may differ for SF7 vs SF12.
- **No interaction with other settings**: Only delay parameters are swept. Other MeshCore settings (e.g., max hops, flood limits) stay at defaults.
- **Uniform settings**: All repeaters are set to the exactly same delay settings - no distinction for 'local/regional' repeaters. We are testing kind of 'default' settings.

## 5b. Auto-Tune Table Optimization

### Background

MeshCore supports **automatic delay tuning** where each repeater independently adjusts its delay parameters based on how many active neighbors it has. The mapping from neighbor count to delay values is stored in a compile-time lookup table (`DelayTuning.h`) with 13 entries (0-12+ neighbors), each containing three floats:

```c
struct DelayTuning {
  float tx_delay;          // flood retransmission backoff factor
  float direct_tx_delay;   // direct retransmission backoff factor
  float rx_delay_base;     // base delay before processing received packets
};
```

When `auto_tune_delays` is enabled (on by default), each repeater calls `recalcAutoTune()` periodically. This counts active neighbors (SNR > 0, heard within 7 days) and looks up the corresponding table entry. Sparse nodes (few neighbors) use lower delays for faster forwarding; dense nodes (many neighbors) use higher delays to reduce collisions.

Unlike the static sweep (Section 5) which sets identical delays on all repeaters, auto-tuning lets each repeater adapt to its local topology — a more realistic and potentially better-performing approach.

### Parameterization

Optimizing all 39 table values (13 entries x 3 floats) directly would create an intractable search space. Instead, the table is parameterized as a **linear model** with 6 free parameters:

```
value(n) = clamp(base + slope * n, clamp_min, clamp_max)
```

where `n` is the neighbor count (0-12). Three (base, slope) pairs — one for each delay type:

| Parameter | Meaning | Typical range |
|---|---|---|
| `tx_base` | tx_delay at 0 neighbors | 0.5 - 2.0 |
| `tx_slope` | tx_delay increment per neighbor | 0.00 - 0.10 |
| `dtx_base` | direct_tx_delay at 0 neighbors | 0.2 - 1.0 |
| `dtx_slope` | direct_tx_delay increment per neighbor | 0.00 - 0.05 |
| `rx_base` | rx_delay_base at 0 neighbors | 0.2 - 1.0 |
| `rx_slope` | rx_delay_base increment per neighbor | 0.00 - 0.05 |

Example: `tx_base=1.0, tx_slope=0.033` produces tx_delay values from 1.000 (0 neighbors) to 1.396 (12 neighbors).

### Method (`tools/optimize_tuning.py`)

The script operates differently from `optimize_delays.py` because it must recompile the MeshCore fork for each parameter variant:

1. Parse 6 parameter ranges (each accepts `value` or `min:max:step` format)
2. Backup original `DelayTuning.h`
3. **For each parameter combination** (sequential — shared build directory):
   a. Generate `DelayTuning.h` from linear formula
   b. Rebuild the fork orchestrator (`cmake --build build-fork`, ~15-25s)
   c. Run all seeds in parallel (via `-j` flag)
   d. Parse delivery/ack/channel results
4. Restore original `DelayTuning.h` (guaranteed via `try/finally`)
5. Aggregate and rank results

The config is sanitized before each run: any manual `set rxdelay`/`set txdelay`/`set direct.txdelay` commands are stripped (these disable auto-tune), and `set autotune on` is injected for all repeaters.

### CLI

```bash
python3 tools/optimize_tuning.py simulation/real_network.json \
  --tx-base 0.8:1.2:0.1 --tx-slope 0.02:0.05:0.01 \
  --dtx-base 0.3:0.5:0.05 --dtx-slope 0.01:0.03:0.005 \
  --rx-base 0.3:0.5:0.05 --rx-slope 0.01:0.03:0.005 \
  --seeds 3 --build-dir build-fork \
  --meshcore-dir ../MeshCore-stachuman \
  -j 4 -o results.csv
```

| Flag | Default | Description |
|---|---|---|
| `config` (positional) | required | Simulation config JSON |
| `--tx-base` | `1.0` | tx_delay intercept range |
| `--tx-slope` | `0.02:0.04:0.005` | tx_delay per-neighbor slope range |
| `--dtx-base` | `0.4` | direct_tx_delay intercept range |
| `--dtx-slope` | `0.01:0.03:0.005` | direct_tx_delay slope range |
| `--rx-base` | `0.4` | rx_delay_base intercept range |
| `--rx-slope` | `0.01:0.03:0.005` | rx_delay_base slope range |
| `--clamp-min` | `0.05` | Minimum value for any table entry |
| `--clamp-max` | `5.0` | Maximum value for any table entry |
| `--seeds` | `3` | Seeds per variant |
| `--seed-base` | `42` | First seed value |
| `--build-dir` | `build-fork` | CMake build directory |
| `--meshcore-dir` | `../MeshCore-stachuman` | Path to MeshCore fork |
| `-j` / `--jobs` | `1` | Parallel seed runs per variant |
| `--top` | `10` | Show top N results |
| `-o` / `--output` | none | CSV output path |
| `-v` / `--verbose` | off | Per-run orchestrator summary |

All range flags accept: `1.0` (constant), `1.0:1.0:0` (constant), or `0.0:5.0:1.0` (sweep).

### Prerequisites

The script requires a pre-configured CMake build directory pointing to the MeshCore fork:

```bash
cmake -S . -B build-fork -DMESHCORE_DIR=$(pwd)/../MeshCore-stachuman
cmake --build build-fork
```

### Time estimation

Each variant requires a rebuild (~15-25s) plus seed runs. For a simulation config that takes ~30s per seed:

| Sweep dimensions | Variants | Seeds | Time per variant | Total |
|---|---|---|---|---|
| 1x1x1x1x1x1 | 1 | 3 | ~50s | ~1 min |
| 3x3x1x1x1x1 | 9 | 3 | ~50s | ~8 min |
| 5x4x3x3x3x3 | 1620 | 3 | ~50s | ~22 hours |

Strategy: start with coarse sweeps on few parameters (fix others as constants), then refine around the best region.

### Output

The script prints a ranked table of all variants and, for the best result, outputs the full 13-entry C array ready to paste into `DelayTuning.h`:

```
Best: tx=1.1+0.04*n  dtx=0.4+0.025*n  rx=0.4+0.025*n
  -> 15.0% mean delivery (std=0.0%, range 15-15%)

  C array for DelayTuning.h:
  static const DelayTuning DELAY_TUNING_TABLE[] = {
    {1.100f, 0.400f, 0.400f},  // 0 neighbors
    {1.140f, 0.425f, 0.425f},  // 1 neighbors
    ...
    {1.580f, 0.700f, 0.700f},  // 12 neighbors
  };
```

### Limitations

- **Linear model constraint**: Real optimal tables may be non-linear (e.g., plateau in the middle, steeper rise at high neighbor counts). The linear model cannot capture these shapes. Future work could add piecewise-linear or polynomial models.
- **Sequential builds**: Each variant requires a full rebuild (~15-25s). This limits practical sweep size compared to `optimize_delays.py` where all runs can be parallelized.
- **Single build directory**: Only one variant can be compiled at a time. Running multiple instances of the script simultaneously against the same build directory will corrupt results.
- **Topology-specific**: Optimal table values depend on the network's neighbor-count distribution. A network where most nodes have 2-4 neighbors will be sensitive to different table entries than one where most have 8-10.
- **Static table**: The linear model assumes the relationship between neighbor count and optimal delay is consistent across all network conditions. In practice, the optimal slope may depend on traffic load, message patterns, or radio parameters.

## 6. Running the Pipeline

```bash
# 1. Generate simulation config (10 companions, SF8/BW125k, direct + channel messages)
python3 tools/build_real_sim.py simulation/topology.json \
  --companions 10 --sf 8 --bw 125000 --cr 4 \
  --msg-interval 30 --msg-count 5 \
  -o simulation/real_network.json -v

# 1b. Same but with separate channel interval, or no channel messages
python3 tools/build_real_sim.py simulation/topology.json \
  --companions 10 --sf 8 --bw 125000 --cr 4 \
  --msg-interval 30 --msg-count 5 \
  --chan-interval 60 --chan-count 3 \
  -o simulation/real_network.json -v

python3 tools/build_real_sim.py simulation/topology.json \
  --companions 10 --no-channel \
  -o simulation/real_network.json -v

# 2. Quick test run
build/orchestrator/orchestrator simulation/real_network.json > output.ndjson

# 3a. Static delay sweep (all repeaters get same values)
python3 tools/optimize_delays.py simulation/real_network.json \
  --rxdelay 0:5:1 --txdelay 0:1:0.2 --direct-txdelay 0:0.5:0.1 \
  --seeds 3 -j 4 -o results_static.csv

# 3b. Auto-tune table sweep (each repeater adapts by neighbor count)
# Requires MeshCore fork build:
#   cmake -S . -B build-fork -DMESHCORE_DIR=$(pwd)/../MeshCore-stachuman
#   cmake --build build-fork
python3 tools/optimize_tuning.py simulation/real_network.json \
  --tx-base 0.8:1.2:0.1 --tx-slope 0.02:0.05:0.01 \
  --dtx-base 0.3:0.5:0.1 --dtx-slope 0.02 \
  --rx-base 0.3:0.5:0.1 --rx-slope 0.02 \
  --seeds 3 --build-dir build-fork \
  --meshcore-dir ../MeshCore-stachuman \
  -j 4 -o results_tuning.csv

# 3c. Apply specific static delays to a config (without sweep)
python3 tools/set_delays.py simulation/real_network.json \
  --rxdelay 1.0 --txdelay 0.5 --direct-txdelay 0.2 \
  -o simulation/real_network_tuned.json

# 4. Visualize a specific run
python3 visualization/visualize.py output.ndjson
```

Channel schedule flags for `build_real_sim.py`:

| Flag | Default | Description |
|---|---|---|
| `--no-channel` | off | Disable channel broadcast messages entirely |
| `--chan-interval` | same as `--msg-interval` | Channel message interval in seconds |
| `--chan-count` | same as `--msg-count` | Number of channel messages per companion |

## 7. Results


Sweep grid used for the results:

Run 1

```
Parameter sweep: 63 combinations x 3 seeds = 189 runs
  rxdelay:         [0.0, 0.5, 1.0]
  txdelay:         [0.0, 0.3, 0.6]
  direct.txdelay:  [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
  seeds:           [42, 43, 44]
  Repeaters:       71

=====================================================================================
  Completed 189 runs (63 combos x 3 seeds) in 3m45s
=====================================================================================
  Top 10 combinations (of 63):
=====================================================================================
   rxdelay   txdelay  d.txdelay    mean    std   min   max  delivered   acks   chan
  --------  --------  ---------  ------  -----  ----  ----  ---------  -----  -----
      0.00      0.00       1.50   19.3%   1.7%   17%   21%   197/1020    10%    46%
      1.00      0.00       1.50   19.3%   1.7%   17%   21%   197/1020    10%    46%
      0.00      0.00       1.00   19.3%   1.9%   18%   22%   198/1020    10%    45%
      1.00      0.00       1.00   19.3%   1.9%   18%   22%   198/1020    10%    45%
      0.50      0.00       0.50   19.0%   0.8%   18%   20%   194/1020     9%    45%
      0.00      0.00       3.00   19.0%   1.6%   17%   21%   194/1020     9%    47%
      1.00      0.00       3.00   19.0%   1.6%   17%   21%   194/1020     9%    47%
      0.00      0.00       0.00   18.7%   1.2%   17%   20%   192/1020     9%    43%
      1.00      0.00       0.00   18.7%   1.2%   17%   20%   192/1020     9%    43%
      0.50      0.00       0.00   18.3%   0.9%   17%   19%   187/1020    10%    44%

```
