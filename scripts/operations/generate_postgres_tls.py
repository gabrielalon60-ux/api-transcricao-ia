#!/usr/bin/env python3
"""Generate dedicated PostgreSQL TLS certificates and private staging CA."""

from __future__ import annotations

import argparse
import datetime
import ipaddress
import os
import stat
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def generate_postgres_tls(
    runtime_dir: Path,
    ca_private_dir: Path,
    sans: list[str] | None = None,
    days: int = 365,
    overwrite: bool = False,
) -> dict[str, Path]:
    runtime_dir = runtime_dir.resolve()
    ca_private_dir = ca_private_dir.resolve()

    if runtime_dir == ca_private_dir:
        raise ValueError("runtime-dir and ca-private-dir must be distinct directories")

    ca_crt_path = runtime_dir / "ca.crt"
    server_crt_path = runtime_dir / "server.crt"
    server_key_path = runtime_dir / "server.key"
    ca_key_path = ca_private_dir / "ca.key"

    target_files = [ca_crt_path, server_crt_path, server_key_path, ca_key_path]
    if not overwrite:
        existing = [str(f) for f in target_files if f.exists()]
        if existing:
            raise FileExistsError(
                f"Target TLS files already exist (use --overwrite to replace): {', '.join(existing)}"
            )

    runtime_dir.mkdir(parents=True, exist_ok=True)
    ca_private_dir.mkdir(parents=True, exist_ok=True)

    if os.name != "nt":
        os.chmod(ca_private_dir, stat.S_IRWXU)  # 0700

    # 1. Generate Root CA Private Key & Self-Signed Certificate
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    ca_name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "Staging Platform Root CA"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "API Transcricao IA"),
        ]
    )
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=days * 2))
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )

    # 2. Generate Server Private Key & Certificate signed by Root CA
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "platform-db"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "API Transcricao IA"),
        ]
    )

    san_list = sans or ["platform-db", "localhost", "127.0.0.1"]
    san_entries: list[x509.GeneralName] = []
    for entry in san_list:
        try:
            ip = ipaddress.ip_address(entry)
            san_entries.append(x509.IPAddress(ip))
        except ValueError:
            san_entries.append(x509.DNSName(entry))

    server_cert = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.SubjectAlternativeName(san_entries),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    # 3. Write CA Private Key to isolated directory
    ca_key_bytes = ca_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    ca_key_path.write_bytes(ca_key_bytes)
    if os.name != "nt":
        os.chmod(ca_key_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600

    # 4. Write Runtime Artifacts
    ca_crt_bytes = ca_cert.public_bytes(serialization.Encoding.PEM)
    ca_crt_path.write_bytes(ca_crt_bytes)

    server_crt_bytes = server_cert.public_bytes(serialization.Encoding.PEM)
    server_crt_path.write_bytes(server_crt_bytes)

    server_key_bytes = server_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    server_key_path.write_bytes(server_key_bytes)

    if os.name != "nt":
        os.chmod(ca_crt_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)  # 0644
        os.chmod(server_crt_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)  # 0644
        os.chmod(server_key_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600

    return {
        "ca_crt": ca_crt_path,
        "ca_key": ca_key_path,
        "server_crt": server_crt_path,
        "server_key": server_key_path,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate PostgreSQL TLS certificates for staging")
    parser.add_argument(
        "--runtime-dir",
        type=Path,
        required=True,
        help="Directory where ca.crt, server.crt, and server.key are written",
    )
    parser.add_argument(
        "--ca-private-dir",
        type=Path,
        required=True,
        help="Protected directory where ca.key is stored",
    )
    parser.add_argument(
        "--san",
        type=str,
        default="platform-db,localhost,127.0.0.1",
        help="Comma-separated SAN names/IPs (default: platform-db,localhost,127.0.0.1)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=365,
        help="Validity period in days (default: 365)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing files if present",
    )
    args = parser.parse_args(argv)

    try:
        sans = [item.strip() for item in args.san.split(",") if item.strip()]
        result = generate_postgres_tls(
            runtime_dir=args.runtime_dir,
            ca_private_dir=args.ca_private_dir,
            sans=sans,
            days=args.days,
            overwrite=args.overwrite,
        )
        print("POSTGRES TLS CERTIFICATE GENERATION SUCCESSFUL")
        print(f"  CA Certificate:     {result['ca_crt']}")
        print(f"  Server Certificate: {result['server_crt']}")
        print(f"  Server Key:         {result['server_key']}")
        print(f"  CA Private Key:     {result['ca_key']} (ISOLATED)")
        return 0
    except Exception as exc:
        print(f"POSTGRES TLS GENERATION FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
