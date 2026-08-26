# Gate 10 Deployment Runbook

## Safety boundary

This runbook is procedural only during G10-A. Do not access or mutate staging or production without an explicit G10-B/G10-C authorization naming the environment, immutable artifact, operator, maintenance window, secret-store reference, and rollback target. Never paste secret values into commands, evidence, tickets, or logs.

## Required inputs

- full approved 40-character commit SHA and application `name@sha256` image;
- pinned PostgreSQL and approved WUZAPI image digests;
- protected environment file assembled from the approved secret store;
- provisioned runtime TLS directory (`POSTGRES_TLS_DIR`) containing `ca.crt`, `server.crt`, and `server.key` (with `ca.key` strictly isolated in offline/operator storage);
- database URLs configured with `sslmode=verify-full&sslrootcert=/run/secrets/postgres_ca.crt`;
- DNS/TLS/edge-network inventory, deploy identity, backup identity, and prior-known-good manifest;
- successful security audit and recent isolated restore evidence.

## Procedure

1. Record the inputs in `GATE_10_RELEASE_MANIFEST.md` without secret values.
2. Provision PostgreSQL TLS certificates via `python scripts/operations/generate_postgres_tls.py --runtime-dir <path> --ca-private-dir <isolated-path>`. Verify `server.key` mode is `0600` and `ca.key` is not in the runtime directory.
3. Run `gate10_preflight.py`; any nonzero exit blocks release.
4. Render `deploy/compose.release.yml` and verify that only Orchestrator joins the edge network, `platform-db` has TLS enabled (`ssl=on`), and no service publishes a host port.
5. Confirm key-only, non-root SSH, firewall allowlist, break-glass ownership, TLS certificate, WUZAPI admin isolation, and backup readiness.
6. Obtain explicit release-owner approval for the exact manifest.
7. Pull only the approved immutable images. Do not build on the target host and do not run unreviewed migrations.
8. Apply the release through the environment-owned orchestrator. Stop on any unhealthy service.
9. Run bounded health, authentication, webhook-rejection, queue, Writer, TLS verify-full handshake, and log-canary checks.
10. If any check fails, invoke the rollback runbook. Do not delete volumes or prune images.

## Evidence

Record timestamps, sanitized command outcomes, exact immutable identities, health results, operator, authorization reference, and rollback decision. A deployment is not Gate 10 approval.
