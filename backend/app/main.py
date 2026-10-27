"""FastAPI entrypoint: CORS, routers, health."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Mohalla Mind API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    """Liveness check with the current simulated date."""
    from app.db import SessionLocal
    from app.models import SimState

    sim_date = settings.sim_start_date
    with SessionLocal() as db:
        state = db.get(SimState, 1)
        if state:
            sim_date = state.current_date
    return {"status": "ok", "sim_date": sim_date.isoformat()}
