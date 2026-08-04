from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from transcription.auth.internal import verify_internal_transcription_token
from transcription.core.config import get_settings
from transcription.core.logging import get_logger, sanitize_log_value
from transcription.database.session import get_db
from transcription.schemas.internal import InternalExtractionFailure, InternalExtractionMetadata
from transcription.services.ai.gemini_provider import GeminiProvider
from transcription.services.ai.provider import AIProvider
from transcription.services.internal_extraction_service import InternalExtractionService

logger = get_logger(__name__)

router = APIRouter(prefix="/internal", tags=["Internal Extraction"])


def get_internal_ai_provider() -> AIProvider:
    return GeminiProvider()


@router.post("/extract")
async def internal_extract(
    file: UploadFile = File(..., description="JPEG, PNG, WEBP, or PDF document."),
    metadata: str = Form(..., description="JSON metadata string."),
    _: None = Depends(verify_internal_transcription_token),
    db: Session = Depends(get_db),
    ai_provider: AIProvider = Depends(get_internal_ai_provider),
):
    logger.info(
        "POST /internal/extract | filename='%s' | content_type='%s'",
        sanitize_log_value(file.filename),
        sanitize_log_value(file.content_type),
    )
    try:
        parsed_metadata = InternalExtractionMetadata.model_validate_json(metadata)
    except ValidationError:
        body = InternalExtractionFailure(
            request_id=None,
            error_code="INVALID_METADATA",
            retryable=False,
        )
        return JSONResponse(status_code=422, content=body.model_dump(mode="json"))

    try:
        file_bytes = await read_upload_bounded(file)
    except HTTPException as exc:
        body = InternalExtractionFailure(
            request_id=parsed_metadata.request_id,
            error_code=str(exc.detail),
            retryable=False,
        )
        return JSONResponse(status_code=exc.status_code, content=body.model_dump(mode="json"))
    finally:
        await file.close()

    service = InternalExtractionService(db=db, ai_provider=ai_provider)
    result = await service.process(
        metadata=parsed_metadata,
        file_bytes=file_bytes,
        declared_mime=file.content_type,
    )
    return JSONResponse(status_code=result.status_code, content=result.body.model_dump(mode="json"))


async def read_upload_bounded(file: UploadFile) -> bytes:
    settings = get_settings()
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    chunks: list[bytes] = []
    total = 0
    start_loop_time = asyncio.get_running_loop().time()
    while True:
        if asyncio.get_running_loop().time() - start_loop_time > settings.upload_total_timeout_seconds:
            raise HTTPException(status_code=408, detail="UPLOAD_READ_TIMEOUT")
        try:
            chunk = await asyncio.wait_for(
                file.read(settings.upload_chunk_size_bytes),
                timeout=settings.upload_chunk_read_timeout_seconds,
            )
        except TimeoutError:
            raise HTTPException(status_code=408, detail="UPLOAD_READ_TIMEOUT") from None
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail="FILE_TOO_LARGE")
        chunks.append(chunk)
    if total == 0:
        raise HTTPException(status_code=422, detail="EMPTY_FILE")
    return b"".join(chunks)
