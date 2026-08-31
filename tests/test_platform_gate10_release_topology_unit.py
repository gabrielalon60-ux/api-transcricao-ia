from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_preflight():
    path = ROOT / "scripts" / "operations" / "gate10_preflight.py"
    spec = importlib.util.spec_from_file_location("gate10_preflight_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _release_env() -> dict[str, str]:
    digest = "a" * 64
    secret = "s" * 40
    return {
        "APP_ENV": "staging",
        "RELEASE_IMAGE": f"registry.invalid/app@sha256:{digest}",
        "POSTGRES_IMAGE": f"postgres@sha256:{digest}",
        "WUZAPI_IMAGE": f"registry.invalid/wuzapi@sha256:{digest}",
        "RELEASE_HOST": "staging.example.test",
        "EDGE_NETWORK": "edge-test",
        "POSTGRES_TLS_DIR": "/tmp/fake-tls",
        "DATABASE_URL": "postgresql://app:secret@platform-db/platform?sslmode=verify-full&sslrootcert=/run/secrets/postgres_ca.crt",
        "DB_USER": "app",
        "DB_PASSWORD": secret,
        "DB_NAME": "platform",
        "DF_DATABASE_URL": "postgresql://writer:secret@df-db/df?sslmode=verify-full&sslrootcert=/run/secrets/postgres_ca.crt",
        "DF_HOLDING_IDENTIFIERS": '["12345678901","12345678000199"]',
        "GEMINI_MODEL": "gemini-test",
        "POSTGRES_CA_CERT_B64": "dGVzdF9jYV9zZWNyZXRfbG9uZ19lbm91Z2hfMTIzNDU2Nzg=",
        "POSTGRES_SERVER_CERT_B64": "dGVzdF9zcnZfY3J0X3NlY3JldF9sb25nX2Vub3VnaF8xMjM0NTY3OA==",
        "POSTGRES_SERVER_KEY_B64": "dGVzdF9zcnZfa2V5X3NlY3JldF9sb25nX2Vub3VnaF8xMjM0NTY3OA==",
        **{name: secret for name in (
            "API_KEY_HASH_SECRET", "WUZAPI_WEBHOOK_SECRET", "REGISTRATION_SECRET_PEPPER",
            "LOG_PII_HASH_KEY", "ORCHESTRATOR_TO_BOT_TOKEN", "BOT_TO_TRANSCRIPTION_TOKEN",
            "DB_WRITER_INTERNAL_TOKEN", "GEMINI_API_KEY", "WUZAPI_ADMIN_TOKEN", "WUZAPI_TOKEN",
            "WUZAPI_GLOBAL_ENCRYPTION_KEY",
        )},
    }


def test_release_compose_is_private_and_hardened() -> None:
    text = (ROOT / "deploy" / "compose.release.yml").read_text(encoding="utf-8")
    assert "ports:" not in text
    assert "read_only: true" in text
    assert "tmpfs: [/tmp:size=64m]" in text
    assert "no-new-privileges:true" in text
    assert "cap_drop: [ALL]" in text
    assert "internal: true" in text
    assert "${RELEASE_IMAGE:?" in text
    assert "${WUZAPI_IMAGE:?" in text
    assert "ssl=on" in text
    assert "server.crt" in text
    assert "server.key" in text
    assert "/run/secrets/postgres_ca.crt" in text
    assert "wuzapi-data:/app/dbdata" in text
    assert "wuzapi-data:" in text
    assert "ca.key" not in text
    assert "platform-migrator:" in text
    assert "transcription-migrator:" in text
    assert "packages/db/alembic.ini" in text
    assert "apps/transcription/alembic.ini" in text
    assert "apps/db_writer/alembic.ini" not in text
    assert "service_completed_successfully" in text
    assert "WUZAPI_GLOBAL_ENCRYPTION_KEY" in text
    assert "WUZAPI_GLOBAL_HMAC_KEY: ${WUZAPI_WEBHOOK_SECRET:?WUZAPI_WEBHOOK_SECRET is required}" in text
    assert "${WUZAPI_GLOBAL_HMAC_KEY" not in text


def test_dockerfile_uses_immutable_multistage_nonroot_runtime() -> None:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert text.count("@sha256:") >= 3
    assert " AS builder" in text
    assert " AS runtime" in text
    assert "USER 10001:10001" in text
    assert "pip install" not in text


def test_release_workflow_is_manual_serialized_and_environment_gated() -> None:
    text = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "push:" not in text
    assert "cancel-in-progress: false" in text
    assert "environment: production" in text
    assert "@v" not in text


def test_preflight_accepts_only_complete_protected_contract(tmp_path: Path) -> None:
    module = _load_preflight()
    env_file = tmp_path / "release.env"
    values = _release_env()
    env_file.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
    assert module.validate(values, env_file, ROOT / "deploy" / "compose.release.yml") == []
    values["RELEASE_IMAGE"] = "registry.invalid/app:latest"
    assert any("RELEASE_IMAGE" in error for error in module.validate(values, env_file, ROOT / "deploy" / "compose.release.yml"))


def test_preflight_df_holding_identifiers_format_contract(tmp_path: Path) -> None:
    module = _load_preflight()
    env_file = tmp_path / "release.env"
    compose_path = ROOT / "deploy" / "compose.release.yml"

    # 1. JSON single-item array -> PASS
    values = _release_env()
    values["DF_HOLDING_IDENTIFIERS"] = '["12345678000100"]'
    env_file.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    assert module.validate(values, env_file, compose_path) == []

    # 2. JSON multi-item array -> PASS
    values = _release_env()
    values["DF_HOLDING_IDENTIFIERS"] = '["12345678000100","98765432000100"]'
    env_file.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    assert module.validate(values, env_file, compose_path) == []

    # 3. Plain scalar -> FAIL
    values = _release_env()
    values["DF_HOLDING_IDENTIFIERS"] = "12345678000100"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    assert any("DF_HOLDING_IDENTIFIERS" in err for err in module.validate(values, env_file, compose_path))

    # 4. Comma-separated scalar -> FAIL
    values = _release_env()
    values["DF_HOLDING_IDENTIFIERS"] = "12345678000100,98765432000100"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    assert any("DF_HOLDING_IDENTIFIERS" in err for err in module.validate(values, env_file, compose_path))

    # 5. Empty JSON array -> FAIL
    values = _release_env()
    values["DF_HOLDING_IDENTIFIERS"] = "[]"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    assert any("DF_HOLDING_IDENTIFIERS" in err for err in module.validate(values, env_file, compose_path))

    # 6. JSON object -> FAIL
    values = _release_env()
    values["DF_HOLDING_IDENTIFIERS"] = '{"id": "12345678000100"}'
    env_file.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    assert any("DF_HOLDING_IDENTIFIERS" in err for err in module.validate(values, env_file, compose_path))

    # 7. JSON number -> FAIL
    values = _release_env()
    values["DF_HOLDING_IDENTIFIERS"] = "12345678000100"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    assert any("DF_HOLDING_IDENTIFIERS" in err for err in module.validate(values, env_file, compose_path))

    # 8. Array with non-string element -> FAIL
    values = _release_env()
    values["DF_HOLDING_IDENTIFIERS"] = "[12345678000100]"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    assert any("DF_HOLDING_IDENTIFIERS" in err for err in module.validate(values, env_file, compose_path))

    # 9. Malformed JSON -> FAIL
    values = _release_env()
    values["DF_HOLDING_IDENTIFIERS"] = '["12345678000100"'
    env_file.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    assert any("DF_HOLDING_IDENTIFIERS" in err for err in module.validate(values, env_file, compose_path))


def test_postgres_tls_generator_creates_valid_isolated_certificates(tmp_path: Path) -> None:
    gen_path = ROOT / "scripts" / "operations" / "generate_postgres_tls.py"
    spec = importlib.util.spec_from_file_location("generate_postgres_tls", gen_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    runtime_dir = tmp_path / "runtime_tls"
    ca_private_dir = tmp_path / "ca_private"
    result = mod.generate_postgres_tls(runtime_dir, ca_private_dir)

    assert result["ca_crt"].is_file()
    assert result["server_crt"].is_file()
    assert result["server_key"].is_file()
    assert result["ca_key"].is_file()
    assert not (runtime_dir / "ca.key").exists()


def test_dokploy_compose_is_private_hardened_and_uses_named_volumes() -> None:
    text = (ROOT / "deploy" / "compose.dokploy.yml").read_text(encoding="utf-8")
    assert "ports:" not in text
    assert "read_only: true" in text
    assert "tmpfs: [/tmp:size=64m]" in text
    assert "no-new-privileges:true" in text
    assert "cap_drop: [ALL]" in text
    assert "internal: true" in text
    assert "ssl=on" in text
    assert "postgres-server-tls-data" in text
    assert "postgres-ca-data" in text
    assert "wuzapi-data:/app/dbdata" in text
    assert "wuzapi-data:" in text
    assert "ca.key" not in text
    assert "tls-provisioner:" in text
    assert "platform-db:" in text
    assert "platform-migrator:" in text
    assert "transcription-migrator:" in text
    assert "packages/db/alembic.ini" in text
    assert "apps/transcription/alembic.ini" in text
    assert "apps/db_writer/alembic.ini" not in text
    assert "service_completed_successfully" in text
    assert "WUZAPI_GLOBAL_ENCRYPTION_KEY" in text
    assert "WUZAPI_GLOBAL_HMAC_KEY: ${WUZAPI_WEBHOOK_SECRET:?WUZAPI_WEBHOOK_SECRET is required}" in text
    assert "${WUZAPI_GLOBAL_HMAC_KEY" not in text
    assert 'user: "0:0"' in text
    assert "network_mode: \"none\"" in text
    assert "security.tls_provisioner" in text
    assert "--server-key-uid" in text
    assert "--server-key-gid" in text
    assert "--overwrite" in text


def test_wuzapi_hmac_single_source_and_env_contract_count() -> None:
    module = _load_preflight()
    assert len(module.DOKPLOY_REQUIRED) == 25
    assert len(module.STANDALONE_REQUIRED) == 25
    assert "WUZAPI_GLOBAL_ENCRYPTION_KEY" in module.DOKPLOY_REQUIRED
    assert "WUZAPI_WEBHOOK_SECRET" in module.DOKPLOY_REQUIRED
    assert "WUZAPI_GLOBAL_HMAC_KEY" not in module.DOKPLOY_REQUIRED
    assert "WUZAPI_GLOBAL_HMAC_KEY" not in module.STANDALONE_REQUIRED

    rel_text = (ROOT / "deploy" / "compose.release.yml").read_text(encoding="utf-8")
    dok_text = (ROOT / "deploy" / "compose.dokploy.yml").read_text(encoding="utf-8")
    for text in (rel_text, dok_text):
        assert "WUZAPI_GLOBAL_HMAC_KEY: ${WUZAPI_WEBHOOK_SECRET:?WUZAPI_WEBHOOK_SECRET is required}" in text
        assert "${WUZAPI_GLOBAL_HMAC_KEY" not in text
        assert "WUZAPI_GLOBAL_ENCRYPTION_KEY: ${WUZAPI_GLOBAL_ENCRYPTION_KEY:?WUZAPI_GLOBAL_ENCRYPTION_KEY is required}" in text
        assert "WUZAPI_TOKEN: ${WUZAPI_TOKEN:?WUZAPI_TOKEN is required}" in text


def test_dokploy_tls_provisioner_unpacks_and_validates(tmp_path: Path) -> None:
    gen_path = ROOT / "scripts" / "operations" / "generate_postgres_tls.py"
    spec = importlib.util.spec_from_file_location("generate_postgres_tls", gen_path)
    assert spec is not None and spec.loader is not None
    gen_mod = importlib.util.module_from_spec(spec)
    gen_mod_loader = spec.loader
    gen_mod_loader.exec_module(gen_mod)

    prov_path = ROOT / "scripts" / "operations" / "provision_dokploy_tls.py"
    spec_p = importlib.util.spec_from_file_location("provision_dokploy_tls", prov_path)
    assert spec_p is not None and spec_p.loader is not None
    prov_mod = importlib.util.module_from_spec(spec_p)
    spec_p.loader.exec_module(prov_mod)

    # Generate source certs
    src_runtime = tmp_path / "src_runtime"
    src_ca_priv = tmp_path / "src_ca_priv"
    certs = gen_mod.generate_postgres_tls(src_runtime, src_ca_priv, sans=["platform-db"])

    ca_bytes = certs["ca_crt"].read_bytes()
    srv_crt_bytes = certs["server_crt"].read_bytes()
    srv_key_bytes = certs["server_key"].read_bytes()

    # Target volumes
    target_server = tmp_path / "dst_server_tls"
    target_ca = tmp_path / "dst_ca_tls"

    # Test 1: Successful provisioning
    result = prov_mod.validate_and_provision_tls(
        target_dir=target_server,
        ca_crt_bytes=ca_bytes,
        server_crt_bytes=srv_crt_bytes,
        server_key_bytes=srv_key_bytes,
        ca_target_dir=target_ca,
    )
    assert result["server_crt"].is_file()
    assert result["server_key"].is_file()
    assert result["ca_crt"].is_file()
    assert not (target_server / "ca.key").exists()
    assert not (target_ca / "ca.key").exists()

    # Test 2: Fails if already exists without overwrite
    import pytest
    with pytest.raises(FileExistsError):
        prov_mod.validate_and_provision_tls(
            target_dir=target_server,
            ca_crt_bytes=ca_bytes,
            server_crt_bytes=srv_crt_bytes,
            server_key_bytes=srv_key_bytes,
            ca_target_dir=target_ca,
            overwrite=False,
        )

    # Test 3: Fails on invalid base64
    with pytest.raises(ValueError, match="Invalid base64"):
        prov_mod.decode_b64_secret("TEST_VAR", "not-valid-base64!!!")

    # Test 4: Fails on SAN mismatch
    certs_bad_san = gen_mod.generate_postgres_tls(
        tmp_path / "bad_runtime", tmp_path / "bad_ca", sans=["other-host"], overwrite=True
    )
    with pytest.raises(ValueError, match="SAN missing required 'DNS:platform-db'"):
        prov_mod.validate_and_provision_tls(
            target_dir=tmp_path / "bad_target",
            ca_crt_bytes=ca_bytes,
            server_crt_bytes=certs_bad_san["server_crt"].read_bytes(),
            server_key_bytes=certs_bad_san["server_key"].read_bytes(),
        )


def test_preflight_dokploy_target_mode(tmp_path: Path) -> None:
    import base64
    module = _load_preflight()
    gen_path = ROOT / "scripts" / "operations" / "generate_postgres_tls.py"
    spec = importlib.util.spec_from_file_location("generate_postgres_tls", gen_path)
    assert spec is not None and spec.loader is not None
    gen_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen_mod)

    certs = gen_mod.generate_postgres_tls(tmp_path / "pre_runtime", tmp_path / "pre_ca", sans=["platform-db"])
    ca_b64 = base64.b64encode(certs["ca_crt"].read_bytes()).decode("ascii")
    srv_b64 = base64.b64encode(certs["server_crt"].read_bytes()).decode("ascii")
    key_b64 = base64.b64encode(certs["server_key"].read_bytes()).decode("ascii")

    values = _release_env()
    # Remove standalone-specific keys
    del values["POSTGRES_TLS_DIR"]
    del values["RELEASE_HOST"]
    del values["EDGE_NETWORK"]
    # Add dokploy-specific base64 secrets
    values["POSTGRES_CA_CERT_B64"] = ca_b64
    values["POSTGRES_SERVER_CERT_B64"] = srv_b64
    values["POSTGRES_SERVER_KEY_B64"] = key_b64

    env_file = tmp_path / "dokploy.env"
    env_file.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")

    # Target dokploy mode passes with compose.dokploy.yml
    errors = module.validate(
        values, env_file, ROOT / "deploy" / "compose.dokploy.yml", target="dokploy"
    )
    assert errors == [], f"Dokploy preflight errors: {errors}"

    # Standalone mode on this env fails because POSTGRES_TLS_DIR is missing
    errors_std = module.validate(
        values, env_file, ROOT / "deploy" / "compose.release.yml", target="standalone"
    )
    assert any("POSTGRES_TLS_DIR" in err for err in errors_std)


def test_release_compose_renders_with_synthetic_values() -> None:
    environment = os.environ.copy()
    environment.update(_release_env())
    result = subprocess.run(
        ["docker", "compose", "-f", str(ROOT / "deploy" / "compose.release.yml"), "config", "--quiet"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_dokploy_compose_renders_with_synthetic_values() -> None:
    environment = os.environ.copy()
    values = _release_env()
    values["POSTGRES_CA_CERT_B64"] = "YWFh" * 10
    values["POSTGRES_SERVER_CERT_B64"] = "YWFh" * 10
    values["POSTGRES_SERVER_KEY_B64"] = "YWFh" * 10
    environment.update(values)
    result = subprocess.run(
        ["docker", "compose", "-f", str(ROOT / "deploy" / "compose.dokploy.yml"), "config", "--quiet"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_dokploy_compose_matches_fresh_renderer_output() -> None:
    render_path = ROOT / "scripts" / "operations" / "render_dokploy_compose.py"
    spec = importlib.util.spec_from_file_location("render_dokploy_compose", render_path)
    assert spec is not None and spec.loader is not None
    render_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(render_mod)

    rendered = render_mod.render_dokploy_compose(ROOT / "deploy" / "compose.release.yml")
    tracked = (ROOT / "deploy" / "compose.dokploy.yml").read_text(encoding="utf-8")
    assert tracked == rendered, "Tracked deploy/compose.dokploy.yml has drifted from fresh renderer output!"


def test_canonical_and_dokploy_resource_boundaries() -> None:
    release_text = (ROOT / "deploy" / "compose.release.yml").read_text(encoding="utf-8")
    dokploy_text = (ROOT / "deploy" / "compose.dokploy.yml").read_text(encoding="utf-8")

    for text in (release_text, dokploy_text):
        assert "mem_limit: 512m" in text  # platform-db
        assert "mem_limit: 256m" in text  # wuzapi
        assert "mem_limit: 768m" in text  # x-app
