# convert_topology.py

Converts raw MeshCore topology data (`topology.json`) into orchestrator config format.

## Basic Usage

```bash
python3 tools/convert_topology.py simulation/topology.json -o config.json
```

By default this:
- Drops stub nodes (prefix < 8 chars) and nodes with missing coordinates (lat=0, lon=0)
- Estimates coordinates for lat=0/lon=0 nodes from neighbors before filtering
- Filters edges: drops `inferred` source, confidence < 0.7, SNR < -10 dB
- Fills gaps: adds estimated edges for unmeasured nearby pairs
- Outputs directed (unidir) edges with `bidir: false`

## Edge Filtering

```bash
# Lower SNR threshold to include weaker links
python3 tools/convert_topology.py topology.json --min-snr -15 -o out.json

# Include inferred edges (lower confidence)
python3 tools/convert_topology.py topology.json --include-inferred -o out.json

# Adjust confidence threshold
python3 tools/convert_topology.py topology.json --min-confidence 0.5 -o out.json
```

## Coordinate Estimation

Nodes with lat=0/lon=0 (and 8+ char prefix) get coordinates estimated from their neighbors using SNR-weighted centroid. This is **on by default**.

```bash
# Disable coordinate estimation (keep zero coords)
python3 tools/convert_topology.py topology.json --no-estimate-coords -o out.json

# Show estimation details
python3 tools/convert_topology.py topology.json -v -o out.json
# Output: Estimated coords for EE9FFB17 ([EE9FFB17]): (54.22, 18.67) from 6 neighbors
```

## Coordinate Validation

Flag nodes whose measured SNR values don't match what the propagation model predicts for their position. Prints warnings only, doesn't change data.

```bash
python3 tools/convert_topology.py topology.json --validate-coords -v -o out.json
```

## Bidirectional Edge Merging

The raw topology has directed edges (A->B and B->A as separate entries). `--merge-bidir` combines matching pairs into single edges with averaged SNR/RSSI.

```bash
python3 tools/convert_topology.py topology.json --merge-bidir -o out.json
```

Results in three edge types:
- **bidir=true** -- both directions observed, SNR/RSSI averaged
- **bidir=false** -- only one direction observed (asymmetric)
- **bidir=true (companion)** -- companion links pass through unchanged

## Gap-Fill

Adds estimated edges between nearby nodes that lack observed connections. Uses a propagation model fitted from measured edges plus log-normal shadow fading. **On by default.**

Candidates are sorted globally by distance (closest first), so nearby pairs get connected before distant ones consume per-node caps.

```bash
# Disable gap-fill
python3 tools/convert_topology.py topology.json --no-fill-gaps -o out.json

# Tune gap-fill parameters
python3 tools/convert_topology.py topology.json \
  --max-gap-km 20 \
  --max-good-links 4 \
  --gap-sigma 6.0 \
  -o out.json
```

Gap-fill edges are always `bidir: true`. Output is deterministic (seeded RNG).

Caps per node:
- `--max-good-links` (default 3): max SNR > 0 dB edges
- Hard cap: 12 total edges per node

## Adding Companions

```bash
python3 tools/convert_topology.py topology.json \
  --add-companion alice:GD_Swibno_rpt \
  --add-companion bob:RPT_PRG_02 \
  -o out.json
```

Format: `name:repeater_name[:snr[:rssi]]` (defaults: snr=10, rssi=-70).

## Message Schedules

```bash
python3 tools/convert_topology.py topology.json \
  --add-companion alice:GD_Swibno_rpt \
  --add-companion bob:RPT_PRG_02 \
  --msg-schedule alice:bob:30 \
  -o out.json
```

Format: `from:to:interval_seconds`.

## Full Pipeline Example

```bash
python3 tools/convert_topology.py simulation/topology.json \
  --merge-bidir \
  --add-companion alice:GD_Swibno_rpt \
  --add-companion bob:RPT_PRG_02 \
  --msg-schedule alice:bob:30 \
  -v \
  -o simulation/real_network.json
```

## Simulation Parameters

| Flag | Default | Description |
|------|---------|-------------|
| `--duration` | 300000 | Simulation duration (ms) |
| `--step` | 5 | Simulation step (ms) |
| `--warmup` | 5000 | Warmup period (ms) |
| `--hot-start` | true | Enable hot start |
| `--hot-start-settle` | 15000 | Hot start settle time (ms) |

## Pipeline Order

```
topology.json
  -> Parse nodes
  -> Estimate coordinates (default on)
  -> Filter + convert edges
  -> Fit propagation model (unless --no-fill-gaps and no --validate-coords)
  -> Validate coordinates (if --validate-coords)
  -> Merge bidir edges (if --merge-bidir)
  -> Fill gaps (default on, disable with --no-fill-gaps)
  -> Inject companions
  -> Assemble output
```

Order matters: merge runs before gap-fill so edge counts aren't double-counted.
