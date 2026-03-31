# MeshCore Real Sim

A single-process network simulator for [MeshCore](https://github.com/ripplebiz/MeshCore) LoRa mesh networks. Runs unmodified MeshCore firmware code against a configurable virtual radio layer with realistic RF propagation, collisions, and half-duplex constraints.

## Building

Requires CMake 3.16+, a C++17 compiler, and OpenSSL.

```bash
cmake -S . -B build
cmake --build build
```

This produces three binaries:
- `build/orchestrator/orchestrator` -- the multi-node simulator (main tool)
- `build/simple_repeater/simple_repeater` -- standalone single-repeater binary
- `build/companion_radio/companion_radio` -- standalone single-companion binary

## Quick Start

### Run a test config

```bash
build/orchestrator/orchestrator test/t02_hot_start_msg.json
```

NDJSON event log goes to stdout, summary and assertions to stderr.

### Run with visualization

```bash
tools/run_sim.sh test/t06_msg_stats.json
```

This runs the orchestrator, saves events as `t06_msg_stats_events.ndjson`, and opens an interactive swim-lane visualizer in the browser (plus a topology map view if the config has coordinates).

### Run all tests

```bash
bash test/run_tests.sh
```

Discovers all `test/t*.json` files and reports pass/fail.

## Simulation Config

Configs are JSON files with these sections:

```json
{
  "simulation": {
    "duration_ms": 90000,
    "step_ms": 5,
    "warmup_ms": 5000,
    "hot_start": true
  },
  "nodes": [
    { "name": "alice", "role": "companion" },
    { "name": "relay1", "role": "repeater" },
    { "name": "bob", "role": "companion" }
  ],
  "topology": {
    "links": [
      { "from": "alice", "to": "relay1", "snr": 8.0, "rssi": -80.0, "bidir": true },
      { "from": "relay1", "to": "bob", "snr": 8.0, "rssi": -80.0, "bidir": true }
    ]
  },
  "commands": [
    { "at_ms": 6000, "node": "alice", "command": "msg bob hello" }
  ],
  "expect": [
    { "type": "cmd_reply_contains", "node": "alice", "command": "msg bob", "value": "msg sent to bob" }
  ]
}
```

- **`hot_start`** -- injects mutual node awareness at t=0 (skips the slow advert exchange)
- **`warmup_ms`** -- instant packet delivery during warmup (no collisions/physics)
- **`message_schedule`** -- auto-generates periodic `msg`/`msga` commands (supports `"ack": true`)

See [docs/CONFIG_FORMAT.md](docs/CONFIG_FORMAT.md) for full reference.

## Working with Real Topologies

The `simulation/` directory contains data from a real MeshCore network:

| File | Description |
|---|---|
| `simulation/topology.json` | Raw network export -- nodes with GPS, edges with measured SNR/RSSI |
| `simulation/real_network.json` | Pre-converted orchestrator config ready to simulate |

### Converting a topology export

```bash
python3 tools/convert_topology.py simulation/topology.json \
    --add-companion alice:567EBBCC \
    --add-companion bob:05FE75AB \
    --msg-schedule alice:bob:30000 \
    -o my_sim.json
```

This converts raw topology data into a simulator config:
- Filters out stub nodes and weak links
- Estimates GPS coordinates for nodes missing them
- Fills connectivity gaps using a fitted path-loss model (on by default)
- Injects companion nodes attached to specified repeaters
- Generates periodic message commands

Key flags:
| Flag | Default | Description |
|---|---|---|
| `--min-snr` | -7.5 | Drop links below this SNR |
| `--fill-gaps` / `--no-fill-gaps` | on | Estimate edges for nearby unmeasured pairs |
| `--merge-bidir` | off | Merge A->B + B->A into single bidirectional links |
| `--estimate-coords` | on | Infer GPS for nodes with missing coordinates |
| `--validate-coords` | off | Flag nodes with suspicious coordinates |
| `-v` | off | Print detailed statistics |

See [docs/CONVERT_TOPOLOGY.md](docs/CONVERT_TOPOLOGY.md) for full documentation.

### Running the real network simulation

```bash
# Use the pre-converted config directly
tools/run_sim.sh simulation/real_network.json

# Or convert with custom companions and run
python3 tools/convert_topology.py simulation/topology.json \
    --add-companion alice:567EBBCC \
    --add-companion bob:GDA1R \
    --msg-schedule alice:bob:30000 \
    -o /tmp/my_network.json
tools/run_sim.sh /tmp/my_network.json -v
```

## Radio Physics Model

The simulator models realistic LoRa radio behavior:

- **LoRa airtime** -- exact symbol/preamble/payload timing for any SF/BW/CR combination
- **Half-duplex** -- nodes cannot transmit while receiving (and vice versa)
- **Collisions** -- 3-stage survival: capture effect (6dB), preamble grace window, FEC tolerance
- **Listen-before-talk** -- preamble detection delay, SNR-gated channel busy notifications
- **SNR variance** -- per-link Gaussian sampling (`snr_std_dev`)
- **Stochastic loss** -- per-link drop probability (`loss`)
- **Adversarial modes** -- per-node packet drop, bit corruption, or delayed replay

## Visualization

The visualizer serves two interactive views:

```bash
python3 visualization/visualize.py events.ndjson --config config.json
```

- **Swim-lane view** (port 8000) -- timeline of TX/RX/collision events per node, with packet tracing
- **Map view** (port 8001) -- geographic topology with link SNR coloring (requires `--config` with node coordinates)

Controls: scroll to zoom, drag to pan, click packets for details, press `T` for spread-tree trace.

## Test Generator

Generate grid topology tests with configurable dimensions:

```bash
python3 tools/gen_grid_test.py --rows 5 --cols 5 -n 4 -o test/t_custom_grid.json
```

Creates a repeater grid with companion nodes at the corners, auto-generates cross-grid messaging commands and discovery assertions.

## Project Structure

```
MeshCore/           MeshCore firmware sources (read-only, never modified)
shims/              Platform shim layer (Arduino, FS, crypto, radio)
orchestrator/       Multi-node simulator engine
  Orchestrator.cpp  Main simulation loop, physics, collision detection
  JsonConfig.cpp    Config parser
  CompanionNode.cpp Companion mesh node factory
  RepeaterNode.cpp  Repeater mesh node factory
simple_repeater/    Standalone single-repeater binary
companion_radio/    Standalone single-companion binary
simulation/         Real-world topology data
test/               Test configs (t*.json) and runner
tools/              Conversion, generation, and run scripts
visualization/      Interactive event visualizer
docs/               Config format reference
```
