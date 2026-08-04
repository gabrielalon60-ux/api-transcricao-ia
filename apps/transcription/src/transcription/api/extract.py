from fastapi import APIRouter, Depends, File, UploadFile, HTTPException, status
from functools import lru_cache
from sqlalchemy.orm import Session
from transcription.database.session import get_db
from transcription.database.models import Application
from transcription.auth.api_key_auth import get_current_application
from transcription.services.extraction_service import ExtractionService
from transcription.services.ai.gemini_provider import GeminiProvider
from transcription.services.ai.provider import AIProvider
from transcription.schemas.requests import ExtractionResponse
from transcription.core.logging import get_logger, sanitize_log_value
from transcription.core.config import get_settings
from transcription.services.prompt_service import PromptConfigurationError

logger = get_logger(__name__)

router = APIRouter(prefix="/extract", tags=["Extraction"])


def validate_magic_bytes(data: bytes) -> str:
    """
    Validates magic bytes against supported document signatures.
    Returns the resolved MIME type or raises ValueError.
    """
    if len(data) < 4:
        raise ValueError("File is too small to determine format.")
    
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] == b"%PDF":
        return "application/pdf"
        
    raise ValueError("Invalid or unsupported file format signature.")


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
    file: UploadFile = File(
        ..., description="Image file to analyze (JPEG, PNG, WEBP, PDF)."
    ),
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

    # Validate Magic Bytes / real MIME signature
    try:
        real_mime = validate_magic_bytes(image_bytes)
        logger.info(f"Verified magic bytes: real_mime={real_mime}")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    service = ExtractionService(db=db, ai_provider=_get_ai_provider())

    try:
        request_id, data = await service.process(
            application_id=current_app.id,
            image_bytes=image_bytes,
            image_filename=file.filename,
        )
    except PromptConfigurationError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SYSTEM_PROMPT_INVALID",
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
        if (
            "429" in exc_str
            or "RESOURCE_EXHAUSTED" in exc_str
            or "quota" in exc_str.lower()
        ):
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
