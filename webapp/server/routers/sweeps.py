"""Sweeps router -- parameter sweep CRUD, progress SSE, and results endpoints.

Endpoints:
    POST   /api/sweeps               Start a new parameter sweep
    GET    /api/sweeps               List all sweeps
    GET    /api/sweeps/{id}          Sweep status + results summary
    DELETE /api/sweeps/{id}          Cancel/delete a sweep
    GET    /api/sweeps/{id}/results  Full results (aggregated + raw)
    GET    /api/sweeps/{id}/progress SSE progress stream
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from server.config import Settings
from server.services.sweep_runner import SweepRunner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sweeps", tags=["sweeps"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ParamRange(BaseModel):
    min: float = 0.0
    max: float = 0.0
    step: float = 0.0


class SweepParams(BaseModel):
    rxdelay: ParamRange = Field(default_factory=ParamRange)
    txdelay: ParamRange = Field(default_factory=ParamRange)
    direct_txdelay: ParamRange = Field(default_factory=ParamRange)


class CreateSweepRequest(BaseModel):
    config_json: dict
    params: SweepParams
    seeds: list[int] = Field(default_factory=lambda: [42, 43, 44])
    max_workers: Optional[int] = None


# ---------------------------------------------------------------------------
# Lazy initialization of SweepRunner
# ---------------------------------------------------------------------------

def _get_runner(request: Request) -> SweepRunner:
    """Return the SweepRunner, lazily creating it on first access."""
    if not hasattr(request.app.state, "sweep_runner"):
        settings = Settings.get()
        request.app.state.sweep_runner = SweepRunner(
            data_dir=settings.DATA_DIR,
            orchestrator_path=str(settings.ORCHESTRATOR_PATH),
            max_workers=settings.MAX_CONCURRENT_SIMS,
        )
    return request.app.state.sweep_runner


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("", include_in_schema=True)
async def create_sweep(body: CreateSweepRequest, request: Request):
    """Start a new parameter sweep."""
    runner = _get_runner(request)

    params_dict = body.params.model_dump()
    seeds = body.seeds

    sweep_id = await runner.create_sweep(
        config=body.config_json,
        params=params_dict,
        seeds=seeds,
        max_workers=body.max_workers,
    )
    return {"id": sweep_id, "status": "pending"}


@router.get("", include_in_schema=True)
async def list_sweeps(request: Request):
    """List all sweeps, newest first."""
    runner = _get_runner(request)
    sweeps = runner.list_sweeps()
    result = []
    for s in sweeps:
        result.append({
            "id": s.id,
            "status": s.status,
            "created_at": s.created_at,
            "completed_at": s.completed_at,
            "error": s.error,
            "progress_pct": s.progress_pct,
            "total_runs": s.total_runs,
            "completed_runs": s.completed_runs,
            "params": s.params,
            "seeds": s.seeds,
            "best": s.results[0] if s.results else None,
        })
    return result


@router.get("/{sweep_id}")
async def get_sweep(sweep_id: str, request: Request):
    """Get sweep status and results summary."""
    runner = _get_runner(request)
    sweep = runner.get_sweep(sweep_id)
    if sweep is None:
        raise HTTPException(status_code=404, detail="Sweep not found")

    return {
        "id": sweep.id,
        "status": sweep.status,
        "created_at": sweep.created_at,
        "completed_at": sweep.completed_at,
        "error": sweep.error,
        "progress_pct": sweep.progress_pct,
        "total_runs": sweep.total_runs,
        "completed_runs": sweep.completed_runs,
        "params": sweep.params,
        "seeds": sweep.seeds,
        "results": sweep.results,
    }


@router.delete("/{sweep_id}")
async def delete_sweep(sweep_id: str, request: Request):
    """Cancel and delete a sweep and all its data."""
    runner = _get_runner(request)
    existed = await runner.delete_sweep(sweep_id)
    if not existed:
        raise HTTPException(status_code=404, detail="Sweep not found")
    return {"id": sweep_id, "deleted": True}


@router.get("/{sweep_id}/results")
async def get_sweep_results(sweep_id: str, request: Request):
    """Return full results (aggregated and raw) for a completed sweep."""
    runner = _get_runner(request)
    sweep = runner.get_sweep(sweep_id)
    if sweep is None:
        raise HTTPException(status_code=404, detail="Sweep not found")

    if sweep.status not in ("completed", "failed", "cancelled"):
        raise HTTPException(
            status_code=409,
            detail=f"Sweep is still {sweep.status}; results not available yet",
        )

    return {
        "id": sweep.id,
        "status": sweep.status,
        "aggregated": sweep.results,
        "raw": sweep.raw_results,
    }


@router.get("/{sweep_id}/progress")
async def sweep_progress(sweep_id: str, request: Request):
    """SSE endpoint streaming sweep progress updates."""
    runner = _get_runner(request)
    sweep = runner.get_sweep(sweep_id)
    if sweep is None:
        raise HTTPException(status_code=404, detail="Sweep not found")

    q = runner.subscribe_progress(sweep_id)

    async def event_stream():
        try:
            while True:
                data = await q.get()
                if data is None:
                    yield f"data: {json.dumps({'done': True})}\n\n"
                    break
                yield f"data: {json.dumps(data)}\n\n"
        finally:
            runner.unsubscribe_progress(sweep_id, q)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
