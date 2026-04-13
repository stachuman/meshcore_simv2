"""TopoGenerator service -- runs ITM-based topology generation from MeshCore map API.

Wraps the topology_generator module for async web usage:
- Uses ProcessPoolExecutor for parallel ITM propagation computation
- Publishes progress via asyncio.Queue (SSE pattern, same as SweepRunner)
- Persists results to data/generators/{id}/ on disk
- Max 1 active generation at a time
"""

import asyncio
import json
import logging
import math
import shutil
import sys
import time
import uuid
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

# Add project root so topology_generator is importable
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from topology_generator.api_fetch import (
    fetch_nodes,
    filter_nodes,
    sanitize_name,
)
from topology_generator.terrain import (
    get_elevation_data,
    haversine_km,
    sample_elevation_profile,
)
from topology_generator.__main__ import (
    _compute_link_worker,
    select_links,
    check_connectivity,
    survival_filter,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Params model
# ---------------------------------------------------------------------------

class GeneratorParams(BaseModel):
    # Region
    bbox: list[float] = Field(..., min_length=4, max_length=4)

    # RF
    freq_mhz: float = 869.618
    tx_power_dbm: float = 22.0
    antenna_height: float = 5.0
    noise_figure_db: float = 6.0
    sf: int = 8
    bw: int = 62500
    cr: int = 4

    # ITM
    climate: int = 6
    polarization: int = 1
    clutter_db: float = 6.0

    # Link filtering
    max_distance_km: float = 30.0
    min_snr: float = -10.0
    max_edges_per_node: int = 8
    max_good_links: int = 3
    link_survival: float = 1.0
    survival_seed: int = 42
    survival_snr_mid: float = 10.0

    # Node types (1=companion, 2=repeater, 3=gateway)
    node_types: list[int] = [2, 3]

    # Post-processing
    connect_islands: bool = False

    # Execution
    workers: int = 4


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class GenerationRecord:
    id: str
    status: str  # pending, running, completed, failed, cancelled
    params: dict
    created_at: float
    completed_at: Optional[float] = None
    error: Optional[str] = None
    result: Optional[dict] = None  # {nodes, links, radio, stats}
    progress_pct: int = 0
    progress_step: str = ""


# ---------------------------------------------------------------------------
# Island bridging
# ---------------------------------------------------------------------------

def _bridge_islands(
    links: list[dict],
    nodes: list[dict],
    raw_links: list[dict],
    components: list[set],
) -> list[dict]:
    """Connect disconnected components with best available links from raw_links.

    MST-like approach: repeatedly find the best (highest SNR) link between
    the two closest components and add it, then re-merge until connected.
    Prefers links from the pre-filter raw_links pool so we get real ITM-computed
    quality rather than artificial estimates.
    """
    # Build coord lookup for distance-based fallback
    name_coords = {}
    for nd in nodes:
        lat, lon = nd.get("lat", 0.0), nd.get("lon", 0.0)
        if lat != 0.0 or lon != 0.0:
            name_coords[nd["name"]] = (lat, lon)

    # Index raw_links by node-pair for fast lookup
    raw_by_pair: dict[tuple[str, str], dict] = {}
    for link in raw_links:
        key = (link["from"], link["to"])
        rkey = (link["to"], link["from"])
        if key not in raw_by_pair:
            raw_by_pair[key] = link
        if rkey not in raw_by_pair:
            raw_by_pair[rkey] = link

    # Already-selected link set (avoid duplicates)
    existing = set()
    for link in links:
        existing.add((link["from"], link["to"]))
        existing.add((link["to"], link["from"]))

    result = list(links)
    comps = [set(c) for c in components]

    while len(comps) > 1:
        best_link = None
        best_snr = -999.0
        best_ci = -1
        best_cj = -1

        # Try to find best raw link between any two components
        for i in range(len(comps)):
            for j in range(i + 1, len(comps)):
                for na in comps[i]:
                    for nb in comps[j]:
                        link = raw_by_pair.get((na, nb))
                        if link and link["snr"] > best_snr:
                            best_snr = link["snr"]
                            best_link = link
                            best_ci, best_cj = i, j

        if best_link is None:
            # No ITM link exists — create synthetic bridge using closest nodes
            best_dist = float("inf")
            best_na = best_nb = None
            for i in range(len(comps)):
                ci_nodes = [n for n in comps[i] if n in name_coords]
                for j in range(i + 1, len(comps)):
                    cj_nodes = [n for n in comps[j] if n in name_coords]
                    for na in ci_nodes:
                        la, lo = name_coords[na]
                        for nb in cj_nodes:
                            lb, lob = name_coords[nb]
                            d = haversine_km(la, lo, lb, lob)
                            if d < best_dist:
                                best_dist = d
                                best_na, best_nb = na, nb
                                best_ci, best_cj = i, j

            if best_na is None:
                break  # cannot bridge (no coordinates)

            # Synthetic link with estimated SNR (log-distance model)
            snr_est = max(-15.0, 20.0 - 30.0 * math.log10(max(best_dist, 0.1)))
            best_link = {
                "from": best_na,
                "to": best_nb,
                "snr": round(snr_est, 1),
                "bidir": True,
            }

        key = (best_link["from"], best_link["to"])
        if key not in existing:
            result.append(best_link)
            existing.add(key)
            existing.add((best_link["to"], best_link["from"]))

        # Merge the two components
        merged = comps[best_ci] | comps[best_cj]
        comps = [c for k, c in enumerate(comps) if k != best_ci and k != best_cj]
        comps.insert(0, merged)

    return result


# ---------------------------------------------------------------------------
# Module-level worker bridge for ProcessPoolExecutor
# ---------------------------------------------------------------------------

def _submit_and_get(executor: ProcessPoolExecutor, work_item: tuple) -> dict:
    """Submit a single _compute_link_worker call and return its result."""
    future = executor.submit(_compute_link_worker, work_item)
    return future.result()


# ---------------------------------------------------------------------------
# TopoGenerator
# ---------------------------------------------------------------------------

class TopoGenerator:
    """Manages topology generation lifecycle, process pool, and progress."""

    def __init__(self, data_dir: Path, max_workers: int = 4):
        self.data_dir = data_dir
        self.max_workers = max_workers
        self._generations: dict[str, GenerationRecord] = {}
        self._progress_subscribers: dict[str, list[asyncio.Queue]] = {}
        self._cancel_flags: dict[str, bool] = {}
        self._active_generation: Optional[str] = None
        self._scan_existing()

    # ------------------------------------------------------------------
    # Startup scan
    # ------------------------------------------------------------------

    def _scan_existing(self) -> None:
        gen_dir = self.data_dir / "generators"
        if not gen_dir.exists():
            return

        for entry in sorted(gen_dir.iterdir()):
            if not entry.is_dir():
                continue

            gen_id = entry.name
            status_path = entry / "status.json"
            if not status_path.exists():
                continue

            try:
                status_data = json.loads(status_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            raw_status = status_data.get("status", "failed")
            if raw_status in ("running", "pending"):
                raw_status = "failed"
                status_data["error"] = status_data.get(
                    "error", "Server restarted while generation was in progress"
                )

            result = None
            result_path = entry / "result.json"
            if result_path.exists():
                try:
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    pass

            self._generations[gen_id] = GenerationRecord(
                id=gen_id,
                status=raw_status,
                params=status_data.get("params", {}),
                created_at=status_data.get("created_at", 0),
                completed_at=status_data.get("completed_at"),
                error=status_data.get("error"),
                result=result,
                progress_pct=100 if raw_status == "completed" else 0,
                progress_step=status_data.get("progress_step", ""),
            )

        logger.info(
            "Scanned %d existing generation(s) from %s",
            len(self._generations), gen_dir,
        )

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _gen_dir(self, gen_id: str) -> Path:
        return self.data_dir / "generators" / gen_id

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_generation(self, params: GeneratorParams) -> str:
        if self._active_generation is not None:
            rec = self._generations.get(self._active_generation)
            if rec and rec.status == "running":
                raise ValueError("A generation is already running")

        gen_id = uuid.uuid4().hex[:12]
        gen_dir = self._gen_dir(gen_id)
        gen_dir.mkdir(parents=True, exist_ok=True)

        record = GenerationRecord(
            id=gen_id,
            status="pending",
            params=params.model_dump(),
            created_at=time.time(),
        )
        self._generations[gen_id] = record
        self._cancel_flags[gen_id] = False
        self._active_generation = gen_id

        self._save_status(gen_id)

        asyncio.create_task(self._run_generation(gen_id, params))
        return gen_id

    def get_generation(self, gen_id: str) -> Optional[GenerationRecord]:
        return self._generations.get(gen_id)

    def list_generations(self) -> list[GenerationRecord]:
        return sorted(
            self._generations.values(),
            key=lambda g: g.created_at,
            reverse=True,
        )

    async def delete_generation(self, gen_id: str) -> bool:
        if gen_id not in self._generations:
            return False

        self._cancel_flags[gen_id] = True

        record = self._generations.get(gen_id)
        if record and record.status == "running":
            record.status = "cancelled"
            record.completed_at = time.time()
            self._save_status(gen_id)
            self._notify_progress(gen_id, {"status": "cancelled"})
            self._notify_progress(gen_id, None)

        gen_dir = self._gen_dir(gen_id)
        if gen_dir.exists():
            shutil.rmtree(gen_dir)

        if self._active_generation == gen_id:
            self._active_generation = None

        self._generations.pop(gen_id, None)
        self._cancel_flags.pop(gen_id, None)
        self._progress_subscribers.pop(gen_id, None)
        return True

    # ------------------------------------------------------------------
    # SSE progress subscription
    # ------------------------------------------------------------------

    def subscribe_progress(self, gen_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._progress_subscribers.setdefault(gen_id, []).append(q)

        record = self._generations.get(gen_id)
        if record and record.status in ("completed", "failed", "cancelled"):
            try:
                q.put_nowait({
                    "status": record.status,
                    "progress_pct": record.progress_pct,
                    "progress_step": record.progress_step,
                })
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass
        return q

    def unsubscribe_progress(self, gen_id: str, q: asyncio.Queue) -> None:
        queues = self._progress_subscribers.get(gen_id, [])
        try:
            queues.remove(q)
        except ValueError:
            pass
        if not queues:
            self._progress_subscribers.pop(gen_id, None)

    # ------------------------------------------------------------------
    # Core generation pipeline
    # ------------------------------------------------------------------

    async def _run_generation(self, gen_id: str, params: GeneratorParams) -> None:
        record = self._generations[gen_id]
        record.status = "running"
        self._save_status(gen_id)

        loop = asyncio.get_running_loop()

        def _cancelled():
            return self._cancel_flags.get(gen_id, False)

        def _progress(pct: int, step: str, detail: str = ""):
            record.progress_pct = pct
            record.progress_step = step
            self._notify_progress(gen_id, {
                "status": "running",
                "progress_pct": pct,
                "progress_step": step,
                "detail": detail,
            })

        try:
            # --- Step 1: Fetch API nodes (0-5%) ---
            _progress(0, "Fetching nodes from API...")
            cache_dir = self.data_dir / "generators"
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path = str(cache_dir / "api_cache.json")

            raw_nodes = await loop.run_in_executor(
                None, lambda: fetch_nodes(cache_path=cache_path)
            )
            _progress(5, f"Fetched {len(raw_nodes)} nodes from API")

            if _cancelled():
                self._finish_cancelled(gen_id, record)
                return

            # --- Step 2: Filter by region + type (5-10%) ---
            bbox = tuple(params.bbox)
            node_types = set(params.node_types)
            nodes = filter_nodes(raw_nodes, bbox=bbox, node_types=node_types)
            _progress(10, f"Filtering: {len(nodes)} nodes in region")

            if not nodes:
                raise ValueError(
                    f"No nodes found in region [{bbox[0]:.2f},{bbox[1]:.2f},"
                    f"{bbox[2]:.2f},{bbox[3]:.2f}]. "
                    "Try a larger bounding box."
                )

            if _cancelled():
                self._finish_cancelled(gen_id, record)
                return

            # --- Step 3: Sanitize names (10-12%) ---
            seen_names = {}
            for node in nodes:
                node["_name"] = sanitize_name(
                    node.get("adv_name", "node"), seen_names
                )

            nodes_out = []
            name_set = set()
            for node in nodes:
                ntype = node.get("type", 2)
                if ntype == 1:
                    role = "companion"
                elif ntype == 3:
                    role = "repeater"  # gateways act as repeaters in sim
                else:
                    role = "repeater"
                nd = {
                    "name": node["_name"],
                    "role": role,
                    "lat": node["adv_lat"],
                    "lon": node["adv_lon"],
                }
                nodes_out.append(nd)
                name_set.add(node["_name"])

            _progress(12, f"Prepared {len(nodes_out)} nodes")

            # --- Step 4: Generate candidate pairs (12-15%) ---
            pairs = []
            for i in range(len(nodes)):
                for j in range(i + 1, len(nodes)):
                    a, b = nodes[i], nodes[j]
                    dist = haversine_km(
                        a["adv_lat"], a["adv_lon"],
                        b["adv_lat"], b["adv_lon"],
                    )
                    if 0.01 < dist <= params.max_distance_km:
                        pairs.append((i, j, dist))

            _progress(15, f"Generated {len(pairs)} candidate pairs")

            if _cancelled():
                self._finish_cancelled(gen_id, record)
                return

            # --- Step 5: Sample terrain profiles (15-30%) ---
            _progress(15, "Downloading terrain data (SRTM)...")
            elev_data = await loop.run_in_executor(None, get_elevation_data)

            def _sample_all_profiles():
                profs = {}
                total = len(pairs)
                for idx, (i, j, dist) in enumerate(pairs):
                    a, b = nodes[i], nodes[j]
                    prof = sample_elevation_profile(
                        elev_data, a["adv_lat"], a["adv_lon"],
                        b["adv_lat"], b["adv_lon"], 150,
                    )
                    profs[(i, j)] = prof
                return profs

            profiles = await loop.run_in_executor(None, _sample_all_profiles)

            _progress(30, f"Sampled {len(profiles)} terrain profiles")

            if _cancelled():
                self._finish_cancelled(gen_id, record)
                return

            # --- Step 6: ITM propagation (30-85%) ---
            antenna_heights = (params.antenna_height, params.antenna_height)
            raw_links = []

            work_items = []
            pair_indices = []
            for (i, j, dist) in pairs:
                work_items.append((
                    profiles[(i, j)], dist, params.freq_mhz, antenna_heights,
                    params.tx_power_dbm, float(params.bw), params.noise_figure_db,
                    params.min_snr, params.climate, params.polarization,
                    15.0, 0.005, 314.0, params.clutter_db,
                ))
                pair_indices.append((i, j))

            if work_items:
                workers = min(params.workers, self.max_workers)
                total_links = len(work_items)

                _progress(30, f"Computing ITM propagation (0/{total_links})...")

                executor = ProcessPoolExecutor(max_workers=workers)
                try:
                    # Use gather (preserves submission order) so pair_indices[idx]
                    # correctly maps each result to its node pair.
                    pending_tasks = [
                        loop.run_in_executor(None, _submit_and_get, executor, w)
                        for w in work_items
                    ]
                    results = await asyncio.gather(*pending_tasks)

                    for idx, result in enumerate(results):
                        if result is not None:
                            i, j = pair_indices[idx]
                            link = {
                                "from": nodes[i]["_name"],
                                "to": nodes[j]["_name"],
                                **result,
                                "bidir": True,
                            }
                            raw_links.append(link)
                finally:
                    executor.shutdown(wait=False, cancel_futures=True)

            _progress(85, f"{len(raw_links)} viable links (SNR >= {params.min_snr} dB)")

            if _cancelled():
                self._finish_cancelled(gen_id, record)
                return

            # --- Step 7: Link survival filter (85-87%) ---
            if params.link_survival < 1.0:
                _progress(85, "Applying link survival filter...")
                raw_links = survival_filter(
                    raw_links, params.link_survival,
                    seed=params.survival_seed,
                    snr_mid=params.survival_snr_mid,
                )
            _progress(87, f"{len(raw_links)} links after survival filter")

            # --- Step 8: Edge caps (87-90%) ---
            _progress(87, "Applying edge caps...")
            links_out = select_links(
                raw_links, params.max_edges_per_node, params.max_good_links,
                seed=params.survival_seed,
            )
            _progress(90, f"{len(links_out)} links after edge caps")

            # --- Step 9: Prune isolated nodes (90-92%) ---
            connected_names = set()
            for link in links_out:
                if link["snr"] > 0.0:
                    connected_names.add(link["from"])
                    connected_names.add(link["to"])
            pruned = [
                n for n in nodes_out
                if n["name"] not in name_set or n["name"] in connected_names
            ]
            n_pruned = len(nodes_out) - len(pruned)
            if n_pruned > 0:
                nodes_out = pruned
                name_set = {n["name"] for n in nodes_out}
                links_out = [
                    l for l in links_out
                    if l["from"] in name_set and l["to"] in name_set
                ]
            _progress(92, f"{len(nodes_out)} nodes, {len(links_out)} links after pruning")

            # --- Step 10: Connectivity check + island bridging (92-95%) ---
            components = check_connectivity(links_out, name_set)

            if params.connect_islands and len(components) > 1:
                _progress(93, f"Bridging {len(components)} islands...")
                links_out = _bridge_islands(
                    links_out, nodes_out, raw_links, components,
                )
                components = check_connectivity(
                    links_out, {n["name"] for n in nodes_out},
                )

            _progress(95, f"{len(components)} component(s)")

            # --- Step 11: Assemble result (95-100%) ---
            # Compute stats
            snrs = [l["snr"] for l in links_out] if links_out else []
            dists = [l.get("dist_km", 0) for l in links_out] if links_out else []

            stats = {
                "node_count": len(nodes_out),
                "link_count": len(links_out),
                "components": len(components),
                "pruned_nodes": n_pruned,
            }
            if snrs:
                sorted_snrs = sorted(snrs)
                stats["snr_min"] = round(min(snrs), 1)
                stats["snr_max"] = round(max(snrs), 1)
                stats["snr_median"] = round(sorted_snrs[len(sorted_snrs) // 2], 1)
            if dists:
                sorted_dists = sorted(dists)
                stats["dist_min_km"] = round(min(dists), 1)
                stats["dist_max_km"] = round(max(dists), 1)
                stats["dist_median_km"] = round(sorted_dists[len(sorted_dists) // 2], 1)

            # Strip dist_km from output links
            links_clean = []
            for link in links_out:
                lc = dict(link)
                lc.pop("dist_km", None)
                links_clean.append(lc)

            result_data = {
                "nodes": nodes_out,
                "links": links_clean,
                "radio": {"sf": params.sf, "bw": params.bw, "cr": params.cr},
                "stats": stats,
            }

            record.result = result_data
            record.status = "completed"
            record.completed_at = time.time()
            record.progress_pct = 100
            record.progress_step = (
                f"Complete! {len(nodes_out)} nodes, {len(links_clean)} links"
            )

            self._save_result(gen_id)
            self._save_status(gen_id)

            self._notify_progress(gen_id, {
                "status": "completed",
                "progress_pct": 100,
                "progress_step": f"Complete! {len(nodes_out)} nodes, {len(links_clean)} links",
            })

        except Exception as e:
            logger.exception("Generation %s failed", gen_id)
            record.status = "failed"
            record.error = str(e)
            record.completed_at = time.time()
            self._save_status(gen_id)
            self._notify_progress(gen_id, {
                "status": "failed",
                "error": str(e),
            })
        finally:
            if self._active_generation == gen_id:
                self._active_generation = None
            self._cancel_flags.pop(gen_id, None)
            self._notify_progress(gen_id, None)

    def _finish_cancelled(self, gen_id: str, record: GenerationRecord) -> None:
        if record.status == "cancelled":
            return  # already handled by delete_generation
        record.status = "cancelled"
        record.completed_at = time.time()
        # Guard: delete_generation may have removed dir already
        if self._gen_dir(gen_id).exists():
            self._save_status(gen_id)
        self._notify_progress(gen_id, {"status": "cancelled"})

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_status(self, gen_id: str) -> None:
        record = self._generations.get(gen_id)
        if not record:
            return
        status_path = self._gen_dir(gen_id) / "status.json"
        status_data = {
            "status": record.status,
            "created_at": record.created_at,
            "completed_at": record.completed_at,
            "error": record.error,
            "params": record.params,
            "progress_step": record.progress_step,
        }
        try:
            status_path.write_text(
                json.dumps(status_data, indent=2), encoding="utf-8"
            )
        except OSError:
            logger.exception("Failed to save status for generation %s", gen_id)

    def _save_result(self, gen_id: str) -> None:
        record = self._generations.get(gen_id)
        if not record or not record.result:
            return
        result_path = self._gen_dir(gen_id) / "result.json"
        try:
            result_path.write_text(
                json.dumps(record.result, indent=2), encoding="utf-8"
            )
        except OSError:
            logger.exception("Failed to save result for generation %s", gen_id)

    # ------------------------------------------------------------------
    # SSE notification
    # ------------------------------------------------------------------

    def _notify_progress(self, gen_id: str, data) -> None:
        queues = self._progress_subscribers.get(gen_id, [])
        for q in queues:
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                pass
