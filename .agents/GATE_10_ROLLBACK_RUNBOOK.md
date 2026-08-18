# Gate 10 Rollback Runbook

## Preconditions

Rollback is authorized only for the environment and immutable prior-known-good artifact named in the approved release manifest. Target decision-to-recovery time is at most 15 minutes. Never use wildcard deletion, remove persistent volumes, reset a database, or downgrade schema without a separately approved compatibility procedure.

## Trigger

Trigger rollback on failed readiness/health, authentication regression, queue/Writer safety failure, secret exposure, unexpected public port, data-integrity concern, or an unresolved Critical/High security regression.

## Procedure

1. Stop further rollout and record the sanitized trigger and decision timestamp.
2. Confirm the prior manifest digest, configuration-schema compatibility, and database compatibility.
3. Reapply only the immutable prior application image; retain current and backup data volumes.
4. Verify service health, signed-webhook behavior, queue progress, Writer idempotency, and absence of raw canaries in logs.
5. If application rollback is unsafe because of data compatibility, keep traffic stopped and escalate to the separately approved restore procedure.
6. Record recovery time, final immutable identities, checks, and incident owner.

The physical timed exercise belongs to G10-B. The production invocation belongs to G10-C.
