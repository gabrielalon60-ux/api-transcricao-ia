from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class InternalExtractionMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: uuid.UUID
    bot_instance_id: uuid.UUID
    correlation_id: str = Field(min_length=1, max_length=128)
    received_at: datetime
    source: Literal["WHATSAPP"]

    @field_validator("received_at")
    @classmethod
    def validate_received_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("received_at must be timezone-aware")
        return value.astimezone(timezone.utc)

    @field_validator("correlation_id")
    @classmethod
    def validate_correlation_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("correlation_id must not be blank")
        return value


class InternalUsageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str | None = None
    model: str
    pricing_version: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None
    usage_status: str | None = None
    estimated_cost: str | None = None
    currency: str | None = None


class InternalFileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sha256: str
    detected_mime: str
    declared_mime: str | None = None
    size_bytes: int


class InternalTimingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latency_ms: int


class InternalExtractionSuccess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: uuid.UUID
    event_id: uuid.UUID | None = None
    status: Literal["SUCCEEDED"]
    document_type: str | None = None
    extraction: dict[str, Any]
    normalization: dict[str, Any]
    confidence: float | None = None
    quality_flags: list[str]
    usage: InternalUsageResponse
    file: InternalFileResponse
    timing: InternalTimingResponse


class InternalExtractionFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: uuid.UUID | None = None
    event_id: uuid.UUID | None = None
    status: Literal["FAILED"] = "FAILED"
    error_code: str
    retryable: bool
    retry_after_seconds: int | None = None
