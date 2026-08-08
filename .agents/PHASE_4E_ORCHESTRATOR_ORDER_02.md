# Phase 4E Closure — Orchestrator Corrective Order 02

## Objective

Correct the unsupported claims in the previous Phase 4E report and close, or explicitly prove unresolvable within authorized scope, the real runtime-integration gaps for heartbeat/sweepers, prompt dispatch, inbound answers, and `/cancelar`. Produce auditable execution evidence; do not self-approve Phase 4E.

## Governance baseline that must remain unchanged

- Phases 4A–4D and HOLD 4: APPROVED.
- Phase 4E and migration `9e0a1b2c3d5e`: UNDER REVIEW / NOT APPROVED.
- HOLD 5: NOT REACHED.
- Phase 4F and Database Writer: NOT AUTHORIZED.
- `PERSISTING`, `PERSIST_RETRYABLE`, and `PERSIST_OUTCOME_UNKNOWN`: NOT AUTHORIZED for implementation or runtime entry.
- Persistent, staging, production, Supabase, and remote databases/resources: NOT AUTHORIZED.
- Commit, push, and pull request creation: NOT AUTHORIZED.

## Review findings that must be addressed

These are observed repository facts, not optional suggestions:

1. `apps/orchestrator/src/orchestrator/main.py::lifespan` only calls `setup_logging()` and yields; it starts no Phase 4E runtime task.
2. `apps/orchestrator/src/orchestrator/fifo_worker.py::run_fifo_worker_loop` exists, but the current `docker-compose.yml` starts only `uvicorn orchestrator.main:app`; no reviewed production/local service entry starts the FIFO worker loop.
3. The Phase 4E unit file contains six parser/config/identity tests and no lifecycle/loop test.
4. The Phase 4E PostgreSQL file contains twelve test functions total (migration matrix plus `test_1` through `test_11`), but none executes `run_fifo_worker_loop`, application lifespan startup/shutdown, repeated periodic iterations, or failure continuation.
5. Repository search finds production definition of `dispatch_user_prompt` but callers only in Phase 4E tests. The previous statement that it is invoked from `fifo_worker.py` or an HTTP clarification path is not supported by the code.
6. `dispatch_user_prompt(..., prompt_sender_func=None)` currently treats the missing sender as acknowledged success. This is test-default behavior, not real WUZAPI runtime integration, and must not be reachable as a production success path.
7. `WorkerClaimTracker.renew_all_heartbeats` catches per-item exceptions but reuses the same SQLAlchemy session without an explicit rollback. Prove that one database error cannot poison the session and prevent later claims from renewing, or fix the isolation.
8. The submitted command history does not contain a Phase 4E unit-test invocation or the claimed 19-test unit regression invocation. It also omits command outputs and exit codes. Those PASS claims are therefore not accepted.
9. The report omitted the required acceptance-criterion evidence table and omitted residual blockers while claiming none.
10. `.agents/TASKS_TESTS_GATES.md` contains contradictory duplicate Gate 4 status lines (`Phase 4E ... PENDING REVIEW` and later `Phase 4E: NOT STARTED`; approved and not-yet-approved HOLD 4). Reconcile without erasing relevant history and without marking Phase 4E approved.

## Required actions

### A. Establish the actual runtime boundary

Choose and implement the smallest architecture-consistent runtime entry that is genuinely launched by the project: either supervised tasks in the Orchestrator lifespan or an explicit separately launched worker service/entrypoint. Document why the chosen boundary matches the approved architecture. It must start heartbeat, stale-recovery, and waiting-input-expiration work; use short-lived sessions; isolate iteration failures; and stop deterministically without task/session leakage.

Do not merely leave a callable `if __name__ == '__main__'` module that no declared runtime starts.

### B. Prove the lifecycle

Add focused tests that invoke the real chosen runtime boundary and prove:

- startup validation and startup scan;
- at least two heartbeat/sweeper iterations under controlled intervals;
- one injected DB/session failure does not prevent a later iteration or another owned claim from succeeding;
- ownership loss removes the claim and an expired lease is not revived;
- deterministic graceful shutdown and resource cleanup.

Direct service-function tests alone are insufficient.

### C. Close prompt runtime integration or report the exact scope conflict

Trace the real `READY -> ACTIVE -> VALIDATING` path. Connect `dispatch_user_prompt` to a production caller and a real WUZAPI adapter only if the question type and prompt decision are already available under approved Phase 4E scope. Never treat an absent sender as acknowledged in production.

If deciding that a prompt is necessary requires unauthorized Gate 5 business rules, do not invent those rules. Instead:

- implement only a safe integration seam that consumes an already-determined `question_type`, if possible;
- identify the exact missing producer/contract and affected files;
- classify the result `BLOCKED — PHASE 4E RUNTIME INTEGRATION CONFLICT` if a real end-to-end caller cannot exist without entering an unauthorized phase.

### D. Prove actual inbound HTTP routing

Add route-level tests through the real FastAPI application/dependency overrides for:

- answer while a matching conversation item is `WAITING_USER_INPUT`;
- duplicate delivery/replay of the same inbound external message;
- a second distinct late answer;
- `/cancelar` with and without a waiting item;
- ordinary text with no waiting item;
- zero processing-item/sequence allocation and zero extraction call for all text-answer/cancel paths.

Service-level calls alone do not prove routing. Use local fakes for WUZAPI and no remote calls.

### E. Reconcile answer idempotency precisely

State and test the canonical key at each layer: WUZAPI external-message identity, `events` identity, and `user_answers.inbound_event_id`. Demonstrate whether a replay is short-circuited at event ingestion or reaches the answer savepoint path. Remove contradictory claims and prove committed-outcome replay without double application/checkpoints.

### F. Execute and preserve evidence

Use only newly created local disposable PostgreSQL 15-compatible infrastructure for PostgreSQL-specific migration, constraint, savepoint, `ON CONFLICT`, and race behavior. Remove it afterward. Run:

- focused Phase 4E unit/runtime tests;
- focused Phase 4E disposable-PostgreSQL tests;
- affected route tests;
- all Gate 4 Phase 4A–4E unit and disposable-PostgreSQL regressions;
- compileall, Ruff, and mypy for changed Phase 4E Python files.

Run a final non-mutating Ruff check after any `--fix`. Do not count skipped/not-collected tests as proof.

## Prohibited actions

- No Phase 4F, Database Writer, or persistence-state implementation.
- No Gate 5 business-rule invention or implementation.
- No persistent/remote database or external WUZAPI call.
- No commit, push, PR, phase approval, or HOLD advancement.
- Do not edit approved Phase 4A–4D migration sources.
- Do not hide or delete failed tests or relevant governance history.

## Required report

Return the 11-section report from `.agents/AGENT_PROTOCOL.md` and include:

1. exact commands, complete summary lines, and exit codes;
2. collected/passed/failed/error/xfail/skipped counts per command;
3. sanitized disposable PostgreSQL identifiers plus creation, version, migration, and removal evidence;
4. an evidence table mapping every item A–F above to exact test names and production symbols;
5. observed call graph for runtime startup and prompt dispatch;
6. `git status --short`, diff summary, and files changed during this corrective order distinguished from pre-existing changes;
7. explicit remaining blockers and unexecuted validations.

End with exactly one classification:

- `BLOCKED — PHASE 4E RUNTIME INTEGRATION CONFLICT`
- `IMPLEMENTATION INCOMPLETE`
- `READY FOR PHASE 4E APPROVAL`

Use `READY FOR PHASE 4E APPROVAL` only if every runtime and evidence requirement is directly demonstrated and no unauthorized Gate 5 decision is needed. Stop after the report and await review.
