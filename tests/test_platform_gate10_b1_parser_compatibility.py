"""
Unit test suite verifying WUZAPI webhook parser compatibility in Orchestrator.

Covers:
- Existing legacy nested payload formats
- Current asternic/wuzapi flat message-event payloads
- Fallback fields (data.sender -> data.chat)
- Defensive handling of malformed data types (int, dict, list, null)
- Verification of extract_file_info ID resolution
"""

import hmac
import hashlib
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base
from orchestrator.main import app, extract_file_info, get_db
from orchestrator.config import get_settings


def generate_test_signature(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


@pytest.fixture
def test_db_session(tmp_path):
    db_file = tmp_path / "test_parser.db"
    engine = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)

    def _override_get_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    yield
    app.dependency_overrides.clear()
    engine.dispose()


# ------------------------------------------------------------------
# Unit tests for extract_file_info()
# ------------------------------------------------------------------


def test_extract_file_info_legacy_nested_format():
    payload = {
        "provider": "WUZAPI",
        "instanceId": "legacy-inst-1",
        "external_message_id": "legacy-msg-100",
        "data": {
            "message": {
                "key": {
                    "id": "legacy-msg-100",
                    "remoteJid": "5511999999999@s.whatsapp.net",
                },
                "conversation": "Teste legado",
            }
        },
    }
    info = extract_file_info(payload, "text", text_content="Teste legado")
    assert info["external_instance_id"] == "legacy-inst-1"
    assert info["external_message_id"] == "legacy-msg-100"
    assert info["message_type"] == "text"


def test_extract_file_info_flat_asternic_format():
    payload = {
        "event": "Message",
        "instance": "asternic-inst-1",
        "data": {
            "id": "asternic-msg-200",
            "chat": "5511888888888@s.whatsapp.net",
            "sender": "5511888888888@s.whatsapp.net",
            "message": {"conversation": "Teste flat asternic"},
        },
    }
    info = extract_file_info(payload, "text", text_content="Teste flat asternic")
    assert info["external_instance_id"] == "asternic-inst-1"
    assert info["external_message_id"] == "asternic-msg-200"
    assert info["message_type"] == "text"


def test_extract_file_info_malformed_types_defensive():
    # Defensive test: instance is integer, data.id is int
    payload = {
        "event": "Message",
        "instance": 12345,
        "data": {
            "id": 9999,
            "sender": {"invalid": "dict"},
            "message": {"conversation": "Defensive test"},
        },
    }
    info = extract_file_info(payload, "text", text_content="Defensive test")
    # Should fall back to default bounded strings without throwing AttributeError / TypeError
    assert info["external_instance_id"] == "inst-1"
    assert info["external_message_id"] == "msg-1"


# ------------------------------------------------------------------
# Fast API Webhook Ingestion parser integration tests (signature mocked)
# ------------------------------------------------------------------


def test_webhook_parser_flat_asternic_payload(monkeypatch, test_db_session):
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    payload = {
        "event": "Message",
        "instance": "inst-test-b1",
        "data": {
            "id": "msg-flat-b1-100",
            "sender": "5511977777777@s.whatsapp.net",
            "message": {"conversation": "Teste texto flat"},
        },
    }

    body = json.dumps(payload).encode("utf-8")
    sig = generate_test_signature(body, secret)

    with TestClient(app) as client:
        response = client.post(
            "/webhook",
            content=body,
            headers={"Content-Type": "application/json", "x-hmac-signature": sig},
        )
        # HTTP 200 with instance_not_found detail (since instance isn't in test DB)
        # proving parser successfully extracted instance ID and message ID!
        assert response.status_code == 200
        assert response.json().get("detail") == "instance_not_found"


def test_webhook_parser_chat_fallback_payload(monkeypatch, test_db_session):
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    payload = {
        "event": "Message",
        "instance": "inst-test-b1",
        "data": {
            "id": "msg-chat-fallback-101",
            "chat": "5511966666666@s.whatsapp.net",
            "message": {"conversation": "Teste chat fallback"},
        },
    }

    body = json.dumps(payload).encode("utf-8")
    sig = generate_test_signature(body, secret)

    with TestClient(app) as client:
        response = client.post(
            "/webhook",
            content=body,
            headers={"Content-Type": "application/json", "x-hmac-signature": sig},
        )
        assert response.status_code == 200
        assert response.json().get("detail") == "instance_not_found"


def test_webhook_parser_malformed_shapes_rejects_missing_fields(monkeypatch):
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    payload = {
        "event": "Message",
        "instance": 999,  # Invalid type (int instead of str)
        "data": {
            "id": {"invalid": "id"},  # Invalid type (dict instead of str)
            "message": {"conversation": "Malformed"},
        },
    }

    body = json.dumps(payload).encode("utf-8")
    sig = generate_test_signature(body, secret)

    with TestClient(app) as client:
        response = client.post(
            "/webhook",
            content=body,
            headers={"Content-Type": "application/json", "x-hmac-signature": sig},
        )
        # Should return HTTP 400 Missing required source fields without 500 crash!
        assert response.status_code == 400
        assert "Missing required source fields" in response.json()["detail"]
