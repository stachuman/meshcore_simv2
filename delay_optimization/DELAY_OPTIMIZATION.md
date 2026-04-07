# Delay Parameter Optimization — Methodology

## Overview

This document describes how we set up simulations to find optimal delay tuning tables for MeshCore repeater networks. The pipeline: live network API -> ITM propagation model -> simulated topology -> companion + message injection -> automated parameter sweep.

### Scope: connected networks only

Delay tuning controls *when* a repeater retransmits — it trades collision avoidance against forwarding speed. This only helps when messages have a viable multi-hop path to the destination. If the network has disconnected islands, messages between islands simply cannot be delivered, regardless of delay settings. Auto-tune cannot fix a topology problem.

All experiments in this document therefore target **connected topologies** (or topologies where the main connected component contains >95% of nodes and all companions). When generating test topologies from real-world node data, we tune the survival filter and edge caps to produce a single large component rather than fragmented islands. This is a deliberate choice — we are measuring the effect of delay tuning on routing efficiency, not the effect of network fragmentation on reachability.

## 1. Test Topology

### Source data

Topologies are generated from live MeshCore network data using the `topology_generator` module. It fetches node positions from the [MeshCore map API](https://map.meshcore.io), then computes RF links using the **Irregular Terrain Model (ITM / Longley-Rice)** propagation model with SRTM elevation data.

The Gdansk/Pomerania region (bounding box `53.7,17.3,54.8,19.5`) is used for all delay optimization tests. A typical fetch returns ~150-160 repeaters with GPS coordinates.

### Processing pipeline (`topology_generator`)

1. **API fetch** — downloads all nodes in the bounding box from the MeshCore map API. Responses can be cached (`--api-cache`) to avoid repeated downloads.
2. **ITM link computation** — for each pair of nodes within `--max-distance-km` (40 km), computes path loss using the ITM model with terrain profiles from SRTM data, then derives received SNR. Links below `--min-snr` (-10 dB) are dropped.
3. **Clutter attenuation** — adds `--clutter-db` (6 dB) to account for urban/suburban clutter not captured by SRTM.
4. **Link density control** — three parameters shape the final topology:
   - `--link-survival`: SNR-weighted stochastic survival — each link survives with probability `p = survival / (1 + exp(-(snr - snr_mid) / scale))`, controlled by `--survival-snr-mid` (default 10 dB). Lower survival = sparser; lower snr_mid = more weak links preserved.
   - `--max-edges-per-node`: hard cap on total neighbors
   - `--max-good-links`: cap on "good" links (SNR > 0) per node; remaining slots filled with weaker links
5. **Connectivity check** — the generator reports connected components. For delay optimization, we require the main component to contain >95% of nodes. If the topology fragments into islands, the filter parameters must be adjusted (see below).
6. **Config emission** — outputs a simulation config JSON with nodes (name, role, lat/lon) and bidirectional links (SNR, RSSI, loss probability).

### Ensuring connectivity across regions

The survival filter uses an SNR-weighted sigmoid: `p = survival_prob / (1 + exp(-(snr - snr_mid) / scale))`. The default parameters (`snr_mid=10`, `survival=0.4`, `max-good=3`) work well for Gdansk's hilly terrain, where link SNR values span a wide range and many links have SNR < 0 (which don't count against the `max-good` cap).

Different terrain requires different filter settings. In flat regions (e.g. Netherlands), almost all ITM-computed links have high SNR, which causes two problems:
- **Bridge links break**: the few weak inter-region links (SNR ~5 dB) fall on the steep part of the sigmoid and get killed, fragmenting the topology into islands.
- **Good-links cap bites too hard**: with nearly all links being "good" (SNR > 0), a low `max-good` cap (e.g. 3) produces an artificially sparse topology.

The fix is region-specific tuning of three parameters:
- **`--survival-snr-mid`**: lower the sigmoid midpoint (e.g. 5 instead of 10) to give weak bridge links a better survival chance.
- **`--max-good-links`**: raise the cap (e.g. 7 instead of 3) to compensate for the flat SNR distribution.
- **`--link-survival`**: adjust the base survival probability to achieve the target average neighbor count.

The goal is always the same: a connected topology with the target density (e.g. avg ~5 neighbors for medium). The filter parameters are a means to that end, not fixed constants.

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
| `--survival-snr-mid` | 10.0 | Sigmoid midpoint for SNR-weighted survival (lower preserves weak bridge links) |
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

## 5b. Auto-Tune Table Optimization Proposal - PR2125 - https://github.com/meshcore-dev/MeshCore/pull/2125

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

All results use Gdansk/Pomerania region topologies generated via `topology_generator` with ITM propagation model. Three network densities are tested (see Section 5d). Each sweep uses `optimize_tuning.py` with 512 variants and 6 seeds per variant. Full CSV results are stored in this directory.

### 7.1 Sparse network

Script: `run_sparse.sh`. CSV: `results_sparse_20260406_170128.csv`

**Topology**: 156 repeaters, 4 companions, 213 links. Neighbors per repeater: min=1, max=6, mean=2.7, median=2.

| Neighbors | Count |
|-----------|-------|
| 1 | 40 |
| 2 | 41 |
| 3 | 38 |
| 4 | 14 |
| 5 | 8 |
| 6 | 15 |

**Baseline** (default firmware, 6 seeds): delivery=48.7% +/-2.4% (min=46% max=52%), ack=17.3%, chan=61.7%, col/lost=15.9, drp/lost=0.2. 146/300 delivered.

**Sweep summary** (512 variants, 6 seeds each):

| Metric | Min | Max | Mean | Baseline |
|--------|-----|-----|------|----------|
| Delivery % | 38.7 | 52.7 | 45.9 | 48.7 |
| Ack % | 17.3 | 31.0 | 23.9 | 17.3 |
| Channel % | 50.3 | 75.7 | 63.7 | 61.7 |
| Collision/lost | 8.3 | 17.2 | 11.7 | 15.9 |
| Drop/lost | 0.1 | 0.3 | 0.2 | 0.2 |

**Top 10 variants** (sorted by delivery %):

| # | tx_b | tx_s | dtx_b | dtx_s | rx_b | rx_s | mean | std | min | max | del/sent | ack% | chan% | col/lost | drp/lost |
|---|------|------|-------|-------|------|------|------|-----|-----|-----|----------|------|------|----------|----------|
| 1 | 1.5 | 0.6 | 1.5 | 0.6 | 0.0 | 0.3 | 52.7% | 4.5% | 48% | 60% | 158/300 | 26.3% | 60.8% | 13.8 | 0.2 |
| 2 | 1.5 | 0.6 | 1.0 | 0.6 | 0.0 | 0.6 | 52.7% | 5.8% | 44% | 60% | 158/300 | 26.0% | 56.8% | 12.5 | 0.2 |
| 3 | 1.0 | 0.6 | 1.0 | 0.6 | 0.0 | 0.6 | 52.3% | 6.4% | 42% | 60% | 157/300 | 27.7% | 64.7% | 11.8 | 0.2 |
| 4 | 1.0 | 0.6 | 1.5 | 0.6 | 0.5 | 0.6 | 51.7% | 5.0% | 46% | 60% | 155/300 | 27.0% | 53.2% | 11.4 | 0.2 |
| 5 | 1.0 | 0.3 | 1.5 | 0.6 | 0.5 | 0.3 | 51.7% | 8.2% | 44% | 64% | 155/300 | 30.0% | 59.5% | 12.0 | 0.1 |
| 6 | 0.0 | 0.6 | 1.5 | 0.3 | 1.0 | 0.6 | 51.3% | 6.0% | 44% | 62% | 154/300 | 21.3% | 57.3% | 12.8 | 0.2 |
| 7 | 1.5 | 0.6 | 0.0 | 0.6 | 0.5 | 0.3 | 51.0% | 3.9% | 46% | 54% | 153/300 | 28.7% | 54.8% | 11.5 | 0.1 |
| 8 | 1.5 | 0.3 | 1.0 | 0.3 | 0.5 | 0.3 | 51.0% | 3.7% | 44% | 54% | 153/300 | 25.0% | 60.7% | 12.7 | 0.1 |
| 9 | 1.5 | 0.3 | 0.5 | 0.6 | 0.5 | 0.3 | 50.7% | 6.0% | 44% | 58% | 152/300 | 21.0% | 64.8% | 14.5 | 0.1 |
| 10 | 1.0 | 0.6 | 0.5 | 0.6 | 0.0 | 0.6 | 50.7% | 5.2% | 42% | 56% | 152/300 | 26.0% | 61.2% | 12.0 | 0.2 |

**Top parameter patterns** (value counts in top 10):
- `tx_base`: 1.5 (5x), 1.0 (4x) — high base TX delay preferred
- `tx_slope`: 0.6 (7x) — steep slope preferred
- `dtx_base`: 1.5 (4x), 1.0 (3x) — high direct TX base preferred
- `dtx_slope`: 0.6 (8x) — steep slope dominant
- `rx_base`: 0.5 (5x), 0.0 (4x) — low RX delay preferred
- `rx_slope`: 0.3 (5x), 0.6 (5x) — no clear preference

### 7.2 Medium network

Script: `run_medium.sh`. CSV: `results_medium_20260406_192440.csv`

**Topology**: 174 repeaters, 4 companions, 451 links. Neighbors per repeater: min=1, max=12, mean=5.2, median=5.

| Neighbors | Count |
|-----------|-------|
| 1 | 14 |
| 2 | 22 |
| 3 | 20 |
| 4 | 28 |
| 5 | 21 |
| 6 | 18 |
| 7 | 14 |
| 8 | 12 |
| 9 | 7 |
| 10 | 7 |
| 11 | 4 |
| 12 | 7 |

**Baseline** (default firmware, 6 seeds): delivery=44.0% +/-4.6% (min=40% max=52%), ack=15.3%, chan=64.8%, col/lost=74.7, drp/lost=0.5. 132/300 delivered.

**Sweep summary** (512 variants, 6 seeds each):

| Metric | Min | Max | Mean | Baseline |
|--------|-----|-----|------|----------|
| Delivery % | 36.0 | 49.3 | 42.5 | 44.0 |
| Ack % | 14.0 | 27.3 | 21.4 | 15.3 |
| Channel % | 53.2 | 80.2 | 66.7 | 64.8 |
| Collision/lost | 38.3 | 80.8 | 60.9 | 74.7 |
| Drop/lost | 0.2 | 0.7 | 0.5 | 0.5 |

**Top 10 variants** (sorted by delivery %):

| # | tx_b | tx_s | dtx_b | dtx_s | rx_b | rx_s | mean | std | min | max | del/sent | ack% | chan% | col/lost | drp/lost |
|---|------|------|-------|-------|------|------|------|-----|-----|-----|----------|------|------|----------|----------|
| 1 | 1.5 | 0.6 | 1.0 | 0.6 | 0.5 | 0.3 | 49.3% | 4.8% | 42% | 56% | 148/300 | 24.7% | 64.2% | 63.8 | 0.4 |
| 2 | 1.0 | 0.6 | 0.0 | 0.3 | 1.5 | 0.3 | 49.0% | 1.1% | 48% | 50% | 147/300 | 23.0% | 64.3% | 59.1 | 0.5 |
| 3 | 1.0 | 0.6 | 1.5 | 0.6 | 1.0 | 0.6 | 48.3% | 4.5% | 40% | 52% | 145/300 | 21.0% | 62.5% | 64.4 | 0.4 |
| 4 | 1.5 | 0.6 | 1.5 | 0.6 | 0.5 | 0.3 | 48.0% | 3.3% | 44% | 54% | 144/300 | 24.7% | 65.0% | 68.2 | 0.5 |
| 5 | 0.5 | 0.6 | 0.0 | 0.3 | 1.0 | 0.6 | 48.0% | 5.1% | 40% | 54% | 144/300 | 22.0% | 70.0% | 74.9 | 0.7 |
| 6 | 1.5 | 0.6 | 0.0 | 0.6 | 0.5 | 0.3 | 47.7% | 5.1% | 44% | 56% | 143/300 | 24.3% | 59.0% | 65.5 | 0.5 |
| 7 | 0.5 | 0.6 | 0.5 | 0.3 | 0.5 | 0.6 | 47.3% | 5.5% | 40% | 56% | 142/300 | 22.3% | 60.3% | 64.5 | 0.5 |
| 8 | 0.5 | 0.6 | 0.0 | 0.3 | 0.5 | 0.3 | 47.3% | 6.8% | 38% | 56% | 142/300 | 22.0% | 67.5% | 65.9 | 0.4 |
| 9 | 0.5 | 0.3 | 1.5 | 0.3 | 0.0 | 0.3 | 47.3% | 9.8% | 38% | 66% | 142/300 | 22.3% | 64.3% | 73.2 | 0.7 |
| 10 | 1.5 | 0.6 | 1.0 | 0.6 | 0.0 | 0.3 | 47.0% | 2.8% | 44% | 52% | 141/300 | 23.7% | 61.5% | 71.4 | 0.6 |

**Top parameter patterns** (value counts in top 10):
- `tx_base`: 1.5 (4x), 0.5 (4x), 1.0 (2x) — bimodal, no single clear winner
- `tx_slope`: 0.6 (9x) — steep slope strongly preferred
- `dtx_base`: 0.0 (4x), 1.5 (3x) — bimodal
- `dtx_slope`: mixed, 0.6 (5x), 0.3 (5x) — no preference
- `rx_base`: 0.5 (5x) — moderate RX delay preferred
- `rx_slope`: 0.3 (7x) — lower RX slope preferred

### 7.3 Dense network

Script: `run_dense.sh`. CSV: `results_dense_20260406_192446.csv`

**Topology**: 190 repeaters, 4 companions, 762 links. Neighbors per repeater: min=1, max=16, mean=8.0, median=7.

| Neighbors | Count |
|-----------|-------|
| 1 | 8 |
| 2 | 12 |
| 3 | 16 |
| 4 | 15 |
| 5 | 6 |
| 6 | 22 |
| 7 | 20 |
| 8 | 14 |
| 9 | 15 |
| 10 | 10 |
| 11 | 10 |
| 12 | 4 |
| 13 | 8 |
| 14 | 4 |
| 15 | 3 |
| 16 | 23 |

**Baseline** (default firmware, 6 seeds): delivery=45.0% +/-2.8% (min=42% max=48%), ack=9.7%, chan=58.8%, col/lost=136.2, drp/lost=2.5. 135/300 delivered.

**Sweep summary** (512 variants, 6 seeds each):

| Metric | Min | Max | Mean | Baseline |
|--------|-----|-----|------|----------|
| Delivery % | 37.7 | 55.7 | 46.5 | 45.0 |
| Ack % | 6.0 | 21.7 | 15.1 | 9.7 |
| Channel % | 45.2 | 69.5 | 56.3 | 58.8 |
| Collision/lost | 100.5 | 189.8 | 149.8 | 136.2 |
| Drop/lost | 1.7 | 3.8 | 2.7 | 2.5 |

**Top 10 variants** (sorted by delivery %):

| # | tx_b | tx_s | dtx_b | dtx_s | rx_b | rx_s | mean | std | min | max | del/sent | ack% | chan% | col/lost | drp/lost |
|---|------|------|-------|-------|------|------|------|-----|-----|-----|----------|------|------|----------|----------|
| 1 | 0.0 | 0.3 | 0.0 | 0.3 | 0.0 | 0.6 | 55.7% | 3.2% | 50% | 58% | 167/300 | 10.7% | 54.7% | 158.1 | 3.2 |
| 2 | 0.0 | 0.3 | 1.0 | 0.3 | 1.0 | 0.6 | 53.7% | 2.9% | 48% | 56% | 161/300 | 8.7% | 57.8% | 134.4 | 2.6 |
| 3 | 0.0 | 0.3 | 1.0 | 0.6 | 1.0 | 0.6 | 53.3% | 3.0% | 48% | 56% | 160/300 | 9.0% | 53.0% | 155.9 | 3.1 |
| 4 | 0.0 | 0.3 | 1.5 | 0.3 | 1.0 | 0.6 | 53.0% | 2.4% | 50% | 56% | 159/300 | 9.0% | 56.7% | 155.0 | 3.0 |
| 5 | 0.0 | 0.3 | 0.0 | 0.6 | 1.0 | 0.6 | 53.0% | 6.0% | 42% | 58% | 159/300 | 10.0% | 56.2% | 138.3 | 2.6 |
| 6 | 0.0 | 0.3 | 0.0 | 0.3 | 0.5 | 0.6 | 53.0% | 4.9% | 48% | 62% | 159/300 | 8.0% | 52.3% | 176.3 | 3.0 |
| 7 | 0.5 | 0.3 | 0.5 | 0.6 | 1.5 | 0.6 | 52.7% | 3.9% | 46% | 58% | 158/300 | 13.3% | 54.8% | 140.8 | 2.7 |
| 8 | 0.5 | 0.3 | 0.0 | 0.6 | 1.5 | 0.6 | 52.7% | 3.3% | 48% | 56% | 158/300 | 14.7% | 51.3% | 149.8 | 2.8 |
| 9 | 0.0 | 0.6 | 0.0 | 0.6 | 1.0 | 0.6 | 52.7% | 5.8% | 46% | 62% | 158/300 | 19.3% | 49.3% | 157.4 | 2.8 |
| 10 | 0.0 | 0.3 | 0.5 | 0.3 | 1.0 | 0.6 | 52.7% | 5.9% | 42% | 58% | 158/300 | 7.0% | 55.7% | 152.7 | 2.9 |

**Top parameter patterns** (value counts in top 10):
- `tx_base`: 0.0 (8x) — low base TX delay strongly preferred
- `tx_slope`: 0.3 (9x) — low slope strongly preferred
- `dtx_base`: 0.0 (5x), 1.0 (2x) — low direct TX base preferred
- `dtx_slope`: mixed, 0.3 (5x), 0.6 (5x) — no preference
- `rx_base`: 1.0 (6x), 1.5 (2x) — high RX delay preferred
- `rx_slope`: 0.6 (10x) — steep RX slope unanimously preferred

### 7.4 Cross-density comparison

#### Baseline vs best optimized

| Density | Baseline delivery | Best optimized | Delta | Baseline ack | Best ack | Baseline chan | Best chan |
|---------|-------------------|----------------|-------|--------------|----------|---------------|----------|
| Sparse | 48.7% | 52.7% | **+4.0 pp** | 17.3% | 26.3% | 61.7% | 60.8% |
| Medium | 44.0% | 49.3% | **+5.3 pp** | 15.3% | 24.7% | 64.8% | 64.2% |
| Dense | 45.0% | 55.7% | **+10.7 pp** | 9.7% | 10.7% | 58.8% | 54.7% |

#### Overall sweep performance

| Density | Repeaters | Links | Mean nbrs | Best delivery | Worst delivery | Mean delivery | Baseline | Mean col/lost | Mean drp/lost |
|---------|-----------|-------|-----------|---------------|----------------|---------------|----------|---------------|---------------|
| Sparse | 156 | 213 | 2.7 | 52.7% | 38.7% | 45.9% | 48.7% | 11.7 | 0.2 |
| Medium | 174 | 451 | 5.2 | 49.3% | 36.0% | 42.5% | 44.0% | 60.9 | 0.5 |
| Dense | 190 | 762 | 8.0 | 55.7% | 37.7% | 46.5% | 45.0% | 149.8 | 2.7 |

Note: the baseline (default firmware) falls near the sweep mean in all cases — the default DelayTuning.h is a reasonable middle-ground, but leaves room for improvement, especially in dense networks.

#### Optimal parameter direction by density

| Parameter | Sparse winner | Dense winner | Interpretation |
|-----------|---------------|-------------|----------------|
| `tx_base` | High (1.0-1.5) | Low (0.0) | Sparse needs more TX backoff; dense benefits from fast flooding |
| `tx_slope` | High (0.6) | Low (0.3) | Sparse: scale up steeply; dense: already high neighbors, gentle slope |
| `rx_base` | Low (0.0-0.5) | High (1.0-1.5) | Dense needs longer RX collection window to pick best route |
| `rx_slope` | Mixed | High (0.6) | Dense: RX delay should scale steeply with neighbors |

### Key findings

1. **Tuning helps, especially in dense networks**: The best optimized variant improves delivery by +4.0 pp (sparse), +5.3 pp (medium), and +10.7 pp (dense) over the default firmware. Dense networks benefit most because the default table doesn't ramp delays aggressively enough for high neighbor counts.

2. **Ack rates improve significantly in sparse/medium**: The best variants boost ack rates from 17.3% to 26.3% (sparse) and 15.3% to 24.7% (medium) — a ~50% relative improvement. Dense ack rates barely change (9.7% to 10.7%), suggesting round-trip reliability in dense networks is fundamentally limited by collision probability.

3. **Channel delivery is stable**: Optimized variants show similar channel % to the baseline across all densities. The tuning primarily improves directed message delivery without degrading broadcast reach.

4. **Collision dominance**: Across all densities, message losses are overwhelmingly caused by collisions, not link drops. The `drp/lost` values are negligible (0.1-3.2) compared to `col/lost` (8-176). This means delay tuning is the right lever — link quality is not the bottleneck.

5. **Collision severity scales with density**: Mean collisions per lost message: sparse=11.7, medium=60.9, dense=149.8. Dense networks see 13x more collisions than sparse, reflecting exponentially more interference as neighbors increase.

6. **TX and RX delays serve opposite roles across densities**:
   - **Sparse**: high TX delays (base=1.0-1.5, slope=0.6) spread retransmissions over time, reducing the few collisions that do occur. Low RX delays (0.0-0.5) avoid unnecessary waiting on sparse paths.
   - **Dense**: low TX delays (base=0.0, slope=0.3) get packets moving quickly through the many available paths. High RX delays (base=1.0, slope=0.6) let nodes collect packets from multiple paths before choosing the best one.

7. **Medium density is hardest**: Medium networks show the lowest peak delivery (49.3%) and mean delivery (42.5%), worse than both sparse and dense. This suggests medium density hits a "worst of both worlds" — enough neighbors for serious collisions but not enough for reliable path diversity.

8. **Default firmware is a reasonable middle-ground**: The baseline falls near the sweep mean in all three densities, suggesting the current DelayTuning.h is not badly calibrated — but it's a compromise that doesn't excel at any specific density.

9. **Parameter sensitivity is moderate**: The spread between best and worst variants is 14 pp (sparse), 13.3 pp (medium), and 18 pp (dense). Delays matter, but don't transform a bad topology into a good one — the fundamental limit is the network structure itself.

## 8. Validation

### Motivation

The parameter sweep (Section 7) used short, light tests: 15-minute simulations with 4 companions generating 50 direct messages per seed. These are fast enough for a 512-variant grid search but don't prove the winner holds up under realistic conditions. Overfitting to the test scenario is a real risk — a parameter set could win by exploiting specific timing patterns, message counts, or companion placements that won't generalize.

The validation test addresses this by running the best sweep winner against the default firmware under much heavier, more diverse conditions — and crucially, on a **different region** (Groningen+Friesland, Netherlands) than the one used for the sweep (Gdansk/Pomerania, Poland). If the optimized parameters only work on the topology they were tuned on, they're useless. Cross-region validation is the strongest test of generalizability.

Both regions use identical radio settings (869.618 MHz, SF8, BW 62500, CR 4/8 — the EU-wide MeshCore standard), so the only differences are network structure and terrain.

### What changes vs the sweep tests

| Feature                | Sweep tests              | Validation test                           |
| ---------------------- | ------------------------ | ----------------------------------------- |
| **Region**             | Gdansk/Pomerania, Poland | **Groningen+Friesland, Netherlands**      |
| Duration               | 15 min                   | **1 hour**                                |
| Companions             | 4                        | **16**                                    |
| Direct messages / seed | 50                       | **496**                                   |
| Traffic patterns       | 3 (structured)           | **4 (+ random pairs)**                    |
| Mean message interval  | 70s                      | **180s**                                  |
| Correlated fading      | off                      | **snr_coherence_ms=30000**                |
| Seeds                  | 6                        | 6 (same)                                  |

The key additions:

- **Different region** — the sweep optimized on Gdansk/Pomerania (174 repeaters, flat coastal terrain). Validation uses Groningen+Friesland, Netherlands (~200 repeaters, flat farmland/coast/islands). Same radio settings (EU standard), completely different network structure and terrain.
- **16 companions** create a much busier network with more routing diversity and contention. The farthest-point placement ensures geographic spread across the topology.
- **Random pairs** add a 4th traffic pattern alongside 1-to-1 (round-robin), 1-to-many, and many-to-1. This generates 16 randomly selected sender-receiver pairs (`--random-pairs 16`), breaking the structural regularity of the other patterns.
- **Correlated fading** (Ornstein-Uhlenbeck process with 30s coherence) replaces i.i.d. SNR jitter. Links fade slowly and correlate over time, creating realistic "good periods" and "bad periods" that stress routing adaptation.
- **1-hour duration** with 180s mean inter-arrival gives messages time to propagate through congested paths and tests steady-state behavior, not just initial burst performance.

### Topology parameter tuning for NL terrain

The survival filter uses an SNR-weighted sigmoid: `p = survival_prob / (1 + exp(-(snr - snr_mid) / scale))`. The default `snr_mid=10` was calibrated for Gdansk's hilly/coastal terrain where many links have moderate SNR. In the flat Netherlands, most links are high-SNR, so:

- **`snr_mid=5`** (lowered from 10): preserves weaker bridge links between sub-regions that would otherwise be killed by the sigmoid. Without this, the topology fragments into disconnected islands.
- **`max-good=7`** (raised from 3): in flat terrain, nearly all links are "good" (SNR > 0). With `max-good=3`, the cap bites too hard and produces avg ~2.7 neighbors. Raising to 7 produces avg ~5.1, matching Gdansk medium's 5.0.
- **`survival=0.5`** (raised from 0.4): combined with `snr_mid=5`, this produces a well-connected main component (217/222 nodes, 97.7%).

### Topology comparison

The validation topology (Groningen+Friesland) is structurally similar to the sweep topology (Gdansk) — both are medium-density networks with closely matched average neighbor counts — but differ in size, terrain, and distribution shape.

| Metric | Gdansk (sweep) | Groningen+Friesland (validation) |
|--------|---------------|----------------------------------|
| Region | Pomerania, Poland | Northern Netherlands |
| Repeaters | 174 | 222 |
| Companions | 4 | 16 |
| Links | 451 | 643 |
| Avg neighbors | 5.0 | 5.1 |
| Median neighbors | 5 | 5 |
| Min neighbors | 1 (16 nodes) | 1 (14 nodes) |
| Max neighbors | 12 | 7 (capped by max-good) |
| Components | 1 | 3 (main: 217, residual: 3+2) |

Neighbor count distributions:

```
Gdansk (174 repeaters):
   1: ████████████████ 16                 7: ███████████████ 15
   2: ███████████████████████ 23          8: ██████████ 10
   3: ██████████████████ 18               9: ████████ 8
   4: ██████████████████████████████ 30  10: ██████ 6
   5: ██████████████████████ 22          11: ███████ 7
   6: ███████████████ 15                 12+: █████ 4

Groningen+Friesland (222 repeaters):
   1: ██████ 14
   2: █████████ 21
   3: ███████ 18
   4: █████████████ 31
   5: ███████████ 27
   6: ████████ 20
   7: ████████████████████████████████████████ 91
```

Key structural differences:
- **Larger network**: 222 vs 174 repeaters — tests whether parameters scale to bigger networks.
- **Flat max-good cap**: NL peaks at 7 neighbors (91 nodes, 41%) due to the `max-good=7` cap, while Gdansk spreads up to 12. This creates a denser cluster of nodes at the cap, testing the tuning table's behavior at mid-range entries.
- **Near-connected**: the main component contains 97.7% of nodes. Two tiny residual fragments (3+2 nodes) are geographically isolated; companions are placed on the main component only via farthest-point sampling.
- **Matched density**: avg 5.1 vs 5.0 neighbors — close enough for meaningful comparison despite different terrain and filter parameters.

### Traffic volume per seed

| Pattern | Pairs | Msgs/pair | Total |
|---------|-------|-----------|-------|
| 1-to-1 (round-robin) | 16 | 8 | 128 |
| 1-to-many | 15 | 8 | 120 |
| many-to-1 | 15 | 8 | 120 |
| random pairs | 16 | 8 | 128 |
| **Direct total** | | | **496** |
| Channel | 16 senders | 6 | 96 |
| **Grand total** | | | **592** |

Across 6 seeds: 2,976 direct messages and 576 channel messages — roughly 10x the sweep volume.

### Method

Script: `delay_optimization/run_medium_validation.sh`. Six steps:

1. **Generate topology** — Groningen+Friesland region (`52.8,5.2,53.5,7.0`), with NL-tuned density parameters (link-survival=0.5, survival-snr-mid=5, max-edges=12, max-good=7) that produce avg ~5.1 neighbors — matching the Gdansk medium sweep topology (see "Topology parameter tuning" above).

2. **Inject test cases** — 16 companions via farthest-point sampling (min-neighbors=2), auto-schedule with all 4 patterns, channel broadcasts, 1-hour duration. Post-processes the config to add `snr_coherence_ms=30000` for O-U correlated fading.

3. **Print topology statistics** — network summary for reference.

4. **Run baseline** — 6 seeds with the stock orchestrator (`build/orchestrator/orchestrator`). Per-seed and aggregate results printed.

5. **Run optimized** — writes the best medium DelayTuning.h (tx_b=1.5, tx_s=0.6, dtx_b=1.0, dtx_s=0.6, rx_b=0.5, rx_s=0.3) to the fork, rebuilds, injects `set autotune on` commands for all repeaters, then runs 6 seeds with the fork orchestrator.

6. **Print comparison table** — baseline vs optimized side-by-side with deltas for delivery, ack, channel, collision/lost, and drop/lost.

### What a successful validation looks like

The optimized parameters pass validation if:

- **Delivery % improves** over baseline on the unseen Netherlands topology. The improvement may be smaller than the sweep's +5.3 pp (different region, harder conditions), but should remain positive. A negative delta would indicate overfitting to the Gdansk topology.
- **No metric regresses badly** — ack % and channel % should not drop significantly vs baseline.
- **Lower variance** — the optimized variant should show similar or lower standard deviation across seeds, indicating stable behavior under different random conditions.
- **Improvement survives correlated fading** — the O-U process tests whether the parameters work when link quality fluctuates slowly, not just under static conditions.
- **Cross-region generalization** — this is the primary question. If parameters tuned on Gdansk also improve delivery on a completely different Dutch network, they likely capture a genuine property of the MeshCore routing algorithm rather than an artifact of one topology.

### Usage

```bash
# Full validation (6 seeds, ~2-3 hours)
./delay_optimization/run_medium_validation.sh

# Quick test (fewer seeds)
./delay_optimization/run_medium_validation.sh --seeds 2
```

### Results

*(pending — validation run in progress)*
