from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from transcription.core.config import get_settings
from transcription.core.logging import get_logger

logger = get_logger(__name__)


def verify_internal_transcription_token(
    authorization: str | None = Header(default=None),
) -> None:
    settings = get_settings()
    expected = settings.bot_to_transcription_token
    if expected is None or not expected.strip():
        logger.critical("Internal transcription token is not configured.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal authentication is not configured.",
        )
    supplied = ""
    if authorization:
        scheme, separator, credentials = authorization.partition(" ")
        if separator and scheme.lower() == "bearer" and credentials:
            supplied = credentials
    if not secrets.compare_digest(supplied, expected):
        logger.warning("Internal transcription authentication failed.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal credentials.",
        )
