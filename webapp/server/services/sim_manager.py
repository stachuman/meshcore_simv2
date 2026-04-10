"""SimManager service -- manages orchestrator subprocess lifecycle, progress
parsing, and simulation data storage.

Bridges the web frontend and the orchestrator binary:
1. Writes config JSON to disk
2. Spawns the orchestrator binary as a subprocess
3. Captures stdout (NDJSON events) to a file
4. Parses stderr for progress updates (verbose timestamps + summary lines)
5. Streams progress to SSE subscribers via asyncio.Queue
6. Manages simulation lifecycle (create, list, cancel, delete)
"""

import asyncio
import json
import logging
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SimRecord:
    """In-memory record of a simulation's state."""

    id: str
    status: str  # pending, running, completed, failed, cancelled
    created_at: float
    completed_at: Optional[float] = None
    config: dict = field(default_factory=dict)
    error: Optional[str] = None
    pid: Optional[int] = None
    progress_pct: int = 0
    progress_detail: str = ""


class SimManager:
    """Manages orchestrator subprocess lifecycle and simulation data."""

    def __init__(
        self,
        data_dir: Path,
        orchestrator_path: str,
        max_concurrent: int = 4,
    ):
        self.data_dir = data_dir
        self.orchestrator_path = orchestrator_path
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._sims: dict[str, SimRecord] = {}  # in-memory registry
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._progress_subscribers: dict[str, list[asyncio.Queue]] = {}
        self._scan_existing()  # load completed sims from disk on startup

    # ------------------------------------------------------------------
    # Startup scan
    # ------------------------------------------------------------------

    def _scan_existing(self) -> None:
        """Scan data_dir/simulations/ for existing simulation directories.

        Populates the in-memory registry with any simulations that were
        previously run (completed, failed, or cancelled).  Running/pending
        sims from a prior process are marked as failed since the subprocess
        is no longer alive.
        """
        sims_dir = self.data_dir / "simulations"
        if not sims_dir.exists():
            return

        for entry in sorted(sims_dir.iterdir()):
            if not entry.is_dir():
                continue

            sim_id = entry.name
            config_path = entry / "config.json"
            events_path = entry / "events.ndjson"
            status_path = entry / "status.json"

            if not status_path.exists():
                # No status file -- infer from presence of events
                status = "completed" if events_path.exists() else "failed"
                created_at = os.path.getctime(str(entry))
                config = self._load_json(config_path)
                self._sims[sim_id] = SimRecord(
                    id=sim_id,
                    status=status,
                    created_at=created_at,
                    config=config,
                )
                continue

            status_data = self._load_json(status_path)
            config = self._load_json(config_path)

            raw_status = status_data.get("status", "failed")
            # If a previous server died while a sim was running/pending,
            # mark it as failed since the subprocess no longer exists.
            if raw_status in ("running", "pending"):
                raw_status = "failed"
                status_data["error"] = status_data.get(
                    "error", "Server restarted while simulation was in progress"
                )

            self._sims[sim_id] = SimRecord(
                id=sim_id,
                status=raw_status,
                created_at=status_data.get(
                    "created_at", os.path.getctime(str(entry))
                ),
                completed_at=status_data.get("completed_at"),
                config=config,
                error=status_data.get("error"),
            )

        logger.info(
            "Scanned %d existing simulation(s) from %s", len(self._sims), sims_dir
        )

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _sim_dir(self, sim_id: str) -> Path:
        return self.data_dir / "simulations" / sim_id

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_sim(self, config: dict) -> str:
        """Create a new simulation, write config, start orchestrator subprocess.

        Returns the newly generated simulation ID.
        """
        sim_id = self._generate_id()
        sim_dir = self._sim_dir(sim_id)
        sim_dir.mkdir(parents=True, exist_ok=True)

        # Write config to disk
        config_path = sim_dir / "config.json"
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)

        record = SimRecord(
            id=sim_id,
            status="pending",
            created_at=time.time(),
            config=config,
        )
        self._sims[sim_id] = record
        self._save_status(sim_id)

        # Start the orchestrator in the background
        asyncio.create_task(self._run_sim(sim_id))
        return sim_id

    def get_sim(self, sim_id: str) -> Optional[SimRecord]:
        """Return the SimRecord for a simulation, or None."""
        return self._sims.get(sim_id)

    def list_sims(self) -> list[SimRecord]:
        """Return all simulations, newest first."""
        return sorted(self._sims.values(), key=lambda s: s.created_at, reverse=True)

    async def cancel_sim(self, sim_id: str) -> bool:
        """Cancel a running simulation by terminating its subprocess.

        Returns True if the process was found and terminated.
        """
        proc = self._processes.get(sim_id)
        if proc is None:
            return False

        proc.terminate()
        record = self._sims.get(sim_id)
        if record:
            record.status = "cancelled"
            record.completed_at = time.time()
            self._save_status(sim_id)
            self._notify_progress(sim_id, {"status": "cancelled"})
            self._notify_progress(sim_id, None)  # sentinel for SSE close
        return True

    async def delete_sim(self, sim_id: str) -> bool:
        """Delete a simulation and all its data from disk.

        Cancels the simulation first if it is still running.
        Returns True if the simulation existed.
        """
        if sim_id not in self._sims:
            return False

        await self.cancel_sim(sim_id)

        sim_dir = self._sim_dir(sim_id)
        if sim_dir.exists():
            shutil.rmtree(sim_dir)

        self._sims.pop(sim_id, None)
        self._progress_subscribers.pop(sim_id, None)
        return True

    def get_events_path(self, sim_id: str) -> Optional[Path]:
        """Return path to events.ndjson if it exists on disk."""
        p = self._sim_dir(sim_id) / "events.ndjson"
        return p if p.exists() else None

    def get_config_path(self, sim_id: str) -> Optional[Path]:
        """Return path to config.json if it exists on disk."""
        p = self._sim_dir(sim_id) / "config.json"
        return p if p.exists() else None

    # ------------------------------------------------------------------
    # SSE progress subscription
    # ------------------------------------------------------------------

    def subscribe_progress(self, sim_id: str) -> asyncio.Queue:
        """Subscribe to progress updates for a simulation.

        Returns an asyncio.Queue that will receive dicts with progress
        information.  A ``None`` sentinel signals the end of updates.
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._progress_subscribers.setdefault(sim_id, []).append(q)

        # If simulation already finished, send final state immediately
        record = self._sims.get(sim_id)
        if record and record.status in ("completed", "failed", "cancelled"):
            try:
                q.put_nowait({"status": record.status, "progress": record.progress_pct / 100.0})
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass
        return q

    def unsubscribe_progress(self, sim_id: str, q: asyncio.Queue) -> None:
        """Remove a progress subscriber queue."""
        queues = self._progress_subscribers.get(sim_id, [])
        try:
            queues.remove(q)
        except ValueError:
            pass
        # Clean up empty subscriber lists
        if not queues:
            self._progress_subscribers.pop(sim_id, None)

    # ------------------------------------------------------------------
    # Subprocess management (private)
    # ------------------------------------------------------------------

    async def _run_sim(self, sim_id: str) -> None:
        """Run orchestrator as subprocess, capture output, parse progress."""
        record = self._sims[sim_id]
        sim_dir = self._sim_dir(sim_id)
        config_path = sim_dir / "config.json"
        events_path = sim_dir / "events.ndjson"

        async with self._semaphore:
            record.status = "running"
            self._save_status(sim_id)
            self._notify_progress(sim_id, {
                "status": "running",
                "progress": 0,
                "phase": "init",
                "message": "Starting orchestrator...",
            })

            try:
                proc = await asyncio.create_subprocess_exec(
                    self.orchestrator_path,
                    str(config_path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                self._processes[sim_id] = proc
                record.pid = proc.pid

                # Extract duration_ms from config for progress estimation
                duration_ms = self._extract_duration_ms(record.config)

                # Pipe stdout (NDJSON events) to file
                stdout_task = asyncio.create_task(
                    self._pipe_to_file(proc.stdout, events_path)
                )
                # Parse stderr for progress
                stderr_task = asyncio.create_task(
                    self._parse_stderr(sim_id, proc.stderr, duration_ms)
                )

                await proc.wait()
                await stdout_task
                stderr_output = await stderr_task

                if proc.returncode == 0:
                    record.status = "completed"
                    record.progress_pct = 100
                    self._notify_progress(
                        sim_id, {"status": "completed", "progress": 1.0}
                    )
                elif record.status == "cancelled":
                    # Already handled by cancel_sim
                    pass
                else:
                    record.status = "failed"
                    record.error = (
                        stderr_output[-2000:] if stderr_output else "Unknown error"
                    )
                    self._notify_progress(
                        sim_id, {"status": "failed", "error": record.error}
                    )

            except FileNotFoundError:
                msg = f"Orchestrator binary not found: {self.orchestrator_path}"
                logger.error(msg)
                record.status = "failed"
                record.error = msg
                self._notify_progress(sim_id, {"status": "failed", "error": msg})
            except Exception as e:
                logger.exception("Simulation %s failed with unexpected error", sim_id)
                record.status = "failed"
                record.error = str(e)
                self._notify_progress(
                    sim_id, {"status": "failed", "error": str(e)}
                )
            finally:
                record.completed_at = time.time()
                self._processes.pop(sim_id, None)
                self._save_status(sim_id)
                # Send sentinel to close all SSE connections
                self._notify_progress(sim_id, None)

    async def _pipe_to_file(
        self, stream: asyncio.StreamReader, path: Path
    ) -> None:
        """Pipe subprocess stdout to a file in chunks."""
        with open(path, "wb") as f:
            while True:
                chunk = await stream.read(65536)
                if not chunk:
                    break
                f.write(chunk)

    async def _parse_stderr(
        self,
        sim_id: str,
        stream: asyncio.StreamReader,
        duration_ms: Optional[int],
    ) -> str:
        """Parse orchestrator stderr for progress updates.

        The orchestrator emits structured progress lines:
            [PROGRESS] init: 3 nodes, step=1ms, duration=90.0s
            [PROGRESS] hot-start: exchanging adverts (3 nodes)
            [PROGRESS] hot-start: complete
            [PROGRESS] warmup: instant delivery for 5.0s
            [PROGRESS] 50% (45.0s / 90.0s)
            [PROGRESS] running: physics simulation started
        and summary lines like:
            === Simulation Summary (60.0s) ===

        Returns the full stderr text (for error reporting on failure).
        """
        record = self._sims[sim_id]
        full_output: list[str] = []

        # [PROGRESS] phase: message  OR  [PROGRESS] 50% (45.0s / 90.0s)
        progress_phase_pat = re.compile(
            r"^\[PROGRESS\]\s+(\w[\w-]*):\s*(.*)"
        )
        progress_pct_pat = re.compile(
            r"^\[PROGRESS\]\s+(\d+)%\s+\(([^)]+)\)"
        )
        # Regex for orchestrator timestamp: [  10.500s] (verbose mode fallback)
        ts_pattern = re.compile(r"^\[\s*([\d.]+)s\]")

        last_notified_pct = -1
        in_summary = False

        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            full_output.append(text)

            # Once summary header is seen, forward ALL remaining lines
            if "=== Simulation Summary" in text:
                in_summary = True
            if in_summary:
                self._notify_progress(sim_id, {
                    "status": "running",
                    "detail": text,
                })
                continue

            # Structured progress: phase messages (init, hot-start, warmup, running)
            phase_match = progress_phase_pat.match(text)
            if phase_match:
                phase = phase_match.group(1)
                message = phase_match.group(2)
                self._notify_progress(sim_id, {
                    "status": "running",
                    "phase": phase,
                    "message": message,
                })
                continue

            # Structured progress: percentage
            pct_match = progress_pct_pat.match(text)
            if pct_match:
                pct = min(int(pct_match.group(1)), 99)
                time_info = pct_match.group(2)
                if pct > last_notified_pct:
                    last_notified_pct = pct
                    record.progress_pct = pct
                    self._notify_progress(sim_id, {
                        "status": "running",
                        "progress": pct / 100.0,
                        "message": time_info,
                    })
                continue

            # Verbose mode fallback: extract sim time from [  10.500s] lines
            ts_match = ts_pattern.search(text)
            if ts_match and duration_ms and duration_ms > 0:
                sim_time_s = float(ts_match.group(1))
                sim_time_ms = sim_time_s * 1000.0
                pct = int(min(sim_time_ms / duration_ms * 100, 99))
                if pct > last_notified_pct:
                    last_notified_pct = pct
                    record.progress_pct = pct
                    self._notify_progress(sim_id, {
                        "status": "running",
                        "progress": pct / 100.0,
                    })
                continue

        return "\n".join(full_output)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _save_status(self, sim_id: str) -> None:
        """Persist simulation status to disk as status.json."""
        record = self._sims.get(sim_id)
        if not record:
            return
        status_path = self._sim_dir(sim_id) / "status.json"
        status_data = {
            "status": record.status,
            "created_at": record.created_at,
            "completed_at": record.completed_at,
            "error": record.error,
        }
        try:
            with open(status_path, "w") as f:
                json.dump(status_data, f, indent=2)
        except OSError:
            logger.exception("Failed to save status for sim %s", sim_id)

    def _notify_progress(self, sim_id: str, data) -> None:
        """Push progress update to all SSE subscriber queues.

        A ``None`` data value acts as a sentinel, signaling that no more
        updates will follow (SSE endpoint should close).
        """
        queues = self._progress_subscribers.get(sim_id, [])
        for q in queues:
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                pass  # drop if subscriber is slow

    @staticmethod
    def _extract_duration_ms(config: dict) -> Optional[int]:
        """Extract simulation duration_ms from a config dict.

        Returns None if the value is not present or not a positive number.
        """
        sim = config.get("simulation", {})
        val = sim.get("duration_ms")
        if val is not None and isinstance(val, (int, float)) and val > 0:
            return int(val)
        return None

    @staticmethod
    def _load_json(path: Path) -> dict:
        """Load a JSON file, returning an empty dict on any error."""
        if not path.exists():
            return {}
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    @staticmethod
    def _generate_id() -> str:
        """Generate a short unique simulation ID (12 hex chars)."""
        return uuid.uuid4().hex[:12]
