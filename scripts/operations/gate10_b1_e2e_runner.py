"""
G10-B1 Local Real WhatsApp / WUZAPI E2E Test Runner & Harness Utility.

Provides controlled subcommands:
- preflight: Offline prerequisite, configuration, and phase validation (P1 allowed).
- prepare-wuzapi: Prepares pinned WUZAPI local image tag (P2-gated).
- up: Starts g10b1 local Docker stack (P2-gated).
- bootstrap: Programs disposable DB fixtures (Org, Instance, User, Enterprise, Supplier) (P2-gated).
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
