#!/usr/bin/env bash
set -Eeuo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
: "${RELEASE_ENV_FILE:?Set RELEASE_ENV_FILE to an approved release env file}"
[[ -f "$RELEASE_ENV_FILE" ]] || { echo "ERROR: approved release environment file not found" >&2; exit 2; }
python scripts/operations/gate10_preflight.py --env-file "$RELEASE_ENV_FILE" --compose-file deploy/compose.release.yml
docker compose --env-file "$RELEASE_ENV_FILE" -f deploy/compose.release.yml config --quiet
echo "Preflight passed. Deployment requires the separately approved environment procedure."
