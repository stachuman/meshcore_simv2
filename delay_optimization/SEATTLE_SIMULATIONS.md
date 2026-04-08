# Seattle Region Simulations

Two independent simulation approaches validate delay optimization findings in the Pacific NW / Seattle region. They use different data sources, topology construction methods, traffic models, and radio parameters — providing complementary evidence.

| | Simulation A: US Targeted | Simulation B: mcsim Import |
|---|---|---|
| **Purpose** | Delay variant comparison with US radio | Reproduce mcsim test with our engine |
| **Topology source** | Live MeshCore API + our ITM model | Brent-A/mcsim `sea.yaml` (pre-computed ITM) |
| **Repeaters** | 773 (varies by API snapshot) | 399 repeaters + 33 companions (fixed dataset) |
| **Link model** | Bidirectional, survival-filtered | Asymmetric, unfiltered, per-link `snr_std_dev` |
| **Companions** | 16, auto-placed (farthest-point) | 4, fixed locations (Downtown, Bellevue, N Seattle, Auburn) |
| **Traffic** | Heavy: 592 msgs/hour | Light: ~41 msgs/hour (session-based stochastic) |
| **Duration** | 1 hour | 1 hour |
| **Radio** | SF10, BW250kHz, CR 4/5, 915 MHz | SF7, BW62.5kHz, CR 4/5, 910.525 MHz |
| **Fading** | O-U correlated (30s coherence) | i.i.d. Gaussian per-packet |
| **Script** | `delay_optimization/run_us_targeted.sh` | `delay_optimization/run_mcsim_seattle.sh` |

---

## Simulation A: US Targeted Delay Test

Generates a Pacific NW topology from live data, injects heavy traffic, and compares 10 delay configurations. Direct analogue of the EU/Gdansk sweep, adapted for US radio parameters.

### Motivation

The delay optimization study (see `delay_optimization/DELAY_OPTIMIZATION.md`) found that zero delays beat all tested configurations for EU topologies (SF8, BW62500, CR4, 869 MHz). This test checks whether that finding holds for a different region and radio band.

Based on the original mcsim evaluation, whole region in Seattle changed delay parameters to the actual firmware defaults.

This test is our cross-check to re-evaluate findings using our meshcore_simv2.

### Radio parameters

| Parameter | Value | Notes |
|-----------|-------|-------|
| Frequency | 915.0 MHz | US ISM band |
| SF | 10 | US MeshCore default |
| BW | 250000 Hz | US MeshCore default |
| CR | 1 (= 4/5) | LoRa convention: CR value 1-4 maps to rate 4/(4+CR) |

### Topology generation

Uses `topology_generator` with the MeshCore map API + ITM propagation model.

| Parameter | Value |
|-----------|-------|
| Region | `47.0,-123.0,48.0,-121.5` (Pacific NW / Seattle area) |
| Climate | 5 (continental temperate) |
| TX power | 20 dBm |
| Antenna height | 5 m |
| Max distance | 40 km |
| Min SNR | -10 dB |
| Clutter | 6 dB |
| Link survival | 0.4 |
| Survival SNR midpoint | 5 dB |
| Max edges per node | 12 |
| Max good links | 12 |

The script aborts if fewer than 5 repeaters are found (the Pacific NW region may have limited MeshCore coverage).

### Traffic injection

Uses `tools/inject_test.py` with auto-placement:

- 16 companions via farthest-point sampling (`--min-neighbors 2`)
- Auto-schedule + channel broadcasts + 16 random DM pairs
- DM: interval 180s, count 8 per pair
- Channel: interval 240s, count 6 per companion
- Duration: 1 hour (3,600,000 ms)

Post-processing injects `simulation.radio` (SF10/BW250k/CR1) and `snr_coherence_ms: 30000` (O-U correlated fading) into the config JSON.

### Delay variants

| Label | txdelay | direct.txdelay | rxdelay | Notes |
|-------|---------|----------------|---------|-------|
| zero | 0 | 0 | 0 | |
| stock_default | (firmware defaults) | | | 0.5 / 0.3 / 0.0 |
| small_0.5 | 0.5 | 0.5 | 0.5 | |
| medium_1.0 | 1.0 | 1.0 | 1.0 | |
| medium_1.5 | 1.5 | 1.5 | 1.5 | |
| large_3.0 | 3.0 | 3.0 | 3.0 | |
| large_5.0 | 5.0 | 5.0 | 5.0 | |
| hi_tx_lo_rx | 3.0 | 1.5 | 0.5 | |
| lo_tx_hi_rx | 0.5 | 0.5 | 3.0 | |
| best_sweep | 1.5 | 0.0 | 0.5 | Best from EU parameter sweep |

Each variant runs with 6 seeds (configurable via `--seeds N`). Results are aggregated as mean delivery rate with standard deviation, compared against `stock_default` as baseline.

### Running

```bash
# Full run (6 seeds, ~1-2 hours)
bash delay_optimization/run_us_targeted.sh

# Quick test (1 seed)
bash delay_optimization/run_us_targeted.sh --seeds 1
```

Requires: built orchestrator (`cmake -S . -B build && cmake --build build`).

Output files in `delay_optimization/`:
- `us_topology.json` — generated topology (repeaters only)
- `us_test.json` — topology + companions + traffic + radio params

### Pipeline steps

1. Generate topology (topology_generator + ITM)
2. Inject test cases (inject_test.py)
3. Post-process: inject US radio params + correlated fading
4. Print topology statistics
5. Run 10 delay variants x N seeds
6. Print comparison table (sorted by delivery, delta vs stock)

### Results (2 seeds, April 2025 API snapshot)

Topology: 773 repeaters, 16 companions, 2,663 links, avg 6.5 neighbors, median 6.

| Variant | Delivery | vs stock | Ack | Chan | Col/lost | Drp/lost |
|---------|----------|----------|-----|------|----------|----------|
| zero | 50.7% +/-0.7 | **+16.0pp** | 16.5% | 71.0% | 188.2 | 80.4 |
| lo_tx_hi_rx | 37.9% +/-0.8 | +3.2pp | 16.5% | 58.0% | 294.9 | 4.4 |
| small_0.5 | 37.2% +/-0.1 | +2.5pp | 15.0% | 59.5% | 300.4 | 4.3 |
| stock_default | 34.7% +/-3.1 | (base) | 14.0% | 54.5% | 289.0 | 4.3 |
| best_sweep | 34.7% +/-0.0 | +0.0pp | **21.0%** | 57.0% | 232.3 | 4.3 |
| medium_1.0 | 33.8% +/-1.3 | -0.9pp | 17.0% | 58.0% | 213.8 | 3.7 |
| hi_tx_lo_rx | 30.8% +/-1.1 | -3.9pp | 15.5% | 57.5% | 324.7 | 7.2 |
| medium_1.5 | 30.7% +/-0.1 | -4.0pp | 15.0% | 60.0% | 220.2 | 4.3 |
| large_5.0 | 29.4% +/-0.8 | -5.3pp | 15.5% | 55.5% | 264.6 | 6.7 |
| large_3.0 | 28.4% +/-0.6 | -6.3pp | 15.0% | 64.0% | 285.5 | 6.2 |

Key findings:

- **Zero delay wins decisively** (+16pp over stock), consistent with EU/Gdansk results
- **Clear monotonic trend**: less delay = better delivery (zero > small > stock > medium > large)
- **Zero delay trades collisions for drops**: fewer collisions per lost msg (188 vs 289) but far more drops (80 vs 4) — aggressive retransmission causes half-duplex TX-aborts-RX, but the net effect is positive because floods propagate faster
- **best_sweep has highest ack rate** (21%) — zero `direct.txdelay` prioritizes DM acknowledgments
- **lo_tx_hi_rx outperforms stock** (+3.2pp) — high rx_delay staggers forwarding decisions, acting as natural collision avoidance for flood packets
- **Delay parameters don't just add jitter — they fundamentally change delivery**: the 22pp gap between zero (50.7%) and large_3.0 (28.4%) is substantial

### MeshCore delay mechanism

Three independent delay parameters control retransmission timing (see `simple_repeater/MyMesh.cpp:521-533`):

| Parameter | Default | Mechanism | Applies to |
|-----------|---------|-----------|------------|
| `rx_delay_base` | 0.0 (disabled) | SNR-based deterministic delay before processing: `(pow(base, 0.85 - score) - 1) * airtime`. Returns 0 if base <= 0. Below 50ms threshold → process immediately. | Flood packets only |
| `tx_delay_factor` | 0.5 | Random jitter before retransmit: `random(0, 5 * airtime * factor)` | Flood retransmit |
| `direct_tx_delay_factor` | 0.3 | Random jitter before retransmit: `random(0, 5 * airtime * factor)` | Direct retransmit |

With stock defaults (rx=0, tx=0.5, dtx=0.3): flood packets are processed immediately (no rx delay) then retransmitted after random jitter. Setting all three to 0 removes both stages — instant retransmission.

---

## Simulation B: mcsim Seattle Import

Imports pre-computed topology and traffic data from the [mcsim](https://github.com/Brent-A/mcsim) project — an independent MeshCore network simulator by Brent-A. This provides a completely separate data source: different ITM implementation, different link computation, different traffic model.

### Design philosophy

Minimal transformation — preserve source data as faithfully as possible:

- SNR values kept as-is (no recalculation)
- Per-link `snr_std_dev` preserved
- Asymmetric links preserved (`bidir: false`, explicit A->B and B->A entries)
- No survival filter, no edge pruning
- No SNR recalculation or distance-based adjustments
- Room_Server firmware type mapped to repeater role
- Companion nodes from topology preserved (`--keep-companions`); chatter overlay adds 4 more
- Radio parameters match mcsim defaults (SF7, BW62500, CR 4/5)
- Fading model matches mcsim (i.i.d. Gaussian per-packet, `snr_coherence_ms=0`)

### Source data

**Topology**: `sea.yaml` from `examples/seattle/` in the mcsim repository.

| Metric | Value |
|--------|-------|
| Total nodes | 432 (357 Repeater + 42 Room_Server + 33 Companion) |
| After conversion | 399 repeaters + 33 companions (Room_Server -> repeater, Companions kept) |
| Directed links | 17,582 |
| Total nodes (with chatter) | 436 (432 + 4 chatter companions) |
| Directed links (with chatter) | 17,590 |
| Avg neighbors | ~25 |
| Max neighbors | ~221 |
| Link SNR range | -7.5 to 120.0 dB |
| snr_std_dev range | 0.0 to 6.5 dB |

This is a dense, unpruned ITM dataset. The high neighbor counts reflect the full RF environment without any survival filtering.

**Traffic**: `chatter.yaml` from `examples/seattle/` — defines 4 companions with a session-based stochastic traffic model.

| Companion | Location | DM targets | DM prob | Channels | Msgs/session | Interval | Gap |
|-----------|----------|------------|---------|----------|-------------|----------|-----|
| Alice | Downtown (47.606, -122.331) | Bob, Charlie | 70% | #general | 5 | 30-120s | 300-600s |
| Bob | Bellevue (47.611, -122.200) | Alice | 80% | #general | 3 | 45-180s | 600-1200s |
| Charlie | N Seattle (47.756, -122.340) | Alice | 30% | #general, #north-seattle | 4 | 60-240s | 900-1800s |
| Dave | Auburn (47.308, -122.227) | Bob | 50% | #general | 2 | 120-300s | 1800-3600s |

### Anchor node mapping

The chatter overlay references anchor repeaters (SEA-Downtown, SEA-Bellevue, etc.) that don't exist in `sea.yaml`. The converter maps each companion to the nearest real repeater by geographic distance, preserving the original SNR values:

| Companion | Nearest repeater | Distance | SNR |
|-----------|-----------------|----------|-----|
| Alice | First Hill | 0.7 km | 18.0 dB |
| Bob | Airhack North Bellevue | 3.7 km | 16.0 dB |
| Charlie | WO0F Ridgecrest | 1.4 km | 14.0 dB |
| Dave | Mojo Repeater | 1.5 km | 15.0 dB |

### Traffic expansion

The mcsim model uses stochastic sessions (N messages with random inter-arrival times, followed by random-duration gaps). Our orchestrator uses deterministic schedules. The converter expands the stochastic model into concrete `message_schedule`/`channel_schedule` entries using a seeded RNG (default seed: 42).

With default parameters (1 hour, seed 42):
- 27 DMs + 14 channel broadcasts = 41 total messages
- Alice: 14 DMs, 6 channel (most active)
- Bob: 7 DMs, 2 channel
- Charlie: 2 DMs, 6 channel (channel-heavy, 30% DM probability)
- Dave: 4 DMs, 0 channel (occasional, long session gaps)

### Running the delay comparison

The full delay sweep script automates all steps (clone, convert, inject radio params, run 10 variants):

```bash
# Full run (6 seeds — slow, 399 nodes per run)
bash delay_optimization/run_mcsim_seattle.sh

# Quick test (1 seed)
bash delay_optimization/run_mcsim_seattle.sh --seeds 1

# Use existing mcsim checkout
bash delay_optimization/run_mcsim_seattle.sh --mcsim-dir /path/to/mcsim
```

The script runs the same 10 delay variants as `run_us_targeted.sh` and produces the same comparison table.

### Results (2 seeds, mcsim sea.yaml topology)

Topology: 399 repeaters + 33 companions + 4 chatter companions = 436 nodes, 17,590 links, avg 26.2 neighbors, median 18. 6 isolated nodes. 72.9% of repeaters in DelayTuning index [12] (high neighbor count).

| Variant | Delivery | vs stock | Ack | Chan | Col/lost | Drp/lost |
|---------|----------|----------|-----|------|----------|----------|
| zero | 100.0% +/-0.0 | **+35.2pp** | 59.0% | 95.0% | 0.0 | 0.0 |
| large_5.0 | 96.3% +/-5.2 | +31.5pp | 63.0% | 82.5% | 1709.7 | 475.6 |
| hi_tx_lo_rx | 94.4% +/-2.6 | +29.6pp | 66.5% | 93.0% | 0.0 | 11.5 |
| medium_1.0 | 90.8% +/-2.6 | +26.0pp | 67.0% | 89.0% | 0.0 | 24.1 |
| lo_tx_hi_rx | 87.1% +/-2.6 | +22.3pp | 52.0% | 92.5% | 11.8 | 14.8 |
| medium_1.5 | 83.3% +/-7.8 | +18.5pp | 57.5% | 89.0% | 0.4 | 5.4 |
| large_3.0 | 83.3% +/-2.6 | +18.5pp | 40.5% | 85.5% | 3.4 | 9.1 |
| small_0.5 | 81.5% +/-0.0 | +16.7pp | 55.5% | 88.0% | 0.0 | 21.1 |
| best_sweep | 70.4% +/-0.0 | +5.6pp | 46.0% | 96.5% | 11.2 | 12.8 |
| stock_default | 64.8% +/-7.8 | (base) | 43.0% | 86.0% | 18.0 | 11.3 |

Key findings:

- **Zero delay achieves 100% delivery** — no collisions, no drops, perfect across both seeds
- **All variants beat stock default** — even the worst non-stock variant (best_sweep, 70.4%) outperforms stock (64.8%)
- **Ranking is NOT monotonic** unlike the US test — medium_1.0 (90.8%) beats small_0.5 (81.5%), and large_5.0 (96.3%) is second best. The dense topology (avg 26.2 neighbors) creates different collision dynamics than the sparser US topology (avg 6.5)
- **large_5.0 seed 43 is anomalous**: col=3419, drp=928.5 with 100% delivery — only 1 message lost but with massive collision/drop counts on that single lost message
- **High variance** due to small sample (27 DMs per seed, 2 seeds): stock_default ranges from 59.3% to 70.4% across seeds
- **Our prediction was wrong**: we expected "all variants will show the same delivery rate" due to light traffic, but the 35pp gap between zero and stock shows that even 27 DMs on a 436-node network generates enough flood retransmissions to differentiate delay settings. The dense topology (avg 26 neighbors) amplifies each flood into many retransmissions, creating contention despite few user messages.

### Running individual steps manually

**Step 1: Get mcsim data**

```bash
git clone https://github.com/Brent-A/mcsim.git /tmp/mcsim_repo
```

**Step 2: Convert topology**

```bash
python3 tools/convert_mcsim.py \
    /tmp/mcsim_repo/examples/seattle/sea.yaml \
    --keep-companions \
    -v -o delay_optimization/mcsim_seattle.json
```

Optional: prune weak links with `--min-snr -15`. Omit `--keep-companions` to drop the 33 companion nodes from sea.yaml.

**Step 3: Add chatter overlay**

```bash
python3 tools/convert_mcsim_chatter.py \
    delay_optimization/mcsim_seattle.json \
    /tmp/mcsim_repo/examples/seattle/chatter.yaml \
    --duration 3600 --seed 42 \
    -v -o delay_optimization/mcsim_seattle_chatter.json
```

**Step 4: Run simulation**

```bash
./build/orchestrator/orchestrator delay_optimization/mcsim_seattle_chatter.json
```

### Output files

| File | Contents |
|------|----------|
| `delay_optimization/mcsim_seattle.json` | Converted topology (399 repeaters + 33 companions, no traffic) |
| `delay_optimization/mcsim_seattle_test.json` | Topology + 4 companions + traffic + radio params (from sweep script) |
| `delay_optimization/mcsim_seattle_chatter.json` | Topology + 4 companions + traffic schedules (from manual steps) |

---

## Fidelity Audit: mcsim Reproduction

An audit comparing what mcsim actually simulates versus what our Simulation B produces. The goal is to identify every divergence so results can be interpreted correctly.

### mcsim simulation defaults

Extracted from `mcsim-model/src/properties/definitions.rs` and `mcsim-lora/src/lib.rs`:

| Parameter | mcsim value | Source |
|-----------|------------|--------|
| SF | 7 | `definitions.rs:73` |
| BW | 62,500 Hz | `definitions.rs:65` |
| CR | 5 (= 4/5) | `definitions.rs:80` |
| Frequency | 910.525 MHz | `definitions.rs:57` |
| TX power | 20 dBm | `definitions.rs:87` |
| Preamble symbols | 8 | `lib.rs:115` |
| SNR threshold (SF7) | -7.5 dB | `lib.rs:118` |
| Capture effect | 6.0 dB flat | `lib.rs:210` |
| Fading | i.i.d. Gaussian N(mean, std_dev) | per-packet sampling |
| Simulation engine | Event-driven (no time step) | priority queue |
| Startup jitter | 10s +/- 10s | `sea.yaml defaults` |
| Default snr_std_dev | 1.8 dB | model property |
| Noise floor | -120 dBm | model property |

### Resolved discrepancies

These were identified by audit and fixed.

**1. Radio parameters: SF/BW mismatch (FIXED)**

The initial script used US radio defaults (SF10, BW250000) instead of mcsim defaults (SF7, BW62500). This changed everything about the radio layer — airtime, sensitivity threshold, collision dynamics. Now fixed: the script uses SF7/BW62500/CR1 matching mcsim exactly.

| | mcsim | Before fix | After fix |
|---|---|---|---|
| SF | 7 | 10 | 7 |
| BW | 62,500 Hz | 250,000 Hz | 62,500 Hz |
| Symbol time | 2.048 ms | 4.096 ms | 2.048 ms |
| SNR threshold | -7.5 dB | -15.0 dB | -7.5 dB |
| 50-byte airtime | ~330 ms | ~170 ms | ~330 ms |

**2. Fading model: O-U correlated vs i.i.d. (FIXED)**

The initial script injected `snr_coherence_ms=30000` (O-U correlated fading with 30s coherence). mcsim samples SNR independently per packet from `N(mean, std_dev)`. Now fixed: `snr_coherence_ms=0` (i.i.d.).

### Remaining discrepancies

These are inherent differences between the two simulation engines. They are documented rather than fixed.

**3. Channel mapping: named channels vs channel 0** -- MINOR

mcsim uses named channels (`#general`, `#north-seattle`). Charlie subscribes to both. Our converter maps all channel broadcasts to `channel: 0`. In practice all 4 companions are on `#general`, so the only difference is Charlie's `#north-seattle` messages — they reach all companions in our sim but would be scoped in mcsim. With only 14 channel messages total (6 from Charlie), impact is small.

**4. Traffic model: reactive agent vs pre-expanded schedule** -- MINOR

mcsim agents run in real-time within the sim and react to events (ACK received, session progression). Our converter pre-expands all traffic into a fixed `message_schedule` at conversion time, regardless of delivery success or failure. With light traffic (41 msgs over 1 hour), this difference is negligible — agents don't adapt their behavior based on delivery outcomes in any meaningful way.

**5. Room_Server firmware type** -- MINOR

42 Room_Server nodes are mapped to plain repeaters. In mcsim, Room_Server runs chat room functionality. For packet forwarding (the behavior we care about), Room_Server and Repeater are equivalent. The chat room features don't affect delay testing.

**6. Collision model: timing-dependent capture vs flat threshold** -- MINOR

mcsim uses a flat 6.0 dB capture threshold for all collisions regardless of arrival timing. Our orchestrator uses timing-dependent capture: 3.0 dB if the first packet locked the preamble (5 symbols head start), 6.0 dB otherwise. Plus preamble grace period and CR-dependent FEC tolerance. Our model is more lenient for first-arrival capture but equivalent for simultaneous collisions. With light traffic (41 msgs), collisions are rare.

**7. Simulation engine: event-driven vs time-stepped** -- MINOR

mcsim is fully event-driven (arbitrary time resolution). Our orchestrator uses discrete 5ms steps. With SF7/BW62500 symbol time of 2.048ms, the 5ms step spans ~2.4 symbols. This introduces up to 5ms timing granularity loss for collision detection and half-duplex state transitions. For the light traffic in this test, the practical impact is minimal.

**8. Half-duplex model differences** -- MINOR

mcsim models radio state machine with 100us RX/TX turnaround time. Our orchestrator uses TX/RX busy flags with half-duplex blocking (RX blocks TX, TX aborts RX). Both prevent simultaneous TX+RX. The 100us turnaround in mcsim is negligible compared to the ~330ms airtime at SF7.

**9. SNR rounding** -- NEGLIGIBLE

Our converter rounds SNR and snr_std_dev to 1 decimal place. Maximum precision loss is 0.05 dB. No practical impact.

**10. Startup jitter mechanism** -- NEGLIGIBLE

mcsim uses `startup_time_s=10 +/- 10s` jitter. Our orchestrator uses clock stagger (0-120s) with loop() calls during stagger. Both achieve the same goal (desynchronize periodic timers) through different mechanisms.

**11. LBT (Listen Before Talk)** -- POTENTIALLY SIGNIFICANT

Our orchestrator models LBT with CAD miss probability (default 5%). mcsim does not appear to model LBT. This means our sim may defer some transmissions that mcsim would not. Impact depends on channel congestion — with 41 messages over 1 hour on a 399-node network, the channel is rarely busy, so LBT rarely fires.

**12. Cryptographic keys** -- NONE

mcsim's `sea.yaml` has redacted keys (`private_key: "*"`). Our orchestrator generates fresh keys per node. This doesn't affect routing or delivery — only identity-based addressing, which works correctly regardless of specific key values.

### Why results may not match mcsim

Even with the radio and fading fixes, several structural differences mean our simulation will not produce identical results to mcsim. These are listed in order of likely impact.

**1. ~~Missing 33 companion nodes and 1,942 edges~~ (FIXED)**

Previously, `convert_mcsim.py` dropped the 33 Companion nodes from sea.yaml (and their 1,942 edges), reducing the network from 432 to 399 nodes. This is now fixed: the converter properly maps Companion firmware type to companion role, and the sweep script uses `--keep-companions`. Our sim now runs **436 nodes** (399 repeaters + 33 companions + 4 chatter companions) with **17,590 directed links** — matching mcsim's full topology.

**2. Traffic volume and topology density interaction** -- MODERATE

41 messages over 1 hour on a 436-node network produces low direct channel contention. At SF7/BW62.5k, each packet takes ~330ms airtime — total channel occupancy across all user messages is ~14 seconds out of 3,600.

However, the initial prediction that "all delay variants will show the same delivery rate" was **disproven by results**: zero delay achieved 100% delivery vs 64.8% for stock default (+35pp). The dense topology (avg 26.2 neighbors) amplifies each flood into many retransmissions — even 27 DMs generate enough relay traffic to create contention at nearby repeaters. The small sample size (27 DMs per seed) does produce high variance (stock_default: 59-70% across seeds), so results should be interpreted with more seeds for tighter confidence intervals.

**3. LBT modeled in our engine but not mcsim** -- MODERATE

Our orchestrator models Listen-Before-Talk with CAD (Channel Activity Detection) and a 5% miss probability. mcsim does not model LBT. Under the light chatter traffic this rarely fires, but during the advert storm at startup (399 nodes exchanging adverts), LBT will defer many transmissions that mcsim would send immediately. This changes the advert exchange dynamics and can affect which neighbor tables are populated by the time user traffic starts.

**4. Collision model is more lenient** -- MINOR

Our timing-dependent capture (3.0 dB threshold if preamble locked) is more lenient than mcsim's flat 6.0 dB for first-arrival packets. When two flood retransmissions arrive at a receiver with a timing offset, our sim will successfully capture more of them. This could yield slightly higher delivery in our sim compared to mcsim under identical traffic.

### Discrepancy summary

| Category | Status | Impact |
|----------|--------|--------|
| Radio params (SF/BW/CR) | Fixed | Was major, now matched |
| Fading model | Fixed | Was moderate, now matched |
| Topology data (SNR, links) | Faithful | Preserved as-is |
| 33 companion nodes | Fixed | Now preserved with `--keep-companions` |
| Traffic volume (41 msgs) | Faithful to mcsim | Moderate — dense topology amplifies floods despite few user msgs |
| LBT | Different engine | Moderate (affects advert exchange) |
| Collision model | Different engine | Minor (more lenient capture) |
| Channel mapping | Known gap | Minor (14 channel msgs) |
| Traffic model | Known gap | Minor (pre-expanded vs reactive) |
| Room_Server mapping | Known gap | Negligible (forwarding identical) |
| Time resolution | Different engine | Minor (5ms vs event-driven) |

### Interpretation guidance

The simulation faithfully reproduces the mcsim topology (all 436 nodes, 17,590 links) and traffic patterns within the inherent differences of the two engines. For **absolute delivery rate comparison** (does our engine match mcsim's numbers?), the LBT and collision model differences will cause some divergence. For **relative delay variant comparison** (which delay setting works best?), the results are clear: zero delay dominates across both simulations. The dense mcsim topology amplifies even light traffic into enough flood retransmissions to differentiate delay settings, though the small sample size (27 DMs/seed) produces high per-seed variance — more seeds are recommended for tighter confidence intervals.

### Cross-simulation comparison

Both simulations agree on the fundamental finding: **zero delay maximizes delivery**.

|                        | Sim A: US Targeted        | Sim B: mcsim Seattle            |
| ---------------------- | ------------------------- | ------------------------------- |
| Best variant           | zero (50.7%)              | zero (100.0%)                   |
| vs stock delta         | +16.0pp                   | +35.2pp                         |
| Monotonic ranking?     | Yes (less delay = better) | No (medium_1.0 beats small_0.5) |
| Zero collisions/drops? | No (188/80 per lost)      | Yes (0/0)                       |
| Topology density       | 6.5 avg neighbors         | 26.2 avg neighbors              |
| Traffic load           | 592 msgs                  | 41 msgs                         |

The denser mcsim topology produces higher absolute delivery (100% vs 50.7% for zero) because each message has more forwarding paths available. The US topology's sparser connectivity means some paths are unreachable regardless of delay settings, capping delivery below 100% even with perfect timing.

The non-monotonic ranking in the mcsim results (medium_1.0 > small_0.5) likely reflects the small sample size and high variance rather than a genuine reversal of the delay-delivery relationship. More seeds would clarify this.

---

## Tools Reference

| Tool                                      | Purpose                                                                                                                                     |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `delay_optimization/run_us_targeted.sh`   | Full delay sweep using generated PNW topology (Simulation A).                                                                               |
| `delay_optimization/run_mcsim_seattle.sh` | Full delay sweep using mcsim topology (Simulation B).                                                                                       |
| `tools/convert_mcsim.py`                  | Convert mcsim YAML topology to orchestrator JSON. Preserves SNR, asymmetric links, per-link std_dev. Use `--keep-companions` to retain companion nodes. |
| `tools/convert_mcsim_chatter.py`          | Convert mcsim chatter YAML overlay to message/channel schedules. Maps companions to nearest repeaters, expands session-based traffic model. |
| `tools/inject_test.py`                    | Add companions and traffic schedules to any topology config. Used by Simulation A.                                                          |
| `tools/topology_stats.py`                 | Print topology statistics (neighbor counts, connectivity, SNR distribution).                                                                |
| `topology_generator`                      | Generate topologies from live MeshCore API + ITM propagation. Used by Simulation A.                                                         |

---

## Notes

- **Radio parameter convention**: LoRa CR values 1-4 map to coding rates 4/5 through 4/8. Both mcsim CR=5 and our CR=1 represent the same rate 4/5 — just different naming conventions (mcsim uses denominator, we use offset).
- **Topology density**: The mcsim topology (avg ~25 neighbors) is much denser than our generated topologies (avg 6.5 neighbors after survival filtering). This reflects different design choices — mcsim includes all ITM-viable links, while our generator applies stochastic pruning for realism.
- **Traffic volume**: Simulation A generates 592 messages/hour (stress test), while Simulation B generates ~41 messages/hour (realistic chat patterns). They test different regimes.
- **Independence**: The two simulations share no data. Simulation A uses live API data + our ITM implementation. Simulation B uses mcsim's pre-computed ITM data. Agreement between them strengthens confidence in findings.
- **SNR values are modulation-independent**: The `mean_snr_db_at20dbm` field in sea.yaml represents received signal-to-noise ratio derived from path loss, TX power, and noise floor. It does not depend on SF/BW/CR. Different modulation settings change the *sensitivity threshold* (minimum SNR to decode), not the SNR itself. This is why the same topology can be validly simulated with different radio parameters.
