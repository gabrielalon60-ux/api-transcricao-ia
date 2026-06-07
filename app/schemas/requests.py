import uuid
from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field
from app.database.models import RequestStatus


# --- Extraction ---

class ExtractionRequest(BaseModel):
    """Schema for the extraction request body (prompt field)."""
    prompt: str = Field(..., min_length=1, description="Instructions describing what to extract from the image.")


class ExtractionResponse(BaseModel):
    """Schema for the /extract response."""
    success: bool
    request_id: uuid.UUID
    data: dict[str, Any]

    class Config:
        from_attributes = True


class ExtractionErrorResponse(BaseModel):
    """Schema for error responses."""
    success: bool = False
    error: str
    detail: str | None = None


# --- Request History ---

class RequestOut(BaseModel):
    id: uuid.UUID
    application_id: uuid.UUID
    created_at: datetime
    completed_at: datetime | None
    status: RequestStatus
    processing_time_ms: int | None

    class Config:
        from_attributes = True


class RequestListResponse(BaseModel):
    total: int
    items: list[RequestOut]
