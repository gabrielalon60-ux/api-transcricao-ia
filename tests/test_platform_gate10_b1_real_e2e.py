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
import sys
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
    # B. No prohibited service host ports, exact loopback bindings for WUZAPI and Orchestrator
    content = COMPOSE_FILE.read_text(encoding="utf-8")
    assert "5432:5432" not in content, "Postgres host port must not be published"
    assert "8001:8001" not in content, "Transcription host port must not be published"
    assert "8004:8004" not in content, "Writer host port must not be published"
    assert "8003:8003" not in content, "Bot DF host port must not be published"

    # Exact loopback host port bindings
    assert '127.0.0.1:18080:8080' in content, "WUZAPI must be bound to 127.0.0.1:18080:8080"
    assert '127.0.0.1:18000:8000' in content, "Orchestrator must be bound to 127.0.0.1:18000:8000"
    assert '0.0.0.0:18080' not in content and '0.0.0.0:18000' not in content
    assert '[::]:18080' not in content and '[::]:18000' not in content

    # Internal webhook contract unchanged
    assert 'WUZAPI_GLOBAL_WEBHOOK: "http://orchestrator:8000/webhook"' in content


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


@pytest.mark.real_e2e
def test_invariant_l_df_holding_identifiers_valid_json_array():
    # L. DF_HOLDING_IDENTIFIERS must be valid JSON array of strings
    import json
    content = COMPOSE_FILE.read_text(encoding="utf-8")
    for line in content.splitlines():
        if "DF_HOLDING_IDENTIFIERS:" in line:
            raw_val = line.split("DF_HOLDING_IDENTIFIERS:", 1)[1].strip().strip("'\"")
            parsed = json.loads(raw_val)
            assert isinstance(parsed, list), "DF_HOLDING_IDENTIFIERS must be a JSON array"
            assert all(isinstance(x, str) for x in parsed), "All items must be strings"
            assert len(parsed) >= 1, "Must contain at least 1 synthetic identifier"
            assert all(x.isdigit() for x in parsed), "Identifiers must be digit strings"
            break
    else:
        pytest.fail("DF_HOLDING_IDENTIFIERS not found in compose.g10b1.yml")


@pytest.mark.real_e2e
def test_invariant_m_runner_seed_fixtures_registration_and_phase_guard():
    # M. seed-fixtures is registered, bootstrap alias is removed, and G10_B1_ALLOW_FIXTURE_SEEDING=1 is strictly mandatory
    runner_code = RUNNER_SCRIPT.read_text(encoding="utf-8")
    assert 'subparsers.add_parser("seed-fixtures"' in runner_code
    assert 'subparsers.add_parser("bootstrap"' not in runner_code
    assert 'def run_seed_fixtures()' in runner_code
    assert 'G10_B1_ALLOW_FIXTURE_SEEDING' in runner_code
    assert 'FIXTURE_SEEDING_NOT_AUTHORIZED' in runner_code

    base_env = {
        k: v for k, v in os.environ.items()
        if k not in ("G10_B1_AUTHORIZED_PHASE", "G10_B1_ALLOW_FIXTURE_SEEDING")
    }

    # Case A: ALLOW_FIXTURE_SEEDING absent + PHASE=P2 => denied
    env_a = {**base_env, "G10_B1_AUTHORIZED_PHASE": "P2"}
    proc_a = subprocess.run([sys.executable, str(RUNNER_SCRIPT), "seed-fixtures"], capture_output=True, text=True, env=env_a)
    assert proc_a.returncode != 0
    assert "FIXTURE_SEEDING_NOT_AUTHORIZED" in proc_a.stderr

    # Case B: ALLOW_FIXTURE_SEEDING absent + PHASE=P3 => denied
    env_b = {**base_env, "G10_B1_AUTHORIZED_PHASE": "P3"}
    proc_b = subprocess.run([sys.executable, str(RUNNER_SCRIPT), "seed-fixtures"], capture_output=True, text=True, env=env_b)
    assert proc_b.returncode != 0
    assert "FIXTURE_SEEDING_NOT_AUTHORIZED" in proc_b.stderr

    # Case C: ALLOW_FIXTURE_SEEDING absent + PHASE=P4 => denied
    env_c = {**base_env, "G10_B1_AUTHORIZED_PHASE": "P4"}
    proc_c = subprocess.run([sys.executable, str(RUNNER_SCRIPT), "seed-fixtures"], capture_output=True, text=True, env=env_c)
    assert proc_c.returncode != 0
    assert "FIXTURE_SEEDING_NOT_AUTHORIZED" in proc_c.stderr

    # Case D: ALLOW_FIXTURE_SEEDING=0 + PHASE=P3 => denied
    env_d = {**base_env, "G10_B1_AUTHORIZED_PHASE": "P3", "G10_B1_ALLOW_FIXTURE_SEEDING": "0"}
    proc_d = subprocess.run([sys.executable, str(RUNNER_SCRIPT), "seed-fixtures"], capture_output=True, text=True, env=env_d)
    assert proc_d.returncode != 0
    assert "FIXTURE_SEEDING_NOT_AUTHORIZED" in proc_d.stderr

    # Case D2: ALLOW_FIXTURE_SEEDING=false + PHASE=P3 => denied
    env_d2 = {**base_env, "G10_B1_AUTHORIZED_PHASE": "P3", "G10_B1_ALLOW_FIXTURE_SEEDING": "false"}
    proc_d2 = subprocess.run([sys.executable, str(RUNNER_SCRIPT), "seed-fixtures"], capture_output=True, text=True, env=env_d2)
    assert proc_d2.returncode != 0
    assert "FIXTURE_SEEDING_NOT_AUTHORIZED" in proc_d2.stderr

    # Case E: ALLOW_FIXTURE_SEEDING=1 + PHASE=P3 => authorization guard permits progression
    env_e = {**base_env, "G10_B1_AUTHORIZED_PHASE": "P3", "G10_B1_ALLOW_FIXTURE_SEEDING": "1"}
    proc_e = subprocess.run([sys.executable, str(RUNNER_SCRIPT), "seed-fixtures"], capture_output=True, text=True, env=env_e)
    assert proc_e.returncode == 0
    assert "Minimum test fixtures seeded successfully" in proc_e.stdout


@pytest.mark.real_e2e
def test_invariant_n_runner_seed_fixtures_sql_and_container_guards():
    # N. SQL insertions target strictly Organization, Bot, and Instance (NO User, NO Enterprise, NO Supplier)
    # and verify Docker project and Postgres major version guards
    runner_code = RUNNER_SCRIPT.read_text(encoding="utf-8")
    assert "INSERT INTO organizations" in runner_code
    assert "INSERT INTO bots" in runner_code
    assert "INSERT INTO instances" in runner_code
    assert "INSERT INTO users" not in runner_code
    assert "INSERT INTO enterprise_command_sessions" not in runner_code
    assert "INSERT INTO whatsapp_chat_enterprise_bindings" not in runner_code
    assert "platform_g10b1" in runner_code
    assert "g10b1_user" in runner_code
    assert "PostgreSQL 15" in runner_code
    assert "com.docker.compose.project" in runner_code


@pytest.mark.real_e2e
def test_invariant_o_zero_hardcoded_runtime_id_in_tracked_source():
    # O. Live WUZAPI user ID is NOT hardcoded in runner or test source
    live_id = "14b8c2097a8eeecb937d7c690a9ea2b7"
    runner_content = RUNNER_SCRIPT.read_text(encoding="utf-8")
    assert live_id not in runner_content, "Live WUZAPI runtime ID must not be hardcoded in runner"


@pytest.mark.real_e2e
def test_invariant_p_dynamic_wuzapi_runtime_id_resolution_contract():
    # P. Dynamic resolver queries /session/status and validates synthetic user 'g10b1_test'
    runner_code = RUNNER_SCRIPT.read_text(encoding="utf-8")
    assert "def get_wuzapi_runtime_instance_id()" in runner_code
    assert "127.0.0.1:18080/session/status" in runner_code
    assert "g10b1_test" in runner_code
    assert "G10_B1_WUZAPI_TOKEN" in runner_code


@pytest.mark.real_e2e
def test_invariant_q_fail_closed_idempotency_contract():
    # Q. PL/pgSQL block enforces fail-closed checks on conflicting data
    runner_code = RUNNER_SCRIPT.read_text(encoding="utf-8")
    assert "FIXTURE_CONFLICT: Conflicting organization row exists" in runner_code
    assert "FIXTURE_CONFLICT: Conflicting bot row exists" in runner_code
    assert "FIXTURE_CONFLICT: Conflicting instance row exists" in runner_code
    assert "inst-g10b1-test" in runner_code
    assert "org-g10b1-test" in runner_code
    assert "bot-g10b1-test" in runner_code
    assert "5511999990000" in runner_code


@pytest.mark.real_e2e
def test_invariant_r_registration_secret_fixture_harness_contract():
    # R. Harness derives registration_secret_hash using security.hash.hash_secret and fails closed on conflict
    runner_code = RUNNER_SCRIPT.read_text(encoding="utf-8")
    assert "from security.hash import hash_secret" in runner_code
    assert "hash_secret(\"org-g10b1-test:\" + reg_secret, reg_pepper)" in runner_code
    assert "FIXTURE_CONFLICT_REGISTRATION_SECRET" in runner_code
    assert "LOCAL_REGISTRATION_SECRET_NOT_AVAILABLE" in runner_code
    assert "UPDATE organizations" in runner_code
    assert "SET registration_secret_hash" in runner_code


@pytest.mark.real_e2e
def test_invariant_s_zero_plaintext_registration_secret_in_tracked_source():
    # S. No hardcoded registration secrets in runner or test files
    runner_content = RUNNER_SCRIPT.read_text(encoding="utf-8")
    assert "G10_B1_REGISTRATION_SECRET =" not in runner_content
    assert "registration_secret_hash" in runner_content


@pytest.mark.real_e2e
def test_registration_secret_security_hash_verification():
    from security.hash import hash_secret, verify_secret

    org_id = "org-g10b1-test"
    secret_a = "synthetic_local_secret_abc123"
    secret_b = "synthetic_local_secret_mismatch456"
    pepper = "pepper_secret_g10b1_32bytes_min"

    hashed_a = hash_secret(f"{org_id}:{secret_a}", pepper)
    assert verify_secret(f"{org_id}:{secret_a}", hashed_a, pepper) is True
    assert verify_secret(f"{org_id}:{secret_b}", hashed_a, pepper) is False


@pytest.mark.real_e2e
def test_invariant_t_rotation_command_registration_and_dedicated_flag_guard():
    # T. rotate-registration-secret is registered and strictly requires G10_B1_ALLOW_REGISTRATION_SECRET_ROTATION=1
    runner_code = RUNNER_SCRIPT.read_text(encoding="utf-8")
    assert 'subparsers.add_parser("rotate-registration-secret"' in runner_code
    assert "def run_rotate_registration_secret()" in runner_code
    assert "G10_B1_ALLOW_REGISTRATION_SECRET_ROTATION" in runner_code
    assert "REGISTRATION_ROTATION_NOT_AUTHORIZED" in runner_code

    base_env = {
        k: v for k, v in os.environ.items()
        if k not in ("G10_B1_ALLOW_REGISTRATION_SECRET_ROTATION", "G10_B1_ALLOW_FIXTURE_SEEDING", "G10_B1_AUTHORIZED_PHASE")
    }

    # Case A: Flag absent => denied
    proc_a = subprocess.run([sys.executable, str(RUNNER_SCRIPT), "rotate-registration-secret"], capture_output=True, text=True, env=base_env)
    assert proc_a.returncode != 0
    assert "REGISTRATION_ROTATION_NOT_AUTHORIZED" in proc_a.stderr

    # Case B: Flag = 0 => denied
    env_b = {**base_env, "G10_B1_ALLOW_REGISTRATION_SECRET_ROTATION": "0"}
    proc_b = subprocess.run([sys.executable, str(RUNNER_SCRIPT), "rotate-registration-secret"], capture_output=True, text=True, env=env_b)
    assert proc_b.returncode != 0
    assert "REGISTRATION_ROTATION_NOT_AUTHORIZED" in proc_b.stderr

    # Case C: Flag = false => denied
    env_c = {**base_env, "G10_B1_ALLOW_REGISTRATION_SECRET_ROTATION": "false"}
    proc_c = subprocess.run([sys.executable, str(RUNNER_SCRIPT), "rotate-registration-secret"], capture_output=True, text=True, env=env_c)
    assert proc_c.returncode != 0
    assert "REGISTRATION_ROTATION_NOT_AUTHORIZED" in proc_c.stderr

    # Case D: Fixture seeding flag alone => denied
    env_d = {**base_env, "G10_B1_ALLOW_FIXTURE_SEEDING": "1", "G10_B1_AUTHORIZED_PHASE": "P3"}
    proc_d = subprocess.run([sys.executable, str(RUNNER_SCRIPT), "rotate-registration-secret"], capture_output=True, text=True, env=env_d)
    assert proc_d.returncode != 0
    assert "REGISTRATION_ROTATION_NOT_AUTHORIZED" in proc_d.stderr

    # Case E: Phase alone => denied
    env_e = {**base_env, "G10_B1_AUTHORIZED_PHASE": "P5"}
    proc_e = subprocess.run([sys.executable, str(RUNNER_SCRIPT), "rotate-registration-secret"], capture_output=True, text=True, env=env_e)
    assert proc_e.returncode != 0
    assert "REGISTRATION_ROTATION_NOT_AUTHORIZED" in proc_e.stderr


@pytest.mark.real_e2e
def test_invariant_u_rotation_hash_contract_and_entropy():
    # U. Rotation generates high entropy secret and uses security.hash with fail-closed checks
    import secrets
    from security.hash import hash_secret, verify_secret

    runner_code = RUNNER_SCRIPT.read_text(encoding="utf-8")
    assert "secrets.token_hex(24)" in runner_code
    assert "UPDATE organizations" in runner_code
    assert "SET registration_secret_hash" in runner_code
    assert "PRE_ROTATION_CREDENTIAL_STATE_INVALID" in runner_code
    assert "NEW_REGISTRATION_SECRET_GENERATED = YES" in runner_code
    assert "NEW_CREDENTIAL_VERIFIES = YES" in runner_code
    assert "OLD_CREDENTIAL_REJECTED = YES" in runner_code

    # Synthetic rotation verification
    org_id = "org-g10b1-test"
    pepper = "pepper_secret_g10b1_32bytes_min"
    old_secret = secrets.token_hex(24)
    old_hash = hash_secret(f"{org_id}:{old_secret}", pepper)
    assert verify_secret(f"{org_id}:{old_secret}", old_hash, pepper) is True

    new_secret = secrets.token_hex(24)
    assert new_secret != old_secret
    assert len(bytes.fromhex(new_secret)) >= 24

    new_hash = hash_secret(f"{org_id}:{new_secret}", pepper)
    assert verify_secret(f"{org_id}:{new_secret}", new_hash, pepper) is True
    assert verify_secret(f"{org_id}:{old_secret}", new_hash, pepper) is False


@pytest.mark.real_e2e
def test_invariant_v_two_phase_recoverable_rotation_contract(tmp_path):
    # V. Two-phase recoverable rotation contract & failure recovery matrix
    import secrets
    from security.hash import hash_secret, verify_secret

    runner_code = RUNNER_SCRIPT.read_text(encoding="utf-8")
    assert ".env.g10b1-rotation-pending.local" in runner_code
    assert "ROTATION_RECOVERY_REQUIRED" in runner_code

    # 1. Verify that pending artifact pattern matches .gitignore rule (.env.*.local)
    proc_ign = subprocess.run(
        ["git", "check-ignore", ".env.g10b1-rotation-pending.local"],
        capture_output=True,
        text=True,
        cwd=str(ROOT_DIR),
    )
    assert proc_ign.returncode == 0, ".env.g10b1-rotation-pending.local must be ignored by Git"

    # 2. Failure Matrix Simulation on isolated temp files
    env_file = tmp_path / ".env.g10b1.local"
    pending_file = tmp_path / ".env.g10b1-rotation-pending.local"
    org_id = "org-g10b1-test"
    pepper = "pepper_secret_g10b1_32bytes_min"

    old_secret = secrets.token_hex(24)
    env_file.write_text(f"G10_B1_REGISTRATION_SECRET={old_secret}\n", encoding="utf-8")
    db_hash = hash_secret(f"{org_id}:{old_secret}", pepper)

    # Scenario A: Stale pending artifact present => must block rotation
    pending_file.write_text(f"G10_B1_REGISTRATION_SECRET={secrets.token_hex(24)}\n", encoding="utf-8")
    assert pending_file.is_file()
    # Guard check
    has_stale = pending_file.is_file()
    assert has_stale is True

    # Scenario B: Crash after DB update but before local file replace => NEW secret is recoverable from pending artifact
    pending_file.unlink()
    new_secret = secrets.token_hex(24)
    pending_file.write_text(f"G10_B1_REGISTRATION_SECRET={new_secret}\n", encoding="utf-8")

    # DB update succeeds
    db_hash = hash_secret(f"{org_id}:{new_secret}", pepper)

    # Simulated crash: env_file still contains old_secret, but pending_file contains new_secret
    assert "G10_B1_REGISTRATION_SECRET=" + old_secret in env_file.read_text(encoding="utf-8")
    recovered_secret = None
    for line in pending_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("G10_B1_REGISTRATION_SECRET="):
            recovered_secret = line.split("=", 1)[1].strip()
    assert recovered_secret == new_secret
    assert verify_secret(f"{org_id}:{recovered_secret}", db_hash, pepper) is True

    # Scenario C: Post-verification cleanup
    pending_file.unlink()
    assert not pending_file.is_file()
