from fastapi import APIRouter, Depends, File, Form, UploadFile, HTTPException, status
from functools import lru_cache
from sqlalchemy.orm import Session
from app.database.session import get_db
from app.database.models import Application
from app.auth.api_key_auth import get_current_application
from app.services.extraction_service import ExtractionService
from app.services.ai.gemini_provider import GeminiProvider
from app.services.ai.provider import AIProvider
from app.schemas.requests import ExtractionResponse
from app.core.logging import get_logger, sanitize_log_value
from app.core.config import get_settings

logger = get_logger(__name__)

router = APIRouter(prefix="/extract", tags=["Extraction"])


@lru_cache(maxsize=1)
def _get_ai_provider() -> AIProvider:
    """Lazily creates and caches the AI provider (after .env is loaded)."""
    return GeminiProvider()


@router.post(
    "",
    response_model=ExtractionResponse,
    summary="Extract structured data from an image",
    description=(
        "Submit an image file. "
        "The AI analyzes the image and returns a JSON object with the fields defined in the system prompt."
    ),
)
async def extract(
    file: UploadFile = File(..., description="Image file to analyze (JPEG, PNG, WEBP, PDF)."),
    current_app: Application = Depends(get_current_application),
    db: Session = Depends(get_db),
):
    logger.info(
        f"POST /extract | app='{current_app.name}' | "
        f"file='{sanitize_log_value(file.filename)}' | "
        f"content_type='{sanitize_log_value(file.content_type)}'"
    )

    # Read image bytes
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    # Enforce upload size limit
    settings = get_settings()
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if len(image_bytes) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Maximum allowed size is {settings.max_upload_size_mb} MB.",
        )

    service = ExtractionService(db=db, ai_provider=_get_ai_provider())

    try:
        request_id, data = await service.process(
            application_id=current_app.id,
            image_bytes=image_bytes,
            image_filename=file.filename,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except Exception as exc:
        exc_str = str(exc)
        if "503" in exc_str or "UNAVAILABLE" in exc_str or "high demand" in exc_str:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="AI provider is currently unavailable due to high demand. Please try again in a few seconds.",
            )
        if "429" in exc_str or "RESOURCE_EXHAUSTED" in exc_str or "quota" in exc_str.lower():
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="AI provider quota exceeded. Please check your API plan and billing, or try again later.",
            )
        logger.exception("Unhandled error during extraction.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Extraction failed. See server logs for details.",
        )

    return ExtractionResponse(success=True, request_id=request_id, data=data)
