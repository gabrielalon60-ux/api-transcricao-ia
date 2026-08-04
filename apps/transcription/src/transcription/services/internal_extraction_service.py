from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from transcription.core.config import get_settings
from transcription.core.logging import get_logger
from transcription.database.models import Extraction, Request, RequestStatus, UsageLog
from transcription.schemas.internal import (
    InternalExtractionFailure,
    InternalExtractionMetadata,
    InternalExtractionSuccess,
    InternalFileResponse,
    InternalTimingResponse,
    InternalUsageResponse,
)
from transcription.services.ai.provider import AIProvider, ExtractionResult
from transcription.services.document_validation import (
    MIME_BY_FORMAT,
    ValidationLimits,
    build_validated_document,
    cleanup_temporary_path,
    detect_format,
    materialize_validation_input,
    run_validation_subprocess_sync,
    validate_declared_mime,
)
from transcription.services.prompt_service import PromptConfigurationError

logger = get_logger(__name__)
_validation_semaphore: asyncio.Semaphore | None = None


@dataclass(frozen=True)
class AttemptRecord:
    attempt_number: int
    provider: str | None
    model_name: str
    status: str
    started_at: datetime
    completed_at: datetime
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None
    usage_status: str | None = None
    estimated_cost: Decimal | None = None
    currency: str | None = None
    pricing_version: str | None = None
    sanitized_error_code: str | None = None


@dataclass(frozen=True)
class InternalServiceResult:
    status_code: int
    body: InternalExtractionSuccess | InternalExtractionFailure


class InternalExtractionService:
    def __init__(self, db: Session, ai_provider: AIProvider, compensation_session_factory: Any | None = None):
        self.db = db
        self.ai_provider = ai_provider
        self.settings = get_settings()
        self.compensation_session_factory = compensation_session_factory

    async def process(
        self,
        *,
        metadata: InternalExtractionMetadata,
        file_bytes: bytes,
        declared_mime: str | None,
    ) -> InternalServiceResult:
        start = time.monotonic()
        replay = self._commit_processing_request(metadata)
        if replay is not None:
            return replay

        try:
            detected_format = detect_format(file_bytes)
            detected_mime = MIME_BY_FORMAT[detected_format]
            validate_declared_mime(detected_mime, declared_mime)
            validation_input, temporary_path = materialize_validation_input(
                file_bytes,
                detected_format,
                self._validation_limits(),
                self.settings.upload_spool_max_memory_bytes,
            )
            try:
                validation_result = await self._run_validation(validation_input)
            finally:
                cleanup_temporary_path(temporary_path)
            if not validation_result.ok:
                error_code = validation_result.error_code or "INVALID_DOCUMENT"
                return self._persist_failed(
                    metadata,
                    error_code=error_code,
                    retryable=is_retryable_error(error_code),
                    status_code=http_status_for_error(error_code),
                    attempts=[],
                    elapsed_ms=_elapsed_ms(start),
                    declared_mime=declared_mime,
                    detected_mime=detected_mime,
                    file_size_bytes=len(file_bytes),
                )
            validated = build_validated_document(file_bytes, detected_mime)
        except ValueError as exc:
            return self._persist_failed(
                metadata,
                error_code=str(exc) if str(exc) else "INVALID_DOCUMENT",
                retryable=False,
                status_code=http_status_for_error(str(exc) if str(exc) else "INVALID_DOCUMENT"),
                attempts=[],
                elapsed_ms=_elapsed_ms(start),
                declared_mime=declared_mime,
                detected_mime=None,
                file_size_bytes=len(file_bytes),
            )

        result, attempts, terminal_error, retryable, status_code, retry_after = await self._call_provider_with_retry(
            validated.data
        )
        elapsed_ms = _elapsed_ms(start)
        if result is None:
            return self._persist_failed(
                metadata,
                error_code=terminal_error or "INTERNAL_ERROR",
                retryable=retryable,
                status_code=status_code,
                attempts=attempts,
                elapsed_ms=elapsed_ms,
                declared_mime=declared_mime,
                detected_mime=validated.detected_mime,
                file_size_bytes=validated.size_bytes,
                file_sha256=validated.sha256_hex,
                retry_after_seconds=retry_after,
            )
        return self._persist_succeeded(
            metadata,
            result,
            attempts,
            elapsed_ms,
            declared_mime,
            validated,
        )

    async def _run_validation(self, validation_input: Any) -> Any:
        semaphore = get_validation_semaphore(self.settings.max_concurrent_validations)
        try:
            await asyncio.wait_for(
                semaphore.acquire(),
                timeout=self.settings.validation_acquisition_timeout_seconds,
            )
        except TimeoutError:
            return type("ValidationResultLike", (), {"ok": False, "error_code": "VALIDATION_CAPACITY_EXCEEDED"})()
        try:
            return await asyncio.to_thread(
                run_validation_subprocess_sync,
                validation_input,
                self.settings.document_validation_timeout_seconds,
                self.settings.document_validation_termination_grace_seconds,
            )
        finally:
            semaphore.release()

    def _validation_limits(self) -> ValidationLimits:
        return ValidationLimits(
            max_image_width=self.settings.max_image_width,
            max_image_height=self.settings.max_image_height,
            max_image_pixels=self.settings.max_image_pixels,
            max_pdf_pages=self.settings.max_pdf_pages,
            max_pdf_objects=self.settings.max_pdf_objects,
            max_pdf_traversal_depth=self.settings.max_pdf_traversal_depth,
        )

    def _commit_processing_request(
        self, metadata: InternalExtractionMetadata
    ) -> InternalServiceResult | None:
        request = Request(
            id=metadata.request_id,
            application_id=None,
            status=RequestStatus.PROCESSING,
            correlation_id=metadata.correlation_id,
            instance_id=metadata.bot_instance_id,
            received_at=metadata.received_at,
            source=metadata.source,
            processing_started_at=_utcnow(),
        )
        self.db.add(request)
        try:
            self.db.commit()
            return None
        except IntegrityError:
            self.db.rollback()
            existing = self.db.get(Request, metadata.request_id)
            if existing is None:
                return self._persistence_error_response(metadata.request_id, None)
            return self._response_for_existing(existing)

    def _response_for_existing(self, request: Request) -> InternalServiceResult:
        if request.status == RequestStatus.PROCESSING:
            return InternalServiceResult(
                409,
                InternalExtractionFailure(
                    request_id=request.id,
                    event_id=request.event_id,
                    error_code="REQUEST_ALREADY_PROCESSING",
                    retryable=True,
                    retry_after_seconds=5,
                ),
            )
        if request.status == RequestStatus.PERSISTENCE_FAILED:
            return self._persistence_error_response(request.id, request.event_id)
        if request.status == RequestStatus.SUCCEEDED and request.extraction is not None:
            return InternalServiceResult(
                200,
                InternalExtractionSuccess.model_validate(request.extraction.response_json),
            )
        if request.status == RequestStatus.FAILED:
            error_code = request.error_code or "INTERNAL_ERROR"
            return InternalServiceResult(
                http_status_for_error(error_code),
                InternalExtractionFailure(
                    request_id=request.id,
                    event_id=request.event_id,
                    error_code=error_code,
                    retryable=is_retryable_error(error_code),
                ),
            )
        return InternalServiceResult(
            409,
            InternalExtractionFailure(
                request_id=request.id,
                event_id=request.event_id,
                error_code="REQUEST_ALREADY_PROCESSING",
                retryable=True,
                retry_after_seconds=5,
            ),
        )

    async def _call_provider_with_retry(
        self, file_bytes: bytes
    ) -> tuple[ExtractionResult | None, list[AttemptRecord], str | None, bool, int, int | None]:
        attempts: list[AttemptRecord] = []
        max_attempts = self.settings.provider_max_retries + 1
        last_error = "INTERNAL_ERROR"
        retryable = False
        status_code = 500
        retry_after: int | None = None
        for attempt_number in range(1, max_attempts + 1):
            started_at = _utcnow()
            try:
                result = await asyncio.wait_for(
                    self.ai_provider.extract(file_bytes),
                    timeout=self.settings.provider_timeout_seconds,
                )
                completed_at = _utcnow()
                attempts.append(_successful_attempt(attempt_number, started_at, completed_at, result))
                return result, attempts, None, False, 200, None
            except PromptConfigurationError:
                return None, attempts, "SYSTEM_PROMPT_INVALID", False, 503, None
            except Exception as exc:
                completed_at = _utcnow()
                last_error, retryable, status_code, retry_after = classify_provider_exception(exc)
                attempts.append(
                    AttemptRecord(
                        attempt_number=attempt_number,
                        provider="google",
                        model_name=getattr(self.ai_provider, "model_name", "unknown"),
                        status="FAILED",
                        started_at=started_at,
                        completed_at=completed_at,
                        usage_status="UNAVAILABLE",
                        sanitized_error_code=last_error,
                    )
                )
                if not retryable or attempt_number >= max_attempts:
                    break
                await asyncio.sleep(min(0.1 * attempt_number, 0.5))
        return None, attempts, last_error, retryable, status_code, retry_after

    def _persist_succeeded(
        self,
        metadata: InternalExtractionMetadata,
        result: ExtractionResult,
        attempts: list[AttemptRecord],
        elapsed_ms: int,
        declared_mime: str | None,
        validated: Any,
    ) -> InternalServiceResult:
        request = self.db.get(Request, metadata.request_id)
        if request is None:
            return self._persistence_error_response(metadata.request_id, None)
        body = _success_body(metadata, request, result, elapsed_ms, declared_mime, validated)
        try:
            request.status = RequestStatus.SUCCEEDED
            request.completed_at = _utcnow()
            request.processing_time_ms = elapsed_ms
            request.detected_mime = validated.detected_mime
            request.declared_mime = declared_mime
            request.file_size_bytes = validated.size_bytes
            request.file_sha256 = validated.sha256_hex
            request.error_code = None
            self.db.add(
                Extraction(
                    request_id=metadata.request_id,
                    prompt=None,
                    response_json=body.model_dump(mode="json"),
                    image_reference=None,
                )
            )
            self._add_usage_logs(metadata.request_id, attempts)
            self.db.flush()
            self.db.commit()
            return InternalServiceResult(200, body)
        except SQLAlchemyError:
            self.db.rollback()
            return self._compensate_persistence_failed(metadata.request_id, request.event_id)

    def _persist_failed(
        self,
        metadata: InternalExtractionMetadata,
        *,
        error_code: str,
        retryable: bool,
        status_code: int,
        attempts: list[AttemptRecord],
        elapsed_ms: int,
        declared_mime: str | None,
        detected_mime: str | None,
        file_size_bytes: int | None,
        file_sha256: str | None = None,
        retry_after_seconds: int | None = None,
    ) -> InternalServiceResult:
        request = self.db.get(Request, metadata.request_id)
        if request is None:
            return self._persistence_error_response(metadata.request_id, None)
        clean_error = sanitize_error_code(error_code)
        try:
            request.status = RequestStatus.FAILED
            request.completed_at = _utcnow()
            request.processing_time_ms = elapsed_ms
            request.error_code = clean_error
            request.detected_mime = detected_mime
            request.declared_mime = declared_mime
            request.file_size_bytes = file_size_bytes
            request.file_sha256 = file_sha256
            self._add_usage_logs(metadata.request_id, attempts)
            self.db.flush()
            self.db.commit()
            return InternalServiceResult(
                status_code,
                InternalExtractionFailure(
                    request_id=metadata.request_id,
                    event_id=request.event_id,
                    error_code=clean_error,
                    retryable=retryable,
                    retry_after_seconds=retry_after_seconds,
                ),
            )
        except SQLAlchemyError:
            self.db.rollback()
            return self._compensate_persistence_failed(metadata.request_id, request.event_id)

    def _add_usage_logs(self, request_id: uuid.UUID, attempts: list[AttemptRecord]) -> None:
        for attempt in attempts:
            self.db.add(
                UsageLog(
                    request_id=request_id,
                    attempt_number=attempt.attempt_number,
                    provider=attempt.provider,
                    model_name=attempt.model_name,
                    status=attempt.status,
                    started_at=attempt.started_at,
                    completed_at=attempt.completed_at,
                    input_tokens=attempt.input_tokens,
                    output_tokens=attempt.output_tokens,
                    total_tokens=attempt.total_tokens,
                    cached_tokens=attempt.cached_tokens,
                    usage_status=attempt.usage_status,
                    estimated_cost=attempt.estimated_cost,
                    currency=attempt.currency,
                    pricing_version=attempt.pricing_version,
                    sanitized_error_code=attempt.sanitized_error_code,
                )
            )

    def _compensate_persistence_failed(
        self, request_id: uuid.UUID, event_id: uuid.UUID | None
    ) -> InternalServiceResult:
        compensation_db = None
        try:
            compensation_db = self._new_compensation_session()
            request = compensation_db.get(Request, request_id)
            if request is not None:
                request.status = RequestStatus.PERSISTENCE_FAILED
                request.error_code = "PERSISTENCE_ERROR"
                request.last_persistence_error_at = _utcnow()
                compensation_db.flush()
                compensation_db.commit()
        except SQLAlchemyError:
            if compensation_db is not None:
                compensation_db.rollback()
            logger.error("Failed to compensate persistence failure for request %s", request_id)
        finally:
            if compensation_db is not None:
                compensation_db.close()
        return self._persistence_error_response(request_id, event_id)

    def _new_compensation_session(self) -> Session:
        if self.compensation_session_factory is not None:
            return self.compensation_session_factory()
        bind = self.db.get_bind()
        return sessionmaker(bind=bind, autocommit=False, autoflush=False)()

    def _persistence_error_response(
        self, request_id: uuid.UUID, event_id: uuid.UUID | None
    ) -> InternalServiceResult:
        return InternalServiceResult(
            500,
            InternalExtractionFailure(
                request_id=request_id,
                event_id=event_id,
                error_code="PERSISTENCE_ERROR",
                retryable=False,
            ),
        )


def classify_provider_exception(exc: Exception) -> tuple[str, bool, int, int | None]:
    if isinstance(exc, TimeoutError | asyncio.TimeoutError):
        return "PROVIDER_TIMEOUT", True, 504, 5
    text = str(exc).lower()
    if "429" in text or "resource_exhausted" in text or "rate" in text or "quota" in text:
        return "PROVIDER_RATE_LIMITED", True, 503, 5
    if "500" in text or "502" in text or "503" in text or "unavailable" in text or "transient" in text:
        return "PROVIDER_TEMPORARY_ERROR", True, 503, 5
    if "auth" in text or "credential" in text or "api key" in text:
        return "PROVIDER_AUTH_ERROR", False, 502, None
    if isinstance(exc, ValueError):
        return sanitize_error_code(str(exc)), False, 422, None
    return "INTERNAL_ERROR", False, 500, None


def sanitize_error_code(error_code: str) -> str:
    allowed = {
        "EMPTY_FILE",
        "FILE_TOO_LARGE",
        "INVALID_DOCUMENT",
        "INVALID_IMAGE",
        "INVALID_PDF",
        "UNSUPPORTED_FILE_TYPE",
        "MIME_MISMATCH",
        "DOCUMENT_VALIDATION_TIMEOUT",
        "VALIDATION_PROCESS_FAILED",
        "VALIDATION_CAPACITY_EXCEEDED",
        "UPLOAD_READ_TIMEOUT",
        "PDF_ACTIVE_CONTENT_UNSUPPORTED",
        "PDF_ENCRYPTED",
        "PDF_PAGE_LIMIT_EXCEEDED",
        "PDF_STRUCTURE_LIMIT_EXCEEDED",
        "IMAGE_DIMENSIONS_EXCEEDED",
        "IMAGE_PIXEL_LIMIT_EXCEEDED",
        "ANIMATED_IMAGE_UNSUPPORTED",
        "PROVIDER_TIMEOUT",
        "PROVIDER_RATE_LIMITED",
        "PROVIDER_TEMPORARY_ERROR",
        "PROVIDER_AUTH_ERROR",
        "PERSISTENCE_ERROR",
        "REQUEST_ALREADY_PROCESSING",
        "SYSTEM_PROMPT_INVALID",
    }
    return error_code if error_code in allowed else "INTERNAL_ERROR"


def http_status_for_error(error_code: str) -> int:
    return {
        "FILE_TOO_LARGE": 413,
        "VALIDATION_PROCESS_FAILED": 500,
        "VALIDATION_CAPACITY_EXCEEDED": 503,
        "UPLOAD_READ_TIMEOUT": 408,
        "PROVIDER_TIMEOUT": 504,
        "PROVIDER_RATE_LIMITED": 503,
        "PROVIDER_TEMPORARY_ERROR": 503,
        "PROVIDER_AUTH_ERROR": 502,
        "PERSISTENCE_ERROR": 500,
        "REQUEST_ALREADY_PROCESSING": 409,
        "SYSTEM_PROMPT_INVALID": 503,
        "INTERNAL_ERROR": 500,
    }.get(sanitize_error_code(error_code), 422)


def is_retryable_error(error_code: str) -> bool:
    return sanitize_error_code(error_code) in {
        "VALIDATION_CAPACITY_EXCEEDED",
        "UPLOAD_READ_TIMEOUT",
        "PROVIDER_TIMEOUT",
        "PROVIDER_RATE_LIMITED",
        "PROVIDER_TEMPORARY_ERROR",
    }


def get_validation_semaphore(max_concurrent_validations: int) -> asyncio.Semaphore:
    global _validation_semaphore
    if _validation_semaphore is None:
        _validation_semaphore = asyncio.Semaphore(max_concurrent_validations)
    return _validation_semaphore


def _successful_attempt(
    attempt_number: int, started_at: datetime, completed_at: datetime, result: ExtractionResult
) -> AttemptRecord:
    return AttemptRecord(
        attempt_number=attempt_number,
        provider=result.provider,
        model_name=result.model_name,
        status="SUCCEEDED",
        started_at=started_at,
        completed_at=completed_at,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        total_tokens=result.total_tokens,
        cached_tokens=result.cached_tokens,
        usage_status=result.usage_status,
        estimated_cost=result.estimated_cost,
        currency=result.currency,
        pricing_version=result.pricing_version,
    )


def _success_body(
    metadata: InternalExtractionMetadata,
    request: Request,
    result: ExtractionResult,
    elapsed_ms: int,
    declared_mime: str | None,
    validated: Any,
) -> InternalExtractionSuccess:
    return InternalExtractionSuccess(
        request_id=metadata.request_id,
        event_id=request.event_id,
        status="SUCCEEDED",
        document_type=_nullable_str(result.data.get("document_type")),
        extraction=_dict_or_empty(result.data.get("extraction"), result.data),
        normalization=_dict_or_empty(result.data.get("normalization"), {}),
        confidence=_nullable_float(result.data.get("confidence")),
        quality_flags=_list_or_empty(result.data.get("quality_flags")),
        usage=InternalUsageResponse(
            provider=result.provider,
            model=result.model_name,
            pricing_version=result.pricing_version,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            total_tokens=result.total_tokens,
            cached_tokens=result.cached_tokens,
            usage_status=result.usage_status,
            estimated_cost=str(result.estimated_cost) if result.estimated_cost is not None else None,
            currency=result.currency,
        ),
        file=InternalFileResponse(
            sha256=validated.sha256_hex,
            detected_mime=validated.detected_mime,
            declared_mime=declared_mime,
            size_bytes=validated.size_bytes,
        ),
        timing=InternalTimingResponse(latency_ms=elapsed_ms),
    )


def _dict_or_empty(value: Any, fallback: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(fallback, dict):
        return fallback
    return {}


def _list_or_empty(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _nullable_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _nullable_float(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
