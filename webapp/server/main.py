from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server.config import Settings
from server.routers import configs, simulations, topology, topologies, sweeps, interactive, topo_creator
from server.services.sim_manager import SimManager
from server.services.event_index import EventIndexCache
from server.services.interactive_manager import InteractiveSessionManager


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.get()
    for subdir in ("simulations", "configs", "topologies", "sweeps", "interactive", "generators"):
        (settings.DATA_DIR / subdir).mkdir(parents=True, exist_ok=True)

    # Initialize shared services
    app.state.sim_manager = SimManager(
        data_dir=settings.DATA_DIR,
        orchestrator_path=str(settings.ORCHESTRATOR_PATH),
        max_concurrent=settings.MAX_CONCURRENT_SIMS,
    )
    app.state.event_cache = EventIndexCache(max_size=5)

    app.state.interactive_manager = InteractiveSessionManager(
        data_dir=settings.DATA_DIR,
        orchestrator_path=str(settings.ORCHESTRATOR_PATH),
        max_sessions=settings.MAX_INTERACTIVE_SESSIONS,
        idle_timeout_s=settings.INTERACTIVE_IDLE_TIMEOUT_S,
    )
    app.state.interactive_manager.start_cleanup_loop()

    yield

    # Shutdown: close all interactive sessions
    await app.state.interactive_manager.shutdown()


app = FastAPI(title="MeshCore Simulator", lifespan=lifespan)

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(configs.router, prefix="/api")
app.include_router(simulations.router, prefix="/api")
app.include_router(topology.router, prefix="/api")
app.include_router(topologies.router, prefix="/api")
app.include_router(sweeps.router, prefix="/api")
app.include_router(interactive.router, prefix="/api")
app.include_router(topo_creator.router, prefix="/api")

static_dir = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
async def root():
    index = static_dir / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"message": "MeshCore Simulator API", "docs": "/docs"}
