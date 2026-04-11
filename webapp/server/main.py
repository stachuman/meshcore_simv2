from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server.config import Settings
from server.routers import configs, simulations, topology, topologies, sweeps
from server.services.sim_manager import SimManager
from server.services.event_index import EventIndexCache


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.get()
    for subdir in ("simulations", "configs", "topologies", "sweeps"):
        (settings.DATA_DIR / subdir).mkdir(parents=True, exist_ok=True)

    # Initialize shared services
    app.state.sim_manager = SimManager(
        data_dir=settings.DATA_DIR,
        orchestrator_path=str(settings.ORCHESTRATOR_PATH),
        max_concurrent=settings.MAX_CONCURRENT_SIMS,
    )
    app.state.event_cache = EventIndexCache(max_size=5)

    yield


app = FastAPI(title="MeshCore Simulator", lifespan=lifespan)

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

static_dir = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
async def root():
    index = static_dir / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"message": "MeshCore Simulator API", "docs": "/docs"}
