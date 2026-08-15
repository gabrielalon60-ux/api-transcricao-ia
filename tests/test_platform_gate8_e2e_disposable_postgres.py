from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from db.models import (
    Event,
    Execution,
    ProcessingItem,
    UserAnswer,
    UserInteraction,
)
from db_writer import main as writer_main
from orchestrator import fifo_worker, main as orchestrator_main
from orchestrator.config import get_settings
from orchestrator.fifo_worker import WorkerClaimTracker
from orchestrator.services.business_rules_evaluator import BusinessRulesEvaluatorService
from orchestrator.services.extraction_dispatcher import (
    ExtractionDispatcher,
    claim_next_received_item_for_extraction,
)
from orchestrator.services.fifo_worker_service import (
    claim_next_ready_item,
    claim_next_resumable_validating_item,
    transition_active_to_validating,
)
from orchestrator.services.final_notification_service import (
    EXTRACTION_FAILED_MESSAGE,
    INCOME_OUT_OF_SCOPE_MESSAGE,
    PERSISTENCE_FAILED_MESSAGE,
    run_final_notification_iteration,
)
from orchestrator.services.persistence_service import (
    claim_persistence_dispatch,
    dispatch_persistence_write,
    reconcile_persistence_outcomes,
    recover_stale_persistence_items,
)
from orchestrator.transcription_client import TranscriptionClientError

ROOT = Path(__file__).resolve().parents[1]
PLATFORM_URL = os.getenv(
    "GATE8_PLATFORM_DISPOSABLE_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:55432/platform_gate4_test",
)
WRITER_URL = os.getenv(
    "GATE8_WRITER_DISPOSABLE_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:55432/db_writer_gate4_test",
)
DF_ID = "12345678000190"
OUTSIDE_ID = "99999999000199"


@dataclass(frozen=True)
class Context:
    organization_id: str
    instance_id: str
    user_id: str
    phone: str
    enterprise_id: str


class LocalWriterClient:
    """In-process HTTP bridge to the real Gate 7 Writer FastAPI application."""

    def __init__(self, client: TestClient):
        self.client = client
        self.write_calls = 0

    @staticmethod
    def _headers(correlation_id: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {writer_main.settings.db_writer_internal_token}",
            "X-Correlation-ID": correlation_id,
        }

    def write(self, **kwargs: object) -> dict[str, object]:
        self.write_calls += 1
        response = self.client.post(
            "/internal/write",
            json=kwargs,
            headers=self._headers(str(kwargs["correlation_id"])),
        )
        if response.status_code == 409:
            return {"status": "REJECTED", "error_code": "IDEMPOTENCY_PAYLOAD_MISMATCH"}
        assert response.status_code == 200, response.text
        return response.json()

    def get_write_status(
        self, idempotency_key: str, correlation_id: str = "gate8-reconcile"
    ) -> dict[str, object]:
        response = self.client.get(
            f"/internal/writes/{idempotency_key}",
            headers=self._headers(correlation_id),
        )
        if response.status_code == 404:
            return {"status": "NOT_FOUND"}
        assert response.status_code == 200, response.text
        return response.json()

    def list_enterprises(self, correlation_id: str) -> list[dict[str, str]]:
        response = self.client.get(
            "/internal/enterprises", headers=self._headers(correlation_id)
        )
        assert response.status_code == 200, response.text
        return response.json()["enterprises"]


@pytest.fixture(scope="module")
def engine():
    value = create_engine(PLATFORM_URL, pool_size=10, max_overflow=10)
    with value.connect() as connection:
        assert connection.scalar(text("SHOW server_version"))
    config = Config(str(ROOT / "packages/db/alembic.ini"))
    config.set_main_option("sqlalchemy.url", PLATFORM_URL)
    command.upgrade(config, "head")
    yield value
    value.dispose()


@pytest.fixture(scope="module")
def writer_engine():
    value = create_engine(WRITER_URL, pool_size=10, max_overflow=10)
    with value.connect() as connection:
        assert connection.scalar(text("SHOW server_version"))
    config = Config(str(ROOT / "apps/db_writer/alembic.ini"))
    config.set_main_option("sqlalchemy.url", WRITER_URL)
    command.upgrade(config, "head")
    yield value
    value.dispose()


@pytest.fixture(autouse=True)
def clean(engine, writer_engine):
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE organizations CASCADE"))
    with writer_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE financial_records, suppliers, enterprises, "
                "write_ledger, df_business_records CASCADE"
            )
        )
    yield


@pytest.fixture
def writer_client(writer_engine, monkeypatch):
    def override():
        with Session(writer_engine) as db:
            yield db

    writer_main.app.dependency_overrides[writer_main.get_db] = override
    bridge = LocalWriterClient(TestClient(writer_main.app))
    monkeypatch.setattr(fifo_worker, "DBWriterClient", lambda: bridge)
    monkeypatch.setattr(
        "orchestrator.services.persistence_service.DBWriterClient", lambda: bridge
    )
    monkeypatch.setattr(fifo_worker, "_send_gate6_prompt", lambda *_args: True)
    yield bridge
    writer_main.app.dependency_overrides.clear()


def create_context(engine, writer_engine) -> Context:
    org, bot, inst, user, enterprise = (str(uuid.uuid4()) for _ in range(5))
    phone = f"55119{uuid.uuid4().int}"[:13]
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO organizations(id,name,slug,status) VALUES (:id,'O',:id,'ACTIVE')"),
            {"id": org},
        )
        connection.execute(
            text("INSERT INTO bots(id,organization_id,name,service_key,status) VALUES (:id,:org,'B',:id,'ACTIVE')"),
            {"id": bot, "org": org},
        )
        connection.execute(
            text("INSERT INTO instances(id,organization_id,bot_id,provider,external_instance_id,phone_number,status) VALUES (:id,:org,:bot,'WUZAPI',:id,:phone,'ACTIVE')"),
            {"id": inst, "org": org, "bot": bot, "phone": phone},
        )
        connection.execute(
            text("INSERT INTO users(id,organization_id,phone_number,status) VALUES (:id,:org,:phone,'ACTIVE')"),
            {"id": user, "org": org, "phone": phone},
        )
        connection.execute(
            text("INSERT INTO whatsapp_chat_enterprise_bindings(id,organization_id,instance_id,user_id,enterprise_id) VALUES (:id,:org,:inst,:user,:enterprise)"),
            {
                "id": str(uuid.uuid4()),
                "org": org,
                "inst": inst,
                "user": user,
                "enterprise": enterprise,
            },
        )
    with writer_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO enterprises(id,name) VALUES (:id,'Empresa Gate 8')"),
            {"id": enterprise},
        )
    return Context(org, inst, user, phone, enterprise)


def post_webhook(
    engine,
    context: Context,
    *,
    external_message_id: str,
    text_value: str | None = None,
):
    def override():
        with Session(engine) as db:
            yield db

    message: dict[str, object] = {
        "key": {
            "id": external_message_id,
            "remoteJid": f"{context.phone}@s.whatsapp.net",
        }
    }
    if text_value is None:
        message["imageMessage"] = {
            "mimetype": "image/jpeg",
            "fileLength": 2048,
            "fileSha256": hashlib.sha256(external_message_id.encode()).hexdigest(),
            "directPath": f"/gate8/{external_message_id}",
        }
    else:
        message["conversation"] = text_value
    payload = {
        "provider": "WUZAPI",
        "instanceId": context.instance_id,
        "data": {"message": message},
    }
    body = json.dumps(payload).encode()
    secret = "gate8-local-webhook-secret"
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    settings = get_settings()
    previous = settings.wuzapi_webhook_secret
    settings.wuzapi_webhook_secret = secret
    orchestrator_main.app.dependency_overrides[orchestrator_main.get_db] = override
    try:
        return TestClient(orchestrator_main.app).post(
            "/webhook",
            content=body,
            headers={"content-type": "application/json", "x-hmac-signature": signature},
        )
    finally:
        orchestrator_main.app.dependency_overrides.clear()
        settings.wuzapi_webhook_secret = previous


class FakeExtraction:
    def __init__(self, normalized: dict[str, object] | None = None, *, fail=False):
        self.normalized = normalized
        self.fail = fail
        self.calls = 0

    async def extract(self, **_kwargs: object) -> dict[str, object]:
        self.calls += 1
        if self.fail:
            raise TranscriptionClientError(
                "sanitized extraction unavailable",
                error_code="EXTRACTION_UNAVAILABLE",
                retryable=False,
            )
        return {
            "document_type": "pix_receipt",
            "extraction": {"provider": "deterministic-local"},
            "normalization": self.normalized or {},
        }


def ingest_and_extract(
    engine,
    context: Context,
    normalized: dict[str, object] | None,
    *,
    external_message_id: str | None = None,
    fail: bool = False,
) -> tuple[str, FakeExtraction]:
    external_id = external_message_id or f"gate8-doc-{uuid.uuid4()}"
    response = post_webhook(
        engine, context, external_message_id=external_id, text_value=None
    )
    assert response.status_code == 200, response.text
    with Session(engine) as db:
        item = claim_next_received_item_for_extraction(db, "gate8")
        assert item is not None
        item_id = item.id
        fake = FakeExtraction(normalized, fail=fail)
        asyncio.run(ExtractionDispatcher(fake).process_item(db, item, b"document"))
    return item_id, fake


def expense_normalized(*, amount: str | None = "1200.00", ambiguous=False):
    return {
        "amount": amount,
        "transaction_date": "2026-07-29",
        "sender_cpf_cnpj": DF_ID,
        "receiver_cpf_cnpj": DF_ID if ambiguous else OUTSIDE_ID,
    }


def income_normalized():
    return {
        "amount": "500.00",
        "transaction_date": "2026-07-29",
        "sender_cpf_cnpj": OUTSIDE_ID,
        "receiver_cpf_cnpj": DF_ID,
    }


def process_next_business(engine, worker: str = "gate8") -> str | None:
    with Session(engine) as db:
        item = claim_next_ready_item(db, worker)
        if item is None:
            return None
        validating = transition_active_to_validating(db, item.id, worker)
        assert validating is not None
        tracker = WorkerClaimTracker(worker)
        tracker.add_claim(item.id)
        fifo_worker._process_validating_item(
            db,
            validating,
            worker,
            BusinessRulesEvaluatorService([DF_ID]),
            tracker,
        )
        return item.id


def resume_business(engine, item_id: str, worker: str = "gate8-resume") -> None:
    with Session(engine) as db:
        item = claim_next_resumable_validating_item(db, worker)
        assert item is not None and item.id == item_id
        tracker = WorkerClaimTracker(worker)
        tracker.add_claim(item.id)
        fifo_worker._process_validating_item(
            db, item, worker, BusinessRulesEvaluatorService([DF_ID]), tracker
        )


def notify_all(engine, sender=None) -> list[tuple[str, str]]:
    sent: list[tuple[str, str]] = []

    def default(_phone: str, message: str, outbound: str) -> bool:
        sent.append((message, outbound))
        return True

    callback = sender or default
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    while run_final_notification_iteration(factory, callback):
        pass
    return sent


def writer_counts(writer_engine, item_id: str) -> tuple[int, int]:
    with writer_engine.connect() as connection:
        records = connection.scalar(
            text("SELECT count(*) FROM financial_records WHERE processing_item_id=:item"),
            {"item": item_id},
        )
        ledger = connection.scalar(
            text("SELECT count(*) FROM write_ledger WHERE processing_item_id=:item AND status='COMMITTED'"),
            {"item": item_id},
        )
    return int(records or 0), int(ledger or 0)


def test_g8_x01_pix_expense_commits_and_sends_one_success(
    engine, writer_engine, writer_client
) -> None:
    context = create_context(engine, writer_engine)
    item_id, extraction = ingest_and_extract(engine, context, expense_normalized())
    assert process_next_business(engine) == item_id
    assert extraction.calls == 1
    with Session(engine) as db:
        item = db.get(ProcessingItem, item_id)
        committed = db.scalar(
            select(Execution).where(
                Execution.processing_item_id == item_id,
                Execution.operation == "PERSISTENCE_COMMITTED",
            )
        )
        assert item is not None and item.status == "COMPLETED"
        assert committed is not None and committed.external_reference
    assert writer_counts(writer_engine, item_id) == (1, 1)
    sent = notify_all(engine)
    assert sent == [
        (
            "\u2705 Gravado com sucesso.\n\nDespesa de R$ 1.200,00 realizada em 29/07/2026.",
            f"final_{item_id}_expense_committed",
        )
    ]


def test_g8_x02_pix_income_is_ignored_and_sends_one_information_message(
    engine, writer_engine, writer_client
) -> None:
    context = create_context(engine, writer_engine)
    item_id, _ = ingest_and_extract(engine, context, income_normalized())
    before = writer_client.write_calls
    assert process_next_business(engine) == item_id
    assert writer_client.write_calls == before
    with Session(engine) as db:
        item = db.get(ProcessingItem, item_id)
        assert item is not None
        assert (item.status, item.outcome_reason) == ("IGNORED", "INCOME_OUT_OF_SCOPE")
    assert writer_counts(writer_engine, item_id) == (0, 0)
    sent = notify_all(engine)
    assert len(sent) == 1 and sent[0][0] == INCOME_OUT_OF_SCOPE_MESSAGE


def _clarification_flow(
    engine,
    writer_engine,
    context: Context,
    normalized: dict[str, object],
    answer: str,
) -> tuple[str, list[tuple[str, str]]]:
    item_id, _ = ingest_and_extract(engine, context, normalized)
    assert process_next_business(engine) == item_id
    with Session(engine) as db:
        item = db.get(ProcessingItem, item_id)
        assert item is not None and item.status == "WAITING_USER_INPUT"
    response = post_webhook(
        engine,
        context,
        external_message_id=f"gate8-answer-{uuid.uuid4()}",
        text_value=answer,
    )
    assert response.status_code == 200
    assert response.json()["detail"] == "answer_applied"
    resume_business(engine, item_id)
    assert writer_counts(writer_engine, item_id) == (1, 1)
    return item_id, notify_all(engine)


def test_g8_x03_ambiguous_direction_answer_commits_and_sends_success(
    engine, writer_engine, writer_client
) -> None:
    context = create_context(engine, writer_engine)
    item_id, sent = _clarification_flow(
        engine, writer_engine, context, expense_normalized(ambiguous=True), "despesa"
    )
    assert sent == [
        (
            "\u2705 Gravado com sucesso.\n\nDespesa de R$ 1.200,00 realizada em 29/07/2026.",
            f"final_{item_id}_expense_committed",
        )
    ]
    with Session(engine) as db:
        answer = db.scalar(select(UserAnswer).where(UserAnswer.processing_item_id == item_id))
        assert answer is not None and answer.status == "APPLIED"


def test_g8_x04_missing_amount_answer_commits_and_sends_success(
    engine, writer_engine, writer_client
) -> None:
    context = create_context(engine, writer_engine)
    item_id, sent = _clarification_flow(
        engine, writer_engine, context, expense_normalized(amount=None), "1.200,00"
    )
    assert len(sent) == 1 and "R$ 1.200,00" in sent[0][0]
    with Session(engine) as db:
        item = db.get(ProcessingItem, item_id)
        assert item is not None and item.status == "COMPLETED"


def test_g8_x05_missing_date_uses_timestamp_and_sends_success(
    engine, writer_engine, writer_client
) -> None:
    context = create_context(engine, writer_engine)
    normalized = expense_normalized()
    normalized["transaction_date"] = None
    item_id, _ = ingest_and_extract(engine, context, normalized)
    assert process_next_business(engine) == item_id
    sent = notify_all(engine)
    assert len(sent) == 1 and "realizada em " in sent[0][0]
    with Session(engine) as db:
        item = db.get(ProcessingItem, item_id)
        assert item is not None and item.date_source == "MESSAGE_TIMESTAMP"


def test_g8_x06_five_documents_preserve_business_fifo_without_notification_barrier(
    engine, writer_engine, writer_client
) -> None:
    context = create_context(engine, writer_engine)
    item_ids = [
        ingest_and_extract(engine, context, expense_normalized())[0]
        for _ in range(5)
    ]
    processed = [process_next_business(engine, f"gate8-{n}") for n in range(5)]
    assert processed == item_ids
    with Session(engine) as db:
        claims = list(
            db.execute(
                select(Execution.processing_item_id)
                .where(Execution.operation == "BUSINESS_CLAIM")
                .order_by(Execution.started_at, Execution.id)
            ).scalars()
        )
        statuses = list(
            db.execute(
                select(ProcessingItem.status)
                .where(ProcessingItem.id.in_(item_ids))
                .order_by(ProcessingItem.sequence)
            ).scalars()
        )
    assert claims == item_ids
    assert statuses == ["COMPLETED"] * 5
    assert all(writer_counts(writer_engine, item_id) == (1, 1) for item_id in item_ids)
    assert len(notify_all(engine)) == 5


def test_g8_x08_original_webhook_replay_has_one_full_effect(
    engine, writer_engine, writer_client
) -> None:
    context = create_context(engine, writer_engine)
    external_id = f"gate8-replay-{uuid.uuid4()}"
    first = post_webhook(engine, context, external_message_id=external_id)
    replay = post_webhook(engine, context, external_message_id=external_id)
    assert first.status_code == replay.status_code == 200
    assert replay.json()["detail"] == "duplicate"
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Event)) == 1
        assert db.scalar(select(func.count()).select_from(ProcessingItem)) == 1
        item = claim_next_received_item_for_extraction(db, "gate8-replay")
        assert item is not None
        item_id = item.id
        fake = FakeExtraction(expense_normalized())
        asyncio.run(ExtractionDispatcher(fake).process_item(db, item, b"document"))
    assert process_next_business(engine) == item_id
    sent = notify_all(engine)
    assert len(sent) == 1
    assert writer_counts(writer_engine, item_id) == (1, 1)
    assert fake.calls == 1


def test_g8_x09_extraction_unavailable_sends_one_sanitized_failure(
    engine, writer_engine, writer_client
) -> None:
    context = create_context(engine, writer_engine)
    item_id, fake = ingest_and_extract(engine, context, None, fail=True)
    with Session(engine) as db:
        item = db.get(ProcessingItem, item_id)
        assert item is not None and item.status == "EXTRACTION_FAILED"
        assert claim_next_ready_item(db, "forbidden") is None
    assert fake.calls == 1
    assert writer_counts(writer_engine, item_id) == (0, 0)
    sent = notify_all(engine)
    assert len(sent) == 1 and sent[0][0] == EXTRACTION_FAILED_MESSAGE
    assert "gemini" not in sent[0][0].lower()


class RetryOnceWriter:
    def __init__(self, delegate: LocalWriterClient):
        self.delegate = delegate
        self.calls = 0

    def write(self, **kwargs: object) -> dict[str, object]:
        self.calls += 1
        if self.calls == 1:
            return {"status": "RETRYABLE_FAILURE", "error_code": "LOCAL_RETRY"}
        return self.delegate.write(**kwargs)


class AmbiguousAfterCommitWriter:
    def __init__(self, delegate: LocalWriterClient):
        self.delegate = delegate

    def write(self, **kwargs: object) -> dict[str, object]:
        committed = self.delegate.write(**kwargs)
        assert committed["status"] == "COMMITTED"
        return {"status": "OUTCOME_UNKNOWN", "error_code": "LOCAL_AMBIGUOUS"}


def _prepare_persisting_item(engine, writer_engine) -> tuple[Context, str]:
    context = create_context(engine, writer_engine)
    item_id, _ = ingest_and_extract(engine, context, expense_normalized())
    return context, item_id


def test_g8_x10_actual_retryable_then_committed(engine, writer_engine, writer_client, monkeypatch) -> None:
    _, item_id = _prepare_persisting_item(engine, writer_engine)
    retry = RetryOnceWriter(writer_client)
    monkeypatch.setattr("orchestrator.services.persistence_service.DBWriterClient", lambda: retry)
    assert process_next_business(engine) == item_id
    with Session(engine) as db:
        item = db.get(ProcessingItem, item_id)
        assert item is not None and item.status == "PERSIST_RETRYABLE"
        assert run_final_notification_iteration(sessionmaker(bind=engine), lambda *_: True) is False
        item.persistence_next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
        assert recover_stale_persistence_items(db) == 1
        claim = claim_persistence_dispatch(db, item_id, "gate8-retry")
        assert claim is not None
        final = dispatch_persistence_write(db, item_id, claim[1], writer_client)
        assert final is not None and final.status == "COMPLETED"
    assert writer_counts(writer_engine, item_id) == (1, 1)
    assert len(notify_all(engine)) == 1


def test_g8_x10_actual_unknown_reconciles_committed(engine, writer_engine, writer_client, monkeypatch) -> None:
    _, item_id = _prepare_persisting_item(engine, writer_engine)
    ambiguous = AmbiguousAfterCommitWriter(writer_client)
    monkeypatch.setattr("orchestrator.services.persistence_service.DBWriterClient", lambda: ambiguous)
    assert process_next_business(engine) == item_id
    with Session(engine) as db:
        item = db.get(ProcessingItem, item_id)
        assert item is not None and item.status == "PERSIST_OUTCOME_UNKNOWN"
        assert reconcile_persistence_outcomes(db, client=writer_client) == 1
        db.refresh(item)
        assert item.status == "COMPLETED"
    assert writer_counts(writer_engine, item_id) == (1, 1)
    assert len(notify_all(engine)) == 1


def test_g8_x10_actual_writer_rejection_sends_failure(engine, writer_engine, writer_client) -> None:
    context = create_context(engine, writer_engine)
    item_id, _ = ingest_and_extract(engine, context, expense_normalized())
    with Session(engine) as db:
        item = db.get(ProcessingItem, item_id)
        assert item is not None
        item.enterprise_id = str(uuid.uuid4())
        db.commit()
    assert process_next_business(engine) == item_id
    with Session(engine) as db:
        item = db.get(ProcessingItem, item_id)
        assert item is not None and item.status == "PERSISTENCE_FAILED"
    assert writer_counts(writer_engine, item_id) == (0, 0)
    sent = notify_all(engine)
    assert len(sent) == 1 and sent[0][0] == PERSISTENCE_FAILED_MESSAGE


def test_g8_x11_outbound_unknown_is_not_resent(engine, writer_engine, writer_client) -> None:
    context = create_context(engine, writer_engine)
    item_id, _ = ingest_and_extract(engine, context, expense_normalized())
    assert process_next_business(engine) == item_id
    calls = 0

    def unavailable(*_args: str) -> bool:
        nonlocal calls
        calls += 1
        return False

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    assert run_final_notification_iteration(factory, unavailable)
    assert not run_final_notification_iteration(factory, unavailable)
    assert calls == 1


def test_g8_x12_physical_correlation_chain(engine, writer_engine, writer_client) -> None:
    context = create_context(engine, writer_engine)
    item_id, _ = _clarification_flow(
        engine, writer_engine, context, expense_normalized(amount=None), "1.200,00"
    )
    with Session(engine) as db:
        item = db.get(ProcessingItem, item_id)
        assert item is not None
        event = db.get(Event, item.event_id)
        interaction = db.scalar(select(UserInteraction).where(UserInteraction.processing_item_id == item_id))
        answer = db.scalar(select(UserAnswer).where(UserAnswer.processing_item_id == item_id))
        operations = set(
            db.scalars(
                select(Execution.operation).where(
                    Execution.processing_item_id == item_id,
                    Execution.correlation_id == item.correlation_id,
                )
            )
        )
        committed = db.scalar(
            select(Execution.external_reference).where(
                Execution.processing_item_id == item_id,
                Execution.operation == "PERSISTENCE_COMMITTED",
            )
        )
    with writer_engine.connect() as connection:
        ledger = connection.execute(
            text("SELECT processing_item_id,committed_record_id,status FROM write_ledger WHERE processing_item_id=:item"),
            {"item": item_id},
        ).one()
        record = connection.scalar(
            text("SELECT count(*) FROM financial_records WHERE id=:id AND processing_item_id=:item"),
            {"id": committed, "item": item_id},
        )
    assert event is not None and event.correlation_id == item.correlation_id
    assert interaction is not None and answer is not None and answer.status == "APPLIED"
    assert ledger.processing_item_id == item.id
    assert str(ledger.committed_record_id) == committed and ledger.status == "COMMITTED"
    assert record == 1
    assert {
        "PERSISTENCE_COMMITTED",
        "FINAL_NOTIFICATION_RESERVED",
        "FINAL_NOTIFICATION_DISPATCHED",
        "FINAL_NOTIFICATION_ACKNOWLEDGED",
    }.issubset(operations)
