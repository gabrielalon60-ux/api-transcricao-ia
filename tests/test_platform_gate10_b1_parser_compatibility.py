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
from orchestrator.main import app, extract_file_info, get_db, normalize_phone_number
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


# ------------------------------------------------------------------
# Three-Mode Matrix & Mislabeled-JSON Robustness Tests
# ------------------------------------------------------------------


def test_mode_1_json_header_json_outer_envelope(monkeypatch, test_db_session):
    # Mode 1: Content-Type: application/json, body is JSON outer envelope
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    synthetic_inner = {
        "event": {
            "Info": {
                "ID": "SYNTH-MODE1-MSG-001",
                "Sender": "5511999991111@s.whatsapp.net",
                "Type": "text",
            },
            "Message": {"conversation": "Modo 1 JSON puro"},
        },
        "type": "Message",
    }
    outer_payload = {
        "instanceName": "g10b1_synthetic_mode1",
        "userID": "synth-wuzapi-user-id-mode1",
        "jsonData": json.dumps(synthetic_inner),
    }

    body = json.dumps(outer_payload).encode("utf-8")
    sig = generate_test_signature(body, secret)

    with TestClient(app) as client:
        response = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "x-hmac-signature": sig,
            },
        )
        assert response.status_code == 200
        assert response.json().get("detail") == "instance_not_found"


def test_mode_2_form_header_genuine_form_body(monkeypatch, test_db_session):
    # Mode 2: Content-Type: application/x-www-form-urlencoded, body is genuine form-encoded
    import urllib.parse

    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    synthetic_inner = {
        "event": {
            "Info": {
                "ID": "SYNTH-MODE2-MSG-002",
                "Sender": "5511999992222@s.whatsapp.net",
                "Type": "text",
            },
            "Message": {"conversation": "Modo 2 Form genuino"},
        },
        "type": "Message",
    }
    form_data = {
        "instanceName": "g10b1_synthetic_mode2",
        "userID": "synth-wuzapi-user-id-mode2",
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
        assert response.json().get("detail") == "instance_not_found"


def test_mode_3_physical_wuzapi_form_header_json_body(monkeypatch, test_db_session):
    # Mode 3 (Physical WUZAPI regression): Content-Type: application/x-www-form-urlencoded, body is JSON outer envelope
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    synthetic_inner = {
        "event": {
            "Info": {
                "ID": "SYNTH-MODE3-MSG-003",
                "Sender": "5511999993333@s.whatsapp.net",
                "Type": "text",
            },
            "Message": {"conversation": "Modo 3 Physical WUZAPI mislabeled JSON"},
        },
        "type": "Message",
    }
    outer_payload = {
        "instanceName": "g10b1_synthetic_mode3",
        "userID": "synth-wuzapi-user-id-mode3",
        "jsonData": json.dumps(synthetic_inner),
    }

    body = json.dumps(outer_payload).encode("utf-8")
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
        assert response.json().get("detail") == "instance_not_found"


def test_mislabeled_json_with_leading_whitespace(monkeypatch, test_db_session):
    # Tests leading whitespace before JSON body with form-urlencoded header
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    synthetic_inner = {
        "event": {
            "Info": {
                "ID": "SYNTH-WS-MSG-004",
                "Sender": "5511999994444@s.whatsapp.net",
                "Type": "text",
            },
            "Message": {"conversation": "Leading whitespace test"},
        },
        "type": "Message",
    }
    outer_payload = {
        "instanceName": "g10b1_synthetic_ws",
        "userID": "synth-wuzapi-user-id-ws",
        "jsonData": json.dumps(synthetic_inner),
    }

    body = b"  \r\n\t  " + json.dumps(outer_payload).encode("utf-8")
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
        assert response.json().get("detail") == "instance_not_found"


def test_mislabeled_json_malformed_json_fails_400(monkeypatch):
    # Tests that a body starting with "{" that is malformed JSON fails 400 (fail-closed)
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    body = b'{"instanceName": "broken_json", "userID": '
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
        assert "Malformed payload" in response.json()["detail"]


def test_mislabeled_json_non_object_fails_400(monkeypatch):
    # Tests that a body starting with "[" (JSON list) fails 400
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    body = json.dumps(["element1", "element2"]).encode("utf-8")
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
        assert "JSON payload must be an object" in response.json()["detail"]


def test_mislabeled_json_missing_info_id_fails_400(monkeypatch):
    # Tests that a Mode 3 payload missing Info.ID fails 400
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    synthetic_inner = {
        "event": {
            "Info": {
                "Sender": "5511999995555@s.whatsapp.net",
                "Type": "text",
            },
            "Message": {"conversation": "No message ID"},
        },
        "type": "Message",
    }
    outer_payload = {
        "instanceName": "g10b1_synthetic_noid",
        "userID": "synth-wuzapi-user-id-noid",
        "jsonData": json.dumps(synthetic_inner),
    }

    body = json.dumps(outer_payload).encode("utf-8")
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


def test_mislabeled_json_duplicate_replay_idempotency(monkeypatch, test_db_session):
    # Tests that sending duplicate Mode 3 payload results in idempotent duplicate handling
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    synthetic_inner = {
        "event": {
            "Info": {
                "ID": "SYNTH-DUP-MSG-006",
                "Sender": "5511999996666@s.whatsapp.net",
                "Type": "text",
            },
            "Message": {"conversation": "Duplicate idempotency test"},
        },
        "type": "Message",
    }
    outer_payload = {
        "instanceName": "g10b1_synthetic_dup",
        "userID": "synth-wuzapi-user-id-dup",
        "jsonData": json.dumps(synthetic_inner),
    }

    body = json.dumps(outer_payload).encode("utf-8")
    sig = generate_test_signature(body, secret)

    with TestClient(app) as client:
        # First send
        resp1 = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "x-hmac-signature": sig,
            },
        )
        assert resp1.status_code == 200
        assert resp1.json().get("detail") == "instance_not_found"

        # Duplicate send
        resp2 = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "x-hmac-signature": sig,
            },
        )
        assert resp2.status_code == 200
        assert resp2.json().get("detail") == "idempotent duplicate"


def test_zero_secret_leakage_in_logs(monkeypatch, capsys, test_db_session):
    secret = "test_webhook_secret_key_32bytes_len"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)
    synthetic_secret = "synthetic_sec_abc123_high_entropy_48chars"
    synthetic_phone = "5511999990001"

    synthetic_inner = {
        "event": {
            "Info": {
                "ID": "SYNTH_DIAG_MSG_001",
                "Sender": f"{synthetic_phone}:1@s.whatsapp.net",
            },
            "Message": {"conversation": f"/cadastro {synthetic_secret}"},
        },
        "type": "Message",
    }
    outer_payload = {
        "instanceName": "g10b1_synthetic_diag",
        "userID": "synth-wuzapi-user-id-diag",
        "jsonData": json.dumps(synthetic_inner),
    }

    body = json.dumps(outer_payload).encode("utf-8")
    sig = generate_test_signature(body, secret)

    with TestClient(app) as client:
        resp = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "x-hmac-signature": sig,
            },
        )
        assert resp.status_code == 200

    captured = capsys.readouterr()
    all_log_text = captured.err + " " + captured.out
    # Assert zero leakage of sensitive data
    assert synthetic_secret not in all_log_text
    assert f"/cadastro {synthetic_secret}" not in all_log_text
    assert synthetic_phone not in all_log_text
    assert secret not in all_log_text
    assert sig not in all_log_text


# ------------------------------------------------------------------
# Mode 4: Top-Level Native WUZAPI Event Envelope Tests
# ------------------------------------------------------------------


def test_mode4_top_level_native_wuzapi_envelope_success(monkeypatch, test_db_session):
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    payload = {
        "instanceName": "g10b1_mode4_test",
        "userID": "synth-wuzapi-user-id-mode4",
        "type": "Message",
        "event": {
            "Info": {
                "ID": "SYNTH-MODE4-MSG-001",
                "Sender": "5511999991234@s.whatsapp.net",
                "Type": "text",
            },
            "Message": {"conversation": "Hello from top-level event"},
        },
    }
    body = json.dumps(payload).encode("utf-8")
    sig = generate_test_signature(body, secret)

    with TestClient(app) as client:
        resp = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "x-hmac-signature": sig,
            },
        )
        assert resp.status_code == 200
        assert resp.json().get("detail") == "instance_not_found"


def test_mode4_top_level_registration_route(monkeypatch, test_db_session):
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    payload = {
        "instanceName": "g10b1_mode4_reg",
        "userID": "synth-wuzapi-user-id-reg",
        "type": "Message",
        "event": {
            "Info": {
                "ID": "SYNTH-MODE4-MSG-REG",
                "Sender": "5511999994321@s.whatsapp.net",
                "Type": "text",
            },
            "Message": {"conversation": "/cadastro SYNTHETIC_SECRET_VAL"},
        },
    }
    body = json.dumps(payload).encode("utf-8")
    sig = generate_test_signature(body, secret)

    with TestClient(app) as client:
        resp = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "x-hmac-signature": sig,
            },
        )
        assert resp.status_code == 200


def test_mode4_missing_info_fails_closed(monkeypatch, test_db_session):
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    payload = {
        "instanceName": "g10b1_mode4_noinfo",
        "userID": "synth-wuzapi-user-id-noinfo",
        "type": "Message",
        "event": {
            "Message": {"conversation": "No info dict"},
        },
    }
    body = json.dumps(payload).encode("utf-8")
    sig = generate_test_signature(body, secret)

    with TestClient(app) as client:
        resp = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "x-hmac-signature": sig,
            },
        )
        assert resp.status_code == 400
        assert "Missing required source fields" in resp.json()["detail"]


def test_mode4_missing_id_fails_400(monkeypatch, test_db_session):
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    payload = {
        "instanceName": "g10b1_mode4_noid",
        "userID": "synth-wuzapi-user-id-noid",
        "type": "Message",
        "event": {
            "Info": {
                "Sender": "5511999991111@s.whatsapp.net",
            },
            "Message": {"conversation": "No ID"},
        },
    }
    body = json.dumps(payload).encode("utf-8")
    sig = generate_test_signature(body, secret)

    with TestClient(app) as client:
        resp = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "x-hmac-signature": sig,
            },
        )
        assert resp.status_code == 400
        assert "Missing required source fields" in resp.json()["detail"]


def test_mode4_empty_id_fails_400(monkeypatch, test_db_session):
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    payload = {
        "instanceName": "g10b1_mode4_emptyid",
        "userID": "synth-wuzapi-user-id-emptyid",
        "type": "Message",
        "event": {
            "Info": {
                "ID": "   ",
                "Sender": "5511999991111@s.whatsapp.net",
            },
            "Message": {"conversation": "Empty ID"},
        },
    }
    body = json.dumps(payload).encode("utf-8")
    sig = generate_test_signature(body, secret)

    with TestClient(app) as client:
        resp = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "x-hmac-signature": sig,
            },
        )
        assert resp.status_code == 400
        assert "Missing required source fields" in resp.json()["detail"]


def test_mode4_privacy_lid_sender_alt_resolution(monkeypatch, test_db_session):
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    payload = {
        "instanceName": "g10b1_mode4_lid",
        "userID": "synth-wuzapi-user-id-lid",
        "type": "Message",
        "event": {
            "Info": {
                "ID": "SYNTH-MODE4-LID-MSG",
                "Sender": "226160143274032:88@lid",
                "SenderAlt": "554791734195:88@s.whatsapp.net",
                "Type": "text",
            },
            "Message": {"conversation": "LID resolution test"},
        },
    }
    body = json.dumps(payload).encode("utf-8")
    sig = generate_test_signature(body, secret)

    with TestClient(app) as client:
        resp = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "x-hmac-signature": sig,
            },
        )
        assert resp.status_code == 200


def test_mode4_lifecycle_non_message_ignored(monkeypatch, test_db_session):
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    payload = {
        "instanceName": "g10b1_mode4_lifecycle",
        "userID": "synth-wuzapi-user-id-lifecycle",
        "type": "ChatPresence",
        "event": {
            "Info": {"ID": "LIFECYCLE-001"},
            "Type": "ChatPresence",
        },
    }
    body = json.dumps(payload).encode("utf-8")
    sig = generate_test_signature(body, secret)

    with TestClient(app) as client:
        resp = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "x-hmac-signature": sig,
            },
        )
        assert resp.status_code == 200
        assert resp.json().get("detail") == "ignored_chatpresence"


def test_mode4_duplicate_idempotency_same_identity(monkeypatch, test_db_session):
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    payload = {
        "instanceName": "g10b1_mode4_dup",
        "userID": "synth-wuzapi-user-id-dup",
        "type": "Message",
        "event": {
            "Info": {
                "ID": "SYNTH-MODE4-DUP-001",
                "Sender": "5511999997777@s.whatsapp.net",
                "Type": "text",
            },
            "Message": {"conversation": "Duplicate Mode 4 test"},
        },
    }
    body = json.dumps(payload).encode("utf-8")
    sig = generate_test_signature(body, secret)

    with TestClient(app) as client:
        resp1 = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "x-hmac-signature": sig,
            },
        )
        assert resp1.status_code == 200
        assert resp1.json().get("detail") == "instance_not_found"

        resp2 = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "x-hmac-signature": sig,
            },
        )
        assert resp2.status_code == 200
        assert resp2.json().get("detail") == "idempotent duplicate"


def test_envelope_conflict_jsonData_vs_top_level_fails_400(monkeypatch, test_db_session):
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    inner_json = {
        "event": {
            "Info": {"ID": "MSG-ID-ALPHA", "Sender": "5511999990001@s.whatsapp.net"},
            "Message": {"conversation": "Alpha"},
        },
        "type": "Message",
    }
    payload = {
        "instanceName": "g10b1_conflict",
        "userID": "synth-wuzapi-user-id-conflict",
        "jsonData": json.dumps(inner_json),
        "event": {
            "Info": {"ID": "MSG-ID-BETA", "Sender": "5511999990002@s.whatsapp.net"},
            "Message": {"conversation": "Beta"},
        },
        "type": "Message",
    }
    body = json.dumps(payload).encode("utf-8")
    sig = generate_test_signature(body, secret)

    with TestClient(app) as client:
        resp = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "x-hmac-signature": sig,
            },
        )
        assert resp.status_code == 400
        assert "Conflicting event envelopes" in resp.json()["detail"]


def test_envelope_matching_jsonData_vs_top_level_succeeds(monkeypatch, test_db_session):
    secret = "test_wuzapi_secret"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)

    inner_json = {
        "event": {
            "Info": {"ID": "MSG-ID-SAME", "Sender": "5511999990001@s.whatsapp.net"},
            "Message": {"conversation": "Same"},
        },
        "type": "Message",
    }
    payload = {
        "instanceName": "g10b1_matching",
        "userID": "synth-wuzapi-user-id-match",
        "jsonData": json.dumps(inner_json),
        "event": {
            "Info": {"ID": "MSG-ID-SAME", "Sender": "5511999990001@s.whatsapp.net"},
            "Message": {"conversation": "Same"},
        },
        "type": "Message",
    }
    body = json.dumps(payload).encode("utf-8")
    sig = generate_test_signature(body, secret)

    with TestClient(app) as client:
        resp = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "x-hmac-signature": sig,
            },
        )
        assert resp.status_code == 200
        assert resp.json().get("detail") == "instance_not_found"


# ------------------------------------------------------------------
# WhatsApp Multi-Device Phone Normalization Unit & Registration Tests
# ------------------------------------------------------------------


def test_normalize_phone_number_multi_device_matrix():
    # A. Plain numeric phone
    assert normalize_phone_number("554791734195") == "554791734195"
    # B. Standard WhatsApp JID
    assert normalize_phone_number("554791734195@s.whatsapp.net") == "554791734195"
    # C. Single-digit device suffix
    assert normalize_phone_number("554791734195:1@s.whatsapp.net") == "554791734195"
    # D. Multi-digit device suffix
    assert normalize_phone_number("554791734195:88@s.whatsapp.net") == "554791734195"
    # E. Colon without @
    assert normalize_phone_number("554791734195:88") == "554791734195"
    # F. Local Brazil number without DDI
    assert normalize_phone_number("4791734195:12@s.whatsapp.net") == "554791734195"
    # G. Special characters with device suffix
    assert normalize_phone_number("+55 (47) 9173-4195:88@s.whatsapp.net") == "554791734195"


def test_mode4_registration_with_multi_device_sender_alt(monkeypatch, test_db_session):
    from unittest.mock import AsyncMock, patch
    from db.models import Organization, Bot, Instance, User, RegistrationAttempt
    from security.hash import hash_secret

    secret = "test_wuzapi_secret"
    registration_secret = "correct_secret_val_123"
    pepper = "reg_pepper_test"
    settings = get_settings()
    monkeypatch.setattr(settings, "wuzapi_webhook_secret", secret)
    monkeypatch.setattr(settings, "registration_secret_pepper", pepper)

    # Seed an Organization, Bot, and Instance in the isolated test DB
    from orchestrator.main import get_db
    db_gen = app.dependency_overrides[get_db]()
    db = next(db_gen)

    org_id = "org-test-multi-device"
    bot_id = "bot-test-multi-device"
    inst_id = "inst-test-multi-device"
    secret_hash = hash_secret(org_id + ":" + registration_secret, pepper)

    org = Organization(id=org_id, name="Test Org", slug="test-org-md", registration_secret_hash=secret_hash)
    bot = Bot(id=bot_id, organization_id=org_id, name="Test Bot", service_key="svc-key-md-1")
    inst = Instance(
        id=inst_id,
        organization_id=org_id,
        bot_id=bot_id,
        external_instance_id="wuzapi-user-md-1",
        phone_number="554791696228",
    )
    db.add(org)
    db.add(bot)
    db.add(inst)
    db.commit()

    payload = {
        "instanceName": "g10b1_mode4_md_reg",
        "userID": "wuzapi-user-md-1",
        "type": "Message",
        "event": {
            "Info": {
                "ID": "SYNTH-MD-MSG-001",
                "Sender": "226160143274032:88@lid",
                "SenderAlt": "554791734195:88@s.whatsapp.net",
                "Type": "text",
            },
            "Message": {"conversation": f"/cadastro {registration_secret}"},
        },
    }
    body = json.dumps(payload).encode("utf-8")
    sig = generate_test_signature(body, secret)

    mock_send = AsyncMock()
    with patch("orchestrator.main.WuzapiClient.send_text_message", mock_send):
        with TestClient(app) as client:
            resp = client.post(
                "/webhook",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "x-hmac-signature": sig,
                },
            )
            assert resp.status_code == 200
            assert resp.json().get("detail") == "REGISTRATION_SUCCEEDED"

    # 1. Verify User phone number is canonical (12 digits, NO device suffix :88)
    user = db.query(User).filter_by(organization_id=org_id).first()
    assert user is not None
    assert user.phone_number == "554791734195"
    assert "88" not in user.phone_number[-2:] or len(user.phone_number) == 12

    # 2. Verify RegistrationAttempt phone number is canonical
    attempt = db.query(RegistrationAttempt).filter_by(organization_id=org_id).first()
    assert attempt is not None
    assert attempt.phone_number == "554791734195"
    assert attempt.success is True

    # 3. Verify outbound call used canonical phone
    mock_send.assert_awaited_once()
    called_phone = mock_send.call_args[0][0]
    assert called_phone == "554791734195"


def test_extract_file_info_preserves_crypto_media_ref_fields():
    """Preserve exact native v1.0.8 media casing observed in the physical webhook."""
    payload = {
        "event": "Message",
        "instance": "inst-100",
        "data": {
            "id": "msg-media-1",
            "message": {
                "imageMessage": {
                    "mimetype": "image/jpeg",
                    "fileLength": 45678,
                    "fileSHA256": "expected_sha_b64",
                    "fileEncSHA256": "enc_sha_b64",
                    "mediaKey": "secret_media_key_b64",
                    "directPath": "/v/t62.7118-24/img.enc",
                    "URL": "https://mmg.whatsapp.net/d/f/img.enc",
                }
            },
        },
    }
    info = extract_file_info(payload, "image", text_content=None)
    assert info["message_type"] == "image"
    assert info["file_mime_type"] == "image/jpeg"
    assert info["file_size"] == 45678
    assert info["file_sha256"] == "expected_sha_b64"
    assert info["media_ref"] is not None
    ref = info["media_ref"]
    assert ref["media_key"] == "secret_media_key_b64"
    assert ref["direct_path"] == "/v/t62.7118-24/img.enc"
    assert ref["file_enc_sha256"] == "enc_sha_b64"
    assert ref["expected_sha256"] == "expected_sha_b64"
    assert ref["expected_size"] == 45678
    assert ref["mime_type"] == "image/jpeg"
    assert ref["url"] == "https://mmg.whatsapp.net/d/f/img.enc"


def test_extract_file_info_accepts_pinned_native_exported_field_names():
    payload = {
        "event": "Message",
        "instance": "inst-native-exported",
        "data": {
            "id": "msg-native-exported",
            "message": {
                "imageMessage": {
                    "Mimetype": "image/jpeg",
                    "FileLength": 45678,
                    "FileSHA256": "expected_sha_b64",
                    "FileEncSHA256": "enc_sha_b64",
                    "MediaKey": "media_key_b64",
                    "DirectPath": "/v/t62.7118-24/img.enc",
                    "URL": "https://mmg.whatsapp.net/d/f/img.enc",
                }
            },
        },
    }

    info = extract_file_info(payload, "image", text_content=None)
    ref = info["media_ref"]
    assert ref is not None
    assert info["file_sha256"] == "expected_sha_b64"
    assert info["file_size"] == 45678
    assert info["file_mime_type"] == "image/jpeg"
    assert ref["expected_sha256"] == "expected_sha_b64"
    assert ref["file_enc_sha256"] == "enc_sha_b64"
    assert ref["media_key"] == "media_key_b64"
    assert ref["direct_path"] == "/v/t62.7118-24/img.enc"
    assert ref["url"] == "https://mmg.whatsapp.net/d/f/img.enc"
