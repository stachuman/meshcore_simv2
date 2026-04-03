# Topology Generator

Generate MeshCore simulation configs from real node positions using the ITM (Longley-Rice) propagation model. Downloads node coordinates from the MeshCore map API, SRTM terrain elevation data, and computes physics-based RF link quality for any region worldwide.

Lives in `topology_generator/` -- separate from the main simulation, no CMake integration needed.

Implementation: `topology_generator/__main__.py` (CLI + pipeline), `topology_generator/propagation.py` (ITM wrapper), `topology_generator/terrain.py` (SRTM), `topology_generator/api_fetch.py` (API client), `topology_generator/config_emitter.py` (JSON assembly).

---

## 1. Installation

```bash
pip install -r requirements.txt
```

Dependencies:
| Package | Version | Purpose |
|---|---|---|
| `itmlogic` | >= 1.2 | Pure Python ITM (Longley-Rice) propagation model |
| `SRTM.py` | >= 0.3.7 | NASA SRTM 30m elevation data (auto-downloads tiles) |
| `requests` | >= 2.28 | HTTP client for MeshCore map API |
| `numpy` | (transitive) | Pulled by itmlogic |

SRTM tiles are cached to `~/.cache/srtm/` (~8 MB per tile, ~30 MB for a typical region).

---

## 2. Workflow

Two-step pipeline: generate topology, then inject test cases.

```
topology_generator  -->  topology.json  -->  inject_test.py  -->  test_scenario.json  -->  orchestrator
```

```bash
# Step 1: Generate pure topology from real node positions
python3 -m topology_generator \
    --region 53.8,17.5,54.8,19.5 \
    --api-cache /tmp/nodes_cache.json \
    -v -o topology.json

# Step 2: Inject companions and test cases
python3 tools/inject_test.py topology.json \
    --add-companion alice:GDA_DW_RPT \
    --add-companion bob:GD_Swibno_rpt \
    --msg-schedule alice:bob:30 \
    --duration 600000 \
    -o test_scenario.json

# Step 3: Run simulation
build/orchestrator/orchestrator test_scenario.json
```

Output is a standard orchestrator config JSON (see [CONFIG_FORMAT.md](CONFIG_FORMAT.md)).

---

## 3. Pipeline

The topology generator runs a 10-step pipeline, with progress reported to stderr:

```
Step 1: Fetch nodes from API          -> ~31K nodes from map.meshcore.io
Step 2: Filter by region + type       -> bounding box + node type filter
Step 3: Sanitize names                -> ASCII-safe unique identifiers
Step 4: Generate candidate pairs      -> N*(N-1)/2, haversine pre-filter
Step 5: Sample terrain profiles       -> SRTM 30m elevation along each path
Step 6: Run ITM propagation           -> Longley-Rice path loss + clutter attenuation
Step 7: Apply edge caps               -> distance-priority, per-node max edges/good links
Step 8: Link statistics               -> SNR/distance range and distribution
Step 9: Connectivity check            -> BFS component detection, warn about islands
Step 10: Assemble + emit config       -> orchestrator JSON
```

Typical performance (Gdansk, ~180 nodes):

| Step | First run | Cached |
|---|---|---|
| API fetch | ~5s | <0.1s |
| SRTM tiles | ~30s | 0s |
| Terrain profiles | ~5s | ~5s |
| ITM computation | ~10s | ~10s |
| **Total** | **~50s** | **~15s** |

---

## 4. Topology Generator CLI

### Region and API

| Flag | Default | Description |
|---|---|---|
| `--region` | (required) | Bounding box: `lat_min,lon_min,lat_max,lon_max` |
| `--api-url` | `map.meshcore.io/api/v1/nodes` | MeshCore map API URL |
| `--api-cache` | none | Cache API response to this file path |
| `--node-types` | `2,3` | Node types: 1=companion, 2=repeater, 3=gateway |

### RF Parameters

| Flag | Default | Description |
|---|---|---|
| `--freq-mhz` | 869.618 | Operating frequency in MHz (EU ISM band) |
| `--tx-power-dbm` | 22.0 | TX power in dBm |
| `--antenna-height` | 5.0 | Antenna height AGL in meters |
| `--noise-figure-db` | 6.0 | Receiver noise figure in dB |
| `--sf` | 8 | LoRa spreading factor (7-12) |
| `--bw` | 62500 | LoRa bandwidth in Hz |
| `--cr` | 4 | LoRa coding rate (1-4) |

### ITM Propagation Parameters

| Flag | Default | Description |
|---|---|---|
| `--climate` | 6 | ITM climate code (see table below) |
| `--polarization` | 1 | 0=horizontal, 1=vertical |
| `--profile-points` | 150 | Elevation samples per link path |
| `--clutter-db` | 6.0 | Extra path loss for urban clutter, cables, connectors (dB) |

ITM climate codes:

| Code | Climate |
|---|---|
| 1 | Equatorial |
| 2 | Continental subtropical |
| 3 | Maritime subtropical |
| 4 | Desert |
| 5 | Continental temperate |
| 6 | Maritime temperate overland |
| 7 | Maritime temperate oversea |

### Link Filtering

| Flag | Default | Description |
|---|---|---|
| `--max-distance-km` | 30.0 | Maximum haversine distance for candidate pairs |
| `--min-snr` | -10.0 | Drop links below this SNR in dB |
| `--max-edges-per-node` | 8 | Hard cap on total edges per node |
| `--max-good-links` | 3 | Cap on SNR > 0 edges per node (prevents over-optimistic density) |

### Simulation Parameters

| Flag | Default | Description |
|---|---|---|
| `--duration` | 300000 | Simulation duration in ms |
| `--step` | 5 | Simulation step in ms |
| `--warmup` | 5000 | Warmup period in ms |
| `--hot-start` | true | Enable hot-start advert exchange |
| `--no-hot-start` | | Disable hot-start |

### Output and Parallelism

| Flag | Default | Description |
|---|---|---|
| `-o`, `--output` | stdout | Output file path |
| `-v`, `--verbose` | false | Verbose progress to stderr |
| `--workers` | 1 | Parallel workers for ITM computation |

---

## 5. Test Injection CLI (`tools/inject_test.py`)

Takes a topology JSON and adds companion nodes, message schedules, and delivery assertions. Supports both manual placement and auto-placement with farthest-point sampling. Replaces `tools/build_real_sim.py` for test scenario generation.

### Manual mode

```bash
python3 tools/inject_test.py topology.json \
    --add-companion alice:GDA_DW_RPT \
    --add-companion bob:GD_Swibno_rpt \
    --msg-schedule alice:bob:30 \
    -o test_scenario.json
```

### Auto mode

```bash
python3 tools/inject_test.py topology.json \
    --companions 10 --auto-schedule --channel \
    --msg-interval 30 --msg-count 5 \
    -v -o test_scenario.json
```

Auto-schedule generates diverse patterns from placed companions:
- **1-to-1**: round-robin pairing (c01->c02, c02->c03, ..., cN->c01)
- **1-to-many**: first companion broadcasts to all others
- **many-to-1**: all companions send to last companion
- **channel**: each companion broadcasts on public channel 0

All patterns run concurrently with random start offsets for desynchronization.

### Companion placement

| Flag | Default | Description |
|---|---|---|
| `--add-companion` | | `name:repeater[:snr[:rssi]]` -- manual placement (repeatable) |
| `--companions` | | Auto-place N companions (farthest-point sampling) |
| `--companion-snr` | 10.0 | SNR for companion-to-repeater links |
| `--companion-rssi` | -70.0 | RSSI for companion-to-repeater links |
| `--seed` | 42 | RNG seed for placement and schedule offsets |

Farthest-point sampling selects repeaters that maximize geographic spread. Requires repeaters to have `lat`/`lon` coordinates.

### Message schedules

| Flag | Default | Description |
|---|---|---|
| `--msg-schedule` | | `from:to:interval_s[:count]` -- manual schedule (repeatable) |
| `--auto-schedule` | | Generate diverse patterns (requires companions) |
| `--msg-interval` | 30 | Message interval in seconds (auto-schedule) |
| `--msg-count` | 5 | Messages per schedule entry (auto-schedule) |
| `--channel` | | Add channel broadcast schedule (requires `--auto-schedule`) |
| `--chan-interval` | (msg-interval) | Channel interval in seconds |
| `--chan-count` | (msg-count) | Channel messages per companion |

### Simulation overrides

| Flag | Default | Description |
|---|---|---|
| `--duration` | (keep input) | Override simulation duration_ms |
| `--warmup` | (keep input) | Override warmup_ms |
| `--no-auto-assert` | | Disable auto-generated delivery assertions |
| `-o`, `--output` | stdout | Output file path |
| `-v`, `--verbose` | | Show companion placement details |

Duration is auto-adjusted if schedules don't fit (adds 30s buffer).

Auto-assertions (on by default): for each unique sender->receiver pair, generates `cmd_reply_contains` checking "msg sent to B" and an `event_count_min` for rx events.

---

## 6. Propagation Model

### Link Budget

```
noise_floor = -174 + 10*log10(BW_Hz) + NF_dB

For BW=62500 Hz, NF=6 dB:
noise_floor = -174 + 47.96 + 6 = -120.04 dBm

SNR = TX_power_dBm - total_path_loss_dB - clutter_dB - noise_floor_dBm
RSSI = noise_floor + SNR
```

Maximum usable path loss at SNR = -10 dB: `22 - (-120) - (-10) = 152 dB`.

### ITM (Longley-Rice) Path Loss

The ITM model predicts RF propagation loss using terrain elevation profiles. It accounts for terrain diffraction, tropospheric scatter, and atmospheric effects over 20 MHz -- 20 GHz.

The tool calls itmlogic internals directly:

1. `qlrps()` -- general preparatory: compute wave number, earth curvature, surface impedance from frequency and ground parameters
2. `qlrpfl()` -- point-to-point preparatory: analyze terrain profile, compute horizon distances and effective heights
3. `avar()` -- statistical variation: compute excess attenuation at requested reliability/confidence levels

**Key detail**: itmlogic `avar()` returns excess attenuation over free-space path loss. Total path loss is:

    total_loss = FSPL(d_km, f_MHz) + avar_excess

Where `FSPL = 32.44 + 20*log10(d_km) + 20*log10(f_MHz)`.

### Short-Range Fallback

ITM is unreliable below ~0.5 km. For short links, free-space path loss is used directly with `snr_std_dev = 1.0`.

### SNR Standard Deviation

Derived from the 10th-90th percentile reliability spread of the ITM prediction:

    snr_std_dev = abs(snr_10pct - snr_90pct) / 2.56

The factor 2.56 is the z-score span between the 10th and 90th percentiles of the standard normal distribution. Minimum value clamped to 0.5 dB.

### Packet Loss

Marginal links have stochastic packet loss via logistic sigmoid:

    loss = 1 / (1 + exp(0.8 * (snr - (-6.0))))

This maps SNR -6 dB to 50% loss, with steep rolloff. Links with loss < 0.01% omit the field.

### Clutter Attenuation

ITM/SRTM models terrain but not buildings, trees, cable losses, or connector losses. The `--clutter-db` parameter adds a flat attenuation (default 6 dB) to both ends of every link. This brings the SNR distribution in line with real-world observations (median SNR around -2 dB in the Gdansk region, vs +2 dB without clutter).

### Edge Caps

After computing all viable links, a distance-priority selection caps edges per node to prevent unrealistic density. Links are sorted by distance (closest first), then accepted only if both endpoints are below their caps:

- `--max-edges-per-node` (default 8): hard cap on total edges
- `--max-good-links` (default 3): cap on SNR > 0 edges (prevents over-optimistic mesh density)

This matches the strategy in `convert_topology.py` and produces edge distributions comparable to real MeshCore network data (median 8 edges/node).

### Default RF Parameters

| Parameter | Value | Rationale |
|---|---|---|
| Frequency | 869.618 MHz | EU ISM LoRa band |
| TX power | 22 dBm | Typical LoRa ERP |
| Antenna height | 5m AGL | Conservative (no API height data) |
| Climate | 6 | Maritime temperate overland (Baltic) |
| Polarization | 1 (vertical) | Standard LoRa antenna |
| Ground permittivity | 15.0 | "Average ground" |
| Ground conductivity | 0.005 S/m | "Average ground" |
| Surface refractivity | 314 N-units | Continental temperate |
| Noise figure | 6 dB | Typical SX1276 receiver |

---

## 7. Data Sources

### MeshCore Map API

`GET https://map.meshcore.io/api/v1/nodes` returns ~31K nodes worldwide. Relevant fields:

```json
{
  "type": 2,
  "adv_name": "GD_Swibno_rpt",
  "adv_lat": 54.356,
  "adv_lon": 18.929,
  "params": { "freq": 869.618, "sf": 8, "bw": 62.5, "cr": 8 }
}
```

Type codes: 1 = companion, 2 = repeater, 3 = gateway. The API provides no antenna height data.

Use `--api-cache` to avoid repeated downloads (cached for 24 hours).

### SRTM Elevation Data

NASA Shuttle Radar Topography Mission, 30m resolution. Tiles auto-downloaded on first use and cached to `~/.cache/srtm/`. Returns `None` for ocean -- treated as 0m (sea level). 150 samples over a 50 km path gives ~333m spacing, well within the 30m data resolution.

---

## 8. Output Format

Output is a standard orchestrator config JSON. See [CONFIG_FORMAT.md](CONFIG_FORMAT.md) for the full schema.

All links are `bidir: true` -- ITM path loss is reciprocal with identical antenna heights.

```json
{
  "_source": "ITM topology generator, region=53.8,17.5,54.8,19.5",
  "simulation": {
    "duration_ms": 300000,
    "step_ms": 5,
    "warmup_ms": 5000,
    "hot_start": true,
    "radio": { "sf": 8, "bw": 62500, "cr": 4 }
  },
  "nodes": [
    { "name": "GD_Swibno_rpt", "role": "repeater", "lat": 54.356, "lon": 18.929 }
  ],
  "topology": {
    "links": [
      { "from": "GD_Swibno_rpt", "to": "GDA_DW_RPT", "snr": 12.5, "rssi": -107.5,
        "snr_std_dev": 3.2, "loss": 0.001, "bidir": true }
    ]
  },
  "commands": []
}
```

---

## 9. Connectivity

The tool checks graph connectivity after link filtering and warns about disconnected components:

```
  WARNING: 3 disconnected components:
    Component 0: 150 nodes
    Component 1: 25 nodes
    Component 2: 9 nodes
```

To reduce islands:
- Increase `--max-distance-km` to allow longer links
- Lower `--min-snr` to keep marginal links
- Increase `--antenna-height` (reduces path loss)
- Increase `--tx-power-dbm`

For post-hoc bridging, see `tools/convert_topology.py --bridge-islands`.

---

## 10. Examples

### Manual test: two named companions

```bash
python3 -m topology_generator \
    --region 53.8,17.5,54.8,19.5 \
    --api-cache nodes.json \
    -v -o gdansk.json

python3 tools/inject_test.py gdansk.json \
    --add-companion alice:GDA_DW_RPT \
    --add-companion bob:GD_Swibno_rpt \
    --msg-schedule alice:bob:30 \
    --duration 600000 \
    -o gdansk_test.json

build/orchestrator/orchestrator gdansk_test.json
```

### Auto test: 10 spread companions with diverse schedules

```bash
python3 -m topology_generator \
    --region 53.8,17.5,54.8,19.5 \
    --api-cache nodes.json \
    -o gdansk.json

python3 tools/inject_test.py gdansk.json \
    --companions 10 --auto-schedule --channel \
    --msg-interval 30 --msg-count 5 \
    --duration 600000 -v \
    -o gdansk_stress.json

build/orchestrator/orchestrator gdansk_stress.json
```

### US 915 MHz band

```bash
python3 -m topology_generator \
    --region 37.0,-122.5,38.0,-121.5 \
    --freq-mhz 915.0 \
    --climate 5 \
    --api-cache nodes_us.json \
    -o bay_area.json
```

### Parallel computation for large regions

```bash
python3 -m topology_generator \
    --region 50.0,14.0,55.0,24.0 \
    --workers 8 \
    --api-cache nodes_pl.json \
    -o poland.json
```
