from fastapi import FastAPI
from contextlib import asynccontextmanager
from observability.logging import setup_logging
from observability.middleware import CorrelationIdMiddleware

@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    yield

app = FastAPI(title="Orchestrator", lifespan=lifespan)
app.add_middleware(CorrelationIdMiddleware)

@app.get("/health")
def health_check():
    return {"status": "ok", "service": "orchestrator"}
