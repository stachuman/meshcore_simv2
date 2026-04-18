# Tools

Standalone Python scripts for preparing simulation inputs.

## convert_topology.py

Converts a real MeshCore network's `topology.json` (exported node list + directed SNR edges) into orchestrator config format.

**Prerequisite:** `simulation/topology.json` is not committed to the repo. Generate one first (fetches live data from the MeshCore API):

```bash
python3 -m topology_generator --region 53.7,17.3,54.8,19.5 -o simulation/topology.json
```

Then convert:

```bash
# Basic: convert topology with default filtering
python3 tools/convert_topology.py simulation/topology.json -o simulation/real_network.json

# Full: add companions, message schedule, custom filtering
python3 tools/convert_topology.py simulation/topology.json \
  --add-companion alice:GD_Swibno_rpt \
  --add-companion bob:RPT_PRG_02 \
  --msg-schedule alice:bob:30 \
  --min-snr -10 \
  --include-inferred \
  -o simulation/real_network.json
```

What it does:
- All topology nodes become repeaters with their lat/lon preserved
- Edges are kept **directional** (`bidir: false`) -- asymmetry between A->B and B->A is preserved exactly
- RSSI estimated from `receiver_noise_floor + snr_db`
- SNR variance from `(snr_db - snr_min_db) / 2`
- Companions injected via `--add-companion`, linked to a named repeater
- Periodic messages via `--msg-schedule from:to:interval_s`

Filtering (per-direction, so A->B may survive while B->A is dropped):
- `--min-snr` (default -7.5) -- drop edges below this SNR
- `--min-confidence` (default 0.7) -- drop low-confidence edges
- `--include-inferred` (default off) -- include `source: "inferred"` edges

Run with `--help` for all options.

## gen_grid_test.py

Generates test configs with grid topologies and random companion placement.

```bash
# 10x10 grid, 20 companions, default output to stdout
python3 tools/gen_grid_test.py

# 5x5 grid, 8 companions, save to file
python3 tools/gen_grid_test.py --rows 5 --cols 5 -n 8 -o test/t_custom.json
```

Useful for stress-testing routing with controlled, regular topologies.
