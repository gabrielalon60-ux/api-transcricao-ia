"""
G10-B1 Local Real WhatsApp / WUZAPI E2E Test Runner & Harness Utility.

Provides controlled subcommands:
- preflight: Offline prerequisite, configuration, and phase validation (P1 allowed).
- prepare-wuzapi: Prepares pinned WUZAPI local image tag (P2-gated).
- up: Starts g10b1 local Docker stack (P2-gated).
- seed-fixtures: Programs minimum disposable DB fixtures (Org, Bot, Instance) (P2/P3/P4 or G10_B1_ALLOW_FIXTURE_SEEDING-gated).
- status: Checks health of g10b1 container stack (P1 allowed).
- replay: Replays a sanitized local JSON webhook fixture with valid HMAC (P2-gated).
- down: Stops g10b1 stack preserving data/session volumes (P2-gated).
- cleanup: Destructively stops g10b1 stack and removes owned volumes (P2-gated).

STRICT SAFETY & PHASE RULES:
- Operates ONLY on g10b1_ owned Docker resources using project name 'g10b1'.
- Requires G10_B1_AUTHORIZED_PHASE=P2 for stack mutating/execution commands.
- NEVER prints secret values or tokens.
- NEVER auto-scans QR codes or connects WhatsApp.
- NEVER accesses staging/VPS/production.
"""

import argparse
import hashlib
import hmac
import os
import socket
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
COMPOSE_FILE = ROOT_DIR / "deploy" / "compose.g10b1.yml"
ENV_FILE = ROOT_DIR / ".env.g10b1.local"

REQUIRED_ENV_VARS = [
    "G10_B1_WUZAPI_TOKEN",
    "G10_B1_WUZAPI_WEBHOOK_SECRET",
    "G10_B1_TEST_WHATSAPP_NUMBER",
]

PROJECT_NAME = "g10b1"
PINNED_WUZAPI_COMMIT = "9487eca9a40f292d19953a44983979c85d91ccce"  # WUZAPI release v1.0.8
PINNED_WUZAPI_IMAGE = f"g10b1-wuzapi:{PINNED_WUZAPI_COMMIT}"


def check_phase_authorized(command_name: str) -> bool:
    current_phase = os.environ.get("G10_B1_AUTHORIZED_PHASE", "").strip().upper()
    if current_phase not in ("P2", "P2_STACK", "P2_EXECUTION"):
        sys.stderr.write(
            f"PHASE_NOT_AUTHORIZED: Command '{command_name}' requires environment variable G10_B1_AUTHORIZED_PHASE=P2.\n"
        )
        return False
    return True


def check_port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def run_cmd(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd, cwd=str(ROOT_DIR), check=check, capture_output=True, text=True
        )
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(f"Command failed: {' '.join(cmd)}\n{exc.stderr}\n")
        raise


def run_preflight() -> bool:
    print("=== G10-B1 PREFLIGHT CHECK ===")
    errors = 0

    # 1. Check Compose file
    if COMPOSE_FILE.is_file():
        print("  [PASS] deploy/compose.g10b1.yml exists")
    else:
        print(f"  [FAIL] Missing compose file: {COMPOSE_FILE}")
        errors += 1

    # 2. Check Docker availability
    try:
        proc = run_cmd(["docker", "info"], check=False)
        if proc.returncode == 0:
            print("  [PASS] Docker engine is available")
        else:
            print("  [FAIL] Docker engine is not responsive")
            errors += 1
    except Exception as exc:
        print(f"  [FAIL] Docker check exception: {exc}")
        errors += 1

    # 3. Check WUZAPI image pinning in compose file
    compose_content = COMPOSE_FILE.read_text(encoding="utf-8")
    if PINNED_WUZAPI_IMAGE in compose_content:
        print(f"  [PASS] WUZAPI image pinned to local tag '{PINNED_WUZAPI_IMAGE}'")
    else:
        print(f"  [FAIL] WUZAPI compose image tag mismatch (expected '{PINNED_WUZAPI_IMAGE}')")
        errors += 1

    # 4. Check network egress configuration (no internal: true blocking WhatsApp/Gemini)
    if "internal: true" not in compose_content:
        print("  [PASS] Compose network allows outbound egress (internal: true absent)")
    else:
        print("  [FAIL] Prohibited 'internal: true' found in compose network definition")
        errors += 1

    # 5. Check .env.g10b1.local status
    if ENV_FILE.is_file():
        print("  [PASS] .env.g10b1.local file present")
        env_content = ENV_FILE.read_text(encoding="utf-8")
        missing_vars = [
            var for var in REQUIRED_ENV_VARS if f"{var}=" not in env_content
        ]
        if not missing_vars:
            print("  [PASS] Required secret variable names present in .env.g10b1.local")
        else:
            print(f"  [WARN] Missing variable names in .env.g10b1.local: {missing_vars}")
    else:
        print("  [INFO] EXECUTION_SECRETS_NOT_VALIDATED_IN_P1 (.env.g10b1.local unpopulated in P1)")

    # 6. Check no HMAC adapter container
    if "wuzapi-hmac-adapter" not in compose_content:
        print("  [PASS] No HMAC adapter present (native WUZAPI HMAC configured)")
    else:
        print("  [FAIL] Prohibited HMAC adapter found in compose file")
        errors += 1

    if errors == 0:
        print("\n=== PREFLIGHT PASSED ===")
        return True
    else:
        print(f"\n=== PREFLIGHT FAILED ({errors} errors) ===")
        return False


def run_prepare_wuzapi() -> None:
    if not check_phase_authorized("prepare-wuzapi"):
        sys.exit(1)

    print(f"=== PREPARING PINNED WUZAPI SOURCE & IMAGE (commit {PINNED_WUZAPI_COMMIT}) ===")
    import tempfile
    prep_dir = Path(tempfile.gettempdir()) / "g10b1_wuzapi_prep"
    prep_dir.mkdir(parents=True, exist_ok=True)

    repo_dir = prep_dir / "wuzapi"
    if not repo_dir.is_dir():
        print(f"Cloning asternic/wuzapi into operator cache: {repo_dir}")
        subprocess.run(
            ["git", "clone", "https://github.com/asternic/wuzapi.git", str(repo_dir)],
            check=True,
        )

    print(f"Checking out exact commit {PINNED_WUZAPI_COMMIT} (v1.0.8)...")
    subprocess.run(["git", "fetch", "--tags", "origin"], cwd=str(repo_dir), check=True)
    subprocess.run(["git", "checkout", PINNED_WUZAPI_COMMIT], cwd=str(repo_dir), check=True)

    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo_dir), capture_output=True, text=True, check=True)
    actual_sha = proc.stdout.strip()
    if actual_sha != PINNED_WUZAPI_COMMIT:
        sys.exit(f"SHA mismatch! Expected {PINNED_WUZAPI_COMMIT}, got {actual_sha}")

    print(f"Building local image '{PINNED_WUZAPI_IMAGE}'...")
    subprocess.run(["docker", "build", "-t", PINNED_WUZAPI_IMAGE, "."], cwd=str(repo_dir), check=True)
    print("WUZAPI image prepared successfully.")


def run_up() -> None:
    if not check_phase_authorized("up"):
        sys.exit(1)

    print("=== STARTING G10-B1 LOCAL CONTAINER STACK ===")
    if not COMPOSE_FILE.is_file():
        sys.exit("Compose file missing.")

    cmd = ["docker", "compose", "-p", PROJECT_NAME, "-f", str(COMPOSE_FILE)]
    if ENV_FILE.is_file():
        cmd.extend(["--env-file", str(ENV_FILE)])
    cmd.extend(["up", "-d"])

    proc = run_cmd(cmd, check=False)
    if proc.returncode == 0:
        print("Stack started successfully.")
    else:
        print(f"Failed to start stack:\n{proc.stderr}")
        sys.exit(1)


def run_status() -> None:
    print("=== G10-B1 STACK STATUS ===")
    proc = run_cmd(["docker", "ps", "--filter", f"name={PROJECT_NAME}_"], check=False)
    print(proc.stdout)


def run_replay(fixture_path: str) -> None:
    if not check_phase_authorized("replay"):
        sys.exit(1)

    print(f"=== REPLAYING SANITIZED FIXTURE: {fixture_path} ===")
    path = Path(fixture_path)
    if not path.is_file():
        sys.exit(f"Fixture file not found: {path}")

    body_bytes = path.read_bytes()
    secret = os.environ.get("G10_B1_WUZAPI_WEBHOOK_SECRET", "test_wuzapi_secret")
    sig = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()

    import urllib.request
    req = urllib.request.Request(
        "http://localhost:8000/webhook",
        data=body_bytes,
        headers={"Content-Type": "application/json", "x-hmac-signature": sig},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode("utf-8")
            print(f"Replay response ({resp.status}): {body}")
    except Exception as exc:
        print(f"Replay failed: {exc}")
        sys.exit(1)


def get_wuzapi_runtime_instance_id() -> str:
    """
    Safely queries local WUZAPI (127.0.0.1:18080) in memory to resolve the canonical
    runtime user/instance ID for the exact synthetic test user 'g10b1_test'.
    Tokens are held in memory only and never printed or logged.
    """
    token = os.environ.get("G10_B1_WUZAPI_TOKEN")
    if not token and ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("G10_B1_WUZAPI_TOKEN="):
                token = line.split("=", 1)[1].strip().strip("\"'")
                break

    if not token:
        raise ValueError("G10_B1_WUZAPI_TOKEN not found in environment or .env.g10b1.local")

    import json
    import urllib.request

    req = urllib.request.Request(
        "http://127.0.0.1:18080/session/status",
        headers={"token": token},
    )
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            if resp.status != 200:
                raise ValueError(f"WUZAPI returned status {resp.status} on /session/status")
            data_bytes = resp.read()
            payload = json.loads(data_bytes.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Failed to query WUZAPI session status on 127.0.0.1:18080: {exc}") from exc

    user_data = payload.get("data", {})
    user_name = user_data.get("name")
    if user_name != "g10b1_test":
        raise ValueError(f"WUZAPI user name mismatch: expected 'g10b1_test', got '{user_name}'")

    user_id = user_data.get("id")
    if not user_id or not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("WUZAPI user ID is missing or invalid in /session/status response")

    return user_id.strip()


def run_seed_fixtures() -> None:
    # 1. Mandatory Dedicated Fixture Seeding Authorization Guard & Phase Validation
    allow_seeding = os.environ.get("G10_B1_ALLOW_FIXTURE_SEEDING", "").strip()
    current_phase = os.environ.get("G10_B1_AUTHORIZED_PHASE", "").strip().upper()
    if allow_seeding != "1":
        sys.stderr.write(
            "FIXTURE_SEEDING_NOT_AUTHORIZED: Command 'seed-fixtures' requires explicit environment variable G10_B1_ALLOW_FIXTURE_SEEDING=1.\n"
        )
        sys.exit(1)
    if current_phase not in ("P2", "P3", "P4", "P2_STACK", "P2_EXECUTION"):
        sys.stderr.write(
            "PHASE_NOT_AUTHORIZED: Command 'seed-fixtures' requires G10_B1_AUTHORIZED_PHASE in (P2, P3, P4).\n"
        )
        sys.exit(1)

    print("=== SEEDING G10-B1 MINIMUM TEST FIXTURES (DISPOSABLE DB ONLY) ===")

    # 2. Strict Docker & PostgreSQL Safety Identity Guard
    inspect_cmd = [
        "docker", "inspect", "g10b1_postgres",
        "--format", "{{index .Config.Labels \"com.docker.compose.project\"}}"
    ]
    inspect_proc = run_cmd(inspect_cmd, check=False)
    if inspect_proc.returncode != 0 or inspect_proc.stdout.strip() != PROJECT_NAME:
        sys.stderr.write(
            f"SAFETY_CHECK_FAILED: Container g10b1_postgres is not owned by compose project '{PROJECT_NAME}'. Refusing mutation.\n"
        )
        sys.exit(1)

    check_cmd = [
        "docker", "exec", "g10b1_postgres", "psql", "-U", "g10b1_user", "-d", "platform_g10b1",
        "-t", "-A", "-c", "SELECT current_database() || '|' || current_user || '|' || version();"
    ]
    proc = run_cmd(check_cmd, check=False)
    if proc.returncode != 0 or not proc.stdout.strip():
        sys.stderr.write(f"SAFETY_CHECK_FAILED: Unable to verify disposable DB identity: {proc.stderr}\n")
        sys.exit(1)

    db_ident = proc.stdout.strip()
    parts = db_ident.split("|")
    db_name = parts[0] if len(parts) > 0 else ""
    db_user = parts[1] if len(parts) > 1 else ""
    db_ver = parts[2] if len(parts) > 2 else ""

    if db_name != "platform_g10b1" or db_user != "g10b1_user" or "PostgreSQL 15" not in db_ver:
        sys.stderr.write(f"PROHIBITED_TARGET: Refusing mutation on non-disposable target: {db_ident}\n")
        sys.exit(1)

    # 3. Dynamic WUZAPI Runtime Identifier Resolution
    try:
        external_instance_id = get_wuzapi_runtime_instance_id()
    except Exception as exc:
        sys.stderr.write(f"WUZAPI_RESOLUTION_FAILED: {exc}\n")
        sys.exit(1)

    # 4. Fail-Closed Idempotent PL/pgSQL Block
    plpgsql_script = f"""
    DO $$
    DECLARE
        v_org_count INT;
        v_bot_count INT;
        v_inst_count INT;
        v_org_slug TEXT;
        v_org_status TEXT;
        v_bot_org TEXT;
        v_bot_key TEXT;
        v_bot_status TEXT;
        v_inst_id TEXT;
        v_inst_org TEXT;
        v_inst_bot TEXT;
        v_inst_prov TEXT;
        v_inst_ext TEXT;
        v_inst_status TEXT;
    BEGIN
        -- 1. Organization Fail-Closed / Idempotent Check
        SELECT count(*), min(slug), min(status)
        INTO v_org_count, v_org_slug, v_org_status
        FROM organizations
        WHERE id = 'org-g10b1-test' OR slug = 'g10b1-test-org';

        IF v_org_count > 0 THEN
            IF v_org_count > 1 OR v_org_slug != 'g10b1-test-org' OR v_org_status != 'ACTIVE' THEN
                RAISE EXCEPTION 'FIXTURE_CONFLICT: Conflicting organization row exists (count=%, slug=%, status=%)', v_org_count, v_org_slug, v_org_status;
            END IF;
        ELSE
            INSERT INTO organizations (id, name, slug, status)
            VALUES ('org-g10b1-test', 'G10-B1 Test Organization', 'g10b1-test-org', 'ACTIVE');
        END IF;

        -- 2. Bot Fail-Closed / Idempotent Check
        SELECT count(*), min(organization_id), min(service_key), min(status)
        INTO v_bot_count, v_bot_org, v_bot_key, v_bot_status
        FROM bots
        WHERE id = 'bot-g10b1-test' OR service_key = 'g10b1-test-bot-key';

        IF v_bot_count > 0 THEN
            IF v_bot_count > 1 OR v_bot_org != 'org-g10b1-test' OR v_bot_key != 'g10b1-test-bot-key' OR v_bot_status != 'ACTIVE' THEN
                RAISE EXCEPTION 'FIXTURE_CONFLICT: Conflicting bot row exists (count=%, org=%, key=%, status=%)', v_bot_count, v_bot_org, v_bot_key, v_bot_status;
            END IF;
        ELSE
            INSERT INTO bots (id, organization_id, name, service_key, status)
            VALUES ('bot-g10b1-test', 'org-g10b1-test', 'G10-B1 Test Bot', 'g10b1-test-bot-key', 'ACTIVE');
        END IF;

        -- 3. Instance Fail-Closed / Idempotent Check
        SELECT count(*), min(id), min(organization_id), min(bot_id), min(provider), min(external_instance_id), min(status)
        INTO v_inst_count, v_inst_id, v_inst_org, v_inst_bot, v_inst_prov, v_inst_ext, v_inst_status
        FROM instances
        WHERE id = 'inst-g10b1-test' OR (provider = 'WUZAPI' AND external_instance_id = '{external_instance_id}');

        IF v_inst_count > 0 THEN
            IF v_inst_count > 1
               OR v_inst_id != 'inst-g10b1-test'
               OR v_inst_org != 'org-g10b1-test'
               OR v_inst_bot != 'bot-g10b1-test'
               OR v_inst_prov != 'WUZAPI'
               OR v_inst_ext != '{external_instance_id}'
               OR v_inst_status != 'ACTIVE' THEN
                RAISE EXCEPTION 'FIXTURE_CONFLICT: Conflicting instance row exists (count=%, id=%, ext=%, status=%)', v_inst_count, v_inst_id, v_inst_ext, v_inst_status;
            END IF;
        ELSE
            INSERT INTO instances (id, organization_id, bot_id, provider, external_instance_id, phone_number, status)
            VALUES ('inst-g10b1-test', 'org-g10b1-test', 'bot-g10b1-test', 'WUZAPI', '{external_instance_id}', '5511999990000', 'ACTIVE');
        END IF;
    END $$;
    """

    exec_cmd = [
        "docker", "exec", "g10b1_postgres", "psql", "-U", "g10b1_user", "-d", "platform_g10b1",
        "-c", plpgsql_script
    ]
    exec_proc = run_cmd(exec_cmd, check=False)
    if exec_proc.returncode != 0:
        sys.stderr.write(f"FIXTURE_SEED_FAILED: {exec_proc.stderr}\n")
        sys.exit(1)

    # 5. Read-Back Verification Audit
    audit_sql = """
    SELECT 'organizations' AS tbl, count(*) FROM organizations UNION ALL
    SELECT 'bots' AS tbl, count(*) FROM bots UNION ALL
    SELECT 'instances' AS tbl, count(*) FROM instances UNION ALL
    SELECT 'users' AS tbl, count(*) FROM users UNION ALL
    SELECT 'enterprise_command_sessions' AS tbl, count(*) FROM enterprise_command_sessions UNION ALL
    SELECT 'whatsapp_chat_enterprise_bindings' AS tbl, count(*) FROM whatsapp_chat_enterprise_bindings;
    """
    audit_cmd = [
        "docker", "exec", "g10b1_postgres", "psql", "-U", "g10b1_user", "-d", "platform_g10b1",
        "-c", audit_sql
    ]
    audit_proc = run_cmd(audit_cmd, check=True)
    print(audit_proc.stdout)
    print("Minimum test fixtures seeded successfully (1 Org, 1 Bot, 1 Instance).")


def run_down() -> None:
    if not check_phase_authorized("down"):
        sys.exit(1)

    print("=== STOPPING G10-B1 CONTAINER STACK (PRESERVING SESSION VOLUMES) ===")
    cmd = ["docker", "compose", "-p", PROJECT_NAME, "-f", str(COMPOSE_FILE)]
    if ENV_FILE.is_file():
        cmd.extend(["--env-file", str(ENV_FILE)])
    cmd.extend(["down"])

    proc = run_cmd(cmd, check=False)
    print(proc.stdout or "Stack stopped. Session data volumes preserved.")


def run_cleanup() -> None:
    if not check_phase_authorized("cleanup"):
        sys.exit(1)

    print("=== DESTRUCTIVELY STOPPING G10-B1 CONTAINER STACK AND REMOVING VOLUMES ===")
    cmd = ["docker", "compose", "-p", PROJECT_NAME, "-f", str(COMPOSE_FILE)]
    if ENV_FILE.is_file():
        cmd.extend(["--env-file", str(ENV_FILE)])
    cmd.extend(["down", "-v"])

    proc = run_cmd(cmd, check=False)
    print(proc.stdout or "Stack stopped and owned g10b1 volumes destroyed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="G10-B1 E2E Test Runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("preflight", help="Run offline preflight check (P1 allowed)")
    subparsers.add_parser("prepare-wuzapi", help="Prepare local pinned WUZAPI image (P2-gated)")
    subparsers.add_parser("up", help="Start local g10b1 container stack (P2-gated)")
    subparsers.add_parser("seed-fixtures", help="Seed minimum disposable DB fixtures (G10_B1_ALLOW_FIXTURE_SEEDING=1 mandatory, P2/P3/P4-gated)")
    subparsers.add_parser("status", help="Check g10b1 stack status (P1 allowed)")

    replay_parser = subparsers.add_parser("replay", help="Replay sanitized webhook fixture (P2-gated)")
    replay_parser.add_argument("--fixture", required=True, help="Path to sanitized JSON fixture")

    subparsers.add_parser("down", help="Stop g10b1 stack preserving session volumes (P2-gated)")
    subparsers.add_parser("cleanup", help="Destructively stop g10b1 stack and remove volumes (P2-gated)")

    args = parser.parse_args()

    if args.command == "preflight":
        ok = run_preflight()
        sys.exit(0 if ok else 1)
    elif args.command == "prepare-wuzapi":
        run_prepare_wuzapi()
    elif args.command == "up":
        run_up()
    elif args.command == "seed-fixtures":
        run_seed_fixtures()
    elif args.command == "status":
        run_status()
    elif args.command == "replay":
        run_replay(args.fixture)
    elif args.command == "down":
        run_down()
    elif args.command == "cleanup":
        run_cleanup()


if __name__ == "__main__":
    main()
