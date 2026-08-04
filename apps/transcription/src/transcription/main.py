from fastapi import FastAPI
from contextlib import asynccontextmanager

from observability.logging import setup_logging
from transcription.core.config import get_settings
from observability.middleware import CorrelationIdMiddleware
from transcription.core.logging import get_logger
from transcription.services.prompt_service import PromptConfigurationError, PromptService

from transcription.api import extract, internal_extract, requests, usage, whatsapp

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic."""
    setup_logging()
    try:
        PromptService.load_prompt()
    except PromptConfigurationError as exc:
        logger.critical(
            "startup validation failed | error_code=SYSTEM_PROMPT_INVALID | prompt_source=%s | reason=%s",
            PromptService.source_classification(),
            exc.reason,
        )
        raise RuntimeError("SYSTEM_PROMPT_INVALID") from None
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
app.add_middleware(CorrelationIdMiddleware)


# Routers
app.include_router(extract.router)
app.include_router(internal_extract.router)
app.include_router(requests.router)
app.include_router(usage.router)
app.include_router(whatsapp.router)


@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "ok", "version": app.version}
