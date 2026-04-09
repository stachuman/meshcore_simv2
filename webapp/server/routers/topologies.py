"""Topology CRUD router -- create, list, get, update, delete, extract from config."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from server.config import Settings

router = APIRouter(prefix="/topologies", tags=["topologies"])


# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------

class TopologyCreateRequest(BaseModel):
    name: str
    description: Optional[str] = ""
    nodes: list[dict] = Field(default_factory=list)
    links: list[dict] = Field(default_factory=list)
    radio: dict = Field(default_factory=dict)


class TopologyUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    nodes: Optional[list[dict]] = None
    links: Optional[list[dict]] = None
    radio: Optional[dict] = None


class TopologySummary(BaseModel):
    id: str
    name: str
    description: str
    created_at: float
    updated_at: float
    node_count: int = 0
    link_count: int = 0
    has_geo: bool = False


class TopologyEntry(BaseModel):
    id: str
    name: str
    description: str
    created_at: float
    updated_at: float
    nodes: list[dict]
    links: list[dict]
    radio: dict


class FromConfigRequest(BaseModel):
    config: dict
    name: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _topologies_dir() -> Path:
    return Settings.get().DATA_DIR / "topologies"


def _topo_path(topo_id: str) -> Path:
    return _topologies_dir() / f"{topo_id}.json"


def _extract_summary(entry: dict) -> dict:
    nodes = entry.get("nodes", [])
    links = entry.get("links", [])
    node_count = len(nodes) if isinstance(nodes, list) else 0
    link_count = len(links) if isinstance(links, list) else 0
    has_geo = False
    if isinstance(nodes, list):
        has_geo = any(
            isinstance(n, dict) and "lat" in n and "lon" in n
            for n in nodes
        )
    return {
        "id": entry["id"],
        "name": entry["name"],
        "description": entry.get("description", ""),
        "created_at": entry["created_at"],
        "updated_at": entry["updated_at"],
        "node_count": node_count,
        "link_count": link_count,
        "has_geo": has_geo,
    }


def _load_entry(topo_id: str) -> dict:
    path = _topo_path(topo_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Topology '{topo_id}' not found")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to read topology '{topo_id}': {exc}"
        )


def _save_entry(entry: dict) -> None:
    path = _topo_path(entry["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entry, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/", response_model=TopologyEntry, status_code=201)
async def create_topology(body: TopologyCreateRequest) -> TopologyEntry:
    topo_id = uuid.uuid4().hex[:12]
    now = time.time()
    entry = {
        "id": topo_id,
        "name": body.name,
        "description": body.description or "",
        "created_at": now,
        "updated_at": now,
        "nodes": body.nodes,
        "links": body.links,
        "radio": body.radio,
    }
    _save_entry(entry)
    return TopologyEntry(**entry)


@router.get("/", response_model=list[TopologySummary])
async def list_topologies() -> list[TopologySummary]:
    topo_dir = _topologies_dir()
    if not topo_dir.exists():
        return []

    results: list[dict] = []
    for path in sorted(topo_dir.glob("*.json")):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            results.append(_extract_summary(entry))
        except (json.JSONDecodeError, OSError, KeyError):
            continue

    results.sort(key=lambda e: e["updated_at"], reverse=True)
    return [TopologySummary(**r) for r in results]


@router.get("/{topo_id}", response_model=TopologyEntry)
async def get_topology(topo_id: str) -> TopologyEntry:
    entry = _load_entry(topo_id)
    return TopologyEntry(**entry)


@router.put("/{topo_id}", response_model=TopologyEntry)
async def update_topology(topo_id: str, body: TopologyUpdateRequest) -> TopologyEntry:
    entry = _load_entry(topo_id)

    if body.name is not None:
        entry["name"] = body.name
    if body.description is not None:
        entry["description"] = body.description
    if body.nodes is not None:
        entry["nodes"] = body.nodes
    if body.links is not None:
        entry["links"] = body.links
    if body.radio is not None:
        entry["radio"] = body.radio

    entry["updated_at"] = time.time()
    _save_entry(entry)
    return TopologyEntry(**entry)


@router.delete("/{topo_id}", status_code=204)
async def delete_topology(topo_id: str) -> None:
    path = _topo_path(topo_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Topology '{topo_id}' not found")
    path.unlink()


@router.post("/from-config", response_model=TopologyEntry, status_code=201)
async def extract_from_config(body: FromConfigRequest) -> TopologyEntry:
    """Extract topology (nodes, links, radio) from an existing full config."""
    config = body.config
    nodes = config.get("nodes", [])
    links = config.get("topology", {}).get("links", [])
    radio = config.get("simulation", {}).get("radio", {})

    # Strip scenario-only fields from nodes (keep name, role, lat, lon, radio overrides)
    topo_nodes = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        clean = {}
        for key in ("name", "role", "lat", "lon", "sf", "bw", "cr", "contact"):
            if key in n:
                clean[key] = n[key]
        topo_nodes.append(clean)

    name = body.name
    if not name:
        name = f"Topology from config ({len(topo_nodes)} nodes)"

    topo_id = uuid.uuid4().hex[:12]
    now = time.time()
    entry = {
        "id": topo_id,
        "name": name,
        "description": "",
        "created_at": now,
        "updated_at": now,
        "nodes": topo_nodes,
        "links": links,
        "radio": radio,
    }
    _save_entry(entry)
    return TopologyEntry(**entry)
