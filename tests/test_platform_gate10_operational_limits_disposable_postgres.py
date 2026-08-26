from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from db.models import Event, ProcessingItem
from orchestrator.services.fifo_worker_service import claim_next_ready_item
from orchestrator.services.ingestion_service import IngestionOutcome, ingest_event_transaction

pytestmark = pytest.mark.real_pg15


ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.environ.get("GATE10_DISPOSABLE_DATABASE_URL", "postgresql://postgres:postgres@localhost:55432/platform_gate10_test")


@pytest.fixture(scope="module")
def database():
    engine = create_engine(DATABASE_URL, connect_args={"connect_timeout": 2})
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"disposable PostgreSQL 15 is unavailable: {exc}")
    config = Config(str(ROOT / "packages" / "db" / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", DATABASE_URL)
    command.upgrade(config, "head")
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def clean(database):
    yield
    with database.begin() as connection:
        connection.execute(text("TRUNCATE service_usage, executions, processing_items, conversation_queue_counters, events, registration_rate_limits, registration_attempts, instances, users, bots, organizations CASCADE"))


def _context(database, users: int = 1) -> tuple[str, str, list[str]]:
    organization, bot, instance = (str(uuid.uuid4()) for _ in range(3))
    user_ids = [str(uuid.uuid4()) for _ in range(users)]
    with database.begin() as connection:
        connection.execute(text("INSERT INTO organizations (id,name,slug,status) VALUES (:id,'Gate10',:slug,'ACTIVE')"), {"id": organization, "slug": f"g10-{organization}"})
        connection.execute(text("INSERT INTO bots (id,organization_id,name,service_key,status) VALUES (:id,:org,'Gate10',:key,'ACTIVE')"), {"id": bot, "org": organization, "key": f"key-{bot}"})
        connection.execute(text("INSERT INTO instances (id,organization_id,bot_id,provider,external_instance_id,phone_number,status) VALUES (:id,:org,:bot,'WUZAPI',:external,:phone,'ACTIVE')"), {"id": instance, "org": organization, "bot": bot, "external": f"ext-{instance}", "phone": "55" + str(uuid.uuid4().int)[:11]})
        for index, user in enumerate(user_ids):
            connection.execute(text("INSERT INTO users (id,organization_id,phone_number,status) VALUES (:id,:org,:phone,'ACTIVE')"), {"id": user, "org": organization, "phone": "56" + str(uuid.uuid4().int + index)[:11]})
    return organization, instance, user_ids


def _event(organization: str, instance: str, user: str) -> Event:
    identity = str(uuid.uuid4())
    return Event(id=identity, correlation_id=f"c-{identity}", provider="WUZAPI", external_instance_id=f"ext-{instance}", external_message_id=f"msg-{identity}", organization_id=organization, instance_id=instance, user_id=user, message_type="image", status="RECEIVED")


def _file() -> dict[str, object]:
    return {"mime_type": "image/jpeg", "size": 100, "sha256": uuid.uuid4().hex, "filename": "document.jpg"}


def test_organization_outstanding_limit_rejects_before_conversation_capacity(database) -> None:
    organization, instance, users = _context(database, 2)
    with Session(database) as session:
        first = ingest_event_transaction(session, _event(organization, instance, users[0]), organization, instance, users[0], _file(), max_organization_outstanding_limit=1)
    with Session(database) as session:
        second = ingest_event_transaction(session, _event(organization, instance, users[1]), organization, instance, users[1], _file(), max_organization_outstanding_limit=1)
        second_error_code = second.item.error_code if second.item is not None else None
    assert first.outcome is IngestionOutcome.CREATED
    assert second.outcome is IngestionOutcome.CAPACITY_REJECTED
    assert second_error_code == "ORGANIZATION_CAPACITY_EXCEEDED"


def _insert_item(database, organization: str, instance: str, user: str, status: str, received: datetime) -> str:
    event = _event(organization, instance, user)
    item_id = str(uuid.uuid4())
    with Session(database) as session:
        session.add(event)
        session.flush()
        session.add(ProcessingItem(id=item_id, event_id=event.id, correlation_id=event.correlation_id, organization_id=organization, instance_id=instance, user_id=user, sequence=1, status=status, message_received_at=received, file_mime_type="image/jpeg", file_size=100, file_sha256=uuid.uuid4().hex, original_filename="document.jpg", attempt_count=1 if status != "READY" else 0))
        session.commit()
    return item_id


def test_saturated_oldest_organization_does_not_starve_next_eligible(database) -> None:
    org_a, instance_a, users_a = _context(database, 21)
    org_b, instance_b, users_b = _context(database, 1)
    now = datetime.now(UTC)
    for index in range(20):
        _insert_item(database, org_a, instance_a, users_a[index], "ACTIVE", now - timedelta(minutes=30 - index))
    _insert_item(database, org_a, instance_a, users_a[20], "READY", now - timedelta(minutes=2))
    expected = _insert_item(database, org_b, instance_b, users_b[0], "READY", now - timedelta(minutes=1))
    with Session(database) as session:
        claimed = claim_next_ready_item(session, "gate10-worker", max_organization_active_items=20)
        assert claimed is not None and claimed.id == expected
        session.rollback()
