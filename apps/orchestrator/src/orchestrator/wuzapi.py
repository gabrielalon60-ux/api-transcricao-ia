import httpx
import logging
from orchestrator.config import get_settings

logger = logging.getLogger(__name__)


class WuzapiError(Exception):
    """Raised when communication with WUZAPI fails."""

    pass


class WuzapiClient:
    """Encapsulates outbound API requests to the WUZAPI server."""

    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = settings.wuzapi_base_url.rstrip("/")
        self.token = settings.wuzapi_token
        self._headers = {
            "token": self.token,
            "Content-Type": "application/json",
        }

    async def send_text_message(self, phone: str, text: str) -> None:
        """
        Send a plain-text WhatsApp message via WUZAPI chat/send/text.
        """
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
                f"Failed to send WhatsApp message via WUZAPI: {exc}"
            ) from exc
