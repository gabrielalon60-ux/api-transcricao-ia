# Gate 10 Deployment Runbook

## Safety boundary

This runbook is procedural only during G10-A. Do not access or mutate staging or production without an explicit G10-B/G10-C authorization naming the environment, immutable artifact, operator, maintenance window, secret-store reference, and rollback target. Never paste secret values into commands, evidence, tickets, or logs.

## Required inputs

- full approved 40-character commit SHA and application `name@sha256` image;
- pinned PostgreSQL and approved WUZAPI image digests;
- protected environment file assembled from the approved secret store;
- DNS/TLS/edge-network inventory, deploy identity, backup identity, and prior-known-good manifest;
- successful security audit and recent isolated restore evidence.

## Procedure

1. Record the inputs in `GATE_10_RELEASE_MANIFEST.md` without secret values.
2. Run `gate10_preflight.py`; any nonzero exit blocks release.
3. Render `deploy/compose.release.yml` and verify that only Orchestrator joins the edge network and no service publishes a host port.
4. Confirm key-only, non-root SSH, firewall allowlist, break-glass ownership, TLS certificate, WUZAPI admin isolation, and backup readiness.
5. Obtain explicit release-owner approval for the exact manifest.
6. Pull only the approved immutable images. Do not build on the target host and do not run unreviewed migrations.
7. Apply the release through the environment-owned orchestrator. Stop on any unhealthy service.
8. Run bounded health, authentication, webhook-rejection, queue, Writer, and log-canary checks.
9. If any check fails, invoke the rollback runbook. Do not delete volumes or prune images.

## Evidence

Record timestamps, sanitized command outcomes, exact immutable identities, health results, operator, authorization reference, and rollback decision. A deployment is not Gate 10 approval.
