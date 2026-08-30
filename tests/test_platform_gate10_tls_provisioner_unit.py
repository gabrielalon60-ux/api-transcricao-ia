#!/usr/bin/env python3
"""Dedicated unit tests for PostgreSQL TLS Provisioner (Gate 10)."""

from __future__ import annotations

import base64
import datetime
import os
from unittest.mock import patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from security.tls_provisioner import (
    decode_b64_secret,
    main,
    validate_and_provision_tls,
)


@pytest.fixture
def tls_materials():
    """Generates valid test CA, server cert (with SAN DNS:platform-db), and server key."""
    # 1. Generate CA
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Root CA")])
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=10))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )

    # 2. Generate Server Cert + Key
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "platform-db")])
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=10))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("platform-db")]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    ca_pem = ca_cert.public_bytes(serialization.Encoding.PEM)
    server_crt_pem = server_cert.public_bytes(serialization.Encoding.PEM)
    server_key_pem = server_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )

    return {
        "ca_pem": ca_pem,
        "server_crt_pem": server_crt_pem,
        "server_key_pem": server_key_pem,
        "ca_b64": base64.b64encode(ca_pem).decode("ascii"),
        "server_crt_b64": base64.b64encode(server_crt_pem).decode("ascii"),
        "server_key_b64": base64.b64encode(server_key_pem).decode("ascii"),
    }


def test_decode_b64_secret_valid():
    raw = b"hello test secret"
    encoded = base64.b64encode(raw).decode("ascii")
    assert decode_b64_secret("TEST_VAR", encoded) == raw


def test_decode_b64_secret_invalid():
    with pytest.raises(ValueError, match="Invalid base64 encoding"):
        decode_b64_secret("TEST_VAR", "not_valid_base64!@#$")


def test_decode_b64_secret_missing_or_empty():
    with pytest.raises(ValueError, match="Required TLS environment secret is empty or missing"):
        decode_b64_secret("TEST_VAR", "")
    with pytest.raises(ValueError, match="Required TLS environment secret is empty or missing"):
        decode_b64_secret("TEST_VAR", None)


def test_provision_tls_success_first_run(tmp_path, tls_materials):
    target_dir = tmp_path / "certs"
    ca_dir = tmp_path / "ca"

    with patch("os.chown", create=True) as mock_chown:
        results = validate_and_provision_tls(
            target_dir=target_dir,
            ca_crt_bytes=tls_materials["ca_pem"],
            server_crt_bytes=tls_materials["server_crt_pem"],
            server_key_bytes=tls_materials["server_key_pem"],
            ca_target_dir=ca_dir,
            server_key_uid=70,
            server_key_gid=70,
            overwrite=False,
        )

    assert results["ca_crt"].is_file()
    assert results["server_crt"].is_file()
    assert results["server_key"].is_file()

    assert results["ca_crt"].read_bytes() == tls_materials["ca_pem"]
    assert results["server_crt"].read_bytes() == tls_materials["server_crt_pem"]
    assert results["server_key"].read_bytes() == tls_materials["server_key_pem"]

    if os.name != "nt":
        mock_chown.assert_any_call(results["server_key"], 70, 70)


def test_provision_tls_collision_without_overwrite(tmp_path, tls_materials):
    target_dir = tmp_path / "certs"
    validate_and_provision_tls(
        target_dir=target_dir,
        ca_crt_bytes=tls_materials["ca_pem"],
        server_crt_bytes=tls_materials["server_crt_pem"],
        server_key_bytes=tls_materials["server_key_pem"],
        overwrite=False,
    )

    with pytest.raises(FileExistsError, match="Target TLS files already exist"):
        validate_and_provision_tls(
            target_dir=target_dir,
            ca_crt_bytes=tls_materials["ca_pem"],
            server_crt_bytes=tls_materials["server_crt_pem"],
            server_key_bytes=tls_materials["server_key_pem"],
            overwrite=False,
        )


def test_provision_tls_success_with_overwrite(tmp_path, tls_materials):
    target_dir = tmp_path / "certs"
    validate_and_provision_tls(
        target_dir=target_dir,
        ca_crt_bytes=tls_materials["ca_pem"],
        server_crt_bytes=tls_materials["server_crt_pem"],
        server_key_bytes=tls_materials["server_key_pem"],
        overwrite=False,
    )

    # Overwrite should succeed
    results = validate_and_provision_tls(
        target_dir=target_dir,
        ca_crt_bytes=tls_materials["ca_pem"],
        server_crt_bytes=tls_materials["server_crt_pem"],
        server_key_bytes=tls_materials["server_key_pem"],
        overwrite=True,
    )
    assert results["server_key"].is_file()


def test_fail_closed_invalid_overwrite_preserves_valid_files(tmp_path, tls_materials):
    target_dir = tmp_path / "certs"
    ca_dir = tmp_path / "ca"
    validate_and_provision_tls(
        target_dir=target_dir,
        ca_crt_bytes=tls_materials["ca_pem"],
        server_crt_bytes=tls_materials["server_crt_pem"],
        server_key_bytes=tls_materials["server_key_pem"],
        ca_target_dir=ca_dir,
        overwrite=False,
    )

    # Store original contents
    orig_srv_crt = (target_dir / "server.crt").read_bytes()
    orig_srv_key = (target_dir / "server.key").read_bytes()
    orig_ca_crt = (ca_dir / "ca.crt").read_bytes()

    # Attempt overwrite with invalid server cert (corrupted bytes)
    with pytest.raises(ValueError, match="Malformed server certificate"):
        validate_and_provision_tls(
            target_dir=target_dir,
            ca_crt_bytes=tls_materials["ca_pem"],
            server_crt_bytes=b"INVALID_CORRUPTED_CERT",
            server_key_bytes=tls_materials["server_key_pem"],
            ca_target_dir=ca_dir,
            overwrite=True,
        )

    # Verify existing valid files remained unmodified
    assert (target_dir / "server.crt").read_bytes() == orig_srv_crt
    assert (target_dir / "server.key").read_bytes() == orig_srv_key
    assert (ca_dir / "ca.crt").read_bytes() == orig_ca_crt


def test_key_cert_mismatch_failure(tmp_path, tls_materials):
    target_dir = tmp_path / "certs"
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_key_pem = other_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )

    with pytest.raises(ValueError, match="does NOT match server certificate public key"):
        validate_and_provision_tls(
            target_dir=target_dir,
            ca_crt_bytes=tls_materials["ca_pem"],
            server_crt_bytes=tls_materials["server_crt_pem"],
            server_key_bytes=other_key_pem,
            overwrite=False,
        )


def test_san_missing_platform_db_failure(tmp_path, tls_materials):
    target_dir = tmp_path / "certs"
    # Create server cert with wrong SAN
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Root CA")])
    now = datetime.datetime.now(datetime.timezone.utc)
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "wrong-host")])
    bad_san_cert = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=10))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("wrong-host.local")]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    bad_cert_pem = bad_san_cert.public_bytes(serialization.Encoding.PEM)
    srv_key_pem = server_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )

    with pytest.raises(ValueError, match="Server certificate SAN missing required 'DNS:platform-db'"):
        validate_and_provision_tls(
            target_dir=target_dir,
            ca_crt_bytes=tls_materials["ca_pem"],
            server_crt_bytes=bad_cert_pem,
            server_key_bytes=srv_key_pem,
            overwrite=False,
        )


def test_ca_key_forbidden_detection(tmp_path, tls_materials):
    target_dir = tmp_path / "certs"
    target_dir.mkdir(parents=True, exist_ok=True)
    forbidden_ca_key = target_dir / "ca.key"
    forbidden_ca_key.write_text("dummy ca key")

    with pytest.raises(ValueError, match="SECURITY VIOLATION: ca.key detected"):
        validate_and_provision_tls(
            target_dir=target_dir,
            ca_crt_bytes=tls_materials["ca_pem"],
            server_crt_bytes=tls_materials["server_crt_pem"],
            server_key_bytes=tls_materials["server_key_pem"],
            overwrite=True,
        )


def test_cli_main_entrypoint(tmp_path, tls_materials, monkeypatch):
    target_dir = tmp_path / "certs"
    ca_dir = tmp_path / "ca"
    monkeypatch.setenv("POSTGRES_CA_CERT_B64", tls_materials["ca_b64"])
    monkeypatch.setenv("POSTGRES_SERVER_CERT_B64", tls_materials["server_crt_b64"])
    monkeypatch.setenv("POSTGRES_SERVER_KEY_B64", tls_materials["server_key_b64"])

    rc = main([
        "--target-dir", str(target_dir),
        "--ca-target-dir", str(ca_dir),
        "--server-key-uid", "70",
        "--server-key-gid", "70",
        "--overwrite",
    ])
    assert rc == 0
    assert (target_dir / "server.key").is_file()
    assert (ca_dir / "ca.crt").is_file()
