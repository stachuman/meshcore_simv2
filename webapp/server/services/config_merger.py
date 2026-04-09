"""Merge a reusable topology with a scenario to produce a full orchestrator config."""

from __future__ import annotations


def merge_topology_and_scenario(topology: dict, scenario: dict) -> dict:
    """Merge topology data with scenario data into a full orchestrator config.

    Parameters
    ----------
    topology : dict
        Topology entry with keys: nodes, links, radio.
    scenario : dict
        Scenario config with keys: simulation, commands, expect,
        message_schedule, channel_schedule, etc.

    Returns
    -------
    dict
        Full orchestrator config ready for SimManager.
    """
    merged: dict = {}

    # Simulation section from scenario, with radio defaults from topology
    sim = dict(scenario.get("simulation", {}))
    topo_radio = topology.get("radio", {})
    if topo_radio:
        # Topology radio provides defaults; scenario radio overrides
        scenario_radio = sim.get("radio", {})
        merged_radio = {**topo_radio, **scenario_radio}
        # Normalize CR: topology editor may store 5-8 (LoRa convention),
        # but orchestrator expects 1-4.
        cr = merged_radio.get("cr")
        if isinstance(cr, int) and 5 <= cr <= 8:
            merged_radio["cr"] = cr - 4
        sim["radio"] = merged_radio
    merged["simulation"] = sim

    # Nodes from topology
    merged["nodes"] = list(topology.get("nodes", []))

    # Topology links
    merged["topology"] = {"links": list(topology.get("links", []))}

    # Scenario sections
    for key in ("commands", "expect", "message_schedule", "channel_schedule"):
        if key in scenario:
            merged[key] = scenario[key]

    return merged
