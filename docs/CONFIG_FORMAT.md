# Orchestrator Config Format

The orchestrator reads a single JSON file that defines the network, radio links, and test scenario.

## Minimal Example

```json
{
  "simulation": { "duration_ms": 15000, "step_ms": 5, "warmup_ms": 5000, "hot_start": true },
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
  ]
}
```

---

## Sections

### `simulation`

| Field | Type | Default | Description |
|---|---|---|---|
| `duration_ms` | int | 300000 | Total simulation time |
| `step_ms` | int | 5 | Time resolution per tick |
| `epoch_start` | int | 1700000000 | Unix epoch for simulated RTC |
| `warmup_ms` | int | 0 | During warmup, packets are delivered instantly (no physics). Must be < `duration_ms`. |
| `hot_start` | bool | false | Inject mutual node awareness at t=0 (skips slow advert propagation). Settle time is auto-detected via quiescence. |
| `seed` | int | 42 | RNG seed for all randomness (stagger, SNR variance, link loss, adversarial, per-node MeshCore). Change for Monte Carlo runs. |
| `radio.sf` | int | 8 | Global default LoRa spreading factor (7-12). Applied to all nodes unless overridden per-node. |
| `radio.bw` | int | 62500 | Global default bandwidth in Hz. |
| `radio.cr` | int | 4 | Global default coding rate (1-4). |

**Typical setup**: For message-passing tests, use `hot_start: true` with `warmup_ms` long enough for the hot-start advert exchange to complete. Commands should fire after warmup ends.

### `nodes`

Array of node definitions. Each node becomes an independent MeshCore instance.

```json
{ "name": "relay1", "role": "repeater", "lat": 54.329, "lon": 18.933 }
```

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | string | *required* | Unique node identifier. Used in links, commands, and output events. |
| `role` | string | `"repeater"` | `"repeater"` or `"companion"`. Repeaters forward packets; companions send/receive messages. |
| `lat` | float | *(omit)* | Latitude (WGS84). Optional. Passed through to `node_ready` output event for analysis/visualization. |
| `lon` | float | *(omit)* | Longitude (WGS84). Optional. Must appear together with `lat`. |
| `radio.sf` | int | *(global)* | Per-node override for LoRa spreading factor (7-12). Falls back to `simulation.radio.sf`. |
| `radio.bw` | int | *(global)* | Per-node override for bandwidth in Hz. Falls back to `simulation.radio.bw`. |
| `radio.cr` | int | *(global)* | Per-node override for coding rate (1-4). Falls back to `simulation.radio.cr`. |

**Adversarial testing** (optional per-node):

```json
{
  "name": "evil",
  "role": "repeater",
  "adversarial": {
    "mode": "drop",
    "probability": 0.5
  }
}
```

| Field | Default | Description |
|---|---|---|
| `adversarial.mode` | none | `"drop"` (suppress TX), `"corrupt"` (flip bits), or `"replay"` (retransmit old packets) |
| `adversarial.probability` | 1.0 | Chance [0.0-1.0] the adversarial action triggers per packet |
| `adversarial.corrupt_bits` | 1 | Number of random bits to flip (corrupt mode only) |
| `adversarial.replay_delay_ms` | 5000 | Delay before replaying captured packet (replay mode only) |

### `topology.links`

Each entry defines a radio link between two nodes.

```json
{ "from": "A", "to": "B", "snr": 8.0, "rssi": -80.0, "bidir": true }
```

| Field | Type | Default | Description |
|---|---|---|---|
| `from` | string | *required* | Source node name |
| `to` | string | *required* | Destination node name |
| `snr` | float | 8.0 | Signal-to-noise ratio in dB |
| `rssi` | float | -80.0 | Received signal strength in dBm |
| `snr_std_dev` | float | 0.0 | Per-reception Gaussian jitter on SNR (0 = deterministic) |
| `loss` | float | 0.0 | Packet loss probability [0.0-1.0], applied after collision detection |
| `bidir` | bool | true | If true, creates both A->B and B->A with same parameters. If false, only from->to. |

**Asymmetric links**: Real-world RF links are often asymmetric. Set `bidir: false` and define each direction separately:

```json
{ "from": "A", "to": "B", "snr": 12.0, "rssi": -75.0, "bidir": false },
{ "from": "B", "to": "A", "snr": -2.0, "rssi": -110.0, "bidir": false }
```

**No link = no communication.** Nodes without a link between them cannot hear each other at all.

### `commands`

One-shot commands executed at specific simulation times. Sorted by `at_ms` at runtime.

```json
{ "at_ms": 6000, "node": "alice", "command": "msg bob hello from alice" }
```

| Field | Type | Description |
|---|---|---|
| `at_ms` | int | Simulation time to execute (ms) |
| `node` | string | Target node name |
| `command` | string | CLI command string (see below) |

**Available commands** depend on node role:

| Command | Role | Description |
|---|---|---|
| `msg <name> <text>` | companion | Send direct/flood message to named contact |
| `msga <name> <text>` | companion | Same as `msg` but tracks ack receipt |
| `msgc <text>` | companion | Send message on public channel (channel 0, flood). No ack support. |
| `advert` | companion | Broadcast self-advert (flood) |
| `advert.zerohop` | companion | Broadcast self-advert (zero-hop only) |
| `neighbors` | companion | List known contacts |
| `stats` | companion | Print message send/receive counters |
| `import <hex>` | companion | Import a contact from hex-encoded advert |
| `get rxdelay` | repeater | Query RX delay base (float, default 0.0) |
| `set rxdelay <f>` | repeater | Set RX delay base (>= 0) |
| `get txdelay` | repeater | Query TX delay factor (float, default 0.5) |
| `set txdelay <f>` | repeater | Set TX delay factor (>= 0) |
| `get direct.txdelay` | repeater | Query direct TX delay factor (float, default 0.3) |
| `set direct.txdelay <f>` | repeater | Set direct TX delay factor (>= 0) |
| *(any CLI)* | repeater | Passed through to MeshCore's `CommonCLI::handleCommand` |

### `message_schedule`

Generates periodic `msg` commands automatically. Expanded into `commands` entries at parse time.

```json
{
  "message_schedule": [
    {
      "from": "alice",
      "to": "bob",
      "start_ms": 10000,
      "interval_ms": 30000,
      "count": 10,
      "message": "periodic test #{n}"
    }
  ]
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `from` | string | *required* | Sending companion node name |
| `to` | string | *required* | Receiving companion node name |
| `start_ms` | int | 10000 | Time of first message |
| `interval_ms` | int | *required* | Period between messages |
| `count` | int | *auto* | Number of messages. If omitted: fills until `duration_ms - 10000` |
| `message` | string | `"test msg {n}"` | Text template. `{n}` is replaced with 1-based sequence number. |
| `ack` | bool | `false` | If true, generates `msga` commands (message with ack tracking) instead of `msg`. |

Each entry expands to commands like:
```
at_ms=10000  node=alice  command="msg bob periodic test #1"
at_ms=40000  node=alice  command="msg bob periodic test #2"
...
```

### `channel_schedule`

Generates periodic `msgc` (public channel) commands. Works like `message_schedule` but for broadcast channel messages.

```json
{
  "channel_schedule": [
    {
      "from": "alice",
      "channel": 0,
      "start_ms": 10000,
      "interval_ms": 30000,
      "count": 5,
      "message": "chan hello #{n}"
    }
  ]
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `from` | string | *required* | Sending companion node name |
| `channel` | int | 0 | Channel index (only 0 = public channel currently supported) |
| `start_ms` | int | 10000 | Time of first message |
| `interval_ms` | int | *required* | Period between messages |
| `count` | int | *auto* | Number of messages. If omitted: fills until `duration_ms - 10000` |
| `message` | string | `"channel msg {n}"` | Text template. `{n}` is replaced with 1-based sequence number. |

Channel messages use flood routing (no direct path). No ack support — delivery is tracked by counting receptions.

**Delivery metric**: Each channel message should reach all other companions. Expected receptions = `sent * (N_companions - 1)`. The summary reports `Channel: X/Y receptions (P%)`.

### `expect`

Test assertions checked after the simulation completes. The orchestrator exits with code 0 if all pass, 1 otherwise.

```json
{ "type": "cmd_reply_contains", "node": "alice", "command": "msg bob", "value": "msg sent to bob" }
{ "type": "cmd_reply_not_contains", "node": "alice", "command": "msg bob", "value": "ERROR" }
{ "type": "event_count_min", "value": "tx", "count": 5 }
```

| Type | Fields | Checks |
|---|---|---|
| `cmd_reply_contains` | `node`, `command`, `value` | Some command reply from `node` matching prefix `command` contains substring `value` |
| `cmd_reply_not_contains` | `node`, `command`, `value` | No matching reply contains `value` |
| `event_count_min` | `value`, `count` | Total events of type `value` (e.g. `"tx"`, `"rx"`) >= `count` |
| `event_count` | `event_type`, `node` (opt), `min`/`max` | Event count in [`min`, `max`] range. `node` scopes to one node. -1 = no bound. |
| `tx_airtime_between` | `node`, `min`, `max` | All TX airtimes from `node` fall within [`min`, `max`] ms |

---

## Converting Real Topology Data

`tools/convert_topology.py` converts a real MeshCore network's `topology.json` (node list + directed SNR edges) into this config format.

```bash
python3 tools/convert_topology.py simulation/topology.json \
  --add-companion alice:GD_Swibno_rpt \
  --add-companion bob:RPT_PRG_02 \
  --msg-schedule alice:bob:30 \
  -o simulation/real_network.json
```

Key behaviors:
- All topology nodes become repeaters. Companions are injected with `--add-companion`.
- Edges are **directional** (`bidir: false`). A->B and B->A are separate links with their own SNR.
- RSSI estimated from `receiver_noise_floor + snr_db` (clamped to [-140, -20]).
- SNR variance derived from `(snr_db - snr_min_db) / 2` when both values exist.

Filtering flags:
| Flag | Default | Effect |
|---|---|---|
| `--min-snr` | -7.5 | Drop edges below this SNR |
| `--min-confidence` | 0.7 | Drop edges below this confidence |
| `--include-inferred` | off | Include `source: "inferred"` edges (dropped by default) |

Simulation flags: `--duration`, `--step`, `--warmup`.

---

## Visualizing Output

```bash
# Run simulation, capture output
build/orchestrator/orchestrator config.json > output.ndjson

# Launch interactive visualizer
python3 visualization/visualize.py output.ndjson
```

Opens a browser with an interactive swim-lane view:
- **Scroll** to zoom time axis, **drag** to pan
- **Click** a node label for node details
- **T** key or Trace button to enter trace mode — click any packet to see its full journey
- **Arrow keys** to scroll time, **F** to fit all, **Esc** to clear
- Zoomed out: shows per-node activity density. Zoomed in: individual packets with airtime boxes, RX markers, collision X marks.
- Sidebar shows packet spread tree, event details, and node stats.
