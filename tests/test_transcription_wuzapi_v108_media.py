"""
Comprehensive Unit Tests for Transcription WUZAPI v1.0.8 Media Integrity Edge-Cases.

100% offline execution with zero real network or Gemini calls.
"""

import base64
import hashlib
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from transcription.core.config import Settings
from transcription.integrations.wuzapi import WuzapiClient, WuzapiError
from transcription.services.whatsapp_service import WhatsAppService


@pytest.fixture
def settings():
    return Settings(
        wuzapi_base_url="http://wuzapi:8080",
        wuzapi_instance="test_instance",
        wuzapi_token="test_wuzapi_token",
        gemini_api_key="test_gemini_key",
    )


@pytest.fixture
def wuzapi_client(settings):
    return WuzapiClient(settings)


@pytest.mark.asyncio
async def test_a_c_d_i_o_q_image_happy_path(wuzapi_client):
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

    with patch.object(wuzapi_client, "_request", new_callable=AsyncMock, return_value=mock_response) as mock_req:
        media_info = {
            "url": "https://mmg.whatsapp.net/d/f/img.enc",
            "directPath": "/v/t62.7118-24/img.enc",
            "mediaKey": "base64_media_key_str",
            "mimetype": "image/jpeg",
            "fileEncSHA256": "enc_sha_str",
            "fileSHA256": sha_b64,
            "fileLength": len(sample_bytes),
        }

        res = await wuzapi_client.download_media_v108("imageMessage", media_info, expected_mime="image/jpeg")
        assert res == sample_bytes
        mock_req.assert_called_once()
        payload = mock_req.call_args[1]["json_payload"]
        assert payload["MediaKey"] == "base64_media_key_str"
        assert payload["FileLength"] == len(sample_bytes)


@pytest.mark.asyncio
async def test_b_document_happy_path(wuzapi_client):
    sample_bytes = b"%PDF-1.4 fake_pdf_content"
    encoded_b64 = base64.b64encode(sample_bytes).decode("utf-8")
    data_url = f"data:application/pdf;base64,{encoded_b64}"
    sha_b64 = base64.b64encode(hashlib.sha256(sample_bytes).digest()).decode("utf-8")

    mock_response = httpx.Response(
        status_code=200,
        json={"Mimetype": "application/pdf", "Data": data_url},
        request=httpx.Request("POST", "http://wuzapi:8080/chat/downloaddocument"),
    )

    with patch.object(wuzapi_client, "_request", new_callable=AsyncMock, return_value=mock_response) as mock_req:
        media_info = {
            "url": "https://mmg.whatsapp.net/d/f/doc.enc",
            "directPath": "/v/t62.7118-24/doc.enc",
            "mediaKey": "key",
            "mimetype": "application/pdf",
            "fileEncSHA256": "enc",
            "fileSHA256": sha_b64,
            "fileLength": len(sample_bytes),
        }

        res = await wuzapi_client.download_media_v108("documentMessage", media_info, expected_mime="application/pdf")
        assert res == sample_bytes
        assert mock_req.call_args[0][1] == "http://wuzapi:8080/chat/downloaddocument"


@pytest.mark.asyncio
async def test_e_f_g_crypto_field_bytes_conversion(wuzapi_client):
    sample_bytes = b"sample_bytes_for_sha"
    data_url = f"data:image/png;base64,{base64.b64encode(sample_bytes).decode('utf-8')}"
    sha_bytes = hashlib.sha256(sample_bytes).digest()

    mock_response = httpx.Response(status_code=200, json={"Mimetype": "image/png", "Data": data_url})

    with patch.object(wuzapi_client, "_request", new_callable=AsyncMock, return_value=mock_response) as mock_req:
        raw_key_bytes = b"\x01\x02\x03\x04"
        media_info = {
            "mediaKey": raw_key_bytes,
            "fileEncSHA256": b"\x05\x06",
            "fileSHA256": sha_bytes,  # passed as Python bytes matching actual sha256 digest
            "fileLength": len(sample_bytes),
        }

        res = await wuzapi_client.download_media_v108("imageMessage", media_info, expected_mime="image/png")
        assert res == sample_bytes
        payload = mock_req.call_args[1]["json_payload"]
        assert payload["MediaKey"] == base64.b64encode(raw_key_bytes).decode("ascii")
        assert payload["FileSHA256"] == base64.b64encode(sha_bytes).decode("ascii")


@pytest.mark.asyncio
async def test_h_unsupported_crypto_field_type(wuzapi_client):
    with pytest.raises(WuzapiError, match="Unsupported type 'int'"):
        await wuzapi_client.download_media_v108("imageMessage", {"mediaKey": 12345})


@pytest.mark.asyncio
async def test_missing_required_mediakey(wuzapi_client):
    with pytest.raises(WuzapiError, match="Missing required cryptographic field 'MediaKey'"):
        await wuzapi_client.download_media_v108("imageMessage", {"mediaKey": ""})


@pytest.mark.asyncio
async def test_response_mimetype_requirements(wuzapi_client):
    sample_bytes = b"sample_bytes"
    data_url = f"data:image/png;base64,{base64.b64encode(sample_bytes).decode('utf-8')}"

    # Missing Mimetype
    mock_resp_missing = httpx.Response(status_code=200, json={"Data": data_url})
    with patch.object(wuzapi_client, "_request", new_callable=AsyncMock, return_value=mock_resp_missing):
        with pytest.raises(WuzapiError, match="Missing or invalid Mimetype"):
            await wuzapi_client.download_media_v108("imageMessage", {"mediaKey": "key"}, expected_mime="image/png")

    # Mimetype null/empty
    mock_resp_empty = httpx.Response(status_code=200, json={"Mimetype": "", "Data": data_url})
    with patch.object(wuzapi_client, "_request", new_callable=AsyncMock, return_value=mock_resp_empty):
        with pytest.raises(WuzapiError, match="Missing or invalid Mimetype"):
            await wuzapi_client.download_media_v108("imageMessage", {"mediaKey": "key"}, expected_mime="image/png")


@pytest.mark.asyncio
async def test_empty_data_url_mime_segment(wuzapi_client):
    sample_bytes = b"sample_bytes"
    data_url = f"data:;base64,{base64.b64encode(sample_bytes).decode('utf-8')}"
    mock_resp = httpx.Response(status_code=200, json={"Mimetype": "image/png", "Data": data_url})
    with patch.object(wuzapi_client, "_request", new_callable=AsyncMock, return_value=mock_resp):
        with pytest.raises(WuzapiError, match="Data URL contains empty MIME type segment"):
            await wuzapi_client.download_media_v108("imageMessage", {"mediaKey": "key"})


@pytest.mark.asyncio
async def test_j_k_l_invalid_data_url_and_strict_base64(wuzapi_client):
    # Missing data: prefix
    mock_resp_prefix = httpx.Response(status_code=200, json={"Mimetype": "image/png", "Data": "http://invalid.url"})
    with patch.object(wuzapi_client, "_request", new_callable=AsyncMock, return_value=mock_resp_prefix):
        with pytest.raises(WuzapiError, match="Invalid or missing Data URL"):
            await wuzapi_client.download_media_v108("imageMessage", {"mediaKey": "key"})

    # Invalid base64 characters (%%%%)
    mock_resp_b64 = httpx.Response(status_code=200, json={"Mimetype": "image/png", "Data": "data:image/png;base64,%%%%"})
    with patch.object(wuzapi_client, "_request", new_callable=AsyncMock, return_value=mock_resp_b64):
        with pytest.raises(WuzapiError, match="Failed to decode base64"):
            await wuzapi_client.download_media_v108("imageMessage", {"mediaKey": "key"})

    # Empty payload
    mock_resp_empty = httpx.Response(status_code=200, json={"Mimetype": "image/png", "Data": "data:image/png;base64,"})
    with patch.object(wuzapi_client, "_request", new_callable=AsyncMock, return_value=mock_resp_empty):
        with pytest.raises(WuzapiError, match="empty"):
            await wuzapi_client.download_media_v108("imageMessage", {"mediaKey": "key"})


@pytest.mark.asyncio
async def test_m_n_mime_mismatches(wuzapi_client):
    encoded_b64 = base64.b64encode(b"content").decode("utf-8")

    # Response MIME mismatch
    mock_resp_res = httpx.Response(status_code=200, json={"Mimetype": "image/png", "Data": f"data:image/png;base64,{encoded_b64}"})
    with patch.object(wuzapi_client, "_request", new_callable=AsyncMock, return_value=mock_resp_res):
        with pytest.raises(WuzapiError, match="MIME type mismatch"):
            await wuzapi_client.download_media_v108("imageMessage", {"mediaKey": "key"}, expected_mime="application/pdf")

    # Data URL MIME mismatch
    mock_resp_data = httpx.Response(status_code=200, json={"Mimetype": "image/png", "Data": f"data:image/gif;base64,{encoded_b64}"})
    with patch.object(wuzapi_client, "_request", new_callable=AsyncMock, return_value=mock_resp_data):
        with pytest.raises(WuzapiError, match="MIME type mismatch"):
            await wuzapi_client.download_media_v108("imageMessage", {"mediaKey": "key"}, expected_mime="image/png")


@pytest.mark.asyncio
async def test_p_r_s_t_size_and_sha_mismatches(wuzapi_client):
    sample_bytes = b"real_bytes"
    encoded_b64 = base64.b64encode(sample_bytes).decode("utf-8")
    data_url = f"data:image/png;base64,{encoded_b64}"
    mock_resp = httpx.Response(status_code=200, json={"Mimetype": "image/png", "Data": data_url})

    with patch.object(wuzapi_client, "_request", new_callable=AsyncMock, return_value=mock_resp):
        # Size mismatch (expected 999, got len(sample_bytes))
        with pytest.raises(WuzapiError, match="File size mismatch"):
            await wuzapi_client.download_media_v108("imageMessage", {"mediaKey": "key", "fileLength": 999})

        # SHA mismatch (arbitrary 32-byte digest b"0"*32 != sha256(sample_bytes))
        wrong_sha_b64 = base64.b64encode(b"0" * 32).decode("utf-8")
        with pytest.raises(WuzapiError, match="digest mismatch"):
            await wuzapi_client.download_media_v108("imageMessage", {"mediaKey": "key", "fileSHA256": wrong_sha_b64})

        # Malformed SHA base64
        with pytest.raises(WuzapiError, match="Invalid expected FileSHA256"):
            await wuzapi_client.download_media_v108("imageMessage", {"mediaKey": "key", "fileSHA256": "%%%invalid%%%"})

        # SHA decoded length != 32
        short_sha_b64 = base64.b64encode(b"too_short").decode("utf-8")
        with pytest.raises(WuzapiError, match="Invalid FileSHA256 length"):
            await wuzapi_client.download_media_v108("imageMessage", {"mediaKey": "key", "fileSHA256": short_sha_b64})


@pytest.mark.asyncio
async def test_u_v_w_file_length_validations(wuzapi_client):
    with pytest.raises(WuzapiError, match="numeric"):
        await wuzapi_client.download_media_v108("imageMessage", {"mediaKey": "key", "fileLength": "not_a_number"})

    with pytest.raises(WuzapiError, match="positive integer"):
        await wuzapi_client.download_media_v108("imageMessage", {"mediaKey": "key", "fileLength": -10})

    with pytest.raises(WuzapiError, match="missing or boolean"):
        await wuzapi_client.download_media_v108("imageMessage", {"mediaKey": "key", "fileLength": True})


@pytest.mark.asyncio
async def test_x_y_z_http_errors_and_timeout(wuzapi_client):
    with patch.object(wuzapi_client, "_request", new_callable=AsyncMock, side_effect=WuzapiError("HTTP 400")):
        with pytest.raises(WuzapiError, match="HTTP 400"):
            await wuzapi_client.download_media_v108("imageMessage", {"mediaKey": "key"})

    with patch.object(wuzapi_client, "_request", new_callable=AsyncMock, side_effect=WuzapiError("HTTP 500")):
        with pytest.raises(WuzapiError, match="HTTP 500"):
            await wuzapi_client.download_media_v108("imageMessage", {"mediaKey": "key"})

    with patch.object(wuzapi_client, "_request", new_callable=AsyncMock, side_effect=WuzapiError("Request timeout")):
        with pytest.raises(WuzapiError, match="timeout"):
            await wuzapi_client.download_media_v108("imageMessage", {"mediaKey": "key"})


@pytest.mark.asyncio
async def test_aa_ab_ac_active_path_isolation():
    mock_db = MagicMock()
    mock_ai = MagicMock()
    mock_wuzapi = MagicMock(spec=WuzapiClient)
    mock_wuzapi.get_media_info = AsyncMock()
    mock_wuzapi.download_media = AsyncMock()
    mock_wuzapi.download_media_v108 = AsyncMock(return_value=b"fake_image_bytes")
    mock_wuzapi.send_text_message = AsyncMock()
    mock_formatter = MagicMock()

    service = WhatsAppService(
        db=mock_db,
        ai_provider=mock_ai,
        wuzapi=mock_wuzapi,
        formatter=mock_formatter,
        application_id=MagicMock(),
    )

    payload = {
        "event": "messages.upsert",
        "data": {
            "message": {
                "key": {"remoteJid": "5511999999999@s.whatsapp.net", "id": "msg_123", "fromMe": False},
                "imageMessage": {
                    "mimetype": "image/jpeg",
                    "url": "https://mmg.whatsapp.net/d/f/img.enc",
                    "directPath": "/v/t62.7118-24/img.enc",
                    "mediaKey": "key",
                },
            }
        },
    }

    await service.handle_webhook(payload)

    mock_wuzapi.get_media_info.assert_not_called()
    mock_wuzapi.download_media.assert_not_called()
    mock_wuzapi.download_media_v108.assert_called_once()
