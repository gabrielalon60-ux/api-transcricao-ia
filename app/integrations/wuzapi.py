"""
WUZAPI HTTP integration layer.

All communication with the WUZAPI server is isolated here.
No extraction logic lives in this module — only HTTP calls.
"""

import json
import time
import traceback
import uuid
from typing import Any, Optional

import httpx
from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class WuzapiError(Exception):
    """Raised when a WUZAPI HTTP call fails."""


class WuzapiClient:
    """Encapsulates every HTTP call to the WUZAPI server."""

    def __init__(self, settings: Settings) -> None:
        self.base_url = settings.wuzapi_base_url.rstrip("/")
        self.instance = settings.wuzapi_instance
        self.token = settings.wuzapi_token
        self._headers = {
            "token": self.token,
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _mask_headers(self, headers: dict) -> dict:
        """Return a copy of *headers* with sensitive values masked."""
        masked = dict(headers)
        if "token" in masked:
            masked["token"] = "***"
        return masked

    def _pretty(self, obj: Any) -> str:
        """JSON-pretty-print a dict/list, or return str(obj) as fallback."""
        try:
            return json.dumps(obj, indent=4, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(obj)

    def _safe_response_text(self, response: httpx.Response) -> str:
        """Attempt to return the response body as pretty JSON, else raw text."""
        try:
            return self._pretty(response.json())
        except Exception:
            return response.text

    # ------------------------------------------------------------------
    # Centralized request executor
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict,
        timeout: int,
        params: Optional[dict] = None,
        json_payload: Optional[dict] = None,
    ) -> httpx.Response:
        """
        Execute an HTTP request with full structured logging.

        Logs:
        - Outbound request details (method, URL, masked headers, payload, timeout)
        - Successful response details (status, elapsed, body)
        - Specific exception diagnostics per httpx error type

        Returns the httpx.Response on success.
        Raises WuzapiError on any failure.
        """
        request_id = str(uuid.uuid4())
        masked_headers = self._mask_headers(headers)

        # ---- Log outbound request ----
        log_parts = [
            "WUZAPI Request",
            f"Request ID: {request_id}",
            f"Method: {method.upper()}",
            f"URL: {url}",
            f"Headers:\n{self._pretty(masked_headers)}",
        ]
        if params:
            log_parts.append(f"Params:\n{self._pretty(params)}")
        if json_payload is not None:
            log_parts.append(f"Payload:\n{self._pretty(json_payload)}")
        log_parts.append(f"Timeout: {timeout} seconds")

        logger.info("\n\n".join(log_parts))

        # ---- Execute ----
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json_payload,
                )
                response.raise_for_status()

        # ---- httpx.HTTPStatusError ----
        except httpx.HTTPStatusError as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.error(
                "\n\n".join([
                    "WUZAPI HTTP Error",
                    f"Request ID: {request_id}",
                    f"Method: {method.upper()}",
                    f"URL: {url}",
                    f"Status: {exc.response.status_code}",
                    f"Elapsed: {elapsed_ms:.0f} ms",
                    f"Response Body:\n{self._safe_response_text(exc.response)}",
                    f"Response Headers:\n{self._pretty(dict(exc.response.headers))}",
                ])
            )
            raise WuzapiError(
                f"[{request_id}] HTTP {exc.response.status_code} from {url}"
            ) from exc

        # ---- httpx.ConnectTimeout ----
        except httpx.ConnectTimeout as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.error(
                "\n\n".join([
                    "WUZAPI Connect Timeout",
                    f"Request ID: {request_id}",
                    "Could not establish a connection to the WUZAPI server.",
                    f"URL: {url}",
                    f"Timeout: {timeout} seconds",
                    f"Elapsed: {elapsed_ms:.0f} ms",
                    "Possible causes:\n"
                    "- WUZAPI offline\n"
                    "- Wrong IP\n"
                    "- Wrong port\n"
                    "- Oracle Security List\n"
                    "- Ubuntu firewall\n"
                    "- Docker port mapping",
                ])
            )
            raise WuzapiError(
                f"[{request_id}] Connect timeout reaching {url}"
            ) from exc

        # ---- httpx.ReadTimeout ----
        except httpx.ReadTimeout as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.error(
                "\n\n".join([
                    "WUZAPI Read Timeout",
                    f"Request ID: {request_id}",
                    "Connection established successfully.",
                    "The server did not respond before the configured timeout.",
                    f"URL: {url}",
                    f"Timeout: {timeout} seconds",
                    f"Elapsed: {elapsed_ms:.0f} ms",
                ])
            )
            raise WuzapiError(
                f"[{request_id}] Read timeout from {url}"
            ) from exc

        # ---- httpx.ConnectError ----
        except httpx.ConnectError as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            errno_info = ""
            if hasattr(exc, "__cause__") and exc.__cause__:
                cause = exc.__cause__
                if hasattr(cause, "errno"):
                    errno_info = f"\nerrno: {cause.errno}"
            logger.error(
                "\n\n".join([
                    "WUZAPI Connection Error",
                    f"Request ID: {request_id}",
                    "Unable to establish TCP connection.",
                    f"URL: {url}",
                    f"Elapsed: {elapsed_ms:.0f} ms",
                    f"Exception: {repr(exc)}{errno_info}",
                ])
            )
            raise WuzapiError(
                f"[{request_id}] Connection error to {url}"
            ) from exc

        # ---- httpx.NetworkError (parent of ConnectError, but catch separately for others) ----
        except httpx.NetworkError as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.error(
                "\n\n".join([
                    "WUZAPI Network Error",
                    f"Request ID: {request_id}",
                    f"URL: {url}",
                    f"Elapsed: {elapsed_ms:.0f} ms",
                    f"Exception: {repr(exc)}",
                    f"Traceback:\n{traceback.format_exc()}",
                ])
            )
            raise WuzapiError(
                f"[{request_id}] Network error reaching {url}"
            ) from exc

        # ---- httpx.RequestError (catch-all for remaining httpx errors) ----
        except httpx.RequestError as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.error(
                "\n\n".join([
                    "WUZAPI Request Error",
                    f"Request ID: {request_id}",
                    f"URL: {url}",
                    f"Elapsed: {elapsed_ms:.0f} ms",
                    f"Exception: {repr(exc)}",
                    f"Traceback:\n{traceback.format_exc()}",
                ])
            )
            raise WuzapiError(
                f"[{request_id}] Request error to {url}"
            ) from exc

        # ---- Truly unexpected ----
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            context_parts = [
                "WUZAPI Unexpected Error",
                f"Request ID: {request_id}",
                f"URL: {url}",
                f"Method: {method.upper()}",
                f"Elapsed: {elapsed_ms:.0f} ms",
                f"Exception: {repr(exc)}",
                f"Traceback:\n{traceback.format_exc()}",
            ]
            if json_payload:
                context_parts.append(f"Payload:\n{self._pretty(json_payload)}")
            logger.error("\n\n".join(context_parts))
            raise WuzapiError(
                f"[{request_id}] Unexpected error calling {url}"
            ) from exc

        # ---- Log successful response ----
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "\n\n".join([
                "WUZAPI Response",
                f"Request ID: {request_id}",
                f"Status: {response.status_code}",
                f"Elapsed: {elapsed_ms:.0f} ms",
                f"Body:\n{self._safe_response_text(response)}",
            ])
        )

        return response

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_media_info(self, message_id: str) -> dict:
        """
        Retrieve media metadata (URL, mime_type, etc.) for a given message ID.

        Returns the JSON dict from WUZAPI.
        Raises WuzapiError on failure.
        """
        url = f"{self.base_url}/chat/getmessage"
        params = {"messageId": message_id}

        response = await self._request(
            "GET",
            url,
            headers=self._headers,
            timeout=30,
            params=params,
        )

        data = response.json()
        return data

    async def download_media(self, media_url: str) -> bytes:
        """
        Download raw bytes from a WUZAPI media URL.

        Raises WuzapiError on failure.
        """
        response = await self._request(
            "GET",
            media_url,
            headers={"token": self.token},
            timeout=60,
        )

        return response.content

    async def send_text_message(self, phone: str, text: str) -> None:
        """
        Send a plain-text WhatsApp message via WUZAPI.

        Raises WuzapiError on failure.
        """
        url = f"{self.base_url}/chat/send/text"
        payload = {
            "phone": phone,
            "body": text,
        }

        await self._request(
            "POST",
            url,
            headers=self._headers,
            timeout=30,
            json_payload=payload,
        )
