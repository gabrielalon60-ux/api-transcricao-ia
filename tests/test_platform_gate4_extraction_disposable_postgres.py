from __future__ import annotations

import os
import uuid
import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine, text
from alembic.config import Config
from alembic import command

from db.models import Event, ProcessingItem
from orchestrator.main import extract_file_info
from orchestrator.repositories.queue_repository import create_processable_processing_item
from orchestrator.services.extraction_dispatcher import (
    claim_next_received_item_for_extraction,
    claim_expired_extracting_item_for_recovery,
    apply_extraction_success,
    apply_extraction_failure,
)

ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = ROOT / "packages" / "db" / "alembic.ini"
DISPOSABLE_DB_URL = "postgresql://postgres:postgres@localhost:55432/platform_gate4_test"


@pytest.fixture(scope="module")
def disposable_postgres():
    os.environ["GATE4_DISPOSABLE_DATABASE_URL"] = DISPOSABLE_DB_URL
    engine = create_engine(DISPOSABLE_DB_URL, pool_size=20, max_overflow=10, connect_args={"connect_timeout": 2})

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"Disposable PostgreSQL container at {DISPOSABLE_DB_URL} is not accessible: {exc}")

    # Run Alembic Upgrade Head
    alembic_cfg = Config(str(ALEMBIC_INI))
    alembic_cfg.set_main_option("sqlalchemy.url", DISPOSABLE_DB_URL)
    command.upgrade(alembic_cfg, "head")

    yield engine

    # Clean up test rows
    with engine.connect() as conn:
        conn.execute(text("TRUNCATE service_usage, executions, processing_items, conversation_queue_counters, events, registration_rate_limits, registration_attempts, instances, users, bots, organizations CASCADE;"))
        conn.commit()


def setup_test_context(engine):
    org_id, bot_id, inst_id, user_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    unique_suffix = str(uuid.uuid4().int)[:8]
    inst_phone = f"551199{unique_suffix}"
    user_phone = f"551198{unique_suffix}"
    with engine.connect() as conn:
        conn.execute(text("INSERT INTO organizations (id, name, slug, status) VALUES (:id, 'Org Test', :slug, 'ACTIVE')"), {"id": org_id, "slug": f"slug-{org_id}"})
        conn.execute(text("INSERT INTO bots (id, organization_id, name, service_key, status) VALUES (:id, :org_id, 'Bot Test', :key, 'ACTIVE')"), {"id": bot_id, "org_id": org_id, "key": f"key-{bot_id}"})
        conn.execute(text("INSERT INTO instances (id, organization_id, bot_id, provider, external_instance_id, phone_number, status) VALUES (:id, :org_id, :bot_id, 'WUZAPI', :ext, :phone, 'ACTIVE')"), {"id": inst_id, "org_id": org_id, "bot_id": bot_id, "ext": f"ext-{inst_id}", "phone": inst_phone})
        conn.execute(text("INSERT INTO users (id, organization_id, phone_number, status) VALUES (:id, :org_id, :phone, 'ACTIVE')"), {"id": user_id, "org_id": org_id, "phone": user_phone})
        conn.commit()
    return org_id, inst_id, user_id


def test_1_end_to_end_wuzapi_webhook_persists_minimal_media_ref(disposable_postgres):
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)

    wuzapi_payload = {
        "instanceId": f"ext-{inst_id}",
        "provider": "WUZAPI",
        "data": {
            "message": {
                "key": {"id": f"msg-wuz-{uuid.uuid4()}", "remoteJid": "5511999999999@s.whatsapp.net"},
                "imageMessage": {
                    "mimetype": "image/jpeg",
                    "fileLength": 2048,
                    "fileSha256": "abc123sha256",
                    "directPath": "/v/t62.7118-24/123456_n.enc",
                    "mediaKey": "sensitivekey_not_persisted",
                    "url": "http://untrusted.cdn.com/123",
                },
            }
        },
    }

    file_info = extract_file_info(wuzapi_payload, "image", text_content=None)
    assert file_info["media_ref"] is not None
    assert file_info["media_ref"]["direct_path"] == "/v/t62.7118-24/123456_n.enc"
    # Plaintext media_key and raw url REMOVED from persisted media_ref
    assert "media_key" not in file_info["media_ref"]
    assert "url" not in file_info["media_ref"]

    evt_id = str(uuid.uuid4())
    from sqlalchemy.orm import Session
    with Session(disposable_postgres) as session:
        evt = Event(id=evt_id, correlation_id="c-wuz", provider="WUZAPI", external_instance_id=f"ext-{inst_id}", external_message_id="msg-wuz-test", organization_id=org_id, instance_id=inst_id, user_id=user_id, message_type="image", status="RECEIVED")
        session.add(evt)
        session.flush()

        item = create_processable_processing_item(session, evt, org_id, inst_id, user_id, sequence=1, file_info=file_info)
        item_id = item.id
        session.commit()

    with Session(disposable_postgres) as session:
        claimed = claim_next_received_item_for_extraction(session, dispatcher_id="wuz-w1")
        assert claimed is not None
        assert claimed.id == item_id
        assert claimed.extraction_claim_token.startswith("claim-")
        assert claimed.media_ref["direct_path"] == "/v/t62.7118-24/123456_n.enc"


def test_2_extraction_claim_token_and_attempt_reset(disposable_postgres):
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    evt_id, item_id = str(uuid.uuid4()), str(uuid.uuid4())
    from sqlalchemy.orm import Session
    with Session(disposable_postgres) as session:
        evt = Event(id=evt_id, correlation_id=f"c-{evt_id}", provider="WUZAPI", external_instance_id=f"ext-{inst_id}", external_message_id=f"msg-{evt_id}", organization_id=org_id, instance_id=inst_id, user_id=user_id, message_type="image", status="RECEIVED")
        session.add(evt)
        session.flush()

        item = ProcessingItem(
            id=item_id,
            event_id=evt_id,
            correlation_id=f"c-{evt_id}",
            organization_id=org_id,
            instance_id=inst_id,
            user_id=user_id,
            sequence=1,
            status="EXTRACTING",
            message_received_at=datetime.now(timezone.utc),
            file_mime_type="image/jpeg",
            file_size=1024,
            file_sha256="sha256-test",
            original_filename="receipt.jpg",
            extraction_claim_token="claim-valid-123",
            attempt_count=2,
        )
        session.add(item)
        session.commit()

    # 1. Stale token response is ignored cleanly
    with Session(disposable_postgres) as session:
        payload = {"document_type": "invoice", "extraction": {"v": 100}, "normalization": {"amount": 100}}
        stale_res = apply_extraction_success(session, item_id, dispatched_claim_token="claim-OLD-STALE", extraction_payload=payload)
        assert stale_res is None

    # 2. Valid token response applies, clears token, and resets attempt_count to 0 at READY
    with Session(disposable_postgres) as session:
        payload = {"document_type": "invoice", "extraction": {"v": 100}, "normalization": {"amount": 100}}
        valid_res = apply_extraction_success(session, item_id, dispatched_claim_token="claim-valid-123", extraction_payload=payload)
        assert valid_res is not None
        assert valid_res.status == "READY"
        assert valid_res.attempt_count == 0  # Attempt counter reset to 0 at READY
        assert valid_res.extraction_claim_token is None


def test_3_stale_failure_guard_and_recovery(disposable_postgres):
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    evt_id, item_id = str(uuid.uuid4()), str(uuid.uuid4())
    from sqlalchemy.orm import Session
    with Session(disposable_postgres) as session:
        evt = Event(id=evt_id, correlation_id=f"c-{evt_id}", provider="WUZAPI", external_instance_id=f"ext-{inst_id}", external_message_id=f"msg-{evt_id}", organization_id=org_id, instance_id=inst_id, user_id=user_id, message_type="image", status="RECEIVED")
        session.add(evt)
        session.flush()

        item = ProcessingItem(
            id=item_id,
            event_id=evt_id,
            correlation_id=f"c-{evt_id}",
            organization_id=org_id,
            instance_id=inst_id,
            user_id=user_id,
            sequence=1,
            status="EXTRACTING",
            message_received_at=datetime.now(timezone.utc),
            file_mime_type="image/jpeg",
            file_size=1024,
            file_sha256="sha256-test",
            original_filename="receipt.jpg",
            extraction_claim_token="claim-rec-999",
            lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=10),
            attempt_count=1,
        )
        session.add(item)
        session.commit()

    # Stale failure from older token ignored
    with Session(disposable_postgres) as session:
        fail_res = apply_extraction_failure(session, item_id, dispatched_claim_token="claim-OLD-TOKEN", error_code="HTTP_500", retryable=True)
        assert fail_res is None

    # Recovery worker claims expired item with new token
    with Session(disposable_postgres) as session:
        recovered = claim_expired_extracting_item_for_recovery(session, dispatcher_id="rec-2")
        assert recovered is not None
        assert recovered.extraction_claim_token.startswith("claim-rec-")
        assert recovered.attempt_count == 2
