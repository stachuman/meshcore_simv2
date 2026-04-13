"""InteractiveSessionManager -- manages orchestrator -i subprocess lifecycle
for WebSocket-based interactive simulation control.

Each session spawns an ``orchestrator -i config.json`` subprocess and provides:
- Command serialization (asyncio.Lock per session)
- Stdout classification (``> `` prefix = response, JSON = event, else drop)
- Event persistence to ``events.ndjson``
- WebSocket client registration + broadcast
- Idle timeout cleanup
- Export to ``data/simulations/`` for visualization compatibility
"""

import asyncio
import json
import logging
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastapi import WebSocket

logger = logging.getLogger(__name__)


@dataclass
class InteractiveSession:
    """In-memory record of an interactive session."""

    id: str
    status: str  # starting, ready, closed
    created_at: float
    config: dict = field(default_factory=dict)
    last_activity: float = 0.0
    time_ms: int = 0
    step_ms: int = 5
    nodes: list = field(default_factory=list)
    error: Optional[str] = None


class InteractiveSessionManager:
    """Manages interactive orchestrator sessions with WebSocket relay."""

    def __init__(
        self,
        data_dir: Path,
        orchestrator_path: str,
        max_sessions: int = 4,
        idle_timeout_s: int = 300,
    ):
        self.data_dir = data_dir
        self.orchestrator_path = orchestrator_path
        self.max_sessions = max_sessions
        self.idle_timeout_s = idle_timeout_s

        self._sessions: dict[str, InteractiveSession] = {}
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._ws_clients: dict[str, list[WebSocket]] = {}
        self._response_queues: dict[str, asyncio.Queue] = {}
        self._cmd_locks: dict[str, asyncio.Lock] = {}
        self._reader_tasks: dict[str, asyncio.Task] = {}
        self._event_files: dict[str, object] = {}  # open file handles
        self._cleanup_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start_cleanup_loop(self) -> None:
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def shutdown(self) -> None:
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        for sid in list(self._sessions):
            await self.close_session(sid, reason="shutdown")

    # ------------------------------------------------------------------
    # Session CRUD
    # ------------------------------------------------------------------

    async def create_session(self, config: dict) -> InteractiveSession:
        """Create a new interactive session, spawn orchestrator -i."""
        active = sum(
            1 for s in self._sessions.values() if s.status != "closed"
        )
        if active >= self.max_sessions:
            raise RuntimeError(
                f"Maximum interactive sessions ({self.max_sessions}) reached"
            )

        sid = uuid.uuid4().hex[:12]
        sess_dir = self._session_dir(sid)
        sess_dir.mkdir(parents=True, exist_ok=True)

        config_path = sess_dir / "config.json"
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)

        step_ms = config.get("simulation", {}).get("step_ms", 5)
        session = InteractiveSession(
            id=sid,
            status="starting",
            created_at=time.time(),
            last_activity=time.time(),
            config=config,
            step_ms=step_ms,
        )
        self._sessions[sid] = session
        self._ws_clients[sid] = []
        self._response_queues[sid] = asyncio.Queue()
        self._cmd_locks[sid] = asyncio.Lock()

        # Spawn orchestrator -i
        try:
            proc = await asyncio.create_subprocess_exec(
                self.orchestrator_path,
                "-i",
                str(config_path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._processes[sid] = proc

            # Open events file for append
            self._event_files[sid] = open(sess_dir / "events.ndjson", "w")

            # Start stdout reader
            self._reader_tasks[sid] = asyncio.create_task(
                self._read_stdout(sid)
            )

            # Probe with 'nodes' command to detect readiness
            # (response arrives after init + hot-start completes)
            proc.stdin.write(b"nodes\n")
            await proc.stdin.drain()

        except FileNotFoundError:
            session.status = "closed"
            session.error = f"Orchestrator not found: {self.orchestrator_path}"
            raise RuntimeError(session.error)
        except Exception as e:
            session.status = "closed"
            session.error = str(e)
            raise

        return session

    def get_session(self, sid: str) -> Optional[InteractiveSession]:
        return self._sessions.get(sid)

    def list_sessions(self) -> list[InteractiveSession]:
        return sorted(
            self._sessions.values(), key=lambda s: s.created_at, reverse=True
        )

    async def close_session(self, sid: str, reason: str = "closed") -> bool:
        session = self._sessions.get(sid)
        if not session:
            return False

        if session.status == "closed":
            return True

        session.status = "closed"

        # Kill process
        proc = self._processes.pop(sid, None)
        if proc and proc.returncode is None:
            try:
                proc.stdin.write(b"quit\n")
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=3)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass

        # Cancel reader task
        task = self._reader_tasks.pop(sid, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Close event file
        ef = self._event_files.pop(sid, None)
        if ef:
            try:
                ef.close()
            except OSError:
                pass

        # Broadcast close to WS clients
        await self._broadcast(sid, {"type": "closed", "reason": reason})

        # Clean up per-session state
        self._response_queues.pop(sid, None)
        self._cmd_locks.pop(sid, None)

        return True

    async def delete_session(self, sid: str) -> bool:
        await self.close_session(sid, reason="deleted")
        if sid not in self._sessions:
            return False
        sess_dir = self._session_dir(sid)
        if sess_dir.exists():
            shutil.rmtree(sess_dir)
        self._sessions.pop(sid, None)
        self._ws_clients.pop(sid, None)
        return True

    async def export_session(self, sid: str) -> Optional[str]:
        """Copy session events to data/simulations/ for visualization."""
        session = self._sessions.get(sid)
        if not session:
            return None

        sess_dir = self._session_dir(sid)
        events_src = sess_dir / "events.ndjson"
        config_src = sess_dir / "config.json"

        if not events_src.exists():
            return None

        sim_id = uuid.uuid4().hex[:12]
        sim_dir = self.data_dir / "simulations" / sim_id
        sim_dir.mkdir(parents=True, exist_ok=True)

        # Close event file if still open (to flush)
        ef = self._event_files.get(sid)
        if ef:
            try:
                ef.flush()
            except OSError:
                pass

        shutil.copy2(events_src, sim_dir / "events.ndjson")
        if config_src.exists():
            shutil.copy2(config_src, sim_dir / "config.json")

        # Write status.json
        status = {
            "status": "completed",
            "created_at": session.created_at,
            "completed_at": time.time(),
            "error": None,
        }
        with open(sim_dir / "status.json", "w") as f:
            json.dump(status, f, indent=2)

        return sim_id

    # ------------------------------------------------------------------
    # Command dispatch
    # ------------------------------------------------------------------

    async def send_command(self, sid: str, raw_cmd: str) -> dict:
        """Send a raw command to the orchestrator, wait for response.

        Uses a per-session lock to serialize commands (only one at a time).
        """
        session = self._sessions.get(sid)
        if not session or session.status == "closed":
            return {"error": "Session closed"}

        proc = self._processes.get(sid)
        if not proc or proc.returncode is not None:
            return {"error": "Process not running"}

        lock = self._cmd_locks.get(sid)
        if not lock:
            return {"error": "Session not initialized"}

        queue = self._response_queues.get(sid)
        if not queue:
            return {"error": "Session not initialized"}

        # Drain any stale responses
        while not queue.empty():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        async with lock:
            session.last_activity = time.time()
            try:
                proc.stdin.write((raw_cmd + "\n").encode())
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return {"error": "Process stdin broken"}

            try:
                response = await asyncio.wait_for(queue.get(), timeout=60)
                return response
            except asyncio.TimeoutError:
                return {"error": "Command timed out (60s)"}

    # ------------------------------------------------------------------
    # WebSocket management
    # ------------------------------------------------------------------

    async def register_ws(self, sid: str, ws: WebSocket) -> None:
        clients = self._ws_clients.get(sid)
        if clients is not None:
            clients.append(ws)

        session = self._sessions.get(sid)
        if session:
            session.last_activity = time.time()

    async def unregister_ws(self, sid: str, ws: WebSocket) -> None:
        clients = self._ws_clients.get(sid)
        if clients:
            try:
                clients.remove(ws)
            except ValueError:
                pass

    async def _broadcast(self, sid: str, msg: dict) -> None:
        clients = self._ws_clients.get(sid, [])
        dead = []
        for ws in clients:
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            try:
                clients.remove(ws)
            except ValueError:
                pass

    # ------------------------------------------------------------------
    # Stdout reader (core event loop)
    # ------------------------------------------------------------------

    async def _read_stdout(self, sid: str) -> None:
        """Continuously read orchestrator stdout, classify lines, persist + broadcast."""
        proc = self._processes.get(sid)
        session = self._sessions.get(sid)
        queue = self._response_queues.get(sid)

        if not proc or not session or not queue:
            return

        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break  # EOF = process exited

                text = line.decode("utf-8", errors="replace").rstrip()
                if not text:
                    continue

                if text.startswith("> "):
                    # Response line
                    payload = text[2:]
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError:
                        data = {"raw": payload}

                    # Check if this is the initial 'nodes' response (readiness)
                    if session.status == "starting" and isinstance(data, list):
                        session.nodes = data
                        session.status = "ready"
                        session.time_ms = 0
                        await self._broadcast(sid, {
                            "type": "ready",
                            "nodes": data,
                            "time_ms": 0,
                            "step_ms": session.step_ms,
                        })
                        # Put on queue too in case create_session's nodes
                        # command is being awaited via send_command
                        try:
                            queue.put_nowait(data)
                        except asyncio.QueueFull:
                            pass
                        continue

                    # Update time_ms from step/time/next responses
                    if isinstance(data, dict):
                        if "stepped_to_ms" in data:
                            session.time_ms = data["stepped_to_ms"]
                        elif "time_ms" in data:
                            session.time_ms = data["time_ms"]

                    # Put response on queue for send_command waiter
                    try:
                        queue.put_nowait(data)
                    except asyncio.QueueFull:
                        pass

                    # Also broadcast to WS clients
                    await self._broadcast(sid, {
                        "type": "response",
                        "data": data,
                    })

                else:
                    # Try to parse as NDJSON event
                    try:
                        event = json.loads(text)
                    except json.JSONDecodeError:
                        continue  # Drop non-JSON lines

                    # Persist to events.ndjson
                    ef = self._event_files.get(sid)
                    if ef:
                        try:
                            ef.write(text + "\n")
                            ef.flush()
                        except OSError:
                            pass

                    # Broadcast event to WS clients
                    await self._broadcast(sid, {
                        "type": "event",
                        "data": event,
                    })

        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Stdout reader for session %s crashed", sid)
        finally:
            # Process ended or reader crashed — close session
            if session.status != "closed":
                reason = "process_exited"
                if proc.returncode and proc.returncode != 0:
                    reason = "crashed"
                await self.close_session(sid, reason=reason)

    # ------------------------------------------------------------------
    # Cleanup loop
    # ------------------------------------------------------------------

    async def _cleanup_loop(self) -> None:
        """Periodically kill sessions that are idle with no WS clients."""
        try:
            while True:
                await asyncio.sleep(60)
                now = time.time()
                for sid, session in list(self._sessions.items()):
                    if session.status == "closed":
                        continue
                    clients = self._ws_clients.get(sid, [])
                    idle = now - session.last_activity
                    if not clients and idle > self.idle_timeout_s:
                        logger.info(
                            "Closing idle session %s (idle %.0fs)", sid, idle
                        )
                        await self.close_session(sid, reason="idle")
        except asyncio.CancelledError:
            return

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _session_dir(self, sid: str) -> Path:
        return self.data_dir / "interactive" / sid
