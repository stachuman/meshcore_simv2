"""Simulations router -- full CRUD + event query endpoints for simulations.

Connects the frontend to SimManager (subprocess lifecycle) and EventIndex
(event queries over NDJSON output files).
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from server.config import Settings
from server.services.config_merger import merge_topology_and_scenario
from server.services.event_index import (
    EventIndex,
    EventIndexCache,
    compute_map_stats,
    get_messages,
    load_topology,
)
from server.services.sim_manager import SimManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sims", tags=["simulations"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CreateSimRequest(BaseModel):
    config_json: dict
    topology_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_sim_or_404(sim_id: str, request: Request):
    """Return the SimRecord for *sim_id*, or raise 404."""
    sim_manager: SimManager = request.app.state.sim_manager
    sim = sim_manager.get_sim(sim_id)
    if sim is None:
        raise HTTPException(status_code=404, detail="Simulation not found")
    return sim


def _get_index(sim_id: str, request: Request) -> EventIndex:
    """Load (or retrieve from cache) the EventIndex for a completed sim.

    Raises 404 if the simulation doesn't exist, or 409 if it hasn't
    completed yet (no events file).
    """
    sim = _get_sim_or_404(sim_id, request)
    sim_manager: SimManager = request.app.state.sim_manager
    event_cache: EventIndexCache = request.app.state.event_cache

    events_path = sim_manager.get_events_path(sim_id)
    if events_path is None:
        if sim.status in ("pending", "running"):
            raise HTTPException(
                status_code=409,
                detail=f"Simulation is still {sim.status}; events not available yet",
            )
        raise HTTPException(
            status_code=404, detail="Events file not found for this simulation"
        )

    return event_cache.get(sim_id, str(events_path))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", include_in_schema=True)
async def create_sim(body: CreateSimRequest, request: Request):
    """Start a new simulation from a config JSON dict.

    If ``topology_id`` is provided, the topology is loaded from disk and
    merged with ``config_json`` (treated as a scenario).  Otherwise
    ``config_json`` is used as a full config (backward compatible).
    """
    config = body.config_json

    if body.topology_id:
        topo_path = Settings.get().DATA_DIR / "topologies" / f"{body.topology_id}.json"
        if not topo_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Topology '{body.topology_id}' not found",
            )
        try:
            topo_data = json.loads(topo_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to read topology: {exc}",
            )
        config = merge_topology_and_scenario(topo_data, config)

    sim_manager: SimManager = request.app.state.sim_manager
    sim_id = await sim_manager.create_sim(config)
    return {"id": sim_id, "status": "pending"}


@router.get("", include_in_schema=True)
async def list_sims(request: Request):
    """List all simulations, newest first."""
    sim_manager: SimManager = request.app.state.sim_manager
    sims = sim_manager.list_sims()
    result = []
    for s in sims:
        node_count = len(s.config.get("nodes", []))
        result.append(
            {
                "id": s.id,
                "status": s.status,
                "created_at": s.created_at,
                "completed_at": s.completed_at,
                "node_count": node_count,
                "error": s.error,
            }
        )
    return result


@router.get("/{sim_id}")
async def get_sim(sim_id: str, request: Request):
    """Get simulation status and config summary."""
    sim = _get_sim_or_404(sim_id, request)
    # Build a config summary (avoid returning the full config blob)
    config = sim.config
    node_count = len(config.get("nodes", []))
    duration_ms = config.get("simulation", {}).get("duration_ms")
    return {
        "id": sim.id,
        "status": sim.status,
        "created_at": sim.created_at,
        "completed_at": sim.completed_at,
        "error": sim.error,
        "progress": sim.progress_pct,
        "config_summary": {
            "node_count": node_count,
            "duration_ms": duration_ms,
        },
    }


@router.delete("/{sim_id}")
async def delete_sim(sim_id: str, request: Request):
    """Cancel and delete a simulation and all its data."""
    sim_manager: SimManager = request.app.state.sim_manager
    event_cache: EventIndexCache = request.app.state.event_cache

    existed = await sim_manager.delete_sim(sim_id)
    if not existed:
        raise HTTPException(status_code=404, detail="Simulation not found")

    event_cache.evict(sim_id)
    return {"id": sim_id, "deleted": True}


@router.get("/{sim_id}/meta")
async def sim_meta(sim_id: str, request: Request):
    """Return node list, time range, event count, and stats."""
    index = _get_index(sim_id, request)
    return index.get_meta()


@router.get("/{sim_id}/events")
async def sim_events(
    sim_id: str,
    request: Request,
    from_ms: int = Query(alias="from", default=0),
    to_ms: int = Query(alias="to", default=2**31),
):
    """Return events in a time window [from, to] (milliseconds)."""
    index = _get_index(sim_id, request)
    events = index.query_time_range(from_ms, to_ms)
    return {"events": events, "count": len(events)}


@router.get("/{sim_id}/density")
async def sim_density(
    sim_id: str,
    request: Request,
    from_ms: int = Query(alias="from", default=0),
    to_ms: int = Query(alias="to", default=2**31),
    bucket_ms: int = Query(alias="bucket", default=1000),
):
    """Return per-node event density heatmap in time buckets."""
    index = _get_index(sim_id, request)
    return index.density(from_ms, to_ms, bucket_ms)


@router.get("/{sim_id}/trace/{pkt}")
async def sim_trace(sim_id: str, pkt: str, request: Request):
    """Return all events for a single packet fingerprint."""
    index = _get_index(sim_id, request)
    return {"pkt": pkt, "events": index.query_pkt(pkt)}


@router.get("/{sim_id}/deep_trace/{pkt}")
async def sim_deep_trace(sim_id: str, pkt: str, request: Request):
    """Follow relay chain across packet hash boundaries."""
    index = _get_index(sim_id, request)
    return index.deep_trace(pkt)


@router.get("/{sim_id}/topology")
async def sim_topology(sim_id: str, request: Request):
    """Return topology (nodes + links) extracted from the simulation config."""
    _get_sim_or_404(sim_id, request)
    sim_manager: SimManager = request.app.state.sim_manager

    config_path = sim_manager.get_config_path(sim_id)
    if config_path is None:
        raise HTTPException(status_code=404, detail="Config file not found")

    return load_topology(str(config_path))


@router.get("/{sim_id}/stats")
async def sim_stats(sim_id: str, request: Request):
    """Return per-node and per-link map statistics."""
    index = _get_index(sim_id, request)
    return compute_map_stats(index)


@router.get("/{sim_id}/messages")
async def sim_messages(sim_id: str, request: Request):
    """Return list of sent messages extracted from cmd_reply events."""
    index = _get_index(sim_id, request)
    return get_messages(index)


@router.get("/{sim_id}/msg_tx/{node}")
async def sim_msg_tx(
    sim_id: str,
    node: str,
    request: Request,
    after_ms: int = Query(alias="after", default=0),
):
    """Find the first TX from a node after a given time (cmd_reply->TX correlation)."""
    index = _get_index(sim_id, request)
    tx = index.find_msg_tx(node, after_ms)
    return {"tx": tx}


@router.get("/{sim_id}/node_events/{node}")
async def sim_node_events(
    sim_id: str,
    node: str,
    request: Request,
    from_ms: int = Query(alias="from", default=0),
    to_ms: int = Query(alias="to", default=2**31),
    limit: int = Query(default=5000),
):
    """Return events involving a specific node in a time range."""
    index = _get_index(sim_id, request)
    events = index.query_node_range(node, from_ms, to_ms)
    if limit and len(events) > limit:
        events = events[:limit]
    return {"node": node, "events": events}


@router.get("/{sim_id}/progress")
async def sim_progress(sim_id: str, request: Request):
    """SSE endpoint streaming simulation progress updates."""
    sim_manager: SimManager = request.app.state.sim_manager
    sim = sim_manager.get_sim(sim_id)
    if not sim:
        raise HTTPException(status_code=404, detail="Simulation not found")

    q = sim_manager.subscribe_progress(sim_id)

    async def event_stream():
        try:
            while True:
                data = await q.get()
                if data is None:
                    yield f"data: {json.dumps({'done': True})}\n\n"
                    break
                yield f"data: {json.dumps(data)}\n\n"
        finally:
            sim_manager.unsubscribe_progress(sim_id, q)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
