import base64
import hashlib
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from orchestrator.config import Settings
from orchestrator.wuzapi import WuzapiClient, WuzapiError


@pytest.fixture
def orchestrator_settings(monkeypatch):
    settings = Settings(
        database_url="sqlite:///:memory:",
        wuzapi_base_url="http://wuzapi:8080",
        wuzapi_token="test_wuzapi_admin_token",
        wuzapi_webhook_secret="test_secret",
        transcription_service_url="http://transcription:8001",
        db_writer_url="http://db-writer:8004",
        bot_df_url="http://bot-df:8003",
        log_pii_hash_key="test_hash_key",
        api_key_hash_secret="test_api_key_secret",
        registration_secret_pepper="test_pepper",
    )
    monkeypatch.setattr("orchestrator.wuzapi.get_settings", lambda: settings)
    return settings


@pytest.fixture
def wuzapi_client(orchestrator_settings):
    return WuzapiClient()


def _valid_media_ref(**overrides):
    media_ref = {
        "url": "https://mmg.whatsapp.net/d/f/media.enc",
        "direct_path": "/v/t62.7118-24/media.enc",
        "media_key": "base64_media_key_str",
        "mime_type": "image/jpeg",
        "file_enc_sha256": "enc_sha_str",
        "expected_sha256": "expected_sha_str",
        "expected_size": 1234,
    }
    media_ref.update(overrides)
    return media_ref


@pytest.mark.asyncio
async def test_download_image_native_endpoint_and_payload(wuzapi_client):
    sample_bytes = b"fake_jpeg_binary_content"
    encoded_b64 = base64.b64encode(sample_bytes).decode("utf-8")
    data_url = f"data:image/jpeg;base64,{encoded_b64}"
    sha_bytes = hashlib.sha256(sample_bytes).digest()
    sha_b64 = base64.b64encode(sha_bytes).decode("utf-8")

    mock_response = httpx.Response(
        status_code=200,
        json={"Mimetype": "image/jpeg", "Data": data_url},
        request=httpx.Request("POST", "http://wuzapi:8080/chat/downloadimage"),
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
        media_ref = _valid_media_ref(
            url="https://mmg.whatsapp.net/d/f/img.enc",
            direct_path="/v/t62.7118-24/img.enc",
            expected_sha256=sha_b64,
            expected_size=len(sample_bytes),
        )

        res = await wuzapi_client.download_media(media_ref, mime_type="image/jpeg")
        assert res == sample_bytes
        mock_post.assert_called_once()
        call_url = mock_post.call_args[0][0]
        assert call_url == "http://wuzapi:8080/chat/downloadimage"
        payload = mock_post.call_args[1]["json"]
        assert payload["MediaKey"] == "base64_media_key_str"
        assert payload["DirectPath"] == "/v/t62.7118-24/img.enc"
        assert payload["FileLength"] == len(sample_bytes)
        assert payload["Mimetype"] == "image/jpeg"


@pytest.mark.asyncio
async def test_download_document_native_endpoint_and_payload(wuzapi_client):
    sample_bytes = b"%PDF-1.4 fake_pdf_content"
    encoded_b64 = base64.b64encode(sample_bytes).decode("utf-8")
    data_url = f"data:application/pdf;base64,{encoded_b64}"

    mock_response = httpx.Response(
        status_code=200,
        json={"Mimetype": "application/pdf", "Data": data_url},
        request=httpx.Request("POST", "http://wuzapi:8080/chat/downloaddocument"),
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
        media_ref = _valid_media_ref(
            direct_path="/v/t62.7118-24/doc.enc",
            media_key="doc_key",
            mime_type="application/pdf",
        )

        res = await wuzapi_client.download_media(media_ref, mime_type="application/pdf")
        assert res == sample_bytes
        assert mock_post.call_args[0][0] == "http://wuzapi:8080/chat/downloaddocument"


@pytest.mark.asyncio
async def test_download_audio_video_sticker_endpoints(wuzapi_client):
    sample_bytes = b"media_bytes"
    data_url = f"data:audio/ogg;base64,{base64.b64encode(sample_bytes).decode('utf-8')}"

    mock_response = httpx.Response(
        status_code=200,
        json={"Mimetype": "audio/ogg", "Data": data_url},
        request=httpx.Request("POST", "http://wuzapi:8080/chat/downloadaudio"),
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
        media_ref = _valid_media_ref(media_key="key", mime_type="audio/ogg")
        res = await wuzapi_client.download_media(media_ref, mime_type="audio/ogg")
        assert res == sample_bytes
        assert mock_post.call_args[0][0] == "http://wuzapi:8080/chat/downloadaudio"


@pytest.mark.asyncio
async def test_download_missing_media_key_fails_closed_deterministic(wuzapi_client):
    media_ref = _valid_media_ref(media_key="")
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        with pytest.raises(WuzapiError) as exc_info:
            await wuzapi_client.download_media(media_ref, mime_type="image/jpeg")
        assert exc_info.value.retryable is False
        assert exc_info.value.reason == "MISSING_CRYPTO_FIELD"
        mock_post.assert_not_called()


@pytest.mark.asyncio
async def test_download_missing_plaintext_hash_fails_before_http(wuzapi_client):
    media_ref = _valid_media_ref(expected_sha256="")
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        with pytest.raises(WuzapiError) as exc_info:
            await wuzapi_client.download_media(media_ref, mime_type="image/jpeg")
        assert exc_info.value.retryable is False
        assert exc_info.value.reason == "MISSING_CRYPTO_FIELD"
        mock_post.assert_not_called()


@pytest.mark.asyncio
async def test_download_404_classified_non_retryable(wuzapi_client):
    mock_response = httpx.Response(
        status_code=404,
        text="Not Found",
        request=httpx.Request("POST", "http://wuzapi:8080/chat/downloadimage"),
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        media_ref = _valid_media_ref(media_key="key")
        with pytest.raises(WuzapiError) as exc_info:
            await wuzapi_client.download_media(media_ref, mime_type="image/jpeg")
        assert exc_info.value.retryable is False
        assert exc_info.value.http_status == 404
        assert exc_info.value.reason == "NOT_FOUND_404"


@pytest.mark.asyncio
async def test_download_500_classified_retryable(wuzapi_client):
    mock_response = httpx.Response(
        status_code=500,
        text="Internal Server Error",
        request=httpx.Request("POST", "http://wuzapi:8080/chat/downloadimage"),
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        media_ref = _valid_media_ref(media_key="key")
        with pytest.raises(WuzapiError) as exc_info:
            await wuzapi_client.download_media(media_ref, mime_type="image/jpeg")
        assert exc_info.value.retryable is True
        assert exc_info.value.http_status == 500


@pytest.mark.asyncio
async def test_download_integrity_500_classified_non_retryable(wuzapi_client):
    mock_response = httpx.Response(
        status_code=500,
        json={
            "code": 500,
            "error": "failed to download image hash of media plaintext doesn't match",
            "success": False,
        },
        request=httpx.Request("POST", "http://wuzapi:8080/chat/downloadimage"),
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        with pytest.raises(WuzapiError) as exc_info:
            await wuzapi_client.download_media(_valid_media_ref(), mime_type="image/jpeg")
        assert exc_info.value.retryable is False
        assert exc_info.value.http_status == 500
        assert exc_info.value.reason == "MEDIA_INTEGRITY_FAILURE"


@pytest.mark.asyncio
async def test_download_timeout_classified_retryable(wuzapi_client):
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=httpx.TimeoutException("Timeout")):
        media_ref = _valid_media_ref(media_key="key")
        with pytest.raises(WuzapiError) as exc_info:
            await wuzapi_client.download_media(media_ref, mime_type="image/jpeg")
        assert exc_info.value.retryable is True
        assert exc_info.value.reason == "TIMEOUT"


@pytest.mark.asyncio
async def test_download_malformed_base64_fails_closed(wuzapi_client):
    mock_response = httpx.Response(
        status_code=200,
        json={"Mimetype": "image/jpeg", "Data": "data:image/jpeg;base64,@@@@NOT_BASE64@@@@"},
        request=httpx.Request("POST", "http://wuzapi:8080/chat/downloadimage"),
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        media_ref = _valid_media_ref(media_key="key")
        with pytest.raises(WuzapiError) as exc_info:
            await wuzapi_client.download_media(media_ref, mime_type="image/jpeg")
        assert exc_info.value.retryable is False
        assert exc_info.value.reason == "BASE64_DECODE_ERROR"


@pytest.mark.asyncio
async def test_download_size_limit_exceeded(wuzapi_client):
    huge_bytes = b"A" * (11 * 1024 * 1024)
    data_url = f"data:image/jpeg;base64,{base64.b64encode(huge_bytes).decode('utf-8')}"
    mock_response = httpx.Response(
        status_code=200,
        json={"Mimetype": "image/jpeg", "Data": data_url},
        request=httpx.Request("POST", "http://wuzapi:8080/chat/downloadimage"),
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        media_ref = _valid_media_ref(media_key="key")
        with pytest.raises(WuzapiError) as exc_info:
            await wuzapi_client.download_media(media_ref, mime_type="image/jpeg")
        assert exc_info.value.retryable is False
        assert exc_info.value.reason == "FILE_SIZE_LIMIT_EXCEEDED"
