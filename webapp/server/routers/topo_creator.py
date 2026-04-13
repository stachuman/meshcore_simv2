"""Topology Creator router -- ITM-based topology generation from MeshCore map API.

Endpoints:
    POST   /api/topo-creator           Start a new topology generation
    GET    /api/topo-creator           List all generations
    GET    /api/topo-creator/{id}      Generation status + result
    DELETE /api/topo-creator/{id}      Cancel/delete a generation
    GET    /api/topo-creator/{id}/progress  SSE progress stream
    POST   /api/topo-creator/{id}/save      Save result as topology
"""

import json
import logging
import time
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from server.config import Settings, validate_safe_id
from server.services.topo_generator import GeneratorParams, TopoGenerator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/topo-creator", tags=["topo-creator"])


# ---------------------------------------------------------------------------
# Lazy initialization
# ---------------------------------------------------------------------------

def _get_generator(request: Request) -> TopoGenerator:
    if not hasattr(request.app.state, "topo_generator"):
        settings = Settings.get()
        request.app.state.topo_generator = TopoGenerator(
            data_dir=settings.DATA_DIR,
            max_workers=settings.MAX_CONCURRENT_SIMS,
        )
    return request.app.state.topo_generator


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class SaveRequest(BaseModel):
    name: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_gen_id(gen_id: str) -> str:
    try:
        return validate_safe_id(gen_id, "generation ID")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("", include_in_schema=True)
async def create_generation(body: GeneratorParams, request: Request):
    generator = _get_generator(request)
    try:
        gen_id = await generator.create_generation(body)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"id": gen_id, "status": "pending"}


@router.get("", include_in_schema=True)
async def list_generations(request: Request):
    generator = _get_generator(request)
    gens = generator.list_generations()
    result = []
    for g in gens:
        entry = {
            "id": g.id,
            "status": g.status,
            "created_at": g.created_at,
            "completed_at": g.completed_at,
            "error": g.error,
            "progress_pct": g.progress_pct,
            "progress_step": g.progress_step,
            "params": g.params,
        }
        if g.result:
            entry["stats"] = g.result.get("stats", {})
        result.append(entry)
    return result


@router.get("/{gen_id}")
async def get_generation(gen_id: str, request: Request):
    _validate_gen_id(gen_id)
    generator = _get_generator(request)
    g = generator.get_generation(gen_id)
    if g is None:
        raise HTTPException(status_code=404, detail="Generation not found")

    resp = {
        "id": g.id,
        "status": g.status,
        "created_at": g.created_at,
        "completed_at": g.completed_at,
        "error": g.error,
        "progress_pct": g.progress_pct,
        "progress_step": g.progress_step,
        "params": g.params,
        "result": g.result,
    }
    return resp


@router.delete("/{gen_id}")
async def delete_generation(gen_id: str, request: Request):
    _validate_gen_id(gen_id)
    generator = _get_generator(request)
    existed = await generator.delete_generation(gen_id)
    if not existed:
        raise HTTPException(status_code=404, detail="Generation not found")
    return {"id": gen_id, "deleted": True}


@router.get("/{gen_id}/progress")
async def generation_progress(gen_id: str, request: Request):
    _validate_gen_id(gen_id)
    generator = _get_generator(request)
    g = generator.get_generation(gen_id)
    if g is None:
        raise HTTPException(status_code=404, detail="Generation not found")

    q = generator.subscribe_progress(gen_id)

    async def event_stream():
        try:
            while True:
                data = await q.get()
                if data is None:
                    yield f"data: {json.dumps({'done': True})}\n\n"
                    break
                yield f"data: {json.dumps(data)}\n\n"
        finally:
            generator.unsubscribe_progress(gen_id, q)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{gen_id}/save")
async def save_as_topology(gen_id: str, body: SaveRequest, request: Request):
    _validate_gen_id(gen_id)
    generator = _get_generator(request)
    g = generator.get_generation(gen_id)
    if g is None:
        raise HTTPException(status_code=404, detail="Generation not found")
    if g.status != "completed" or not g.result:
        raise HTTPException(
            status_code=409,
            detail="Generation is not completed or has no result",
        )

    result = g.result
    nodes = result.get("nodes", [])
    links = result.get("links", [])
    radio = result.get("radio", {})
    stats = result.get("stats", {})

    # Auto-generate name if not provided
    name = body.name
    if not name:
        bbox = g.params.get("bbox", [])
        region_desc = ""
        if len(bbox) == 4:
            lat_mid = (bbox[0] + bbox[2]) / 2
            lon_mid = (bbox[1] + bbox[3]) / 2
            region_desc = f" ({lat_mid:.1f},{lon_mid:.1f})"
        name = f"ITM{region_desc} ({stats.get('node_count', len(nodes))} nodes)"

    # Save as topology using same pattern as topologies router
    settings = Settings.get()
    topo_dir = settings.DATA_DIR / "topologies"
    topo_dir.mkdir(parents=True, exist_ok=True)

    topo_id = uuid.uuid4().hex[:12]
    while (topo_dir / f"{topo_id}.json").exists():
        topo_id = uuid.uuid4().hex[:12]
    now = time.time()
    entry = {
        "id": topo_id,
        "name": name,
        "description": f"Generated from MeshCore Map API using ITM propagation model",
        "created_at": now,
        "updated_at": now,
        "nodes": nodes,
        "links": links,
        "radio": radio,
    }
    topo_path = topo_dir / f"{topo_id}.json"
    topo_path.write_text(json.dumps(entry, indent=2), encoding="utf-8")

    return {"id": topo_id, "name": name}
