from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from db.models import (
    Event,
    ProcessingItem,
    UserInteraction,
    WhatsappChatEnterpriseBinding,
)
from orchestrator.services.enterprise_command_service import (
    apply_enterprise_command_answer,
    expire_enterprise_command_sessions,
    open_enterprise_command_session,
    recover_reserved_enterprise_command_sessions,
)
from orchestrator.services.enterprise_resolution_service import (
    build_enterprise_option_mapping,
    materialize_persistent_enterprise_binding,
)
from orchestrator.services.fifo_worker_service import claim_next_ready_item
from orchestrator.services.user_interaction_service import (
    apply_user_answer,
    dispatch_user_prompt,
)

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
        self.write_calls = 0

    def list_enterprises(self, correlation_id: str):
        self.calls += 1
        return [{"id": self.enterprise_id, "display_name": "Empresa A"}]

    def write(self, *_args, **_kwargs):
        self.write_calls += 1
        raise AssertionError("Writer POST must not occur while enterprise is unresolved")


@pytest.fixture(scope="module")
def engine():
    value = create_engine(URL)
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


def _context(engine):
    ids = [str(uuid.uuid4()) for _ in range(4)]
    org, bot, inst, user = ids
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO organizations(id,name,slug,status) VALUES (:id,'O',:id,'ACTIVE')"
            ),
            {"id": org},
        )
        c.execute(
            text(
                "INSERT INTO bots(id,organization_id,name,service_key,status) VALUES (:id,:org,'B',:id,'ACTIVE')"
            ),
            {"id": bot, "org": org},
        )
        c.execute(
            text(
                "INSERT INTO instances(id,organization_id,bot_id,provider,external_instance_id,phone_number,status) VALUES (:id,:org,:bot,'WUZAPI',:id,:phone,'ACTIVE')"
            ),
            {"id": inst, "org": org, "bot": bot, "phone": f"55{uuid.uuid4().int}"[:15]},
        )
        c.execute(
            text(
                "INSERT INTO users(id,organization_id,phone_number,status) VALUES (:id,:org,:phone,'ACTIVE')"
            ),
            {"id": user, "org": org, "phone": f"54{uuid.uuid4().int}"[:15]},
        )
    return org, inst, user


def _event(db, context, message_type="text"):
    org, inst, user = context
    event = Event(
        correlation_id=str(uuid.uuid4()),
        provider="WUZAPI",
        external_instance_id=inst,
        external_message_id=str(uuid.uuid4()),
        organization_id=org,
        instance_id=inst,
        user_id=user,
        message_type=message_type,
        status="ROUTED",
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def _validating_item(
    db: Session,
    context: tuple[str, str, str],
    *,
    claimed_by: str | None,
    lease_expires_at: datetime | None,
) -> ProcessingItem:
    org, inst, user = context
    source = _event(db, context, "image")
    item = ProcessingItem(
        event_id=source.id,
        correlation_id=source.correlation_id,
        organization_id=org,
        instance_id=inst,
        user_id=user,
        sequence=1,
        status="VALIDATING",
        claimed_by=claimed_by,
        lease_expires_at=lease_expires_at,
        heartbeat_at=datetime.now(timezone.utc) if claimed_by else None,
        attempt_count=1,
        message_received_at=datetime.now(timezone.utc),
        file_mime_type="image/jpeg",
        file_size=1,
        file_sha256="e" * 64,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def test_validating_prompt_rejects_wrong_worker(engine) -> None:
    context = _context(engine)
    with Session(engine) as db:
        item = _validating_item(
            db,
            context,
            claimed_by="worker-owner",
            lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        with pytest.raises(ValueError, match="current worker"):
            dispatch_user_prompt(
                db, item.id, "transaction_amount", worker_id="worker-other"
            )
        assert db.query(UserInteraction).count() == 0


def test_validating_prompt_rejects_expired_lease(engine) -> None:
    context = _context(engine)
    with Session(engine) as db:
        item = _validating_item(
            db,
            context,
            claimed_by="worker-owner",
            lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        with pytest.raises(ValueError, match="live lease"):
            dispatch_user_prompt(
                db, item.id, "transaction_amount", worker_id="worker-owner"
            )
        assert db.query(UserInteraction).count() == 0


def test_validating_prompt_rejects_missing_ownership(engine) -> None:
    context = _context(engine)
    with Session(engine) as db:
        item = _validating_item(
            db, context, claimed_by=None, lease_expires_at=None
        )
        with pytest.raises(ValueError, match="current worker"):
            dispatch_user_prompt(
                db, item.id, "transaction_amount", worker_id="worker-owner"
            )
        assert db.query(UserInteraction).count() == 0


def test_validating_prompt_accepts_matching_worker_with_live_lease(engine) -> None:
    context = _context(engine)
    with Session(engine) as db:
        item = _validating_item(
            db,
            context,
            claimed_by="worker-owner",
            lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        interaction = dispatch_user_prompt(
            db, item.id, "transaction_amount", worker_id="worker-owner"
        )
        assert interaction.status == "WAITING"
        assert db.query(UserInteraction).count() == 1


def test_waiting_prompt_replay_remains_idempotent_without_worker_claim(engine) -> None:
    context = _context(engine)
    with Session(engine) as db:
        item = _validating_item(
            db,
            context,
            claimed_by="worker-owner",
            lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        first = dispatch_user_prompt(
            db, item.id, "transaction_amount", worker_id="worker-owner"
        )
        replay = dispatch_user_prompt(db, item.id, "transaction_amount")
        assert replay.id == first.id
        assert replay.generation == first.generation
        assert db.query(UserInteraction).count() == 1


def test_command_selection_upserts_and_clear_deletes_binding(engine) -> None:
    context = _context(engine)
    enterprise_id = str(uuid.uuid4())
    with Session(engine) as db:
        event = _event(db, context)
        open_enterprise_command_session(
            db, *context, event.id, event.correlation_id, FakeClient(enterprise_id)
        )
        answer_event = _event(db, context)
        answer = apply_enterprise_command_answer(db, *context, answer_event.id, "1")
        assert answer.status == "APPLIED"
        binding = db.query(WhatsappChatEnterpriseBinding).one()
        assert binding.enterprise_id == enterprise_id
        second = _event(db, context)
        open_enterprise_command_session(
            db, *context, second.id, second.correlation_id, FakeClient(enterprise_id)
        )
        clear_event = _event(db, context)
        clear = apply_enterprise_command_answer(db, *context, clear_event.id, "2")
        assert clear.status == "APPLIED"
        assert db.query(WhatsappChatEnterpriseBinding).count() == 0


def test_invalid_duplicate_and_late_command_answers(engine) -> None:
    context = _context(engine)
    enterprise_id = str(uuid.uuid4())
    with Session(engine) as db:
        event = _event(db, context)
        open_enterprise_command_session(
            db, *context, event.id, event.correlation_id, FakeClient(enterprise_id)
        )
        bad_event = _event(db, context)
        rejected = apply_enterprise_command_answer(db, *context, bad_event.id, "99")
        assert rejected.status == "REJECTED"
        assert (
            apply_enterprise_command_answer(db, *context, bad_event.id, "1").id
            == rejected.id
        )
        good_event = _event(db, context)
        assert (
            apply_enterprise_command_answer(db, *context, good_event.id, "1").status
            == "APPLIED"
        )
        late_event = _event(db, context)
        assert (
            apply_enterprise_command_answer(db, *context, late_event.id, "1").status
            == "LATE"
        )


def test_document_enterprise_answer_materializes_item_only(engine) -> None:
    context = _context(engine)
    org, inst, user = context
    enterprise_id = str(uuid.uuid4())
    with Session(engine) as db:
        source = _event(db, context, "image")
        item = ProcessingItem(
            event_id=source.id,
            correlation_id=source.correlation_id,
            organization_id=org,
            instance_id=inst,
            user_id=user,
            sequence=1,
            status="VALIDATING",
            claimed_by="worker-1",
            lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            attempt_count=1,
            message_received_at=datetime.now(timezone.utc),
            file_mime_type="image/jpeg",
            file_size=1,
            file_sha256="a" * 64,
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        mapping = {"1": {"enterprise_id": enterprise_id, "display_name": "Empresa A"}}
        interaction = dispatch_user_prompt(
            db,
            item.id,
            "enterprise_selection",
            option_mapping=mapping,
            worker_id="worker-1",
        )
        assert interaction.option_mapping == mapping
        answer_event = _event(db, context)
        answer = apply_user_answer(db, answer_event.id, "1")
        assert answer.status == "APPLIED"
        db.refresh(item)
        assert item.enterprise_id == enterprise_id
        assert db.query(WhatsappChatEnterpriseBinding).count() == 0


def test_stale_binding_is_preserved_but_not_materialized(engine) -> None:
    context = _context(engine)
    org, inst, user = context
    stale_id = str(uuid.uuid4())
    with Session(engine) as db:
        source = _event(db, context, "image")
        item = ProcessingItem(
            event_id=source.id,
            correlation_id=source.correlation_id,
            organization_id=org,
            instance_id=inst,
            user_id=user,
            sequence=1,
            status="VALIDATING",
            claimed_by="worker-1",
            lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            attempt_count=1,
            message_received_at=datetime.now(timezone.utc),
            file_mime_type="image/jpeg",
            file_size=1,
            file_sha256="b" * 64,
        )
        db.add(item)
        db.add(
            WhatsappChatEnterpriseBinding(
                organization_id=org,
                instance_id=inst,
                user_id=user,
                enterprise_id=stale_id,
            )
        )
        db.commit()
        current_id = str(uuid.uuid4())
        client = FakeClient(current_id)
        assert (
            materialize_persistent_enterprise_binding(
                db, item, client, source.correlation_id
            )
            is None
        )
        db.refresh(item)
        assert item.enterprise_id is None
        assert db.query(WhatsappChatEnterpriseBinding).count() == 1
        assert item.status == "VALIDATING"
        mapping = build_enterprise_option_mapping(client, source.correlation_id)
        interaction = dispatch_user_prompt(
            db,
            item.id,
            "enterprise_selection",
            option_mapping=mapping,
            worker_id="worker-1",
        )
        db.refresh(item)
        assert item.status == "WAITING_USER_INPUT"
        assert item.enterprise_id is None
        assert interaction.option_mapping == {
            "1": {"enterprise_id": current_id, "display_name": "Empresa A"}
        }
        binding = db.query(WhatsappChatEnterpriseBinding).one()
        assert binding.enterprise_id == stale_id
        assert client.write_calls == 0


def test_missing_binding_creates_enterprise_selection_and_blocks_fifo(engine) -> None:
    context = _context(engine)
    current_id = str(uuid.uuid4())
    with Session(engine) as db:
        item = _validating_item(
            db,
            context,
            claimed_by="worker-1",
            lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        client = FakeClient(current_id)
        assert materialize_persistent_enterprise_binding(
            db, item, client, item.correlation_id
        ) is None
        mapping = build_enterprise_option_mapping(client, item.correlation_id)
        interaction = dispatch_user_prompt(
            db,
            item.id,
            "enterprise_selection",
            option_mapping=mapping,
            worker_id="worker-1",
        )
        source = _event(db, context, "image")
        ready = ProcessingItem(
            event_id=source.id,
            correlation_id=source.correlation_id,
            organization_id=context[0],
            instance_id=context[1],
            user_id=context[2],
            sequence=2,
            status="READY",
            attempt_count=0,
            message_received_at=datetime.now(timezone.utc),
            file_mime_type="image/jpeg",
            file_size=1,
            file_sha256="f" * 64,
        )
        db.add(ready)
        db.commit()
        assert interaction.question_type == "enterprise_selection"
        assert interaction.option_mapping == mapping
        assert item.status == "WAITING_USER_INPUT"
        assert client.write_calls == 0
        assert claim_next_ready_item(db, "worker-later") is None
        db.refresh(ready)
        assert ready.status == "READY"
        assert ready.attempt_count == 0


def test_current_binding_materializes_for_future_document(engine) -> None:
    context = _context(engine)
    org, inst, user = context
    enterprise_id = str(uuid.uuid4())
    with Session(engine) as db:
        source = _event(db, context, "image")
        item = ProcessingItem(
            event_id=source.id,
            correlation_id=source.correlation_id,
            organization_id=org,
            instance_id=inst,
            user_id=user,
            sequence=1,
            status="VALIDATING",
            claimed_by="worker-1",
            lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            attempt_count=1,
            message_received_at=datetime.now(timezone.utc),
            file_mime_type="image/jpeg",
            file_size=1,
            file_sha256="d" * 64,
        )
        db.add_all(
            [
                item,
                WhatsappChatEnterpriseBinding(
                    organization_id=org,
                    instance_id=inst,
                    user_id=user,
                    enterprise_id=enterprise_id,
                ),
            ]
        )
        db.commit()
        assert (
            materialize_persistent_enterprise_binding(
                db, item, FakeClient(enterprise_id), source.correlation_id
            )
            == enterprise_id
        )
        db.refresh(item)
        assert item.enterprise_id == enterprise_id


@pytest.mark.parametrize(
    "terminal_status",
    ["IGNORED", "COMPLETED", "FAILED", "PERSISTENCE_FAILED", "EXPIRED", "CANCELLED"],
)
def test_terminal_item_cannot_reserve_prompt(engine, terminal_status) -> None:
    context = _context(engine)
    org, inst, user = context
    with Session(engine) as db:
        source = _event(db, context, "image")
        item = ProcessingItem(
            event_id=source.id,
            correlation_id=source.correlation_id,
            organization_id=org,
            instance_id=inst,
            user_id=user,
            sequence=1,
            status=terminal_status,
            outcome_reason=(
                "INCOME_OUT_OF_SCOPE" if terminal_status == "IGNORED" else None
            ),
            attempt_count=1,
            message_received_at=datetime.now(timezone.utc),
            file_mime_type="image/jpeg",
            file_size=1,
            file_sha256="c" * 64,
        )
        db.add(item)
        db.commit()
        with pytest.raises(ValueError, match="not eligible"):
            dispatch_user_prompt(
                db, item.id, "transaction_amount", worker_id="worker-1"
            )
        assert db.query(UserInteraction).count() == 0


def test_reserved_command_recovery_reuses_generation_and_mapping(engine) -> None:
    context = _context(engine)
    enterprise_id = str(uuid.uuid4())
    with Session(engine) as db:
        event = _event(db, context)
        result = open_enterprise_command_session(
            db, *context, event.id, event.correlation_id, FakeClient(enterprise_id)
        )
        original_id = result.session.id
        original_mapping = dict(result.session.option_mapping)
        sends: list[str] = []
        assert (
            recover_reserved_enterprise_command_sessions(
                db, lambda session: sends.append(session.id) or True
            )
            == 1
        )
        db.refresh(result.session)
        assert result.session.id == original_id
        assert result.session.option_mapping == original_mapping
        assert result.session.status == "WAITING"
        assert sends == [original_id]


def test_reserved_command_expiry_releases_without_binding_change(engine) -> None:
    context = _context(engine)
    org, inst, user = context
    existing_enterprise = str(uuid.uuid4())
    with Session(engine) as db:
        source = _event(db, context, "image")
        ready = ProcessingItem(
            event_id=source.id,
            correlation_id=source.correlation_id,
            organization_id=org,
            instance_id=inst,
            user_id=user,
            sequence=1,
            status="READY",
            attempt_count=0,
            message_received_at=datetime.now(timezone.utc),
            file_mime_type="image/jpeg",
            file_size=1,
            file_sha256="e" * 64,
        )
        db.add_all(
            [
                ready,
                WhatsappChatEnterpriseBinding(
                    organization_id=org,
                    instance_id=inst,
                    user_id=user,
                    enterprise_id=existing_enterprise,
                ),
            ]
        )
        db.commit()
        event = _event(db, context)
        result = open_enterprise_command_session(
            db, *context, event.id, event.correlation_id, FakeClient(str(uuid.uuid4()))
        )
        assert claim_next_ready_item(db, "worker-before-expiry") is None
        result.session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        assert expire_enterprise_command_sessions(db) == 1
        db.refresh(result.session)
        assert result.session.status == "EXPIRED"
        binding = db.query(WhatsappChatEnterpriseBinding).one()
        assert binding.enterprise_id == existing_enterprise
        claimed = claim_next_ready_item(db, "worker-after-expiry")
        assert claimed is not None and claimed.id == ready.id


def test_outcome_unknown_answer_uses_stable_mapping_without_resend(engine) -> None:
    context = _context(engine)
    enterprise_id = str(uuid.uuid4())
    with Session(engine) as db:
        event = _event(db, context)
        result = open_enterprise_command_session(
            db, *context, event.id, event.correlation_id, FakeClient(enterprise_id)
        )
        original_mapping = dict(result.session.option_mapping)
        original_outbound = result.session.outbound_message_id
        result.session.status = "OUTBOUND_OUTCOME_UNKNOWN"
        result.session.waiting_since = datetime.now(timezone.utc)
        db.commit()
        answer_event = _event(db, context)
        answer = apply_enterprise_command_answer(db, *context, answer_event.id, "1")
        assert answer.status == "APPLIED"
        db.refresh(result.session)
        assert result.session.option_mapping == original_mapping
        assert result.session.outbound_message_id == original_outbound
        assert result.session.status == "ANSWERED"


def test_concurrent_same_event_command_answer_is_applied_once(engine) -> None:
    context = _context(engine)
    enterprise_id = str(uuid.uuid4())
    with Session(engine) as db:
        event = _event(db, context)
        open_enterprise_command_session(
            db, *context, event.id, event.correlation_id, FakeClient(enterprise_id)
        )
        answer_event_id = _event(db, context).id

    def apply_once() -> tuple[str, str]:
        with Session(engine) as db:
            answer = apply_enterprise_command_answer(db, *context, answer_event_id, "1")
            return answer.id, answer.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            future.result(timeout=10)
            for future in [pool.submit(apply_once), pool.submit(apply_once)]
        ]
    assert results[0] == results[1]
    assert results[0][1] == "APPLIED"
    with Session(engine) as db:
        assert db.query(WhatsappChatEnterpriseBinding).count() == 1
