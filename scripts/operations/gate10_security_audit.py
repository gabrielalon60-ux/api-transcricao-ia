#!/usr/bin/env python3
"""Run pinned Gate 10 secret and dependency scanners and enforce severity policy."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

TRIVY_IMAGE = "aquasec/trivy:0.67.2@sha256:e2b22eac59c02003d8749f5b8d9bd073b62e30fefaef5b7c8371204e0a4b0c08"
GITLEAKS_IMAGE = "zricethezav/gitleaks:v8.28.0@sha256:cdbb7c955abce02001a9f6c9f602fb195b7fadc1e812065883f695d1eeaba854"
IMAGE_RE = re.compile(r"^[^\s]+@sha256:[0-9a-f]{64}$")
FALSE_POSITIVE_FIXTURES = {
    "/evidence/release-worktree/tests/test_platform_gate4f_db_writer_unit.py:generic-api-key:114": {
        "rule": "generic-api-key",
        "path": "/evidence/release-worktree/tests/test_platform_gate4f_db_writer_unit.py",
        "reason": "synthetic idempotency-key HTTP contract fixture",
        "reviewer": "Codex G10-A exact-source review 2026-08-17",
    },
    "/evidence/release-worktree/tests/test_platform_gate4_phase4e_disposable_postgres.py:generic-api-key:176": {
        "rule": "generic-api-key",
        "path": "/evidence/release-worktree/tests/test_platform_gate4_phase4e_disposable_postgres.py",
        "reason": "synthetic disposable idempotency-key boundary fixture",
        "reviewer": "Codex G10-A exact-source review 2026-08-17",
    },
    "/evidence/release-worktree/tests/test_platform_gate4f_orchestrator_persistence_postgres.py:generic-api-key:102": {
        "rule": "generic-api-key",
        "path": "/evidence/release-worktree/tests/test_platform_gate4f_orchestrator_persistence_postgres.py",
        "reason": "synthetic persistence idempotency-key fixture",
        "reviewer": "Codex G10-A exact-source review 2026-08-17",
    },
    "b1dc35b22ec2ef374afd7285ab60e1189b639575:tests/test_platform_gate4f_db_writer_unit.py:generic-api-key:114": {
        "rule": "generic-api-key",
        "path": "tests/test_platform_gate4f_db_writer_unit.py",
        "reason": "synthetic idempotency-key HTTP contract fixture",
        "reviewer": "Codex G10-A exact-source review 2026-08-16",
    },
    "b1dc35b22ec2ef374afd7285ab60e1189b639575:tests/test_platform_gate4_phase4e_disposable_postgres.py:generic-api-key:168": {
        "rule": "generic-api-key",
        "path": "tests/test_platform_gate4_phase4e_disposable_postgres.py",
        "reason": "synthetic disposable idempotency-key boundary fixture",
        "reviewer": "Codex G10-A exact-source review 2026-08-16",
    },
    "b1dc35b22ec2ef374afd7285ab60e1189b639575:tests/test_platform_gate4f_orchestrator_persistence_postgres.py:generic-api-key:102": {
        "rule": "generic-api-key",
        "path": "tests/test_platform_gate4f_orchestrator_persistence_postgres.py",
        "reason": "synthetic persistence idempotency-key fixture",
        "reviewer": "Codex G10-A exact-source review 2026-08-16",
    },
    "9465286cbf46e290dfd2f4175dc438484965b76e:README.md:curl-auth-header:976": {
        "rule": "curl-auth-header",
        "path": "README.md",
        "reason": "Portuguese API-key placeholder in localhost curl documentation",
        "reviewer": "Codex G10-A exact-source review 2026-08-16",
    },
    "9465286cbf46e290dfd2f4175dc438484965b76e:README.md:curl-auth-header:984": {
        "rule": "curl-auth-header",
        "path": "README.md",
        "reason": "Portuguese API-key placeholder in localhost curl documentation",
        "reviewer": "Codex G10-A exact-source review 2026-08-16",
    },
}


def stage_release_worktree(repository: Path, stage: Path) -> int:
    """Copy exactly tracked plus non-ignored untracked files into an owned scan root."""
    repository = repository.resolve(strict=True)
    if stage.exists():
        raise RuntimeError("worktree scan stage already exists")
    completed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=repository,
        check=False,
        capture_output=True,
        timeout=30,
    )
    if completed.returncode != 0 or completed.stderr:
        raise RuntimeError("unable to resolve release worktree scope")
    try:
        names = [name for name in completed.stdout.decode("utf-8", errors="strict").split("\0") if name]
    except UnicodeDecodeError as exc:
        raise RuntimeError("release worktree contains a non-UTF-8 path") from exc
    stage.mkdir(parents=False)
    copied = 0
    for name in names:
        relative = Path(name)
        source = repository / relative
        if relative.is_absolute() or ".." in relative.parts or source.is_symlink():
            raise RuntimeError("unsafe release worktree path")
        resolved = source.resolve(strict=True)
        if not resolved.is_relative_to(repository) or not resolved.is_file():
            raise RuntimeError("release worktree path escapes repository or is not a file")
        destination = stage / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(resolved, destination)
        copied += 1
    return copied


def run_scanners(repository: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    repository = repository.resolve(strict=True)
    if output.resolve().is_relative_to(repository):
        raise RuntimeError("security evidence directory must be outside the repository")
    stage = output / "release-worktree"
    staged_count = stage_release_worktree(repository, stage)
    repo_mount = f"{repository.resolve()}:/repo:ro"
    evidence_mount = f"{output.resolve()}:/evidence"
    commands = [
        ["docker", "run", "--rm", "-v", repo_mount, "-v", evidence_mount, GITLEAKS_IMAGE,
         "git", "/repo", "--no-banner", "--redact=100", "--report-format=json",
         "--report-path=/evidence/gitleaks-history.json"],
        ["docker", "run", "--rm", "-v", repo_mount, "-v", evidence_mount, GITLEAKS_IMAGE,
         "dir", "/evidence/release-worktree", "--no-banner", "--redact=100", "--report-format=json",
         "--report-path=/evidence/gitleaks-worktree.json"],
        ["docker", "run", "--rm", "-v", repo_mount, "-v", evidence_mount, TRIVY_IMAGE,
         "fs", "--scanners", "vuln", "--format", "json", "--output", "/evidence/trivy.json",
         "--exit-code", "0", "/repo"],
    ]
    try:
        for index, command in enumerate(commands):
            completed = subprocess.run(command, check=False, text=True, capture_output=True, timeout=900)
            report_names = ("gitleaks-history.json", "gitleaks-worktree.json", "trivy.json")
            report = output / report_names[index]
            accepted_codes = {0, 1} if index < 2 else {0}
            if completed.returncode not in accepted_codes or not report.is_file():
                raise RuntimeError(f"scanner failed with exit code {completed.returncode}")
        (output / "metadata.json").write_text(
            json.dumps(
                {
                    "created_at": datetime.now(UTC).isoformat(),
                    "images": [GITLEAKS_IMAGE, TRIVY_IMAGE],
                    "worktree_scope": "git tracked plus non-ignored untracked",
                    "worktree_file_count": staged_count,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    finally:
        if stage.is_dir():
            shutil.rmtree(stage)


def evaluate(output: Path) -> list[str]:
    errors: list[str] = []
    metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    if metadata.get("images") != [GITLEAKS_IMAGE, TRIVY_IMAGE] or not all(
        IMAGE_RE.fullmatch(item) for item in metadata.get("images", [])
    ):
        errors.append("scanner identity is missing or not immutable")
    for report_name in ("gitleaks-history.json", "gitleaks-worktree.json"):
        leaks = json.loads((output / report_name).read_text(encoding="utf-8"))
        if not isinstance(leaks, list):
            errors.append(f"malformed secret-scan evidence: {report_name}")
            continue
        unresolved = []
        for finding in leaks:
            disposition = FALSE_POSITIVE_FIXTURES.get(str(finding.get("Fingerprint", "")))
            if (
                disposition is None
                or finding.get("RuleID") != disposition["rule"]
                or finding.get("File") != disposition["path"]
            ):
                unresolved.append(finding)
        if unresolved:
            errors.append(f"confirmed/potential secret findings in {report_name}: {len(unresolved)}")
    trivy = json.loads((output / "trivy.json").read_text(encoding="utf-8"))
    vulnerabilities = [
        vuln
        for result in trivy.get("Results", [])
        for vuln in (result.get("Vulnerabilities") or [])
    ]
    counts = {severity: 0 for severity in ("CRITICAL", "HIGH", "MEDIUM")}
    for vuln in vulnerabilities:
        severity = str(vuln.get("Severity", "")).upper()
        if severity in counts:
            counts[severity] += 1
    for severity, count in counts.items():
        if count:
            errors.append(f"undisposed {severity} vulnerabilities: {count}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--evaluate-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not args.evaluate_only:
            run_scanners(args.repository, args.output_dir)
        errors = evaluate(args.output_dir)
    except (OSError, UnicodeError, ValueError, KeyError, json.JSONDecodeError, subprocess.SubprocessError, RuntimeError) as exc:
        print(f"SECURITY AUDIT FAILED: {exc}", file=sys.stderr)
        return 2
    if errors:
        for error in errors:
            print(f"SECURITY AUDIT FAILED: {error}", file=sys.stderr)
        return 3
    print("SECURITY AUDIT PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
