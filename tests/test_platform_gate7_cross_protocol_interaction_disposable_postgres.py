from __future__ import annotations

import os
import uuid
import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from db.models import (
    EnterpriseCommandAnswer,
    EnterpriseCommandSession,
    Event,
    ProcessingItem,
    UserAnswer,
    UserInteraction,
    WhatsappChatEnterpriseBinding,
)
from orchestrator import main as orchestrator_main
from orchestrator import fifo_worker
from orchestrator.fifo_worker import WorkerClaimTracker
from orchestrator.services.business_rules_evaluator import BusinessRulesEvaluatorService
from orchestrator.services import enterprise_command_service
from orchestrator.services.enterprise_command_service import (
    apply_enterprise_command_answer,
    open_enterprise_command_session,
)
from orchestrator.services.enterprise_resolution_service import (
    materialize_persistent_enterprise_binding,
)
from orchestrator.services.fifo_worker_service import claim_next_ready_item
from orchestrator.services.user_interaction_service import (
    EnterpriseCommandBarrier,
    dispatch_user_prompt,
)
from orchestrator.config import get_settings
from orchestrator.main import app, get_db

pytestmark = pytest.mark.real_pg15



ROOT = Path(__file__).resolve().parents[1]
URL = os.getenv(
    "GATE7_PLATFORM_DISPOSABLE_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:55432/platform_gate4_test",
)


class FakeClient:
    def __init__(self, enterprise_id: str):
        self.enterprise_id = enterprise_id
        self.calls = 0

    def list_enterprises(self, correlation_id: str):
        self.calls += 1
        return [{"id": self.enterprise_id, "display_name": "Empresa"}]


@pytest.fixture(scope="module")
def engine():
    value = create_engine(URL, pool_size=10, max_overflow=10)
    try:
        with value.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"Disposable PostgreSQL 15 unavailable: {exc}")
    cfg = Config(str(ROOT / "packages/db/alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", URL)
    command.upgrade(cfg, "head")
    yield value
    value.dispose()


@pytest.fixture(autouse=True)
def clean(engine):
    yield
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE organizations CASCADE"))


def _setup(engine, item_status="VALIDATING"):
    org, bot, inst, user = (str(uuid.uuid4()) for _ in range(4))
    now = datetime.now(timezone.utc)
    with Session(engine) as db:
        db.execute(
            text(
                "INSERT INTO organizations(id,name,slug,status) VALUES (:id,'O',:id,'ACTIVE')"
            ),
            {"id": org},
        )
        db.execute(
            text(
                "INSERT INTO bots(id,organization_id,name,service_key,status) VALUES (:id,:org,'B',:id,'ACTIVE')"
            ),
            {"id": bot, "org": org},
        )
        db.execute(
            text(
                "INSERT INTO instances(id,organization_id,bot_id,provider,external_instance_id,phone_number,status) VALUES (:id,:org,:bot,'WUZAPI',:id,:phone,'ACTIVE')"
            ),
            {"id": inst, "org": org, "bot": bot, "phone": f"55{uuid.uuid4().int}"[:15]},
        )
        db.execute(
            text(
                "INSERT INTO users(id,organization_id,phone_number,status) VALUES (:id,:org,:phone,'ACTIVE')"
            ),
            {"id": user, "org": org, "phone": f"54{uuid.uuid4().int}"[:15]},
        )
        event = Event(
            correlation_id=str(uuid.uuid4()),
            provider="WUZAPI",
            external_instance_id=inst,
            external_message_id=str(uuid.uuid4()),
            organization_id=org,
            instance_id=inst,
            user_id=user,
            message_type="image",
            status="ROUTED",
        )
        db.add(event)
        db.flush()
        item = ProcessingItem(
            event_id=event.id,
            correlation_id=event.correlation_id,
            organization_id=org,
            instance_id=inst,
            user_id=user,
            sequence=1,
            status=item_status,
            claimed_by="worker-1" if item_status == "VALIDATING" else None,
            lease_expires_at=now + timedelta(minutes=5)
            if item_status == "VALIDATING"
            else None,
            attempt_count=1 if item_status == "VALIDATING" else 0,
            message_received_at=now,
            file_mime_type="image/jpeg",
            file_size=1,
            file_sha256="f" * 64,
        )
        db.add(item)
        db.commit()
        return (org, inst, user), event.id, event.correlation_id, item.id


def _command_event(engine, context):
    org, inst, user = context
    with Session(engine) as db:
        event = Event(
            correlation_id=str(uuid.uuid4()),
            provider="WUZAPI",
            external_instance_id=inst,
            external_message_id=str(uuid.uuid4()),
            organization_id=org,
            instance_id=inst,
            user_id=user,
            message_type="text",
            status="ROUTED",
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        return event.id, event.correlation_id


def test_simultaneous_command_and_document_prompt_exactly_one_owner(engine) -> None:
    context, _, _, item_id = _setup(engine)
    cmd_event, corr = _command_event(engine, context)
    enterprise_id = str(uuid.uuid4())

    def command_open():
        with Session(engine) as db:
            return open_enterprise_command_session(
                db, *context, cmd_event, corr, FakeClient(enterprise_id)
            ).status

    def prompt_open():
        with Session(engine) as db:
            try:
                return dispatch_user_prompt(
                    db, item_id, "transaction_amount", worker_id="worker-1"
                ).status
            except EnterpriseCommandBarrier:
                return "BARRIER"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(command_open), pool.submit(prompt_open)]
        [future.result(timeout=10) for future in results]
    with Session(engine) as db:
        commands = (
            db.query(EnterpriseCommandSession)
            .filter(
                EnterpriseCommandSession.status.in_(
                    ["RESERVED", "WAITING", "OUTBOUND_OUTCOME_UNKNOWN"]
                )
            )
            .count()
        )
        interactions = (
            db.query(UserInteraction)
            .filter(
                UserInteraction.status.in_(
                    ["RESERVED", "WAITING", "OUTBOUND_OUTCOME_UNKNOWN"]
                )
            )
            .count()
        )
        assert commands + interactions == 1


def test_open_command_remains_owner_when_worker_requires_direction(engine) -> None:
    context, _, _, item_id = _setup(engine)
    command_event, corr = _command_event(engine, context)
    with Session(engine) as db:
        command = open_enterprise_command_session(
            db, *context, command_event, corr, FakeClient(str(uuid.uuid4()))
        )
        tracker = WorkerClaimTracker("worker-1")
        tracker.add_claim(item_id)
        item = db.get(ProcessingItem, item_id)
        assert item is not None
        fifo_worker._process_validating_item(
            db,
            item,
            "worker-1",
            BusinessRulesEvaluatorService(["00000000000000"]),
            tracker,
        )
        db.refresh(item)
        db.refresh(command.session)
        assert command.session.status == "RESERVED"
        assert db.query(EnterpriseCommandSession).count() == 1
        assert db.query(UserInteraction).count() == 0
        assert item.status == "VALIDATING"
        assert item.claimed_by is None
        assert item.attempt_count == 1
        assert item_id not in tracker.owned_claims


def test_open_command_blocks_ready_then_answer_resumes(engine) -> None:
    context, _, _, item_id = _setup(engine, "READY")
    cmd_event, corr = _command_event(engine, context)
    enterprise_id = str(uuid.uuid4())
    with Session(engine) as db:
        open_enterprise_command_session(
            db, *context, cmd_event, corr, FakeClient(enterprise_id)
        )
        assert claim_next_ready_item(db, "worker-2") is None
    answer_event, _ = _command_event(engine, context)
    with Session(engine) as db:
        assert (
            apply_enterprise_command_answer(db, *context, answer_event, "1").status
            == "APPLIED"
        )
        claimed = claim_next_ready_item(db, "worker-2")
        assert claimed is not None and claimed.id == item_id


def test_open_command_barrier_skips_conversation_without_mutating_ready_item(
    engine,
) -> None:
    context_a, _, _, item_a_id = _setup(engine, "READY")
    command_event, corr = _command_event(engine, context_a)
    selected_enterprise_id = str(uuid.uuid4())
    with Session(engine) as db:
        open_enterprise_command_session(
            db, *context_a, command_event, corr, FakeClient(selected_enterprise_id)
        )
    _, _, _, item_b_id = _setup(engine, "READY")

    with Session(engine) as db:
        before = db.get(ProcessingItem, item_a_id)
        assert before is not None
        original = (
            before.sequence,
            before.attempt_count,
            before.claimed_by,
            before.lease_expires_at,
        )
        claimed_b = claim_next_ready_item(db, "worker-b")
        assert claimed_b is not None and claimed_b.id == item_b_id
        db.refresh(before)
        assert before.status == "READY"
        assert (
            before.sequence,
            before.attempt_count,
            before.claimed_by,
            before.lease_expires_at,
        ) == original

    answer_event, _ = _command_event(engine, context_a)
    with Session(engine) as db:
        answer = apply_enterprise_command_answer(
            db, *context_a, answer_event, "1"
        )
        assert answer.status == "APPLIED"
        claimed_a = claim_next_ready_item(db, "worker-a")
        assert claimed_a is not None and claimed_a.id == item_a_id
        assert claimed_a.sequence == original[0]
        assert claimed_a.attempt_count == original[1] + 1
        assert claimed_a.enterprise_id is None
        assert materialize_persistent_enterprise_binding(
            db,
            claimed_a,
            FakeClient(selected_enterprise_id),
            claimed_a.correlation_id,
        ) == selected_enterprise_id
        assert claimed_a.enterprise_id == selected_enterprise_id


def test_clear_command_releases_ready_item_to_document_fallback(engine) -> None:
    context, _, _, item_id = _setup(engine, "READY")
    org, inst, user = context
    cmd_event, corr = _command_event(engine, context)
    with Session(engine) as db:
        db.add(
            WhatsappChatEnterpriseBinding(
                organization_id=org,
                instance_id=inst,
                user_id=user,
                enterprise_id=str(uuid.uuid4()),
            )
        )
        db.commit()
        open_enterprise_command_session(
            db, *context, cmd_event, corr, FakeClient(str(uuid.uuid4()))
        )
        assert claim_next_ready_item(db, "worker-before-clear") is None
    answer_event, _ = _command_event(engine, context)
    with Session(engine) as db:
        assert (
            apply_enterprise_command_answer(db, *context, answer_event, "2").status
            == "APPLIED"
        )
        assert db.query(WhatsappChatEnterpriseBinding).count() == 0
        claimed = claim_next_ready_item(db, "worker-after-clear")
        assert claimed is not None and claimed.id == item_id
        assert claimed.enterprise_id is None


def test_open_document_interaction_makes_command_busy(engine) -> None:
    context, _, _, item_id = _setup(engine)
    cmd_event, corr = _command_event(engine, context)
    with Session(engine) as db:
        interaction = dispatch_user_prompt(
            db, item_id, "transaction_amount", worker_id="worker-1"
        )
        before_expiry = interaction.expires_at
        client = FakeClient(str(uuid.uuid4()))
        result = open_enterprise_command_session(db, *context, cmd_event, corr, client)
        assert result.status == "DOCUMENT_INTERACTION_BUSY"
        assert client.calls == 0
        db.refresh(interaction)
        assert interaction.expires_at == before_expiry
        assert db.query(EnterpriseCommandSession).count() == 0


def _post_text(
    engine,
    context,
    text_value: str,
    *,
    external_message_id: str | None = None,
):
    org, inst, user = context
    with Session(engine) as db:
        phone = db.execute(
            text("SELECT phone_number FROM users WHERE id=:id"), {"id": user}
        ).scalar_one()

    def override():
        with Session(engine) as session:
            yield session

    secret = "gate7-router-secret"
    settings = get_settings()
    previous = settings.wuzapi_webhook_secret
    settings.wuzapi_webhook_secret = secret
    payload = {
        "provider": "WUZAPI",
        "instanceId": inst,
        "data": {
            "message": {
                "key": {
                    "id": external_message_id or str(uuid.uuid4()),
                    "remoteJid": f"{phone}@s.whatsapp.net",
                },
                "conversation": text_value,
            }
        },
    }
    body = json.dumps(payload).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    app.dependency_overrides[get_db] = override
    try:
        return TestClient(app).post(
            "/webhook",
            content=body,
            headers={
                "content-type": "application/json",
                "x-hmac-signature": signature,
            },
        )
    finally:
        app.dependency_overrides.clear()
        settings.wuzapi_webhook_secret = previous


def test_new_normal_text_routes_without_unbound_event_crash(engine) -> None:
    context, _, _, item_id = _setup(engine)
    with Session(engine) as db:
        dispatch_user_prompt(
            db, item_id, "transaction_amount", worker_id="worker-1"
        )
    response = _post_text(engine, context, "10,00")
    assert response.status_code == 200
    assert response.json()["detail"] == "answer_applied"


def test_new_free_text_uses_existing_fallback_without_crash(engine) -> None:
    context, _, _, _ = _setup(engine, "READY")
    response = _post_text(engine, context, "texto livre")
    assert response.status_code == 200
    assert response.json()["detail"] == "text_no_waiting_item"


def test_router_sends_numeric_answer_only_to_open_command(engine) -> None:
    context, _, _, _ = _setup(engine, "READY")
    command_event, corr = _command_event(engine, context)
    with Session(engine) as db:
        open_enterprise_command_session(
            db, *context, command_event, corr, FakeClient(str(uuid.uuid4()))
        )
    response = _post_text(engine, context, "1")
    assert response.status_code == 200
    assert response.json()["detail"] == "enterprise_command_answer_applied"


def test_router_records_late_answer_for_recent_expired_command(engine) -> None:
    context, _, _, _ = _setup(engine, "READY")
    command_event, corr = _command_event(engine, context)
    with Session(engine) as db:
        result = open_enterprise_command_session(
            db, *context, command_event, corr, FakeClient(str(uuid.uuid4()))
        )
        result.session.status = "EXPIRED"
        result.session.resolved_at = datetime.now(timezone.utc)
        db.commit()
    response = _post_text(engine, context, "1")
    assert response.status_code == 200
    assert response.json()["detail"] == "enterprise_command_answer_late"


def test_real_webhook_duplicate_command_answer_has_one_business_effect(engine) -> None:
    context, _, _, _ = _setup(engine, "READY")
    enterprise_id = str(uuid.uuid4())
    command_event, corr = _command_event(engine, context)
    with Session(engine) as db:
        result = open_enterprise_command_session(
            db, *context, command_event, corr, FakeClient(enterprise_id)
        )
        session_id = result.session.id
    external_id = str(uuid.uuid4())
    first = _post_text(engine, context, "1", external_message_id=external_id)
    replay = _post_text(engine, context, "1", external_message_id=external_id)
    assert first.json()["detail"] == "enterprise_command_answer_applied"
    assert replay.json()["detail"] == "idempotent duplicate"
    with Session(engine) as db:
        session = db.get(EnterpriseCommandSession, session_id)
        assert session is not None and session.status == "ANSWERED"
        assert db.query(EnterpriseCommandAnswer).count() == 1
        binding = db.query(WhatsappChatEnterpriseBinding).one()
        assert binding.enterprise_id == enterprise_id


def test_real_webhook_duplicate_user_answer_has_one_applied_mutation(
    engine, monkeypatch
) -> None:
    context, _, _, item_id = _setup(engine)
    sends: list[str] = []

    class Sender:
        async def send_text_message(self, _phone, message):
            sends.append(message)

    monkeypatch.setattr(orchestrator_main, "WuzapiClient", Sender)
    with Session(engine) as db:
        dispatch_user_prompt(
            db, item_id, "transaction_amount", worker_id="worker-1"
        )
    external_id = str(uuid.uuid4())
    first = _post_text(engine, context, "10,00", external_message_id=external_id)
    replay = _post_text(engine, context, "10,00", external_message_id=external_id)
    assert first.json()["detail"] == "answer_applied"
    assert replay.json()["detail"] == "idempotent duplicate"
    with Session(engine) as db:
        item = db.get(ProcessingItem, item_id)
        assert item is not None and str(item.amount) == "10.00"
        answers = db.query(UserAnswer).all()
        assert len(answers) == 1 and answers[0].status == "APPLIED"
    assert len(sends) == 1


def test_real_webhook_duplicate_unsupported_text_has_no_duplicate_effect(
    engine, monkeypatch
) -> None:
    context, _, _, _ = _setup(engine, "READY")
    sends: list[str] = []

    class Sender:
        async def send_text_message(self, _phone, message):
            sends.append(message)

    monkeypatch.setattr(orchestrator_main, "WuzapiClient", Sender)
    external_id = str(uuid.uuid4())
    first = _post_text(engine, context, "texto livre", external_message_id=external_id)
    replay = _post_text(engine, context, "texto livre", external_message_id=external_id)
    assert first.json()["detail"] == "text_no_waiting_item"
    assert replay.json()["detail"] == "idempotent duplicate"
    with Session(engine) as db:
        event = db.query(Event).filter(Event.external_message_id == external_id).one()
        assert event.duplicate_count == 1
        assert db.query(UserAnswer).count() == 0
        assert db.query(EnterpriseCommandAnswer).count() == 0
    assert sends == []


def test_real_webhook_cancelled_command_answer_is_late_without_reopen(engine) -> None:
    context, _, _, _ = _setup(engine, "READY")
    enterprise_id = str(uuid.uuid4())
    client = FakeClient(enterprise_id)
    command_event, corr = _command_event(engine, context)
    org, inst, user = context
    original_binding = str(uuid.uuid4())
    with Session(engine) as db:
        result = open_enterprise_command_session(
            db, *context, command_event, corr, client
        )
        session_id = result.session.id
        result.session.status = "CANCELLED"
        result.session.resolved_at = datetime.now(timezone.utc)
        db.add(
            WhatsappChatEnterpriseBinding(
                organization_id=org,
                instance_id=inst,
                user_id=user,
                enterprise_id=original_binding,
            )
        )
        db.commit()
    calls_before = client.calls
    response = _post_text(engine, context, "1")
    assert response.json()["detail"] == "enterprise_command_answer_late"
    with Session(engine) as db:
        session = db.get(EnterpriseCommandSession, session_id)
        assert session is not None and session.status == "CANCELLED"
        answer = db.query(EnterpriseCommandAnswer).one()
        assert answer.status == "LATE"
        assert db.query(EnterpriseCommandSession).count() == 1
        assert db.query(UserInteraction).count() == 0
        assert db.query(WhatsappChatEnterpriseBinding).one().enterprise_id == original_binding
    assert client.calls == calls_before


def test_real_webhook_enterprise_command_during_amount_prompt_is_busy(
    engine, monkeypatch
) -> None:
    context, _, _, item_id = _setup(engine)
    writer_calls: list[str] = []
    outbound: list[str] = []

    class Writer:
        def list_enterprises(self, correlation_id):
            writer_calls.append(correlation_id)
            return []

    class Sender:
        async def send_text_message(self, _phone, message):
            outbound.append(message)

    monkeypatch.setattr(enterprise_command_service, "DBWriterClient", Writer)
    monkeypatch.setattr(orchestrator_main, "WuzapiClient", Sender)
    with Session(engine) as db:
        interaction = dispatch_user_prompt(
            db, item_id, "transaction_amount", worker_id="worker-1"
        )
        interaction_id = interaction.id
        original_expiry = interaction.expires_at
    response = _post_text(engine, context, "/empreendimento")
    assert response.json()["detail"] == "enterprise_command_document_busy"
    with Session(engine) as db:
        interaction = db.get(UserInteraction, interaction_id)
        item = db.get(ProcessingItem, item_id)
        assert interaction is not None and interaction.status == "WAITING"
        assert interaction.expires_at == original_expiry
        assert item is not None and item.question_type == "transaction_amount"
        assert db.query(UserAnswer).count() == 0
        assert db.query(EnterpriseCommandSession).count() == 0
    assert writer_calls == []
    assert len(outbound) == 1
