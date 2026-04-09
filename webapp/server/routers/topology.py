"""Topology router -- convert and generate orchestrator configs from topology data."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from server.services.topo_tools import convert_topology, generate_grid

router = APIRouter(prefix="/topo", tags=["topology"])


# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------

class ConvertOptions(BaseModel):
    """Options for the topology conversion pipeline."""
    min_snr: float = -10.0
    min_confidence: float = 0.7
    include_inferred: bool = False
    merge_bidir: bool = False
    fill_gaps: bool = True
    bridge_islands: bool = True
    max_gap_km: float = 30.0
    max_good_links: int = 2
    max_edges_per_node: int = 8
    max_link_km: float = 80.0
    gap_sigma: Optional[float] = None
    dedup_km: float = 0.0
    estimate_coords: bool = True
    validate_coords: bool = False
    duration_ms: int = 300000
    step_ms: int = 5
    warmup_ms: int = 5000
    hot_start: bool = True
    companions: list[dict] = Field(default_factory=list)
    msg_schedules: list[dict] = Field(default_factory=list)


class ConvertRequest(BaseModel):
    """Request body for POST /api/topo/convert."""
    topology: dict
    options: ConvertOptions = Field(default_factory=ConvertOptions)


class GenerateRequest(BaseModel):
    """Request body for POST /api/topo/generate."""
    rows: int = 3
    cols: int = 3
    spacing_km: float = 5.0
    snr: float = 5.0
    base_lat: float = 54.35
    base_lon: float = 18.65
    num_companions: int = 2
    duration_ms: int = 300000
    step_ms: int = 5
    warmup_ms: int = 5000


class ConvertResponse(BaseModel):
    """Response from convert and generate endpoints."""
    config: dict
    node_count: int
    link_count: int
    has_geo: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_response(config: dict) -> ConvertResponse:
    """Build a ConvertResponse from an orchestrator config dict."""
    nodes = config.get("nodes", [])
    links = config.get("topology", {}).get("links", [])
    has_geo = any(
        isinstance(n, dict) and "lat" in n and "lon" in n
        for n in nodes
    )
    return ConvertResponse(
        config=config,
        node_count=len(nodes),
        link_count=len(links),
        has_geo=has_geo,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/convert", response_model=ConvertResponse)
async def convert_topology_endpoint(body: ConvertRequest) -> ConvertResponse:
    """Convert a MeshCore topology.json to orchestrator config format.

    Accepts raw topology data (nodes dict + edges list) and conversion
    options. Returns a ready-to-use orchestrator config.
    """
    topo = body.topology
    opts = body.options

    if "nodes" not in topo:
        raise HTTPException(
            status_code=422,
            detail="topology must contain a 'nodes' key",
        )
    if "edges" not in topo:
        raise HTTPException(
            status_code=422,
            detail="topology must contain an 'edges' key",
        )

    try:
        config = convert_topology(
            topology_json=topo,
            min_snr=opts.min_snr,
            min_confidence=opts.min_confidence,
            include_inferred=opts.include_inferred,
            merge_bidir=opts.merge_bidir,
            fill_gaps=opts.fill_gaps,
            bridge_islands=opts.bridge_islands,
            max_gap_km=opts.max_gap_km,
            max_good_links=opts.max_good_links,
            max_edges_per_node=opts.max_edges_per_node,
            max_link_km=opts.max_link_km,
            gap_sigma=opts.gap_sigma,
            dedup_km=opts.dedup_km,
            estimate_coords=opts.estimate_coords,
            validate_coords=opts.validate_coords,
            duration_ms=opts.duration_ms,
            step_ms=opts.step_ms,
            warmup_ms=opts.warmup_ms,
            hot_start=opts.hot_start,
            companions=opts.companions or None,
            msg_schedules=opts.msg_schedules or None,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Conversion failed: {exc}",
        )

    return _build_response(config)


@router.post("/generate", response_model=ConvertResponse)
async def generate_grid_endpoint(body: GenerateRequest) -> ConvertResponse:
    """Generate a grid topology config with repeaters and companions.

    Creates an NxM repeater grid with configurable spacing, SNR, and
    geo-coordinates. Companions are automatically placed at corners,
    edges, then interior positions.
    """
    if body.rows < 1 or body.cols < 1:
        raise HTTPException(
            status_code=422,
            detail="rows and cols must be >= 1",
        )
    if body.num_companions < 0:
        raise HTTPException(
            status_code=422,
            detail="num_companions must be >= 0",
        )
    max_companions = body.rows * body.cols
    if body.num_companions > max_companions:
        raise HTTPException(
            status_code=422,
            detail=f"num_companions ({body.num_companions}) exceeds grid size ({max_companions})",
        )

    try:
        config = generate_grid(
            rows=body.rows,
            cols=body.cols,
            spacing_km=body.spacing_km,
            snr=body.snr,
            base_lat=body.base_lat,
            base_lon=body.base_lon,
            num_companions=body.num_companions,
            duration_ms=body.duration_ms,
            step_ms=body.step_ms,
            warmup_ms=body.warmup_ms,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Grid generation failed: {exc}",
        )

    return _build_response(config)
