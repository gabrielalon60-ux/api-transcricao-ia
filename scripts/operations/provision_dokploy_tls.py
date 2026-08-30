#!/usr/bin/env python3
"""Provision PostgreSQL TLS material into project-scoped named volume from base64 secrets."""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import stat
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def decode_b64_secret(var_name: str, raw_value: str | None) -> bytes:
    if not raw_value or not raw_value.strip():
        raise ValueError(f"Required TLS environment secret is empty or missing: {var_name}")
    try:
        decoded = base64.b64decode(raw_value.strip(), validate=True)
        if not decoded:
            raise ValueError(f"Decoded content for {var_name} is empty")
        return decoded
    except Exception as e:
        raise ValueError(f"Invalid base64 encoding in {var_name}: {e}") from e


def validate_and_provision_tls(
    target_dir: Path,
    ca_crt_bytes: bytes,
    server_crt_bytes: bytes,
    server_key_bytes: bytes,
    ca_target_dir: Path | None = None,
    overwrite: bool = False,
) -> dict[str, Path]:
    target_dir = target_dir.resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    ca_dir = ca_target_dir.resolve() if ca_target_dir else target_dir
    ca_dir.mkdir(parents=True, exist_ok=True)

    ca_crt_path = ca_dir / "ca.crt"
    server_crt_path = target_dir / "server.crt"
    server_key_path = target_dir / "server.key"
    forbidden_ca_key = target_dir / "ca.key"
    forbidden_ca_key_in_ca_dir = ca_dir / "ca.key"

    if forbidden_ca_key.exists() or forbidden_ca_key_in_ca_dir.exists():
        raise ValueError("SECURITY VIOLATION: ca.key detected in runtime directory")

    target_files = [ca_crt_path, server_crt_path, server_key_path]
    if not overwrite:
        existing = [str(f) for f in target_files if f.exists()]
        if existing:
            raise FileExistsError(
                f"Target TLS files already exist (use --overwrite to replace): {', '.join(existing)}"
            )

    # 1. Parse and validate CA certificate
    try:
        x509.load_pem_x509_certificate(ca_crt_bytes)
    except Exception as e:
        raise ValueError(f"Malformed CA certificate: {e}") from e

    # 2. Parse and validate Server certificate
    try:
        server_cert = x509.load_pem_x509_certificate(server_crt_bytes)
    except Exception as e:
        raise ValueError(f"Malformed server certificate: {e}") from e

    # 3. Validate Server Certificate SAN contains platform-db
    try:
        san_ext = server_cert.extensions.get_extension_for_oid(x509.ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
        san_value = san_ext.value
        dns_names = san_value.get_values_for_type(x509.DNSName)  # type: ignore[attr-defined]
        if "platform-db" not in dns_names:
            raise ValueError(f"Server certificate SAN missing required 'DNS:platform-db' (found: {dns_names})")
    except x509.ExtensionNotFound:
        raise ValueError("Server certificate missing Subject Alternative Name extension") from None

    # 4. Parse and validate Server Private Key
    try:
        server_key = serialization.load_pem_private_key(server_key_bytes, password=None)
    except Exception as e:
        raise ValueError(f"Malformed server private key: {e}") from e

    # 5. Validate Public Key Match between server cert and server key
    if isinstance(server_key, rsa.RSAPrivateKey):
        key_pub = server_key.public_key().public_numbers()
        cert_pub = server_cert.public_key().public_numbers()  # type: ignore[attr-defined]
        if key_pub != cert_pub:
            raise ValueError("Server private key does NOT match server certificate public key")

    # 6. Write files with strict POSIX permissions
    ca_crt_path.write_bytes(ca_crt_bytes)
    server_crt_path.write_bytes(server_crt_bytes)
    server_key_path.write_bytes(server_key_bytes)

    if os.name != "nt":
        os.chmod(ca_crt_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)  # 0644
        os.chmod(server_crt_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)  # 0644
        os.chmod(server_key_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600

    return {
        "ca_crt": ca_crt_path,
        "server_crt": server_crt_path,
        "server_key": server_key_path,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Provision PostgreSQL TLS material into named volume from secrets.")
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=Path(os.environ.get("POSTGRES_TLS_TARGET_DIR", "/var/lib/postgresql/certs")),
        help="Target directory/volume to write server TLS files",
    )
    parser.add_argument(
        "--ca-target-dir",
        type=Path,
        default=None,
        help="Target directory/volume to write public CA certificate (defaults to target-dir)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing TLS files in target directory",
    )
    args = parser.parse_args(argv)

    ca_b64 = os.environ.get("POSTGRES_CA_CERT_B64")
    server_crt_b64 = os.environ.get("POSTGRES_SERVER_CERT_B64")
    server_key_b64 = os.environ.get("POSTGRES_SERVER_KEY_B64")

    try:
        ca_bytes = decode_b64_secret("POSTGRES_CA_CERT_B64", ca_b64)
        server_crt_bytes = decode_b64_secret("POSTGRES_SERVER_CERT_B64", server_crt_b64)
        server_key_bytes = decode_b64_secret("POSTGRES_SERVER_KEY_B64", server_key_b64)

        results = validate_and_provision_tls(
            target_dir=args.target_dir,
            ca_crt_bytes=ca_bytes,
            server_crt_bytes=server_crt_bytes,
            server_key_bytes=server_key_bytes,
            ca_target_dir=args.ca_target_dir,
            overwrite=args.overwrite,
        )

        ca_fp = hashlib.sha256(ca_bytes).hexdigest()[:16]
        srv_fp = hashlib.sha256(server_crt_bytes).hexdigest()[:16]

        print("POSTGRESQL TLS MATERIAL PROVISIONED SUCCESSFULLY")
        print(f"  Target Directory:    {args.target_dir.resolve()}")
        print(f"  CA Certificate:      {results['ca_crt']} (SHA256:{ca_fp})")
        print(f"  Server Certificate:  {results['server_crt']} (SHA256:{srv_fp})")
        print(f"  Server Private Key:  {results['server_key']} (Mode 0600)")
        print("  CA Private Key:      NOT PRESENT (Root CA isolated on operator workstation)")
        return 0
    except Exception as e:
        print(f"ERROR: PostgreSQL TLS provisioning failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
