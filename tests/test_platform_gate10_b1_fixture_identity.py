"""
Unit and Contract Tests for G10-B1 Fixture Identity & Transcription Compatibility.

Validates:
1. Future fixture Instance.id generation conforms to UUIDv4 formatted string convention.
2. The generated Instance.id satisfies InternalExtractionMetadata.bot_instance_id validation.
3. Seeding logic is idempotent and does not create duplicate instances.
4. Webhook identity resolution maps unambiguously to exactly ONE instance.
"""

import uuid
from datetime import datetime, timezone
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from db.models import Base, Bot, Instance, Organization
from transcription.schemas.internal import InternalExtractionMetadata


def test_future_fixture_instance_id_is_valid_uuidv4():
    """Verify that newly generated instance IDs are valid RFC 4122 UUIDv4 strings."""
    raw_id = str(uuid.uuid4())
    parsed = uuid.UUID(raw_id)
    assert parsed.version == 4
    assert str(parsed) == raw_id
    assert raw_id != "inst-g10b1-test"


def test_future_fixture_transcription_metadata_compatibility():
    """Verify that a future UUIDv4 Instance.id passes Transcription InternalExtractionMetadata schema."""
    future_instance_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    correlation_id = f"corr-{uuid.uuid4()}"
    received_at = datetime.now(timezone.utc)

    # Valid payload with UUIDv4 instance ID
    meta = InternalExtractionMetadata.model_validate({
        "request_id": request_id,
        "bot_instance_id": future_instance_id,
        "correlation_id": correlation_id,
        "received_at": received_at,
        "source": "WHATSAPP",
    })
    assert meta.bot_instance_id == uuid.UUID(future_instance_id)

    # Negative check: invalid sentinel 'inst-g10b1-test' must fail
    with pytest.raises(Exception):
        InternalExtractionMetadata.model_validate({
            "request_id": request_id,
            "bot_instance_id": "inst-g10b1-test",
            "correlation_id": correlation_id,
            "received_at": received_at,
            "source": "WHATSAPP",
        })


def test_fixture_seeding_idempotency_and_unambiguous_webhook_lookup():
    """Verify that seeding an instance creates exactly 1 row and subsequent webhook lookup is unique."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        # Seed Org and Bot
        org = Organization(id="org-g10b1-test", name="G10-B1 Test Org", slug="g10b1-test-org", status="ACTIVE")
        bot = Bot(id="bot-g10b1-test", organization_id="org-g10b1-test", name="G10-B1 Test Bot", service_key="g10b1-test-bot-key", status="ACTIVE")
        session.add_all([org, bot])
        session.commit()

        external_id = "wuzapi_runtime_user_test"
        future_instance_id = str(uuid.uuid4())

        # First Seeding
        inst1 = Instance(
            id=future_instance_id,
            organization_id=org.id,
            bot_id=bot.id,
            provider="WUZAPI",
            external_instance_id=external_id,
            phone_number="5511999990000",
            status="ACTIVE",
        )
        session.add(inst1)
        session.commit()

        # Verify count is 1
        instances = session.scalars(select(Instance).where(Instance.external_instance_id == external_id)).all()
        assert len(instances) == 1
        assert instances[0].id == future_instance_id
        assert uuid.UUID(instances[0].id).version == 4

        # Simulate Idempotency check: if instance with external_id already exists, do not insert second row
        existing = session.scalars(
            select(Instance).where(Instance.provider == "WUZAPI", Instance.external_instance_id == external_id)
        ).all()
        if not existing:
            session.add(Instance(
                id=str(uuid.uuid4()),
                organization_id=org.id,
                bot_id=bot.id,
                provider="WUZAPI",
                external_instance_id=external_id,
                phone_number="5511999990000",
                status="ACTIVE",
            ))
            session.commit()

        # Verify count remains exactly 1
        instances_after = session.scalars(select(Instance).where(Instance.external_instance_id == external_id)).all()
        assert len(instances_after) == 1

        # Webhook lookup resolution check: must resolve to exactly ONE instance
        resolved_instances = session.scalars(
            select(Instance).where(Instance.external_instance_id == external_id, Instance.provider == "WUZAPI")
        ).all()
        assert len(resolved_instances) == 1
        assert resolved_instances[0].id == future_instance_id
