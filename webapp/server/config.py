import os
from pathlib import Path


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

    @classmethod
    def get(cls) -> "Settings":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
