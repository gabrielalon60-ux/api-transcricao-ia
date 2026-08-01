# Agent Execution Protocol — DF Holding WhatsApp Platform

## 1. Purpose

This project is specification-driven, security-sensitive, and Gate-controlled.

Use these documents together:

- `PRD.md`: approved product behavior, business rules, architecture invariants, security requirements, constraints and TBDs.
- `IMPLEMENTATION_PLAN.md`: implementation order, dependencies, phases and Gate boundaries.
- `TASKS_TESTS_GATES.md`: operational source of truth for tasks, tests and Gate readiness.
- `CURRENT_STATE.md`: convenience summary only; never more authoritative than the tracker.

---

## 2. Source-of-truth priority

Use this precedence:

1. Explicit user instructions in the current interaction.
2. Approved business/security rules in `PRD.md`.
3. Gate requirements in `TASKS_TESTS_GATES.md`.
4. Guidance in `IMPLEMENTATION_PLAN.md`.
5. Existing repository implementation.

Existing code is not automatically correct when it conflicts with approved specifications.

If documents, code, or a new request conflict:

1. stop the conflicting work;
2. identify the exact conflict;
3. identify affected files/components/contracts;
4. explain impact of each interpretation;
5. recommend a resolution;
6. wait for approval when behavior, architecture, security, contracts, data, or Gate scope would change.

Never silently resolve material ambiguity.

---

## 3. Start-of-session protocol

At the start of a new agent session:

1. read `.agents/rules/project-governance.md`;
2. read `.agents/CURRENT_STATE.md`;
3. inspect the current Gate in `.agents/TASKS_TESTS_GATES.md`;
4. read relevant sections of `.agents/PRD.md` and `.agents/IMPLEMENTATION_PLAN.md`;
5. inspect Git branch/status;
6. inspect recent project knowledge/progress if available;
7. identify the user's authorized scope;
8. resolve ambiguity before material changes.

Never infer project progress only from existing code.

Tracker + actual test evidence determines progress.

---

## 4. Specification before code

Before large repository reads or implementation:

1. identify the current Gate;
2. identify exact Task IDs in scope;
3. read corresponding Gate tests;
4. read the relevant PRD rules;
5. read the relevant Plan phase;
6. identify earlier approved Gates that could regress.

When Graphify or another project knowledge index is available:

- query it before broad repository reads;
- read source files when project knowledge is insufficient or implementation details need verification;
- do not repeatedly scan the whole repository without a concrete reason.

---

## 5. Gate discipline

When Gate N is active:

DO:
- implement authorized P0/P1 tasks for Gate N;
- add/run tests required by Gate N;
- make minimal prerequisite changes required for the current Gate;
- rerun relevant regressions from earlier approved Gates.

DO NOT:
- implement future-Gate features merely because they are convenient;
- redesign architecture without approval;
- introduce speculative infrastructure;
- silently broaden scope.

A Gate can be technically ready only when:

- required P0 tasks are complete;
- required executable tests pass;
- no unresolved blocker affects the Gate;
- documentation matches reality;
- evidence exists.

The agent never approves a Gate.

When technically ready, report:

`READY FOR USER APPROVAL`

Do not start the next Gate until the user explicitly authorizes it.

---

## 6. Task/test status

Allowed task statuses:

- TODO
- IN_PROGRESS
- BLOCKED
- DONE
- DEFERRED

A task becomes DONE only when implementation exists and required validation actually ran and passed.

A test becomes PASS only when it actually ran successfully.

Never fabricate execution.

If a test cannot run:

`BLOCKED — <reason>`

Code written but not validated is not DONE.

---

## 7. Tracker and state maintenance

After every implementation interaction, update `TASKS_TESTS_GATES.md` when objective status changes.

Rules:

- preserve IDs;
- never renumber existing tasks/tests;
- never delete failed tests;
- never mark PASS without execution;
- add newly discovered required work explicitly;
- do not silently perform significant untracked work.

`CURRENT_STATE.md` is a summary.

If it conflicts with the tracker:

1. tracker wins;
2. report the inconsistency;
3. repair CURRENT_STATE.

---

## 8. Change control

### PRD

Do not change approved business behavior merely to simplify implementation.

When a business-rule change appears necessary:

1. show current rule;
2. explain the problem;
3. propose replacement;
4. explain behavioral/security/data consequences;
5. wait for user approval;
6. then update PRD, Plan and Tracker as required.

### Implementation Plan

May be refined as technical knowledge improves only if approved behavior/security is preserved.

Requires user approval:
- service merge/split;
- queue authority replacement;
- routing-model changes;
- database credential ownership changes;
- FIFO/conversation behavior changes;
- materially different persistence architecture;
- significant external infrastructure not in the Plan.

### New requirements

Classify as:
A. clarification;
B. change;
C. current-Gate scope;
D. future-Gate scope;
E. conflict.

Report impact on PRD, Gate, tasks, tests, contracts, schema and security.

Do not implement a material new requirement before it is sufficiently specified.

---

## 9. Architecture invariants

Unless the PRD is explicitly changed:

- WUZAPI = transport.
- Orchestrator = routing, identity, ingress/egress control.
- BOT DF Holding = business rules and workflow coordination.
- Transcription Service = document extraction.
- Database Writer = persistence in the DF database.
- Platform PostgreSQL = platform state, routing/configuration, persistent FIFO queue, audit and usage.

Do not move DF business rules into WUZAPI, Orchestrator, or Transcription Service.

Do not give DF database credentials to Orchestrator, BOT, or Transcription Service.

---

## 10. Queue invariants

Preserve:

- FIFO business order by receive sequence;
- extraction may execute concurrently within configured limits;
- original media does not wait in the business queue;
- only extracted/normalized data waits;
- business processing is sequential per conversation;
- maximum one active interaction per conversation;
- one conversation never blocks another;
- queue state survives restart;
- failed/expired items do not block later items;
- overflow is rejected before unnecessary AI cost;
- webhook replay does not create duplicate business execution.

Do not change these rules without approval.

---

## 11. Security and data safety

Never weaken security to make implementation easier.

Core invariants:

- no secrets in Git;
- no secrets in logs;
- authenticated WUZAPI webhook;
- authenticated service-to-service calls;
- Database Writer alone owns DF database credentials;
- temporary media removed after processing;
- no binary/base64 media in logs;
- validation before costly processing;
- rate limits where required;
- idempotent webhook/write operations;
- TLS for database connections;
- least-privilege DB credentials;
- separate secrets per environment;
- internal services remain private unless exposure is explicitly required.

Never without explicit user authorization:

- reset a real database;
- delete production data;
- remove production persistent volumes;
- overwrite/rotate production secrets;
- run destructive production migrations;
- recreate WUZAPI persistent session data;
- force-reset Git history.

When a security issue is discovered:

1. classify severity;
2. explain impact without exposing the secret;
3. identify affected Gate;
4. propose remediation;
5. add task/test when appropriate.

---

## 12. Testing and regression

Before implementing a Gate, read its required tests.

During implementation:
- add unit tests;
- add integration tests;
- add concurrency/race tests where relevant;
- add regression tests for defects found.

Before requesting Gate approval:
- run the relevant Gate suite;
- rerun earlier-Gate tests affected by the change;
- report anything not executed.

An approved Gate must remain valid.

A new Gate cannot be ready if it breaks an earlier approved Gate.

---

## 13. Code quality and proportionality

Prefer:
- explicit types;
- small cohesive modules;
- clear service boundaries;
- deterministic behavior;
- idempotent operations;
- structured errors/logs;
- versioned contracts;
- explicit timeout/retry behavior;
- testability.

Avoid:
- hidden global state;
- catch-all exceptions;
- duplicated business rules;
- magic values;
- undocumented side effects;
- embedded credentials;
- speculative complexity.

Do not introduce Kafka, RabbitMQ, Kubernetes, external secret managers, or equivalent complexity unless the approved Plan requires them or the user approves a justified change.

The initial queue authority is Platform PostgreSQL.

---

## 14. Documentation ownership

### PRD.md
Update only for approved requirement clarification/change or approved architectural/security decisions.

### IMPLEMENTATION_PLAN.md
May be technically refined while preserving approved behavior/security. Material architecture/Gate changes need approval.

### TASKS_TESTS_GATES.md
Live operational source of truth. Reflect reality, not intent.

### CURRENT_STATE.md
Convenience summary. Must match the tracker.

Documentation is part of implementation. Do not knowingly leave docs inconsistent with code.

---

## 15. End-of-interaction protocol

Before ending implementation/debug/test/review work:

- know Git branch/status;
- update Tracker when justified;
- update CURRENT_STATE;
- ensure docs match implementation;
- report uncommitted files;
- report tests not executed;
- report blockers;
- identify exactly one recommended next action.

Do not assume permission to commit/push unless authorized.

---

## 16. Required Execution Report

After every interaction involving inspection, implementation, modification, testing, migration, debugging, or review, return:

### 1. Scope
- Gate:
- Task IDs:
- Objective:

### 2. What I changed
For each important change:
- component;
- file(s);
- behavior affected.

### 3. What I did not change
Important adjacent areas intentionally left untouched.

### 4. Tests executed
Use:

`[PASS|FAIL|BLOCKED] test/command — result`

Never report PASS unless it actually ran.

### 5. Task status
Examples:

`G2-T07: TODO → DONE`

`G2-T13: TODO → BLOCKED`

`G2-X04: PASS`

### 6. Security review
Report:
- security-sensitive changes;
- secrets exposure: YES/NO;
- new public endpoints: YES/NO;
- database privilege changes: YES/NO;
- unresolved security risks.

If none:

`No new security risks identified in this interaction.`

### 7. Architecture / PRD compliance
- PRD rules affected;
- deviations: NONE or list;
- architecture changes: NONE or list.

### 8. Files changed
Added, modified and deleted files.

### 9. Open issues / blockers
If none:

`None.`

### 10. Gate status
Use exactly one:

`NOT READY`

`BLOCKED`

`READY FOR USER APPROVAL`

`ALREADY APPROVED — REGRESSION CHECK PASSED`

Explain why briefly.

### 11. Recommended next action
Recommend exactly one logical next task or user decision.

Do not automatically start it unless it is already inside the authorized scope.

---

## 17. Completion claims

Never claim “100% done”, “production ready”, “secure”, “Gate complete” or “all tests pass” unless evidence supports the exact statement.

Prefer:

- `All currently executable Gate 4 P0 tests passed.`
- `Gate 4 is ready for user approval.`
- `G4-X07 remains blocked because ...`
- `Production Security Gate 10 has not been executed.`
