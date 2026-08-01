import os
from threading import Thread, Barrier
import pytest
import hmac
import hashlib
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import (
    Base,
    Organization,
    Bot,
    Instance,
    User,
    Event,
    RegistrationAttempt,
    RegistrationRateLimit,
)
from security.hash import hash_secret, hash_pii
from orchestrator.main import app as orch_app, normalize_phone_number, mask_phone_number
from orchestrator.config import get_settings
from bot_df.main import app as bot_app

# Settings overrides
SETTINGS_OVERRIDE = {
    "wuzapi_webhook_secret": "wuz_webhook_sec",
    "registration_secret_pepper": "reg_pepper",
    "log_pii_hash_key": "pii_key",
    "orchestrator_to_bot_token": "bear_token",
    "bot_df_url": "http://bot-df:8003",
    "wuzapi_base_url": "http://wuzapi:8080",
    "wuzapi_token": "wuz_tok",
}


@pytest.fixture(autouse=True)
def mock_settings():
    settings = get_settings()
    for k, v in SETTINGS_OVERRIDE.items():
        setattr(settings, k, v)
    yield settings


_test_sessionmaker = None


@pytest.fixture
def db_session():
    global _test_sessionmaker
    database_url = os.environ.get("DATABASE_URL")

    if database_url and database_url.startswith("postgresql"):
        # PostgreSQL integration
        engine = create_engine(database_url)
        # Wipe schema to guarantee test isolation
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        _test_sessionmaker = sessionmaker(bind=engine)
        session = _test_sessionmaker()
        try:
            yield session
        finally:
            session.close()
            # Clean up after test run
            Base.metadata.drop_all(bind=engine)
            engine.dispose()
    else:
        # SQLite fallback
        db_file = "test_gate2.db"
        if os.path.exists(db_file):
            try:
                os.remove(db_file)
            except Exception:
                pass
        engine = create_engine(
            f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(bind=engine)
        _test_sessionmaker = sessionmaker(bind=engine)
        session = _test_sessionmaker()
        try:
            yield session
        finally:
            session.close()
            engine.dispose()
            if os.path.exists(db_file):
                try:
                    os.remove(db_file)
                except Exception:
                    pass


@pytest.fixture
def client(db_session):
    from orchestrator.main import get_db

    def override_get_db():
        session = _test_sessionmaker()
        try:
            yield session
        finally:
            session.close()

    orch_app.dependency_overrides[get_db] = override_get_db
    yield TestClient(orch_app)
    orch_app.dependency_overrides.clear()


def generate_signature(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def seed_base_data(session):
    # Hash of "senha123" is hmac of "senha123" with pepper "reg_pepper" and "org_id" prepended
    org = Organization(
        id="org_id",
        name="DF Holding",
        slug="df-holding",
        status="ACTIVE",
        registration_secret_hash=hash_secret("org_id:senha123", "reg_pepper"),
    )
    bot = Bot(
        id="bot_id",
        organization_id="org_id",
        name="BOT DF",
        service_key="bot_service_key",
        status="ACTIVE",
    )
    inst = Instance(
        id="inst_id",
        organization_id="org_id",
        bot_id="bot_id",
        provider="WUZAPI",
        external_instance_id="ext_inst_123",
        phone_number="5511999999999",
        status="ACTIVE",
    )
    session.add(org)
    session.flush()
    session.add(bot)
    session.flush()
    session.add(inst)
    session.commit()


# ───────────────────────────────────────────────────────────────────────────
# Webhook Authentication
# ───────────────────────────────────────────────────────────────────────────


def test_webhook_signature_missing(client):
    payload = {"instanceId": "ext_inst_123"}
    response = client.post("/webhook", json=payload)
    assert response.status_code == 401
    assert "Invalid webhook signature" in response.json()["detail"]


def test_webhook_signature_invalid(client):
    payload = {"instanceId": "ext_inst_123"}
    headers = {"x-hmac-signature": "wrong_signature"}
    response = client.post("/webhook", json=payload, headers=headers)
    assert response.status_code == 401


def test_webhook_signature_valid(client, db_session):
    seed_base_data(db_session)
    payload = {
        "instanceId": "ext_inst_123",
        "provider": "WUZAPI",
        "data": {
            "instanceId": "ext_inst_123",
            "message": {
                "key": {
                    "id": "msg_id_001",
                    "remoteJid": "5511999999999@s.whatsapp.net",
                },
                "conversation": "hello",
            },
        },
    }
    body = json.dumps(payload).encode("utf-8")
    sig = generate_signature(body, "wuz_webhook_sec")

    with patch(
        "orchestrator.main.WuzapiClient.send_text_message", new_callable=AsyncMock
    ) as mock_send:
        response = client.post(
            "/webhook",
            content=body,
            headers={"x-hmac-signature": sig, "content-type": "application/json"},
        )
        assert response.status_code == 200
        # Should reply with unauthorized prompt because user is not registered
        mock_send.assert_called_once()
        assert "Este número ainda não está cadastrado" in mock_send.call_args[0][1]


def test_webhook_altered_body(client, db_session):
    seed_base_data(db_session)
    payload = {
        "instanceId": "ext_inst_123",
        "data": {"message": {"key": {"id": "msg_001"}}},
    }
    body = json.dumps(payload).encode("utf-8")
    # Generate signature for unaltered body
    sig = generate_signature(body, "wuz_webhook_sec")

    # Alter body
    altered_payload = {
        "instanceId": "ext_inst_123",
        "data": {"message": {"key": {"id": "msg_002"}}},
    }
    altered_body = json.dumps(altered_payload).encode("utf-8")

    response = client.post(
        "/webhook",
        content=altered_body,
        headers={"x-hmac-signature": sig, "content-type": "application/json"},
    )
    assert response.status_code == 401


# ───────────────────────────────────────────────────────────────────────────
# Routing & Unknown Instance
# ───────────────────────────────────────────────────────────────────────────


def test_unknown_instance(client, db_session):
    payload = {
        "instanceId": "unknown_inst_id",
        "provider": "WUZAPI",
        "data": {
            "message": {
                "key": {"id": "msg_id_002", "remoteJid": "5511999999999@s.whatsapp.net"}
            }
        },
    }
    body = json.dumps(payload).encode("utf-8")
    sig = generate_signature(body, "wuz_webhook_sec")

    with patch(
        "orchestrator.main.WuzapiClient.send_text_message", new_callable=AsyncMock
    ):
        response = client.post(
            "/webhook",
            content=body,
            headers={"x-hmac-signature": sig, "content-type": "application/json"},
        )
        assert response.status_code == 200
        assert response.json()["detail"] == "instance_not_found"
        # Verify event saved with INSTANCE_NOT_FOUND
        evt = (
            db_session.query(Event).filter_by(external_message_id="msg_id_002").first()
        )
        assert evt is not None
        assert evt.status == "INSTANCE_NOT_FOUND"


# ───────────────────────────────────────────────────────────────────────────
# Phone Normalization & Privacy Logs
# ───────────────────────────────────────────────────────────────────────────


def test_phone_normalization():
    assert normalize_phone_number("5511999999999@s.whatsapp.net") == "5511999999999"
    assert normalize_phone_number("+55 (11) 99999-9999") == "5511999999999"
    assert (
        normalize_phone_number("11999999999") == "5511999999999"
    )  # Brazil DDD + mobile prepends 55


def test_phone_masking():
    assert mask_phone_number("5511999999999") == "5511****9999"
    assert mask_phone_number("123") == "****"


def test_phone_hashing_pii():
    canonical = "5511999999999"
    key = "pii_key"
    hashed = hash_pii(canonical, key)
    assert len(hashed) == 64
    assert (
        hashed != hashlib.sha256(canonical.encode()).hexdigest()
    )  # Verify it is HMAC, not plain SHA256


# ───────────────────────────────────────────────────────────────────────────
# Inactive / Suspended Users & Organization Mismatch
# ───────────────────────────────────────────────────────────────────────────


def test_inactive_user_suspended(client, db_session):
    seed_base_data(db_session)
    # Register an INACTIVE user
    inactive_user = User(
        organization_id="org_id", phone_number="5511999999999", status="INACTIVE"
    )
    db_session.add(inactive_user)
    db_session.commit()

    payload = {
        "instanceId": "ext_inst_123",
        "provider": "WUZAPI",
        "data": {
            "message": {
                "key": {
                    "id": "msg_inactive_001",
                    "remoteJid": "5511999999999@s.whatsapp.net",
                },
                "imageMessage": {},
            }
        },
    }
    body = json.dumps(payload).encode("utf-8")
    sig = generate_signature(body, "wuzapi_webhook_secret")  # Will use test settings
    sig = generate_signature(body, "wuz_webhook_sec")

    with patch(
        "orchestrator.main.WuzapiClient.send_text_message", new_callable=AsyncMock
    ) as mock_send:
        response = client.post(
            "/webhook",
            content=body,
            headers={"x-hmac-signature": sig, "content-type": "application/json"},
        )
        assert response.status_code == 200
        assert response.json()["detail"] == "user_inactive"
        mock_send.assert_called_once()
        assert "O acesso deste número está desativado" in mock_send.call_args[0][1]

        evt = (
            db_session.query(Event)
            .filter_by(external_message_id="msg_inactive_001")
            .first()
        )
        assert evt.status == "USER_INACTIVE"


def test_organization_mismatch(client, db_session):
    seed_base_data(db_session)
    # Register user in another organization
    other_org = Organization(
        id="other_org_id", name="Other Org", slug="other-org", status="ACTIVE"
    )
    db_session.add(other_org)
    db_session.flush()

    mismatched_user = User(
        organization_id="other_org_id", phone_number="5511999999999", status="ACTIVE"
    )
    db_session.add(mismatched_user)
    db_session.commit()

    payload = {
        "instanceId": "ext_inst_123",  # Belongs to org_id, not other_org_id
        "provider": "WUZAPI",
        "data": {
            "message": {
                "key": {
                    "id": "msg_mismatch_001",
                    "remoteJid": "5511999999999@s.whatsapp.net",
                },
                "imageMessage": {},
            }
        },
    }
    body = json.dumps(payload).encode("utf-8")
    sig = generate_signature(body, "wuz_webhook_sec")

    with patch(
        "orchestrator.main.WuzapiClient.send_text_message", new_callable=AsyncMock
    ) as mock_send:
        response = client.post(
            "/webhook",
            content=body,
            headers={"x-hmac-signature": sig, "content-type": "application/json"},
        )
        assert response.status_code == 200
        assert response.json()["detail"] == "organization_mismatch"
        mock_send.assert_called_once()
        assert (
            "Este número já está vinculado a outra organização"
            in mock_send.call_args[0][1]
        )

        evt = (
            db_session.query(Event)
            .filter_by(external_message_id="msg_mismatch_001")
            .first()
        )
        assert evt.status == "USER_ORGANIZATION_MISMATCH"


# ───────────────────────────────────────────────────────────────────────────
# Registration and Rate Limiting
# ───────────────────────────────────────────────────────────────────────────


def test_registration_success(client, db_session):
    seed_base_data(db_session)

    payload = {
        "instanceId": "ext_inst_123",
        "provider": "WUZAPI",
        "data": {
            "message": {
                "key": {
                    "id": "msg_reg_001",
                    "remoteJid": "5511999999999@s.whatsapp.net",
                },
                "conversation": "/cadastro senha123",
            }
        },
    }
    body = json.dumps(payload).encode("utf-8")
    sig = generate_signature(body, "wuz_webhook_sec")

    with patch(
        "orchestrator.main.WuzapiClient.send_text_message", new_callable=AsyncMock
    ):
        response = client.post(
            "/webhook",
            content=body,
            headers={"x-hmac-signature": sig, "content-type": "application/json"},
        )
        assert response.status_code == 200
        assert response.json()["detail"] == "REGISTRATION_SUCCEEDED"

        # Verify user is created
        usr = db_session.query(User).filter_by(phone_number="5511999999999").first()
        assert usr is not None
        assert usr.status == "ACTIVE"

        # Verify attempt audit
        attempt = (
            db_session.query(RegistrationAttempt)
            .filter_by(phone_number="5511999999999")
            .first()
        )
        assert attempt is not None
        assert attempt.success is True


def test_registration_invalid_password(client, db_session):
    seed_base_data(db_session)

    payload = {
        "instanceId": "ext_inst_123",
        "provider": "WUZAPI",
        "data": {
            "message": {
                "key": {
                    "id": "msg_reg_fail_001",
                    "remoteJid": "5511999999999@s.whatsapp.net",
                },
                "conversation": "/cadastro wrong_password",
            }
        },
    }
    body = json.dumps(payload).encode("utf-8")
    sig = generate_signature(body, "wuz_webhook_sec")

    with patch(
        "orchestrator.main.WuzapiClient.send_text_message", new_callable=AsyncMock
    ):
        response = client.post(
            "/webhook",
            content=body,
            headers={"x-hmac-signature": sig, "content-type": "application/json"},
        )
        assert response.status_code == 200
        assert response.json()["detail"] == "REGISTRATION_FAILED"

        # Verify attempt audit
        attempt = (
            db_session.query(RegistrationAttempt)
            .filter_by(phone_number="5511999999999")
            .first()
        )
        assert attempt is not None
        assert attempt.success is False
        assert attempt.failure_reason == "INVALID_REGISTRATION_SECRET"

        # Verify failure count
        limit = (
            db_session.query(RegistrationRateLimit)
            .filter_by(organization_id="org_id", phone_number="5511999999999")
            .first()
        )
        assert limit.failure_count == 1


def test_registration_rate_limit_blocking(client, db_session):
    seed_base_data(db_session)

    # Pre-simulate 2 failures
    limit = RegistrationRateLimit(
        organization_id="org_id",
        phone_number="5511999999999",
        failure_count=2,
        window_started_at=datetime.now(timezone.utc),
    )
    db_session.add(limit)
    db_session.commit()

    # Send 3rd failed attempt
    payload = {
        "instanceId": "ext_inst_123",
        "provider": "WUZAPI",
        "data": {
            "message": {
                "key": {
                    "id": "msg_reg_fail_003",
                    "remoteJid": "5511999999999@s.whatsapp.net",
                },
                "conversation": "/cadastro wrong_password_again",
            }
        },
    }
    body = json.dumps(payload).encode("utf-8")
    sig = generate_signature(body, "wuz_webhook_sec")

    with patch(
        "orchestrator.main.WuzapiClient.send_text_message", new_callable=AsyncMock
    ) as mock_send:
        response = client.post(
            "/webhook",
            content=body,
            headers={"x-hmac-signature": sig, "content-type": "application/json"},
        )
        assert response.status_code == 200
        assert response.json()["detail"] == "REGISTRATION_BLOCKED"
        mock_send.assert_called_once()
        assert (
            "Muitas tentativas de cadastro foram realizadas"
            in mock_send.call_args[0][1]
        )

        # Verify blocked status
        db_session.refresh(limit)
        assert limit.failure_count == 3
        assert limit.blocked_until is not None

        # Verify next attempts blocked without secret validation (mocking verify_secret to prove it is not called)
        with patch("orchestrator.main.verify_secret") as mock_verify:
            payload2 = dict(payload)
            payload2["data"]["message"]["key"]["id"] = "msg_reg_fail_004"
            body2 = json.dumps(payload2).encode("utf-8")
            sig2 = generate_signature(body2, "wuz_webhook_sec")

            response2 = client.post(
                "/webhook",
                content=body2,
                headers={"x-hmac-signature": sig2, "content-type": "application/json"},
            )
            assert response2.status_code == 200
            assert response2.json()["detail"] == "REGISTRATION_BLOCKED"
            mock_verify.assert_not_called()


# ───────────────────────────────────────────────────────────────────────────
# Idempotency and Duplicates
# ───────────────────────────────────────────────────────────────────────────


def test_idempotent_duplicate_webhook(client, db_session):
    seed_base_data(db_session)

    payload = {
        "instanceId": "ext_inst_123",
        "provider": "WUZAPI",
        "data": {
            "message": {
                "key": {
                    "id": "msg_dup_001",
                    "remoteJid": "5511999999999@s.whatsapp.net",
                },
                "conversation": "hello",
            }
        },
    }
    body = json.dumps(payload).encode("utf-8")
    sig = generate_signature(body, "wuz_webhook_sec")

    with patch(
        "orchestrator.main.WuzapiClient.send_text_message", new_callable=AsyncMock
    ) as mock_send:
        # First request
        res1 = client.post(
            "/webhook",
            content=body,
            headers={"x-hmac-signature": sig, "content-type": "application/json"},
        )
        assert res1.status_code == 200

        # Second request (duplicate)
        res2 = client.post(
            "/webhook",
            content=body,
            headers={"x-hmac-signature": sig, "content-type": "application/json"},
        )
        assert res2.status_code == 200
        assert res2.json()["detail"] == "idempotent duplicate"

        # External send should only be called once
        assert mock_send.call_count == 1

        # Verify duplicate count incremented directly in Event
        evt = (
            db_session.query(Event).filter_by(external_message_id="msg_dup_001").first()
        )
        assert evt.duplicate_count == 1
        assert evt.last_duplicate_at is not None


# ───────────────────────────────────────────────────────────────────────────
# Internal service authentication
# ───────────────────────────────────────────────────────────────────────────


def test_bot_df_auth_bearer():
    from fastapi.testclient import TestClient

    bot_client = TestClient(bot_app)

    # No auth
    res1 = bot_client.post("/events", json={})
    assert res1.status_code == 401

    # Wrong token
    res2 = bot_client.post(
        "/events", json={}, headers={"Authorization": "Bearer wrong_token"}
    )
    assert res2.status_code == 401

    # Correct token
    with patch.dict("os.environ", {"ORCHESTRATOR_TO_BOT_TOKEN": "bear_token"}):
        res3 = bot_client.post(
            "/events",
            json={"external_message_id": "msg_001"},
            headers={"Authorization": "Bearer bear_token"},
        )
        assert res3.status_code == 200
        assert res3.json()["status"] == "accepted"


# ───────────────────────────────────────────────────────────────────────────
# Outbound failure isolation
# ───────────────────────────────────────────────────────────────────────────


def test_outbound_failure_isolation(client, db_session):
    seed_base_data(db_session)
    payload = {
        "instanceId": "ext_inst_123",
        "provider": "WUZAPI",
        "data": {
            "message": {
                "key": {
                    "id": "msg_out_fail_001",
                    "remoteJid": "5511999999999@s.whatsapp.net",
                },
                "conversation": "/cadastro senha123",
            }
        },
    }
    body = json.dumps(payload).encode("utf-8")
    sig = generate_signature(body, "wuz_webhook_sec")

    # Mock send to raise WuzapiError
    from orchestrator.wuzapi import WuzapiError

    with patch(
        "orchestrator.main.WuzapiClient.send_text_message",
        side_effect=WuzapiError("connection failed"),
    ):
        response = client.post(
            "/webhook",
            content=body,
            headers={"x-hmac-signature": sig, "content-type": "application/json"},
        )
        assert response.status_code == 200

        # Verify user still got registered successfully despite outbound failure
        usr = db_session.query(User).filter_by(phone_number="5511999999999").first()
        assert usr is not None
        assert usr.status == "ACTIVE"

        # Verify event saved with error details
        evt = (
            db_session.query(Event)
            .filter_by(external_message_id="msg_out_fail_001")
            .first()
        )
        assert evt.status == "REGISTRATION_SUCCEEDED"
        assert evt.error_code == "WUZAPI_SEND_FAILED"


# ───────────────────────────────────────────────────────────────────────────
# Concurrency Tests (with Synchronization Barriers)
# ───────────────────────────────────────────────────────────────────────────
def test_concurrent_webhook_idempotency(client, db_session):
    seed_base_data(db_session)
    payload = {
        "instanceId": "ext_inst_123",
        "provider": "WUZAPI",
        "data": {
            "message": {
                "key": {
                    "id": "msg_concurrent_dup",
                    "remoteJid": "5511999999999@s.whatsapp.net",
                },
                "conversation": "hello",
            }
        },
    }
    body = json.dumps(payload).encode("utf-8")
    sig = generate_signature(body, "wuz_webhook_sec")

    num_threads = 4
    barrier = Barrier(num_threads)
    results = []

    def worker():
        barrier.wait()
        try:
            res = client.post(
                "/webhook",
                content=body,
                headers={"x-hmac-signature": sig, "content-type": "application/json"},
            )
            results.append(res)
        except Exception as e:
            results.append(e)

    threads = [Thread(target=worker) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == num_threads
    success_responses = [
        r for r in results if not isinstance(r, Exception) and r.status_code == 200
    ]
    assert len(success_responses) == num_threads

    db_session.rollback()
    evt = (
        db_session.query(Event)
        .filter_by(external_message_id="msg_concurrent_dup")
        .first()
    )
    assert evt is not None
    assert evt.duplicate_count == num_threads - 1


def test_concurrent_first_time_rate_limit_initialization(client, db_session):
    seed_base_data(db_session)
    phone = "5511988888888"
    payload = {
        "instanceId": "ext_inst_123",
        "provider": "WUZAPI",
        "data": {
            "message": {
                "key": {
                    "id": "msg_reg_fail_concurrent",
                    "remoteJid": f"{phone}@s.whatsapp.net",
                },
                "conversation": "/cadastro wrong_pwd",
            }
        },
    }

    num_threads = 3
    barrier = Barrier(num_threads)
    results = []

    def worker(idx):
        payload_copy = dict(payload)
        payload_copy["data"]["message"]["key"]["id"] = f"msg_reg_fail_concurrent_{idx}"
        body = json.dumps(payload_copy).encode("utf-8")
        sig = generate_signature(body, "wuz_webhook_sec")
        barrier.wait()
        try:
            res = client.post(
                "/webhook",
                content=body,
                headers={"x-hmac-signature": sig, "content-type": "application/json"},
            )
            results.append(res)
        except Exception as e:
            results.append(e)

    threads = [Thread(target=worker, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == num_threads
    success_responses = [
        r for r in results if not isinstance(r, Exception) and r.status_code == 200
    ]
    assert len(success_responses) == num_threads

    db_session.rollback()
    limits = db_session.query(RegistrationRateLimit).filter_by(phone_number=phone).all()
    assert len(limits) == 1
    assert limits[0].failure_count == 3
    assert limits[0].blocked_until is not None


def test_concurrent_valid_registrations(client, db_session):
    seed_base_data(db_session)
    phone = "5511977777777"

    num_threads = 2
    barrier = Barrier(num_threads)
    results = []

    def worker(idx):
        payload = {
            "instanceId": "ext_inst_123",
            "provider": "WUZAPI",
            "data": {
                "message": {
                    "key": {
                        "id": f"msg_reg_success_concurrent_{idx}",
                        "remoteJid": f"{phone}@s.whatsapp.net",
                    },
                    "conversation": "/cadastro senha123",
                }
            },
        }
        body = json.dumps(payload).encode("utf-8")
        sig = generate_signature(body, "wuz_webhook_sec")
        barrier.wait()
        try:
            res = client.post(
                "/webhook",
                content=body,
                headers={"x-hmac-signature": sig, "content-type": "application/json"},
            )
            results.append(res)
        except Exception as e:
            results.append(e)

    threads = [Thread(target=worker, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == num_threads
    success_responses = [
        r for r in results if not isinstance(r, Exception) and r.status_code == 200
    ]
    assert len(success_responses) == num_threads

    db_session.rollback()
    users = db_session.query(User).filter_by(phone_number=phone).all()
    assert len(users) == 1

    details = [r.json()["detail"] for r in success_responses]
    print("\n--- DETAILS ARE ---", details)
    assert all(d in ("REGISTRATION_SUCCEEDED", "already_active") for d in details)
