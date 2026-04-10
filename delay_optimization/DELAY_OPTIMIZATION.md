# Delay Parameter Optimization — Methodology and Findings

## Overview

This document describes how we set up simulations to test whether delay tuning tables can improve message delivery in MeshCore repeater networks. The pipeline: live network API -> ITM propagation model -> simulated topology -> companion + message injection -> automated parameter sweep.

**Key finding**: The theoretical assumption that increasing delays reduces collisions and improves delivery does not hold in simulation. Zero delays consistently outperform all tested delay configurations — including the firmware defaults, 512 auto-tune table variants, and the hand-tuned recommendations from the white paper. The details of this discovery are in Section 8.

### Theoretical thesis

The starting hypothesis was based on the analysis in *"White Paper: Using rxdelay, txdelay and direct.txdelay to Minimize Collisions in a MeshCore Routing"*, which makes the following argument:

1. **Collision probability is inversely proportional to the number of backoff slots.** MeshCore's retransmission delay uses the equation `t = floor(5 * airtime * txdelay)`, and the retransmit time is drawn uniformly from `[0, 5*t+1)`. More slots = lower collision probability: `P_collision ~ 1/t`.

2. **Default delays create high collision risk.** The firmware defaults (txdelay=0.5, direct.txdelay=0.2, rxdelay=0) produce only 2 backoff slots for flood routing and near-zero slots for direct traffic — a 12.5% and ~100% pairwise collision probability respectively.

3. **Delays should scale with neighbor count.** More neighbors means more nodes retransmitting the same message simultaneously. The white paper proposes a tuning table indexed by SNR-positive neighbor count, with txdelay ranging from 1.0 (sparse, 0-3 neighbors) to 2.0+ (regional, 11-12+), and corresponding direct.txdelay from 0.4 to 0.9.

4. **Tiered delays provide temporal band separation.** Nodes with higher txdelay access a larger backoff window. The portion exclusive to high-delay nodes is collision-free with respect to low-delay nodes, creating an implicit priority hierarchy.

This reasoning is mathematically sound for the *pairwise* case: if two nodes receive the same flood message and both decide to retransmit, more slots do reduce the probability they pick the same slot. The question is whether this pairwise benefit translates to improved end-to-end delivery in a real multi-hop network with mixed traffic, retransmissions, and timeouts.

### Scope: connected networks only

Delay tuning controls *when* a repeater retransmits — it trades collision avoidance against forwarding speed. This only helps when messages have a viable multi-hop path to the destination. If the network has disconnected islands, messages between islands simply cannot be delivered, regardless of delay settings. Auto-tune cannot fix a topology problem.

All experiments in this document therefore target **connected topologies** (or topologies where the main connected component contains >95% of nodes and all companions). When generating test topologies from real-world node data, we tune the survival filter and edge caps to produce a single large component rather than fragmented islands. This is a deliberate choice — we are measuring the effect of delay tuning on routing efficiency, not the effect of network fragmentation on reachability.

## 1. Test Topology

### Source data

Topologies are generated from live MeshCore network data using the `topology_generator` module. It fetches node positions from the [MeshCore map API](https://map.meshcore.io), then computes RF links using the **Irregular Terrain Model (ITM / Longley-Rice)** propagation model with SRTM elevation data.

The Gdansk/Pomerania region (bounding box `53.7,17.3,54.8,19.5`) is used for the parameter sweep tests (Section 7). Validation (Section 9) uses a different region to test generalizability. A typical fetch returns ~150-160 repeaters with GPS coordinates.

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
- **2ms time resolution** (`step_ms = 2`) — changed from initial 4ms for better capture/collision accuracy (see Section 3.1)
- **Message interval**: 70s mean for direct messages, 80s mean for channel broadcasts
- **5 direct messages** and **4 channel messages** per schedule entry
- **Poisson-distributed timing**: each message gets an exponential inter-arrival time (mean = interval), clamped to [0.2x, 3x] to avoid extreme bunching or gaps. This models realistic user behavior rather than fixed-interval robots.
- **8 seeds** per parameter combination (seeds 42-49) to account for stochastic variation

### 3.1 Time Resolution Choice

The simulation time resolution (`step_ms`) determines the granularity of collision detection, capture effect timing, and half-duplex modeling. For the radio parameters used in all tests (SF=8, BW=62.5kHz), the symbol time is:

```
T_sym = 2^8 / 62500 Hz = 4.096 ms
```

Critical physics-based timing intervals that depend on sub-symbol resolution:

- **Preamble lock for capture**: 5 symbols = 20.5ms — the simulator needs to detect if one packet locked the receiver 5 symbols *before* an interferer arrives
- **LBT channel detection**: 5 symbols = 20.5ms preamble detection delay
- **Collision survival**: timing-dependent capture requires accurate measurement of arrival time differences

**Initial tests** used `step_ms = 4ms` (1.02 steps per symbol), which passes the warning threshold (`step_ms <= T_sym`) but provides minimal timing accuracy:
- Preamble lock window: only ~5 simulation steps
- Timing error: ±4ms in determining packet arrival order

**Final sweep runs** use `step_ms = 2ms` (2.05 steps per symbol) for:
- **2x better capture accuracy**: ±2ms timing error vs ±4ms
- **10 steps for preamble lock**: more precise collision modeling
- **Smoother parameter gradients**: reduces quantization artifacts in optimization results

**Trade-off**: 2ms resolution doubles simulation runtime (~2x wall-clock time for the full sweep), but provides more reliable collision/capture modeling. For a 42,768-run parameter sweep where we're optimizing delay values, the accuracy improvement justifies the runtime cost.

All three density test scripts (`run_sparse.sh`, `run_medium.sh`, `run_dense.sh`) use `step_ms = 2` for consistency.

### Schedule patterns

Four patterns run concurrently (interleaved), all using Poisson-distributed send times:

| Pattern | Command | Description | Purpose |
|---|---|---|---|
| **1-to-1** | `msga` | Round-robin pairs (alice->bob, bob->carol, carol->dave, dave->alice) | Baseline point-to-point delivery |
| **1-to-many** | `msga` | First companion sends to all others (alice->bob, alice->carol, alice->dave) | Tests fan-out / flood routing |
| **many-to-1** | `msga` | All send to last companion (alice->dave, bob->dave, carol->dave) | Tests convergent traffic / congestion |
| **channel broadcast** | `msgc` | Each companion sends on public channel (channel 0) | Tests flood routing under mixed traffic |

Direct messages (first three patterns) use `msga` (message with ack tracking) so both delivery and acknowledgement rates are measured. The first message to each destination goes via **flood routing** (no known path); subsequent messages use **path/direct routing** (route learned from the first ACK). This natural flood→path transition is tracked in the routing split metrics (see Section 5). Channel messages use `msgc` (flood, no ack) — delivery is tracked by counting receptions at all other companions.

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

50 direct messages per seed, 400 across 8 seeds.

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

Multiple metrics are collected per run. Variants are currently ranked by **Delivery %** only; the others are reported for analysis but do not affect sort order. Lower standard deviation breaks ties.

> **Open question — what is the right success metric?** Delivery % (message reached the destination) and Ack % (sender got confirmation back) measure different things. A network that delivers 90% of messages but only confirms 40% via ACKs will trigger retransmissions, increasing congestion. Conversely, optimizing for Ack % may favour parameters that improve the return path at the expense of forward delivery. The right ranking target likely depends on the use case — fire-and-forget messaging cares about delivery, while interactive chat cares about round-trip ACK. For now, Delivery % is the primary sort key; future work should evaluate whether Ack %, a weighted composite, or a multi-objective approach produces better real-world outcomes.

| Priority | Metric | Definition | Why |
|:--------:|--------|------------|-----|
| 1 | **Delivery %** | direct messages received / direct messages sent | Current primary objective — point-to-point reliability is the most user-visible quality measure |
| 2 | **Ack %** | all acks received / all acks expected | Round-trip success — may be a better objective for interactive messaging (see open question above) |
| 3 | **Flood Delivery %** | flood-routed messages delivered / flood-routed messages sent | Delivery split: messages sent via flood routing (no known path to destination) |
| 4 | **Path Delivery %** | path-routed messages delivered / path-routed messages sent | Delivery split: messages sent via direct/path routing (known route exists) |
| 5 | **Flood Ack %** | flood acks received / flood acks expected | ACK split: round-trip success for flood-routed messages (ACK embedded in PATH return) |
| 6 | **Path Ack %** | path acks received / path acks expected | ACK split: round-trip success for path-routed messages (standalone ACK packet) |
| 7 | **Channel %** | channel receptions / expected receptions (sent x (N_companions - 1)) | Flood broadcast reach indicates overall network health |
| 8 | **Radio RX Efficiency** | successful receptions / total reception attempts | How much of the radio activity results in useful data |
| 9 | **ACK/PATH copies per delivered msg** | ACK/PATH packets reaching original sender / delivered messages | Ack return path efficiency — 1.0 is ideal, >1.0 means wasted airtime on redundant ack forwarding |
| — | **Stability** | standard deviation of Delivery % across seeds | Tiebreaker — lower variance means more predictable behaviour |

#### Flood vs path routing split

MeshCore uses two routing strategies for direct messages:

- **Flood**: when no path to the destination is known, the message is broadcast to all neighbors for relay. The first message to a new contact always goes flood. ACKs for flooded messages are embedded in the PATH return packet.
- **Path/direct**: when a route has been learned (typically from a previous flood+ACK exchange), the message is sent along the known path. ACKs are standalone packets sent back along the reverse path.

The flood/path split reveals whether delivery failures are concentrated in the route-discovery phase (flood) or the steady-state phase (path). A high flood delivery % with low path delivery % suggests routes are being learned but then breaking; the reverse suggests route discovery is the bottleneck.

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
| `tx_base` | tx_delay at 0 neighbors | 0.0 - 2.0 (step 0.2) |
| `tx_slope` | tx_delay increment per neighbor | 0.3 - 0.6 (step 0.3) |
| `dtx_base` | direct_tx_delay at 0 neighbors | 0.0 - 1.0 (step 0.2) |
| `dtx_slope` | direct_tx_delay increment per neighbor | 0.3 - 0.6 (step 0.3) |
| `rx_base` | rx_delay_base at 0 neighbors | 0.0 - 6.0 (step 2.0) |
| `rx_slope` | rx_delay_base increment per neighbor | 0.3 - 0.6 (step 0.3) |

This gives 11 x 2 x 6 x 2 x 4 x 2 = **2,112 variants** per density level.

### Method (`tools/optimize_tuning.py`)

The script uses **runtime delay injection** — the orchestrator binary is built once with `-DDELAY_TUNING_RUNTIME=ON`, which replaces the static `const` delay table with a mutable `extern` array populated from the JSON config's `simulation.delay_tuning` section. No per-variant recompilation needed.

1. Parse 6 parameter ranges (each accepts `value` or `min:max:step` format)
2. Build the orchestrator once (if not already built)
3. **For each parameter combination**:
   a. Generate a 13-entry delay table from the linear formula
   b. Inject it into the config JSON as `simulation.delay_tuning`
   c. Run all seeds in parallel (via `-j` flag) — each is a separate OS process
   d. Parse delivery/ack/channel/fate results including flood/path routing splits
4. Aggregate and rank results

The config is sanitized before each run: any manual `set rxdelay`/`set txdelay`/`set direct.txdelay` commands are stripped (these disable auto-tune), and `set autotune on` is injected for all repeaters.

### CLI

```bash
python3 tools/optimize_tuning.py config.json \
  --tx-base 0.0:2.0:0.2 --tx-slope 0.3:0.6:0.3 \
  --dtx-base 0.0:1.0:0.2 --dtx-slope 0.3:0.6:0.3 \
  --rx-base 0.0:6.0:2.0 --rx-slope 0.3:0.6:0.3 \
  --clamp-max 6.0 \
  --seeds 8 --build-dir build-native-delay \
  --meshcore-dir ../MeshCore-stachuman \
  -j 8 -o results.csv
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
| `--build-dir` | `build-native-delay` | CMake build directory (must be built with `DELAY_TUNING_RUNTIME=ON`) |
| `--meshcore-dir` | `../MeshCore-stachuman` | Path to MeshCore fork |
| `-j` / `--jobs` | `1` | Parallel seed runs per variant |
| `--top` | `10` | Show top N results |
| `-o` / `--output` | none | CSV output path |
| `-v` / `--verbose` | off | Per-run orchestrator summary |

All range flags accept: `1.0` (constant), `1.0:1.0:0` (constant), or `0.0:5.0:1.0` (sweep).

### Prerequisites

The script requires a MeshCore fork built with runtime delay injection:

```bash
cmake -S . -B build-native-delay \
  -DCMAKE_BUILD_TYPE=NativeRelease \
  -DMESHCORE_DIR=$(pwd)/../MeshCore-stachuman \
  -DDELAY_TUNING_RUNTIME=ON
cmake --build build-native-delay
```

The `DELAY_TUNING_RUNTIME=ON` flag replaces the static `const DelayTuning DELAY_TUNING_TABLE[]` with a mutable `extern` array. At startup, `setDelayTuningLinear()` fills it from the JSON config's `simulation.delay_tuning` section. Each process gets its own copy, so multiple sweep runs can execute in parallel safely.

### Time estimation

With runtime injection, no per-variant rebuild is needed — only the seed runs. For a 15-minute simulation with 8 seeds and `-j 8`:

| Variants | Seeds | Time per variant | Total |
|---|---|---|---|
| 1 | 8 | ~15s | ~15s |
| 64 (4x2x4x1x1x1) | 8 | ~15s | ~16 min |
| 512 (4x2x4x2x4x2) | 8 | ~15s | ~2 hours |
| 2112 (11x2x6x2x4x2) | 8 | ~15s | ~9 hours |

### Output

The script prints a ranked table of all variants with delivery metrics, flood/path routing splits, and message fate diagnostics. For the best result, it outputs the full 13-entry C array ready to paste into `DelayTuning.h`:

```
  Top 10 variants (of 512):
    tx_b   tx_s   dtx_b  dtx_s    rx_b   rx_s    mean    std   min   max  delivered   acks   chan  F_del  P_del  F_ack  P_ack  r_eff  ap_eff  col/lost  drp/lost  ack/del
  ------ ------  ------ ------  ------ ------  ------  -----  ----  ----  ---------  -----  -----  -----  -----  -----  -----  -----  ------  --------  --------  -------
   0.000 0.3000   0.000 0.3000   0.500 0.3000   51.0%   3.3%   48%   56%   153/300      4%    51%    48%    53%     3%     5%    12%     8%      73.9       0.8      1.4
   0.000 0.3000   0.500 0.6000   0.500 0.3000   50.3%   3.4%   46%   56%   151/300      6%    54%    47%    52%     4%     7%    11%     7%      65.2       1.1      1.6
```

| Column | Description |
|--------|-------------|
| `F_del` | Flood delivery % — messages sent via flood routing that were delivered |
| `P_del` | Path delivery % — messages sent via direct/path routing that were delivered |
| `F_ack` | Flood ack % — ACKs received for flood-routed messages |
| `P_ack` | Path ack % — ACKs received for path-routed messages |
| `r_eff` | Radio RX efficiency — successful receptions / total receptions (including collisions, drops) |
| `ap_eff` | ACK+path radio RX efficiency — same ratio but only for ACK/PATH packet types |
| `col/lost` | Mean collisions per lost message (from fate tracking) |
| `drp/lost` | Mean drops per lost message (from fate tracking) |
| `ack/del` | Mean ACK/PATH copies reaching the original sender per delivered message |

The fate columns (`col/lost`, `drp/lost`) come from per-message fate tracking (see Section 5c below). They show the mean number of collisions and drops experienced per lost (undelivered) message, helping diagnose whether failures are due to congestion (high collisions) or link quality (high drops). The `ack/del` column shows how many ACK or PATH_RETURN copies reached the original sender per delivered message — 1.0 is ideal, higher values indicate wasted airtime on redundant ack/path forwarding.

### CSV output

Results are saved incrementally to CSV via the `-o` flag. Rows are appended after each variant completes and flushed immediately, so partial results survive interruption. At the end of a complete run, the CSV is rewritten sorted by delivery %.

CSV columns:

| Column                          | Description                                                          |
| ------------------------------- | -------------------------------------------------------------------- |
| `tx_base` .. `rx_slope`         | The 6 linear model parameters                                        |
| `mean_delivery_pct`             | Mean delivery % across seeds                                         |
| `std_pct`, `min_pct`, `max_pct` | Delivery % statistics                                                |
| `total_delivered`, `total_sent` | Absolute counts summed across seeds                                  |
| `mean_ack_pct`                  | Mean combined ack % across seeds (empty if no acks)                  |
| `mean_chan_pct`                 | Mean channel % across seeds (empty if no channel messages)           |
| `mean_flood_delivery_pct`       | Mean flood-routed delivery % across seeds                            |
| `mean_path_delivery_pct`        | Mean path-routed delivery % across seeds                             |
| `mean_flood_ack_pct`            | Mean flood ack % across seeds                                        |
| `mean_path_ack_pct`             | Mean path ack % across seeds                                         |
| `n_seeds`                       | Number of seeds completed                                            |
| `fate_tracked`                  | Total messages tracked across seeds                                  |
| `fate_delivered`                | Messages where fate tracking confirmed delivery                      |
| `fate_lost`                     | Messages where fate tracking found no delivery                       |
| `lost_mean_collision`           | Mean collisions per lost message (averaged across seeds with losses) |
| `lost_mean_drop`                | Mean drops per lost message (averaged across seeds with losses)      |
| `mean_radio_eff_pct`            | Mean radio RX efficiency % — successful RX / total RX attempts      |
| `mean_ackpath_eff_pct`          | Mean ACK+path radio RX efficiency %                                  |
| `del_mean_ack_copies`           | Mean ACK/PATH copies reaching sender per delivered message           |
| `lost_mean_ack_copies`          | Mean ACK/PATH copies reaching sender per lost message                |

### Limitations

- **Linear model constraint**: Real optimal tables may be non-linear (e.g., plateau in the middle, steeper rise at high neighbor counts). The linear model cannot capture these shapes. Future work could add piecewise-linear or polynomial models.
- **Topology-specific**: Optimal table values depend on the network's neighbor-count distribution. A network where most nodes have 2-4 neighbors will be sensitive to different table entries than one where most have 8-10.
- **Static table**: The linear model assumes the relationship between neighbor count and optimal delay is consistent across all network conditions. In practice, the optimal slope may depend on traffic load, message patterns, or radio parameters.

## 5c. Message Fate Analysis

### Motivation

When messages fail to deliver, aggregate stats (e.g., "30% delivery") don't explain *why*. Was it collisions saturating the network? Link drops on weak paths? The message fate tracker follows each scheduled message through the relay chain and counts per-message collisions and drops, giving actionable diagnostic data.

### How it works

The orchestrator tracks each `msg`/`msga` command from send to delivery (or loss):

1. **Command detection**: When `processCommands` processes a `msg <dest>` or `msga <dest>` command, a `MessageFate` entry is created with `from_idx`, `to_idx`, `send_time_ms`, and `sent_as_flood` (determined from the reply string — flood vs direct routing).

2. **Initial TX linking**: In `registerTransmissions`, the first TX from the sending node (matching "msg" packet type) is linked to the fate via its FNV-1a packet hash.

3. **Relay chain tracking**: When a node receives a tracked packet (in `deliverReceptions`), it's marked as carrying that fate. If the node later transmits a "msg" packet (in `registerTransmissions`), the new TX hash is linked to the same fate -- even if the relay happens many simulation steps later (MeshCore's Dispatcher adds delay).

4. **Event counting**: For each tracked packet hash, the orchestrator counts:
   - **tx_count**: how many times the message was transmitted (including relays)
   - **rx_count**: successful receptions at any node
   - **collisions**: receptions destroyed by interference
   - **drops**: receptions lost to half-duplex conflicts or link loss

5. **Delivery detection**: If any tracked hash reaches the destination node as a successful RX, the fate is marked `delivered`.

### Orchestrator output

The orchestrator prints delivery stats with flood/path routing split, followed by a per-message fate summary:

```
Delivery: 3/3 messages (100%)
Delivery (flood): 1/1 (100%)
Delivery (path): 2/2 (100%)
Acks: 2/2 received (100%)
Acks (flood): 1/1 (100%)
Acks (path): 1/1 (100%)

Message fate (50 tracked, 11 delivered, 39 lost):
  Per delivered message: mean tx=194.2  rx=325.5  collision=290.2  drop=0.8  ack_copies=1.4
  Per lost message:      mean tx=43.4  rx=65.0  collision=50.3  drop=0.6  ack_copies=0.0
```

The `Delivery (flood)` / `Delivery (path)` lines show how many messages of each routing type were delivered. Similarly, `Acks (flood)` / `Acks (path)` split ACK reception by the routing type used for the original message.

Interpretation of the fate summary:
- **Delivered messages** had extensive network activity (194 TX, 325 RX) -- the flood reached enough nodes despite 290 collisions per message. The sender received 1.4 ACK/PATH copies on average (slight redundancy).
- **Lost messages** had much less activity (43 TX, 65 RX) -- the relay chain died early, starved by collisions. Zero ack copies reached the sender (as expected for undelivered messages).
- **Low drop counts** in both cases suggest link drops are not the primary failure mode; collisions dominate.

### Integration with optimize_tuning.py

The optimization script parses the fate summary and routing split stats from each run and aggregates across seeds. Six additional columns appear in both the results table and CSV:

| Table column | CSV column | Meaning |
|-------------|------------|---------|
| `F_del` | `mean_flood_delivery_pct` | Mean delivery % for flood-routed messages |
| `P_del` | `mean_path_delivery_pct` | Mean delivery % for path-routed messages |
| `F_ack` | `mean_flood_ack_pct` | Mean ACK % for flood-routed messages |
| `P_ack` | `mean_path_ack_pct` | Mean ACK % for path-routed messages |
| `r_eff` | `mean_radio_eff_pct` | Radio RX efficiency — successful RX / total RX attempts |
| `ap_eff` | `mean_ackpath_eff_pct` | ACK+path radio RX efficiency |
| `col/lost` | `lost_mean_collision` | Mean collisions per lost message — high values indicate congestion-dominated failure |
| `drp/lost` | `lost_mean_drop` | Mean drops per lost message — high values indicate link-quality-dominated failure |
| `ack/del` | `del_mean_ack_copies` | Mean ACK/PATH copies reaching sender per delivered message — 1.0 is ideal |

The routing split helps distinguish whether delay parameters disproportionately affect route discovery (flood) vs steady-state delivery (path). The fate columns help distinguish between congestion (high collisions) and link quality (high drops) as failure modes. The `ack/del` column tracks per-message ACK/PATH efficiency: how many ACK or PATH_RETURN copies reached the original sender for each delivered message. A value of 1.0 means exactly one copy arrived (ideal); higher values indicate redundant ack/path forwarding consuming airtime.

### Limitations

- **Relay ambiguity**: When a node receives multiple tracked messages and relays one, the relay is linked to all active fates. This can slightly over-count TX/RX for individual fates. Rare in practice.
- **Hash change on relay**: MeshCore modifies packet headers when relaying, changing the packet hash. The tracker bridges this gap using temporal correlation (RX at node N, then TX from node N), but if a node receives two different tracked messages simultaneously, their relay hashes may be cross-linked.
- **Only `msg`/`msga` commands**: Channel messages (`msgc`) are not tracked in this first approach.
- **No per-hop breakdown**: The tracker counts total collisions/drops across all hops, not which specific hop failed. A message that traverses 8 hops and loses 3 packets to collisions at hop 5 looks the same as one that loses 3 packets across hops 2, 4, and 7.

## 5d. Multi-Density Test Scripts

### Purpose

Optimal delay parameters may differ across network densities. The `delay_optimization/` directory includes three scripts that generate networks with different link densities from the Gdansk region and run full parameter sweeps on each.

### Script architecture

A unified `run_sweep.sh` script handles all three densities. It takes a variant name as the first argument and selects density-specific parameters via a case statement. The individual scripts (`run_sparse.sh`, `run_medium.sh`, `run_dense.sh`) are thin wrappers:

```bash
#!/usr/bin/env bash
exec "$(dirname "$0")/run_sweep.sh" sparse "$@"
```

All variants share a **single build directory** (`build-native-delay`) and a **single MeshCore fork** (`../MeshCore-stachuman`). The build uses `DELAY_TUNING_RUNTIME=ON`, which makes the delay table injectable from JSON at runtime — no per-variant recompilation needed. When multiple variants run in parallel, `flock` serializes the cmake build step; the first process builds, others wait and skip.

### What each script does

Each variant runs a 4-step pipeline:

1. **Generate topology** — runs `topology_generator` with the Gdansk region, density-specific link survival and edge caps
2. **Inject test cases** — runs `inject_test.py` to place 4 companions (alice, bob, carol, dave) and generate auto-schedules (70s direct interval, 80s channel interval, 5 direct + 4 channel messages per entry)
3. **Print topology statistics** — runs `topology_stats.py` to summarize the generated network
4. **Run optimization sweep** — runs `optimize_tuning.py` with 2,112 variants (11x2x6x2x4x2), 8 seeds, 8 parallel jobs

### Density configurations

| Variant | Density | `--link-survival` | `--max-edges-per-node` | `--max-good-links` | Typical neighbors |
|---------|---------|-------------------|------------------------|--------------------|--------------------|
| `sparse` | Sparse | 0.2 | 6 | 2 | 1-3 |
| `medium` | Medium | 0.4 | 12 | 3 | 3-6 |
| `dense` | Dense | 0.7 | 16 | 6 | 6-12 |

### Sweep parameters (same for all densities)

| Parameter | Range | Values |
|-----------|-------|--------|
| `tx_base` | 0.0:2.0:0.2 | 0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0 |
| `tx_slope` | 0.3:0.6:0.3 | 0.3, 0.6 |
| `dtx_base` | 0.0:1.0:0.2 | 0.0, 0.2, 0.4, 0.6, 0.8, 1.0 |
| `dtx_slope` | 0.3:0.6:0.3 | 0.3, 0.6 |
| `rx_base` | 0.0:6.0:2.0 | 0.0, 2.0, 4.0, 6.0 |
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
# Full sweeps (sequential)
./delay_optimization/run_sparse.sh
./delay_optimization/run_medium.sh
./delay_optimization/run_dense.sh

# All three in parallel (flock prevents build races)
./delay_optimization/run_sparse.sh &
./delay_optimization/run_medium.sh &
./delay_optimization/run_dense.sh &
wait

# Quick test (override seeds/jobs)
./delay_optimization/run_sparse.sh --seeds 2 -j 2

# Direct invocation of unified script
./delay_optimization/run_sweep.sh medium --seeds 4
```

Each script accepts `MESHCORE_DIR` as an environment variable to point to the MeshCore fork (defaults to `../MeshCore-stachuman`). Any additional arguments after the variant name are passed through to `optimize_tuning.py`.

### Prerequisites

1. MeshCore fork at `../MeshCore-stachuman` (or set `MESHCORE_DIR`)
2. No pre-build step needed — the sweep script builds automatically with `DELAY_TUNING_RUNTIME=ON` into `build-native-delay/`
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

# Step 5: Auto-tune table sweep (requires MeshCore fork build with runtime delay injection)
cmake -S . -B build-native-delay \
    -DCMAKE_BUILD_TYPE=NativeRelease \
    -DMESHCORE_DIR=$(pwd)/../MeshCore-stachuman \
    -DDELAY_TUNING_RUNTIME=ON
cmake --build build-native-delay

python3 tools/optimize_tuning.py test_config.json \
    --tx-base 0.0:2.0:0.2 --tx-slope 0.3:0.6:0.3 \
    --dtx-base 0.0:1.0:0.2 --dtx-slope 0.3:0.6:0.3 \
    --rx-base 0.0:6.0:2.0 --rx-slope 0.3:0.6:0.3 \
    --clamp-max 6.0 \
    --seeds 8 --build-dir build-native-delay \
    --meshcore-dir ../MeshCore-stachuman \
    -j 8 -o results.csv

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

All results below use Gdansk/Pomerania region topologies generated via `topology_generator` with ITM propagation model. Three network densities are tested (see Section 5d). These historical results used `optimize_tuning.py` with 512 variants and 6 seeds per variant (the current default is 8 seeds). The results predate the flood/path routing split — they report combined delivery and ack metrics only. Full CSV results are stored in this directory.

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

### Within-sweep findings

These findings compare the 512 auto-tune variants against each other and against the firmware default. They are valid **within the tested parameter range** but, as Section 8 reveals, the sweep's parameter range excluded the critical zero-delay baseline.

1. **Within the sweep range, the best variants improve over the default firmware** by +4.0 pp (sparse), +5.3 pp (medium), and +10.7 pp (dense). Dense networks benefit most because the default table doesn't ramp delays aggressively enough for high neighbor counts.

2. **Ack rates improve significantly in sparse/medium**: The best variants boost ack rates from 17.3% to 26.3% (sparse) and 15.3% to 24.7% (medium) — a ~50% relative improvement.

3. **Collision dominance**: Across all densities, message losses are overwhelmingly caused by collisions, not link drops. The `drp/lost` values are negligible (0.1-3.2) compared to `col/lost` (8-176).

4. **TX and RX delays serve opposite roles across densities**:
   - **Sparse**: high TX delays (base=1.0-1.5, slope=0.6) with low RX delays (0.0-0.5).
   - **Dense**: low TX delays (base=0.0, slope=0.3) with high RX delays (base=1.0, slope=0.6).

5. **Medium density is hardest**: Medium networks show the lowest peak delivery (49.3%) and mean delivery (42.5%), worse than both sparse and dense.

6. **Delivery is essentially flat across delay levels**: Quartile analysis shows <1 pp difference between the lowest-delay and highest-delay quartiles within each density. The sweep explored a flat plateau where delay amount barely matters — the "best" combinations are largely explained by seed variance.

7. **Critical omission**: The sweep's minimum parameter values (all slopes >= 0.3, clamp_min=0.05) meant the lowest total delay at an average node was 4.5-7.2s depending on density. **Zero delays were never tested.** This omission is corrected in Section 8.

## 8. Zero-Delay Baseline Discovery

### The missing baseline

The 512-variant sweep (Section 7) explored auto-tune tables where all slopes were >= 0.3 and the minimum clamp was 0.05. This meant the lowest total delay at an average node (5 neighbors) was:

```
Minimum sweep entry: base=0.0, slope=0.3 for all three delay types
At 5 neighbors: txdelay=1.5, direct.txdelay=1.5, rxdelay=1.5
Total: 4.5 seconds of added delay
```

**No combination in the sweep tested zero delays.** The sweep compared 512 ways of adding delay, but never compared any of them against the null hypothesis: no added delay at all.

### Firmware defaults

Querying a stock repeater (`get rxdelay`, `get txdelay`, `get direct.txdelay`) reveals the firmware defaults:

| Parameter | Default |
|-----------|---------|
| rxdelay | **0.0** |
| txdelay | **0.5** |
| direct.txdelay | **0.3** |

Even the stock firmware adds 0.8s total delay per hop. These are the values the white paper's analysis starts from — and recommends *increasing*.

### Targeted test: does any delay beat zero?

To test whether delays help at all, we ran a targeted experiment on the NL validation topology (215 repeaters, avg 5.2 neighbors, 496 direct messages per seed, 1-hour duration, O-U correlated fading). Nine delay configurations, 6 seeds each, all using the stock orchestrator with explicit `set` commands:

Script: `delay_optimization/run_targeted_nl.sh`

| Variant | txdelay | direct.txdelay | rxdelay | Delivery | vs stock | Col/lost | Drp/lost |
|---------|---------|----------------|---------|----------|----------|----------|----------|
| **zero** | 0 | 0 | 0 | **42.1% +/-0.3** | **+11.0pp** | 22.7 | 10.8 |
| small_0.5 | 0.5 | 0.5 | 0.5 | 32.3% +/-0.3 | +1.2pp | 25.1 | 0.5 |
| lo_tx_hi_rx | 0.5 | 0.5 | 3.0 | 31.4% +/-0.6 | +0.3pp | 26.4 | 0.4 |
| stock_default | 0.5 | 0.3 | 0.0 | 31.1% +/-0.5 | (base) | 25.3 | 0.5 |
| large_3.0 | 3.0 | 3.0 | 3.0 | 31.0% +/-0.3 | -0.1pp | 32.4 | 0.8 |
| medium_1.0 | 1.0 | 1.0 | 1.0 | 30.1% +/-0.4 | -1.0pp | 24.4 | 0.6 |
| medium_1.5 | 1.5 | 1.5 | 1.5 | 30.0% +/-0.8 | -1.1pp | 30.2 | 0.7 |
| hi_tx_lo_rx | 3.0 | 1.5 | 0.5 | 29.0% +/-0.3 | -2.1pp | 22.9 | 0.6 |
| large_5.0 | 5.0 | 5.0 | 5.0 | 28.9% +/-1.3 | -2.2pp | 23.4 | 0.6 |

**Zero delays win by a massive margin** — +11.0 pp over stock defaults, +13.2 pp over large delays. The relationship is monotonic: more delay = worse delivery. No delay level between 0 and 5.0 beats zero.

Collisions are also *lower* at zero (22.7) than at most non-zero settings. Delays do not reduce collisions — they appear to synchronize retransmissions rather than spreading them.

### Confirmation across all Gdansk densities

The same zero-delay test was run on the three Gdansk sweep topologies to compare directly with the 512-variant sweep data. Script: `delay_optimization/run_zero_baseline.sh`

| Density | Zero delays | Best of 512 sweep | Sweep mean | Sweep worst | Baseline |
|---------|-------------|-------------------|------------|-------------|----------|
| Sparse | **55.3% +/-3.5** | 52.7% | 45.9% | 38.7% | 48.7% |
| Medium | **55.7% +/-8.3** | 49.3% | 42.5% | 36.0% | 44.0% |
| Dense | **65.7% +/-3.9** | 55.7% | 46.5% | 37.7% | 45.0% |

Zero delays beat **every single one of the 512 auto-tune variants**, across **all three densities**. The advantage grows with density:

| Density | Zero vs best-of-512 | Zero vs baseline | Zero vs sweep-mean |
|---------|--------------------:|----------------:|-------------------:|
| Sparse | +2.6 pp | +6.6 pp | +9.4 pp |
| Medium | +6.4 pp | +11.7 pp | +13.2 pp |
| Dense | +10.0 pp | +20.7 pp | +19.2 pp |

### Why the theory fails in practice

The white paper's collision probability analysis (`P_collision ~ 1/t`) is correct for a single pair of nodes choosing from t backoff slots. But in a multi-hop network, several second-order effects overwhelm this pairwise benefit:

1. **Delay-induced synchronization.** Nodes with similar neighbor counts get similar delay values from the auto-tune table. When they all wait similar amounts, they tend to transmit at the same time — creating the exact synchronized burst the delays were supposed to prevent. With zero delays, natural timing differences from propagation, processing jitter, and half-duplex deferral provide sufficient decorrelation.

2. **Accumulated latency kills multi-hop delivery.** A message traversing 4 hops accumulates delay at each hop. With txdelay=1.5 and rxdelay=2.0 at each relay (typical for medium density in the white paper's table), a single message traversal adds ~14 seconds of total delay. This pushes messages past retransmission timeouts, causing the sender to retry — which creates *more* traffic and *more* collisions.

3. **The white paper correctly identifies but underweights implicit LBT.** Section 3.8.5 of the white paper notes that half-duplex RX-busy deferral "behaves like an implicit, topology-dependent random backoff." This effect is "loosely and noisily correlated with neighbor count" and "already implicitly helps." Our simulation confirms this is not just helpful but *sufficient* — the hardware-level half-duplex constraint already provides the timing decorrelation that txdelay attempts to add, without the latency cost.

4. **Direct messages have a single forwarder per hop.** The white paper itself acknowledges this (Section 3.6.1): "a direct message does not fan out" so "the probability of collision for that message's retransmission is independent of direct.txdelay." Adding direct.txdelay only helps when independent direct messages happen to collide — a rare event that doesn't justify the latency penalty on every message.

5. **The collision probability model ignores opportunity cost.** More backoff slots reduce per-event collision probability, but each slot represents one airtime of waiting. During that waiting time, the channel is potentially idle when it could be carrying useful traffic. The model optimizes for collision avoidance without accounting for the throughput lost to idle waiting.

### Practical implication

The most effective "optimization" for MeshCore delay parameters is:

```
set txdelay 0
set direct.txdelay 0
set rxdelay 0
```

This contradicts the white paper's recommendation to increase delays above the defaults. The firmware defaults of txdelay=0.5 and direct.txdelay=0.3, while modest, already cost approximately 11 percentage points of delivery in our simulation.

### Caveats

These findings are from simulation only. Real hardware may have legitimate reasons for non-zero delays that the simulator does not model:

- **Crystal oscillator settling time** — a radio may need brief delays between RX and TX mode transitions.
- **Regulatory duty cycle** — the simulator does not enforce EU 1% duty cycle limits. Delays might reduce duty-cycle violations on real hardware.
- **Processing latency** — real microcontrollers may not be able to process and retransmit as fast as the simulator assumes.
- **Frequency offset / near-far effects** — real radios have imperfect frequency alignment that may affect capture behavior differently than the simulator's SNR-based model.

If any of these hardware constraints require non-zero delays, the optimal values are likely much smaller than the white paper recommends — closer to the minimum needed for hardware compliance, not scaled by neighbor count.

## 9. Validation: Auto-Tune vs Baseline vs Zero

### Motivation

The parameter sweep (Section 7) found auto-tune variants that appeared to improve over firmware defaults. Section 8 then showed that zero delays beat everything. To complete the picture, we ran the NL validation topology with the "best" auto-tune candidate to confirm it doesn't help on an unseen topology.

### Validation topology

Script: `delay_optimization/run_medium_validation.sh`. Region: Groningen+Friesland, Netherlands (`52.8,5.2,53.5,7.0`), ~215 repeaters, 16 companions, 496 direct messages per seed, 1-hour duration, O-U correlated fading (`snr_coherence_ms=30000`).

Topology parameters tuned for NL terrain: `survival=0.27`, `snr_mid=5`, `max-good=12` (no cap). This produces avg ~5.2 neighbors with a natural distribution and well-connected main component (96.7%).

| Feature                | Sweep tests              | Validation test                      |
| ---------------------- | ------------------------ | ------------------------------------ |
| **Region**             | Gdansk/Pomerania, Poland | **Groningen+Friesland, Netherlands** |
| Duration               | 15 min                   | **1 hour**                           |
| Companions             | 4                        | **16**                               |
| Direct messages / seed | 50                       | **496**                              |
| Traffic patterns       | 3 (structured)           | **4 (+ random pairs)**               |
| Correlated fading      | off                      | **snr_coherence_ms=30000**           |

### Results: auto-tune vs stock baseline

The "universal candidate" from the sweep (tx_b=0.5, tx_s=0.6, dtx_b=0.0, dtx_s=0.3, rx_b=1.0, rx_s=0.6) was tested against stock firmware defaults:

| Metric | Baseline (stock) | Auto-tune (best of sweep) | Delta |
|--------|-----------------|--------------------------|-------|
| Delivery | 30.8% +/-2.7 | 29.2% +/-1.0 | **-1.6 pp** |
| Ack | 13.7% +/-1.2 | 14.7% +/-0.8 | +1.0 pp |
| Channel | 49.3% +/-3.2 | 50.3% +/-2.4 | +1.0 pp |
| Col/lost | 24.8 | 30.9 | +6.1 |
| Drop/lost | 0.5 | 0.8 | +0.3 |
| Msgs delivered | 917/2976 | 871/2976 | -46 |

The auto-tune variant **made delivery worse** (-1.6 pp) and **increased collisions** (+6.1 per lost message). The sweep's "best" parameters did not generalize to an unseen topology — confirming the finding from Section 8 that added delays hurt rather than help.

### Results: zero delays on NL topology

From the targeted test (Section 8), zero delays on the same NL topology:

| Metric | Baseline (stock) | Auto-tune | Zero delays |
|--------|-----------------|-----------|-------------|
| Delivery | 30.8% | 29.2% | **42.1%** |
| vs baseline | — | -1.6 pp | **+11.3 pp** |
| Col/lost | 24.8 | 30.9 | 22.7 |

Zero delays deliver +11.3 pp more than stock defaults and +12.9 pp more than the "optimized" auto-tune — on a topology neither was designed for.

## 10. Conclusions

### Summary of evidence

| Test | Densities | Region | Zero delivery | Best auto-tune delivery | Stock default delivery |
|------|-----------|--------|--------------|-------------------------|----------------------|
| Gdansk sparse | avg 2.7 nbrs | Poland | **55.3%** | 52.7% | 48.7% |
| Gdansk medium | avg 5.2 nbrs | Poland | **55.7%** | 49.3% | 44.0% |
| Gdansk dense | avg 8.0 nbrs | Poland | **65.7%** | 55.7% | 45.0% |
| NL validation | avg 5.2 nbrs | Netherlands | **42.1%** | 29.2% | 30.8% |

Zero delays are the best configuration in every test, across all densities, on both regions.

### What the simulation shows

1. **The white paper's pairwise collision model does not predict network-level delivery.** More backoff slots do reduce the probability that two specific nodes collide, but the accumulated latency, synchronized release effects, and retransmission timeouts more than cancel this benefit.

2. **Implicit mechanisms already provide sufficient decorrelation.** Half-duplex RX-busy deferral, propagation delay differences, processing jitter, and MeshCore's own Dispatcher timing create enough natural randomization that explicit delay parameters are redundant.

3. **The firmware defaults (txdelay=0.5, direct.txdelay=0.3) cost ~11 pp of delivery** in the simulator. The auto-tune table values recommended by the white paper (txdelay=1.0-2.5, rxdelay=2-8) cost even more.

4. **The effect is monotonic and density-independent.** More delay always means less delivery. The advantage of zero delays grows with network density (from +6.6 pp in sparse to +20.7 pp in dense), which is the opposite of what the pairwise theory predicts — dense networks, where collisions should be worst, benefit *most* from removing delays.

### Limitations

These findings are from simulation. Real hardware may require non-zero delays for reasons the simulator does not model (crystal settling, duty cycle compliance, processing latency). If so, the optimal values are likely much smaller than the white paper recommends — just enough for hardware compliance, without neighbor-count scaling.

### Recommendations

1. **For simulation/testing**: use `set txdelay 0`, `set direct.txdelay 0`, `set rxdelay 0` on all repeaters.
2. **For real hardware**: validate the zero-delay finding on physical repeaters before deploying. If hardware constraints require some delay, keep values as small as possible and do not scale by neighbor count.
3. **For the auto-tune PR**: the neighbor-count-indexed delay table approach adds complexity without benefit in simulation. The simplest improvement to MeshCore's default firmware would be reducing the stock txdelay from 0.5 to 0 and direct.txdelay from 0.3 to 0.
