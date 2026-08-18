#!/usr/bin/env bash
set -Eeuo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
: "${RELEASE_ENV_FILE:?Set RELEASE_ENV_FILE to an approved release env file}"
: "${RELEASE_AUTHORIZATION_REFERENCE:?Set the approved release authorization reference}"
python scripts/operations/gate10_preflight.py --env-file "$RELEASE_ENV_FILE" --compose-file deploy/compose.release.yml
docker compose --env-file "$RELEASE_ENV_FILE" -f deploy/compose.release.yml config --quiet
echo "Validated immutable update request ${RELEASE_AUTHORIZATION_REFERENCE}."
echo "Execution remains governed by the deployment and rollback runbooks."
