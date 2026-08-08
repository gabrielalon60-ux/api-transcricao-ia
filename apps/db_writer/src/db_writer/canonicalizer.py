from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any, Dict


def _normalize_item(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return f"{obj:.2f}"
    elif isinstance(obj, float):
        return f"{Decimal(str(obj)):.2f}"
    elif isinstance(obj, dict):
        return {k: _normalize_item(v) for k, v in sorted(obj.items())}
    elif isinstance(obj, list):
        return [_normalize_item(v) for v in obj]
    return obj


def canonicalize_payload(payload: Dict[str, Any]) -> str:
    """Produces a deterministic SHA-256 hex digest for a business payload.

    Invariants:
      - Recursive dict key sorting (sort_keys=True, no whitespace separators).
      - Fixed 2-decimal representation for amounts & Decimals.
      - Explicit null preservation.
      - UTF-8 Unicode encoding.
    """
    normalized = _normalize_item(payload)
    serialized = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
