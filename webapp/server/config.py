import os
import re
from pathlib import Path

# Filesystem-safe ID: hex chars only (uuid4().hex[:12] format)
_SAFE_ID_RE = re.compile(r"^[a-f0-9]{1,64}$")


def validate_safe_id(value: str, label: str = "ID") -> str:
    """Raise ValueError if *value* is not a safe filesystem ID."""
    if not _SAFE_ID_RE.match(value):
        raise ValueError(f"Invalid {label}: must be 1-64 lowercase hex characters")
    return value


class Settings:
    """Application settings loaded from environment variables."""

    _instance: "Settings | None" = None

    def __init__(self) -> None:
        self.DATA_DIR: Path = Path(os.environ.get("DATA_DIR", "./data"))
        self.ORCHESTRATOR_PATH: Path = Path(
            os.environ.get("ORCHESTRATOR_PATH", "../build/orchestrator/orchestrator")
        )
        self.MAX_CONCURRENT_SIMS: int = int(
            os.environ.get("MAX_CONCURRENT_SIMS", str(os.cpu_count() or 4))
        )
        self.MAX_INTERACTIVE_SESSIONS: int = int(
            os.environ.get("MAX_INTERACTIVE_SESSIONS", "4")
        )
        # Safety net for sessions whose WebSocket closed without a clean
        # DELETE (e.g. browser crash, hard tab-kill). Normal navigation
        # away from the interactive view triggers an explicit DELETE via
        # the `pagehide` handler in interactive.html, so this timeout is
        # only the fallback for unclean shutdowns.
        self.INTERACTIVE_IDLE_TIMEOUT_S: int = int(
            os.environ.get("INTERACTIVE_IDLE_TIMEOUT_S", "60")
        )

    @classmethod
    def get(cls) -> "Settings":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
