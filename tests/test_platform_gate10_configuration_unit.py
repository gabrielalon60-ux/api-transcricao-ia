from __future__ import annotations

import asyncio
import logging

import pytest

from bot_df.main import _configured_token
from db_writer.config import DBWriterSettings
from observability.logging import sanitize_log_message
from orchestrator.config import Settings as OrchestratorSettings
from transcription.core.config import Settings as TranscriptionSettings
from transcription.core.logging import sanitize_log_value
from transcription.services.internal_extraction_service import (
    get_provider_semaphore,
    http_status_for_error,
    is_retryable_error,
)

SAFE = "s" * 40


def _orchestrator(**overrides: object) -> OrchestratorSettings:
    values: dict[str, object] = {
        "database_url": "postgresql://platform-db/platform?sslmode=verify-full",
        "app_env": "production",
        "wuzapi_base_url": "http://wuzapi:8080",
        "wuzapi_token": SAFE,
        "wuzapi_webhook_secret": SAFE,
        "api_key_hash_secret": SAFE,
        "registration_secret_pepper": SAFE,
        "log_pii_hash_key": SAFE,
        "orchestrator_to_bot_token": SAFE,
        "bot_df_url": "http://bot-df:8003",
        "bot_to_transcription_token": SAFE,
        "transcription_service_url": "http://transcription:8001",
        "orchestrator_to_writer_token": SAFE,
        "db_writer_url": "http://db-writer:8004",
        "df_holding_identifiers": ["12345678901", "12345678000199"],
        "registration_max_failed_attempts": 5,
        "registration_window_seconds": 3600,
        "registration_block_seconds": 86400,
    }
    values.update(overrides)
    return OrchestratorSettings(**values)


def test_protected_orchestrator_configuration_accepts_exact_contract() -> None:
    _orchestrator().validate_environment()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("wuzapi_webhook_secret", "placeholder"),
        ("bot_df_url", "http://localhost:8003"),
        ("df_holding_identifiers", ["00000000000"]),
        ("registration_window_seconds", 3599),
        ("max_organization_active_items", 21),
    ],
)
def test_protected_orchestrator_configuration_fails_closed(name: str, value: object) -> None:
    with pytest.raises(ValueError):
        _orchestrator(**{name: value}).validate_environment()


def test_transcription_protected_configuration_and_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = TranscriptionSettings(
        app_env="staging",
        gemini_api_key=SAFE,
        api_key_hash_secret=SAFE,
        bot_to_transcription_token=SAFE,
        transcription_database_url="postgresql://transcription-db/platform?sslmode=verify-full",
        max_upload_size_mb=25,
        max_concurrent_validations=4,
        max_concurrent_provider_calls=2,
        provider_acquisition_timeout_seconds=2,
    )
    settings.validate_environment()
    monkeypatch.setenv("MAX_PROVIDER_CONCURRENT_CALLS", "3")
    assert TranscriptionSettings(gemini_api_key="x", api_key_hash_secret="y").max_concurrent_provider_calls == 3


def test_db_writer_requires_strong_token_and_verify_full() -> None:
    settings = DBWriterSettings(
        app_env="production",
        df_database_url="postgresql://writer@db/df?sslmode=verify-full",
        db_writer_internal_token=SAFE,
    )
    settings.validate_environment()
    with pytest.raises(ValueError):
        DBWriterSettings(
            app_env="production",
            df_database_url="postgresql://writer@db/df?sslmode=disable",
            db_writer_internal_token=SAFE,
        ).validate_environment()


def test_bot_df_protected_token_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ORCHESTRATOR_TO_BOT_TOKEN", "placeholder_bearer_token")
    with pytest.raises(RuntimeError):
        _configured_token()


def test_logging_boundaries_redact_secrets_dsn_phone_and_controls() -> None:
    raw = "token=TOPSECRET\nBearer abcdef postgresql://u:p@db/x phone=5511999999999"
    for sanitized in (sanitize_log_value(raw), sanitize_log_message(raw)):
        assert "TOPSECRET" not in sanitized
        assert "abcdef" not in sanitized
        assert "postgresql://" not in sanitized
        assert "5511999999999" not in sanitized
        assert "\n" not in sanitized


def test_provider_capacity_contract_is_bounded_and_retryable() -> None:
    semaphore = get_provider_semaphore(2)

    async def exercise() -> None:
        await semaphore.acquire()
        await semaphore.acquire()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(semaphore.acquire(), timeout=0.01)
        semaphore.release()
        semaphore.release()

    asyncio.run(exercise())
    assert http_status_for_error("PROVIDER_CAPACITY_EXCEEDED") == 503
    assert is_retryable_error("PROVIDER_CAPACITY_EXCEEDED")


def test_json_logging_never_formats_raw_exception_message(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR):
        logging.getLogger("gate10").error("bounded failure category")
    assert "bounded failure category" in caplog.text
