"""Config CRUD router -- create, list, get, update, delete, validate."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from server.config import Settings, validate_safe_id
from server.services.config_validator import validate_config

router = APIRouter(prefix="/configs", tags=["configs"])


# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------

class ConfigCreateRequest(BaseModel):
    config: dict
    name: Optional[str] = None


class ConfigUpdateRequest(BaseModel):
    config: Optional[dict] = None
    name: Optional[str] = None


class ConfigValidateRequest(BaseModel):
    config: dict


class ConfigSummary(BaseModel):
    """Lightweight entry returned by the list endpoint (no full config)."""
    id: str
    name: str
    created_at: float
    updated_at: float
    node_count: int = 0
    has_geo: bool = False
    topology_id: Optional[str] = None
    command_count: int = 0
    assertion_count: int = 0


class ConfigEntry(BaseModel):
    """Full entry returned by get / create / update."""
    id: str
    name: str
    created_at: float
    updated_at: float
    config: dict
    topology_id: Optional[str] = None


class ValidationResponse(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _configs_dir() -> Path:
    return Settings.get().DATA_DIR / "configs"


def _config_path(config_id: str) -> Path:
    validate_safe_id(config_id, "config ID")
    return _configs_dir() / f"{config_id}.json"


def _auto_name(config: dict) -> str:
    """Generate a human-readable name from the config content."""
    nodes = config.get("nodes")
    if isinstance(nodes, list):
        return f"Config with {len(nodes)} nodes"
    return "Unnamed config"


def _extract_summary(entry: dict) -> dict:
    """Extract lightweight summary fields from a stored entry."""
    config = entry.get("config", {})
    nodes = config.get("nodes", [])
    node_count = len(nodes) if isinstance(nodes, list) else 0
    has_geo = False
    if isinstance(nodes, list):
        has_geo = any(
            isinstance(n, dict) and "lat" in n and "lon" in n
            for n in nodes
        )
    topology_id = config.get("topology_id")
    commands = config.get("commands", [])
    command_count = len(commands) if isinstance(commands, list) else 0
    expect = config.get("expect", [])
    assertion_count = len(expect) if isinstance(expect, list) else 0
    return {
        "id": entry["id"],
        "name": entry["name"],
        "created_at": entry["created_at"],
        "updated_at": entry["updated_at"],
        "node_count": node_count,
        "has_geo": has_geo,
        "topology_id": topology_id,
        "command_count": command_count,
        "assertion_count": assertion_count,
    }


def _load_entry(config_id: str) -> dict:
    """Load a config entry from disk, raising 404 if missing."""
    path = _config_path(config_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Config '{config_id}' not found")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to read config '{config_id}': {exc}"
        )


def _save_entry(entry: dict) -> None:
    """Persist a config entry to disk."""
    path = _config_path(entry["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entry, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/", response_model=ConfigEntry, status_code=201)
async def create_config(body: ConfigCreateRequest) -> ConfigEntry:
    """Create / upload a new config."""
    config_id = uuid.uuid4().hex[:12]
    now = time.time()
    name = body.name if body.name else _auto_name(body.config)

    topo_id = body.config.get("topology_id") if isinstance(body.config, dict) else None
    entry = {
        "id": config_id,
        "name": name,
        "created_at": now,
        "updated_at": now,
        "config": body.config,
        "topology_id": topo_id,
    }
    _save_entry(entry)
    return ConfigEntry(**entry)


@router.get("/", response_model=list[ConfigSummary])
async def list_configs() -> list[ConfigSummary]:
    """List all saved configs (without full config JSON)."""
    configs_dir = _configs_dir()
    if not configs_dir.exists():
        return []

    results: list[dict] = []
    for path in sorted(configs_dir.glob("*.json")):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            results.append(_extract_summary(entry))
        except (json.JSONDecodeError, OSError, KeyError):
            # Skip malformed files silently
            continue

    # Sort by most recently updated first
    results.sort(key=lambda e: e["updated_at"], reverse=True)
    return [ConfigSummary(**r) for r in results]


@router.get("/{config_id}", response_model=ConfigEntry)
async def get_config(config_id: str) -> ConfigEntry:
    """Get a config entry including the full config JSON."""
    entry = _load_entry(config_id)
    # Extract topology_id from config for convenience
    if "topology_id" not in entry:
        config = entry.get("config", {})
        topo_id = config.get("topology_id") if isinstance(config, dict) else None
        entry["topology_id"] = topo_id
    return ConfigEntry(**entry)


@router.put("/{config_id}", response_model=ConfigEntry)
async def update_config(config_id: str, body: ConfigUpdateRequest) -> ConfigEntry:
    """Update an existing config's content and/or name."""
    entry = _load_entry(config_id)

    if body.config is not None:
        entry["config"] = body.config
    if body.name is not None:
        entry["name"] = body.name

    entry["updated_at"] = time.time()
    # Extract topology_id from config for response
    config = entry.get("config", {})
    entry["topology_id"] = config.get("topology_id") if isinstance(config, dict) else None
    _save_entry(entry)
    return ConfigEntry(**entry)


@router.delete("/{config_id}", status_code=204)
async def delete_config(config_id: str) -> None:
    """Delete a config file."""
    path = _config_path(config_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Config '{config_id}' not found")
    path.unlink()


@router.post("/validate", response_model=ValidationResponse)
async def validate_config_endpoint(body: ConfigValidateRequest) -> ValidationResponse:
    """Validate config JSON without saving it."""
    result = validate_config(body.config)
    return ValidationResponse(
        valid=result.valid,
        errors=result.errors,
        warnings=result.warnings,
    )
