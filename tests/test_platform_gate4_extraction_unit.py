from __future__ import annotations

import uuid
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from orchestrator.wuzapi import WuzapiClient
from orchestrator.transcription_client import TranscriptionClient, TranscriptionClientError
from orchestrator.services.extraction_dispatcher import (
    apply_extraction_success,
    validate_structural_readiness,
    MAX_CONCURRENT_EXTRACTIONS_PER_SERVICE,
)


def test_extraction_settings_and_defaults():
    client = TranscriptionClient(base_url="http://transcription:8000", token="secret_token_123")
    assert client.base_url == "http://transcription:8000"
    assert client.token == "secret_token_123"
    assert MAX_CONCURRENT_EXTRACTIONS_PER_SERVICE == 5


def test_request_id_mapping_and_auth_header():
    item_id = str(uuid.uuid4())
    client = TranscriptionClient(token="my_token")
    assert client.token == "my_token"
    assert item_id is not None


def test_structural_readiness_validation():
    class DummyItem:
        document_type = "invoice"
        normalized_data = {"valor": 100.0, "data": "2026-08-04"}

    item_ok = DummyItem()
    assert validate_structural_readiness(item_ok) is True

    class DummyItemPix:
        document_type = "pix_receipt"
        normalized_data = {"valor": 50.0}

    assert validate_structural_readiness(DummyItemPix()) is True

    class DummyItemUnknownDoc:
        document_type = "unknown"
        normalized_data = {"valor": 100.0}

    assert validate_structural_readiness(DummyItemUnknownDoc()) is False

    class DummyItemMissingData:
        document_type = "invoice"
        normalized_data = None

    assert validate_structural_readiness(DummyItemMissingData()) is False

    class DummyItemEmptyData:
        document_type = "invoice"
        normalized_data = {}

    assert validate_structural_readiness(DummyItemEmptyData()) is False


def test_apply_extraction_success_physical_contract_fallback():
    """Physical contract: empty normalization falls back to non-empty extraction dict."""
    from db.models import ProcessingItem

    mock_db = MagicMock()
    mock_item = MagicMock(spec=ProcessingItem)
    mock_item.status = "EXTRACTING"
    mock_db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = mock_item

    payload = {
        "document_type": "bank_receipt",
        "extraction": {
            "amount": "50,00",
            "payment_date": "22/08/2026",
            "recipient_name": "Mercado Teste LTDA",
        },
        "normalization": {},
        "quality_flags": [],
        "confidence": None,
    }

    result = apply_extraction_success(mock_db, "item-1", None, payload)
    assert result is mock_item
    assert mock_item.document_type == "bank_receipt"
    assert mock_item.raw_extraction == payload["extraction"]
    assert mock_item.normalized_data == payload["extraction"]
    assert mock_item.status == "READY"
    assert mock_item.attempt_count == 0


def test_apply_extraction_success_explicit_normalization_precedence():
    """Explicit non-empty normalization takes precedence over raw extraction."""
    from db.models import ProcessingItem

    mock_db = MagicMock()
    mock_item = MagicMock(spec=ProcessingItem)
    mock_item.status = "EXTRACTING"
    mock_db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = mock_item

    payload = {
        "document_type": "invoice",
        "extraction": {"total_amount": "100.00", "supplier_name": "Acme Corp"},
        "normalization": {"amount": "100.00", "direction": "expense"},
        "quality_flags": [],
        "confidence": 0.95,
    }

    result = apply_extraction_success(mock_db, "item-2", None, payload)
    assert result is mock_item
    assert mock_item.document_type == "invoice"
    assert mock_item.raw_extraction == {"total_amount": "100.00", "supplier_name": "Acme Corp"}
    assert mock_item.normalized_data == {"amount": "100.00", "direction": "expense"}
    assert mock_item.status == "READY"


def test_apply_extraction_success_empty_payload_rejected():
    """Empty extraction and empty normalization fail structural readiness."""
    from db.models import ProcessingItem

    mock_db = MagicMock()
    mock_item = MagicMock(spec=ProcessingItem)
    mock_item.status = "EXTRACTING"
    mock_db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = mock_item

    payload = {
        "document_type": "bank_receipt",
        "extraction": {},
        "normalization": {},
        "quality_flags": [],
        "confidence": None,
    }

    result = apply_extraction_success(mock_db, "item-3", None, payload)
    assert result is mock_item
    assert mock_item.raw_extraction == {}
    assert mock_item.normalized_data == {}
    assert mock_item.status == "EXTRACTION_FAILED"
    assert mock_item.error_code == "INVALID_EXTRACTION_RESULT"


def test_apply_extraction_success_marks_unknown_document_explicitly():
    from db.models import ProcessingItem

    mock_db = MagicMock()
    mock_item = MagicMock(spec=ProcessingItem)
    mock_item.status = "EXTRACTING"
    mock_db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = mock_item

    result = apply_extraction_success(
        mock_db,
        "item-unknown",
        None,
        {
            "document_type": "unknown",
            "extraction": {},
            "normalization": {},
            "quality_flags": [],
            "confidence": None,
        },
    )

    assert result is mock_item
    assert mock_item.status == "EXTRACTION_FAILED"
    assert mock_item.error_code == "UNSUPPORTED_DOCUMENT"
    assert mock_item.error_message_sanitized == "UNSUPPORTED_DOCUMENT"


@pytest.mark.parametrize(
    ("raw", "norm"),
    [
        ("invalid_str", None),
        (["list_val"], {}),
        (None, "invalid_str"),
        (123, None),
    ],
)
def test_apply_extraction_success_malformed_types_fail_closed(raw: object, norm: object):
    """Malformed non-dict extraction/normalization fail closed to empty dict and EXTRACTION_FAILED."""
    from db.models import ProcessingItem

    mock_db = MagicMock()
    mock_item = MagicMock(spec=ProcessingItem)
    mock_item.status = "EXTRACTING"
    mock_db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = mock_item

    payload = {
        "document_type": "invoice",
        "extraction": raw,
        "normalization": norm,
        "quality_flags": [],
        "confidence": None,
    }

    result = apply_extraction_success(mock_db, "item-malformed", None, payload)
    assert result is mock_item
    assert isinstance(mock_item.raw_extraction, dict)
    assert isinstance(mock_item.normalized_data, dict)
    assert mock_item.status == "EXTRACTION_FAILED"


@pytest.mark.parametrize(
    "doc_type",
    ["invoice", "pix_receipt", "bank_receipt", "commercial_document"],
)
def test_apply_extraction_success_all_canonical_types_fallback(doc_type: str):
    """All 4 canonical Gate 3 types transition to READY when empty normalization falls back to extraction."""
    from db.models import ProcessingItem

    mock_db = MagicMock()
    mock_item = MagicMock(spec=ProcessingItem)
    mock_item.status = "EXTRACTING"
    mock_db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = mock_item

    payload = {
        "document_type": doc_type,
        "extraction": {"sample_field": "sample_value"},
        "normalization": {},
        "quality_flags": [],
        "confidence": None,
    }

    result = apply_extraction_success(mock_db, f"item-{doc_type}", None, payload)
    assert result is mock_item
    assert mock_item.document_type == doc_type
    assert mock_item.normalized_data == {"sample_field": "sample_value"}
    assert mock_item.status == "READY"


def test_error_sanitization_and_types():
    err = TranscriptionClientError("Extraction failed", status_code=422, error_code="UNSUPPORTED_DOCUMENT", retryable=False)
    assert err.status_code == 422
    assert err.error_code == "UNSUPPORTED_DOCUMENT"
    assert err.retryable is False


@pytest.mark.asyncio
async def test_wuzapi_download_media_contract():
    import base64
    client = WuzapiClient()
    client.base_url = "http://wuzapi-test:8080"
    client.token = "wuzapi_secret_token"
    client._headers["token"] = "wuzapi_secret_token"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "code": 200,
        "data": {
            "Mimetype": "image/jpeg",
            "Data": f"data:image/jpeg;base64,{base64.b64encode(b'MOCK_BINARY_IMAGE_BYTES').decode('utf-8')}",
        },
        "success": True,
    }

    mock_http_instance = AsyncMock()
    mock_http_instance.post.return_value = mock_resp
    mock_http_instance.__aenter__.return_value = mock_http_instance

    with patch("httpx.AsyncClient", return_value=mock_http_instance):
        data = await client.download_media(
            media_ref={
                "url": "https://mmg.whatsapp.net/d/f/123.enc",
                "media_key": "test_key",
                "direct_path": "/v/123.enc",
                "mime_type": "image/jpeg",
                "expected_sha256": "expected_sha",
                "expected_size": 123,
            },
            mime_type="image/jpeg",
        )
        assert data == b"MOCK_BINARY_IMAGE_BYTES"
        mock_http_instance.post.assert_called_once()
        assert mock_http_instance.post.call_args[0][0] == "http://wuzapi-test:8080/chat/downloadimage"
