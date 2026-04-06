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

### Metrics & ranking

Three metrics are collected per run. Variants are ranked by **Delivery %** only; the other two are reported for analysis but do not affect sort order. Lower standard deviation breaks ties.

| Priority | Metric | Definition | Why |
|:--------:|--------|------------|-----|
| 1 | **Delivery %** | direct messages received / direct messages sent | Primary objective — point-to-point reliability is the most user-visible quality measure |
| 2 | **Channel %** | channel receptions / expected receptions (sent × (N_companions − 1)) | Secondary — flood broadcast reach indicates overall network health |
| 3 | **Ack %** | acknowledgements received / acks expected | Tertiary — round-trip success is desirable but depends on both directions working |
| — | **Stability** | standard deviation of Delivery % across seeds | Tiebreaker — lower variance means more predictable behaviour |

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

The script prints a ranked table of all variants with delivery metrics and message fate diagnostics. For the best result, it outputs the full 13-entry C array ready to paste into `DelayTuning.h`:

```
  Top 10 variants (of 384):
    tx_b   tx_s   dtx_b  dtx_s    rx_b   rx_s    mean    std   min   max  delivered   acks   chan  col/lost  drp/lost
  ------ ------  ------ ------  ------ ------  ------  -----  ----  ----  ---------  -----  -----  --------  --------
   0.000 0.3000   0.000 0.3000   0.500 0.3000   51.0%   3.3%   48%   56%   153/300      4%    51%      73.9       0.8
   0.000 0.3000   0.500 0.6000   0.500 0.3000   50.3%   3.4%   46%   56%   151/300      6%    54%      65.2       1.1
```

The last two columns (`col/lost`, `drp/lost`) come from per-message fate tracking (see Section 5c below). They show the mean number of collisions and drops experienced per lost (undelivered) message, helping diagnose whether failures are due to congestion (high collisions) or link quality (high drops).

### CSV output

Results are saved incrementally to CSV via the `-o` flag. Rows are appended after each variant completes and flushed immediately, so partial results survive interruption. At the end of a complete run, the CSV is rewritten sorted by delivery %.

CSV columns:

| Column | Description |
|--------|-------------|
| `tx_base` .. `rx_slope` | The 6 linear model parameters |
| `mean_delivery_pct` | Mean delivery % across seeds |
| `std_pct`, `min_pct`, `max_pct` | Delivery % statistics |
| `total_delivered`, `total_sent` | Absolute counts summed across seeds |
| `mean_ack_pct` | Mean ack % across seeds (empty if no acks) |
| `mean_chan_pct` | Mean channel % across seeds (empty if no channel messages) |
| `n_seeds` | Number of seeds completed |
| `fate_tracked` | Total messages tracked across seeds |
| `fate_delivered` | Messages where fate tracking confirmed delivery |
| `fate_lost` | Messages where fate tracking found no delivery |
| `lost_mean_collision` | Mean collisions per lost message (averaged across seeds with losses) |
| `lost_mean_drop` | Mean drops per lost message (averaged across seeds with losses) |

### Limitations

- **Linear model constraint**: Real optimal tables may be non-linear (e.g., plateau in the middle, steeper rise at high neighbor counts). The linear model cannot capture these shapes. Future work could add piecewise-linear or polynomial models.
- **Sequential builds**: Each variant requires a full rebuild (~15-25s). This limits practical sweep size compared to `optimize_delays.py` where all runs can be parallelized.
- **Single build directory**: Only one variant can be compiled at a time. Running multiple instances of the script simultaneously against the same build directory will corrupt results.
- **Topology-specific**: Optimal table values depend on the network's neighbor-count distribution. A network where most nodes have 2-4 neighbors will be sensitive to different table entries than one where most have 8-10.
- **Static table**: The linear model assumes the relationship between neighbor count and optimal delay is consistent across all network conditions. In practice, the optimal slope may depend on traffic load, message patterns, or radio parameters.

## 5c. Message Fate Analysis

### Motivation

When messages fail to deliver, aggregate stats (e.g., "30% delivery") don't explain *why*. Was it collisions saturating the network? Link drops on weak paths? The message fate tracker follows each scheduled message through the relay chain and counts per-message collisions and drops, giving actionable diagnostic data.

### How it works

The orchestrator tracks each `msg`/`msga` command from send to delivery (or loss):

1. **Command detection**: When `processCommands` processes a `msg <dest>` or `msga <dest>` command, a `MessageFate` entry is created with `from_idx`, `to_idx`, and `send_time_ms`.

2. **Initial TX linking**: In `registerTransmissions`, the first TX from the sending node (matching "msg" packet type) is linked to the fate via its FNV-1a packet hash.

3. **Relay chain tracking**: When a node receives a tracked packet (in `deliverReceptions`), it's marked as carrying that fate. If the node later transmits a "msg" packet (in `registerTransmissions`), the new TX hash is linked to the same fate -- even if the relay happens many simulation steps later (MeshCore's Dispatcher adds delay).

4. **Event counting**: For each tracked packet hash, the orchestrator counts:
   - **tx_count**: how many times the message was transmitted (including relays)
   - **rx_count**: successful receptions at any node
   - **collisions**: receptions destroyed by interference
   - **drops**: receptions lost to half-duplex conflicts or link loss

5. **Delivery detection**: If any tracked hash reaches the destination node as a successful RX, the fate is marked `delivered`.

### Orchestrator output

The orchestrator prints a per-message fate summary to stderr after the delivery stats:

```
Message fate (50 tracked, 11 delivered, 39 lost):
  Per delivered message: mean tx=194.2  rx=325.5  collision=290.2  drop=0.8
  Per lost message:      mean tx=43.4  rx=65.0  collision=50.3  drop=0.6
```

Interpretation:
- **Delivered messages** had extensive network activity (194 TX, 325 RX) -- the flood reached enough nodes despite 290 collisions per message.
- **Lost messages** had much less activity (43 TX, 65 RX) -- the relay chain died early, starved by collisions.
- **Low drop counts** in both cases suggest link drops are not the primary failure mode; collisions dominate.

### Integration with optimize_tuning.py

The optimization script parses the fate summary from each run and aggregates across seeds. Two columns appear in both the results table and CSV:

| Column | Meaning |
|--------|---------|
| `col/lost` / `lost_mean_collision` | Mean collisions per lost message -- high values indicate congestion-dominated failure |
| `drp/lost` / `lost_mean_drop` | Mean drops per lost message -- high values indicate link-quality-dominated failure |

These help distinguish between parameter regimes where messages fail due to collision overload (increase delays) vs. weak links (topology issue, delays won't help).

### Limitations

- **Relay ambiguity**: When a node receives multiple tracked messages and relays one, the relay is linked to all active fates. This can slightly over-count TX/RX for individual fates. Rare in practice.
- **Hash change on relay**: MeshCore modifies packet headers when relaying, changing the packet hash. The tracker bridges this gap using temporal correlation (RX at node N, then TX from node N), but if a node receives two different tracked messages simultaneously, their relay hashes may be cross-linked.
- **Only `msg`/`msga` commands**: Channel messages (`msgc`) are not tracked in this first approach.
- **No per-hop breakdown**: The tracker counts total collisions/drops across all hops, not which specific hop failed. A message that traverses 8 hops and loses 3 packets to collisions at hop 5 looks the same as one that loses 3 packets across hops 2, 4, and 7.

## 5d. Multi-Density Test Scripts

### Purpose

Optimal delay parameters may differ across network densities. The `delay_optimization/` directory includes three scripts that generate networks with different link densities from the Gdansk region and run full parameter sweeps on each:

| Script | Density | Key topology flags | Typical neighbors |
|--------|---------|-------------------|-------------------|
| `run_sparse.sh` | Sparse | `--link-survival 0.2 --max-edges-per-node 6 --max-good-links 2` | 1-3 |
| `run_medium.sh` | Medium | `--link-survival 0.4 --max-edges-per-node 12 --max-good-links 3` | 3-6 |
| `run_dense.sh` | Dense | `--link-survival 0.7 --max-edges-per-node 16 --max-good-links 6` | 6-12 |

### What each script does

1. Runs `topology_generator` to generate a topology from the Gdansk region API
2. Runs `inject_test.py` to place companions and generate message schedules
3. Runs `optimize_tuning.py` with the generated config

Results are saved as `results_<density>_<timestamp>.csv` in the `delay_optimization/` directory.

### Density control parameters

The topology generator uses these flags to control link density:

| Flag | Effect |
|------|--------|
| `--link-survival` | Sigmoid-based probability that an ITM-computed link survives. Lower values = sparser network. |
| `--max-edges-per-node` | Hard cap on total neighbors per node |
| `--max-good-links` | Cap on "good" links (SNR > 0) per node. Remaining slots filled with weaker links. |

### Usage

```bash
cd delay_optimization
bash run_sparse.sh    # sparse network sweep
bash run_medium.sh    # medium network sweep
bash run_dense.sh     # dense network sweep
```

Each script accepts `MESHCORE_DIR` as an environment variable to point to the MeshCore fork.

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

All results use Gdansk/Pomerania region topologies generated via `topology_generator` with ITM propagation model. Three network densities are tested (see Section 5d). Each sweep uses `optimize_tuning.py` with 3 seeds per variant. CSV files with full results are stored in this directory.

### 7.1 Sparse network

Script: `run_sparse.sh`. Topology: `--link-survival 0.2 --max-edges-per-node 6 --max-good-links 2`.

CSV: [`results_sparse_<timestamp>.csv`]

*(Results pending — run `bash run_sparse.sh`)*

### 7.2 Medium network

Script: `run_medium.sh`. Topology: `--link-survival 0.4 --max-edges-per-node 12 --max-good-links 3`.

CSV: [`results_medium_<timestamp>.csv`]

*(Results pending — run `bash run_medium.sh`)*

### 7.3 Dense network

Script: `run_dense.sh`. Topology: `--link-survival 0.7 --max-edges-per-node 16 --max-good-links 6`.

CSV: [`results_dense_<timestamp>.csv`]

*(Results pending — run `bash run_dense.sh`)*

### Key findings

*(To be filled after all three sweeps complete.)*
