"""SweepRunner service -- runs parameter sweeps over rxdelay/txdelay/direct.txdelay.

Adapted from tools/optimize_delays.py but designed for async web usage:
- Uses ProcessPoolExecutor for parallel orchestrator invocations
- Publishes progress via asyncio.Queue (SSE pattern, same as SimManager)
- Persists results to data/sweeps/{id}/ on disk
- Aggregates per-combo stats (mean/std/min/max across seeds)
"""

import asyncio
import copy
import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level function for ProcessPoolExecutor (must be picklable)
# ---------------------------------------------------------------------------

def _safe_pct(num: int, den: int) -> int:
    """Compute integer percentage, returning -1 if denominator is zero."""
    return int(round(num * 100.0 / den)) if den > 0 else -1


def _parse_sim_summary(stdout: str) -> dict | None:
    """Find and parse the sim_summary JSON line from NDJSON stdout."""
    for line in stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
            if obj.get("type") == "sim_summary":
                return obj
        except json.JSONDecodeError:
            continue
    return None


def _run_single(orchestrator_path: str, config: dict, rxd: float, txd: float,
                dtxd: float, seed: int, run_id: int) -> dict:
    """Run one orchestrator invocation with a specific seed.

    This is a module-level function so it can be pickled and sent to a
    worker process via ProcessPoolExecutor.  Parses the sim_summary JSON
    event from stdout for structured metrics.
    """
    cfg = copy.deepcopy(config)
    cfg.setdefault("simulation", {})["seed"] = seed

    # Inject set commands just after warmup (using @repeaters shorthand)
    warmup_ms = cfg.get("simulation", {}).get("warmup_ms", 0)
    inject_ms = warmup_ms + 1
    set_cmds = [
        {"at_ms": inject_ms, "node": "@repeaters", "command": f"set rxdelay {rxd}"},
        {"at_ms": inject_ms, "node": "@repeaters", "command": f"set txdelay {txd}"},
        {"at_ms": inject_ms, "node": "@repeaters", "command": f"set direct.txdelay {dtxd}"},
    ]

    cfg["commands"] = set_cmds + cfg.get("commands", [])
    cfg.pop("expect", None)

    error_result = {
        "rxd": rxd, "txd": txd, "dtxd": dtxd, "seed": seed,
        "delivered": 0, "sent": 0, "pct": 0,
        "ack_pct": -1, "chan_pct": -1,
        "summary": "", "returncode": -1,
    }

    tmp_path = os.path.join(tempfile.gettempdir(), f"sweep_{run_id}_{seed}.json")
    try:
        with open(tmp_path, "w") as f:
            json.dump(cfg, f)

        result = subprocess.run(
            [orchestrator_path, tmp_path],
            capture_output=True, text=True, timeout=600
        )

        stderr = result.stderr
        summary_match = re.search(r"(=== Simulation Summary.*)", stderr, re.DOTALL)
        summary = summary_match.group(1).strip() if summary_match else ""

        s = _parse_sim_summary(result.stdout)
        if not s:
            return {**error_result, "summary": summary or "NO SUMMARY",
                    "returncode": result.returncode}

        d = s.get("delivery", {})
        a = s.get("acks", {})
        ch = s.get("channel", {})

        return {
            "rxd": rxd, "txd": txd, "dtxd": dtxd, "seed": seed,
            "delivered": d.get("received", 0),
            "sent": d.get("sent", 0),
            "pct": _safe_pct(d.get("received", 0), d.get("sent", 0)),
            "ack_pct": _safe_pct(a.get("received", 0), a.get("pending", 0)),
            "chan_pct": _safe_pct(ch.get("received", 0), ch.get("expected", 0)),
            "summary": summary, "returncode": result.returncode,
        }

    except subprocess.TimeoutExpired:
        return {**error_result, "summary": "TIMEOUT"}
    except Exception as e:
        return {**error_result, "summary": f"ERROR: {e}"}
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _submit_and_get(executor: ProcessPoolExecutor, work_item: tuple) -> dict:
    """Submit a single _run_single call to the executor and return its result.

    This wrapper exists so we can use ``loop.run_in_executor`` to bridge
    the synchronous ProcessPoolExecutor.submit/future.result() pattern
    into asyncio-friendly awaitables.  Called from a thread via
    ``loop.run_in_executor(None, ...)``.
    """
    future = executor.submit(_run_single, *work_item)
    return future.result()


# ---------------------------------------------------------------------------
# Helper: generate range from params dict
# ---------------------------------------------------------------------------

def _make_range(spec: dict) -> list[float]:
    """Generate a list of floats from {"min": lo, "max": hi, "step": step}.

    If step is 0 or min == max, returns [min].
    """
    lo = float(spec.get("min", 0))
    hi = float(spec.get("max", lo))
    step = float(spec.get("step", 0))
    if step <= 0 or lo == hi:
        return [lo]
    n = max(0, int(round((hi - lo) / step)) + 1)
    return [round(lo + i * step, 4) for i in range(n)]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SweepRecord:
    """In-memory record of a sweep's state."""

    id: str
    status: str  # pending, running, completed, failed, cancelled
    config: dict  # base orchestrator config
    params: dict  # parameter range specs
    seeds: list[int]
    created_at: float
    completed_at: Optional[float] = None
    error: Optional[str] = None
    results: list[dict] = field(default_factory=list)       # aggregated
    raw_results: list[dict] = field(default_factory=list)    # per-run
    progress_pct: int = 0
    total_runs: int = 0
    completed_runs: int = 0


# ---------------------------------------------------------------------------
# SweepRunner
# ---------------------------------------------------------------------------

class SweepRunner:
    """Manages parameter sweep lifecycle, subprocess pool, and progress."""

    def __init__(
        self,
        data_dir: Path,
        orchestrator_path: str,
        max_workers: int = 4,
    ):
        self.data_dir = data_dir
        self.orchestrator_path = orchestrator_path
        self.max_workers = max_workers
        self._sweeps: dict[str, SweepRecord] = {}
        self._progress_subscribers: dict[str, list[asyncio.Queue]] = {}
        self._cancel_flags: dict[str, bool] = {}
        self._scan_existing()

    # ------------------------------------------------------------------
    # Startup scan
    # ------------------------------------------------------------------

    def _scan_existing(self) -> None:
        """Load previously completed sweeps from disk."""
        sweeps_dir = self.data_dir / "sweeps"
        if not sweeps_dir.exists():
            return

        for entry in sorted(sweeps_dir.iterdir()):
            if not entry.is_dir():
                continue

            sweep_id = entry.name
            status_path = entry / "status.json"
            if not status_path.exists():
                continue

            try:
                status_data = json.loads(status_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                # Don't crash startup on one bad record, but log loud enough
                # that a user investigating missing sweeps can find the cause.
                logger.warning(
                    "sweep_runner: skipping sweep %s — cannot read %s (%s)",
                    sweep_id, status_path, e
                )
                continue

            raw_status = status_data.get("status", "failed")
            # Mark stale running/pending sweeps as failed
            if raw_status in ("running", "pending"):
                raw_status = "failed"
                status_data["error"] = status_data.get(
                    "error", "Server restarted while sweep was in progress"
                )

            # Load results if available
            results = []
            raw_results = []
            results_path = entry / "results.json"
            if results_path.exists():
                try:
                    rdata = json.loads(results_path.read_text(encoding="utf-8"))
                    results = rdata.get("aggregated", [])
                    raw_results = rdata.get("raw", [])
                except (json.JSONDecodeError, OSError):
                    pass

            self._sweeps[sweep_id] = SweepRecord(
                id=sweep_id,
                status=raw_status,
                config=status_data.get("config", {}),
                params=status_data.get("params", {}),
                seeds=status_data.get("seeds", []),
                created_at=status_data.get("created_at", 0),
                completed_at=status_data.get("completed_at"),
                error=status_data.get("error"),
                results=results,
                raw_results=raw_results,
                total_runs=status_data.get("total_runs", 0),
                completed_runs=status_data.get("completed_runs", 0),
                progress_pct=100 if raw_status == "completed" else 0,
            )

        logger.info(
            "Scanned %d existing sweep(s) from %s",
            len(self._sweeps), sweeps_dir,
        )

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _sweep_dir(self, sweep_id: str) -> Path:
        return self.data_dir / "sweeps" / sweep_id

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_sweep(
        self,
        config: dict,
        params: dict,
        seeds: list[int],
        max_workers: Optional[int] = None,
    ) -> str:
        """Create and start a sweep. Returns sweep_id."""
        sweep_id = uuid.uuid4().hex[:12]
        sweep_dir = self._sweep_dir(sweep_id)
        sweep_dir.mkdir(parents=True, exist_ok=True)

        # Determine actual worker count
        workers = min(
            max_workers or self.max_workers,
            self.max_workers,
        )

        # Build param ranges
        rxd_vals = _make_range(params.get("rxdelay", {"min": 0, "max": 0, "step": 0}))
        txd_vals = _make_range(params.get("txdelay", {"min": 0, "max": 0, "step": 0}))
        dtxd_vals = _make_range(params.get("direct_txdelay", {"min": 0, "max": 0, "step": 0}))

        n_combos = len(rxd_vals) * len(txd_vals) * len(dtxd_vals)
        total_runs = n_combos * len(seeds)

        record = SweepRecord(
            id=sweep_id,
            status="pending",
            config=config,
            params=params,
            seeds=seeds,
            created_at=time.time(),
            total_runs=total_runs,
        )
        self._sweeps[sweep_id] = record
        self._cancel_flags[sweep_id] = False

        # Write config to disk
        config_path = sweep_dir / "config.json"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

        self._save_status(sweep_id)

        # Start the sweep in the background
        asyncio.create_task(
            self._run_sweep(sweep_id, config, rxd_vals, txd_vals, dtxd_vals, seeds, workers)
        )
        return sweep_id

    def get_sweep(self, sweep_id: str) -> Optional[SweepRecord]:
        """Return the SweepRecord for a sweep, or None."""
        return self._sweeps.get(sweep_id)

    def list_sweeps(self) -> list[SweepRecord]:
        """Return all sweeps, newest first."""
        return sorted(self._sweeps.values(), key=lambda s: s.created_at, reverse=True)

    async def delete_sweep(self, sweep_id: str) -> bool:
        """Cancel (if running) and delete a sweep and all its data.

        Returns True if the sweep existed.
        """
        if sweep_id not in self._sweeps:
            return False

        # Signal cancellation
        self._cancel_flags[sweep_id] = True

        record = self._sweeps.get(sweep_id)
        if record and record.status == "running":
            record.status = "cancelled"
            record.completed_at = time.time()
            self._save_status(sweep_id)
            self._notify_progress(sweep_id, {"status": "cancelled"})
            self._notify_progress(sweep_id, None)

        # Remove from disk
        sweep_dir = self._sweep_dir(sweep_id)
        if sweep_dir.exists():
            shutil.rmtree(sweep_dir)

        self._sweeps.pop(sweep_id, None)
        self._cancel_flags.pop(sweep_id, None)
        self._progress_subscribers.pop(sweep_id, None)
        return True

    # ------------------------------------------------------------------
    # SSE progress subscription
    # ------------------------------------------------------------------

    def subscribe_progress(self, sweep_id: str) -> asyncio.Queue:
        """Subscribe to progress updates for a sweep.

        Returns an asyncio.Queue that will receive dicts with progress
        information.  A ``None`` sentinel signals the end of updates.
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._progress_subscribers.setdefault(sweep_id, []).append(q)

        # If sweep already finished, send final state immediately
        record = self._sweeps.get(sweep_id)
        if record and record.status in ("completed", "failed", "cancelled"):
            try:
                q.put_nowait({
                    "status": record.status,
                    "progress_pct": record.progress_pct,
                    "completed_runs": record.completed_runs,
                    "total_runs": record.total_runs,
                })
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass
        return q

    def unsubscribe_progress(self, sweep_id: str, q: asyncio.Queue) -> None:
        """Remove a progress subscriber queue."""
        queues = self._progress_subscribers.get(sweep_id, [])
        try:
            queues.remove(q)
        except ValueError:
            pass
        if not queues:
            self._progress_subscribers.pop(sweep_id, None)

    # ------------------------------------------------------------------
    # Core sweep execution (private)
    # ------------------------------------------------------------------

    async def _run_sweep(
        self,
        sweep_id: str,
        config: dict,
        rxd_vals: list[float],
        txd_vals: list[float],
        dtxd_vals: list[float],
        seeds: list[int],
        max_workers: int,
    ) -> None:
        """Execute the full parameter sweep using a process pool."""
        record = self._sweeps[sweep_id]
        record.status = "running"
        self._save_status(sweep_id)
        self._notify_progress(sweep_id, {
            "status": "running",
            "progress_pct": 0,
            "completed_runs": 0,
            "total_runs": record.total_runs,
        })

        loop = asyncio.get_event_loop()

        try:
            # Build all work items
            work_items: list[tuple] = []
            run_id = 0
            for rxd in rxd_vals:
                for txd in txd_vals:
                    for dtxd in dtxd_vals:
                        for seed in seeds:
                            work_items.append((
                                self.orchestrator_path, config,
                                rxd, txd, dtxd, seed, run_id,
                            ))
                            run_id += 1

            raw_results: list[dict] = []
            completed_count = 0

            # Run in process pool.  We create the executor and use
            # loop.run_in_executor with _submit_and_get to bridge the
            # synchronous ProcessPoolExecutor into async awaitables.
            executor = ProcessPoolExecutor(max_workers=max_workers)
            try:
                pending_tasks = [
                    loop.run_in_executor(None, _submit_and_get, executor, w)
                    for w in work_items
                ]

                for coro in asyncio.as_completed(pending_tasks):
                    result = await coro

                    # Check cancellation
                    if self._cancel_flags.get(sweep_id, False):
                        if record.status != "cancelled":
                            record.status = "cancelled"
                            record.completed_at = time.time()
                            self._save_status(sweep_id)
                            self._notify_progress(sweep_id, {"status": "cancelled"})
                            self._notify_progress(sweep_id, None)
                        return

                    raw_results.append(result)
                    completed_count += 1
                    record.completed_runs = completed_count
                    record.progress_pct = int(
                        completed_count / record.total_runs * 100
                    ) if record.total_runs > 0 else 0

                    self._notify_progress(sweep_id, {
                        "status": "running",
                        "progress_pct": record.progress_pct,
                        "completed_runs": completed_count,
                        "total_runs": record.total_runs,
                        "last_result": {
                            "rxd": result["rxd"],
                            "txd": result["txd"],
                            "dtxd": result["dtxd"],
                            "seed": result["seed"],
                            "pct": result["pct"],
                        },
                    })
            finally:
                executor.shutdown(wait=False, cancel_futures=True)

            # Aggregate results
            record.raw_results = raw_results
            record.results = self._aggregate(raw_results)
            record.status = "completed"
            record.completed_at = time.time()
            record.progress_pct = 100

            # Save to disk
            self._save_results(sweep_id)
            self._save_status(sweep_id)

            self._notify_progress(sweep_id, {
                "status": "completed",
                "progress_pct": 100,
                "completed_runs": record.total_runs,
                "total_runs": record.total_runs,
            })

        except Exception as e:
            logger.exception("Sweep %s failed with unexpected error", sweep_id)
            record.status = "failed"
            record.error = str(e)
            record.completed_at = time.time()
            self._save_status(sweep_id)
            self._notify_progress(sweep_id, {
                "status": "failed",
                "error": str(e),
            })
        finally:
            self._cancel_flags.pop(sweep_id, None)
            # Send sentinel to close all SSE connections
            self._notify_progress(sweep_id, None)

    # ------------------------------------------------------------------
    # Aggregation (same logic as optimize_delays.py)
    # ------------------------------------------------------------------

    @staticmethod
    def _aggregate(raw_results: list[dict]) -> list[dict]:
        """Group raw results by (rxd, txd, dtxd) and compute stats."""
        combos: dict[tuple, list[dict]] = {}
        for r in raw_results:
            key = (r["rxd"], r["txd"], r["dtxd"])
            combos.setdefault(key, []).append(r)

        aggregated = []
        for (rxd, txd, dtxd), runs in combos.items():
            pcts = [r["pct"] for r in runs]
            ack_pcts = [r["ack_pct"] for r in runs if r["ack_pct"] >= 0]
            chan_pcts = [r["chan_pct"] for r in runs if r["chan_pct"] >= 0]
            total_delivered = sum(r["delivered"] for r in runs)
            total_sent = sum(r["sent"] for r in runs)

            mean_pct = sum(pcts) / len(pcts) if pcts else 0
            std_pct = (
                math.sqrt(sum((p - mean_pct) ** 2 for p in pcts) / (len(pcts) - 1))
                if len(pcts) > 1 else 0.0
            )
            min_pct = min(pcts) if pcts else 0
            max_pct = max(pcts) if pcts else 0
            mean_ack = sum(ack_pcts) / len(ack_pcts) if ack_pcts else -1
            mean_chan = sum(chan_pcts) / len(chan_pcts) if chan_pcts else -1

            aggregated.append({
                "rxd": rxd,
                "txd": txd,
                "dtxd": dtxd,
                "mean_pct": round(mean_pct, 1),
                "std_pct": round(std_pct, 1),
                "min_pct": min_pct,
                "max_pct": max_pct,
                "total_delivered": total_delivered,
                "total_sent": total_sent,
                "mean_ack": round(mean_ack, 1) if mean_ack >= 0 else -1,
                "mean_chan": round(mean_chan, 1) if mean_chan >= 0 else -1,
                "n_seeds": len(runs),
            })

        # Sort by mean delivery % descending, then by std ascending
        aggregated.sort(key=lambda a: (a["mean_pct"], -a["std_pct"]), reverse=True)
        return aggregated

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _save_status(self, sweep_id: str) -> None:
        """Persist sweep status to disk as status.json."""
        record = self._sweeps.get(sweep_id)
        if not record:
            return
        status_path = self._sweep_dir(sweep_id) / "status.json"
        status_data = {
            "status": record.status,
            "created_at": record.created_at,
            "completed_at": record.completed_at,
            "error": record.error,
            "params": record.params,
            "seeds": record.seeds,
            "config": record.config,
            "total_runs": record.total_runs,
            "completed_runs": record.completed_runs,
        }
        try:
            status_path.write_text(
                json.dumps(status_data, indent=2), encoding="utf-8"
            )
        except OSError:
            logger.exception("Failed to save status for sweep %s", sweep_id)

    def _save_results(self, sweep_id: str) -> None:
        """Persist sweep results to disk as results.json."""
        record = self._sweeps.get(sweep_id)
        if not record:
            return
        results_path = self._sweep_dir(sweep_id) / "results.json"
        results_data = {
            "aggregated": record.results,
            "raw": record.raw_results,
        }
        try:
            results_path.write_text(
                json.dumps(results_data, indent=2), encoding="utf-8"
            )
        except OSError:
            logger.exception("Failed to save results for sweep %s", sweep_id)

    # ------------------------------------------------------------------
    # SSE notification
    # ------------------------------------------------------------------

    def _notify_progress(self, sweep_id: str, data) -> None:
        """Push progress update to all SSE subscriber queues.

        A ``None`` data value acts as a sentinel, signaling that no more
        updates will follow (SSE endpoint should close).
        """
        queues = self._progress_subscribers.get(sweep_id, [])
        for q in queues:
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                pass  # drop if subscriber is slow
