import base64
import logging
from typing import Any, Optional

import httpx
from orchestrator.config import get_settings

logger = logging.getLogger(__name__)


class WuzapiError(Exception):
    """Raised when communication with WUZAPI fails, with typed retryability classification."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        http_status: Optional[int] = None,
        reason: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        self.http_status = http_status
        self.reason = reason or ("HTTP_" + str(http_status) if http_status else "WUZAPI_ERROR")


def _normalize_crypto_field(field_name: str, val: Any, required: bool = False) -> str:
    """Normalizes base64 string or bytes for cryptographic payload fields without leaking content."""
    if val is None or val == "":
        if required:
            raise WuzapiError(
                f"Missing required cryptographic field '{field_name}'.",
                retryable=False,
                reason="MISSING_CRYPTO_FIELD",
            )
        return ""
    if isinstance(val, bool):
        raise WuzapiError(
            f"Invalid boolean value for cryptographic field '{field_name}'.",
            retryable=False,
            reason="INVALID_CRYPTO_FIELD_TYPE",
        )
    if isinstance(val, str):
        return val
    if isinstance(val, (bytes, bytearray)):
        return base64.b64encode(val).decode("ascii")
    raise WuzapiError(
        f"Unsupported type '{type(val).__name__}' for cryptographic field '{field_name}'.",
        retryable=False,
        reason="INVALID_CRYPTO_FIELD_TYPE",
    )


def _parse_file_length(val: Any) -> int:
    """Safely parses file length to positive integer."""
    if val is None or isinstance(val, bool):
        return 0
    if not isinstance(val, (str, int)):
        raise WuzapiError(
            f"Invalid FileLength type '{type(val).__name__}'.",
            retryable=False,
            reason="INVALID_FILE_LENGTH",
        )
    try:
        num = int(val)
    except (ValueError, TypeError) as exc:
        raise WuzapiError(
            "Invalid FileLength: must be numeric.",
            retryable=False,
            reason="INVALID_FILE_LENGTH",
        ) from exc
    return max(0, num)


class WuzapiClient:
    """Encapsulates outbound API requests to the WUZAPI server using pinned v1.0.8 contracts."""

    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = settings.wuzapi_base_url.rstrip("/")
        self.token = settings.wuzapi_token
        self._headers = {
            "token": self.token,
            "Content-Type": "application/json",
        }

    async def send_text_message(self, phone: str, text: str) -> None:
        """Send a plain-text WhatsApp message via WUZAPI chat/send/text."""
        if not self.base_url or not self.token:
            logger.warning(
                "WuzapiClient is not fully configured (missing base URL or token). Skipping send."
            )
            return

        url = f"{self.base_url}/chat/send/text"
        payload = {
            "phone": phone,
            "body": text,
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(url, headers=self._headers, json=payload)
                response.raise_for_status()
                logger.info(
                    f"Successfully sent WUZAPI message to phone {phone[:5]}****"
                )
        except httpx.HTTPError as exc:
            logger.error(f"WUZAPI outbound request failed: {exc}")
            raise WuzapiError(
                f"Failed to send WhatsApp message via WUZAPI: {exc}",
                retryable=True,
                reason="SEND_TEXT_FAILED",
            ) from exc

    def _resolve_download_route(self, media_type: str) -> str:
        """Selects native WUZAPI v1.0.8 endpoint based on media type or MIME."""
        norm = media_type.strip().lower()
        if norm in ("image", "imagemessage") or norm.startswith("image/"):
            return f"{self.base_url}/chat/downloadimage"
        if norm in ("document", "documentmessage", "pdf") or norm.startswith("application/") or norm.startswith("text/"):
            return f"{self.base_url}/chat/downloaddocument"
        if norm in ("audio", "audiomessage") or norm.startswith("audio/"):
            return f"{self.base_url}/chat/downloadaudio"
        if norm in ("video", "videomessage") or norm.startswith("video/"):
            return f"{self.base_url}/chat/downloadvideo"
        if norm in ("sticker", "stickermessage"):
            return f"{self.base_url}/chat/downloadsticker"
        # Default fallback for unclassified binary media is document endpoint
        return f"{self.base_url}/chat/downloaddocument"

    async def download_media(
        self,
        media_ref: dict[str, Any],
        mime_type: Optional[str] = None,
        phone: Optional[str] = None,
    ) -> bytes:
        """Downloads source media binary from native WUZAPI v1.0.8 endpoint and decodes Data URI."""
        if not self.base_url or not self.token:
            raise WuzapiError(
                "WuzapiClient is not fully configured (missing base URL or token).",
                retryable=False,
                reason="CLIENT_NOT_CONFIGURED",
            )

        if not isinstance(media_ref, dict):
            raise WuzapiError(
                "Invalid media_ref: expected dictionary.",
                retryable=False,
                reason="INVALID_MEDIA_REF",
            )

        effective_type = (
            mime_type
            or media_ref.get("mime_type")
            or media_ref.get("mimetype")
            or media_ref.get("message_type")
            or "image"
        )
        url = self._resolve_download_route(effective_type)

        raw_media_key = media_ref.get("media_key") or media_ref.get("mediaKey") or media_ref.get("MediaKey")
        media_key = _normalize_crypto_field("MediaKey", raw_media_key, required=True)

        raw_file_enc_sha = (
            media_ref.get("file_enc_sha256")
            or media_ref.get("fileEncSHA256")
            or media_ref.get("FileEncSHA256")
            or media_ref.get("fileEncSha256")
        )
        file_enc_sha = _normalize_crypto_field("FileEncSHA256", raw_file_enc_sha)

        raw_file_sha = (
            media_ref.get("file_sha256")
            or media_ref.get("expected_sha256")
            or media_ref.get("fileSHA256")
            or media_ref.get("FileSHA256")
        )
        file_sha = _normalize_crypto_field("FileSHA256", raw_file_sha)

        raw_length = (
            media_ref.get("file_length")
            or media_ref.get("expected_size")
            or media_ref.get("fileLength")
            or media_ref.get("FileLength")
        )
        file_length = _parse_file_length(raw_length)

        mimetype_sent = (
            media_ref.get("mime_type")
            or media_ref.get("mimetype")
            or media_ref.get("Mimetype")
            or mime_type
            or ""
        )

        direct_path = (
            media_ref.get("direct_path")
            or media_ref.get("directPath")
            or media_ref.get("DirectPath")
            or ""
        )

        download_url = (
            media_ref.get("url")
            or media_ref.get("URL")
            or media_ref.get("media_url")
            or ""
        )

        payload = {
            "Url": download_url,
            "DirectPath": direct_path,
            "MediaKey": media_key,
            "Mimetype": mimetype_sent,
            "FileEncSHA256": file_enc_sha,
            "FileSHA256": file_sha,
            "FileLength": file_length,
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(url, headers=self._headers, json=payload)
        except httpx.TimeoutException as exc:
            raise WuzapiError(
                f"WUZAPI media download timed out: {exc}",
                retryable=True,
                reason="TIMEOUT",
            ) from exc
        except httpx.ConnectError as exc:
            raise WuzapiError(
                f"WUZAPI media download connection failed: {exc}",
                retryable=True,
                reason="CONNECT_ERROR",
            ) from exc
        except httpx.HTTPError as exc:
            raise WuzapiError(
                f"WUZAPI media download request failed: {exc}",
                retryable=True,
                reason="HTTP_REQUEST_FAILED",
            ) from exc

        if response.status_code == 404:
            raise WuzapiError(
                "WUZAPI media endpoint or resource not found (HTTP 404)",
                retryable=False,
                http_status=404,
                reason="NOT_FOUND_404",
            )
        if response.status_code in (400, 401, 403, 422):
            raise WuzapiError(
                f"WUZAPI client error HTTP {response.status_code}",
                retryable=False,
                http_status=response.status_code,
                reason=f"CLIENT_ERROR_{response.status_code}",
            )
        if response.status_code >= 500:
            raise WuzapiError(
                f"WUZAPI server error HTTP {response.status_code}",
                retryable=True,
                http_status=response.status_code,
                reason=f"SERVER_ERROR_{response.status_code}",
            )
        if response.status_code != 200:
            raise WuzapiError(
                f"WUZAPI unexpected response HTTP {response.status_code}",
                retryable=False,
                http_status=response.status_code,
                reason=f"UNEXPECTED_STATUS_{response.status_code}",
            )

        try:
            res_data = response.json()
        except Exception as exc:
            raise WuzapiError(
                f"Failed to parse JSON response from WUZAPI media endpoint: {exc}",
                retryable=False,
                reason="MALFORMED_JSON_RESPONSE",
            ) from exc

        if not isinstance(res_data, dict):
            raise WuzapiError(
                "Invalid response structure from WUZAPI media endpoint.",
                retryable=False,
                reason="INVALID_RESPONSE_STRUCTURE",
            )

        data_url = res_data.get("Data") or res_data.get("data") or ""
        if not isinstance(data_url, str) or not data_url.startswith("data:") or ";base64," not in data_url:
            raise WuzapiError(
                "Invalid or missing Data URL in WUZAPI media response.",
                retryable=False,
                reason="INVALID_DATA_URL",
            )

        _header, base64_payload = data_url.split(";base64,", 1)
        try:
            media_bytes = base64.b64decode(base64_payload, validate=True)
        except Exception as exc:
            raise WuzapiError(
                f"Failed to decode base64 Data URL payload: {exc}",
                retryable=False,
                reason="BASE64_DECODE_ERROR",
            ) from exc

        if not media_bytes:
            raise WuzapiError(
                "Decoded media binary payload from WUZAPI is empty.",
                retryable=False,
                reason="EMPTY_MEDIA_BYTES",
            )

        if len(media_bytes) > 10 * 1024 * 1024:
            raise WuzapiError(
                "Media download exceeded maximum size limit of 10MB.",
                retryable=False,
                reason="FILE_SIZE_LIMIT_EXCEEDED",
            )

        return media_bytes
