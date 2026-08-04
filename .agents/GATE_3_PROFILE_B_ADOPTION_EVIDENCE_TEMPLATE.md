# Gate 3 — Profile B Adoption Evidence Template

This template is for a future preserved-database adoption execution. Do not fill it in as completed before execution.

## 1. Operator and timing

- Operator:
- Reviewer:
- Date:
- Start time:
- End time:
- Timezone:
- Maintenance window ID:

## 2. Source state

- Git commit:
- Git branch:
- `git status --short` summary:
- Exact reconciliation source commit:
- Migration head:
- Baseline revision:
- Gate 3 revision:
- Runbook version/path:

## 3. Preserved database identity

- Source environment-variable name:
- Sanitized URL:
- Identity assertion script/result:
- Host:
- Port:
- Database:
- Current schema:
- Current user:
- PostgreSQL version:
- Docker container, if applicable:
- Docker volume, if applicable:
- HOLD 1 confirmation:

## 4. Worktree classification

- Approved migration-source scope files:
- Approved ORM scope files:
- Premature Gate 3 code excluded:
- Unrelated/pre-existing changes:
- Generated artifacts:
- Tracker/documentation:

## 5. Quiescence evidence

- Transcription writers stopped/scaled to zero:
- Active extraction requests:
- Bot DF invocation path disabled/paused:
- Background workers confirmed inactive:
- Startup migration/create_all hooks absent:
- Maintenance window start recorded:
- Maintenance window end recorded:
- Automatic restarts disabled:
- Development processes/tests/manual scripts blocked:
- HOLD 2 confirmation:

## 6. Pre-adoption inventory

### Row counts before

| Table | Count |
|---|---:|
| applications | |
| requests | |
| extractions | |
| usage_logs | |

### Request-status distribution before

| Status | Count |
|---|---:|
| PENDING | |
| PROCESSING | |
| COMPLETED | |
| SUCCEEDED | |
| FAILED | |
| PERSISTENCE_FAILED | |

### Integrity before

- Duplicate `(request_id, attempt_number)` pairs:
- Orphan requests:
- Orphan extractions:
- Orphan usage logs:
- Prompt non-null count:
- Application ID non-null count:
- Estimated-cost count/min/max/sum:
- `alembic_version_transcription` absent:
- Profile B verifier output:

## 7. Backup evidence

- Backup directory:
- Backup filename:
- Backup full path:
- Backup file size:
- SHA-256 checksum:
- `pg_restore --list` result:
- Archive includes four Transcription tables:
- Backup command exit code:
- HOLD 3 confirmation:

## 8. Restore rehearsal evidence

- Disposable restore host:
- Disposable restore port:
- Disposable restore database:
- Disposable restore container:
- Disposable restore volume:
- Restore command result:
- Restored Profile B verifier output:
- Restored row-count comparison:
- Restored constraint/enum/type comparison:
- Rehearsal cleanup confirmation:

## 9. Reconciliation approval

- Reviewed SQL version/source:
- Exact reconciliation source commit:
- Lock timeout:
- Statement timeout:
- Transaction owner confirmed as reconciliation source:
- Final pre-DDL Profile B verifier output:
- HOLD 4 confirmation:
- HOLD 5 confirmation:

## 10. Reconciliation result

- Start time:
- End time:
- Result:
- Error, if any:
- Transaction rollback verified, if failed:

## 11. Post-reconciliation verification

- Post-Gate-3 verifier output:
- Composite uniqueness present:
- Request-only uniqueness absent:
- `requests.application_id` nullable:
- `extractions.prompt` nullable:
- `usage_logs.estimated_cost NUMERIC(18,8)`:
- `usage_logs.attempt_number` NOT NULL with no default:
- Request enum ordered labels:

### Row counts after

| Table | Before | After | Match |
|---|---:|---:|---|
| applications | | | |
| requests | | | |
| extractions | | | |
| usage_logs | | | |

### Request-status distribution after

| Status | Before | After | Match |
|---|---:|---:|---|
| PENDING | | | |
| PROCESSING | | | |
| COMPLETED | | | |
| SUCCEEDED | | | |
| FAILED | | | |
| PERSISTENCE_FAILED | | | |

### Integrity after

- Duplicate `(request_id, attempt_number)` pairs:
- Orphan requests:
- Orphan extractions:
- Orphan usage logs:
- Prompt non-null count before/after:
- Application ID non-null count before/after:
- Estimated-cost count/min/max/sum before/after:
- Estimated-cost scale-8 rounded comparison:
- Numeric conversion representation notes:

## 12. Stamp evidence

- HOLD 6 confirmation:
- Stamp command:
- Stamp result:
- `alembic_version_transcription` value:
- Post-stamp verifier output:

## 13. Service resume

- HOLD 7 confirmation:
- Resume command/procedure:
- Resume result:
- Health/readiness result:
- First post-resume monitoring check:

## 14. Deviations

- Deviations from runbook:
- Reason:
- Approval:
- Impact:

## 15. Final sign-off

- Operator sign-off:
- Reviewer sign-off:
- Final status:
- Follow-up actions:
