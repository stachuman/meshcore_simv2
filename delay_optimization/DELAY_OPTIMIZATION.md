# Delay Parameter Optimization — Methodology

## Overview

This document describes how we set up simulations to find optimal delay tuning tables for MeshCore repeater networks. The pipeline: live network API -> ITM propagation model -> simulated topology -> companion + message injection -> automated parameter sweep.

## 1. Test Topology

### Source data

Topologies are generated from live MeshCore network data using the `topology_generator` module. It fetches node positions from the [MeshCore map API](https://map.meshcore.io), then computes RF links using the **Irregular Terrain Model (ITM / Longley-Rice)** propagation model with SRTM elevation data.

The Gdansk/Pomerania region (bounding box `53.7,17.3,54.8,19.5`) is used for all delay optimization tests. A typical fetch returns ~150-160 repeaters with GPS coordinates.

### Processing pipeline (`topology_generator`)

1. **API fetch** — downloads all nodes in the bounding box from the MeshCore map API. Responses can be cached (`--api-cache`) to avoid repeated downloads.
2. **ITM link computation** — for each pair of nodes within `--max-distance-km` (40 km), computes path loss using the ITM model with terrain profiles from SRTM data, then derives received SNR. Links below `--min-snr` (-10 dB) are dropped.
3. **Clutter attenuation** — adds `--clutter-db` (6 dB) to account for urban/suburban clutter not captured by SRTM.
4. **Link density control** — three parameters shape the final topology:
   - `--link-survival`: stochastic survival probability applied to each ITM-computed link (lower = sparser)
   - `--max-edges-per-node`: hard cap on total neighbors
   - `--max-good-links`: cap on "good" links (SNR > 0) per node; remaining slots filled with weaker links
5. **Config emission** — outputs a simulation config JSON with nodes (name, role, lat/lon) and bidirectional links (SNR, RSSI, loss probability).

### Key topology generator flags

| Flag | Default | Description |
|------|---------|-------------|
| `--region` | (required) | Bounding box: `lat_min,lon_min,lat_max,lon_max` |
| `--freq-mhz` | 869.618 | LoRa carrier frequency (EU ISM) |
| `--tx-power-dbm` | 20.0 | TX power |
| `--antenna-height` | 5.0 | Antenna height in meters |
| `--sf` / `--bw` / `--cr` | 8 / 62500 / 4 | LoRa radio parameters |
| `--max-distance-km` | 40 | Skip node pairs beyond this range |
| `--min-snr` | -10.0 | Drop links below this SNR |
| `--link-survival` | 1.0 | Stochastic survival probability per link |
| `--max-edges-per-node` | 12 | Hard cap on total neighbors |
| `--max-good-links` | (none) | Cap on good (SNR > 0) links per node |
| `--clutter-db` | 6.0 | Additional urban/suburban attenuation |
| `--api-cache` | (none) | Cache API responses to this file |

### Limitations

- **ITM model fidelity**: ITM uses SRTM terrain data (~30m resolution) but does not model buildings, vegetation, or local obstructions. The `--clutter-db` parameter is a rough compensation.
- **Stochastic link survival**: The `--link-survival` flag randomly drops links to simulate real-world sparsity (not all theoretically possible links exist in practice), but the randomness doesn't correlate with terrain or distance.
- **SNR snapshot**: Node positions come from a single API fetch. Real nodes may move, go offline, or change antenna configuration.
- **Symmetric links**: All links are generated as bidirectional with equal SNR. Real LoRa links are often asymmetric.
- **No measured SNR**: Unlike the older `convert_topology.py` pipeline (which used measured neighbor-table SNR), the topology generator computes all SNR values from the propagation model. This gives consistent coverage but may miss real-world anomalies.

## 2. Companion Placement

### Method (`tools/inject_test.py`)

Companion nodes (message endpoints) are placed automatically using **connectivity-aware farthest-point sampling**:

1. Identify the **largest connected component** of repeaters (SNR > 0 links only) to ensure all companions can theoretically reach each other
2. Filter to repeaters with at least `--min-neighbors` good neighbors (default 2) — avoids placing companions on poorly connected edge nodes
3. Apply **farthest-point sampling**: pick a random repeater as the first anchor, then iteratively select the repeater that maximizes the minimum geographic distance to all already-chosen anchors

The density test scripts place **4 companions** named `alice`, `bob`, `carol`, `dave`. Each companion connects to its anchor repeater with a strong link (SNR=10 dB, RSSI=-70 dBm) — simulating a user standing next to a repeater.

### Limitations

- **Uniform strong links**: Real companion-to-repeater links vary in quality. A companion indoors or far from its repeater would have worse SNR.
- **No mobility**: Companions are static for the entire simulation.
- **Geographic vs routing spread**: The algorithm optimizes geographic distance between anchors, not routing diversity. Two geographically distant repeaters might share the same bottleneck relay.

## 3. Message Simulation

### Hot start

Simulations always start with a hot-start phase — collision-free network stabilization. In this phase all collision detection is off; the purpose is to let each repeater know its surroundings AND each companion discover all other companions.

### Simulation parameters

The density test scripts use the following setup:

- **4 companions** placed via farthest-point sampling
- **15 minutes** simulation duration (900,000 ms)
- **Message interval**: 70s mean for direct messages, 80s mean for channel broadcasts
- **5 direct messages** and **4 channel messages** per schedule entry
- **Poisson-distributed timing**: each message gets an exponential inter-arrival time (mean = interval), clamped to [0.2x, 3x] to avoid extreme bunching or gaps. This models realistic user behavior rather than fixed-interval robots.
- **6 seeds** per parameter combination (seeds 42-47) to account for stochastic variation

### Schedule patterns

Four patterns run concurrently (interleaved), all using Poisson-distributed send times:

| Pattern | Command | Description | Purpose |
|---|---|---|---|
| **1-to-1** | `msga` | Round-robin pairs (alice->bob, bob->carol, carol->dave, dave->alice) | Baseline point-to-point delivery |
| **1-to-many** | `msga` | First companion sends to all others (alice->bob, alice->carol, alice->dave) | Tests fan-out / flood routing |
| **many-to-1** | `msga` | All send to last companion (alice->dave, bob->dave, carol->dave) | Tests convergent traffic / congestion |
| **channel broadcast** | `msgc` | Each companion sends on public channel (channel 0) | Tests flood routing under mixed traffic |

Direct messages (first three patterns) use `msga` (message with ack tracking) so both delivery and acknowledgement rates are measured. Channel messages use `msgc` (flood, no ack) — delivery is tracked by counting receptions at all other companions.

### Timing

- All four patterns start from the same base time: warmup + 5 seconds (after hot-start advert exchange completes)
- Each individual message gets a random Poisson-distributed send time
- All patterns compete for airtime simultaneously — creating realistic mixed traffic load
- Duration auto-extends to fit all schedules + 30s delivery buffer

### Message count

With 4 companions, the auto-scheduler generates per seed run:

| Pattern | Pairs | Msgs/pair | Total messages |
|---------|-------|-----------|----------------|
| 1-to-1 | 4 | 5 | 20 |
| 1-to-many | 3 | 5 | 15 |
| many-to-1 | 3 | 5 | 15 |
| **Direct total** | | | **50** |
| Channel | 4 senders | 4 | 16 |

50 direct messages per seed, 300 across 6 seeds.

### Message size

Test messages are ~25-40 characters each. Examples:
- Direct: `"1to1 alice->bob seq=3"`
- Channel: `"chan alice seq=3"`

### Limitations

- **Artificial traffic patterns**: Real networks see irregular, bursty messaging — not scheduled patterns, even with Poisson timing.
- **Small companion count**: 4 companions is a light traffic load compared to a busy real network with dozens of active users.
- **Fixed message size**: All messages are short. Real traffic includes longer payloads.

## 4. Radio Physics

The orchestrator simulates LoRa radio physics:

- **Collision detection**: 3-stage model with timing-dependent capture (3 dB locked / 6 dB unlocked), preamble grace, FEC tolerance. See [RADIO_MODEL.md](../docs/RADIO_MODEL.md) for full details.
- **Half-duplex**: nodes cannot TX while receiving (RX blocks TX, TX aborts active RX)
- **Listen-before-talk**: channel activity detection with preamble sensing delay
- **SNR variance**: per-link Gaussian jitter on each reception
- **Stochastic link loss**: per-link drop probability applied after collision checks

### Limitations

- **No multipath/fading dynamics**: SNR variance is static Gaussian, not correlated over time (no slow fading, no Doppler)
- **No duty cycle**: Real LoRa is subject to regulatory duty-cycle limits (1% in EU). The simulator does not enforce this.
- **No frequency-offset modeling**: All nodes assumed on the same channel. Real LoRa receivers have a frequency-dependent capture margin.
- **No near-far effect**: All SNR values come from the link table. A node receiving a weak distant signal next to a strong nearby transmitter doesn't experience additional desensitization beyond what the collision model captures.
- **Clock stagger only**: Node desynchronization uses random clock offsets (0-120s). Real networks have drift, GPS sync, and power-cycle patterns.

## 5. Delay Parameters

### What we're optimizing

Three MeshCore repeater parameters:

| Parameter | Default | Effect |
|---|---|---|
| `rxdelay` | 0.0 | Base delay before processing received packets. Higher values give more time for better routes to arrive (SNR-weighted exponential backoff) |
| `txdelay` | 0.5 | Random backoff factor for flood (broadcast) retransmissions. Higher = more spread = fewer collisions but slower delivery |
| `direct.txdelay` | 0.3 | Random backoff factor for direct (point-to-point) retransmissions |

### Two optimization approaches

**Static sweep** (`tools/optimize_delays.py`): sets identical delay values on all repeaters via `set` commands. Simple but ignores per-node density differences.

**Auto-tune table sweep** (`tools/optimize_tuning.py`): optimizes the compile-time auto-tune lookup table so each repeater independently adapts delays based on its neighbor count. This is the approach used by the density test scripts (Section 5d) and described in detail below.

### Metrics & ranking

Three metrics are collected per run. Variants are ranked by **Delivery %** only; the other two are reported for analysis but do not affect sort order. Lower standard deviation breaks ties.

| Priority | Metric | Definition | Why |
|:--------:|--------|------------|-----|
| 1 | **Delivery %** | direct messages received / direct messages sent | Primary objective — point-to-point reliability is the most user-visible quality measure |
| 2 | **Channel %** | channel receptions / expected receptions (sent x (N_companions - 1)) | Secondary — flood broadcast reach indicates overall network health |
| 3 | **Ack %** | acknowledgements received / acks expected | Tertiary — round-trip success is desirable but depends on both directions working |
| — | **Stability** | standard deviation of Delivery % across seeds | Tiebreaker — lower variance means more predictable behaviour |

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

### Parameterization

Optimizing all 39 table values (13 entries x 3 floats) directly would create an intractable search space. Instead, the table is parameterized as a **linear model** with 6 free parameters:

```
value(n) = clamp(base + slope * n, clamp_min, clamp_max)
```

where `n` is the neighbor count (0-12). Three (base, slope) pairs — one for each delay type:

| Parameter | Meaning | Sweep range (density scripts) |
|---|---|---|
| `tx_base` | tx_delay at 0 neighbors | 0.0 - 1.5 (step 0.5) |
| `tx_slope` | tx_delay increment per neighbor | 0.3 - 0.6 (step 0.3) |
| `dtx_base` | direct_tx_delay at 0 neighbors | 0.0 - 1.5 (step 0.5) |
| `dtx_slope` | direct_tx_delay increment per neighbor | 0.3 - 0.6 (step 0.3) |
| `rx_base` | rx_delay_base at 0 neighbors | 0.0 - 1.5 (step 0.5) |
| `rx_slope` | rx_delay_base increment per neighbor | 0.3 - 0.6 (step 0.3) |

This gives 4 x 2 x 4 x 2 x 4 x 2 = **512 variants** per density level.

### Method (`tools/optimize_tuning.py`)

The script must recompile the MeshCore fork for each parameter variant:

1. Parse 6 parameter ranges (each accepts `value` or `min:max:step` format)
2. Backup original `DelayTuning.h`
3. **For each parameter combination** (sequential — shared build directory):
   a. Generate `DelayTuning.h` from linear formula
   b. Rebuild the fork orchestrator (`cmake --build build-fork`, ~15-25s)
   c. Run all seeds in parallel (via `-j` flag)
   d. Parse delivery/ack/channel/fate results
4. Restore original `DelayTuning.h` (guaranteed via `try/finally`)
5. Aggregate and rank results

The config is sanitized before each run: any manual `set rxdelay`/`set txdelay`/`set direct.txdelay` commands are stripped (these disable auto-tune), and `set autotune on` is injected for all repeaters.

### CLI

```bash
python3 tools/optimize_tuning.py config.json \
  --tx-base 0.0:1.5:0.5 --tx-slope 0.3:0.6:0.3 \
  --dtx-base 0.0:1.5:0.5 --dtx-slope 0.3:0.6:0.3 \
  --rx-base 0.0:1.5:0.5 --rx-slope 0.3:0.6:0.3 \
  --seeds 6 --build-dir build-fork \
  --meshcore-dir ../MeshCore-stachuman \
  -j 6 -o results.csv
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

Each variant requires a rebuild (~15-25s) plus seed runs. For a 15-minute simulation with 6 seeds and `-j 6`:

| Variants | Seeds | Time per variant | Total |
|---|---|---|---|
| 1 | 6 | ~50s | ~1 min |
| 64 (4x2x4x1x1x1) | 6 | ~50s | ~1 hour |
| 512 (4x2x4x2x4x2) | 6 | ~50s | ~7 hours |

### Output

The script prints a ranked table of all variants with delivery metrics and message fate diagnostics. For the best result, it outputs the full 13-entry C array ready to paste into `DelayTuning.h`:

```
  Top 10 variants (of 512):
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
- **Sequential builds**: Each variant requires a full rebuild (~15-25s). This limits practical sweep size.
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

Optimal delay parameters may differ across network densities. The `delay_optimization/` directory includes three scripts that generate networks with different link densities from the Gdansk region and run full parameter sweeps on each.

### What each script does

Each script runs a 4-step pipeline:

1. **Generate topology** — runs `topology_generator` with the Gdansk region, density-specific link survival and edge caps
2. **Inject test cases** — runs `inject_test.py` to place 4 companions (alice, bob, carol, dave) and generate auto-schedules (70s direct interval, 80s channel interval, 5 direct + 4 channel messages per entry)
3. **Print topology statistics** — runs `topology_stats.py` to summarize the generated network
4. **Run optimization sweep** — runs `optimize_tuning.py` with 512 variants (4x2x4x2x4x2), 6 seeds, 6 parallel jobs

### Density configurations

| Script | Density | `--link-survival` | `--max-edges-per-node` | `--max-good-links` | Typical neighbors |
|--------|---------|-------------------|------------------------|--------------------|--------------------|
| `run_sparse.sh` | Sparse | 0.2 | 6 | 2 | 1-3 |
| `run_medium.sh` | Medium | 0.4 | 12 | 3 | 3-6 |
| `run_dense.sh` | Dense | 0.7 | 16 | 6 | 6-12 |

### Sweep parameters (same for all densities)

| Parameter | Range | Values |
|-----------|-------|--------|
| `tx_base` | 0.0:1.5:0.5 | 0.0, 0.5, 1.0, 1.5 |
| `tx_slope` | 0.3:0.6:0.3 | 0.3, 0.6 |
| `dtx_base` | 0.0:1.5:0.5 | 0.0, 0.5, 1.0, 1.5 |
| `dtx_slope` | 0.3:0.6:0.3 | 0.3, 0.6 |
| `rx_base` | 0.0:1.5:0.5 | 0.0, 0.5, 1.0, 1.5 |
| `rx_slope` | 0.3:0.6:0.3 | 0.3, 0.6 |

### Output files

Each script generates three files in the `delay_optimization/` directory:

| File | Description |
|------|-------------|
| `<density>_topology.json` | Raw topology from `topology_generator` |
| `<density>_test.json` | Complete simulation config with companions and schedules |
| `results_<density>_<timestamp>.csv` | Full sweep results (512 rows) |

### Usage

Scripts are run from the project root (they `cd` automatically):

```bash
# Full sweeps
./delay_optimization/run_sparse.sh
./delay_optimization/run_medium.sh
./delay_optimization/run_dense.sh

# Quick test (override seeds/jobs)
./delay_optimization/run_sparse.sh --seeds 2 -j 2
```

Each script accepts `MESHCORE_DIR` as an environment variable to point to the MeshCore fork (defaults to `../MeshCore-stachuman`).

### Prerequisites

1. MeshCore fork at `../MeshCore-stachuman` (or set `MESHCORE_DIR`)
2. Fork build directory configured: `cmake -S . -B build-fork -DMESHCORE_DIR=$(pwd)/../MeshCore-stachuman`
3. API cache recommended (`/tmp/meshcore_nodes_cache.json`) — first run downloads ~31K nodes
4. Python venv active with `requirements.txt` dependencies

## 6. Running the Pipeline Manually

The density test scripts automate the full pipeline, but each step can be run independently:

```bash
# Step 1: Generate topology from live network data
python3 -m topology_generator \
    --region 53.7,17.3,54.8,19.5 \
    --api-cache /tmp/meshcore_nodes_cache.json \
    --freq-mhz 869.618 --tx-power-dbm 20.0 --antenna-height 5.0 \
    --sf 8 --bw 62500 --cr 4 \
    --max-distance-km 40 --min-snr -10.0 \
    --max-edges-per-node 12 --max-good-links 3 \
    --link-survival 0.4 --clutter-db 6.0 \
    -v -o topology.json

# Step 2: Inject companions and message schedules
python3 tools/inject_test.py topology.json \
    --companions 4 --companion-names alice,bob,carol,dave \
    --min-neighbors 2 \
    --auto-schedule --channel \
    --msg-interval 70 --msg-count 5 \
    --chan-interval 80 --chan-count 4 \
    --duration 900000 \
    -v -o test_config.json

# Step 3: Inspect the generated topology
python3 tools/topology_stats.py test_config.json

# Step 4: Quick test run
build/orchestrator/orchestrator test_config.json > events.ndjson

# Step 5: Auto-tune table sweep (requires MeshCore fork build)
python3 tools/optimize_tuning.py test_config.json \
    --tx-base 0.0:1.5:0.5 --tx-slope 0.3:0.6:0.3 \
    --dtx-base 0.0:1.5:0.5 --dtx-slope 0.3:0.6:0.3 \
    --rx-base 0.0:1.5:0.5 --rx-slope 0.3:0.6:0.3 \
    --seeds 6 --build-dir build-fork \
    --meshcore-dir ../MeshCore-stachuman \
    -j 6 -o results.csv

# Step 6: Visualize a specific run
python3 visualization/visualize.py events.ndjson --config test_config.json
```

### Alternative: static delay sweep

For a simpler sweep that sets identical delays on all repeaters (without recompiling):

```bash
python3 tools/optimize_delays.py test_config.json \
    --rxdelay 0:5:1 --txdelay 0:1:0.2 --direct-txdelay 0:0.5:0.1 \
    --seeds 3 -j 4 -o results_static.csv
```

## 7. Results

All results use Gdansk/Pomerania region topologies generated via `topology_generator` with ITM propagation model. Three network densities are tested (see Section 5d). Each sweep uses `optimize_tuning.py` with 512 variants and 6 seeds per variant. CSV files with full results are stored in this directory.

### 7.1 Sparse network

Script: `run_sparse.sh`. Topology: `--link-survival 0.2 --max-edges-per-node 6 --max-good-links 2`.

CSV: [`results_sparse_<timestamp>.csv`]

*(Results pending — run `bash ./delay_optimization/run_sparse.sh`)*

### 7.2 Medium network

Script: `run_medium.sh`. Topology: `--link-survival 0.4 --max-edges-per-node 12 --max-good-links 3`.

CSV: [`results_medium_<timestamp>.csv`]

*(Results pending — run `bash ./delay_optimization/run_medium.sh`)*

### 7.3 Dense network

Script: `run_dense.sh`. Topology: `--link-survival 0.7 --max-edges-per-node 16 --max-good-links 6`.

CSV: [`results_dense_<timestamp>.csv`]

*(Results pending — run `bash ./delay_optimization/run_dense.sh`)*

### Key findings

*(To be filled after all three sweeps complete.)*
