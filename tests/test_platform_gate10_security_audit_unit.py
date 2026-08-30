from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_audit():
    path = ROOT / "scripts" / "operations" / "gate10_security_audit.py"
    spec = importlib.util.spec_from_file_location("gate10_security_audit_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _evidence(path: Path, vulnerabilities: list[dict] | None = None, leaks: list[dict] | None = None) -> None:
    module = _load_audit()
    path.mkdir()
    (path / "metadata.json").write_text(json.dumps({"images": [module.GITLEAKS_IMAGE, module.TRIVY_IMAGE]}), encoding="utf-8")
    (path / "gitleaks-history.json").write_text(json.dumps(leaks or []), encoding="utf-8")
    (path / "gitleaks-worktree.json").write_text("[]", encoding="utf-8")
    (path / "trivy.json").write_text(json.dumps({"Results": [{"Vulnerabilities": vulnerabilities or []}]}), encoding="utf-8")


def test_clean_immutable_evidence_passes(tmp_path: Path) -> None:
    module = _load_audit()
    output = tmp_path / "clean"
    _evidence(output)
    assert module.evaluate(output) == []


def test_secret_finding_always_blocks(tmp_path: Path) -> None:
    module = _load_audit()
    output = tmp_path / "secret"
    _evidence(output, leaks=[{"RuleID": "generic-api-key", "Secret": "REDACTED"}])
    assert any("secret" in error for error in module.evaluate(output))


def test_only_exact_reviewed_fixture_fingerprint_is_disposed(tmp_path: Path) -> None:
    module = _load_audit()
    fingerprint, disposition = next(iter(module.FALSE_POSITIVE_FIXTURES.items()))
    output = tmp_path / "fixture"
    _evidence(output, leaks=[{"Fingerprint": fingerprint, "RuleID": disposition["rule"], "File": disposition["path"]}])
    assert module.evaluate(output) == []


def test_critical_high_and_medium_each_fail_closed(tmp_path: Path) -> None:
    module = _load_audit()
    output = tmp_path / "vulnerable"
    _evidence(output, vulnerabilities=[{"Severity": value} for value in ("CRITICAL", "HIGH", "MEDIUM")])
    errors = module.evaluate(output)
    assert any("CRITICAL" in error for error in errors)
    assert any("HIGH" in error for error in errors)
    assert any("MEDIUM" in error for error in errors)


def test_malformed_or_mutable_scanner_identity_blocks(tmp_path: Path) -> None:
    module = _load_audit()
    output = tmp_path / "mutable"
    _evidence(output)
    (output / "metadata.json").write_text(json.dumps({"images": ["scanner:latest"]}), encoding="utf-8")
    assert any("immutable" in error for error in module.evaluate(output))


def test_worktree_stage_uses_git_scope_and_excludes_ignored_files(tmp_path: Path) -> None:
    module = _load_audit()
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    (repository / ".gitignore").write_text(".env\n.venv/\n", encoding="utf-8")
    (repository / "tracked.txt").write_text("tracked", encoding="utf-8")
    (repository / "untracked.txt").write_text("untracked", encoding="utf-8")
    (repository / ".env").write_text("ignored-local-value", encoding="utf-8")
    (repository / ".venv").mkdir()
    (repository / ".venv" / "dependency.py").write_text("ignored", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore", "tracked.txt"], cwd=repository, check=True)
    stage = tmp_path / "stage"
    assert module.stage_release_worktree(repository, stage) == 3
    assert (stage / "tracked.txt").is_file()
    assert (stage / "untracked.txt").is_file()
    assert not (stage / ".env").exists()
    assert not (stage / ".venv").exists()


def test_compose_tls_security_validation(tmp_path: Path) -> None:
    module = _load_audit()
    valid_compose = ROOT / "deploy" / "compose.release.yml"
    assert module.validate_compose_tls_security(valid_compose) == []

    bad_compose = tmp_path / "bad_compose.yml"
    bad_compose.write_text(
        "services:\n  platform-db:\n    image: postgres\n  orchestrator:\n    volumes: [server.key:/key]\n",
        encoding="utf-8",
    )
    errors = module.validate_compose_tls_security(bad_compose)
    assert any("ssl=on" in err for err in errors)
    assert any("server.key" in err for err in errors)


def test_dokploy_security_audit_not_observable_classification(tmp_path: Path) -> None:
    module = _load_audit()
    dokploy_compose = ROOT / "deploy" / "compose.dokploy.yml"
    assert dokploy_compose.is_file()

    results_dok = module.audit_target_security(dokploy_compose, target="dokploy")
    assert results_dok["compose_tls_security"] == module.AuditStatus.PASS
    assert results_dok["no_published_host_ports"] == module.AuditStatus.PASS
    assert results_dok["no_global_container_names"] == module.AuditStatus.PASS
    assert results_dok["host_firewall_rules"] == module.AuditStatus.NOT_OBSERVABLE_WITH_PROJECT_ACCESS
    assert results_dok["docker_daemon_security_policy"] == module.AuditStatus.NOT_OBSERVABLE_WITH_PROJECT_ACCESS

    results_std = module.audit_target_security(ROOT / "deploy" / "compose.release.yml", target="standalone")
    assert results_std["compose_tls_security"] == module.AuditStatus.PASS
    assert results_std["host_firewall_rules"] == module.AuditStatus.PASS
