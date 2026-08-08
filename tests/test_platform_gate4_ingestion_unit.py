from __future__ import annotations

import pytest
from pydantic import ValidationError

from orchestrator.config import Settings
from orchestrator.payload import compute_payload_hash, is_payload_mutated


def test_1_canonical_payload_hashing_determinism() -> None:
    file_info1 = {
        "provider": "WUZAPI",
        "external_instance_id": "inst-1",
        "external_message_id": "msg-100",
        "message_type": "image",
        "file_mime_type": "image/jpeg",
        "file_size": 2048,
        "file_sha256": "abc123sha256",
        "original_filename": "receipt.jpg",
    }
    file_info2 = dict(file_info1)

    hash1 = compute_payload_hash(file_info1)
    hash2 = compute_payload_hash(file_info2)

    assert hash1 == hash2
    assert len(hash1) == 64  # Hex SHA-256


def test_2_volatile_fields_excluded_from_payload_hash() -> None:
    base_info = {
        "provider": "WUZAPI",
        "external_instance_id": "inst-1",
        "external_message_id": "msg-100",
        "message_type": "image",
        "file_mime_type": "image/jpeg",
        "file_size": 2048,
        "file_sha256": "abc123sha256",
    }

    info_with_volatile_1 = dict(base_info)
    info_with_volatile_1["received_at"] = "2026-08-04T20:00:00Z"
    info_with_volatile_1["correlation_id"] = "corr-1"
    info_with_volatile_1["header_signature"] = "sig-123"

    info_with_volatile_2 = dict(base_info)
    info_with_volatile_2["received_at"] = "2026-08-04T21:30:00Z"
    info_with_volatile_2["correlation_id"] = "corr-999"
    info_with_volatile_2["header_signature"] = "sig-999"

    hash1 = compute_payload_hash(info_with_volatile_1)
    hash2 = compute_payload_hash(info_with_volatile_2)

    assert hash1 == hash2


def test_3_immutable_field_change_changes_hash() -> None:
    base_info = {
        "provider": "WUZAPI",
        "external_instance_id": "inst-1",
        "external_message_id": "msg-100",
        "message_type": "image",
        "file_mime_type": "image/jpeg",
        "file_size": 2048,
        "file_sha256": "abc123sha256",
    }
    hash_base = compute_payload_hash(base_info)

    # Change file_size
    info_size = dict(base_info, file_size=4096)
    assert compute_payload_hash(info_size) != hash_base

    # Change file_mime_type
    info_mime = dict(base_info, file_mime_type="image/png")
    assert compute_payload_hash(info_mime) != hash_base

    # Change message_type
    info_type = dict(base_info, message_type="pdf")
    assert compute_payload_hash(info_type) != hash_base


def test_4_text_only_payload_conflicts_detected() -> None:
    txt_info1 = {
        "provider": "WUZAPI",
        "external_instance_id": "inst-1",
        "external_message_id": "msg-txt-1",
        "message_type": "text",
        "text_content": "Despesa almoço 50",
    }
    txt_info2 = dict(txt_info1, text_content="Despesa almoço 500")

    h1 = compute_payload_hash(txt_info1)
    h2 = compute_payload_hash(txt_info2)

    assert h1 != h2
    assert is_payload_mutated(h1, h2)


def test_5_media_payload_conflicts_detected() -> None:
    media_info1 = {
        "provider": "WUZAPI",
        "external_instance_id": "inst-1",
        "external_message_id": "msg-m-1",
        "message_type": "image",
        "file_sha256": "sha_v1",
        "file_size": 1000,
    }
    media_info2 = dict(media_info1, file_sha256="sha_v2_mutated")

    h1 = compute_payload_hash(media_info1)
    h2 = compute_payload_hash(media_info2)

    assert h1 != h2
    assert is_payload_mutated(h1, h2)


def test_max_queue_items_setting_validation() -> None:
    s = Settings(database_url="postgresql://localhost/db", max_queue_items_per_conversation=10)
    assert s.max_queue_items_per_conversation == 10

    with pytest.raises(ValidationError):
        Settings(database_url="postgresql://localhost/db", max_queue_items_per_conversation=0)

    with pytest.raises(ValidationError):
        Settings(database_url="postgresql://localhost/db", max_queue_items_per_conversation=-5)
