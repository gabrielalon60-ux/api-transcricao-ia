#!/usr/bin/env python3
"""Thin operational CLI wrapper for PostgreSQL TLS provisioning."""

from __future__ import annotations

import sys
from pathlib import Path

# Add packages/security/src to sys.path if invoked standalone without workspace environment
_SRC = Path(__file__).resolve().parents[2] / "packages" / "security" / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from security.tls_provisioner import (  # noqa: E402
    decode_b64_secret,
    main,
    validate_and_provision_tls,
)

__all__ = ["decode_b64_secret", "validate_and_provision_tls", "main"]

if __name__ == "__main__":
    sys.exit(main())
