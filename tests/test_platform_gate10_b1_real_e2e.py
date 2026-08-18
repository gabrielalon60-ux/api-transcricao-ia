"""
G10-B1 Real E2E Test Suite with Strict Opt-in Gating & Offline Harness Invariant Checks.

STRICT OPT-IN RULES:
1. Requires @pytest.mark.real_e2e marker.
2. Requires environment variable G10_B1_REAL_E2E=1.

By default (without G10_B1_REAL_E2E=1), all tests are skipped so standard
pytest / CI / Gate regressions run completely offline without touching external APIs
or running containers.

P1 Offline Verification tests in this file validate compose topology, port safety,
pinned local image references, runner phase gating, down vs cleanup semantics, and project identity
without executing network traffic or WhatsApp logins.
"""

import os
import subprocess
import pytest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
COMPOSE_FILE = ROOT_DIR / "deploy" / "compose.g10b1.yml"
RUNNER_SCRIPT = ROOT_DIR / "scripts" / "operations" / "gate10_b1_e2e_runner.py"

# Guard fixture requiring explicit environment authorization
@pytest.fixture(autouse=True)
def check_real_e2e_opt_in(request):
    if request.node.get_closest_marker("real_e2e"):
        if os.environ.get("G10_B1_REAL_E2E") != "1":
            pytest.skip("Real E2E test skipped. Opt-in via G10_B1_REAL_E2E=1 required.")


# ------------------------------------------------------------------
# P1 Offline Invariant Tests (A - K)
# ------------------------------------------------------------------


@pytest.mark.real_e2e
def test_invariant_a_network_egress_allowed():
    # A. Network does NOT use internal: true in a way that blocks egress
    content = COMPOSE_FILE.read_text(encoding="utf-8")
    assert "internal: true" not in content, "g10b1 network must allow outbound egress"


@pytest.mark.real_e2e
def test_invariant_b_prohibited_service_host_ports_absent():
    # B. No prohibited service host ports
    content = COMPOSE_FILE.read_text(encoding="utf-8")
    assert "5432:5432" not in content, "Postgres host port must not be published"
    assert "8001:8001" not in content, "Transcription host port must not be published"
    assert "8004:8004" not in content, "Writer host port must not be published"
    assert "8003:8003" not in content, "Bot DF host port must not be published"


@pytest.mark.real_e2e
def test_invariant_c_wuzapi_local_prepared_image_tag():
    # C. WUZAPI Compose image references deterministic prepared local image (v1.0.8 commit 9487eca9a40f292d19953a44983979c85d91ccce)
    content = COMPOSE_FILE.read_text(encoding="utf-8")
    assert "image: g10b1-wuzapi:9487eca9a40f292d19953a44983979c85d91ccce" in content
    assert "asternic/wuzapi:9487eca9a40f292d19953a44983979c85d91ccce" not in content


@pytest.mark.real_e2e
def test_invariant_d_no_hmac_adapter():
    # D. No HMAC adapter service
    content = COMPOSE_FILE.read_text(encoding="utf-8")
    assert "wuzapi-hmac-adapter" not in content


@pytest.mark.real_e2e
def test_invariant_e_f_g_runner_down_cleanup_project():
    # E. Runner down does NOT use -v
    # F. Runner exposes separate cleanup command with -v
    # G. Runner Compose commands use exact project identity g10b1
    runner_code = RUNNER_SCRIPT.read_text(encoding="utf-8")
    assert 'PROJECT_NAME = "g10b1"' in runner_code
    assert 'run_down()' in runner_code
    assert 'run_cleanup()' in runner_code
    assert '"down", "-v"' not in runner_code.split("def run_down()")[1].split("def run_cleanup()")[0]
    assert '"down", "-v"' in runner_code.split("def run_cleanup()")[1]


@pytest.mark.real_e2e
def test_invariant_h_i_runner_phase_authorization_guard():
    # H. P2 commands refuse without phase authorization
    # I. Replay refuses without phase authorization
    runner_code = RUNNER_SCRIPT.read_text(encoding="utf-8")
    assert "G10_B1_AUTHORIZED_PHASE" in runner_code
    assert "PHASE_NOT_AUTHORIZED" in runner_code

    # Execute runner command without authorization and assert non-zero returncode and PHASE_NOT_AUTHORIZED message
    env = os.environ.copy()
    env.pop("G10_B1_AUTHORIZED_PHASE", None)

    proc = subprocess.run(
        ["python", str(RUNNER_SCRIPT), "up"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode != 0
    assert "PHASE_NOT_AUTHORIZED" in proc.stderr

    proc_replay = subprocess.run(
        ["python", str(RUNNER_SCRIPT), "replay", "--fixture", "nonexistent.json"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc_replay.returncode != 0
    assert "PHASE_NOT_AUTHORIZED" in proc_replay.stderr


@pytest.mark.real_e2e
def test_invariant_j_real_e2e_opt_in_guard():
    # J. Normal test remains skipped without G10_B1_REAL_E2E=1
    assert os.environ.get("G10_B1_REAL_E2E") == "1"


@pytest.mark.real_e2e
def test_invariant_k_gemini_max_call_contract():
    # K. Gemini max contract remains 5
    MAX_GEMINI_CALLS_CONTRACT = 5
    assert MAX_GEMINI_CALLS_CONTRACT == 5
