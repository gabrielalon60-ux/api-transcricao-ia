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
        "DF_HOLDING_IDENTIFIERS": "12345678901,12345678000199",
        "GEMINI_MODEL": "gemini-test",
        **{name: secret for name in (
            "API_KEY_HASH_SECRET", "WUZAPI_WEBHOOK_SECRET", "REGISTRATION_SECRET_PEPPER",
            "LOG_PII_HASH_KEY", "ORCHESTRATOR_TO_BOT_TOKEN", "BOT_TO_TRANSCRIPTION_TOKEN",
            "DB_WRITER_INTERNAL_TOKEN", "GEMINI_API_KEY", "WUZAPI_ADMIN_TOKEN", "WUZAPI_TOKEN",
        )},
    }


def test_release_compose_is_private_and_hardened() -> None:
    text = (ROOT / "deploy" / "compose.release.yml").read_text(encoding="utf-8")
    assert "ports:" not in text
    assert "read_only: true" in text
    assert "no-new-privileges:true" in text
    assert "cap_drop: [ALL]" in text
    assert "internal: true" in text
    assert "${RELEASE_IMAGE:?" in text
    assert "${WUZAPI_IMAGE:?" in text
    assert "ssl=on" in text
    assert "server.crt" in text
    assert "server.key" in text
    assert "/run/secrets/postgres_ca.crt" in text
    assert "ca.key" not in text


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
