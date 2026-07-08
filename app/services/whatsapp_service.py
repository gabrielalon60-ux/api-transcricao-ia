"""
WhatsApp service — orchestrates the full webhook processing flow.

Responsibilities:
- Validate and filter incoming WUZAPI webhook payloads.
- Download supported media (images / PDF) via WuzapiClient.
- Call ExtractionService directly (no internal HTTP request).
- Format the result via ResponseFormatter.
- Send the reply back through WuzapiClient.

This service contains NO extraction logic and NO WUZAPI HTTP code.
"""

import uuid
from sqlalchemy.orm import Session

from app.services.extraction_service import ExtractionService
from app.services.response_formatter import ResponseFormatter
from app.services.ai.provider import AIProvider
from app.integrations.wuzapi import WuzapiClient, WuzapiError
from app.core.logging import get_logger

logger = get_logger(__name__)

# MIME types accepted for extraction
SUPPORTED_IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp"}
SUPPORTED_DOC_MIMES = {"application/pdf"}
SUPPORTED_MIMES = SUPPORTED_IMAGE_MIMES | SUPPORTED_DOC_MIMES

# WhatsApp message types we care about
SUPPORTED_MSG_TYPES = {"imageMessage", "documentMessage"}


class WhatsAppService:
    """
    Coordinates the full WhatsApp webhook processing pipeline.

    Injected dependencies:
        db           — SQLAlchemy session (per-request).
        ai_provider  — Reused AI provider (cached singleton).
        wuzapi       — WUZAPI HTTP client.
        formatter    — ResponseFormatter instance.
        application_id — UUID of the Application row for WhatsApp requests.
    """

    def __init__(
        self,
        db: Session,
        ai_provider: AIProvider,
        wuzapi: WuzapiClient,
        formatter: ResponseFormatter,
        application_id: uuid.UUID,
    ) -> None:
        self.db = db
        self.ai_provider = ai_provider
        self.wuzapi = wuzapi
        self.formatter = formatter
        self.application_id = application_id

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def handle_webhook(self, payload: dict) -> None:
        """
        Process a single WUZAPI webhook event.

        Never raises — all exceptions are caught, logged, and replied to
        the sender so the webhook always returns HTTP 200.
        """
        try:
            await self._process(payload)
        except Exception as exc:
            logger.exception(f"Unexpected error in handle_webhook: {exc}")

    # ------------------------------------------------------------------
    # Private pipeline
    # ------------------------------------------------------------------

    async def _process(self, payload: dict) -> None:
        # ── 1. Detect Payload Type & Extract Data ──────────────────────
        is_evolution = "base64" in payload and isinstance(payload.get("event"), dict)
        
        if is_evolution:
            info = payload["event"].get("Info", {})
            if info.get("IsFromMe", False):
                logger.info("Ignoring outbound message sent by the bot itself.")
                return
                
            msg_type = info.get("MediaType", "")
            if msg_type not in {"image", "document"}:
                logger.info(f"Unsupported Evolution media type '{msg_type}' — ignoring.")
                return
                
            remote_jid = info.get("SenderAlt") or info.get("Sender") or ""
            phone = remote_jid.replace("@s.whatsapp.net", "").replace("@g.us", "").replace("@lid", "")
            mime_type = payload.get("mimeType", "")
            filename = payload.get("fileName", f"whatsapp_{msg_type}")
            
            import base64
            try:
                media_bytes = base64.b64decode(payload["base64"])
            except Exception as e:
                logger.exception(f"Failed to decode base64: {e}")
                return
                
        else:
            # WUZAPI Logic
            event_type = payload.get("event") or payload.get("type") or ""
            data = payload.get("data") or {}
            message = data.get("message") or {}

            if not message and "messages" in data:
                messages_list = data["messages"]
                message = messages_list[0] if messages_list else {}

            if not message:
                logger.info(f"Webhook event '{event_type}' has no message — ignoring.")
                return

            if message.get("key", {}).get("fromMe", False):
                logger.info("Ignoring outbound message sent by the bot itself.")
                return

            msg_type = None
            for candidate in SUPPORTED_MSG_TYPES:
                if candidate in message:
                    msg_type = candidate
                    break

            if msg_type is None:
                logger.info("Unsupported message type in payload — ignoring.")
                return

            remote_jid = message.get("key", {}).get("remoteJid", "")
            phone = remote_jid.replace("@s.whatsapp.net", "").replace("@g.us", "")

            media_info = message.get(msg_type, {})
            mime_type = media_info.get("mimetype", "")
            message_id = message.get("key", {}).get("id", "")
            filename = media_info.get("fileName", f"whatsapp_{msg_type}")

        # ── 2. Validate MIME type ──────────────────────────────────────
        mime_base = mime_type.split(";")[0].strip().lower()

        if mime_base not in SUPPORTED_MIMES:
            logger.info(f"Unsupported MIME '{mime_base}' from {phone} — ignoring.")
            return

        logger.info(
            f"Supported media detected | type={msg_type} mime={mime_base} phone={phone}"
        )

        # ── 3. Download media (if WUZAPI) ────────────────────────────────
        if not is_evolution:
            logger.info(f"Downloading media | message_id={message_id}")
            try:
                media_info_full = await self.wuzapi.get_media_info(message_id)
                media_url = (
                    media_info_full.get("data", {}).get("url")
                    or media_info_full.get("url")
                    or ""
                )
                if not media_url:
                    raise WuzapiError("WUZAPI returned no media URL.")

                media_bytes = await self.wuzapi.download_media(media_url)
            except WuzapiError as exc:
                logger.exception(f"Media download failed: {exc}")
                await self._safe_send(
                    phone,
                    "❌ Unable to download the received file.",
                )
                return

        logger.info(f"Media ready | {len(media_bytes)} bytes")

        # ── 7. Extract ─────────────────────────────────────────────────
        logger.info("Starting extraction")
        try:
            service = ExtractionService(db=self.db, ai_provider=self.ai_provider)
            _, data_result = await service.process(
                application_id=self.application_id,
                image_bytes=media_bytes,
                image_filename=filename,
            )
        except Exception as exc:
            logger.exception(f"Extraction failed: {exc}")
            await self._safe_send(
                phone,
                "❌ I couldn't process this document.\nPlease send a clear image or PDF.",
            )
            return

        logger.info("Extraction completed")

        # ── 8. Format & send ───────────────────────────────────────────
        try:
            text = self.formatter.format(data_result)
        except Exception:
            logger.exception("ResponseFormatter raised an unexpected error.")
            text = (
                "⚠️ Unsupported document.\n\n"
                "Currently supported:\n"
                "• PIX Receipt\n"
                "• Commercial Document\n"
                "• Electronic Invoice (NF-e)"
            )

        logger.info(f"Sending WhatsApp response to {phone}")
        await self._safe_send(phone, text)
        logger.info(f"Message sent to {phone}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _safe_send(self, phone: str, text: str) -> None:
        """Send a message, swallowing errors so the webhook never crashes."""
        try:
            await self.wuzapi.send_text_message(phone, text)
        except WuzapiError as exc:
            logger.exception(f"Failed to send WhatsApp reply to {phone}: {exc}")
