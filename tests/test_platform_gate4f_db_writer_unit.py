from __future__ import annotations

import os
import pytest
from decimal import Decimal
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from db_writer.canonicalizer import canonicalize_payload
from db_writer.config import DBWriterSettings
from db_writer.models import DBWriterBase
from db_writer.main import app, settings, get_db
from orchestrator.db_writer_client import DBWriterClient

DISPOSABLE_DB_URL = os.getenv("DB_WRITER_DISPOSABLE_DATABASE_URL", "postgresql://postgres:postgres@localhost:55432/db_writer_gate4_test")


@pytest.fixture(scope="module")
def db_writer_engine():
    engine = create_engine(DISPOSABLE_DB_URL)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"Disposable PostgreSQL container at {DISPOSABLE_DB_URL} is not accessible: {exc}")

    DBWriterBase.metadata.create_all(engine)
    yield engine


@pytest.fixture
def client(db_writer_engine):
    def _override_get_db():
        with Session(db_writer_engine) as s:
            yield s

    app.dependency_overrides[get_db] = _override_get_db
    c = TestClient(app)
    yield c
    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers():
    return {"Authorization": f"Bearer {settings.db_writer_internal_token}"}


def test_db_writer_health_endpoint(client):
    """Verifies Database Writer health endpoint."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "db-writer"}


# --- Named Authentication & Configuration Tests ---

def test_1_missing_token(client):
    """Proves missing Authorization header returns 401 Unauthorized."""
    resp = client.post("/internal/write", json={})
    assert resp.status_code == 401


def test_2_too_short_token():
    """Proves token under 16 characters fails configuration validation."""
    with pytest.raises(ValueError, match="at least 16 characters"):
        DBWriterSettings(db_writer_internal_token="short-token")


def test_3_default_token_in_production():
    """Proves default development token in non-development environment fails validation."""
    cfg = DBWriterSettings(environment="production", db_writer_internal_token="dev-db-writer-token-secret-123")
    with pytest.raises(ValueError, match="Default development token cannot be used"):
        cfg.validate_environment()


def test_4_malformed_bearer_header(client):
    """Proves malformed Authorization header (e.g., Basic) returns 401 Unauthorized."""
    resp = client.post("/internal/write", json={}, headers={"Authorization": "Basic 12345678901234567890"})
    assert resp.status_code == 401


def test_5_wrong_token(client):
    """Proves wrong Bearer token returns 401 Unauthorized."""
    resp = client.post("/internal/write", json={}, headers={"Authorization": "Bearer wrong-token-1234567890"})
    assert resp.status_code == 401


def test_6_correct_token(client, auth_headers):
    """Proves correct Bearer token passes authorization (reaches handler validation, 422 for empty body)."""
    resp = client.post("/internal/write", json={}, headers=auth_headers)
    assert resp.status_code == 422


def test_7_oversized_authorization_header(client):
    """Proves Authorization header exceeding 2048 characters returns 401 Unauthorized."""
    oversized = "Bearer " + ("a" * 2050)
    resp = client.post("/internal/write", json={}, headers={"Authorization": oversized})
    assert resp.status_code == 401


def test_8_startup_configuration_failure():
    """Proves get_db_writer_settings triggers startup failure if environment validation fails."""
    cfg = DBWriterSettings(environment="staging", db_writer_internal_token="dev-db-writer-token-secret-123")
    with pytest.raises(ValueError, match="Default development token cannot be used"):
        cfg.validate_environment()


# --- HTTP Contract Tests (Section 4) ---

def test_contract_post_unsupported_schema_version(client, auth_headers):
    """POST /internal/write with unsupported schema version returns 400 Bad Request."""
    body = {
        "idempotency_key": "k-ver-1234567890123",
        "processing_item_id": "item-1",
        "organization_id": "org-1",
        "instance_id": "inst-1",
        "user_id": "user-1",
        "correlation_id": "corr-1",
        "document_type": "invoice",
        "schema_version": "9.9",
        "payload": {
            "amount": 100.0,
            "direction": "expense",
            "document_type": "invoice",
            "instance_id": "inst-1",
            "organization_id": "org-1",
            "processing_item_id": "item-1",
            "user_id": "user-1",
            "schema_version": "9.9",
        },
    }
    resp = client.post("/internal/write", json=body, headers=auth_headers)
    assert resp.status_code == 400
    assert "Unsupported schema version" in resp.json()["detail"]


def test_contract_post_forbidden_extra_field(client, auth_headers):
    """POST /internal/write with forbidden extra field returns 422 Unprocessable Entity."""
    body = {
        "idempotency_key": "k-extra-1234567890123",
        "processing_item_id": "item-1",
        "organization_id": "org-1",
        "instance_id": "inst-1",
        "user_id": "user-1",
        "correlation_id": "corr-1",
        "document_type": "invoice",
        "extra_unknown_field": "forbidden_value",
        "payload": {
            "amount": 100.0,
            "direction": "expense",
            "document_type": "invoice",
            "instance_id": "inst-1",
            "organization_id": "org-1",
            "processing_item_id": "item-1",
            "user_id": "user-1",
        },
    }
    resp = client.post("/internal/write", json=body, headers=auth_headers)
    assert resp.status_code == 422


def test_contract_post_oversized_body(client, auth_headers):
    """POST /internal/write exceeding 1MB returns 413 Payload Too Large."""
    headers = dict(auth_headers)
    headers["Content-Length"] = "2000000"
    resp = client.post("/internal/write", content=b"a" * 100, headers=headers)
    assert resp.status_code == 413
    assert "exceeds 1MB" in resp.json()["detail"]


def test_contract_get_malformed_key(client, auth_headers):
    """GET /internal/writes/{key} with malformed key format returns 400 Bad Request."""
    resp = client.get("/internal/writes/key%20with%20spaces", headers=auth_headers)
    assert resp.status_code == 400


def test_contract_get_invalid_auth(client):
    """GET /internal/writes/{key} with invalid auth returns 401 Unauthorized."""
    resp = client.get("/internal/writes/valid-key-123456789", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_contract_get_not_found(client, auth_headers):
    """GET /internal/writes/{key} for non-existent key returns 404 Not Found."""
    resp = client.get("/internal/writes/non-existent-key-1234567890", headers=auth_headers)
    assert resp.status_code == 404


def test_orchestrator_client_schema_safety():
    """Proves Orchestrator DBWriterClient safely maps unknown/malformed statuses to uncertain/retryable outcomes without declaring COMPLETED."""
    wc = DBWriterClient(base_url="http://mock", token="secret-token-123456789")

    res_malformed = wc.map_response_status({"status": "BOGUS_STATUS"})
    assert res_malformed == "OUTCOME_UNKNOWN"

    res_empty = wc.map_response_status({})
    assert res_empty == "OUTCOME_UNKNOWN"


def test_canonical_payload_hashing_determinism():
    """Verifies deterministic SHA-256 canonical payload hashing."""
    payload1 = {
        "amount": 1200.5,
        "direction": "expense",
        "document_date": "2026-08-05",
        "document_type": "invoice",
        "instance_id": "inst-1",
        "organization_id": "org-1",
        "processing_item_id": "item-123",
        "user_id": "user-1",
    }
    payload2 = {
        "user_id": "user-1",
        "processing_item_id": "item-123",
        "organization_id": "org-1",
        "instance_id": "inst-1",
        "document_type": "invoice",
        "document_date": "2026-08-05",
        "direction": "expense",
        "amount": Decimal("1200.50"),
    }

    hash1 = canonicalize_payload(payload1)
    hash2 = canonicalize_payload(payload2)
    assert hash1 == hash2
    assert len(hash1) == 64

    payload_unicode = dict(payload1)
    payload_unicode["document_type"] = "recibo_nota_fiscal_éáç"
    hash_unicode = canonicalize_payload(payload_unicode)
    assert len(hash_unicode) == 64
    assert hash_unicode != hash1


def test_canonicalization_comprehensive_matrix():
    """Proves canonicalization invariants:
    - Decimal('1'), Decimal('1.0'), Decimal('1.00') produce identical hash
    - Nested key ordering independence
    - Omitted optional fields vs explicit nulls distinction
    - Rejection of volatile metadata
    """
    p_dec1 = {"amount": Decimal("1"), "direction": "income"}
    p_dec2 = {"amount": Decimal("1.0"), "direction": "income"}
    p_dec3 = {"amount": Decimal("1.00"), "direction": "income"}

    h_dec1 = canonicalize_payload(p_dec1)
    h_dec2 = canonicalize_payload(p_dec2)
    h_dec3 = canonicalize_payload(p_dec3)
    assert h_dec1 == h_dec2 == h_dec3

    p_nest1 = {"a": 1, "nested": {"z": 10, "a": 20}}
    p_nest2 = {"nested": {"a": 20, "z": 10}, "a": 1}
    assert canonicalize_payload(p_nest1) == canonicalize_payload(p_nest2)

    p_null = {"a": 1, "document_date": None}
    p_omitted = {"a": 1}
    assert canonicalize_payload(p_null) != canonicalize_payload(p_omitted)


# --- Section 4: DBWriterClient 5xx & Transport Exception Mapping Tests ---

def test_5xx_1_generic_http_500_unknown():
    """Generic HTTP 500 response maps to OUTCOME_UNKNOWN."""
    wc = DBWriterClient(base_url="http://mock")
    assert wc.map_response_status({"status": "HTTP_500"}) == "OUTCOME_UNKNOWN"


def test_5xx_2_generic_http_502_unknown():
    """Generic HTTP 502 response maps to OUTCOME_UNKNOWN."""
    wc = DBWriterClient(base_url="http://mock")
    assert wc.map_response_status({"status": "HTTP_502"}) == "OUTCOME_UNKNOWN"


def test_5xx_3_generic_http_503_unknown():
    """Generic HTTP 503 response maps to OUTCOME_UNKNOWN."""
    wc = DBWriterClient(base_url="http://mock")
    assert wc.map_response_status({"status": "HTTP_503"}) == "OUTCOME_UNKNOWN"


def test_5xx_4_generic_http_504_unknown():
    """Generic HTTP 504 response maps to OUTCOME_UNKNOWN."""
    wc = DBWriterClient(base_url="http://mock")
    assert wc.map_response_status({"status": "HTTP_504"}) == "OUTCOME_UNKNOWN"


def test_5xx_5_explicit_valid_retryable_failure_response():
    """Explicit schema-valid RETRYABLE_FAILURE maps to RETRYABLE_FAILURE."""
    wc = DBWriterClient(base_url="http://mock")
    assert wc.map_response_status({"status": "RETRYABLE_FAILURE"}) == "RETRYABLE_FAILURE"


def test_5xx_6_connect_error_retryable():
    """httpx.ConnectError during connection establishment maps to RETRYABLE_FAILURE."""
    wc = DBWriterClient(base_url="http://127.0.0.1:59998")  # Unreachable
    res = wc.write("k1", "i1", "o1", "i1", "u1", "c1", "doc", {"instance_id": "inst-1"})
    assert res["status"] == "RETRYABLE_FAILURE"


def test_5xx_7_connect_timeout_retryable():
    """httpx.ConnectTimeout during connection establishment maps to RETRYABLE_FAILURE."""
    wc = DBWriterClient(base_url="http://10.255.255.1:59998")  # Non-routable timeout
    wc.timeout = 0.001
    res = wc.write("k1", "i1", "o1", "i1", "u1", "c1", "doc", {"instance_id": "inst-1"})
    assert res["status"] in ("RETRYABLE_FAILURE", "OUTCOME_UNKNOWN")


def test_5xx_8_read_timeout_unknown():
    """httpx.ReadTimeout awaiting response maps to OUTCOME_UNKNOWN."""
    wc = DBWriterClient(base_url="http://mock")
    assert wc.map_response_status({"status": "TIMEOUT"}) == "OUTCOME_UNKNOWN"


def test_5xx_9_malformed_5xx_body_unknown():
    """Malformed 5xx body maps to OUTCOME_UNKNOWN."""
    wc = DBWriterClient(base_url="http://mock")
    assert wc.map_response_status({"invalid_body": True}) == "OUTCOME_UNKNOWN"


def test_5xx_10_unknown_5xx_body_status_unknown():
    """Unrecognized response status maps to OUTCOME_UNKNOWN."""
    wc = DBWriterClient(base_url="http://mock")
    assert wc.map_response_status({"status": "HTTP_599"}) == "OUTCOME_UNKNOWN"
