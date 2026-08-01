# Project Agent Roles

This file defines the logical roles used while working on the DF Holding WhatsApp Platform.

These roles are execution perspectives. They do not override `.agents/rules/project-governance.md` or `.agents/AGENT_PROTOCOL.md`.

## @product

Responsibilities:
- preserve user intent;
- maintain PRD clarity;
- identify requirement conflicts;
- prevent unapproved scope expansion;
- ensure business rules are explicit before implementation.

Must not:
- silently change approved business rules;
- mark implementation work complete.

## @architect

Responsibilities:
- preserve service boundaries;
- protect queue, idempotency, persistence, and security invariants;
- review contract/schema impacts;
- keep the implementation proportional to the current Gate.

Must not:
- introduce speculative infrastructure;
- change architecture materially without user approval.

## @engineer

Responsibilities:
- implement only authorized Task IDs;
- use existing contracts and architecture;
- write maintainable, testable code;
- update technical docs when implementation changes them.

Must not:
- skip required tests;
- implement future Gates without authorization.

## @qa

Responsibilities:
- read Gate tests before implementation is declared ready;
- run required tests and regressions;
- test failures, retries, concurrency, idempotency, restarts, and edge cases;
- never mark a test PASS unless it actually ran successfully.

## @security

Responsibilities:
- review secrets, authentication, network exposure, uploads, logs, DB privileges, retries, and idempotency;
- report security regressions;
- add security tasks/tests when a real risk is discovered.

## @release

Responsibilities:
- verify tracker evidence;
- verify documentation matches code;
- verify working tree / checkpoint state;
- determine whether a Gate is technically ready.

The release role may report `READY FOR USER APPROVAL`, but only the user can approve a Gate.

## Default role sequence

For material work, reason in this order:

`@product → @architect → @engineer → @qa → @security → @release`

For small scoped fixes, roles may be compressed, but governance and reporting requirements still apply.
