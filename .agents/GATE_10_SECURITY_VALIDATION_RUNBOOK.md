# Gate 10 Security Validation Runbook

G10-A permits repository/local checks only. Network, SSH, WUZAPI, staging log-canary, actual database-role, and restore exercises require separately approved G10-B resources.

## Local audit

Run the pinned auditor with an invocation-owned evidence directory outside the repository:

```sh
python scripts/operations/gate10_security_audit.py --repository . --output-dir /protected/evidence/g10-a-<timestamp>
```

Unavailable, nonzero, malformed, stale, or non-immutable scanner evidence fails closed. Confirmed secrets always block and require revocation/remediation. Critical findings always block. High findings block unless the user approves an exact-artifact mitigation expiring within 30 days. Medium findings require an explicit fixed, false-positive, or accepted-risk disposition. Low/informational findings remain visible.

## Staging-only checks

With G10-B authorization, prove HTTPS/certificate behavior, PostgreSQL TLS encryption and `sslmode=verify-full` handshake with `sslrootcert`, CA private key isolation from runtime environment, zero exposure of `server.key` to application containers, per-IP edge limits, external inaccessibility of PostgreSQL and Database Writer, internal least privilege, WUZAPI admin isolation and media retention, signed webhook rejection/acceptance, restart/idempotency, bounded temporary-file cleanup, and zero raw canary secret/CPF/CNPJ/DSN/phone/provider-output matches in collected logs.

Evidence must contain no secret value, client data, broad exclusion, or raw vulnerable payload.
