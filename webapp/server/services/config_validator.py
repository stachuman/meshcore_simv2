"""
JSON config validator for MeshCore orchestrator configs.

Validates structure, node definitions, topology links, simulation parameters,
commands, and schedule entries. Collects all errors and warnings rather than
stopping at the first problem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Known top-level keys the orchestrator accepts (including _name/_desc metadata).
_KNOWN_TOP_KEYS = {
    "simulation", "nodes", "topology", "commands", "expect",
    "message_schedule", "channel_schedule",
    "_name", "_desc", "_requires_plugins",
}

_VALID_ROLES = {"repeater", "companion"}
_VALID_GROUP_TARGETS = {"@all", "@repeaters", "@companions"}
_VALID_ADVERSARIAL_MODES = {"drop", "corrupt", "replay"}


@dataclass
class ValidationResult:
    valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def _error(self, msg: str) -> None:
        self.errors.append(msg)
        self.valid = False

    def _warn(self, msg: str) -> None:
        self.warnings.append(msg)


def validate_config(config: dict) -> ValidationResult:
    """Validate an orchestrator config dict. Returns errors and warnings."""
    result = ValidationResult()

    # ── Top-level structure ──────────────────────────────────────────
    if not isinstance(config, dict):
        result._error("Config must be a JSON object (dict)")
        return result

    unknown_keys = set(config.keys()) - _KNOWN_TOP_KEYS
    if unknown_keys:
        result._warn(f"Unknown top-level keys: {sorted(unknown_keys)}")

    # ── Nodes (required) ─────────────────────────────────────────────
    if "nodes" not in config:
        result._error("Missing required key 'nodes'")
        node_names: set[str] = set()
        repeater_names: set[str] = set()
    else:
        node_names, repeater_names = _validate_nodes(config["nodes"], result)

    # ── Topology (required) ──────────────────────────────────────────
    if "topology" not in config:
        result._error("Missing required key 'topology'")
    else:
        _validate_topology(config["topology"], node_names, result)

    # ── Simulation (optional) ────────────────────────────────────────
    duration_ms: int | None = None
    if "simulation" in config:
        duration_ms = _validate_simulation(config["simulation"], result)

    # ── Commands (optional) ──────────────────────────────────────────
    if "commands" in config:
        _validate_commands(config["commands"], node_names, duration_ms, result)

    # ── Expect / assertions (optional) ───────────────────────────────
    if "expect" in config:
        _validate_expect(config["expect"], node_names, result)

    # ── Message schedule (optional) ──────────────────────────────────
    if "message_schedule" in config:
        _validate_message_schedule(
            config["message_schedule"], node_names, duration_ms, result,
        )

    # ── Channel schedule (optional) ──────────────────────────────────
    if "channel_schedule" in config:
        _validate_channel_schedule(
            config["channel_schedule"], node_names, duration_ms, result,
        )

    return result


# ── Section validators ───────────────────────────────────────────────


def _validate_nodes(
    nodes: Any, result: ValidationResult,
) -> tuple[set[str], set[str]]:
    """Validate nodes list. Returns (all_node_names, repeater_names)."""
    node_names: set[str] = set()
    repeater_names: set[str] = set()

    if not isinstance(nodes, list):
        result._error("'nodes' must be a list")
        return node_names, repeater_names
    if len(nodes) == 0:
        result._error("'nodes' must not be empty")
        return node_names, repeater_names

    for i, node in enumerate(nodes):
        pfx = f"nodes[{i}]"
        if not isinstance(node, dict):
            result._error(f"{pfx}: must be an object")
            continue

        # name
        name = node.get("name")
        if name is None:
            result._error(f"{pfx}: missing required field 'name'")
        elif not isinstance(name, str) or name == "":
            result._error(f"{pfx}: 'name' must be a non-empty string")
        else:
            pfx = f"node '{name}'"
            if name in node_names:
                result._error(f"{pfx}: duplicate node name")
            node_names.add(name)

        # role
        role = node.get("role")
        if role is None:
            result._error(f"{pfx}: missing required field 'role'")
        elif not isinstance(role, str) or role not in _VALID_ROLES:
            result._error(
                f"{pfx}: 'role' must be 'repeater' or 'companion', "
                f"got {role!r}"
            )
        else:
            if role == "repeater" and isinstance(name, str):
                repeater_names.add(name)

        # contact (companions only — warn if missing)
        if role == "companion":
            contact = node.get("contact")
            if contact is not None:
                if not isinstance(contact, str) or contact == "":
                    result._error(f"{pfx}: 'contact' must be a non-empty string")
                # Cross-ref validated after all nodes are parsed (below).

        # lat/lon
        has_lat = "lat" in node
        has_lon = "lon" in node
        if has_lat != has_lon:
            result._warn(f"{pfx}: 'lat' and 'lon' should both be present or both absent")
        if has_lat and not isinstance(node["lat"], (int, float)):
            result._error(f"{pfx}: 'lat' must be a number")
        if has_lon and not isinstance(node["lon"], (int, float)):
            result._error(f"{pfx}: 'lon' must be a number")

        # radio overrides (flat or nested)
        _validate_radio_params(node, pfx, result)
        if "radio" in node and isinstance(node["radio"], dict):
            _validate_radio_params(node["radio"], f"{pfx}.radio", result)

        # tx_fail_prob
        if "tx_fail_prob" in node:
            _validate_probability(node["tx_fail_prob"], f"{pfx}.tx_fail_prob", result)

        # adversarial
        if "adversarial" in node:
            _validate_adversarial(node["adversarial"], pfx, result)

        # firmware override
        if "firmware" in node:
            fw = node["firmware"]
            if not isinstance(fw, str) or fw == "":
                result._error(f"{pfx}: 'firmware' must be a non-empty string")
            elif not fw.startswith("fw_"):
                result._warn(f"{pfx}: firmware '{fw}' doesn't start with 'fw_' — expected format: fw_<name>")

    # Second pass: validate contact references
    for node in nodes:
        if not isinstance(node, dict):
            continue
        contact = node.get("contact")
        if isinstance(contact, str) and contact != "":
            if contact not in repeater_names:
                result._error(
                    f"node '{node.get('name', '?')}': 'contact' references "
                    f"'{contact}' which is not a repeater node"
                )

    return node_names, repeater_names


def _validate_radio_params(
    obj: dict, pfx: str, result: ValidationResult,
) -> None:
    """Validate sf/bw/cr fields if present in a dict."""
    if "sf" in obj:
        sf = obj["sf"]
        if not isinstance(sf, int):
            result._error(f"{pfx}: 'sf' must be an integer")
        elif sf < 7 or sf > 12:
            result._error(f"{pfx}: 'sf' must be 7-12, got {sf}")
    if "bw" in obj:
        bw = obj["bw"]
        if not isinstance(bw, int):
            result._error(f"{pfx}: 'bw' must be an integer")
        elif bw <= 0:
            result._error(f"{pfx}: 'bw' must be > 0, got {bw}")
    if "cr" in obj:
        cr = obj["cr"]
        if not isinstance(cr, int):
            result._error(f"{pfx}: 'cr' must be an integer")
        elif cr < 1 or cr > 4:
            result._error(f"{pfx}: 'cr' must be 1-4, got {cr}")


def _validate_adversarial(
    adv: Any, pfx: str, result: ValidationResult,
) -> None:
    """Validate adversarial config block on a node."""
    if not isinstance(adv, dict):
        result._error(f"{pfx}: 'adversarial' must be an object")
        return

    mode = adv.get("mode")
    if mode is not None and mode not in _VALID_ADVERSARIAL_MODES:
        result._error(
            f"{pfx}: adversarial.mode must be one of "
            f"{sorted(_VALID_ADVERSARIAL_MODES)}, got {mode!r}"
        )

    if "probability" in adv:
        _validate_probability(adv["probability"], f"{pfx}.adversarial.probability", result)

    if "corrupt_bits" in adv:
        cb = adv["corrupt_bits"]
        if not isinstance(cb, int) or cb <= 0:
            result._error(f"{pfx}: adversarial.corrupt_bits must be a positive integer")

    if "replay_delay_ms" in adv:
        rd = adv["replay_delay_ms"]
        if not isinstance(rd, (int, float)) or rd < 0:
            result._error(f"{pfx}: adversarial.replay_delay_ms must be >= 0")


def _validate_topology(
    topology: Any, node_names: set[str], result: ValidationResult,
) -> None:
    """Validate topology section."""
    if not isinstance(topology, dict):
        result._error("'topology' must be an object")
        return
    if "links" not in topology:
        result._error("'topology' must have a 'links' key")
        return

    links = topology["links"]
    if not isinstance(links, list):
        result._error("'topology.links' must be a list")
        return

    for i, link in enumerate(links):
        pfx = f"topology.links[{i}]"
        if not isinstance(link, dict):
            result._error(f"{pfx}: must be an object")
            continue

        # from / to
        frm = link.get("from")
        to = link.get("to")
        if frm is None:
            result._error(f"{pfx}: missing required field 'from'")
        elif not isinstance(frm, str):
            result._error(f"{pfx}: 'from' must be a string")
        elif frm not in node_names:
            result._error(f"{pfx}: 'from' references unknown node '{frm}'")

        if to is None:
            result._error(f"{pfx}: missing required field 'to'")
        elif not isinstance(to, str):
            result._error(f"{pfx}: 'to' must be a string")
        elif to not in node_names:
            result._error(f"{pfx}: 'to' references unknown node '{to}'")

        if (
            isinstance(frm, str) and isinstance(to, str) and frm == to
        ):
            result._error(f"{pfx}: 'from' and 'to' must not be the same node ('{frm}')")

        # snr (required)
        if "snr" not in link:
            result._error(f"{pfx}: missing required field 'snr'")
        elif not isinstance(link["snr"], (int, float)):
            result._error(f"{pfx}: 'snr' must be a number")

        # optional numeric fields
        if "rssi" in link and not isinstance(link["rssi"], (int, float)):
            result._error(f"{pfx}: 'rssi' must be a number")

        if "snr_std_dev" in link:
            sd = link["snr_std_dev"]
            if not isinstance(sd, (int, float)):
                result._error(f"{pfx}: 'snr_std_dev' must be a number")
            elif sd < 0:
                result._error(f"{pfx}: 'snr_std_dev' must be >= 0, got {sd}")

        if "loss" in link:
            _validate_probability(link["loss"], f"{pfx}.loss", result)

        if "bidir" in link and not isinstance(link["bidir"], bool):
            result._error(f"{pfx}: 'bidir' must be a boolean")


def _validate_simulation(
    sim: Any, result: ValidationResult,
) -> int | None:
    """Validate simulation section. Returns duration_ms if valid, else None."""
    if not isinstance(sim, dict):
        result._error("'simulation' must be an object")
        return None

    duration_ms: int | None = None

    # duration_ms (required when simulation section present)
    if "duration_ms" in sim:
        d = sim["duration_ms"]
        if not isinstance(d, int):
            result._error("simulation.duration_ms must be an integer")
        elif d <= 0:
            result._error(f"simulation.duration_ms must be > 0, got {d}")
        else:
            duration_ms = d
    else:
        result._warn("simulation.duration_ms not specified, will use orchestrator default (300000)")

    # step_ms
    if "step_ms" in sim:
        s = sim["step_ms"]
        if not isinstance(s, int):
            result._error("simulation.step_ms must be an integer")
        elif s <= 0:
            result._error(f"simulation.step_ms must be > 0, got {s}")

    # warmup_ms
    if "warmup_ms" in sim:
        w = sim["warmup_ms"]
        if not isinstance(w, int):
            result._error("simulation.warmup_ms must be an integer")
        elif w < 0:
            result._error(f"simulation.warmup_ms must be >= 0, got {w}")
        elif duration_ms is not None and w >= duration_ms:
            result._error(
                f"simulation.warmup_ms ({w}) must be < duration_ms ({duration_ms})"
            )

    # seed
    if "seed" in sim:
        seed = sim["seed"]
        if not isinstance(seed, int):
            result._error("simulation.seed must be an integer")

    # hot_start
    if "hot_start" in sim:
        if not isinstance(sim["hot_start"], bool):
            result._error("simulation.hot_start must be a boolean")

    # epoch_start
    if "epoch_start" in sim:
        if not isinstance(sim["epoch_start"], int):
            result._error("simulation.epoch_start must be an integer")

    # radio sub-section
    if "radio" in sim:
        _validate_sim_radio(sim["radio"], result)

    # firmware sub-section
    if "firmware" in sim:
        _validate_sim_firmware(sim["firmware"], result)

    return duration_ms


def _validate_sim_radio(radio: Any, result: ValidationResult) -> None:
    """Validate simulation.radio sub-section."""
    if not isinstance(radio, dict):
        result._error("simulation.radio must be an object")
        return

    _validate_radio_params(radio, "simulation.radio", result)

    if "capture_locked_db" in radio:
        v = radio["capture_locked_db"]
        if not isinstance(v, (int, float)):
            result._error("simulation.radio.capture_locked_db must be a number")
        elif v < 0:
            result._error(
                f"simulation.radio.capture_locked_db must be >= 0, got {v}"
            )

    if "capture_unlocked_db" in radio:
        v = radio["capture_unlocked_db"]
        if not isinstance(v, (int, float)):
            result._error("simulation.radio.capture_unlocked_db must be a number")
        elif v < 0:
            result._error(
                f"simulation.radio.capture_unlocked_db must be >= 0, got {v}"
            )

    if "cad_miss_prob" in radio:
        _validate_probability(
            radio["cad_miss_prob"], "simulation.radio.cad_miss_prob", result,
        )

    if "cad_reliable_snr" in radio:
        v = radio["cad_reliable_snr"]
        if not isinstance(v, (int, float)):
            result._error("simulation.radio.cad_reliable_snr must be a number")

    if "cad_marginal_snr" in radio:
        v = radio["cad_marginal_snr"]
        if not isinstance(v, (int, float)):
            result._error("simulation.radio.cad_marginal_snr must be a number")

    # Cross-validate: reliable >= marginal
    reliable = radio.get("cad_reliable_snr", 0.0)
    marginal = radio.get("cad_marginal_snr", -15.0)
    if isinstance(reliable, (int, float)) and isinstance(marginal, (int, float)):
        if reliable < marginal:
            result._error(
                f"simulation.radio.cad_reliable_snr ({reliable}) must be >= "
                f"cad_marginal_snr ({marginal})"
            )

    if "snr_coherence_ms" in radio:
        v = radio["snr_coherence_ms"]
        if not isinstance(v, (int, float)):
            result._error("simulation.radio.snr_coherence_ms must be a number")
        elif v < 0:
            result._error(
                f"simulation.radio.snr_coherence_ms must be >= 0, got {v}"
            )

    if "hardware" in radio:
        hw = radio["hardware"]
        if not isinstance(hw, dict):
            result._error("simulation.radio.hardware must be an object")
        else:
            for field in ("rx_to_tx_delay_ms", "tx_to_rx_delay_ms"):
                if field in hw:
                    v = hw[field]
                    if not isinstance(v, (int, float)):
                        result._error(f"simulation.radio.hardware.{field} must be a number")
                    elif v < 0:
                        result._error(
                            f"simulation.radio.hardware.{field} must be >= 0, got {v}"
                        )


def _validate_sim_firmware(fw: Any, result: ValidationResult) -> None:
    """Validate simulation.firmware sub-section."""
    if not isinstance(fw, dict):
        result._error("simulation.firmware must be an object")
        return
    if "default" in fw:
        d = fw["default"]
        if not isinstance(d, str) or d == "":
            result._error("simulation.firmware.default must be a non-empty string")
        elif not d.startswith("fw_"):
            result._warn(f"simulation.firmware.default '{d}' doesn't start with 'fw_'")
    if "plugins" in fw:
        p = fw["plugins"]
        if not isinstance(p, dict):
            result._error("simulation.firmware.plugins must be an object (name->path)")
        else:
            for k, v in p.items():
                if not isinstance(v, str):
                    result._error(f"simulation.firmware.plugins.{k} must be a string path")


def _validate_commands(
    commands: Any,
    node_names: set[str],
    duration_ms: int | None,
    result: ValidationResult,
) -> None:
    """Validate commands list."""
    if not isinstance(commands, list):
        result._error("'commands' must be a list")
        return

    for i, cmd in enumerate(commands):
        pfx = f"commands[{i}]"
        if not isinstance(cmd, dict):
            result._error(f"{pfx}: must be an object")
            continue

        # at_ms
        at_ms = cmd.get("at_ms")
        if at_ms is None:
            result._error(f"{pfx}: missing required field 'at_ms'")
        elif not isinstance(at_ms, int):
            result._error(f"{pfx}: 'at_ms' must be an integer")
        elif at_ms < 0:
            result._error(f"{pfx}: 'at_ms' must be >= 0, got {at_ms}")
        elif duration_ms is not None and at_ms >= duration_ms:
            result._warn(
                f"{pfx}: 'at_ms' ({at_ms}) is >= duration_ms ({duration_ms}), "
                "command will never execute"
            )

        # node
        node = cmd.get("node")
        if node is None:
            result._error(f"{pfx}: missing required field 'node'")
        elif not isinstance(node, str) or node == "":
            result._error(f"{pfx}: 'node' must be a non-empty string")
        elif node.startswith("@"):
            if node not in _VALID_GROUP_TARGETS:
                result._error(
                    f"{pfx}: unknown group target '{node}', "
                    f"expected one of {sorted(_VALID_GROUP_TARGETS)}"
                )
        elif node not in node_names:
            result._error(f"{pfx}: 'node' references unknown node '{node}'")

        # command
        command = cmd.get("command")
        if command is None:
            result._error(f"{pfx}: missing required field 'command'")
        elif not isinstance(command, str) or command == "":
            result._error(f"{pfx}: 'command' must be a non-empty string")


def _validate_expect(
    expect: Any, node_names: set[str], result: ValidationResult,
) -> None:
    """Validate expect/assertion list."""
    if not isinstance(expect, list):
        result._error("'expect' must be a list")
        return

    valid_types = {
        "cmd_reply_contains",
        "cmd_reply_not_contains",
        "event_count_min",
        "event_count",
        "tx_airtime_between",
    }

    for i, exp in enumerate(expect):
        pfx = f"expect[{i}]"
        if not isinstance(exp, dict):
            result._error(f"{pfx}: must be an object")
            continue

        atype = exp.get("type")
        if atype is None:
            result._error(f"{pfx}: missing required field 'type'")
            continue
        if atype not in valid_types:
            result._warn(f"{pfx}: unknown assertion type '{atype}'")

        # Validate node references in assertions that have them
        node = exp.get("node")
        if node is not None and isinstance(node, str) and node not in node_names:
            result._warn(f"{pfx}: 'node' references unknown node '{node}'")


def _validate_message_schedule(
    schedule: Any,
    node_names: set[str],
    duration_ms: int | None,
    result: ValidationResult,
) -> None:
    """Validate message_schedule list."""
    if not isinstance(schedule, list):
        result._error("'message_schedule' must be a list")
        return

    for i, entry in enumerate(schedule):
        pfx = f"message_schedule[{i}]"
        if not isinstance(entry, dict):
            result._error(f"{pfx}: must be an object")
            continue

        # from (required)
        frm = entry.get("from")
        if frm is None:
            result._error(f"{pfx}: missing required field 'from'")
        elif not isinstance(frm, str):
            result._error(f"{pfx}: 'from' must be a string")
        elif frm not in node_names:
            result._error(f"{pfx}: 'from' references unknown node '{frm}'")

        # to (required)
        to = entry.get("to")
        if to is None:
            result._error(f"{pfx}: missing required field 'to'")
        elif not isinstance(to, str):
            result._error(f"{pfx}: 'to' must be a string")
        elif to not in node_names:
            result._error(f"{pfx}: 'to' references unknown node '{to}'")

        # interval_ms (required)
        interval = entry.get("interval_ms")
        if interval is None:
            result._error(f"{pfx}: missing required field 'interval_ms'")
        elif not isinstance(interval, int) or interval <= 0:
            result._error(f"{pfx}: 'interval_ms' must be a positive integer")

        # start_ms (optional)
        if "start_ms" in entry:
            s = entry["start_ms"]
            if not isinstance(s, int) or s < 0:
                result._error(f"{pfx}: 'start_ms' must be a non-negative integer")
            elif duration_ms is not None and s >= duration_ms:
                result._warn(
                    f"{pfx}: 'start_ms' ({s}) >= duration_ms ({duration_ms})"
                )

        # count (optional)
        if "count" in entry:
            c = entry["count"]
            if not isinstance(c, int) or c <= 0:
                result._error(f"{pfx}: 'count' must be a positive integer")

        # ack (optional)
        if "ack" in entry and not isinstance(entry["ack"], bool):
            result._error(f"{pfx}: 'ack' must be a boolean")


def _validate_channel_schedule(
    schedule: Any,
    node_names: set[str],
    duration_ms: int | None,
    result: ValidationResult,
) -> None:
    """Validate channel_schedule list."""
    if not isinstance(schedule, list):
        result._error("'channel_schedule' must be a list")
        return

    for i, entry in enumerate(schedule):
        pfx = f"channel_schedule[{i}]"
        if not isinstance(entry, dict):
            result._error(f"{pfx}: must be an object")
            continue

        # from (required)
        frm = entry.get("from")
        if frm is None:
            result._error(f"{pfx}: missing required field 'from'")
        elif not isinstance(frm, str):
            result._error(f"{pfx}: 'from' must be a string")
        elif frm not in node_names:
            result._error(f"{pfx}: 'from' references unknown node '{frm}'")

        # interval_ms (required)
        interval = entry.get("interval_ms")
        if interval is None:
            result._error(f"{pfx}: missing required field 'interval_ms'")
        elif not isinstance(interval, int) or interval <= 0:
            result._error(f"{pfx}: 'interval_ms' must be a positive integer")

        # start_ms (optional)
        if "start_ms" in entry:
            s = entry["start_ms"]
            if not isinstance(s, int) or s < 0:
                result._error(f"{pfx}: 'start_ms' must be a non-negative integer")

        # count (optional)
        if "count" in entry:
            c = entry["count"]
            if not isinstance(c, int) or c <= 0:
                result._error(f"{pfx}: 'count' must be a positive integer")

        # channel (optional, only 0 currently)
        if "channel" in entry:
            ch = entry["channel"]
            if not isinstance(ch, int):
                result._error(f"{pfx}: 'channel' must be an integer")
            elif ch != 0:
                result._warn(f"{pfx}: 'channel' {ch} is not 0 (only channel 0 is currently supported)")


# ── Helpers ──────────────────────────────────────────────────────────


def _validate_probability(
    value: Any, field_name: str, result: ValidationResult,
) -> None:
    """Validate a probability value [0.0, 1.0]."""
    if not isinstance(value, (int, float)):
        result._error(f"{field_name} must be a number")
    elif value < 0.0 or value > 1.0:
        result._error(f"{field_name} must be between 0.0 and 1.0, got {value}")
