from fastapi import FastAPI
from contextlib import asynccontextmanager

from app.core.logging import setup_logging
from app.core.config import get_settings
from app.database.session import get_engine
from app.database import models

from app.api import extract, requests, usage, whatsapp


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic."""
    setup_logging()
    # Create all tables if they don't exist (dev convenience).
    # In production, use Alembic migrations instead.
    models.Base.metadata.create_all(bind=get_engine())
    yield


settings = get_settings()

app = FastAPI(
    title="Intelligent Document Extraction API",
    description=(
        "SaaS platform for intelligent document and image processing powered by AI. "
        "Submit an image with an extraction prompt and receive structured JSON data."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)


# Routers
app.include_router(extract.router)
app.include_router(requests.router)
app.include_router(usage.router)
app.include_router(whatsapp.router)



@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "ok", "version": app.version}
