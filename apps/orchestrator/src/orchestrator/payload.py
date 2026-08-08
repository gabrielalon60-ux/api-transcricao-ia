from __future__ import annotations

import hashlib
import json
from typing import Any, Dict


def compute_payload_hash(file_info: Dict[str, Any]) -> str:
    """Computes a deterministic SHA-256 hash of immutable event payload fields.

    Included immutable fields:
      - provider
      - external_instance_id
      - external_message_id
      - message_type
      - file_mime_type
      - file_size
      - file_sha256
      - original_filename
      - text_content (if text message)

    Excluded volatile fields:
      - received_at / timestamps
      - correlation_id
      - headers / signatures
    """
    immutable_keys = [
        "provider",
        "external_instance_id",
        "external_message_id",
        "message_type",
        "file_mime_type",
        "file_size",
        "file_sha256",
        "original_filename",
        "text_content",
    ]

    canonical_data = {
        key: str(file_info[key])
        for key in sorted(immutable_keys)
        if key in file_info and file_info[key] is not None
    }

    canonical_json = json.dumps(canonical_data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def is_payload_mutated(existing_hash: str | None, current_hash: str) -> bool:
    """Returns True if existing hash is present and differs from current hash."""
    if not existing_hash:
        return False
    return existing_hash != current_hash
