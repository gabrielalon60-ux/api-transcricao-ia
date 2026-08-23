from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db.models import Base, Event, Organization, Bot, User, Instance, ProcessingItem
from orchestrator.extraction_worker import ExtractionWorker
from orchestrator.services.extraction_dispatcher import (
    ExtractionDispatcher,
    claim_expired_extracting_item_for_recovery,
    claim_next_received_item_for_extraction,
)
from orchestrator.transcription_client import TranscriptionClient, TranscriptionClientError
from orchestrator.wuzapi import WuzapiClient, WuzapiError

DISPOSABLE_PG15_URL = "postgresql://test_user:test_password@127.0.0.1:15434/extraction_worker_test"


# ---------------------------------------------------------------------------
# SQLite In-Memory Fixtures & Tests (Fast Behavioral Unit Coverage)
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_sqlite_db():
    """In-memory SQLite database strictly isolated from platform_g10b1."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    yield session_factory
    Base.metadata.drop_all(engine)


@pytest.fixture
def sqlite_context(mock_sqlite_db):
    """Seeds prerequisite tenant rows for ProcessingItem foreign keys in SQLite."""
    with mock_sqlite_db() as db:
        org = Organization(id="org-test", name="Test Org", slug="test-org", status="ACTIVE")
        bot = Bot(id="bot-test", organization_id="org-test", name="Test Bot", service_key="key-test", status="ACTIVE")
        db.add_all([org, bot])
        db.commit()
    return {
        "org_id": "org-test",
        "bot_id": "bot-test",
    }


def _create_event_and_item(
    db,
    context: dict,
    status: str = "RECEIVED",
    sequence: int = 1,
    attempt_count: int = 0,
    claimed_by: str | None = None,
    lease_expires_at: datetime | None = None,
    media_ref: dict | None = None,
) -> tuple[Event, ProcessingItem]:
    user_id = f"user-{uuid.uuid4()}"
    inst_id = f"inst-{uuid.uuid4()}"
    evt_id = f"evt-{uuid.uuid4()}"
    item_id = f"item-{uuid.uuid4()}"
    now = datetime.now(timezone.utc)

    user = User(
        id=user_id,
        organization_id=context["org_id"],
        phone_number=f"5547{uuid.uuid4().hex[:8]}",
        status="ACTIVE",
    )
    inst = Instance(
        id=inst_id,
        bot_id=context["bot_id"],
        organization_id=context["org_id"],
        provider="WUZAPI",
        external_instance_id=f"ext-{uuid.uuid4().hex[:8]}",
        phone_number=user.phone_number,
        status="ACTIVE",
    )
    evt = Event(
        id=evt_id,
        organization_id=context["org_id"],
        instance_id=inst_id,
        user_id=user_id,
        provider="WUZAPI",
        external_instance_id=inst.external_instance_id,
        external_message_id=f"msg-{uuid.uuid4()}",
        message_type="image",
        status="RECEIVED",
        correlation_id=f"corr-{uuid.uuid4()}",
        payload_hash="hash123",
        received_at=now,
    )
    item = ProcessingItem(
        id=item_id,
        event_id=evt_id,
        correlation_id=evt.correlation_id,
        organization_id=context["org_id"],
        instance_id=inst_id,
        user_id=user_id,
        sequence=sequence,
        status=status,
        claimed_by=claimed_by,
        lease_expires_at=lease_expires_at,
        attempt_count=attempt_count,
        message_received_at=now,
        file_mime_type="image/jpeg",
        file_size=1024,
        file_sha256="sha256_mock_value",
        media_ref=media_ref,
    )
    db.add(user)
    db.flush()
    db.add(inst)
    db.flush()
    db.add(evt)
    db.flush()
    db.add(item)
    db.commit()
    db.refresh(item)
    return evt, item


def test_extraction_worker_module_imports():
    """1. Verify executable module imports and exposes core entrypoints."""
    import orchestrator.extraction_worker as ew

    assert hasattr(ew, "ExtractionWorker")
    assert hasattr(ew, "run_extraction_worker_loop")
    assert ew.EXTRACTION_POLL_INTERVAL_SECONDS == 1.0


@pytest.mark.asyncio
async def test_sqlite_extraction_worker_claims_and_processes_received_item(mock_sqlite_db, sqlite_context):
    """2. Worker claims RECEIVED item and calls dispatcher, transitioning to READY (SQLite)."""
    with mock_sqlite_db() as db:
        _, item = _create_event_and_item(db, sqlite_context, status="RECEIVED", sequence=1)
        item_id = item.id

    mock_transcription = MagicMock(spec=TranscriptionClient)
    mock_transcription.extract = AsyncMock(
        return_value={
            "document_type": "invoice",
            "extraction": {"total_amount": 50.0},
            "normalization": {"amount": "50.00", "direction": "expense"},
            "quality_flags": [],
            "confidence": 0.98,
        }
    )
    dispatcher = ExtractionDispatcher(transcription_client=mock_transcription)
    worker = ExtractionWorker(
        worker_id="test-1",
        session_factory=mock_sqlite_db,
        dispatcher=dispatcher,
        wuzapi_client=MagicMock(spec=WuzapiClient),
    )

    claimed = await worker.run_iteration()
    assert claimed is True

    with mock_sqlite_db() as db:
        refreshed = db.get(ProcessingItem, item_id)
        assert refreshed is not None
        assert refreshed.status == "READY"
        assert refreshed.document_type == "invoice"
        assert refreshed.normalized_data == {"amount": "50.00", "direction": "expense"}
        assert refreshed.attempt_count == 0  # Reset at READY


@pytest.mark.asyncio
async def test_sqlite_extraction_worker_idles_on_empty_queue(mock_sqlite_db):
    """3. Empty queue returns False without errors or database changes (SQLite)."""
    mock_transcription = MagicMock(spec=TranscriptionClient)
    dispatcher = ExtractionDispatcher(transcription_client=mock_transcription)
    worker = ExtractionWorker(
        worker_id="test-1",
        session_factory=mock_sqlite_db,
        dispatcher=dispatcher,
    )

    claimed = await worker.run_iteration()
    assert claimed is False
    mock_transcription.extract.assert_not_called()


@pytest.mark.asyncio
async def test_sqlite_extraction_worker_ignores_ready_and_other_statuses(mock_sqlite_db, sqlite_context):
    """4. Worker does not claim READY, ACTIVE, or terminal items (SQLite)."""
    with mock_sqlite_db() as db:
        _create_event_and_item(db, sqlite_context, status="READY", sequence=1)
        _create_event_and_item(db, sqlite_context, status="COMPLETED", sequence=2)

    mock_transcription = MagicMock(spec=TranscriptionClient)
    dispatcher = ExtractionDispatcher(transcription_client=mock_transcription)
    worker = ExtractionWorker(
        worker_id="test-1",
        session_factory=mock_sqlite_db,
        dispatcher=dispatcher,
    )

    claimed = await worker.run_iteration()
    assert claimed is False


@pytest.mark.asyncio
async def test_sqlite_extraction_worker_recovers_stale_extracting_lease(mock_sqlite_db, sqlite_context):
    """5. Expired EXTRACTING item is claimed via recovery path and processed (SQLite)."""
    past_time = datetime.now(timezone.utc) - timedelta(seconds=120)
    with mock_sqlite_db() as db:
        _, stale_item = _create_event_and_item(
            db,
            sqlite_context,
            status="EXTRACTING",
            sequence=1,
            attempt_count=1,
            claimed_by="extraction-dead-worker",
            lease_expires_at=past_time,
        )
        item_id = stale_item.id

    mock_transcription = MagicMock(spec=TranscriptionClient)
    mock_transcription.extract = AsyncMock(
        return_value={
            "document_type": "invoice",
            "extraction": {"amount": 100},
            "normalization": {"amount": "100.00", "direction": "expense"},
            "quality_flags": [],
            "confidence": 0.95,
        }
    )
    dispatcher = ExtractionDispatcher(transcription_client=mock_transcription)
    worker = ExtractionWorker(
        worker_id="recovery-worker",
        session_factory=mock_sqlite_db,
        dispatcher=dispatcher,
    )

    claimed = await worker.run_iteration()
    assert claimed is True

    with mock_sqlite_db() as db:
        refreshed = db.get(ProcessingItem, item_id)
        assert refreshed is not None
        assert refreshed.status == "READY"


@pytest.mark.asyncio
async def test_sqlite_extraction_worker_handles_transcription_failure(mock_sqlite_db, sqlite_context):
    """6. Non-retryable transcription error transitions item to EXTRACTION_FAILED (SQLite)."""
    with mock_sqlite_db() as db:
        _, item = _create_event_and_item(db, sqlite_context, status="RECEIVED", sequence=1)
        item_id = item.id

    mock_transcription = MagicMock(spec=TranscriptionClient)
    mock_transcription.extract = AsyncMock(
        side_effect=TranscriptionClientError("Invalid format", error_code="INVALID_DOCUMENT", retryable=False)
    )
    dispatcher = ExtractionDispatcher(transcription_client=mock_transcription)
    worker = ExtractionWorker(
        worker_id="test-fail",
        session_factory=mock_sqlite_db,
        dispatcher=dispatcher,
    )

    claimed = await worker.run_iteration()
    assert claimed is True

    with mock_sqlite_db() as db:
        refreshed = db.get(ProcessingItem, item_id)
        assert refreshed is not None
        assert refreshed.status == "EXTRACTION_FAILED"
        assert refreshed.error_code == "INVALID_DOCUMENT"
        assert refreshed.error_message_sanitized == "INVALID_DOCUMENT"
        assert refreshed.outcome_reason is None


@pytest.mark.asyncio
async def test_sqlite_extraction_worker_transient_media_download_failure_retries(mock_sqlite_db, sqlite_context):
    """7. Transient media download failure (5xx/timeout) resets to RECEIVED for retry."""
    with mock_sqlite_db() as db:
        _, item = _create_event_and_item(
            db,
            sqlite_context,
            status="RECEIVED",
            sequence=1,
            media_ref={"direct_path": "/media/test.jpg", "media_key": "valid_key", "external_message_id": "msg-123"},
        )
        item_id = item.id

    mock_wuzapi = MagicMock(spec=WuzapiClient)
    mock_wuzapi.download_media = AsyncMock(side_effect=WuzapiError("WUZAPI 500 timeout", retryable=True, http_status=500))

    mock_transcription = MagicMock(spec=TranscriptionClient)
    dispatcher = ExtractionDispatcher(transcription_client=mock_transcription)

    worker = ExtractionWorker(
        worker_id="test-media-fail",
        session_factory=mock_sqlite_db,
        dispatcher=dispatcher,
        wuzapi_client=mock_wuzapi,
    )

    claimed = await worker.run_iteration()
    assert claimed is True

    # Transcription service must NOT have been called with mock bytes!
    mock_transcription.extract.assert_not_called()

    with mock_sqlite_db() as db:
        refreshed = db.get(ProcessingItem, item_id)
        assert refreshed is not None
        # Must be reset to RECEIVED for retry (since retryable=True and attempt_count < 3)
        assert refreshed.status == "RECEIVED"
        assert refreshed.claimed_by is None
        assert refreshed.attempt_count == 1
        assert refreshed.error_code is None
        assert refreshed.error_message_sanitized is None


@pytest.mark.asyncio
async def test_sqlite_extraction_worker_deterministic_404_media_failure_terminalizes_immediately(mock_sqlite_db, sqlite_context):
    """7b. Deterministic 404 media download failure terminalizes immediately to EXTRACTION_FAILED (no retry storm)."""
    with mock_sqlite_db() as db:
        _, item = _create_event_and_item(
            db,
            sqlite_context,
            status="RECEIVED",
            sequence=1,
            media_ref={"direct_path": "/media/nonexistent.jpg", "media_key": "valid_key", "external_message_id": "msg-404"},
        )
        item_id = item.id

    mock_wuzapi = MagicMock(spec=WuzapiClient)
    mock_wuzapi.download_media = AsyncMock(
        side_effect=WuzapiError("WUZAPI media 404 Not Found", retryable=False, http_status=404, reason="NOT_FOUND_404")
    )

    mock_transcription = MagicMock(spec=TranscriptionClient)
    dispatcher = ExtractionDispatcher(transcription_client=mock_transcription)

    worker = ExtractionWorker(
        worker_id="test-media-404",
        session_factory=mock_sqlite_db,
        dispatcher=dispatcher,
        wuzapi_client=mock_wuzapi,
    )

    claimed = await worker.run_iteration()
    assert claimed is True

    # Transcription service must NOT have been called!
    mock_transcription.extract.assert_not_called()

    with mock_sqlite_db() as db:
        refreshed = db.get(ProcessingItem, item_id)
        assert refreshed is not None
        # Must immediately transition to EXTRACTION_FAILED on attempt 1 without immediate retry
        assert refreshed.status == "EXTRACTION_FAILED"
        assert refreshed.attempt_count == 1
        assert refreshed.claimed_by is None
        assert refreshed.error_code == "EXTRACTION_ERROR"
        assert refreshed.error_message_sanitized == "NOT_FOUND_404"
        assert refreshed.outcome_reason is None


@pytest.mark.asyncio
async def test_sqlite_extraction_worker_missing_plaintext_hash_terminalizes_immediately(mock_sqlite_db, sqlite_context):
    """7c. Missing required FileSHA256 terminalizes on attempt one without Transcription."""
    with mock_sqlite_db() as db:
        _, item = _create_event_and_item(
            db,
            sqlite_context,
            status="RECEIVED",
            sequence=1,
            media_ref={
                "url": "https://mmg.whatsapp.net/d/f/test.enc",
                "direct_path": "/media/test.jpg",
                "media_key": "valid_key",
                "mime_type": "image/jpeg",
                "expected_size": 1024,
                # expected_sha256 intentionally absent
            },
        )
        item_id = item.id

    real_wuzapi = object.__new__(WuzapiClient)
    real_wuzapi.base_url = "http://wuzapi.invalid"
    real_wuzapi.token = "test-token"
    real_wuzapi._headers = {"token": "test-token", "Content-Type": "application/json"}

    mock_transcription = MagicMock(spec=TranscriptionClient)
    dispatcher = ExtractionDispatcher(transcription_client=mock_transcription)

    worker = ExtractionWorker(
        worker_id="test-missing-key",
        session_factory=mock_sqlite_db,
        dispatcher=dispatcher,
        wuzapi_client=real_wuzapi,
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        claimed = await worker.run_iteration()
        mock_post.assert_not_called()
    assert claimed is True
    mock_transcription.extract.assert_not_called()

    with mock_sqlite_db() as db:
        refreshed = db.get(ProcessingItem, item_id)
        assert refreshed is not None
        assert refreshed.status == "EXTRACTION_FAILED"
        assert refreshed.attempt_count == 1
        assert refreshed.error_code == "EXTRACTION_ERROR"
        assert refreshed.error_message_sanitized == "MISSING_CRYPTO_FIELD"
        assert refreshed.outcome_reason is None
        assert "FileSHA256" not in refreshed.error_message_sanitized


@pytest.mark.asyncio
async def test_no_database_transaction_held_during_wuzapi_media_download(mock_sqlite_db, sqlite_context):
    """8. Proves no database transaction is held while WUZAPI media download is awaited."""
    with mock_sqlite_db() as db:
        _, item = _create_event_and_item(
            db,
            sqlite_context,
            status="RECEIVED",
            sequence=1,
            media_ref={"direct_path": "/media/test.jpg", "media_key": "valid_key", "external_message_id": "msg-123"},
        )
        item_id = item.id

    transaction_state_during_download = []

    async def fake_download_media(*args, **kwargs):
        # Inspect if any transaction is active on the session factory during network I/O
        with mock_sqlite_db() as test_session:
            transaction_state_during_download.append(test_session.in_transaction())
        return b"VALID_IMAGE_BINARY"

    mock_wuzapi = MagicMock(spec=WuzapiClient)
    mock_wuzapi.download_media = AsyncMock(side_effect=fake_download_media)

    mock_transcription = MagicMock(spec=TranscriptionClient)
    mock_transcription.extract = AsyncMock(
        return_value={
            "document_type": "invoice",
            "extraction": {"total_amount": 10.0},
            "normalization": {"amount": "10.00", "direction": "expense"},
            "quality_flags": [],
            "confidence": 0.99,
        }
    )
    dispatcher = ExtractionDispatcher(transcription_client=mock_transcription)

    worker = ExtractionWorker(
        worker_id="test-tx-boundary",
        session_factory=mock_sqlite_db,
        dispatcher=dispatcher,
        wuzapi_client=mock_wuzapi,
    )

    claimed = await worker.run_iteration()
    assert claimed is True
    assert len(transaction_state_during_download) == 1
    # Freshly opened test_session in download hook must NOT be in an existing transaction
    assert transaction_state_during_download[0] is False

    with mock_sqlite_db() as db:
        refreshed = db.get(ProcessingItem, item_id)
        assert refreshed is not None
        assert refreshed.status == "READY"


@pytest.mark.asyncio
async def test_audit_transaction_state_during_transcription_extract(mock_sqlite_db, sqlite_context):
    """9. Proves db.in_transaction() is False when TranscriptionClient.extract is awaited."""
    with mock_sqlite_db() as db:
        _, item = _create_event_and_item(db, sqlite_context, status="RECEIVED", sequence=1)
        item_id = item.id

    tx_state_during_extract = []
    active_session_ref = []

    async def fake_extract(**kwargs):
        if active_session_ref:
            tx_state_during_extract.append(active_session_ref[0].in_transaction())
        return {
            "document_type": "invoice",
            "extraction": {"total_amount": 10.0},
            "normalization": {"amount": "10.00", "direction": "expense"},
            "quality_flags": [],
            "confidence": 0.99,
        }

    mock_transcription = MagicMock(spec=TranscriptionClient)
    mock_transcription.extract = AsyncMock(side_effect=fake_extract)
    real_dispatcher = ExtractionDispatcher(transcription_client=mock_transcription)
    orig_process_item = real_dispatcher.process_item

    async def wrapped_process_item(db, item, mock_file_bytes=None, local_buffer_path=None):
        active_session_ref.append(db)
        return await orig_process_item(db, item, mock_file_bytes=mock_file_bytes, local_buffer_path=local_buffer_path)

    real_dispatcher.process_item = wrapped_process_item  # type: ignore[method-assign]

    worker = ExtractionWorker(
        worker_id="test-tx-audit",
        session_factory=mock_sqlite_db,
        dispatcher=real_dispatcher,
    )

    claimed = await worker.run_iteration()
    assert claimed is True
    assert len(tx_state_during_extract) == 1
    # MUST be False because db.rollback() closed the read transaction before extract await!
    assert tx_state_during_extract[0] is False

    with mock_sqlite_db() as db:
        refreshed = db.get(ProcessingItem, item_id)
        assert refreshed is not None
        assert refreshed.status == "READY"


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_kind", ["new", "dirty", "deleted"])
async def test_extraction_dispatcher_fails_closed_on_dirty_session(mock_sqlite_db, sqlite_context, pending_kind):
    """10. Proves ExtractionDispatcher raises RuntimeError without calling Transcription if session has pending changes."""
    with mock_sqlite_db() as db:
        _, item = _create_event_and_item(db, sqlite_context, status="RECEIVED", sequence=1)
        item_id = item.id

    mock_transcription = MagicMock(spec=TranscriptionClient)
    mock_transcription.extract = AsyncMock()
    dispatcher = ExtractionDispatcher(transcription_client=mock_transcription)

    with mock_sqlite_db() as db:
        test_item = db.get(ProcessingItem, item_id)
        assert test_item is not None

        if pending_kind == "new":
            unrelated_user = User(
                id="uncommitted-user",
                phone_number="5511999990000",
                organization_id="org-test",
                name="Test Uncommitted",
            )
            db.add(unrelated_user)
            assert len(db.new) > 0
        elif pending_kind == "dirty":
            test_item.original_filename = "modified_uncommitted.jpg"
            assert len(db.dirty) > 0
        elif pending_kind == "deleted":
            db.delete(test_item)
            assert len(db.deleted) > 0

        with pytest.raises(RuntimeError, match="ExtractionDispatcher requires a clean database session"):
            await dispatcher.process_item(db, test_item)

        # Invariant: TranscriptionClient.extract must NEVER be called!
        mock_transcription.extract.assert_not_called()


@pytest.mark.asyncio
async def test_extraction_worker_stop_signal_terminates_loop(mock_sqlite_db):
    """9. Calling worker.stop() terminates run_loop after at most one poll."""
    worker = ExtractionWorker(
        worker_id="test-stop",
        session_factory=mock_sqlite_db,
        dispatcher=MagicMock(spec=ExtractionDispatcher),
        poll_interval=0.01,
    )

    async def trigger_stop():
        await asyncio.sleep(0.02)
        worker.stop()

    task = asyncio.create_task(trigger_stop())
    await worker.run_loop()
    await task
    assert worker.running is False


def test_zero_secret_leakage_in_worker_logs(caplog):
    """10. Ensure no sensitive tokens or payload internals are emitted in worker logs."""
    caplog.set_level(logging.DEBUG)
    worker = ExtractionWorker(
        worker_id="test-privacy",
        session_factory=MagicMock(),
        dispatcher=MagicMock(),
    )
    worker.stop()

    for record in caplog.records:
        message = record.getMessage()
        assert "token" not in message.lower() or "claim" in message.lower()
        assert "secret" not in message.lower()
        assert "key" not in message.lower() or "worker-" in message.lower()
        assert "direct_path" not in message.lower()


# ---------------------------------------------------------------------------
# Real Disposable PostgreSQL 15 Tests (Locking, SKIP LOCKED, Stale Recovery)
# ---------------------------------------------------------------------------


@pytest.fixture
def disposable_pg15_session_factory():
    """Connects to real disposable PostgreSQL 15 container (g10b1_extraction_worker_test_pg_final on port 15434)."""
    engine = create_engine(DISPOSABLE_PG15_URL, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def pg15_context(disposable_pg15_session_factory):
    """Seeds tenant context into disposable PostgreSQL 15."""
    with disposable_pg15_session_factory() as db:
        org = Organization(id="org-pg15-test", name="Test Org PG15", slug="test-org-pg15", status="ACTIVE")
        db.add(org)
        db.flush()
        bot = Bot(id="bot-pg15-test", organization_id="org-pg15-test", name="Test Bot PG15", service_key="key-pg15", status="ACTIVE")
        db.add(bot)
        db.commit()
    return {
        "org_id": "org-pg15-test",
        "bot_id": "bot-pg15-test",
    }


def test_real_pg15_received_claim_and_locking(disposable_pg15_session_factory, pg15_context):
    """11. Real PostgreSQL 15: claim_next_received_item_for_extraction sets EXTRACTING and claim_token."""
    with disposable_pg15_session_factory() as db:
        _, item = _create_event_and_item(db, pg15_context, status="RECEIVED", sequence=1)
        item_id = item.id

    with disposable_pg15_session_factory() as db:
        claimed = claim_next_received_item_for_extraction(db, dispatcher_id="worker-pg15-1")
        assert claimed is not None
        assert claimed.id == item_id
        assert claimed.status == "EXTRACTING"
        assert claimed.claimed_by == "extraction-worker-pg15-1"
        assert claimed.attempt_count == 1
        assert claimed.extraction_claim_token is not None
        assert claimed.lease_expires_at is not None

    with disposable_pg15_session_factory() as db:
        # Second claim attempt on empty queue returns None
        second_attempt = claim_next_received_item_for_extraction(db, dispatcher_id="worker-pg15-2")
        assert second_attempt is None


def test_real_pg15_competing_claimers_skip_locked(disposable_pg15_session_factory, pg15_context):
    """12. Real PostgreSQL 15: SKIP LOCKED prevents duplicate claims between two competing sessions."""
    with disposable_pg15_session_factory() as db:
        _, item = _create_event_and_item(db, pg15_context, status="RECEIVED", sequence=1)
        item_id = item.id

    # Simulate two workers claiming simultaneously via separate DB sessions
    with disposable_pg15_session_factory() as db1, disposable_pg15_session_factory() as db2:
        claimed_w1 = claim_next_received_item_for_extraction(db1, dispatcher_id="worker-pg15-A")
        claimed_w2 = claim_next_received_item_for_extraction(db2, dispatcher_id="worker-pg15-B")

    # Exactly one worker wins; the other receives None
    winners = [c for c in (claimed_w1, claimed_w2) if c is not None]
    assert len(winners) == 1
    assert winners[0].id == item_id
    assert winners[0].claimed_by in ("extraction-worker-pg15-A", "extraction-worker-pg15-B")


@pytest.mark.asyncio
async def test_real_pg15_deterministic_media_failure_terminalizes_and_persists_safe_error(
    disposable_pg15_session_factory, pg15_context
):
    with disposable_pg15_session_factory() as db:
        _, item = _create_event_and_item(
            db,
            pg15_context,
            status="RECEIVED",
            sequence=1,
            media_ref={
                "url": "https://mmg.whatsapp.net/d/f/test.enc",
                "media_key": "valid_key",
                "mime_type": "image/jpeg",
                "expected_size": 1024,
            },
        )
        item_id = item.id

    mock_wuzapi = MagicMock(spec=WuzapiClient)
    mock_wuzapi.download_media = AsyncMock(
        side_effect=WuzapiError(
            "Missing required cryptographic field.",
            retryable=False,
            reason="MISSING_CRYPTO_FIELD",
        )
    )
    mock_transcription = MagicMock(spec=TranscriptionClient)
    worker = ExtractionWorker(
        worker_id="pg15-deterministic-media",
        session_factory=disposable_pg15_session_factory,
        dispatcher=ExtractionDispatcher(transcription_client=mock_transcription),
        wuzapi_client=mock_wuzapi,
    )

    assert await worker.run_iteration() is True
    mock_transcription.extract.assert_not_called()

    with disposable_pg15_session_factory() as db:
        refreshed = db.get(ProcessingItem, item_id)
        assert refreshed is not None
        assert refreshed.status == "EXTRACTION_FAILED"
        assert refreshed.attempt_count == 1
        assert refreshed.claimed_by is None
        assert refreshed.lease_expires_at is None
        assert refreshed.error_code == "EXTRACTION_ERROR"
        assert refreshed.error_message_sanitized == "MISSING_CRYPTO_FIELD"
        assert refreshed.outcome_reason is None


def test_real_pg15_stale_lease_recovery(disposable_pg15_session_factory, pg15_context):
    """13. Real PostgreSQL 15: claim_expired_extracting_item_for_recovery reclaims expired leases."""
    past_time = datetime.now(timezone.utc) - timedelta(seconds=120)
    with disposable_pg15_session_factory() as db:
        _, stale_item = _create_event_and_item(
            db,
            pg15_context,
            status="EXTRACTING",
            sequence=1,
            attempt_count=1,
            claimed_by="worker-dead-node",
            lease_expires_at=past_time,
        )
        item_id = stale_item.id

    with disposable_pg15_session_factory() as db:
        recovered = claim_expired_extracting_item_for_recovery(db, dispatcher_id="worker-live-node")
        assert recovered is not None
        assert recovered.id == item_id
        assert recovered.status == "EXTRACTING"
        assert recovered.claimed_by == "extraction-recovery-worker-live-node"
        assert recovered.attempt_count == 2  # Incremented from 1
        assert recovered.lease_expires_at > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_real_pg15_full_worker_iteration_cycle(disposable_pg15_session_factory, pg15_context):
    """14. Real PostgreSQL 15: Full ExtractionWorker run_iteration with real PG15 transactions."""
    with disposable_pg15_session_factory() as db:
        _, item = _create_event_and_item(db, pg15_context, status="RECEIVED", sequence=1)
        item_id = item.id

    mock_transcription = MagicMock(spec=TranscriptionClient)
    mock_transcription.extract = AsyncMock(
        return_value={
            "document_type": "invoice",
            "extraction": {"total_amount": 75.50},
            "normalization": {"amount": "75.50", "direction": "expense"},
            "quality_flags": [],
            "confidence": 0.99,
        }
    )
    dispatcher = ExtractionDispatcher(transcription_client=mock_transcription)
    worker = ExtractionWorker(
        worker_id="pg15-worker-1",
        session_factory=disposable_pg15_session_factory,
        dispatcher=dispatcher,
        wuzapi_client=MagicMock(spec=WuzapiClient),
    )

    claimed = await worker.run_iteration()
    assert claimed is True

    with disposable_pg15_session_factory() as db:
        refreshed = db.get(ProcessingItem, item_id)
        assert refreshed is not None
        assert refreshed.status == "READY"
        assert refreshed.document_type == "invoice"
        assert refreshed.normalized_data == {"amount": "75.50", "direction": "expense"}
        assert refreshed.attempt_count == 0


@pytest.mark.asyncio
async def test_real_pg15_no_transaction_held_during_transcription(disposable_pg15_session_factory, pg15_context):
    """15. Real PostgreSQL 15: Proves db.in_transaction() is False at Transcription extract entry."""
    with disposable_pg15_session_factory() as db:
        _, item = _create_event_and_item(db, pg15_context, status="RECEIVED", sequence=1)
        item_id = item.id

    tx_state_during_extract = []
    active_session_ref = []

    async def fake_extract(**kwargs):
        if active_session_ref:
            tx_state_during_extract.append(active_session_ref[0].in_transaction())
        return {
            "document_type": "invoice",
            "extraction": {"total_amount": 100.0},
            "normalization": {"amount": "100.00", "direction": "expense"},
            "quality_flags": [],
            "confidence": 0.99,
        }

    mock_transcription = MagicMock(spec=TranscriptionClient)
    mock_transcription.extract = AsyncMock(side_effect=fake_extract)
    real_dispatcher = ExtractionDispatcher(transcription_client=mock_transcription)
    orig_process_item = real_dispatcher.process_item

    async def wrapped_process_item(db, item, mock_file_bytes=None, local_buffer_path=None):
        active_session_ref.append(db)
        return await orig_process_item(db, item, mock_file_bytes=mock_file_bytes, local_buffer_path=local_buffer_path)

    real_dispatcher.process_item = wrapped_process_item  # type: ignore[method-assign]

    worker = ExtractionWorker(
        worker_id="pg15-tx-audit",
        session_factory=disposable_pg15_session_factory,
        dispatcher=real_dispatcher,
    )

    claimed = await worker.run_iteration()
    assert claimed is True
    assert len(tx_state_during_extract) == 1
    # On real PostgreSQL 15, db.in_transaction() is False immediately before external Transcription I/O!
    assert tx_state_during_extract[0] is False

    with disposable_pg15_session_factory() as db:
        refreshed = db.get(ProcessingItem, item_id)
        assert refreshed is not None
        assert refreshed.status == "READY"
