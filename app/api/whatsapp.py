"""
WhatsApp webhook endpoint.

Extremely thin: receives the payload, delegates entirely to WhatsAppService,
returns HTTP 200. No extraction logic, no WUZAPI calls, no formatting here.
"""

import uuid
from functools import lru_cache

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.ai.gemini_provider import GeminiProvider
from app.services.ai.provider import AIProvider
from app.services.response_formatter import ResponseFormatter
from app.services.whatsapp_service import WhatsAppService
from app.integrations.wuzapi import WuzapiClient

logger = get_logger(__name__)

router = APIRouter(prefix="/whatsapp", tags=["WhatsApp"])


# ---------------------------------------------------------------------------
# Cached singletons — created once, reused across requests
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _get_ai_provider() -> AIProvider:
    """Lazily creates and caches the AI provider (after .env is loaded)."""
    return GeminiProvider()


@lru_cache(maxsize=1)
def _get_wuzapi_client() -> WuzapiClient:
    """Lazily creates and caches the WUZAPI client."""
    return WuzapiClient(settings=get_settings())


@lru_cache(maxsize=1)
def _get_formatter() -> ResponseFormatter:
    """Lazily creates and caches the response formatter."""
    return ResponseFormatter()


def _get_whatsapp_application_id() -> uuid.UUID:
    """Return the Application UUID used to attribute WhatsApp requests."""
    settings = get_settings()
    raw = settings.wuzapi_application_id
    if not raw:
        raise RuntimeError(
            "WUZAPI_APPLICATION_ID is not set. "
            "Please add it to your .env file."
        )
    return uuid.UUID(raw)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def _build_whatsapp_service(db: Session) -> WhatsAppService:
    return WhatsAppService(
        db=db,
        ai_provider=_get_ai_provider(),
        wuzapi=_get_wuzapi_client(),
        formatter=_get_formatter(),
        application_id=_get_whatsapp_application_id(),
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/webhook", status_code=200, summary="WUZAPI webhook receiver")
async def webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Receives WUZAPI webhook events.

    - Delegates all processing to WhatsAppService.
    - Always returns HTTP 200 (malformed payloads are logged and ignored).
    - No auth required — this endpoint is called server-to-server by WUZAPI.
    """
    import json
    try:
        content_type = request.headers.get("content-type", "")
        if "form-urlencoded" in content_type or "multipart/form-data" in content_type:
            form = await request.form()
            payload = json.loads(form.get("jsonData", "{}"))
        else:
            payload = await request.json()
            
        debug_payload = {k: v for k, v in payload.items() if k != 'base64'}
        logger.info(f"Evolution API Payload: {debug_payload}")
    except Exception as exc:
        raw_body = await request.body()
        logger.warning(f"Webhook failed to parse payload. Error: {exc} | Body: {str(raw_body)[:200]}")
        return {"status": "ok"}

    logger.info("Webhook received")

    service = _build_whatsapp_service(db)
    await service.handle_webhook(payload)

    logger.info("Webhook processing finished")
    return {"status": "ok"}
