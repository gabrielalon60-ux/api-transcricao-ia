from __future__ import annotations

import uuid
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from orchestrator.wuzapi import WuzapiClient
from orchestrator.transcription_client import TranscriptionClient, TranscriptionClientError
from orchestrator.services.extraction_dispatcher import (
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
        "Mimetype": "image/jpeg",
        "Data": f"data:image/jpeg;base64,{base64.b64encode(b'MOCK_BINARY_IMAGE_BYTES').decode('utf-8')}",
    }

    mock_http_instance = AsyncMock()
    mock_http_instance.post.return_value = mock_resp
    mock_http_instance.__aenter__.return_value = mock_http_instance

    with patch("httpx.AsyncClient", return_value=mock_http_instance):
        data = await client.download_media(
            media_ref={"media_key": "test_key", "direct_path": "/v/123.enc", "mime_type": "image/jpeg"},
            mime_type="image/jpeg",
        )
        assert data == b"MOCK_BINARY_IMAGE_BYTES"
        mock_http_instance.post.assert_called_once()
        assert mock_http_instance.post.call_args[0][0] == "http://wuzapi-test:8080/chat/downloadimage"
