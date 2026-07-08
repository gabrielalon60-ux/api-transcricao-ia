"""
WUZAPI HTTP integration layer.

All communication with the WUZAPI server is isolated here.
No extraction logic lives in this module — only HTTP calls.
"""

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

        logger.debug(f"WuzapiClient.get_media_info | message_id={message_id}")
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                response = await client.get(url, headers=self._headers, params=params)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise WuzapiError(
                    f"Failed to get media info for message {message_id}: {exc}"
                ) from exc

        data = response.json()
        logger.debug(f"WuzapiClient.get_media_info response: {str(data)[:200]}")
        return data

    async def download_media(self, media_url: str) -> bytes:
        """
        Download raw bytes from a WUZAPI media URL.

        Raises WuzapiError on failure.
        """
        logger.debug(f"WuzapiClient.download_media | url={media_url[:80]}")
        async with httpx.AsyncClient(timeout=60) as client:
            try:
                response = await client.get(media_url, headers={"token": self.token})
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise WuzapiError(
                    f"Failed to download media from {media_url}: {exc}"
                ) from exc

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

        logger.debug(f"WuzapiClient.send_text_message | phone={phone}")
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                response = await client.post(
                    url, headers=self._headers, json=payload
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise WuzapiError(
                    f"Failed to send text message to {phone}: {exc}"
                ) from exc

        logger.debug(f"WuzapiClient.send_text_message | sent to {phone}")
