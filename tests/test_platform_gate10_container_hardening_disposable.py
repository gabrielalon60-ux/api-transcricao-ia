from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.real_pg15


ROOT = Path(__file__).resolve().parents[1]


def test_release_contract_enforces_runtime_hardening() -> None:
    compose = (ROOT / "deploy" / "compose.release.yml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    for control in ("read_only: true", "cap_drop: [ALL]", "no-new-privileges:true", "pids_limit:", "mem_limit:", "cpus:"):
        assert control in compose
    assert "USER 10001:10001" in dockerfile
    assert "apt-get" not in dockerfile


@pytest.mark.skipif(os.environ.get("GATE10_RUN_DOCKER") != "1", reason="explicit disposable Docker authorization required")
def test_built_runtime_is_nonroot_and_readonly_compatible() -> None:
    tag = f"gate10-app-test:{uuid.uuid4().hex}"
    try:
        build = subprocess.run(["docker", "build", "--pull=false", "-t", tag, "."], cwd=ROOT, capture_output=True, text=True, timeout=900, check=False)
        assert build.returncode == 0, build.stderr[-2000:]
        inspect = subprocess.run(["docker", "image", "inspect", tag], capture_output=True, text=True, timeout=30, check=False)
        assert inspect.returncode == 0
        assert json.loads(inspect.stdout)[0]["Config"]["User"] == "10001:10001"
        probe = subprocess.run(
            ["docker", "run", "--rm", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", tag,
             "python", "-c", "import os,pathlib; assert os.getuid()==10001; p=pathlib.Path('/app/probe');\ntry: p.write_text('x')\nexcept OSError: pass\nelse: raise SystemExit(2)"],
            capture_output=True, text=True, timeout=60, check=False,
        )
        assert probe.returncode == 0, probe.stderr
    finally:
        subprocess.run(["docker", "image", "rm", "-f", tag], capture_output=True, timeout=60, check=False)
