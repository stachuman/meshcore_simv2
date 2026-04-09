# Simulation Quality Audit

Comprehensive assessment of MeshCore Real Sim radio model accuracy, based on
internal code audit, academic LoRa PHY literature review, and survey of
open-source LoRa simulators. Conducted April 2026.

Implementation references: `orchestrator/Orchestrator.cpp`,
`shims/platform_shim/SimRadio.cpp`, `orchestrator/VirtualClock.h`.

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [HIGH Severity Findings](#2-high-severity-findings)
3. [MEDIUM Severity Findings](#3-medium-severity-findings)
4. [LOW Severity Findings](#4-low-severity-findings)
5. [Confirmed Correct](#5-confirmed-correct)
6. [Comparison with Other Simulators](#6-comparison-with-other-simulators)
7. [Academic Reference Values](#7-academic-reference-values)
8. [Remaining Opportunities](#8-remaining-opportunities)
9. [References](#9-references)

---

## 1. Executive Summary

The MeshCore Real Sim radio model is significantly more sophisticated than any
existing open-source LoRa simulator. It runs actual MeshCore firmware against a
physics model (not a simplified protocol simulation), and correctly implements:
timing-dependent capture, half-duplex with TX-aborts-RX, LBT with CAD miss
probability, O-U correlated fading, and 3-stage collision resolution.

The audit identified 2 HIGH and 6 MEDIUM severity issues. All have been
resolved: code fixes for H1, M2, M3, M4, M5, M6; analysis confirmed H2 was
not a bug; M1 was accepted by design with documentation. The model is now
well-aligned with academic literature.

### Severity Classification

| Severity | Found | Resolved | Meaning |
|----------|-------|----------|---------|
| HIGH     | 2     | 2 (1 fixed, 1 not a bug) | Measurably affects simulation results |
| MEDIUM   | 6     | 6 (5 fixed, 1 by design) | May affect edge-case accuracy |
| LOW      | 5     | 0 (accepted)             | Minor, unlikely to affect conclusions |

---

## 2. HIGH Severity Findings (All Resolved)

### H1. Default Coding Rate Mismatch — FIXED

Default `cr` changed from 4 (CR 4/8) to 1 (CR 4/5) to match MeshCore
hardware. Files: `Orchestrator.h`, `SimRadio.h`.

### H2. Capture Effect SNR vs SIR — Not a Bug

At the same receiver, noise floor N cancels: `SNR_A - SNR_B = SIR_AB` in dB.
No fix needed.

---

## 3. MEDIUM Severity Findings (All Resolved)

### M1. TX Snap-to-Step-Boundary — Accepted by Design

All TXes within a step share the same `rx_start_ms`. Analysis showed lock
time (6*T_sym = 24.6ms at SF8/BW62.5k) >> step_ms (4ms), so sub-step
offsets cannot cause a preamble lock. Users should set `step_ms <= T_sym`
for fast configs (documented in RADIO_MODEL.md Section 11).

### M2. Single RNG Cross-Coupling — FIXED

Split into 5 purpose-specific `mt19937` streams (`_rng_fading`, `_rng_loss`,
`_rng_cad`, `_rng_stagger`, `_rng_adversarial`), seeded from master seed
via XOR constants.

### M3. CAD Miss Probability is Distance-Independent — FIXED

Replaced flat `cad_miss_prob` with SNR-gated model: `cad_reliable_snr`
(above: base miss rate), `cad_marginal_snr` (below: always miss), linear
interpolation between. Defaults: 0.0 dB / -15.0 dB.

### M4. Preamble Lock Symbols — FIXED

Changed `PREAMBLE_LOCK_SYMBOLS` from 5 to 6 per Semtech AN1200.22 and
Bor et al. 2016. Updated `getPreambleDetectMs()` to match.

### M5. O-U Fading is Direction-Independent — FIXED

Changed from per-directed (`n*n`) to per-undirected (`n*(n-1)/2`) symmetric
indexing. A->B and B->A now share the same fading offset (reciprocal shadow
fading).

### M6. Hot-Start Companion Adverts Bypass Link Topology — FIXED

Replaced all-to-all companion advert injection with BFS reachability through
the full link topology. Only companions connected (directly or via multi-hop
repeater chain) receive each other's adverts.

---

## 4. LOW Severity Findings

### L1. VirtualClock Second-Resolution for getCurrentTime()

**Location**: `VirtualClock.h`, `getCurrentTime()`.

**Issue**: `getCurrentTime()` returns `_epoch_base + _virtual_millis / 1000`
(integer division). MeshCore uses this for timestamp-based decisions.
1-second granularity may cause timing coarseness for features that use
absolute time.

**Impact**: MeshCore primarily uses `getMillis()` for timing. `getCurrentTime()`
is used for display and packet timestamps, not critical scheduling.

---

### L2. Packet Hash Collisions (FNV-1a 32-bit)

**Location**: `Orchestrator.cpp`, packet fingerprinting.

**Issue**: FNV-1a 32-bit hash has ~1/2^32 collision probability per pair.
With N packets, birthday paradox gives ~N^2/2^33 expected collisions.
At 10,000 packets per simulation: ~0.01 expected collisions.

**Impact**: Negligible. Would only cause a false positive in fate tracking
(linking two unrelated packets). Would not affect delivery or collision
detection.

---

### L3. LBT Cleanup Performance

**Location**: `SimRadio.cpp`, `isReceiving()` iterates over busy windows.

**Issue**: Expired LBT busy windows are cleaned up lazily during
`isReceiving()` checks. With many transmissions, the list could grow.

**Impact**: Performance only, not accuracy. In practice, busy windows are
short-lived and the list stays small.

---

### L4. Message Fate Tracking Gaps

**Location**: `Orchestrator.cpp`, `_step_rx_fates` / `_pending_msg_fates`.

**Issue**: The RX-to-TX correlation for relay tracking uses FNV-1a hash
matching with a temporal window. If MeshCore internally modifies the packet
before retransmission (e.g., TTL decrement changes the hash), the relay
chain breaks.

**Impact**: Fate tracking may undercount relays for some packet types.
Does not affect simulation correctness, only diagnostic output.

---

### L5. xorshift32 RNG in SimRadio

**Location**: `SimRadio.cpp`, TX failure probability.

**Issue**: Uses a simple `xorshift32` PRNG seeded from node index. This is a
low-quality RNG with known statistical weaknesses (short period, poor
dimensional equidistribution).

**Impact**: TX failure probability is a minor feature used rarely.
The RNG quality is adequate for this purpose.

---

## 5. Confirmed Correct

The following aspects were audited and found to be correctly implemented:

### Airtime Calculation

`SimRadio::getEstAirtimeFor()` matches Semtech AN1200.13 exactly:
- Correct `DE=1` for `T_sym >= 16ms` (SF11+/BW125, SF12/BW250)
- `CRC=1` (always on for P2P)
- `IH=0` (explicit header)
- Ceiling function correctly implemented
- Preamble: `(8 + 4.25) * T_sym`

Verified against the [avbentem airtime calculator](https://avbentem.github.io/airtime-calculator/).

### SNR Sensitivity Thresholds

Match SX1276 datasheet values: SF7=-7.5dB through SF12=-20.0dB (2.5dB
step per SF).

### Half-Duplex Model

Correctly implements all three half-duplex effects:
1. TX blocks RX (drop_halfduplex)
2. RX blocks TX (isReceiving() defers Dispatcher)
3. TX aborts active RX (halfduplex_abort)

Pre-pass ensures symmetric behavior regardless of node iteration order.

### O-U Fading Statistics

Marginal distribution correctly preserves configured `snr_std_dev`.
Alpha calculation `exp(-dt/coherence_ms)` is correct for the O-U process.
First sample after large dt is effectively independent.

### Two-Phase Delivery Pipeline

Correct separation of TX registration (RF energy begins) and RX delivery
(packet decoded after full airtime). Lost packets correctly participate in
collision detection and LBT notification.

### NDJSON Event Timing

Events correctly use `rx_start_ms` (not delivery time) as the timestamp,
ensuring visualization bars align with TX bars.

---

## 6. Comparison with Other Simulators

### Major Open-Source LoRa Simulators

| Simulator | Type | Capture | Collision Model | LBT | Mesh | Fading | Active |
|-----------|------|---------|-----------------|-----|------|--------|--------|
| **LoRaSim** (Lancaster) | Packet-level Python | 6 dB SIR, flat | Time-overlap | No | No | No | Legacy (2016) |
| **FLoRa** (Aalto, OMNeT++) | Packet-level C++ | SIR-based | INET radio model | No | No | INET models | Inactive (2022) |
| **ns-3 lorawan** (Signetlab) | Packet-level C++ | SIR + inter-SF | Full ns-3 radio | No | No | ns-3 models | Active (v0.3.6, Mar 2026) |
| **ELoRa** (Orange, ns-3) | Emulation C++ | SIR-based | ns-3 radio model | Yes | No | ns-3 models | Active (v0.2.5, Jan 2026) |
| **Meshtasticator** | Discrete-event Python | Yes | Discrete-event | Partial | **Yes** | Simple | Active (440 commits) |
| **gr-lora_sdr** (GNU Radio) | Signal-level C++ | Real demod | Actual PHY | N/A | N/A | Real channel | Active (941 stars) |
| **LoRaPHY** (MATLAB) | Signal-level | BER curves | Signal-level | No | No | No | Inactive (2022) |
| **LoRaEnergySim** (Python) | Packet-level | No | Simple overlap | No | No | No | Inactive (2023) |
| **LWN-Simulator** (Go) | Network-level | No | Simplified | No | No | No | Active |
| **MeshCore Real Sim** (ours) | **Firmware-in-loop C++** | **Timing-dependent** | **3-stage + FEC** | **Yes + CAD miss** | **Yes** | **O-U correlated** | Active |

### Key Differentiators of MeshCore Real Sim

1. **Firmware-in-the-loop**: Runs actual MeshCore firmware, not a simplified
   protocol model. Captures all real timing, state machine transitions, and
   routing decisions.

2. **Timing-dependent capture**: Distinguishes locked (3 dB) vs unlocked (6 dB)
   capture thresholds based on preamble lock timing. Most simulators use a
   flat threshold.

3. **3-stage collision resolution**: Capture + preamble grace + FEC tolerance.
   Most simulators only implement stage 1 (capture).

4. **LBT with CAD miss**: Models channel activity detection with configurable
   miss probability. No other LoRa mesh simulator implements this.

5. **O-U correlated fading**: Time-correlated, reciprocal channel variations
   per link. Most packet-level simulators use i.i.d. or no fading.

6. **Half-duplex TX-aborts-RX**: Models the transceiver switching from RX to
   TX mid-reception. Most simulators only model TX-blocks-RX.

### Relevant External Code/Data

**Meshtasticator** is the closest comparable project (LoRa mesh simulator with
capture effect). Its collision model and CAD implementation could be a useful
cross-reference for validation, though it doesn't run real firmware.

**gr-lora_sdr** (GNU Radio LoRa SDR) implements full LoRa PHY (modulation,
demodulation, synchronization, CFO correction). Its BER curves at various SNR
levels could be used to validate sensitivity thresholds and derive more
accurate capture probability curves (instead of hard thresholds).

---

## 7. Academic Reference Values

### Capture Effect Thresholds

| Parameter | Literature Value | Our Value | Source |
|-----------|-----------------|-----------|--------|
| Same-SF capture, locked | 1-3 dB SIR | 3 dB (SNR diff) | Benaissa et al. 2021 |
| Same-SF capture, unlocked | ~6 dB SIR | 6 dB (SNR diff) | Semtech docs, Bor et al. 2016 |
| Preamble lock symbols | 6 symbols | 6 symbols | Semtech AN1200.22 |
| Inter-SF rejection | -16 to -36 dB | N/A (single SF) | Croce et al. 2018 |

### CAD Performance

| Parameter | Literature Value | Our Value | Source |
|-----------|-----------------|-----------|--------|
| CAD miss rate (short range) | ~0% | `cad_miss_prob` (default 5%) | Benaissa et al. 2021 |
| CAD miss rate (medium range) | 10-50% | SNR-interpolated (5%→100%) | Benaissa et al. 2021 |
| CAD miss rate (long range) | 50-100% | 100% (below `cad_marginal_snr`) | Benaissa et al. 2021 |
| CAD false positive rate | ~0% | 0% (not modeled) | Benaissa et al. 2021 |
| CAD detection time | ~2 symbols | 6 symbols (preamble detect) | Semtech AN1200.85 |

### Channel Characteristics

| Parameter | Literature Value | Our Model | Source |
|-----------|-----------------|-----------|--------|
| Channel reciprocity | Nearly reciprocal (<few dB) | Reciprocal (symmetric index) | Martinez 2019, Bor 2017 |
| Temporal fading std dev | 2-8 dB (environment) | Configurable `snr_std_dev` | Cattani et al. 2017, Liando et al. 2019 |
| Temporal coherence | 0.5-5 seconds (urban) | Configurable `snr_coherence_ms` | Multiple studies |
| CFO impact (SF7-9) | Negligible at +/-10ppm | Not modeled | Khartoum Eng J |
| CFO impact (SF11-12) | Significant at +/-10ppm | Not modeled | Khartoum Eng J |

### Airtime Formula Edge Cases

| Edge Case | Status | Notes |
|-----------|--------|-------|
| Low Data Rate Optimization (DE=1) | Correct | Applied when T_sym >= 16ms |
| CRC always on | Correct | CRC=1 for P2P |
| Explicit header (IH=0) | Correct | Header present |
| Header coded at CR 4/8 | **Not modeled** | Our formula uses configured CR for entire packet |
| Ceiling function boundary | Correct | Matches AN1200.13 |

Note: The header is always transmitted at CR 4/8 regardless of the configured
payload CR. The impact is small (header is only ~20 bits, 5 symbols at most)
but means our airtime is slightly optimistic at low CR values (CR 4/5, 4/6)
and exact at CR 4/8.

---

## 8. Remaining Opportunities

All HIGH and MEDIUM findings have been resolved. The following minor items
remain as potential future improvements:

| Issue | Effort | Impact |
|-------|--------|--------|
| Header coded at CR 4/8 in airtime formula | Small | ~5 symbols difference at low CR |
| L1-L5 low-severity items (see Section 4) | Various | Negligible |

---

## 9. References

### LoRa Capture Effect

- Bor, Roedig, Voigt, Alonso. "Do LoRa Low-Power Wide-Area Networks Scale?"
  MSWiM 2016. [ACM DL](https://dl.acm.org/doi/10.1145/2988287.2989163) |
  [PDF](https://uu.diva-portal.org/smash/get/diva2:1044681/FULLTEXT01.pdf)

- Rahmadhani, Kuipers. "When LoRaWAN Frames Collide." WiNTECH 2018.
  [ACM DL](https://dl.acm.org/doi/10.1145/3267204.3267212)

- Benaissa, Plets, Tanghe et al. "Dense Deployment of LoRa Networks:
  Expectations and Limits of CAD and Capture Effect." Sensors 2021.
  [PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC7865706/)

- Varsier, Dufrene. "Capacity limits of LoRaWAN." ICC 2017.

### Inter-SF Interference

- Croce, Gucciardo, Mangione, Tinnirello. "Impact of LoRa Imperfect
  Orthogonality." IEEE Comm. Letters 2018.
  [Semantic Scholar](https://www.semanticscholar.org/paper/Impact-of-LoRa-Imperfect-Orthogonality:-Analysis-of-Croce-Gucciardo/eb3386b51312b1e87cf6771425787ae702c1fa53)

- Waret, Kaneko, Guitton, El Rachkidy. "LoRa Throughput Analysis with
  Imperfect SF Orthogonality." IEEE WL 2019.
  [HAL](https://uca.hal.science/hal-01881386v1/document)

- Markkula, Mikhaylov, Haapola. "Simulating LoRaWAN: On Importance of
  Inter-SF Interference." ICC 2019.
  [Academia](https://www.academia.edu/60870484/Simulating_LoRaWAN_On_Importance_of_Inter_Spreading_Factor_Interference_and_Collision_Effect)

### CAD and LBT

- Semtech AN1200.85. "Introduction to Channel Activity Detection."
  [PDF](https://www.semtech.com/uploads/technology/LoRa/cad-ensuring-lora-packets.pdf)

- Xu et al. "LoRa Preamble Detection With Optimized Thresholds." IEEE 2022.
  [IEEE Xplore](https://ieeexplore.ieee.org/document/9997090/)

### Link Characterization

- Liando, Gamage, Tengourtius, Li. "Known and Unknown Facts of LoRa."
  ACM TOSN 2019. [ACM DL](https://dl.acm.org/doi/10.1145/3293534)

- Cattani, Perera, Calandra, Molteni. "Experimental Evaluation of LoRa
  Reliability." JSAN 2017. [MDPI](https://www.mdpi.com/2224-2708/6/2/7)

- Martinez et al. Sigfox/LoRa urban measurement study. 2019.
  [HAL](https://inria.hal.science/hal-02907283/document)

### Frequency Offset

- "Empirical Characterization of CFO Tolerance in LoRa." U. Khartoum Eng. J.
  [Paper](https://uofkej.uofk.edu/index.php/uofkej/article/view/330)

- CoRa: Collision-Resistant LoRa Symbol Detector. arXiv 2024.
  [arXiv](https://arxiv.org/html/2412.13930v2)

### Airtime and PHY

- Semtech SX1276 Datasheet.
  [PDF](https://cdn-shop.adafruit.com/product-files/3179/sx1276_77_78_79.pdf)

- Semtech AN1200.13. LoRa Modem Designer's Guide.

- Semtech AN1200.22. LoRa Modulation Basics.
  [PDF](https://www.frugalprototype.com/wp-content/uploads/2016/08/an1200.22.pdf)

- avbentem airtime calculator.
  [Web](https://avbentem.github.io/airtime-calculator/)

### Simulator Surveys

- Cotrim, Kleinschmidt. "Survey and Comparative Study of LoRa-Enabled
  Simulators." Sensors 2022.
  [MDPI](https://www.mdpi.com/1424-8220/22/15/5546)

### Open-Source Simulators

- LoRaSim. [Lancaster](https://www.lancaster.ac.uk/scc/sites/lora/lorasim.html)
- FLoRa (OMNeT++). [Website](https://flora.aalto.fi/)
- ns-3 lorawan (Signetlab). [GitHub](https://github.com/signetlabdei/lorawan) (GPLv2, active, v0.3.6 Mar 2026)
- ELoRa (Orange). [GitHub](https://github.com/Orange-OpenSource/elora) (GPLv2, active, v0.2.5 Jan 2026)
- Meshtasticator. [GitHub](https://github.com/meshtastic/Meshtasticator) (CC-BY-4.0, active, 193 stars)
- gr-lora_sdr (GNU Radio). [GitHub](https://github.com/tapparelj/gr-lora_sdr) (GPL-3.0, active, 941 stars)
- LoRaPHY (MATLAB). [GitHub](https://github.com/jkadbear/LoRaPHY) (MIT, 150 stars)
- LoRaEnergySim. [GitHub](https://github.com/GillesC/LoRaEnergySim) (GPL-3.0)

### Network Scalability

- Lavric, Popa. "LoRaWAN Communication Scalability in Large-Scale WSNs."
  Wireless Comms and Mobile Computing 2018.
  [Wiley](https://onlinelibrary.wiley.com/doi/10.1155/2018/6730719)

- Adelantado, Vilajosana et al. "Understanding the Limits of LoRaWAN."
  IEEE Communications Magazine 2017.

- LoRa Alliance. "LoRaWAN Gateways: Radio Coexistence Issues and Solutions."
  [PDF](https://lora-alliance.org/wp-content/uploads/2021/04/LA-White-Paper-LoRaWAN-Gateways-1.pdf)
