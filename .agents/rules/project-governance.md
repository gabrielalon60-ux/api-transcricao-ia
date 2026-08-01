---
trigger: always_on
---

# Project Governance — Always On

This workspace is specification-driven, security-sensitive, and Gate-controlled.

Always follow:

@../AGENT_PROTOCOL.md
@../CURRENT_STATE.md

Authoritative project documents:

- `.agents/PRD.md`
- `.agents/IMPLEMENTATION_PLAN.md`
- `.agents/TASKS_TESTS_GATES.md`

## Mandatory behavior

Before material project work:

1. Identify the current Gate and the user's authorized scope.
2. Identify the exact Task IDs involved.
3. Read the relevant PRD rules, Implementation Plan phase, and Gate tests.
4. Query Graphify/project knowledge before broad repository reads when available.
5. Inspect only source files needed to verify or implement the task.
6. Do not work on a future Gate unless the user explicitly authorizes it.

After implementation, debugging, migration, testing, or review:

1. Run the relevant tests and regressions.
2. Update `.agents/TASKS_TESTS_GATES.md` only when objective status changed.
3. Update `.agents/CURRENT_STATE.md` so it matches the tracker.
4. Return the structured Execution Report defined in `AGENT_PROTOCOL.md`.

## Authority

Priority order:

1. Explicit user instruction in the current interaction.
2. Approved PRD business/security rules.
3. Gate requirements in TASKS_TESTS_GATES.
4. Implementation Plan guidance.
5. Existing code.

Existing code is not authoritative when it conflicts with approved specifications.

## Gate rule

Never approve a Gate on behalf of the user.

When all required work and executable tests are complete, report:

`READY FOR USER APPROVAL`

Do not start the next Gate until the user explicitly authorizes it.

## Safety

Never expose or commit secrets.
Never perform destructive production operations without explicit authorization.
Never weaken security requirements to make implementation easier.
