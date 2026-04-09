from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class SimConfig(BaseModel):
    model_config = {"extra": "forbid"}

    config_json: dict = Field(default_factory=dict)
    config_id: Optional[str] = None


class SimStatus(BaseModel):
    id: str
    status: str = Field(pattern=r"^(pending|running|completed|failed|cancelled)$")
    created_at: datetime
    completed_at: Optional[datetime] = None
    config_summary: dict = Field(default_factory=dict)
    error: Optional[str] = None


class ConfigEntry(BaseModel):
    id: str
    name: str
    created_at: datetime
    updated_at: datetime
    summary: dict = Field(default_factory=dict)


class SweepConfig(BaseModel):
    model_config = {"extra": "forbid"}

    config_json: dict
    rxdelay_range: str
    txdelay_range: str
    direct_txdelay_range: str
    seeds: int = 3
    jobs: int = 1


class SweepStatus(BaseModel):
    id: str
    status: str
    created_at: datetime
    completed_at: Optional[datetime] = None
    progress: int = 0
    total: int = 0
    results: Optional[list] = None


class ValidationResult(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
