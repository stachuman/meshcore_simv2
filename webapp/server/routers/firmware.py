"""Firmware plugin discovery router."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from server.config import Settings

router = APIRouter(prefix="/firmware", tags=["firmware"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class SourceInfo(BaseModel):
    name: str
    repo: str | None = None
    branch: str | None = None
    tag: str | None = None
    path: str | None = None
    commit: str | None = None
    dirty: bool | None = None


class FirmwareResponse(BaseModel):
    plugins: list[str]
    sources: dict[str, SourceInfo]


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_cache: dict[str, Any] = {"data": None, "ts": 0.0}
_CACHE_TTL = 30.0  # seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_plugins() -> list[str]:
    """Scan orchestrator's directory for fw_*.so files."""
    orch = Settings.get().ORCHESTRATOR_PATH.resolve()
    plugin_dir = orch.parent
    if not plugin_dir.is_dir():
        return []
    plugins = sorted(p.stem for p in plugin_dir.glob("fw_*.so"))
    return plugins


def _find_firmware_json() -> Path | None:
    """Locate firmware.json by walking up from the orchestrator binary."""
    orch = Settings.get().ORCHESTRATOR_PATH.resolve()
    # Walk up looking for firmware.json next to CMakeLists.txt
    d = orch.parent
    for _ in range(6):
        candidate = d / "firmware.json"
        if candidate.is_file():
            return candidate
        d = d.parent
    return None


def _git_info(repo_path: Path) -> dict[str, Any]:
    """Get commit hash and dirty status for a repo path."""
    info: dict[str, Any] = {"commit": None, "dirty": None}
    if not repo_path.is_dir():
        return info
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo_path), capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            info["commit"] = result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repo_path), capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            info["dirty"] = len(result.stdout.strip()) > 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return info


def _build_response() -> FirmwareResponse:
    plugins = _find_plugins()
    sources: dict[str, SourceInfo] = {}

    fw_json_path = _find_firmware_json()
    if fw_json_path:
        try:
            registry = json.loads(fw_json_path.read_text(encoding="utf-8"))
            project_root = fw_json_path.parent
            for name, src in registry.get("sources", {}).items():
                repo_path = project_root / src.get("path", "")
                git = _git_info(repo_path)
                sources[name] = SourceInfo(
                    name=name,
                    repo=src.get("repo"),
                    branch=src.get("branch"),
                    tag=src.get("tag"),
                    path=src.get("path"),
                    commit=git.get("commit"),
                    dirty=git.get("dirty"),
                )
        except (json.JSONDecodeError, OSError):
            pass

    return FirmwareResponse(plugins=plugins, sources=sources)


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.get("", response_model=FirmwareResponse)
async def get_firmware() -> FirmwareResponse:
    """Return available firmware plugins and source registry info."""
    now = time.monotonic()
    if _cache["data"] is not None and (now - _cache["ts"]) < _CACHE_TTL:
        return _cache["data"]

    resp = _build_response()
    _cache["data"] = resp
    _cache["ts"] = now
    return resp
