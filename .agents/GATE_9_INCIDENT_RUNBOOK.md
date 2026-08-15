# Gate 9 Incident Runbook

> **Scope:** bounded read-only diagnosis against explicitly authorized environments.
> **Gate 9:** local disposable PostgreSQL 15 validation only.
> **Never:** expose payloads/PII/secrets, edit durable state directly, or blindly resend an ambiguous external operation.

All report commands use `scripts/operations/gate9_report.py`, dedicated `G9_*_DATABASE_URL` variables, PostgreSQL read-only transactions, a maximum 31-day UTC interval, and a maximum 1,000-row detailed result. Store no complete DSN in a shell history, ticket, screenshot, or incident note.

## Transcription or Gemini unavailable

- Signal: extraction backlog or bounded Transcription provider failure codes.
- Evidence: `blocked`, `failures`, `service-usage`, and correlation reports; process-health response and sanitized structured logs.
- First action: verify configured process liveness and whether failures are retryable or terminal.
- Safe recovery: restore the dependency and allow the frozen extraction retry/recovery path to proceed.
- Prohibited: synthetic success, manual token zero, direct ProcessingItem edits, real provider calls during Gate 9 verification.
- Escalate: sustained terminal/provider-auth failures or growth beyond the frozen retry capacity.

## WUZAPI unavailable

- Signal: dispatch failures or durable OUTBOUND_OUTCOME_UNKNOWN checkpoints.
- Evidence: correlation report and Platform final-notification Executions.
- First action: verify process/configuration health without sending a message.
- Safe recovery: preserve durable ambiguity and use the frozen reconciliation/recovery behavior.
- Prohibited: blind resend after a durable DISPATCHED checkpoint or manual ACK creation.
- Escalate: sustained UNKNOWN growth or confirmed credential/configuration failure.

## Platform database unavailable

- Signal: reporting connection failure, worker/API database error, or stalled durable progress.
- Evidence: process health and sanitized connection-error category; no fallback database is permitted.
- First action: stop new mutation paths safely and verify the authorized database identity/connectivity.
- Safe recovery: restore connectivity, then allow frozen workers/recovery paths to resume.
- Prohibited: schema repair, migration, direct status mutation, restore over the existing database.
- Escalate: corruption, identity ambiguity, or failed integrity checks.

## Writer database unavailable

- Signal: PERSIST_RETRYABLE or PERSIST_OUTCOME_UNKNOWN backlog.
- Evidence: `blocked`, `failures`, and correlation reports, including Writer ledger when configured.
- First action: distinguish known retryable failure from ambiguous outcome.
- Safe recovery: restore Writer availability and use only the frozen retry/reconciliation path.
- Prohibited: blind POST replay for an unknown outcome or direct ledger/financial edits.
- Escalate: growing ambiguity backlog or contradictory Writer/Platform ledgers.

## PERSIST_RETRYABLE backlog

- Signal: elevated count/age in `blocked`.
- Evidence: bounded item attempt counts, next-attempt timestamps, and component error codes.
- First action: identify the unavailable dependency or recurring bounded error.
- Safe recovery: restore the dependency and allow the existing bounded retry schedule.
- Prohibited: reset attempts, rewrite next-attempt timestamps, or force completion.
- Escalate: maximum-attempt growth or persistent dependency failure.

## PERSIST_OUTCOME_UNKNOWN backlog

- Signal: elevated count/age in `blocked`.
- Evidence: correlation chain through persistence Executions and Writer ledger.
- First action: query the frozen reconciliation evidence by stable processing identity.
- Safe recovery: invoke only the existing reconciliation workflow under its own authorization.
- Prohibited: blind Writer POST, manual COMMITTED/REJECTED state, or data repair SQL.
- Escalate: missing/contradictory durable evidence.

## Final-notification OUTBOUND_OUTCOME_UNKNOWN

- Signal: final-notification UNKNOWN count/checkpoint.
- Evidence: matching RESERVED, DISPATCHED, and terminal final-key Executions.
- First action: preserve the one-attempt durable identity and inspect sanitized transport evidence.
- Safe recovery: none in Gate 9; leave the result unknown for authorized follow-up.
- Prohibited: blind WhatsApp resend or manual ACK.
- Escalate: growing UNKNOWN volume or transport configuration failure.

## FIFO or enterprise-command stall

- Signal: oldest blocked item/session age increases without durable progress.
- Evidence: `blocked` and correlation reports for the item, interaction, answer, command barrier, and lease checkpoints.
- First action: distinguish a live wait/barrier from an expired lease or recoverable checkpoint.
- Safe recovery: use existing sweeper/recovery behavior only.
- Prohibited: direct sequence/status edits, deleting interactions, or bypassing the conversation barrier.
- Escalate: contradictory ownership/lease evidence or cross-conversation blocking.

## Token or cost spike

- Signal: unexpected attempt, token, or known-cost increase.
- Evidence: `tokens-document`, `tokens-organization`, and `service-usage` grouped by provider/model/status/pricing version.
- First action: identify retries, failures, model changes, and PARTIAL totals.
- Safe recovery: correct the separately authorized upstream cause; preserve the attempt ledger.
- Prohibited: delete/reprice attempts, coerce unknown tokens to zero, or populate Platform `service_usage`.
- Escalate: unexplained retries, provider/model change, or incomplete pricing evidence.

## Backup failure

- Signal: nonzero sanitized exit code or missing/invalid artifact group.
- Evidence: exit category, owned marker, complete dump/checksum/manifest group, hash, and `pg_restore --list` result.
- First action: preserve the last valid complete group and verify disposable identity/tool versions.
- Safe recovery: correct the precondition and rerun against a new group identity.
- Prohibited: overwrite the last valid artifact, delete unknown/partial/unowned files, or use a persistent source.
- Escalate: ownership/containment ambiguity, checksum mismatch, or PostgreSQL incompatibility.

## Restore failure

- Signal: nonzero sanitized exit or `SANITIZED_MANUAL_CLEANUP_REQUIRED`.
- Evidence: invocation sidecar, exact generated target name, owner, database comment marker, and sanitized validation stage.
- First action: preserve evidence and determine whether all ownership values still match.
- Safe recovery: automatic cleanup only when immediate full revalidation succeeds; otherwise obtain separate manual-cleanup authorization.
- Prohibited: pattern-based DROP, dropping a pre-existing/unknown target, restoring over source/persistent data, or using raw credentials in diagnostics.
- Escalate: cleanup refusal, identity mismatch, physical restore/integrity failure, or any non-disposable target evidence.
