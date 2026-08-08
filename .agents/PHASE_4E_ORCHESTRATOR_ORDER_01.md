# Phase 4E Closure — Orchestrator Order 01

## Objective

Close only the Phase 4E runtime lifecycle gap for the business worker heartbeat and the local stale/expiration sweepers, and produce execution evidence. Do not broaden this order to the remaining prompt/answer closure items; those will be reviewed in later cycles.

## Governance baseline that must remain unchanged

- Phase 4A: APPROVED.
- Phase 4B: APPROVED.
- Phase 4C: APPROVED.
- Phase 4D: APPROVED.
- HOLD 4: APPROVED.
- Phase 4E: NOT APPROVED; implementation remains under review.
- Migration `9e0a1b2c3d5e`: UNDER REVIEW.
- HOLD 5: NOT REACHED.
- Phase 4F: NOT AUTHORIZED.
- Persistent, staging, production, and remote database execution: NOT AUTHORIZED.
- Commit, push, and pull request creation: NOT AUTHORIZED.

The Antigravity agent has no approval authority. Do not mark Phase 4E approved or ready solely because this order completes.

## Context and observed gap

The current `apps/orchestrator/src/orchestrator/main.py` lifespan only calls logging setup and yields. Phase 4E service modules exist, including heartbeat, stale recovery, waiting-input expiration, and worker tracking, but a real application-runtime loop and its shutdown behavior have not been demonstrated. Existing PostgreSQL tests exercise service functions directly; that is not evidence that the application lifecycle invokes them.

## In scope

- `apps/orchestrator/src/orchestrator/main.py`
- `apps/orchestrator/src/orchestrator/config.py`
- Phase 4E heartbeat/recovery/sweeper services under `apps/orchestrator/src/orchestrator/services/`
- `apps/orchestrator/src/orchestrator/services/fifo_worker_service.py` only where required to expose or track currently owned business items for heartbeats
- Phase 4E-focused tests needed to prove runtime lifecycle behavior
- `.agents/TASKS_TESTS_GATES.md` and `.agents/CURRENT_STATE.md` only if objective evidence changes their status; reconcile the currently duplicated/stale Gate 4 status lines without claiming approval

## Required actions

1. Inspect the existing Phase 4E service APIs and establish the smallest production runtime wiring that:
   - validates heartbeat, lease, stale-sweeper, and waiting-input TTL/interval configuration at startup;
   - starts an actual recurring heartbeat task for all business items currently owned by this process while they remain in lease-bearing Phase 4E states;
   - starts recurring local stale-recovery and waiting-input-expiration sweeps;
   - uses fresh, short-lived database sessions per iteration and does not hold a transaction while sleeping or during external I/O;
   - survives one iteration failure by rolling back/closing that session, emitting a sanitized log, and continuing later iterations;
   - stops deterministically on FastAPI lifespan shutdown, cancels/awaits background tasks, closes resources, and does not leak tasks or sessions.
2. Preserve the existing FIFO claim and state-machine invariants. Heartbeat renewal must stop immediately for an item when ownership is lost, its lease expires, or it leaves an eligible state.
3. Add focused tests that run the real application lifespan/background loops with controlled intervals and faked/local disposable dependencies. Tests must prove startup, repeated execution, failure isolation/continuation, and graceful shutdown—not merely call the service functions directly.
4. Run the focused Phase 4E unit/runtime tests. If PostgreSQL semantics are involved in a changed path, also run the applicable Phase 4E suite against local disposable PostgreSQL compatible with the project. Do not treat a skip as evidence.
5. Run proportionate regression checks for earlier approved Gate 4 phases affected by runtime wiring.
6. Inspect the final diff and ensure no unrelated behavior or future-phase code was introduced.

## Explicitly prohibited

- Do not implement Phase 4F or Database Writer behavior.
- Do not enter, dispatch, recover, or otherwise implement the `PERSISTING`, `PERSIST_RETRYABLE`, or `PERSIST_OUTCOME_UNKNOWN` flow.
- Do not access any persistent, staging, production, Supabase, or remote database/resource.
- Do not run migrations outside a newly created local disposable PostgreSQL instance.
- Do not alter approved migrations from Phases 4A–4D.
- Do not commit, push, open a PR, or approve any phase/hold.
- Do not claim that prompt sending, inbound answers, `/cancelar`, the response-idempotency strategy, or the full PostgreSQL race matrix are closed by this order unless directly and separately demonstrated; they remain outside this cycle.

## Acceptance criteria

- A real FastAPI lifespan starts and stops the Phase 4E heartbeat and sweep loops.
- Runtime tests demonstrate at least two iterations where meaningful, continued operation after an injected iteration failure, and clean shutdown with no live task/session leak.
- Heartbeats cover process-owned eligible items and reject/stop lost ownership without reviving expired leases.
- Sweepers run on startup and periodically using local configuration.
- All executed focused tests pass with zero Phase 4E skips; any unexecuted or skipped test is explicitly reported and is not counted as proof.
- Earlier approved behavior touched by the change has proportionate passing regressions.
- No prohibited state, external environment, commit, or Phase 4F component is touched.

## Required report format

Return the 11-section Execution Report required by `.agents/AGENT_PROTOCOL.md`, plus:

1. exact commands and exit codes;
2. test counts, failures, errors, xfails, and skips;
3. whether PostgreSQL was used, with only sanitized local host/port/database/container identifiers and proof it was disposable and removed afterward;
4. a per-acceptance-criterion evidence table linking each criterion to the test name and relevant file/symbol;
5. `git status --short` and a concise diff summary;
6. all residual Phase 4E blockers, explicitly retaining prompt runtime integration, inbound answer/`/cancelar` routing, response idempotency reconciliation, PostgreSQL race matrix, and full regression unless separately evidenced.

End the report with exactly one orchestration classification from this set:

- `BLOCKED — PHASE 4E RUNTIME INTEGRATION CONFLICT`
- `IMPLEMENTATION INCOMPLETE`
- `READY FOR PHASE 4E APPROVAL`

For this bounded order, normally use `IMPLEMENTATION INCOMPLETE` even if its acceptance criteria pass, because other Phase 4E closure cycles remain. Do not begin another cycle without a new order.
