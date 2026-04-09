# Orchestrator Config Format

The orchestrator reads a single JSON file that defines the network, radio links, and test scenario.

## Minimal Example

```json
{
  "simulation": { "duration_ms": 15000, "step_ms": 4, "warmup_ms": 5000, "hot_start": true },
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
| `step_ms` | int | 1 | Simulation time step in milliseconds. **Changed from 5ms to 1ms** for hardware delay accuracy. Values > 1 trigger warning and auto-clamp to 1ms. |
| `epoch_start` | int | 1700000000 | Unix epoch for simulated RTC |
| `warmup_ms` | int | 0 | During warmup, packets are delivered instantly (no physics). Must be < `duration_ms`. |
| `hot_start` | bool | false | Inject mutual node awareness at t=0 (skips slow advert propagation). Settle time is auto-detected via quiescence. |
| `seed` | int | 42 | RNG seed for all randomness (stagger, SNR variance, link loss, adversarial, per-node MeshCore). Change for Monte Carlo runs. |
| `radio.sf` | int | 8 | Global default LoRa spreading factor (7-12). Applied to all nodes unless overridden per-node. |
| `radio.bw` | int | 62500 | Global default bandwidth in Hz. |
| `radio.cr` | int | 1 | Global default coding rate (1-4). Maps to CR 4/(4+cr): 1=CR4/5 (MeshCore default), 4=CR4/8. |
| `radio.capture_locked_db` | float | 3.0 | Capture threshold (dB) when receiver has locked onto a preamble. See [RADIO_MODEL.md](RADIO_MODEL.md) sec 4.1. |
| `radio.capture_unlocked_db` | float | 6.0 | Capture threshold (dB) when preambles overlap (no lock). |
| `radio.cad_miss_prob` | float | 0.05 | CAD base false-negative probability [0.0-1.0] at high SNR. Actual miss rate is SNR-dependent (see below). 0 = perfect CAD. |
| `radio.cad_reliable_snr` | float | 0.0 | SNR threshold (dB) above which the base `cad_miss_prob` applies. Between this and `cad_marginal_snr`, miss rate interpolates linearly. |
| `radio.cad_marginal_snr` | float | -15.0 | SNR threshold (dB) below which CAD always misses (miss rate = 1.0). Must be <= `cad_reliable_snr`. |
| `radio.snr_coherence_ms` | float | 0.0 | Fading coherence time for Ornstein-Uhlenbeck correlated fading. 0 = i.i.d. Gaussian (original behavior). Fading is reciprocal (same offset both directions). Requires `snr_std_dev > 0` on links to have effect. |

### simulation.radio.hardware (optional)

Hardware turnaround delays for RX↔TX transitions. Models SX1262 transceiver (Heltec V3, Seeed Xiao nRF52).

**Defaults (if section omitted):** `rx_to_tx_delay_ms: 1.0`, `tx_to_rx_delay_ms: 5.0`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `rx_to_tx_delay_ms` | float | 1.0 | RX→TX switching time (PA ramp + mode switching) |
| `tx_to_rx_delay_ms` | float | 5.0 | TX→RX switching time (STANDBY_RC oscillator stabilization) |

**Example:**
```json
{
  "simulation": {
    "radio": {
      "hardware": {
        "rx_to_tx_delay_ms": 1.0,
        "tx_to_rx_delay_ms": 5.0
      }
    }
  }
}
```

**Note:** Set both to 0.0 for idealized testing only (not representative of real hardware).

**⚠️ Important:** To accurately model the 1ms RX→TX delay, `step_ms` must be **≤ 1ms**. The orchestrator validates this at startup and emits a warning if `step_ms > 1`, automatically clamping to 1ms.

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
| `tx_fail_prob` | float | 0.0 | TX failure probability [0.0-1.0]. Models SPI/hardware errors per RadioLib error path. When `startSendRaw()` fails, the radio transitions to IDLE and the packet is dropped (or requeued with MeshCore PR #2141). Generates `tx_fail` NDJSON events. |

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
| `snr_std_dev` | float | 0.0 | Per-reception SNR jitter std dev in dB (0 = deterministic). When `snr_coherence_ms > 0`, uses O-U correlated fading; otherwise i.i.d. Gaussian. |
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
| `node` | string | Target node name, or `@repeaters`, `@companions`, `@all` (see below) |
| `command` | string | CLI command string (see below) |

**Group targeting**: The `node` field accepts special `@` prefixes that expand to one command per matching node at parse time:

| Target        | Expands to                         |
| ------------- | ---------------------------------- |
| `@all`        | Every node                         |
| `@repeaters`  | All nodes with `role: "repeater"`  |
| `@companions` | All nodes with `role: "companion"` |

```json
{ "at_ms": 100000, "node": "@repeaters", "command": "set txdelay 0.1" }
```

This is equivalent to writing one command per repeater node. Useful for applying global settings without listing each node.

**Available commands** depend on node role:

| Command | Role | Description |
|---|---|---|
| `msg <name> <text>` | companion | Send direct/flood message to named contact. Uses direct routing if a path is known, flood otherwise. |
| `msga <name> <text>` | companion | Same as `msg` but tracks ack receipt |
| `msgc <text>` | companion | Send message on public channel (channel 0, flood). No ack support. |
| `advert` | companion | Broadcast self-advert (flood) |
| `advert.zerohop` | companion | Broadcast self-advert (zero-hop only) |
| `reset_path <name>` | companion | Clear learned direct route to named contact. Forces next `msg` to use flood routing (re-discovers path). Equivalent to MeshCore's `CMD_RESET_PATH` binary frame. |
| `neighbors` | companion | List known contacts |
| `stats` | companion | Print message send/receive counters |
| `import <hex>` | companion | Import a contact from hex-encoded advert |
| `get rxdelay` | repeater | Query RX delay base (float, default 0.0) |
| `set rxdelay <f>` | repeater | Set RX delay base (>= 0). Disables autotune. |
| `get txdelay` | repeater | Query TX delay factor (float, default 0.5) |
| `set txdelay <f>` | repeater | Set TX delay factor (>= 0). Controls flood retransmit delay: `random(0, 5 * airtime * factor)`. Disables autotune. |
| `get direct.txdelay` | repeater | Query direct TX delay factor (float, default 0.3) |
| `set direct.txdelay <f>` | repeater | Set direct TX delay factor (>= 0). Same formula as txdelay but for direct-route relays. Disables autotune. |
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

**Countable event types** (usable with `event_count` and `event_count_min`):

| Event type | Description |
|---|---|
| `tx` | Packet transmitted |
| `rx` | Packet successfully received |
| `collision` | Packet lost to collision (interfering signal within capture threshold) |
| `drop_weak` | Packet dropped: SNR below demodulation threshold |
| `drop_halfduplex` | Packet dropped: receiver was transmitting (half-duplex) |
| `drop_loss` | Packet dropped: stochastic link loss (per-link `loss` probability) |
| `tx_fail` | TX hardware failure (per-node `tx_fail_prob`) |

Additional NDJSON event types (logged but not counted for assertions):

| Event type | Description |
|---|---|
| `sim_start` | Simulation start metadata |
| `sim_end` | Simulation end marker |
| `node_ready` | Node initialized (includes pub key, optional lat/lon) |
| `cmd_reply` | Command response from a node |
| `adversarial_drop` | Adversarial node suppressed a TX |
| `adversarial_corrupt` | Adversarial node flipped bits in a TX |
| `adversarial_replay` | Adversarial node scheduled a packet replay |

---

## Stderr Summary Output

After the simulation completes, the orchestrator prints a summary to stderr. This includes radio stats, per-node message delivery, and (when `msg`/`msga` commands are present) message fate diagnostics.

### Delivery summary

```
=== Simulation Summary (22.0min) ===
Radio: 4521 TX, 6832 RX

Sent messages:
  alice        6 (flood:2 direct:4 group:0)
    -> bob        3 sent, 2 delivered (67%)
    -> carol      3 sent, 3 delivered (100%)

Received messages:
  bob          2  <-  alice:2
  carol        3  <-  alice:3

Delivery: 5/6 messages (83%)
Channel: 12/18 receptions (67%)
Acks: 3/5 received (60%)
```

### Message fate

When `msg` or `msga` commands are used, the orchestrator tracks each message through the relay chain and reports per-message collision/drop means:

```
Message fate (50 tracked, 11 delivered, 39 lost):
  Per delivered message: mean tx=194.2  rx=325.5  collision=290.2  drop=0.8
  Per lost message:      mean tx=43.4  rx=65.0  collision=50.3  drop=0.6
```

| Field | Meaning |
|-------|---------|
| `tracked` | Number of `msg`/`msga` commands that were fate-tracked |
| `delivered` | Messages where a tracked packet reached the destination |
| `lost` | Messages where no tracked packet reached the destination |
| `tx` | Mean number of transmissions (including relays) per message |
| `rx` | Mean successful receptions per message |
| `collision` | Mean packet collisions per message |
| `drop` | Mean drops (half-duplex + link loss) per message |

High `collision` relative to `tx` indicates congestion; high `drop` relative to `rx` indicates link-quality issues. Channel messages (`msgc`) are not tracked.

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
| `--min-snr` | -10.0 | Drop edges below this SNR |
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
- **Hover** over TX/RX bars to see decoded packet info: msg_src, msg_dst, and **path** (the repeater hash chain). Flood packets show the growing path `[B → C → D]`; direct packets show remaining hops `[E → B]`.
- Sidebar shows packet spread tree, event details, decoded path, and node stats.
