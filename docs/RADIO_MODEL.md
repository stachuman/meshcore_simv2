# Radio Physics Model

The MeshCore Real Sim orchestrator models LoRa radio physics for packet delivery between simulated nodes. This document describes the complete radio model: airtime calculation, signal propagation, collision detection, half-duplex behavior, and listen-before-talk.

Implementation: `orchestrator/Orchestrator.cpp` (delivery pipeline, collision model), `shims/platform_shim/SimRadio.cpp` (airtime, thresholds).

---

## 1. LoRa Airtime Calculation

Based on Semtech AN1200.13 (LoRa Modem Designer's Guide).

### Symbol duration

    T_sym = 2^SF / BW_Hz * 1000   [ms]

| SF | BW 62500 Hz | BW 125000 Hz |
|----|-------------|--------------|
| 7  | 2.048 ms    | 1.024 ms     |
| 8  | 4.096 ms    | 2.048 ms     |
| 9  | 8.192 ms    | 4.096 ms     |
| 10 | 16.384 ms   | 8.192 ms     |
| 11 | 32.768 ms   | 16.384 ms    |
| 12 | 65.536 ms   | 32.768 ms    |

### Preamble duration

    T_preamble = (N_pre + 4.25) * T_sym

N_pre = 8 symbols (SX1276 default, hardcoded in `SimRadio::getPreambleSymbols()`).

### Payload airtime

    DE = 1 if T_sym >= 16 ms (low data-rate optimize), else 0
    payload_symbols = 8 + max(ceil((8*N_bytes - 4*SF + 44) / (4*(SF - 2*DE))) * (CR+4), 0)
    T_payload = payload_symbols * T_sym

### Total airtime

    T_total = T_preamble + T_payload

Implementation: `SimRadio::getEstAirtimeFor()`.

---

## 2. Link Model

Matrix-based: each ordered (sender, receiver) pair either has a link or is unreachable (no communication).

### Link parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| snr | float dB | 8.0 | Mean signal-to-noise ratio at receiver |
| rssi | float dBm | -80.0 | Received signal strength indicator |
| snr_std_dev | float dB | 0.0 | Per-reception Gaussian jitter (0 = deterministic) |
| loss | float [0,1] | 0.0 | Stochastic packet loss probability |

Links can be unidirectional (`bidir: false`) or bidirectional. Bidirectional links share parameters in both directions. Real links from topology data are typically directional with distinct SNR values per direction.

### Per-reception SNR sampling

When `snr_std_dev > 0`:

    rx_snr ~ Normal(snr_mean, snr_std_dev)

using the global seeded `std::mt19937` (default seed=42). Models real-world fading and interference variation.

### Receiver sensitivity (SNR threshold)

Each SF has a minimum decodable SNR (from Semtech SX1276 datasheet):

| SF | Threshold (dB) |
|----|----------------|
| 7  | -7.5  |
| 8  | -10.0 |
| 9  | -12.5 |
| 10 | -15.0 |
| 11 | -17.5 |
| 12 | -20.0 |

Signals below threshold are dropped as `drop_weak` — no RF energy contribution, no LBT notification (below CAD detection level).

Implementation: `SimRadio::getSnrThreshold()`, `SimRadio::packetScore()`.

---

## 3. Two-Phase Packet Delivery

Packets use a two-phase pipeline that models the physical reality of RF transmission: energy is on the air for the full airtime, and the receiver can only decode after the entire packet is received.

### Phase 1: `registerTransmissions()` (at TX start time)

For each TX captured from a node's `pending_tx` queue:

1. **Adversarial pre-pass**: Drop/Corrupt/Replay modifications (if configured)
2. **Half-duplex pre-pass**: Set `tx_busy_until` for all senders; mark any active RX on senders as `halfduplex_abort`
3. **Per-receiver processing**:
   - Sample per-reception SNR from Gaussian
   - Check SNR threshold → `drop_weak` if below
   - Notify LBT (channel busy window on receiver)
   - Check half-duplex (receiver transmitting → `drop_halfduplex`)
   - Roll stochastic link loss (mark `link_loss`, but still create PendingRx — lost packets still occupy the channel as RF energy for collision detection)
   - Create `PendingRx` entry with `rx_start_ms = current_ms`, `rx_end_ms = current_ms + airtime`
   - **Collision detection**: test against all existing `active_rx` entries on this receiver (see Section 4)
   - Notify receiver's `SimRadio` of active reception (`notifyRxStart`)
   - Add to receiver's `active_rx` queue

### Phase 2: `deliverReceptions()` (at RX completion time)

When `current_ms >= rx_end_ms`, process the `PendingRx`:

- **Not collided/lost/aborted** → deliver to radio queue (successful `rx` event)
- **`halfduplex_abort`** → `drop_halfduplex` event
- **`link_loss`** → `drop_loss` event
- **`collided`** → `collision` event

All events use `rx_start_ms` as the NDJSON timestamp (not `current_ms`) so visualization bars align with TX bars.

---

## 4. Collision Model

When multiple packets overlap in time at the same receiver, the collision model determines which survive. Each pair of overlapping packets is tested independently in both directions.

### 4.1 Timing-Dependent Capture Effect

Real SX1276/SX1262 receivers lock onto the first detected preamble. The capture threshold depends on whether the primary signal achieved preamble lock before the interferer arrived.

**Preamble lock**: After receiving `PREAMBLE_LOCK_SYMBOLS` (5) clean preamble symbols, the receiver has synchronized its correlator to the primary signal's chirp pattern.

    lock_time = primary.rx_start_ms + 5 * T_sym

**Threshold selection**:

| Scenario | Threshold | Rationale |
|---|---|---|
| Primary locked (`interferer.rx_start_ms >= lock_time`) | **1 dB** | Semtech AN1200.22: co-SF capture at 1 dB SIR when receiver is synchronized |
| Preambles overlap (`interferer.rx_start_ms < lock_time`) | **6 dB** | Classic LoRaSim model: uncertain lock, power must dominate |

**Capture check**:

    if primary.snr >= interferer.snr + threshold:
        primary survives (not destroyed)

This is evaluated for each `(primary, interferer)` pair in both directions. The evaluation is naturally asymmetric when packets arrive at different times: the first arrival gets the low threshold (lock advantage), the later arrival faces the high threshold. When both arrive in the same simulation step (`rx_start_ms` equal), both directions use 6 dB.

**Why not always 1 dB?** When preambles overlap, the receiver's correlator sees two concurrent chirp patterns. It cannot cleanly lock onto either unless one dominates by ~6 dB. The timing-dependent threshold reflects this physical reality.

### 4.2 Preamble Grace Period

If the interferer only overlaps the non-critical early portion of the primary's preamble, it does not destroy the primary. The first 3 symbols (of 8 total preamble) are grace period:

    preamble_grace = (N_pre - 5) * T_sym = 3 * T_sym
    critical_time = primary.rx_start_ms + preamble_grace

    if interferer.rx_end_ms <= critical_time:
        primary survives

### 4.3 FEC Overlap Tolerance

If the overlap is small and occurs entirely within the payload portion (after preamble), forward error correction may recover the data. The tolerance depends on coding rate:

| Coding Rate | FEC Symbols | Tolerance |
|---|---|---|
| CR 4/5 | 0 | 0 ms |
| CR 4/6 | 0 | 0 ms |
| CR 4/7 | 1 | 1 * T_sym |
| CR 4/8 | 2 | 2 * T_sym |

    overlap = [max(primary.start, interferer.start), min(primary.end, interferer.end)]

    if overlap_duration <= fec_tolerance AND overlap_start >= payload_start:
        primary survives

### Collision flow summary

For each new `PendingRx` at a receiver, test against every existing `active_rx`:

```
for each (new_pkt, existing_pkt) pair:
    if isDestroyedBy(new_pkt, existing_pkt):     new_pkt.collided = true
    if isDestroyedBy(existing_pkt, new_pkt):     existing_pkt.collided = true
```

Both packets can be destroyed (mutual collision), or one can survive (capture), or both survive (no temporal overlap, or both survive their respective checks).

---

## 5. Half-Duplex Modeling

LoRa transceivers cannot transmit and receive simultaneously.

### 5.1 TX blocks RX

When a node is transmitting (`tx_busy_from` to `tx_busy_until`), incoming packets are dropped as `drop_halfduplex`. TX-busy flags are set in a **pre-pass** over all senders before the main routing loop, ensuring symmetric behavior regardless of node iteration order.

### 5.2 RX blocks TX (LBT / isReceiving)

When a node is receiving (active `PendingRx` or LBT busy window), `SimRadio::isReceiving()` returns true. MeshCore's `Dispatcher` checks this before attempting TX — if the channel is busy, TX is deferred.

### 5.3 TX aborts RX

If a node starts TX while an active reception is in progress (via `SimRadio::startSendRaw()`), the reception is marked `halfduplex_abort` and will be dropped at delivery time. The radio's `_rx_active_until` is cleared.

---

## 6. Listen-Before-Talk (LBT)

Channel activity is detected after a preamble detection delay:

    preamble_detect_delay = 5 * T_sym

This matches the preamble lock time — the receiver needs ~5 symbol periods to detect and classify an incoming preamble via Channel Activity Detection (CAD).

The channel is marked busy from `node_now + preamble_detect_delay` until `node_now + airtime`. Only signals above the SNR threshold trigger LBT notification (weak signals are invisible to CAD).

**Clock domain**: LBT timestamps are converted from orchestrator time to **node-clock domain** (which includes per-node stagger offset) to ensure correct comparison with `SimRadio::isReceiving()`.

---

## 7. Stochastic Effects

### Per-link SNR variance

When `snr_std_dev > 0`, each reception draws a Gaussian-sampled SNR. This affects:
- Whether the signal exceeds the receiver sensitivity threshold (drop_weak)
- The effective SNR used in capture effect comparisons
- LBT notification (only above-threshold signals trigger it)

### Per-link packet loss

When `loss > 0`, packets are dropped with the given probability at registration time. Lost packets still occupy the channel as RF energy — they create `PendingRx` entries that participate in collision detection and LBT notification, but are not delivered to the application layer.

---

## 8. Warmup Phase

During `[0, warmup_ms)`, packets are delivered instantly with **no physics simulation**:
- No collision detection
- No half-duplex checks
- No SNR filtering
- No LBT notification
- No airtime delay

This allows the network to bootstrap routing tables quickly before the physics simulation begins.

---

## 9. Hot Start

Optional collision-free advert exchange before the main simulation loop:

1. **Companion-to-companion injection**: Direct advert injection between all companions (repeaters don't forward companion adverts)
2. **Staggered advert triggers**: Each node's `advert` command fired 2 seconds apart (Dispatcher needs ~1500ms between adverts)
3. **Collision-free delivery**: Instant delivery with no physics (same as warmup)
4. **Quiescence-based settle**: Loop continues until no node transmits for 5 seconds. Auto-adapts to any network size (safety cap at 120 seconds).

See CONFIG_FORMAT.md for configuration.

---

## 10. Per-Node Clock Stagger

Each node owns an independent `VirtualClock`. After mesh initialization, each node's clock is advanced by a random duration in [0, 120s) with `loop()` calls (TX suppressed). This causes MeshCore's internal periodic timers (advert broadcasts, route maintenance) to fire at different times across nodes, preventing artificial synchronization.

Key insight: a constant clock **offset** doesn't stagger timers (cancels in `next = getMillis() + interval` vs check). The clock must be advanced **with loop() calls** so MeshCore processes the time naturally and resets its timers to the staggered epoch.

---

## 11. Constants Summary

| Constant | Value | Source | Location |
|---|---|---|---|
| CAPTURE_LOCKED_DB | 1.0 dB | Semtech AN1200.22 | Orchestrator.cpp |
| CAPTURE_UNLOCKED_DB | 6.0 dB | LoRaSim | Orchestrator.cpp |
| PREAMBLE_LOCK_SYMBOLS | 5 symbols | SX1276 empirical | Orchestrator.cpp |
| Preamble symbols (N_pre) | 8 | SX1276 default | SimRadio.h |
| Preamble grace | (N_pre - 5) * T_sym = 3 * T_sym | Derived | Orchestrator.cpp |
| FEC tolerance | {0, 0, 1, 2} symbols for CR 4/5..4/8 | Derived | Orchestrator.cpp |
| Preamble detect delay | 5 * T_sym | SX1276 CAD | SimRadio.cpp |
| SNR thresholds | SF7:-7.5 ... SF12:-20.0 dB | SX1276 datasheet | SimRadio.cpp |

---

## 12. What Is NOT Modeled

| Feature | Why omitted |
|---|---|
| **Inter-SF interference** | MeshCore uses a single SF across all nodes. The Semtech AN1200.22 inter-SF isolation matrix (1-25 dB rejection) would apply if mixed SFs were used. |
| **Frequency offset** | All nodes share one channel. LoRaSim checks +-30kHz (BW125), but irrelevant for single-channel. |
| **Multipath / slow fading** | SNR variance is static Gaussian, not time-correlated. No Rayleigh/Rician channel model. |
| **Duty cycle limits** | EU 868MHz requires 1% duty cycle. Not enforced. |
| **Near-far desensitization** | Receiver sensitivity is not degraded by strong nearby transmitters beyond the collision model. |
| **Clock drift** | Node clocks advance in lockstep (no drift). Only stagger offsets differentiate timing. |
| **Terrain / propagation** | SNR comes from the link table (measured or gap-filled). No ray-tracing or path-loss calculation. |

---

## References

- Semtech AN1200.13 — LoRa Modem Designer's Guide (airtime formulas)
- Semtech AN1200.22 — LoRa Modulation Basics (inter-SF isolation matrix, co-SF capture)
- [LoRaSim](https://github.com/adwaitnd/lorasim) — Original 6 dB capture model
- [Dense LoRa Deployment: CAD and Capture Effect](https://pmc.ncbi.nlm.nih.gov/articles/PMC7865706/) — Empirical timing-dependent capture measurements
- [Simulating LoRaWAN: Inter-SF Interference (ICC 2019)](https://ieeexplore.ieee.org/document/8761055/) — Three collision model comparison
