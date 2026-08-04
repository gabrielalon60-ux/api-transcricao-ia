from __future__ import annotations

import uuid
import asyncio
from io import BytesIO
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from transcription.auth.api_key_auth import get_current_application
from transcription.auth.hash import hash_api_key
from transcription.auth.internal import verify_internal_transcription_token
from transcription.database.models import Application, Extraction, Request, RequestStatus, UsageLog
from transcription.schemas.internal import InternalExtractionMetadata
from transcription.services.document_validation import (
    ValidationResult,
    ValidationInput,
    ValidationLimits,
    detect_format,
    materialize_validation_input,
    run_validation_subprocess_sync,
    validate_declared_mime,
    validate_document_worker,
    _walk_pdf_object,
)
from transcription.services.ai.provider import AIProvider, ExtractionResult
from transcription.services.internal_extraction_service import (
    InternalExtractionService,
    classify_provider_exception,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def gate3_settings(monkeypatch):
    from transcription.core.config import get_settings
    from transcription.services.prompt_service import PromptService

    get_settings.cache_clear()
    PromptService.load_prompt.cache_clear()
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    monkeypatch.setenv("API_KEY_HASH_SECRET", "test-secret")
    monkeypatch.setenv("BOT_TO_TRANSCRIPTION_TOKEN", "test-token")
    monkeypatch.setenv("TRANSCRIPTION_DATABASE_URL", "postgresql://unused/unused")
    monkeypatch.setenv("PROVIDER_MAX_RETRIES", "2")
    yield
    PromptService.load_prompt.cache_clear()
    get_settings.cache_clear()


class FakeProvider(AIProvider):
    model_name = "fake-model"

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.received_payloads = []

    async def extract(self, image_bytes: bytes) -> ExtractionResult:
        self.calls += 1
        self.received_payloads.append(image_bytes)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def first(self):
        return self.rows[0] if self.rows else None


class FakeDB:
    def __init__(self, existing=None):
        self.requests = {}
        self.added = []
        self.extractions = []
        self.usage_logs = []
        self.commits = 0
        self.rollbacks = 0
        self.fail_compensation_commit = False
        self._pending_duplicate_request = False
        if existing is not None:
            self.requests[existing.id] = existing

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, Request):
            if obj.id in self.requests:
                self._pending_duplicate_request = True
                return
            if obj.id is None:
                obj.id = uuid.uuid4()
            self.requests[obj.id] = obj
        elif isinstance(obj, Extraction):
            self.extractions.append(obj)
            request = self.requests.get(obj.request_id)
            if request is not None:
                request.extraction = obj
        elif isinstance(obj, UsageLog):
            self.usage_logs.append(obj)

    def commit(self):
        self.commits += 1
        if self._pending_duplicate_request:
            self._pending_duplicate_request = False
            raise IntegrityError("duplicate request", None, None)
        if self.fail_compensation_commit:
            raise SQLAlchemyError("forced compensation failure")

    def rollback(self):
        self.rollbacks += 1
        self.extractions.clear()
        self.usage_logs.clear()

    def flush(self):
        return None

    def close(self):
        return None

    def get(self, model, key):
        if model is Request:
            return self.requests.get(key)
        return None

    def refresh(self, obj):
        return None


def metadata(request_id=None) -> InternalExtractionMetadata:
    return InternalExtractionMetadata(
        request_id=request_id or uuid.uuid4(),
        bot_instance_id=uuid.uuid4(),
        correlation_id="corr-1",
        received_at=datetime.now(timezone.utc),
        source="WHATSAPP",
    )


def result() -> ExtractionResult:
    return ExtractionResult(
        data={"document_type": "receipt", "extraction": {"total": "12.34"}},
        model_name="fake-model",
        input_tokens=10,
        output_tokens=20,
        total_tokens=30,
        estimated_cost=Decimal("0.00000081"),
        provider="fake",
        usage_status="AVAILABLE",
        pricing_version="test",
        currency="USD",
    )


DOCUMENT_TYPE_FIXTURES = [
    (
        "Nota fiscal",
        "invoice",
        {
            "supplier_name": "Fornecedor Exemplo Ltda",
            "supplier_cpf_cnpj": "00.000.000/0001-00",
            "customer_name": "Cliente Exemplo Ltda",
            "customer_cpf_cnpj": "11.111.111/0001-11",
            "invoice_date": "2026-08-03",
            "total_amount": "123.45",
            "amount_assurance_percentage": "100%",
        },
    ),
    (
        "Cupom fiscal",
        "invoice",
        {
            "supplier_name": "Loja Exemplo Ltda",
            "supplier_cpf_cnpj": "22.222.222/0001-22",
            "customer_name": None,
            "customer_cpf_cnpj": None,
            "invoice_date": "2026-08-03",
            "total_amount": "45.67",
            "amount_assurance_percentage": "95%",
        },
    ),
    (
        "Comprovante PIX",
        "pix_receipt",
        {
            "sender_name": "Pagador Exemplo",
            "sender_cpf_cnpj": "000.000.000-00",
            "receiver_name": "Recebedor Exemplo",
            "receiver_cpf_cnpj": "11.111.111/0001-11",
            "amount": "89.01",
            "amount_assurance_percentage": "100%",
            "transaction_date": "2026-08-03",
        },
    ),
    (
        "Boleto",
        "bank_receipt",
        {
            "payer_name": "Pagador Exemplo",
            "payer_cpf_cnpj": "000.000.000-00",
            "recipient_name": "Banco Exemplo",
            "recipient_cpf_cnpj": "22.222.222/0001-22",
            "amount": "200.00",
            "amount_assurance_percentage": "98%",
            "payment_date": "2026-08-03",
            "due_date": "2026-08-10",
            "barcode": "00000000000000000000000000000000000000000000",
            "bank_code": "001",
        },
    ),
    (
        "Pedido",
        "commercial_document",
        {
            "supplier_name": "Fornecedor Pedido Ltda",
            "supplier_cpf_cnpj": "33.333.333/0001-33",
            "customer_name": "Cliente Pedido Ltda",
            "customer_cpf_cnpj": "44.444.444/0001-44",
            "document_date": "2026-08-03",
            "total_amount": "300.00",
            "amount_assurance_percentage": "97%",
        },
    ),
    (
        "Orçamento",
        "commercial_document",
        {
            "supplier_name": "Fornecedor Orcamento Ltda",
            "supplier_cpf_cnpj": "55.555.555/0001-55",
            "customer_name": "Cliente Orcamento Ltda",
            "customer_cpf_cnpj": "66.666.666/0001-66",
            "document_date": "2026-08-03",
            "total_amount": "400.00",
            "amount_assurance_percentage": "96%",
        },
    ),
]


def fixture_result(document_type: str, extraction: dict) -> ExtractionResult:
    return ExtractionResult(
        data={
            "document_type": document_type,
            "extraction": extraction,
            "normalization": {"business_fixture": True},
            "confidence": None,
            "quality_flags": [],
        },
        model_name="fake-model",
        input_tokens=10,
        output_tokens=20,
        total_tokens=30,
        estimated_cost=Decimal("0.00000081"),
        provider="fake",
        usage_status="AVAILABLE",
        pricing_version="test",
        currency="USD",
    )


def minimal_png() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
        b"\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfeA\xe2&\xbd"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def valid_png() -> bytes:
    from PIL import Image

    buffer = BytesIO()
    image = Image.new("RGB", (1, 1), color=(255, 255, 255))
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def minimal_jpeg() -> bytes:
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"


def minimal_webp() -> bytes:
    return b"RIFF\x04\x00\x00\x00WEBP"


@pytest.fixture
def route_client(monkeypatch):
    from transcription.api.internal_extract import get_internal_ai_provider
    from transcription.core.config import get_settings
    from transcription.database.session import get_db
    from transcription.main import app

    get_settings.cache_clear()
    db = FakeDB()
    provider = FakeProvider([result()])
    monkeypatch.setattr(
        "transcription.services.internal_extraction_service.run_validation_subprocess_sync",
        lambda *args: ValidationResult(True),
    )
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_internal_ai_provider] = lambda: provider
    yield TestClient(app), db, provider
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def multipart(meta: dict | str | None = None, data: bytes | None = None, content_type: str = "image/png"):
    payload = meta
    if payload is None:
        payload = {
            "request_id": str(uuid.uuid4()),
            "bot_instance_id": str(uuid.uuid4()),
            "correlation_id": "corr-1",
            "received_at": "2026-08-03T12:00:00-03:00",
            "source": "WHATSAPP",
        }
    if isinstance(payload, dict):
        import json

        payload = json.dumps(payload)
    return {
        "files": {"file": ("document.png", data if data is not None else minimal_png(), content_type)},
        "data": {"metadata": payload},
    }


def test_internal_auth_uses_timing_safe_compare(monkeypatch):
    from transcription.auth import internal as internal_auth

    calls = []

    def fake_compare(supplied, expected):
        calls.append((supplied, expected))
        return True

    monkeypatch.setattr(internal_auth.secrets, "compare_digest", fake_compare)
    verify_internal_transcription_token("Bearer test-token")

    assert calls == [("test-token", "test-token")]


@pytest.mark.parametrize(
    "header",
    [None, "Basic test-token", "Bearer", "Bearer ", "Bearer wrong-token"],
)
def test_internal_auth_rejects_invalid_headers_without_secret_leak(header, caplog):
    with pytest.raises(HTTPException) as exc:
        verify_internal_transcription_token(header)

    assert exc.value.status_code == 401
    assert "test-token" not in str(exc.value.detail)
    assert "wrong-token" not in caplog.text
    assert "test-token" not in caplog.text


def test_internal_auth_missing_or_blank_configuration(monkeypatch):
    from transcription.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.delenv("BOT_TO_TRANSCRIPTION_TOKEN", raising=False)
    with pytest.raises(HTTPException) as exc:
        verify_internal_transcription_token("Bearer any")
    assert exc.value.status_code == 503

    get_settings.cache_clear()
    monkeypatch.setenv("BOT_TO_TRANSCRIPTION_TOKEN", "   ")
    with pytest.raises(HTTPException) as exc:
        verify_internal_transcription_token("Bearer any")
    assert exc.value.status_code == 503
    get_settings.cache_clear()


def test_internal_route_valid_multipart(route_client):
    client, db, provider = route_client
    response = client.post(
        "/internal/extract",
        headers={"Authorization": "Bearer test-token"},
        **multipart(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "SUCCEEDED"
    assert body["file"]["detected_mime"] == "image/png"
    assert provider.calls == 1
    request = next(iter(db.requests.values()))
    assert request.received_at.isoformat().startswith("2026-08-03T15:00:00")


@pytest.mark.parametrize(("business_category", "expected_label", "extraction"), DOCUMENT_TYPE_FIXTURES)
def test_six_business_document_fixtures_map_to_four_runtime_labels(monkeypatch, business_category, expected_label, extraction):
    from transcription.services.prompt_service import PromptService

    db = FakeDB()
    provider = FakeProvider([fixture_result(expected_label, extraction)])
    svc = InternalExtractionService(db=db, ai_provider=provider)
    meta = metadata()
    prompt = PromptService.load_prompt()

    assert "pix_receipt" in prompt
    assert "commercial_document" in prompt
    assert "invoice" in prompt
    assert "bank_receipt" in prompt
    assert "unknown" in prompt
    assert {
        "Nota fiscal": "Nota fiscal",
        "Cupom fiscal": "cupom fiscal",
        "Comprovante PIX": "PIX",
        "Boleto": "Boletos",
        "Pedido": "Pedidos",
        "Orçamento": "Orçamentos",
    }[business_category] in prompt
    fixture_file = valid_png()
    assert validate_document_worker(ValidationInput(fixture_file, None, "PNG", svc._validation_limits())).ok is True

    monkeypatch.setattr(
        "transcription.services.internal_extraction_service.run_validation_subprocess_sync",
        lambda *args: ValidationResult(True),
    )
    response = asyncio.run(
        svc.process(
            metadata=meta,
            file_bytes=fixture_file,
            declared_mime="image/png",
        )
    )

    assert response.status_code == 200
    assert response.body.document_type == expected_label
    assert response.body.extraction == extraction
    assert response.body.normalization == {"business_fixture": True}
    assert response.body.file.sha256
    assert response.body.file.detected_mime == "image/png"
    assert db.requests[meta.request_id].status == RequestStatus.SUCCEEDED
    assert db.requests[meta.request_id].file_sha256 == response.body.file.sha256
    assert db.extractions[0].response_json["document_type"] == expected_label
    assert db.usage_logs[0].attempt_number == 1
    assert provider.calls == 1
    assert provider.received_payloads == [fixture_file]

    replay = svc.process(
        metadata=meta,
        file_bytes=fixture_file,
        declared_mime="image/png",
    )
    replay_response = asyncio.run(replay)

    assert replay_response.status_code == 200
    assert replay_response.body.document_type == expected_label
    assert replay_response.body.extraction == extraction
    assert provider.calls == 1


@pytest.mark.parametrize(
    ("meta_update", "expected_status"),
    [
        ({"request_id": "not-a-uuid"}, 422),
        ({"bot_instance_id": "not-a-uuid"}, 422),
        ({"correlation_id": ""}, 422),
        ({"correlation_id": "   "}, 422),
        ({"correlation_id": "x" * 129}, 422),
        ({"received_at": "2026-08-03T12:00:00"}, 422),
        ({"source": "EMAIL"}, 422),
        ({"unexpected": "field"}, 422),
    ],
)
def test_internal_route_metadata_rejections(route_client, meta_update, expected_status):
    client, _, provider = route_client
    base = {
        "request_id": str(uuid.uuid4()),
        "bot_instance_id": str(uuid.uuid4()),
        "correlation_id": "x" * 128,
        "received_at": "2026-08-03T12:00:00Z",
        "source": "WHATSAPP",
    }
    base.update(meta_update)
    response = client.post(
        "/internal/extract",
        headers={"Authorization": "Bearer test-token"},
        **multipart(base),
    )

    assert response.status_code == expected_status
    assert response.json()["error_code"] == "INVALID_METADATA"
    assert response.json()["request_id"] is None
    assert provider.calls == 0


@pytest.mark.parametrize("metadata_payload", ["{", "[]", '"not-object"'])
def test_internal_route_malformed_metadata(route_client, metadata_payload):
    client, _, provider = route_client

    response = client.post(
        "/internal/extract",
        headers={"Authorization": "Bearer test-token"},
        **multipart(metadata_payload),
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "INVALID_METADATA"
    assert provider.calls == 0


def test_internal_route_missing_parts(route_client):
    client, _, _ = route_client

    missing_file = client.post(
        "/internal/extract",
        headers={"Authorization": "Bearer test-token"},
        data=multipart()["data"],
    )
    missing_metadata = client.post(
        "/internal/extract",
        headers={"Authorization": "Bearer test-token"},
        files=multipart()["files"],
    )

    assert missing_file.status_code == 422
    assert missing_metadata.status_code == 422


def test_internal_route_empty_file_and_mime_mismatch(route_client):
    client, _, provider = route_client

    empty = client.post(
        "/internal/extract",
        headers={"Authorization": "Bearer test-token"},
        **multipart(data=b""),
    )
    mismatch = client.post(
        "/internal/extract",
        headers={"Authorization": "Bearer test-token"},
        **multipart(content_type="application/pdf"),
    )

    assert empty.status_code == 422
    assert empty.json()["error_code"] == "EMPTY_FILE"
    assert mismatch.status_code == 422
    assert mismatch.json()["error_code"] == "MIME_MISMATCH"
    assert provider.calls == 0


def test_legacy_api_key_hash_maps_to_physical_api_key_column():
    raw_key = "legacy-secret"
    stored_hash = hash_api_key(raw_key)
    app = Application(id=uuid.uuid4(), name="legacy", api_key_hash=stored_hash, active=True)

    class Query:
        def filter(self, expression):
            assert Application.__table__.c.api_key.name == "api_key"
            assert "api_key" in str(expression)
            assert "api_key_hash" not in str(expression)
            return self

        def first(self):
            return app

    class DB:
        def query(self, model):
            assert model is Application
            return Query()

    credentials = SimpleNamespace(credentials=raw_key)
    assert get_current_application(credentials, DB()) is app
    assert app.api_key_hash == stored_hash
    assert app.api_key_hash != raw_key


def test_legacy_extract_http_success_and_internal_token_rejected(monkeypatch):
    from transcription.auth.api_key_auth import get_current_application
    from transcription.core.config import get_settings
    from transcription.database.session import get_db
    from transcription.main import app as fastapi_app

    get_settings.cache_clear()
    legacy_app = Application(
        id=uuid.uuid4(),
        name="legacy-app",
        api_key_hash=hash_api_key("legacy-key"),
        active=True,
    )
    db = FakeDB()
    provider = FakeProvider([result()])
    fastapi_app.dependency_overrides[get_current_application] = lambda: legacy_app
    fastapi_app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr("transcription.api.extract._get_ai_provider", lambda: provider)
    try:
        client = TestClient(fastapi_app)
        response = client.post(
            "/extract",
            headers={"Authorization": "Bearer legacy-key"},
            files={"file": ("document.png", minimal_png(), "image/png")},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert provider.calls == 1
        request = next(iter(db.requests.values()))
        assert request.application_id == legacy_app.id
        assert request.status == RequestStatus.COMPLETED
        assert len(db.usage_logs) == 1
        assert db.usage_logs[0].attempt_number == 1
    finally:
        fastapi_app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_legacy_extract_http_provider_failure_preserves_legacy_error(monkeypatch):
    from transcription.auth.api_key_auth import get_current_application
    from transcription.core.config import get_settings
    from transcription.database.session import get_db
    from transcription.main import app as fastapi_app

    get_settings.cache_clear()
    legacy_app = Application(id=uuid.uuid4(), name="legacy-app", api_key_hash="hash", active=True)
    db = FakeDB()
    provider = FakeProvider([RuntimeError("503 unavailable")])
    fastapi_app.dependency_overrides[get_current_application] = lambda: legacy_app
    fastapi_app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr("transcription.api.extract._get_ai_provider", lambda: provider)
    try:
        client = TestClient(fastapi_app)
        response = client.post(
            "/extract",
            headers={"Authorization": "Bearer legacy-key"},
            files={"file": ("document.png", minimal_png(), "image/png")},
        )
        assert response.status_code == 503
        assert "AI provider is currently unavailable" in response.json()["detail"]
        request = next(iter(db.requests.values()))
        assert request.status == RequestStatus.FAILED
    finally:
        fastapi_app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_legacy_extract_missing_and_malformed_auth_are_rejected():
    from transcription.main import app as fastapi_app

    client = TestClient(fastapi_app)
    missing = client.post("/extract", files={"file": ("document.png", minimal_png(), "image/png")})
    malformed = client.post(
        "/extract",
        headers={"Authorization": "NotBearer key"},
        files={"file": ("document.png", minimal_png(), "image/png")},
    )
    assert missing.status_code in {401, 403}
    assert malformed.status_code in {401, 403}


def test_internal_bot_token_cannot_authenticate_legacy_route(monkeypatch):
    from transcription.database.session import get_db
    from transcription.main import app as fastapi_app

    class EmptyQuery:
        def filter(self, expression):
            return self

        def first(self):
            return None

    class EmptyDB:
        def query(self, model):
            return EmptyQuery()

    fastapi_app.dependency_overrides[get_db] = lambda: EmptyDB()
    try:
        client = TestClient(fastapi_app)
        response = client.post(
            "/extract",
            headers={"Authorization": "Bearer test-token"},
            files={"file": ("document.png", minimal_png(), "image/png")},
        )
        assert response.status_code == 401
        assert "test-token" not in str(response.json())
    finally:
        fastapi_app.dependency_overrides.clear()


def test_startup_and_configuration_safety(monkeypatch):
    import transcription.main as main_module
    from transcription.core.config import get_settings
    from transcription.database import models
    from transcription.database.session import _get_engine

    called = False

    def forbidden_create_all(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("create_all must not be called at import/startup")

    monkeypatch.setattr(models.Base.metadata, "create_all", forbidden_create_all)
    assert main_module.app is not None
    assert called is False

    get_settings.cache_clear()
    monkeypatch.delenv("TRANSCRIPTION_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://wrong/wrong")
    with pytest.raises(RuntimeError, match="TRANSCRIPTION_DATABASE_URL"):
        _get_engine()
    get_settings.cache_clear()


def test_transcription_compose_command_uses_one_uvicorn_worker():
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    command_line = next(line.strip() for line in compose.splitlines() if "uvicorn transcription.main:app" in line)

    assert "--workers 1" in command_line
    assert "--workers 2" not in command_line
    assert "--workers 3" not in command_line


def test_document_signature_and_mime_rules():
    assert detect_format(minimal_png()) == "PNG"
    assert detect_format(b"%PDF-1.7\n") == "PDF"
    with pytest.raises(ValueError, match="INVALID_PDF"):
        detect_format(b"%PDF")
    with pytest.raises(ValueError, match="UNSUPPORTED_FILE_TYPE"):
        detect_format(b"GIF89a")
    with pytest.raises(ValueError, match="MIME_MISMATCH"):
        validate_declared_mime("image/png", "application/pdf")


def test_validation_transport_and_worker_guards(tmp_path):
    limits = ValidationLimits(100, 100, 10_000, 1, 100, 5)
    small, path = materialize_validation_input(b"abc", "PNG", limits, 10)
    assert small.source_bytes == b"abc"
    assert path is None

    large, path = materialize_validation_input(b"abc", "PNG", limits, 1)
    assert large.temporary_path == path
    assert path is not None
    assert validate_document_worker(ValidationInput(b"x", path, "PNG", limits)).error_code == "VALIDATION_PROCESS_FAILED"


def test_temporary_validation_file_cleanup_is_idempotent(tmp_path):
    from transcription.services.document_validation import cleanup_temporary_path

    path = tmp_path / "spooled-document.bin"
    path.write_bytes(b"x")

    cleanup_temporary_path(str(path))
    cleanup_temporary_path(str(path))

    assert not path.exists()


def test_prompt_service_loads_packaged_prompt_outside_repo_cwd(monkeypatch, tmp_path):
    from transcription.services.prompt_service import PromptService

    PromptService.load_prompt.cache_clear()
    monkeypatch.chdir(tmp_path)

    prompt = PromptService.load_prompt()

    assert "document_type" in prompt
    assert "pix_receipt" in prompt
    assert "commercial_document" in prompt
    assert "invoice" in prompt
    assert "bank_receipt" in prompt
    PromptService.load_prompt.cache_clear()


def test_prompt_service_missing_prompt_error_does_not_expose_local_path(monkeypatch, tmp_path):
    from transcription.core.config import get_settings
    from transcription.services.prompt_service import PromptConfigurationError, PromptService

    missing = tmp_path / "missing.md"
    PromptService.load_prompt.cache_clear()
    monkeypatch.setenv("SYSTEM_PROMPT_PATH", str(missing))
    get_settings.cache_clear()

    with pytest.raises(PromptConfigurationError) as exc:
        PromptService.load_prompt()

    assert str(exc.value) == "SYSTEM_PROMPT_INVALID"
    assert exc.value.reason == "missing"
    assert str(tmp_path) not in str(exc.value)
    PromptService.load_prompt.cache_clear()
    get_settings.cache_clear()


def test_prompt_size_and_content_validation_matrix(monkeypatch, tmp_path):
    from transcription.core.config import get_settings
    from transcription.services.prompt_service import PromptConfigurationError, PromptService

    def load(path: Path, limit: int = 262144):
        PromptService.load_prompt.cache_clear()
        get_settings.cache_clear()
        monkeypatch.setenv("SYSTEM_PROMPT_PATH", str(path))
        monkeypatch.setenv("MAX_SYSTEM_PROMPT_SIZE_BYTES", str(limit))
        return PromptService.load_prompt()

    exact = tmp_path / "exact.md"
    exact.write_bytes(b"a" * 262144)
    assert load(exact) == "a" * 262144

    over = tmp_path / "over.md"
    over.write_bytes(b"a" * 262145)
    with pytest.raises(PromptConfigurationError) as exc:
        load(over)
    assert exc.value.reason == "oversized"

    empty = tmp_path / "empty.md"
    empty.write_bytes(b"")
    with pytest.raises(PromptConfigurationError) as exc:
        load(empty)
    assert exc.value.reason == "empty"

    whitespace = tmp_path / "whitespace.md"
    whitespace.write_text(" \n\t", encoding="utf-8")
    with pytest.raises(PromptConfigurationError) as exc:
        load(whitespace)
    assert exc.value.reason == "empty"

    invalid = tmp_path / "invalid.md"
    invalid.write_bytes(b"\xff")
    with pytest.raises(PromptConfigurationError) as exc:
        load(invalid)
    assert exc.value.reason == "invalid_utf8"

    with pytest.raises(PromptConfigurationError) as exc:
        load(tmp_path)
    assert exc.value.reason == "directory"

    for invalid_limit in ("0", "-1", "not-int"):
        PromptService.load_prompt.cache_clear()
        get_settings.cache_clear()
        monkeypatch.setenv("SYSTEM_PROMPT_PATH", str(exact))
        monkeypatch.setenv("MAX_SYSTEM_PROMPT_SIZE_BYTES", invalid_limit)
        with pytest.raises(Exception) as exc:
            PromptService.load_prompt()
        assert str(tmp_path) not in str(exc.value)
        assert "test-gemini" not in str(exc.value)
        assert "test-token" not in str(exc.value)

    PromptService.load_prompt.cache_clear()
    get_settings.cache_clear()


def test_prompt_explicit_path_non_repo_cwd_and_cache_once(monkeypatch, tmp_path):
    from transcription.core.config import get_settings
    from transcription.services.prompt_service import PromptService

    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first.write_text("first prompt", encoding="utf-8")
    second.write_text("second prompt", encoding="utf-8")
    monkeypatch.setenv("SYSTEM_PROMPT_PATH", str(first))
    get_settings.cache_clear()
    PromptService.load_prompt.cache_clear()
    monkeypatch.chdir(tmp_path)

    assert PromptService.load_prompt() == "first prompt"
    first.write_text("changed prompt", encoding="utf-8")
    monkeypatch.setenv("SYSTEM_PROMPT_PATH", str(second))

    assert PromptService.load_prompt() == "first prompt"

    PromptService.load_prompt.cache_clear()
    get_settings.cache_clear()
    assert PromptService.load_prompt() == "second prompt"


def test_prompt_package_data_configuration_and_default_resolution(monkeypatch, tmp_path):
    from transcription.services.prompt_service import PromptService

    monkeypatch.chdir(tmp_path)
    prompt = PromptService.load_prompt()

    assert "document_type" in prompt
    assert "prompts/*.md" in (REPO_ROOT / "apps/transcription/pyproject.toml").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_lifespan_validates_prompt_without_schema_or_db(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from transcription.core.config import get_settings
    from transcription.main import lifespan
    from transcription.services.prompt_service import PromptService

    valid = tmp_path / "valid.md"
    valid.write_text("valid prompt", encoding="utf-8")
    monkeypatch.setenv("SYSTEM_PROMPT_PATH", str(valid))
    get_settings.cache_clear()
    PromptService.load_prompt.cache_clear()

    async with lifespan(FastAPI()):
        assert PromptService.load_prompt() == "valid prompt"

    invalid = tmp_path / "invalid.md"
    invalid.write_text("", encoding="utf-8")
    monkeypatch.setenv("SYSTEM_PROMPT_PATH", str(invalid))
    get_settings.cache_clear()
    PromptService.load_prompt.cache_clear()

    with pytest.raises(RuntimeError, match="SYSTEM_PROMPT_INVALID"):
        async with lifespan(FastAPI()):
            pass


@pytest.mark.asyncio
async def test_internal_runtime_prompt_failure_persists_failed_after_transaction_a(monkeypatch):
    from transcription.services.prompt_service import PromptConfigurationError

    class PromptFailProvider(AIProvider):
        model_name = "prompt-fail"

        def __init__(self):
            self.calls = 0

        async def extract(self, image_bytes: bytes) -> ExtractionResult:
            raise PromptConfigurationError("oversized")

    db = FakeDB()
    provider = PromptFailProvider()
    svc = InternalExtractionService(db=db, ai_provider=provider)
    meta = metadata()
    monkeypatch.setattr(
        "transcription.services.internal_extraction_service.run_validation_subprocess_sync",
        lambda *args: ValidationResult(True),
    )

    response = await svc.process(metadata=meta, file_bytes=valid_png(), declared_mime="image/png")

    assert response.status_code == 503
    assert response.body.error_code == "SYSTEM_PROMPT_INVALID"
    assert response.body.retryable is False
    assert db.requests[meta.request_id].status == RequestStatus.FAILED
    assert db.requests[meta.request_id].error_code == "SYSTEM_PROMPT_INVALID"
    assert db.usage_logs == []
    assert provider.calls == 0


def test_internal_route_runtime_prompt_failure_maps_503_without_provider_call(monkeypatch):
    from transcription.api.internal_extract import get_internal_ai_provider
    from transcription.database.session import get_db
    from transcription.main import app
    from transcription.services.prompt_service import PromptConfigurationError

    class PromptFailProvider(AIProvider):
        model_name = "prompt-fail"

        def __init__(self):
            self.calls = 0

        async def extract(self, image_bytes: bytes) -> ExtractionResult:
            raise PromptConfigurationError("oversized")

    db = FakeDB()
    provider = PromptFailProvider()
    monkeypatch.setattr(
        "transcription.services.internal_extraction_service.run_validation_subprocess_sync",
        lambda *args: ValidationResult(True),
    )
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_internal_ai_provider] = lambda: provider
    try:
        response = TestClient(app).post(
            "/internal/extract",
            headers={"Authorization": "Bearer test-token"},
            **multipart(data=valid_png()),
        )
    finally:
        app.dependency_overrides.clear()

    request = next(iter(db.requests.values()))
    assert response.status_code == 503
    assert response.json()["error_code"] == "SYSTEM_PROMPT_INVALID"
    assert response.json()["retryable"] is False
    assert request.status == RequestStatus.FAILED
    assert request.error_code == "SYSTEM_PROMPT_INVALID"
    assert db.usage_logs == []
    assert provider.calls == 0


def test_document_signature_accepts_approved_formats():
    assert detect_format(minimal_jpeg()) == "JPEG"
    assert detect_format(minimal_png()) == "PNG"
    assert detect_format(minimal_webp()) == "WEBP"


def test_pdf_validation_rejects_structural_active_content_but_not_plain_text_marker():
    pypdf = pytest.importorskip("pypdf")
    generic = pytest.importorskip("pypdf.generic")

    limits = ValidationLimits(100, 100, 10_000, 3, 100, 10)
    benign = generic.DictionaryObject(
        {
            generic.NameObject("/Info"): generic.TextStringObject(
                "ordinary text may mention /JS without declaring an action"
            )
        }
    )
    assert _walk_pdf_object(benign, limits).ok is True

    active = generic.DictionaryObject({generic.NameObject("/OpenAction"): generic.TextStringObject("alert")})
    result = _walk_pdf_object(active, limits)
    assert result == ValidationResult(False, "PDF_ACTIVE_CONTENT_UNSUPPORTED")

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_metadata({"/Subject": "plain /JS text only"})
    pdf_path = Path.cwd() / "benign-marker.pdf"
    with pdf_path.open("wb") as handle:
        writer.write(handle)
    data = pdf_path.read_bytes()
    pdf_path.unlink()

    assert validate_document_worker(ValidationInput(data, None, "PDF", limits)).ok is True


def test_pdf_validation_limits_and_malformed_pdf():
    pypdf = pytest.importorskip("pypdf")
    limits = ValidationLimits(100, 100, 10_000, 1, 100, 10)

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_blank_page(width=72, height=72)
    from io import BytesIO

    buffer = BytesIO()
    writer.write(buffer)
    assert (
        validate_document_worker(ValidationInput(buffer.getvalue(), None, "PDF", limits)).error_code
        == "PDF_PAGE_LIMIT_EXCEEDED"
    )

    assert validate_document_worker(ValidationInput(b"%PDF-1.7\nbroken", None, "PDF", limits)).error_code == "INVALID_PDF"


def test_pdf_validation_rejects_encrypted_pdf():
    pypdf = pytest.importorskip("pypdf")
    from io import BytesIO

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.encrypt("password")
    buffer = BytesIO()
    writer.write(buffer)

    limits = ValidationLimits(100, 100, 10_000, 2, 100, 10)
    assert (
        validate_document_worker(ValidationInput(buffer.getvalue(), None, "PDF", limits)).error_code
        == "PDF_ENCRYPTED"
    )


def test_pdf_object_traversal_limits_are_enforced():
    generic = pytest.importorskip("pypdf.generic")
    obj = generic.DictionaryObject()
    obj[generic.NameObject("/Nested")] = generic.DictionaryObject(
        {generic.NameObject("/Again"): generic.DictionaryObject()}
    )

    result = _walk_pdf_object(obj, ValidationLimits(100, 100, 10_000, 1, 100, 1))

    assert result == ValidationResult(False, "PDF_STRUCTURE_LIMIT_EXCEEDED")


class _MalformedResultPipe:
    def poll(self, timeout):
        return True

    def recv(self):
        return {"ok": True}

    def close(self):
        return None


class _TimeoutPipe:
    def poll(self, timeout):
        return False

    def close(self):
        return None


class _FakeChildPipe:
    def close(self):
        return None


class _FakeProcess:
    def __init__(self, *args, **kwargs):
        self.exitcode = 0
        self.terminated = False
        self.killed = False

    def start(self):
        return None

    def join(self, timeout=None):
        return None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def is_alive(self):
        return self.terminated and not self.killed


def test_validation_subprocess_rejects_malformed_ipc(monkeypatch):
    class FakeContext:
        def Pipe(self, duplex=False):
            return _MalformedResultPipe(), _FakeChildPipe()

        Process = _FakeProcess

    monkeypatch.setattr("transcription.services.document_validation.multiprocessing.get_context", lambda _: FakeContext())
    result = run_validation_subprocess_sync(
        ValidationInput(b"x", None, "PNG", ValidationLimits(1, 1, 1, 1, 1, 1)),
        0.01,
        0.01,
    )

    assert result == ValidationResult(False, "VALIDATION_PROCESS_FAILED")


def test_validation_subprocess_timeout_terminates_then_kills(monkeypatch):
    process = _FakeProcess()

    class FakeContext:
        def Pipe(self, duplex=False):
            return _TimeoutPipe(), _FakeChildPipe()

        def Process(self, *args, **kwargs):
            return process

    monkeypatch.setattr("transcription.services.document_validation.multiprocessing.get_context", lambda _: FakeContext())
    result = run_validation_subprocess_sync(
        ValidationInput(b"x", None, "PNG", ValidationLimits(1, 1, 1, 1, 1, 1)),
        0.01,
        0.01,
    )

    assert result == ValidationResult(False, "DOCUMENT_VALIDATION_TIMEOUT")
    assert process.terminated is True
    assert process.killed is True


@pytest.mark.asyncio
async def test_validation_capacity_pressure_returns_retryable_error(monkeypatch):
    import transcription.services.internal_extraction_service as service_module

    monkeypatch.setenv("VALIDATION_ACQUISITION_TIMEOUT_SECONDS", "0.001")
    service_module._validation_semaphore = None
    semaphore = service_module.get_validation_semaphore(1)
    await semaphore.acquire()
    from transcription.core.config import get_settings

    get_settings.cache_clear()
    svc = InternalExtractionService(db=FakeDB(), ai_provider=FakeProvider([]))
    try:
        result = await svc._run_validation(ValidationInput(b"x", None, "PNG", ValidationLimits(1, 1, 1, 1, 1, 1)))
    finally:
        semaphore.release()
        service_module._validation_semaphore = None

    assert result.ok is False
    assert result.error_code == "VALIDATION_CAPACITY_EXCEEDED"


@pytest.mark.asyncio
async def test_processing_row_is_committed_before_provider_call(monkeypatch):
    db = FakeDB()
    provider = FakeProvider([result()])
    svc = InternalExtractionService(db=db, ai_provider=provider)
    meta = metadata()

    monkeypatch.setattr(
        "transcription.services.internal_extraction_service.detect_format",
        lambda data: "PNG",
    )
    monkeypatch.setattr(
        "transcription.services.internal_extraction_service.run_validation_subprocess_sync",
        lambda *args: type("Validation", (), {"ok": True, "error_code": None})(),
    )

    response = await svc.process(
        metadata=meta,
        file_bytes=b"\x89PNG\r\n\x1a\nfake",
        declared_mime="image/png",
    )

    assert response.status_code == 200
    assert provider.calls == 1
    assert db.commits >= 2
    assert db.requests[meta.request_id].status == RequestStatus.SUCCEEDED


def test_processing_replay_does_not_call_provider():
    req_id = uuid.uuid4()
    existing = Request(id=req_id, status=RequestStatus.PROCESSING)
    db = FakeDB(existing)
    provider = FakeProvider([result()])
    svc = InternalExtractionService(db=db, ai_provider=provider)

    response = svc._response_for_existing(existing)

    assert response.status_code == 409
    assert response.body.error_code == "REQUEST_ALREADY_PROCESSING"
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_provider_retry_records_attempt_numbers():
    provider = FakeProvider([RuntimeError("503 unavailable"), result()])
    svc = InternalExtractionService(db=FakeDB(), ai_provider=provider)

    extracted, attempts, error, retryable, status_code, retry_after = await svc._call_provider_with_retry(b"x")

    assert extracted is not None
    assert [attempt.attempt_number for attempt in attempts] == [1, 2]
    assert attempts[0].status == "FAILED"
    assert attempts[0].sanitized_error_code == "PROVIDER_TEMPORARY_ERROR"
    assert attempts[1].status == "SUCCEEDED"
    assert error is None
    assert retryable is False
    assert status_code == 200
    assert retry_after is None


def test_provider_error_classification_is_sanitized():
    assert classify_provider_exception(RuntimeError("429 quota")) == (
        "PROVIDER_RATE_LIMITED",
        True,
        503,
        5,
    )
    assert classify_provider_exception(ValueError("secret details leaked")) == (
        "INTERNAL_ERROR",
        False,
        422,
        None,
    )


def test_transaction_b_failure_compensation_success(monkeypatch):
    meta = metadata()
    existing = Request(id=meta.request_id, status=RequestStatus.PROCESSING)
    db = FakeDB(existing)
    svc = InternalExtractionService(db=db, ai_provider=FakeProvider([]), compensation_session_factory=lambda: db)

    def fail_usage(*args, **kwargs):
        raise SQLAlchemyError("forced terminal persistence failure")

    monkeypatch.setattr(svc, "_add_usage_logs", fail_usage)
    response = svc._persist_succeeded(
        meta,
        result(),
        [],
        10,
        "image/png",
        SimpleNamespace(
            sha256_hex="0" * 64,
            detected_mime="image/png",
            size_bytes=10,
        ),
    )

    assert response.status_code == 500
    assert response.body.error_code == "PERSISTENCE_ERROR"
    assert response.body.retryable is False
    assert db.requests[meta.request_id].status == RequestStatus.PERSISTENCE_FAILED
    assert db.requests[meta.request_id].last_persistence_error_at is not None
    assert db.extractions == []
    assert db.usage_logs == []


def test_transaction_b_failure_compensation_failure_leaves_processing(monkeypatch):
    meta = metadata()
    existing = Request(id=meta.request_id, status=RequestStatus.PROCESSING)
    db = FakeDB(existing)
    db.fail_compensation_commit = True
    svc = InternalExtractionService(db=db, ai_provider=FakeProvider([]), compensation_session_factory=lambda: db)

    def fail_usage(*args, **kwargs):
        raise SQLAlchemyError("forced terminal persistence failure")

    monkeypatch.setattr(svc, "_add_usage_logs", fail_usage)
    response = svc._persist_succeeded(
        meta,
        result(),
        [],
        10,
        "image/png",
        SimpleNamespace(
            sha256_hex="0" * 64,
            detected_mime="image/png",
            size_bytes=10,
        ),
    )

    assert response.status_code == 500
    assert response.body.error_code == "PERSISTENCE_ERROR"
    assert response.body.retryable is False
    assert db.rollbacks >= 2
    assert db.extractions == []
    assert db.usage_logs == []
