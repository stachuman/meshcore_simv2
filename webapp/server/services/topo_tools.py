"""Topology tools service -- wraps convert_topology.py and gen_grid_test.py.

Provides a function interface to the CLI tools without duplicating their logic.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

# Add the tools directory to sys.path so we can import from it.
_TOOLS_DIR = str(Path(__file__).resolve().parent.parent.parent.parent / "tools")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

# Import helper functions directly from the CLI scripts.
# convert_topology.py: full pipeline via convert() + helpers
import convert_topology as _ct

# gen_grid_test.py: generate_test() already has a clean function interface
from gen_grid_test import generate_test as _gen_grid_test


# ---------------------------------------------------------------------------
# Topology conversion (wraps convert_topology.convert via argparse Namespace)
# ---------------------------------------------------------------------------

def convert_topology(
    topology_json: dict,
    *,
    min_snr: float = -10.0,
    min_confidence: float = 0.7,
    include_inferred: bool = False,
    merge_bidir: bool = False,
    fill_gaps: bool = True,
    bridge_islands: bool = True,
    max_gap_km: float = 30.0,
    max_good_links: int = 2,
    max_edges_per_node: int = 8,
    max_link_km: float = 80.0,
    gap_sigma: float | None = None,
    dedup_km: float = 0.0,
    estimate_coords: bool = True,
    validate_coords: bool = False,
    duration_ms: int = 300000,
    step_ms: int = 5,
    warmup_ms: int = 5000,
    hot_start: bool = True,
    companions: list[dict] | None = None,
    msg_schedules: list[dict] | None = None,
    verbose: bool = False,
) -> dict:
    """Convert a topology.json dict to orchestrator config format.

    This wraps the pipeline in convert_topology.py without touching the
    filesystem.  We reconstruct the conversion pipeline inline, reusing
    all the helper functions from the CLI script.

    Parameters
    ----------
    topology_json : dict
        Raw topology data with "nodes" and "edges" keys.
    companions : list[dict] | None
        Each dict: {"name": str, "repeater": str, "snr"?: float, "rssi"?: float}
    msg_schedules : list[dict] | None
        Each dict: {"from": str, "to": str, "interval_s": float}

    Returns
    -------
    dict
        Orchestrator config ready for JSON serialization.
    """
    nodes_in = dict(topology_json.get("nodes", {}))
    edges_in = list(topology_json.get("edges", []))

    # --- [1] Node processing ---
    seen_names: dict[str, int] = {}
    prefix_to_name: dict[str, str] = {}
    node_noise_floor: dict[str, int] = {}

    nodes_out: list[dict] = []
    for prefix, nd in nodes_in.items():
        if len(prefix) < 8:
            continue

        raw_name = nd.get("name", prefix)
        sname = _ct.sanitize_name(raw_name, seen_names)
        prefix_to_name[prefix] = sname

        nf = nd.get("status", {}).get("noise_floor")
        if nf is not None:
            node_noise_floor[prefix] = int(nf)

        node_def: dict = {"name": sname, "role": "repeater"}
        if "lat" in nd and "lon" in nd:
            node_def["lat"] = nd["lat"]
            node_def["lon"] = nd["lon"]
        nodes_out.append(node_def)

    # --- [1b] Co-located node dedup ---
    if dedup_km > 0:
        nodes_in, edges_in = _ct.dedup_colocated(
            nodes_in, edges_in, dedup_km, verbose=verbose,
        )
        seen_names = {}
        prefix_to_name = {}
        nodes_out = []
        for prefix, nd in nodes_in.items():
            if len(prefix) < 8:
                continue
            raw_name = nd.get("name", prefix)
            sname = _ct.sanitize_name(raw_name, seen_names)
            prefix_to_name[prefix] = sname
            node_def = {"name": sname, "role": "repeater"}
            if "lat" in nd and "lon" in nd:
                node_def["lat"] = nd["lat"]
                node_def["lon"] = nd["lon"]
            nodes_out.append(node_def)

    # --- [2] Estimate coordinates ---
    estimated_coords: dict = {}
    if estimate_coords:
        estimated_coords = _ct.estimate_node_coordinates(
            nodes_in, edges_in, verbose=verbose,
        )
        for prefix, (est_lat, est_lon) in estimated_coords.items():
            nodes_in[prefix]["lat"] = est_lat
            nodes_in[prefix]["lon"] = est_lon
            name = prefix_to_name.get(prefix)
            if name:
                for nd in nodes_out:
                    if nd["name"] == name:
                        nd["lat"] = est_lat
                        nd["lon"] = est_lon
                        break

    # Remove nodes that still have missing coordinates (0, 0)
    filtered_names: set[str] = set()
    filtered_nodes: list[dict] = []
    for nd in nodes_out:
        lat = nd.get("lat", 0.0)
        lon = nd.get("lon", 0.0)
        if lat == 0.0 and lon == 0.0:
            filtered_names.add(nd["name"])
            continue
        filtered_nodes.append(nd)
    nodes_out = filtered_nodes

    if filtered_names:
        prefix_to_name = {
            p: n for p, n in prefix_to_name.items() if n not in filtered_names
        }

    all_sanitized_names = set(prefix_to_name.values())

    # --- [3] Edge filtering and conversion ---
    links_out: list[dict] = []
    max_measured_snr = 0.0

    for edge in edges_in:
        src_prefix = edge["from"]
        dst_prefix = edge["to"]
        snr_db = edge.get("snr_db", 0.0)
        confidence = edge.get("confidence", 1.0)
        source = edge.get("source", "")
        snr_min_db = edge.get("snr_min_db")

        if src_prefix not in prefix_to_name or dst_prefix not in prefix_to_name:
            continue
        if source == "inferred" and not include_inferred:
            continue
        if confidence < min_confidence:
            continue
        if snr_db < min_snr:
            continue

        # Filter by distance
        if src_prefix in nodes_in and dst_prefix in nodes_in:
            n1 = nodes_in[src_prefix]
            n2 = nodes_in[dst_prefix]
            lat1 = float(n1.get("lat", 0.0))
            lon1 = float(n1.get("lon", 0.0))
            lat2 = float(n2.get("lat", 0.0))
            lon2 = float(n2.get("lon", 0.0))
            if (lat1 != 0.0 or lon1 != 0.0) and (lat2 != 0.0 or lon2 != 0.0):
                dist = _ct.haversine_km(lat1, lon1, lat2, lon2)
                if dist > max_link_km:
                    continue

        # Compute RSSI from receiver noise floor + SNR
        receiver_nf = node_noise_floor.get(dst_prefix, _ct._DEFAULT_NOISE_FLOOR)
        rssi = max(-140.0, min(-20.0, receiver_nf + snr_db))

        # Compute SNR variance
        snr_std_dev = 0.0
        if snr_min_db is not None and snr_min_db != snr_db:
            snr_std_dev = round((snr_db - snr_min_db) / 4.0, 2)
            if snr_std_dev < 0:
                snr_std_dev = 0.0

        link: dict = {
            "from": prefix_to_name[src_prefix],
            "to": prefix_to_name[dst_prefix],
            "snr": round(snr_db, 2),
            "rssi": round(rssi, 2),
            "bidir": False,
        }
        if snr_std_dev > 0:
            link["snr_std_dev"] = snr_std_dev
        loss = _ct.snr_to_loss(snr_db)
        if loss > 0.0001:
            link["loss"] = loss
        if snr_db > max_measured_snr:
            max_measured_snr = snr_db
        links_out.append(link)

    # --- [4] Fit propagation model ---
    model_a = model_b = sigma = max_measured_dist = 0.0
    if fill_gaps or validate_coords or bridge_islands:
        model_a, model_b, sigma, max_measured_dist = _ct.fit_propagation_model(
            edges_in, nodes_in, prefix_to_name,
            sigma_override=gap_sigma,
            verbose=verbose,
        )

    # --- [5] Validate coordinates ---
    if validate_coords:
        _ct.validate_coordinates(
            nodes_in, edges_in, model_a, model_b, sigma, verbose=verbose,
        )

    # --- [6] Merge bidir edges ---
    if merge_bidir:
        links_out = _ct.merge_bidir_edges(links_out, verbose=verbose)

    # --- [7] Fill gaps ---
    if fill_gaps:
        links_out = _ct.fill_gaps(
            links_out, nodes_out, nodes_in, prefix_to_name,
            max_gap_km=max_gap_km,
            max_good_links=max_good_links,
            max_edges_per_node=max_edges_per_node,
            gap_sigma=gap_sigma,
            model_a=model_a,
            model_b=model_b,
            sigma=sigma,
            max_measured_dist=max_measured_dist,
            max_measured_snr=max_measured_snr,
            verbose=verbose,
        )

    # --- [7b] Bridge islands ---
    if bridge_islands:
        links_out = _ct.bridge_islands(
            links_out, nodes_out, nodes_in, prefix_to_name,
            model_a=model_a,
            model_b=model_b,
            sigma=sigma,
            max_measured_snr=max_measured_snr,
            verbose=verbose,
        )

    # --- [8] Companion injection ---
    for comp in (companions or []):
        comp_name = comp["name"]
        rpt_name = comp["repeater"]

        # Resolve repeater name (check sanitized names, then original names)
        if rpt_name not in all_sanitized_names:
            found = False
            for prefix, nd in nodes_in.items():
                if nd.get("name", "") == rpt_name:
                    resolved = prefix_to_name.get(prefix)
                    if resolved:
                        rpt_name = resolved
                        found = True
                    break
            if not found:
                continue  # skip unknown repeater

        if comp_name in all_sanitized_names:
            comp_name = comp_name + "_c"

        snr = comp.get("snr", 10.0)
        rssi = comp.get("rssi", -70.0)

        nodes_out.append({"name": comp_name, "role": "companion"})
        all_sanitized_names.add(comp_name)
        links_out.append({
            "from": comp_name,
            "to": rpt_name,
            "snr": snr,
            "rssi": rssi,
            "bidir": True,
        })

    # --- Message schedule ---
    msg_schedule_out: list[dict] = []
    for sched in (msg_schedules or []):
        start_ms = warmup_ms + 5000
        interval_ms = int(sched["interval_s"] * 1000)
        msg_schedule_out.append({
            "from": sched["from"],
            "to": sched["to"],
            "start_ms": start_ms,
            "interval_ms": interval_ms,
        })

    # --- [9] Assemble output ---
    config: dict = {
        "_source": "Converted via webapp topo_tools service",
        "simulation": {
            "duration_ms": duration_ms,
            "step_ms": step_ms,
            "warmup_ms": warmup_ms,
            "hot_start": hot_start,
        },
        "nodes": nodes_out,
        "topology": {
            "links": links_out,
        },
        "commands": [],
    }

    if msg_schedule_out:
        config["message_schedule"] = msg_schedule_out

    return config


# ---------------------------------------------------------------------------
# Grid generation (wraps gen_grid_test.generate_test)
# ---------------------------------------------------------------------------

def generate_grid(
    *,
    rows: int = 3,
    cols: int = 3,
    spacing_km: float = 5.0,
    snr: float = 5.0,
    base_lat: float = 54.35,
    base_lon: float = 18.65,
    num_companions: int = 2,
    duration_ms: int = 300000,
    step_ms: int = 5,
    warmup_ms: int = 5000,
) -> dict:
    """Generate a grid topology config with geo-coordinates.

    Extends gen_grid_test.generate_test by injecting lat/lon coordinates
    computed from the grid geometry (spacing_km, base_lat, base_lon).

    Returns
    -------
    dict
        Orchestrator config ready for JSON serialization.
    """
    config = _gen_grid_test(
        rows=rows,
        cols=cols,
        n_companions=num_companions,
        duration_ms=duration_ms,
        step_ms=step_ms,
        warmup_ms=warmup_ms,
        snr_grid=snr,
        snr_companion=10.0,
    )

    # Inject lat/lon for each repeater node based on grid position.
    # spacing_km converted to approximate degrees.
    import math
    lat_deg_per_km = 1.0 / 111.0
    lon_deg_per_km = 1.0 / (111.0 * math.cos(math.radians(base_lat)))

    for node in config["nodes"]:
        if node["role"] != "repeater":
            continue
        # Parse grid position from name "r{row}_{col}"
        name = node["name"]
        if not name.startswith("r"):
            continue
        parts = name[1:].split("_")
        if len(parts) != 2:
            continue
        try:
            r = int(parts[0])
            c = int(parts[1])
        except ValueError:
            continue

        node["lat"] = round(base_lat + r * spacing_km * lat_deg_per_km, 6)
        node["lon"] = round(base_lon + c * spacing_km * lon_deg_per_km, 6)

    return config
