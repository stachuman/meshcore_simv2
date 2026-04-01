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

Test simulation period is 10 minutes with the following setup:

- **12 companions** placed via farthest-point sampling across 71 repeaters
- **Message interval**: 120s mean (each schedule gets a random offset within [0, interval) for desynchronization)
- **4 concurrent patterns**: 1-to-1, 1-to-many, many-to-1, and channel broadcast — all interleaved from the same base time
- **Deterministic randomization**: 3 different seeds per parameter combination (seeds 42, 43, 44)

Sweep grid used for the results in Section 7:

```
Parameter sweep: 858 combinations x 3 seeds = 2574 runs (roughly 40minutes of real time simulation)
  rxdelay:         [0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0]
  txdelay:         [0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.2, 2.4]
  direct.txdelay:  [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]
  seeds:           [42, 43, 44]
  Repeaters:       71
```


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

- **Collision detection**: 3-stage model (capture effect at 6 dB, preamble grace period, FEC tolerance)
- **Half-duplex**: nodes cannot TX while receiving (RX blocks TX, TX aborts active RX)
- **Listen-before-talk**: channel activity detection with preamble sensing delay
- **SNR variance**: per-link Gaussian jitter on each reception
- **Stochastic link loss**: per-link drop probability applied after collision checks

### Limitations

- **No multipath/fading dynamics**: SNR variance is static Gaussian, not correlated over time (no slow fading, no Doppler)
- **No duty cycle**: Real LoRa is subject to regulatory duty-cycle limits (1% in EU). The simulator does not enforce this.
- **Simplified collision model**: Real LoRa capture effect depends on timing, frequency offset, and coding rate in ways more complex than the 3-stage model
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

# 3. Parameter sweep (3 seeds, 4 parallel workers)
python3 tools/optimize_delays.py simulation/real_network.json \
  --rxdelay 0:5:1 --txdelay 0:1:0.2 --direct-txdelay 0:0.5:0.1 \
  --seeds 3 -j 4 -o results.csv

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

### 1) Direct messages only (no channel broadcast)

Baseline sweep with `--no-channel` — only direct (1-to-1, 1-to-many, many-to-1) traffic:

=====================================================================================
  Completed 2574 runs (858 combos x 3 seeds) in 22m51s
=====================================================================================
  Top 10 combinations (of 858):
=====================================================================================
   rxdelay   txdelay  d.txdelay    mean    std   min   max  delivered   acks
  --------  --------  ---------  ------  -----  ----  ----  ---------  -----
      0.00      0.00       0.00   31.7%   3.1%   29%   36%   266/840     16%
      0.60      0.00       0.00   31.7%   3.1%   29%   36%   266/840     16%
      0.80      0.00       0.00   31.7%   3.1%   29%   36%   266/840     16%
      1.00      0.00       0.00   31.7%   3.1%   29%   36%   266/840     16%
      1.20      0.00       0.00   29.0%   1.4%   28%   31%   242/840     13%
      1.40      0.00       0.00   29.0%   2.2%   26%   31%   243/840     13%
      1.20      0.00       0.50   29.0%   2.9%   25%   32%   244/840     12%
      0.00      0.00       0.50   28.7%   0.9%   28%   30%   241/840     12%
      0.60      0.00       0.50   28.7%   0.9%   28%   30%   241/840     12%
      0.80      0.00       0.50   28.7%   0.9%   28%   30%   241/840     12%


