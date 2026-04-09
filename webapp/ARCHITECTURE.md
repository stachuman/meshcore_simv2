# MeshCore Simulator Web App -- Architecture

## Quick Start

```bash
# Local development
cd webapp
pip install -r requirements.txt
uvicorn server.main:app --reload --port 8000
# Open http://localhost:8000

# Docker
cd webapp
docker compose up --build
# Open http://localhost:8000
```

Environment variables (all optional):

| Variable | Default | Description |
|----------|---------|-------------|
| `DATA_DIR` | `./data` | Root for all persistent data |
| `ORCHESTRATOR_PATH` | `../build/orchestrator/orchestrator` | Path to compiled binary |
| `MAX_CONCURRENT_SIMS` | CPU count | Semaphore limit for parallel sims |

## High-Level Architecture

```
Browser (vanilla JS, no build step)
  |
  |  HTTP / SSE
  v
FastAPI Backend (Python, single process)
  |
  |  asyncio subprocess
  v
orchestrator binary (C++)
  |
  +---> stdout: events.ndjson
  +---> stderr: progress / summary
```

No database. All state is filesystem:

```
data/
  simulations/{id}/
    config.json          # Input config
    events.ndjson        # Orchestrator stdout (NDJSON events)
    status.json          # {status, created_at, completed_at, error, pid}
  configs/{id}.json      # Saved configs with metadata wrapper
  sweeps/{id}/
    status.json          # Sweep state
    results.json         # Aggregated + raw results
```

## Backend Structure

```
webapp/server/
  main.py              # FastAPI app, lifespan, static mount, router registration
  config.py            # Settings singleton (env vars)
  routers/
    configs.py         # /api/configs/*     Config CRUD + validation
    simulations.py     # /api/sims/*        Sim lifecycle + event queries + SSE
    topology.py        # /api/topo/*        Convert/generate topologies
    sweeps.py          # /api/sweeps/*      Parameter sweep lifecycle + SSE
  services/
    sim_manager.py     # Subprocess lifecycle, progress parsing, SSE queues
    event_index.py     # NDJSON indexing (extracted from visualize.py)
    config_validator.py # Structural + semantic config validation
    sweep_runner.py    # Parallel orchestrator runs via ProcessPoolExecutor
    topo_tools.py      # Wraps tools/convert_topology.py + gen_grid_test.py
```

### Shared State (app.state)

Initialized in `main.py` lifespan:

- **`sim_manager`** (`SimManager`) -- subprocess lifecycle, progress queues
- **`event_cache`** (`EventIndexCache`) -- LRU cache (max 3) of parsed EventIndex

`SweepRunner` is lazily initialized on first `/api/sweeps` request.

### Key Services

**SimManager** (`sim_manager.py`, 482 lines)
- `create_sim(config)` -> spawns `asyncio.create_subprocess_exec(orchestrator, ...)`
- stdout piped to `events.ndjson`, stderr parsed for progress (timestamps -> %)
- Semaphore limits concurrent sims to `MAX_CONCURRENT_SIMS`
- Progress pushed via `asyncio.Queue` to SSE subscribers
- On startup: scans `data/simulations/`, marks stale running sims as failed

**EventIndex** (`event_index.py`, 507 lines)
- Extracted verbatim from `visualization/visualize.py`
- Loads NDJSON into sorted arrays, indexes by time, node, packet hash
- `query_time_range()`, `query_pkt()`, `deep_trace()`, `density()`, etc.
- `EventIndexCache`: OrderedDict-based LRU, thread-safe, max 3 sims in memory

**ConfigValidator** (`config_validator.py`, 632 lines)
- Validates nodes, topology, simulation params, commands, schedules
- Returns `ValidationResult(valid, errors, warnings)` -- collects all issues
- Mirrors C++ `validateConfig()` checks + extra structural validation

**SweepRunner** (`sweep_runner.py`, 619 lines)
- Generates parameter grid from `{min, max, step}` ranges
- Runs all combos x seeds via `ProcessPoolExecutor`
- Parses delivery/ack/channel stats from orchestrator stderr
- Aggregates by (rxdelay, txdelay, direct_txdelay) -> mean/std/min/max

**TopoTools** (`topo_tools.py`, 395 lines)
- `convert_topology()` -- wraps `tools/convert_topology.py` pipeline
- `generate_grid()` -- wraps `tools/gen_grid_test.py` + adds lat/lon coords
- Imports via sys.path to reuse existing tool code without duplication

## API Endpoints

### Configs (`/api/configs`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/configs/` | Create (upload) config |
| GET | `/api/configs/` | List saved configs (summaries) |
| GET | `/api/configs/{id}` | Get full config |
| PUT | `/api/configs/{id}` | Update config |
| DELETE | `/api/configs/{id}` | Delete config |
| POST | `/api/configs/validate` | Validate without saving |

### Simulations (`/api/sims`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/sims` | Start sim (body: `{config_json}`) |
| GET | `/api/sims` | List all sims |
| GET | `/api/sims/{id}` | Status + progress + config summary |
| DELETE | `/api/sims/{id}` | Cancel/delete sim + data |
| GET | `/api/sims/{id}/meta` | Nodes, time range, event count |
| GET | `/api/sims/{id}/events?from=&to=` | Time-windowed events |
| GET | `/api/sims/{id}/density?from=&to=&bucket=` | Per-node density heatmap |
| GET | `/api/sims/{id}/trace/{pkt}` | Events for a packet fingerprint |
| GET | `/api/sims/{id}/deep_trace/{pkt}` | Follow relay chain across hashes |
| GET | `/api/sims/{id}/topology` | Nodes + links from config |
| GET | `/api/sims/{id}/stats` | Per-node/link statistics |
| GET | `/api/sims/{id}/messages` | Extracted message list |
| GET | `/api/sims/{id}/msg_tx/{node}?after=` | First TX from node after time |
| GET | `/api/sims/{id}/node_events/{node}?from=&to=&limit=` | Per-node events |
| GET | `/api/sims/{id}/progress` | SSE progress stream |

### Topology (`/api/topo`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/topo/convert` | Convert topology.json -> config |
| POST | `/api/topo/generate` | Generate grid topology |

### Sweeps (`/api/sweeps`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/sweeps` | Start sweep |
| GET | `/api/sweeps` | List sweeps |
| GET | `/api/sweeps/{id}` | Status + results |
| DELETE | `/api/sweeps/{id}` | Cancel/delete |
| GET | `/api/sweeps/{id}/results` | Full results (aggregated + raw) |
| GET | `/api/sweeps/{id}/progress` | SSE progress stream |

## Frontend Structure

```
webapp/static/
  css/common.css       # Dark theme, components, utilities (432 lines)
  js/api.js            # fetchJSON, postJSON, deleteJSON, connectSSE, helpers
  index.html           # Dashboard: stats grid + recent sims table
  editor.html          # CodeMirror 5 JSON editor + validate/save/run
  simulations.html     # Full sim list with expandable details + delete
  configs.html         # Config upload/manage/run/download
  sweep.html           # Sweep config + progress + sortable results table
  visualize.html       # Swim-lane timeline (adapted from visualization/)
  map_view.html        # Geographic topology map (adapted from visualization/)
```

All pages use the same nav bar, dark theme, and `api.js` helpers. No build step -- CodeMirror 5 loaded from CDN.

### Page Data Flow

```
editor.html
  POST /api/configs/validate   (validate button)
  POST /api/configs/           (save button)
  POST /api/sims               (run button)
  SSE  /api/sims/{id}/progress (progress tracking)

simulations.html
  GET  /api/sims               (list, auto-refresh if running)
  DELETE /api/sims/{id}        (delete)

visualize.html?sim={id}
  GET  /api/sims/{id}/meta     (on load)
  GET  /api/sims/{id}/density  (heatmap)
  GET  /api/sims/{id}/events   (viewport events)
  GET  /api/sims/{id}/deep_trace/{pkt}  (click packet)

map_view.html?sim={id}
  GET  /api/sims/{id}/topology (on load)
  GET  /api/sims/{id}/stats    (on load)
  GET  /api/sims/{id}/messages (on load)
  GET  /api/sims/{id}/deep_trace/{pkt}  (click message)
  GET  /api/sims/{id}/node_events/{node} (click node)
```

## Design Decisions

**Why no database?**
Simulation artifacts are inherently files (config JSON, NDJSON event streams). Adding SQLite/Postgres would mean syncing two sources of truth. Filesystem is simple and sufficient for single-user/small-team use.

**Why vanilla JS (no React/Vue)?**
The visualization pages (`visualize.html`, `map_view.html`) are 1700+ lines of working canvas/Leaflet code. Adding a framework would require rewriting them. The simpler pages (dashboard, editor, configs) don't need component systems.

**Why SSE instead of WebSocket?**
Progress is unidirectional (server -> client). SSE auto-reconnects, works through proxies, and needs zero client-side libraries.

**Why extract EventIndex vs import visualize.py?**
`visualize.py` is a standalone script with its own HTTP server. Extracting the EventIndex class and helper functions avoids importing that server machinery and makes the dependency explicit.

**Why LRU cache for EventIndex?**
A 100K-event simulation's EventIndex uses ~50MB RAM. Capping at 3 loaded sims keeps memory bounded. The cache is transparent -- a cache miss just takes a few seconds to re-parse the NDJSON file.

**Why lazy SweepRunner?**
Not every deployment needs sweeps. Lazy init avoids creating the ProcessPoolExecutor until someone actually starts a sweep.

## URL Conventions

- **Configs router** uses trailing-slash paths (`/api/configs/`) -- frontend matches
- **Sims and Sweeps routers** use no-trailing-slash (`/api/sims`, `/api/sweeps`) -- frontend matches
- Both work; just maintain consistency within each router

## Docker Build

Multi-stage: `ubuntu:22.04` builds the C++ orchestrator, `python:3.11-slim` runs the webapp. Named volume `sim-data` persists simulation data across container restarts.

```
docker compose up --build          # first time
docker compose up                  # subsequent
docker compose down -v             # wipe data
```
