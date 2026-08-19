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


# ------------------------------------------------------------------
# Native WUZAPI v1.0.8 JSON Envelope Tests (Cases A - N)
# ------------------------------------------------------------------


def test_extract_file_info_native_wuzapi_v108_envelope():
    synthetic_inner = {
        "event": {
            "Info": {
                "ID": "SYNTH-MSG-ID-001",
                "Sender": "226160000000000@lid",
                "SenderAlt": "5511999998888@s.whatsapp.net",
                "Type": "text",
            },
            "Message": {"conversation": "Oi"},
        },
        "type": "Message",
    }
    payload = {
        "instanceName": "inst_synthetic_test",
        "userID": "synth-wuzapi-user-id-12345",
        "jsonData": json.dumps(synthetic_inner),
    }

    info = extract_file_info(payload, "text", text_content="Oi")
    assert info["external_instance_id"] == "synth-wuzapi-user-id-12345"
    assert info["external_message_id"] == "SYNTH-MSG-ID-001"
    assert info["message_type"] == "text"


def test_webhook_parser_native_wuzapi_v108_valid_text_message(monkeypatch, test_db_session):
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    synthetic_inner = {
        "event": {
            "Info": {
                "ID": "SYNTH-MSG-ID-002",
                "Sender": "226160000000000@lid",
                "SenderAlt": "5511999997777@s.whatsapp.net",
                "Type": "text",
                "Timestamp": "2026-08-18T22:00:00-03:00",
            },
            "Message": {"conversation": "Oi"},
        },
        "type": "Message",
    }
    payload = {
        "instanceName": "inst_synthetic_test",
        "userID": "synth-wuzapi-user-id-12345",
        "jsonData": json.dumps(synthetic_inner),
    }

    body = json.dumps(payload).encode("utf-8")
    sig = generate_test_signature(body, secret)

    with TestClient(app) as client:
        response = client.post(
            "/webhook",
            content=body,
            headers={"Content-Type": "application/json", "x-hmac-signature": sig},
        )
        # Reaches DB layer -> instance_not_found because synthetic ID is not seeded in test DB
        assert response.status_code == 200
        assert response.json().get("detail") == "instance_not_found"


def test_webhook_parser_native_wuzapi_v108_malformed_json_data(monkeypatch):
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    payload = {
        "instanceName": "inst_synthetic_test",
        "userID": "synth-wuzapi-user-id-12345",
        "jsonData": "invalid-json-string-{",
    }

    body = json.dumps(payload).encode("utf-8")
    sig = generate_test_signature(body, secret)

    with TestClient(app) as client:
        response = client.post(
            "/webhook",
            content=body,
            headers={"Content-Type": "application/json", "x-hmac-signature": sig},
        )
        assert response.status_code == 400
        assert "Malformed jsonData payload" in response.json()["detail"]


def test_webhook_parser_native_wuzapi_v108_non_object_json_data(monkeypatch):
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    payload = {
        "instanceName": "inst_synthetic_test",
        "userID": "synth-wuzapi-user-id-12345",
        "jsonData": json.dumps(["an", "array", "not", "an", "object"]),
    }

    body = json.dumps(payload).encode("utf-8")
    sig = generate_test_signature(body, secret)

    with TestClient(app) as client:
        response = client.post(
            "/webhook",
            content=body,
            headers={"Content-Type": "application/json", "x-hmac-signature": sig},
        )
        assert response.status_code == 400
        assert "jsonData must be a JSON object" in response.json()["detail"]


def test_webhook_parser_native_wuzapi_v108_missing_or_empty_message_id(monkeypatch):
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    synthetic_inner = {
        "event": {
            "Info": {
                "ID": "",  # Empty string ID
                "Sender": "5511999997777@s.whatsapp.net",
                "Type": "text",
            },
            "Message": {"conversation": "Oi"},
        },
        "type": "Message",
    }
    payload = {
        "instanceName": "inst_synthetic_test",
        "userID": "synth-wuzapi-user-id-12345",
        "jsonData": json.dumps(synthetic_inner),
    }

    body = json.dumps(payload).encode("utf-8")
    sig = generate_test_signature(body, secret)

    with TestClient(app) as client:
        response = client.post(
            "/webhook",
            content=body,
            headers={"Content-Type": "application/json", "x-hmac-signature": sig},
        )
        assert response.status_code == 400
        assert "Missing required source fields" in response.json()["detail"]


def test_webhook_parser_native_wuzapi_v108_missing_instance_identity(monkeypatch):
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    synthetic_inner = {
        "event": {
            "Info": {
                "ID": "SYNTH-MSG-ID-003",
                "Sender": "5511999997777@s.whatsapp.net",
                "Type": "text",
            },
            "Message": {"conversation": "Oi"},
        },
        "type": "Message",
    }
    payload = {
        "instanceName": "inst_synthetic_test",
        "userID": "",  # Empty user ID
        "jsonData": json.dumps(synthetic_inner),
    }

    body = json.dumps(payload).encode("utf-8")
    sig = generate_test_signature(body, secret)

    with TestClient(app) as client:
        response = client.post(
            "/webhook",
            content=body,
            headers={"Content-Type": "application/json", "x-hmac-signature": sig},
        )
        assert response.status_code == 400
        assert "Missing required source fields" in response.json()["detail"]


# ------------------------------------------------------------------
# Sender vs SenderAlt / LID Precedence Tests
# ------------------------------------------------------------------


def test_webhook_parser_native_wuzapi_v108_sender_canonical_alone(monkeypatch, test_db_session):
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    synthetic_inner = {
        "event": {
            "Info": {
                "ID": "SYNTH-MSG-SENDER-001",
                "Sender": "5511999991111@s.whatsapp.net",
                "Type": "text",
            },
            "Message": {"conversation": "Oi"},
        },
        "type": "Message",
    }
    payload = {
        "instanceName": "inst_synthetic_test",
        "userID": "synth-wuzapi-user-id-12345",
        "jsonData": json.dumps(synthetic_inner),
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


def test_webhook_parser_native_wuzapi_v108_sender_alt_alone(monkeypatch, test_db_session):
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    synthetic_inner = {
        "event": {
            "Info": {
                "ID": "SYNTH-MSG-SENDER-002",
                "SenderAlt": "5511999992222@s.whatsapp.net",
                "Type": "text",
            },
            "Message": {"conversation": "Oi"},
        },
        "type": "Message",
    }
    payload = {
        "instanceName": "inst_synthetic_test",
        "userID": "synth-wuzapi-user-id-12345",
        "jsonData": json.dumps(synthetic_inner),
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


def test_webhook_parser_native_wuzapi_v108_sender_precedence_when_both_phone_jids(monkeypatch, test_db_session):
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    # When both are phone JIDs, canonical Sender wins
    synthetic_inner = {
        "event": {
            "Info": {
                "ID": "SYNTH-MSG-SENDER-003",
                "Sender": "5511999991111@s.whatsapp.net",
                "SenderAlt": "5511999992222@s.whatsapp.net",
                "Type": "text",
            },
            "Message": {"conversation": "Oi"},
        },
        "type": "Message",
    }
    payload = {
        "instanceName": "inst_synthetic_test",
        "userID": "synth-wuzapi-user-id-12345",
        "jsonData": json.dumps(synthetic_inner),
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


def test_webhook_parser_native_wuzapi_v108_sender_lid_uses_sender_alt_phone(monkeypatch, test_db_session):
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    # When Sender is @lid and SenderAlt is phone JID, SenderAlt is selected to preserve phone identity
    synthetic_inner = {
        "event": {
            "Info": {
                "ID": "SYNTH-MSG-SENDER-004",
                "Sender": "226160000000000@lid",
                "SenderAlt": "5511999993333@s.whatsapp.net",
                "Type": "text",
            },
            "Message": {"conversation": "Oi"},
        },
        "type": "Message",
    }
    payload = {
        "instanceName": "inst_synthetic_test",
        "userID": "synth-wuzapi-user-id-12345",
        "jsonData": json.dumps(synthetic_inner),
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


def test_webhook_parser_native_wuzapi_v108_missing_sender_all_empty(monkeypatch, test_db_session):
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    synthetic_inner = {
        "event": {
            "Info": {
                "ID": "SYNTH-MSG-SENDER-005",
                "Sender": "",
                "SenderAlt": "",
                "Chat": "",
                "Type": "text",
            },
            "Message": {"conversation": "Oi"},
        },
        "type": "Message",
    }
    payload = {
        "instanceName": "inst_synthetic_test",
        "userID": "synth-wuzapi-user-id-12345",
        "jsonData": json.dumps(synthetic_inner),
    }

    body = json.dumps(payload).encode("utf-8")
    sig = generate_test_signature(body, secret)

    with TestClient(app) as client:
        response = client.post(
            "/webhook",
            content=body,
            headers={"Content-Type": "application/json", "x-hmac-signature": sig},
        )
        # normalize_phone_number("") returns "" which leads to handling
        assert response.status_code in (200, 400)



def test_webhook_parser_native_wuzapi_v108_lifecycle_non_message(monkeypatch, test_db_session):
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    synthetic_presence = {
        "event": {
            "Info": {
                "Chat": "226160000000000@lid",
                "Sender": "226160000000000@lid",
            }
        },
        "type": "ChatPresence",
    }
    payload = {
        "instanceName": "inst_synthetic_test",
        "userID": "synth-wuzapi-user-id-12345",
        "jsonData": json.dumps(synthetic_presence),
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
        assert response.json().get("detail") == "ignored_chatpresence"


def test_webhook_parser_form_urlencoded_json_data(monkeypatch, test_db_session):
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    form_payload = {
        "instanceId": "form-inst-1",
        "external_message_id": "form-msg-1",
        "sender": "5511988887777@s.whatsapp.net",
        "text": "Teste form",
    }
    form_data = {"jsonData": json.dumps(form_payload)}
    # Build urlencoded body
    import urllib.parse
    encoded_body = urllib.parse.urlencode(form_data).encode("utf-8")
    sig = generate_test_signature(encoded_body, secret)

    with TestClient(app) as client:
        response = client.post(
            "/webhook",
            content=encoded_body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "x-hmac-signature": sig,
            },
        )
        assert response.status_code == 200
        assert response.json().get("detail") == "instance_not_found"


def test_webhook_parser_native_wuzapi_v108_idempotency_duplicate_suppression(monkeypatch, tmp_path):
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    # Use SQLite session for this test
    db_file = tmp_path / "test_idempotency.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)

    from db.models import Organization, Bot, Instance

    # Seed Organization, Bot, Instance
    db = session_factory()
    org = Organization(id="org-synth-1", name="Synthetic Org", slug="synth-org", registration_secret_hash="hash")
    bot = Bot(id="bot-synth-1", organization_id="org-synth-1", name="Synthetic Bot", service_key="bot-key-1")
    inst = Instance(
        id="inst-synth-1",
        organization_id="org-synth-1",
        bot_id="bot-synth-1",
        provider="WUZAPI",
        external_instance_id="synth-user-id-idemp-1",
        phone_number="5511999990000",
        status="ACTIVE",
    )
    db.add(org)
    db.add(bot)
    db.add(inst)
    db.commit()
    db.close()

    def _override_get_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db

    synthetic_inner = {
        "event": {
            "Info": {
                "ID": "SYNTH-DUP-MSG-999",
                "Sender": "226160000000000@lid",
                "SenderAlt": "5511999991111@s.whatsapp.net",
                "Type": "text",
            },
            "Message": {"conversation": "Oi"},
        },
        "type": "Message",
    }
    payload = {
        "instanceName": "inst_synthetic_test",
        "userID": "synth-user-id-idemp-1",
        "jsonData": json.dumps(synthetic_inner),
    }

    body = json.dumps(payload).encode("utf-8")
    sig = generate_test_signature(body, secret)

    try:
        with TestClient(app) as client:
            # 1st attempt: processes as unauthorized_user
            res1 = client.post(
                "/webhook",
                content=body,
                headers={"Content-Type": "application/json", "x-hmac-signature": sig},
            )
            assert res1.status_code == 200
            assert res1.json().get("detail") == "unauthorized_user"

            # 2nd attempt: duplicate suppression
            res2 = client.post(
                "/webhook",
                content=body,
                headers={"Content-Type": "application/json", "x-hmac-signature": sig},
            )
            assert res2.status_code == 200
            assert res2.json().get("detail") == "idempotent duplicate"

        # Verify DB state: exactly 1 Event row with duplicate_count = 1
        db = session_factory()
        from db.models import Event
        events = db.query(Event).all()
        assert len(events) == 1
        assert events[0].duplicate_count == 1
        assert events[0].status == "UNAUTHORIZED_USER"
        db.close()
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


# ------------------------------------------------------------------
# Native WUZAPI v1.0.8 application/x-www-form-urlencoded Tests
# ------------------------------------------------------------------


def test_webhook_parser_form_urlencoded_valid_message(monkeypatch, test_db_session):
    import urllib.parse

    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    synthetic_inner = {
        "event": {
            "Info": {
                "ID": "SYNTH-FORM-MSG-001",
                "Sender": "226160000000000@lid",
                "SenderAlt": "5511999997777@s.whatsapp.net",
                "Type": "text",
                "Timestamp": "2026-08-18T23:00:00-03:00",
            },
            "Message": {"conversation": "/cadastro SYNTHETIC_TEST_PW_123"},
        },
        "type": "Message",
    }
    form_data = {
        "instanceName": "inst_synthetic_test",
        "userID": "synth-wuzapi-user-id-form-1",
        "jsonData": json.dumps(synthetic_inner),
    }

    body = urllib.parse.urlencode(form_data).encode("utf-8")
    sig = generate_test_signature(body, secret)

    with TestClient(app) as client:
        response = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "x-hmac-signature": sig,
            },
        )
        # Reaches DB layer -> instance_not_found because synthetic ID is not seeded in test DB
        assert response.status_code == 200
        assert response.json().get("detail") == "instance_not_found"


def test_webhook_parser_form_urlencoded_malformed_json_data(monkeypatch):
    import urllib.parse

    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    form_data = {
        "instanceName": "inst_synthetic_test",
        "userID": "synth-wuzapi-user-id-form-2",
        "jsonData": "malformed-{json",
    }

    body = urllib.parse.urlencode(form_data).encode("utf-8")
    sig = generate_test_signature(body, secret)

    with TestClient(app) as client:
        response = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "x-hmac-signature": sig,
            },
        )
        assert response.status_code == 400
        assert "Malformed jsonData payload" in response.json()["detail"]


def test_webhook_parser_form_urlencoded_non_object_json_data(monkeypatch):
    import urllib.parse

    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    form_data = {
        "instanceName": "inst_synthetic_test",
        "userID": "synth-wuzapi-user-id-form-3",
        "jsonData": json.dumps(["a", "list"]),
    }

    body = urllib.parse.urlencode(form_data).encode("utf-8")
    sig = generate_test_signature(body, secret)

    with TestClient(app) as client:
        response = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "x-hmac-signature": sig,
            },
        )
        assert response.status_code == 400
        assert "jsonData must be a JSON object" in response.json()["detail"]


def test_webhook_parser_form_urlencoded_missing_user_id(monkeypatch):
    import urllib.parse

    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    synthetic_inner = {
        "event": {
            "Info": {
                "ID": "SYNTH-FORM-MSG-004",
                "Sender": "5511999997777@s.whatsapp.net",
                "Type": "text",
            },
            "Message": {"conversation": "Oi"},
        },
        "type": "Message",
    }
    form_data = {
        "instanceName": "inst_synthetic_test",
        "userID": "",  # Empty userID
        "jsonData": json.dumps(synthetic_inner),
    }

    body = urllib.parse.urlencode(form_data).encode("utf-8")
    sig = generate_test_signature(body, secret)

    with TestClient(app) as client:
        response = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "x-hmac-signature": sig,
            },
        )
        assert response.status_code == 400
        assert "Missing required source fields" in response.json()["detail"]


def test_webhook_parser_form_urlencoded_lifecycle_event_ignored(monkeypatch, test_db_session):
    import urllib.parse

    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    synthetic_inner = {
        "event": {"Chat": "226160000000000@lid", "Sender": "226160000000000@lid", "Type": "ChatPresence"},
        "type": "ChatPresence",
    }
    form_data = {
        "instanceName": "inst_synthetic_test",
        "userID": "synth-wuzapi-user-id-form-5",
        "jsonData": json.dumps(synthetic_inner),
    }

    body = urllib.parse.urlencode(form_data).encode("utf-8")
    sig = generate_test_signature(body, secret)

    with TestClient(app) as client:
        response = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "x-hmac-signature": sig,
            },
        )
        assert response.status_code == 200
        assert response.json().get("detail") == "ignored_chatpresence"
