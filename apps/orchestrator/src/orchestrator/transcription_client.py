from __future__ import annotations

import json
import httpx
from datetime import datetime, timezone
from typing import Any, Dict

from orchestrator.config import get_settings


class TranscriptionClientError(Exception):
    def __init__(self, message: str, status_code: int | None = None, error_code: str | None = None, retryable: bool = True):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.retryable = retryable


class TranscriptionClient:
    def __init__(self, base_url: str | None = None, token: str | None = None, client: httpx.AsyncClient | None = None):
        settings = get_settings()
        resolved_base_url: str = base_url or getattr(settings, "transcription_service_url", "http://localhost:8000") or "http://localhost:8000"
        self.base_url = resolved_base_url.rstrip("/")
        self.token = token or getattr(settings, "bot_to_transcription_token", "dev_bot_token_secret_123") or "dev_bot_token_secret_123"
        self.client = client

    async def extract(
        self,
        processing_item_id: str,
        bot_instance_id: str,
        correlation_id: str,
        received_at: datetime,
        file_bytes: bytes,
        filename: str,
        mime_type: str,
    ) -> Dict[str, Any]:
        """Dispatches an extraction request to Gate 3 Transcription internal endpoint."""
        url = f"{self.base_url}/internal/extract"
        headers = {
            "Authorization": f"Bearer {self.token}",
        }

        # Ensure UTC timezone aware
        if received_at.tzinfo is None:
            received_at = received_at.replace(tzinfo=timezone.utc)

        metadata_dict = {
            "request_id": str(processing_item_id),
            "bot_instance_id": str(bot_instance_id),
            "correlation_id": correlation_id,
            "received_at": received_at.isoformat(),
            "source": "WHATSAPP",
        }

        files = {
            "file": (filename, file_bytes, mime_type),
        }
        data = {
            "metadata": json.dumps(metadata_dict),
        }

        async_client = self.client or httpx.AsyncClient(timeout=60.0)
        close_client = self.client is None

        try:
            response = await async_client.post(url, headers=headers, data=data, files=files)
            if response.status_code == 200:
                return response.json()
            elif response.status_code in (400, 413, 422):
                res_json = response.json() if response.headers.get("content-type") == "application/json" else {}
                error_code = res_json.get("error_code") or "UNSUPPORTED_DOCUMENT"
                retryable = res_json.get("retryable", False)
                raise TranscriptionClientError(
                    message=f"Terminal extraction error: {error_code}",
                    status_code=response.status_code,
                    error_code=error_code,
                    retryable=retryable,
                )
            else:
                raise TranscriptionClientError(
                    message=f"Extraction service error: HTTP {response.status_code}",
                    status_code=response.status_code,
                    error_code="TRANSCRIPTION_SERVICE_ERROR",
                    retryable=True,
                )
        except httpx.RequestError as exc:
            raise TranscriptionClientError(
                message=f"Network error calling Transcription service: {exc}",
                status_code=503,
                error_code="NETWORK_ERROR",
                retryable=True,
            ) from exc
        finally:
            if close_client:
                await async_client.aclose()
