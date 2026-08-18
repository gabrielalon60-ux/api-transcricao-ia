# Gate 10 Planning Package — Security and Release Candidate

> **Status**: G10-A IMPLEMENTED / LOCAL VERIFICATION PASSED / SECURITY AUDIT BLOCKED / CORRECTION DECISION REQUIRED
> **Baseline**: Gate 9 APPROVED / COMPLETE / PUSHED at `ef8551535f807fb231a841367cea8654cbed22a0`
> **Gate 10 implementation**: G10-A REPOSITORY HARDENING IMPLEMENTED; G10-B/G10-C NOT STARTED
> **G10-APPROVED**: false
> **VPS/staging/production access**: NOT AUTHORIZED
> **Production Phase B**: NOT IMPLEMENTED / NOT AUTHORIZED
> **Database execution in this pass**: NONE

## 1. Planning objective

Gate 10 is the security and release-candidate gate. It closes repository hardening, validates a production-equivalent staging topology, and only then prepares a separately authorized production release. This planning pass does not implement source, deployment, infrastructure, secret rotation, database adoption, staging, VPS, tagging, or release actions.

The tracker scope remains exactly G10-T01 through G10-T14, G10-X01 through G10-X18, and the nine release-checklist items. Every item remains unchecked until its own acceptance evidence exists.

## 2. Current repository baseline

### Existing controls that Gate 10 can reuse

- webhook HMAC validation, registration audit/rate limiting, replay/idempotency, and queue bounds from Gates 2 and 4;
- bounded upload reads, magic-byte/MIME validation, PDF/image limits, validation subprocess isolation, temporary-file cleanup, and AI concurrency limits from Gate 3;
- durable FIFO, lease/recovery, cross-replica race handling, and persistence idempotency from Gates 4–8;
- Database Writer constant-time internal auth, production default-token rejection, DF TLS `verify-full` enforcement, finite DB timeouts, and disposable least-privilege evidence from Gate 7;
- sanitized correlation, bounded operational reports, and guarded disposable backup/restore evidence from Gate 9;
- existing unit and disposable PostgreSQL coverage for most G10-X01 through X09 and X17 application invariants.

### Release gaps in the committed baseline

- `docker-compose.yml` is local/development-only: Platform PostgreSQL and every application service publish host ports; it has no private/public network split, no production edge, no container hardening, and placeholder/default credentials remain possible;
- the production topology described by the PRD (Traefik/HTTPS, restricted WUZAPI edge, private internal services and Platform DB) has no committed production Compose/Dokploy contract;
- `postgres:15`, `python:3.13-slim`, `pip install uv`, and `appleboy/ssh-action@v1.2.2` are tag-based rather than immutable digest/SHA pins;
- the Dockerfile runs as root, retains compiler/runtime packages in one stage, has no `no-new-privileges`/capability/read-only-root contract, and does not pin the `uv` installer;
- `.github/workflows/deploy.yml` deploys every push to `main` directly over SSH, with no environment approval, concurrency guard, immutable release artifact, preflight, health gate, or automatic stop on failed post-deploy verification beyond the remote script exit;
- `install.sh` and `update.sh` still reference the legacy `api-transcricao` service that is absent from the current Compose file; they are not valid release procedures for the multi-service platform;
- `.env.example` and Compose contain development placeholders/defaults and do not enumerate the complete current service-to-service, database, WUZAPI, edge, and release configuration contract;
- Orchestrator and Bot DF retain development/default token or identifier fallbacks that must fail closed outside explicit development/test modes;
- there is no committed dependency/image audit workflow, SBOM/vulnerability evidence format, production rollback runbook, staging release runbook, changelog, or release manifest;
- no current evidence proves external port exposure, SSH/firewall policy, WUZAPI admin restriction, deployed WUZAPI media retention, real production secrets, real DF identifiers, real DF schema/grants, or a recent staging restore.

The existing local Compose file must not be treated as a production template without an explicit Gate 10 design. The Gate 9 restore scripts are disposable-only verification tools and are not automatically authorized for staging or production restore.

## 3. Frozen release boundary

Gate 10 must use three separately authorized HOLDs:

### G10-A — Repository hardening HOLD

Local/repository changes only: production configuration contract, immutable image/action pins, container hardening, fail-closed production settings, security/audit tooling, release/rollback documentation, and deterministic tests. No remote system, real secret, real identifier, staging database, VPS, WUZAPI admin, DNS, firewall, SSH, tag, or release mutation.

### G10-B — Staging adoption and security-validation HOLD

Requires an approved staging inventory and test identities. It may provision or update only the explicitly named staging resources, run E2E/security/network/log/restore evidence, and exercise rollback. It must not touch production, client data, production WUZAPI identities, or production secrets.

### G10-C — Production release HOLD

Requires successful G10-A and G10-B evidence, closure of every external input below, approved DF production adoption, approved backup/restore and rollback evidence, and a final explicit user release authorization. Approval of planning, G10-A, or G10-B never authorizes G10-C.

## 4. Closed conservative decisions and deferred external inputs

### G10-D01 — Release data boundary — CLOSED CONSERVATIVE / BLOCKING G10-C

Gabriel owns the decision. G10-A must not infer an enterprise schema, migration, Writer grant, TLS identity, or rollback boundary. The private schema reference and adoption owner remain deferred inputs. Production Phase B and the release-checklist item `Schema DF aprovado` remain blocked until those protected references are separately supplied and approved.

### G10-D02 — Real DF identifiers — CLOSED CONSERVATIVE / BLOCKING G10-B AND G10-C PROVISIONING

Gabriel owns the identifiers. G10-A may implement only a dedicated, validated secret/config reference with digit-only normalization and fail-closed placeholder rejection outside explicit development/test. The actual values, counts, secret-store reference, and formal rotation procedure remain deferred and must never be pasted into Git, test logs, or ordinary chat.

### G10-D03 — Infrastructure inventory — CLOSED CONSERVATIVE / BLOCKING G10-B AND G10-C

Gabriel owns infrastructure. G10-A may prepare an environment-parameterized HTTPS/private-network contract, but it must not invent or access hosts, projects, domains, routes, volumes, backup destinations, or provider settings. The private staging/production inventory remains deferred.

### G10-D04 — SSH and firewall policy — CLOSED CONSERVATIVE / BLOCKING G10-B AND G10-C

Key-only SSH, disabled root/password login, a named non-root deploy identity, restricted source access, firewall enforcement, brute-force protection, and a documented break-glass procedure are mandatory. G10-A may document and statically validate this policy; the exact identity, allowlist/proxy, firewall, and emergency references remain deferred.

### G10-D05 — WUZAPI deployed contract — CLOSED CONSERVATIVE / BLOCKING G10-B AND G10-C

G10-A may require a pinned image, authenticated HTTPS webhook route, private/restricted admin surface, explicit signature contract, and shortest viable media retention, but it must not guess deployed WUZAPI details. Exact version/digest, signature format, routes, authentication, test identity, media path/retention, deletion evidence, and sanitized real fixture remain deferred.

### G10-D06 — Secret inventory and rotation — CLOSED CONSERVATIVE / BLOCKING G10-B AND G10-C

Gabriel owns and audits the secret inventory. Separate tokens per service boundary and new production values are mandatory. G10-A may define names, validation, inventory format, and rotation/revocation procedure only; it must not generate, inspect, print, provision, or rotate any real secret. The approved secret-store reference and complete inventory remain deferred.

### G10-D07 — Operational limits — CLOSED CONSERVATIVE / STAGING VALIDATION REQUIRED

The conservative initial values recorded in `.agents/GATE_10_DECISIONS_REQUIRED.md` are the G10-A configuration contract. Registration abuse control is five failed attempts in an anchored rolling 60-minute window for the durable `(organization_id, phone_number)` identity, followed by a 24-hour block; the block is checked before any window reset and successful registration resets the failure window. End-user IP is not available through the WhatsApp/WUZAPI transport; the separate public-edge per-IP limit applies to network callers.

Existing Gate 4 per-conversation intake capacity remains 10. Organization-wide outstanding capacity is 100 and counts exactly `RECEIVED`, `EXTRACTING`, `EXTRACTED`, `READY`, `ACTIVE`, `VALIDATING`, `WAITING_USER_INPUT`, `PERSISTING`, `PERSIST_RETRYABLE`, and `PERSIST_OUTCOME_UNKNOWN`. Organization-wide active-processing capacity is 20 and counts exactly the existing `BLOCKING_STATES`: `ACTIVE`, `VALIDATING`, `WAITING_USER_INPUT`, `PERSISTING`, `PERSIST_RETRYABLE`, and `PERSIST_OUTCOME_UNKNOWN`. Terminal outcomes, including capacity-rejected `FAILED`, do not consume either capacity.

Both organization limits are PostgreSQL race-safe without a migration: lock the durable `Organization` row, count and revalidate while that lock is held inside the same transaction, then allocate/claim. A saturated organization must be skipped through a bounded deterministic candidate loop so it cannot starve another organization's eligible FIFO work. The global oldest eligible order and every per-conversation FIFO/barrier invariant remain unchanged.

Provider-call concurrency is two per Transcription process. Every physical provider I/O attempt acquires one process-local permit immediately before calling the provider and releases it in `finally` immediately after the call returns or raises. The permit is never held during retry backoff, parsing, usage persistence, or other database work. Acquisition is bounded to two seconds. Failure before provider I/O maps to retryable `PROVIDER_CAPACITY_EXCEEDED`, performs no provider call, and creates zero attempt-level `usage_logs` rows because no real provider attempt occurred. These values remain release candidates, not production evidence, until bounded staging load/security validation confirms or revises them through a reviewed contract change.

### G10-D08 — Staging E2E inventory — CLOSED CONSERVATIVE / BLOCKING G10-B

Gabriel owns staging. G10-A may prepare deterministic local/disposable evidence and runbooks only. The staging project, isolated identities, databases, provider policy, test-data rules, and every disruptive/security exercise remain unavailable and unauthorized until a separate G10-B approval names the exact resources and actions.

### G10-D09 — Release and rollback authority — CLOSED CONSERVATIVE / BLOCKING G10-C

Gabriel is release owner and final go/no-go authority. G10-A may prepare `vMAJOR.MINOR.PATCH`, changelog, immutable-artifact, manual-approval, health/readiness, and rollback procedures. It must not tag or deploy. Maintenance window, previous-known-good identity, final DB compatibility policy, and confirmed rollback SLA remain deferred G10-C inputs; the target rollback time is at most 15 minutes after the decision.

## 5. Proposed repository architecture for G10-A

Subject to HOLD approval, G10-A should prepare:

- a dedicated production/staging Compose or Dokploy contract, separate from local development, with one public edge network and private internal/database networks;
- no host port for Platform PostgreSQL, Database Writer, Transcription, or Bot DF; only the approved edge/webhook/admin routes are published;
- immutable image digests and GitHub Action commit SHAs with a documented update procedure;
- a multi-stage, non-root runtime image with pinned tooling, minimal runtime packages, `no-new-privileges`, dropped capabilities, bounded tmpfs/writable paths, read-only root where compatible, resource limits, restart policy, and health/readiness checks;
- fail-closed production configuration with no placeholder secret, localhost URL, default token, placeholder CPF/CNPJ, or insecure DB fallback;
- complete environment-variable schema documentation using names/placeholders only; production secret values remain in the approved secret store;
- gated release workflow using an approved GitHub Environment or equivalent, serialized deployments, immutable commit/image identity, preflight, migration/adoption hold, post-deploy health, and rollback entrypoint;
- dependency and container audit commands with deterministic artifact/evidence output, plus full-history secret scanning;
- a fail-closed audit policy: scanner/tool execution is immutably pinned; unavailable, malformed, incomplete, stale, or nonzero scanner execution fails; confirmed Critical vulnerabilities admit no Gate 10 exception; High vulnerabilities block unless the user explicitly approves a verified mitigation for the exact immutable artifact with an expiry no longer than 30 days; Medium findings require a documented fix, accepted-risk disposition, or proven false-positive disposition before PASS; Low/Informational findings remain visible;
- any allowed High exception requires owner, vulnerability identifier, affected immutable artifact/version, reachability evidence, mitigation, explicit user approval, approval date, and expiry. Expiry, artifact drift, mitigation failure, or new exploit evidence automatically restores blocking status;
- confirmed secrets always block and require revocation/remediation evidence; they cannot receive vulnerability exceptions. Test-fixture exclusions require an exact path, rule/fingerprint, non-secret proof, reviewer, and reason—never a broad path, regex, or repository-wide allowlist;
- application limits that preserve the Gate 4 per-conversation cap while adding race-safe organization-wide pending/active bounds and a bounded provider-call semaphore;
- log sanitization at every active logging boundary, with raw provider output, raw phone numbers, credentials, DSNs, authorization values, and uncontrolled exception text prohibited from release logs;
- production/staging deployment, incident, backup/restore, and rollback runbooks that contain no real credential or destructive wildcard;
- release manifest/checklist, changelog, and tag procedure without creating the tag during G10-A;
- static and subprocess tests for topology, pinning, hardening, fail-closed configuration, secret absence, and runbook/script safety.

No production migration is inferred by this architecture. Any DF adoption belongs to the separately approved Production Phase B/G10-C boundary.

## 6. G10 task mapping

- **G10-T01**: dedicated validated real-identifier configuration; real provisioning remains blocked on the deferred G10-D02 protected input.
- **G10-T02**: inventory, generation/rotation procedure, fail-closed config, and authorized environment provisioning; real execution remains blocked on the deferred G10-D06 inventory and authorization.
- **G10-T03**: Traefik HTTPS route/certificate contract and staging evidence; physical evidence remains blocked on the deferred G10-D03 infrastructure inventory.
- **G10-T04**: private networks and zero host publication for internal services/DB.
- **G10-T05**: WUZAPI admin isolation/allowlist/auth; physical evidence remains blocked on the deferred G10-D05 deployed contract.
- **G10-T06**: SSH identity, allowlist/proxy, key-only access, non-root policy, and evidence; physical evidence remains blocked on the deferred G10-D04 environment references.
- **G10-T07**: layered edge and application limits under the exact D07 status, locking, anti-starvation, registration-window, and provider-permit contracts above. Values require staging validation before release.
- **G10-T08**: Python dependency, container OS/package, image vulnerability, and full-history secret audits under the frozen fail-closed severity/exception policy.
- **G10-T09**: immutable digest/SHA pins for base/runtime/service images and third-party actions.
- **G10-T10**: non-root/minimal/read-only/capability/resource/runtime hardening.
- **G10-T11**: physical WUZAPI media-retention verification; blocked on the deferred G10-D05 deployed contract and G10-B authorization.
- **G10-T12**: static log-call review plus runtime canary-secret/PII scan across staging services.
- **G10-T13**: recent staging backup and clean isolated restore with schema/count/identity/application smoke evidence; blocked on the deferred G10-D08 staging inventory and separate restore authorization.
- **G10-T14**: immutable previous-version rollback, DB compatibility boundary, procedure, timing, and physical staging exercise; documentation is allowed in G10-A, while physical exercise remains blocked on deferred G10-D09 inputs and G10-B authorization.

## 7. Security-test acceptance map

- **G10-X01**: reuse Gate 2 unsigned/invalid/altered webhook tests and add staging edge rejection without application side effect.
- **G10-X02**: exercise every service-to-service boundary with missing, malformed, oversized, wrong, and cross-service tokens; no token appears in response/logs.
- **G10-X03**: prove the durable `(organization_id, phone_number)` five-failure/one-hour/24-hour-block contract, concurrent failure serialization, successful-registration reset behavior, and separate edge per-IP blocking before expensive work.
- **G10-X04**: reuse Gate 3 bounded upload evidence and prove edge plus application rejection at/beyond exact limits.
- **G10-X05**: reuse magic-byte/MIME mismatch tests through the deployed edge.
- **G10-X06**: malformed JSON, metadata, multipart, headers, and WUZAPI payloads return bounded sanitized errors without durable business side effects.
- **G10-X07**: replay the same signed external message across process restart; one Event/business effect/final logical outcome.
- **G10-X08**: run existing cross-replica FIFO/interaction race suites and a staging concurrency scenario.
- **G10-X09**: run existing dispatch/Writer idempotency races and staging restart/retry evidence.
- **G10-X10**: inject unique non-production canary tokens/API keys/CPF/CNPJ/DSNs/phones/provider output, exercise success/failure paths through both shared and Transcription logging boundaries, collect bounded logs, and assert zero raw matches.
- **G10-X11**: full Git history and worktree scan with a pinned secret scanner; findings require explicit revocation/remediation, never silent allowlisting.
- **G10-X12**: external network scan proves Platform DB is unreachable; internal authorized probe proves only required network peers can connect.
- **G10-X13**: external scan proves Database Writer is unreachable; internal call requires the correct dedicated token.
- **G10-X14**: inspect actual role memberships and table/schema privileges; Writer lacks database/schema ownership and unauthorized DDL/DROP/ALTER while retaining only required read/write operations.
- **G10-X15**: success, rejection, timeout, crash, and restart leave no upload/validation/restore temporary residue outside approved owned directories.
- **G10-X16**: physical WUZAPI version/config/media lifecycle evidence matches the approved retention policy.
- **G10-X17**: abrupt stop at durable reservation/dispatch boundaries produces one eventual business record and no blind resend/duplicate.
- **G10-X18**: restore the latest approved staging backup into a newly generated isolated target, verify integrity and application smoke, then clean only invocation-owned resources.

Existing Gates 2–9 tests are supporting regression evidence, not substitutes for staging/network/WUZAPI/restore evidence where the acceptance explicitly requires the deployed topology.

## 8. Release checklist closure rules

- `Schema DF aprovado`: G10-D01 closed and Production Phase B adoption reviewed.
- `CPF/CNPJ reais`: G10-D02 securely provisioned and validated without disclosure.
- `E2E staging aprovado`: G10-B full happy/failure/restart evidence passed.
- `Segurança aprovada`: G10-X01–X17 passed with no open P0/P1 finding or undocumented exception.
- `Backup/restore aprovado`: G10-X18 passed on a recent staging backup.
- `Runbook operacional`: environment-specific deploy/incident/backup procedures reviewed.
- `Runbook rollback`: physically exercised staging rollback with recorded time/result.
- `Tag de release`: created only after final G10-C authorization from the exact approved commit/artifact.
- `Changelog`: describes product, schema, security, operational, and rollback impact without secrets.
- `G10-APPROVED / FASE 1 RELEASED`: only the user can approve after every item above is complete.

## 9. Verification tiers

### Tier 1 — static/local

Configuration schema tests, Compose rendering, Dockerfile/action pinning, container-policy assertions, compileall, Ruff, mypy, dependency lock consistency, secret scan, and documentation/script safety.

### Tier 2 — disposable integration

Full frozen Gates 2–9 regressions, PostgreSQL 15 security/role tests, container runtime hardening probes, network-isolation tests, abrupt restart/idempotency, and clean restore using only invocation-owned disposable resources.

### Tier 3 — staging physical

HTTPS/certificate, DNS route, firewall/ports, SSH restrictions, WUZAPI admin/media, real staging webhook/E2E, canary log scan, least-privilege roles, backup/restore, and rollback. Requires G10-B authorization.

### Tier 4 — production readiness/release

Read-only preflight and inventory first; mutations, secret rotation, DF adoption, tag, deploy, smoke, and rollback readiness require G10-C authorization. No production release claim is permitted from Tier 1/2 evidence alone.

## 10. G10-A exit and deferred-evidence matrix

The primary Gate 10 task/test checkboxes remain unchecked until their complete acceptance exists. G10-A records local sub-evidence separately; it must never mark a task/test complete merely because its repository portion exists.

### Task ownership by HOLD

| IDs | Required G10-A repository/local result | Remaining physical/release closure | Primary checkbox after G10-A |
|---|---|---|---|
| G10-T01 | Dedicated identifier config schema, normalization, placeholder rejection, and tests without real values | Secure provisioning and validation in G10-C | Unchecked |
| G10-T02 | Complete secret-name inventory template, fail-closed validation, rotation/revocation runbook | Real secret generation/provisioning/rotation in separately authorized G10-B/G10-C | Unchecked |
| G10-T03 | HTTPS-only parameterized edge/topology contract and static rendering tests | Real DNS, certificate, route, and TLS evidence in G10-B | Unchecked |
| G10-T04 | Private release networks, zero internal host publication, and disposable isolation tests | External/internal physical port evidence in G10-B | Unchecked |
| G10-T05 | Pinned-variable WUZAPI/admin-isolation contract and fail-closed configuration | Exact image/signature/admin identity and physical restriction evidence in G10-B | Unchecked |
| G10-T06 | Key-only/non-root/firewall/allowlist/break-glass policy and validation checklist | Named identity and physical SSH/firewall evidence in G10-B/G10-C | Unchecked |
| G10-T07 | Exact registration, conversation, organization, validation, and provider limits with unit/disposable race and anti-starvation evidence | Deployed edge/load/security evidence in G10-B | Unchecked |
| G10-T08 | Pinned dependency/image/history audit tooling and a passing current-repository audit under the frozen policy | Re-run against the final immutable release artifact in G10-C | May be checked only if the complete G10-T08 acceptance run passes |
| G10-T09 | Immutable base image and action pins plus mandatory digest-form WUZAPI input | Exact approved WUZAPI digest and final release image identities in G10-B/G10-C | Unchecked |
| G10-T10 | Non-root multi-stage image and disposable runtime hardening probes | Production-equivalent confirmation in G10-B | May be checked only if the complete local/disposable contract passes |
| G10-T11 | Runbook/evidence procedure only | Physical deployed WUZAPI media lifecycle evidence in G10-B | Unchecked |
| G10-T12 | Static callsite remediation and local canary log scans at every logging boundary | Bounded runtime staging log scan in G10-B | Unchecked |
| G10-T13 | Safe staging-only backup/restore procedure and preflight guards | Recent isolated staging restore in G10-B | Unchecked |
| G10-T14 | Immutable rollback runbook, manifest schema, safety guards, and local dry-run tests | Timed physical rollback in G10-B and previous-known-good identity in G10-C | Unchecked |

### Security-test ownership by HOLD

| IDs | G10-A evidence | Final closure |
|---|---|---|
| G10-X01–X09, G10-X17 | Existing and new local/disposable application, replay, race, restart, and limit evidence | Deployed-edge/staging portions in G10-B where required by Section 7 |
| G10-X10 | Local canary scans across shared and Transcription logging boundaries | Runtime staging canary scan in G10-B |
| G10-X11 | Complete pinned full-history/worktree secret scan under the frozen policy | Re-run against the exact G10-C release commit |
| G10-X12–X14 | Release-topology assertions, disposable network isolation, and disposable PostgreSQL role/privilege evidence | Physical network and actual staging-role evidence in G10-B |
| G10-X15 | Local/disposable success/failure/crash cleanup evidence | Staging filesystem/container evidence in G10-B |
| G10-X16 | Procedure only | Physical deployed WUZAPI media-retention evidence in G10-B |
| G10-X18 | Restore safety contract and disposable regression only | Recent clean staging restore in G10-B |

### G10-A completion rule

G10-A may report `READY FOR USER APPROVAL` only when every authorized repository deliverable exists, Tier 1 and applicable Tier 2 verification pass, affected Gates 2–9 regressions pass, no unresolved P0/P1 finding affects repository hardening, documentation matches evidence, and repository scope/secret/whitespace checks pass. This is approval of the G10-A repository phase only. It keeps `G10-APPROVED` false, leaves deferred primary checkboxes open, does not authorize G10-B/G10-C, and does not claim production readiness.

## 11. Frozen G10-A repository scope for HOLD review

The following is the maximum repository scope that may be considered by the G10-A implementation HOLD review. Inclusion authorizes review, not modification. Implementation may use fewer files, but any additional path requires a new scope review.

### Existing files eligible for bounded modification

- `Dockerfile`
- `.dockerignore`
- `.env.example`
- `.github/workflows/deploy.yml`
- `install.sh`
- `update.sh`
- `apps/orchestrator/src/orchestrator/config.py`
- `apps/orchestrator/src/orchestrator/main.py`
- `apps/orchestrator/src/orchestrator/fifo_worker.py`
- `apps/orchestrator/src/orchestrator/rate_limit.py`
- `apps/orchestrator/src/orchestrator/services/ingestion_service.py`
- `apps/orchestrator/src/orchestrator/services/fifo_worker_service.py`
- `apps/orchestrator/src/orchestrator/repositories/queue_repository.py`
- `apps/bot_df/src/bot_df/main.py`
- `apps/transcription/src/transcription/core/config.py`
- `apps/transcription/src/transcription/core/logging.py`
- `apps/transcription/src/transcription/services/internal_extraction_service.py`
- `apps/transcription/src/transcription/services/whatsapp_service.py`
- `apps/transcription/src/transcription/services/ai/gemini_provider.py`
- `apps/db_writer/src/db_writer/config.py`
- `packages/observability/src/observability/logging.py`
- `.agents/CURRENT_STATE.md`
- `.agents/TASKS_TESTS_GATES.md`
- `.agents/IMPLEMENTATION_PLAN_GATE_10.md`
- `.agents/GATE_10_DECISIONS_REQUIRED.md`

The local `docker-compose.yml` is frozen as a development-only contract. G10-A must use a separate release topology and must not silently convert local development into production behavior.

### New files eligible for creation

- `deploy/compose.release.yml`
- `deploy/README.md`
- `scripts/operations/gate10_preflight.py`
- `scripts/operations/gate10_security_audit.py`
- `.agents/GATE_10_DEPLOYMENT_RUNBOOK.md`
- `.agents/GATE_10_SECURITY_VALIDATION_RUNBOOK.md`
- `.agents/GATE_10_ROLLBACK_RUNBOOK.md`
- `.agents/GATE_10_RELEASE_MANIFEST.md`
- `CHANGELOG.md`
- `tests/test_platform_gate10_configuration_unit.py`
- `tests/test_platform_gate10_release_topology_unit.py`
- `tests/test_platform_gate10_security_audit_unit.py`
- `tests/test_platform_gate10_container_hardening_disposable.py`
- `tests/test_platform_gate10_operational_limits_disposable_postgres.py`
- `tests/test_platform_gate10_database_security_disposable_postgres.py`

### Frozen areas

- `docker-compose.yml`, all models, migrations, dependency manifests, `uv.lock`, application business rules, persistence states, Gates 4–8 tests and business/FIFO ordering semantics, Gate 9 scripts/tests/report authority, and Production Phase B. The specifically listed Gate 10 operational-limit files may change only for the bounded capacity contracts above and must preserve all earlier-Gate regressions;
- no new dependency, external monitoring/security vendor, database schema/index, public application endpoint, remote mutation, real secret/identifier, staging/VPS access, WUZAPI administration, DNS/firewall/SSH change, tag, deploy, or release.

## 12. Planning conclusions

1. The repository is not production-deployable from the current `docker-compose.yml`, `Dockerfile`, `install.sh`, `update.sh`, or deploy workflow without Gate 10 hardening.
2. Application security primitives provide substantial reusable evidence, but deployed-edge, network, WUZAPI, SSH, secret, restore, and rollback acceptance remain unproven.
3. G10-D01 through G10-D09 are closed conservatively for planning, and the corrected maximum G10-A file scope is re-frozen for a repeated read-only implementation HOLD review.
4. Deferred protected/environment inputs continue to block G10-B and G10-C as identified above; conservative closure does not claim those external facts exist.
5. No migration is authorized by this planning package.
6. G10-A implementation was explicitly authorized on 2026-08-16 and has been executed; G10-B/G10-C remain not started and `G10-APPROVED = false`.
7. The first G10-A HOLD review failed on four P1 planning blockers: operational-limit scope/identity, Transcription logging coverage, deterministic audit policy, and dedicated PostgreSQL privilege evidence. Correction Pass 1 addressed those blockers.
8. The repeated HOLD review then failed on four P1 residual blockers: exact organization state/serialization semantics, provider-permit ownership, severity-specific exception behavior, and objective G10-A versus G10-B/G10-C exit ownership. Correction Pass 2 closes those contracts without implementing Gate 10.

## 13. Current correction boundary

G10-A local implementation and applicable Tier 1/Tier 2 verification are complete, except that the fail-closed pinned Trivy audit found one High and two Medium vulnerabilities in the frozen `uv.lock`: `CVE-2024-23342` in `ecdsa 0.19.2` (no fixed version reported), plus `CVE-2026-71852` and `CVE-2026-71870` in `pypdf 6.14.2` (fixed in `6.15.0`). The approved policy blocks G10-A closure. Changing `pyproject.toml` or `uv.lock` is outside the frozen implementation scope; a High mitigation/exception requires the user's exact immutable-artifact approval and a maximum 30-day expiry.

The next action is a bounded security-correction decision: authorize dependency/lockfile scope to upgrade `pypdf`, and separately choose remediation/replacement or a time-bounded exact-artifact mitigation for `ecdsa`. This does not authorize G10-B, G10-C, staging/VPS, secret rotation, tags, deployment, migrations, or Production Phase B.

## 14. G10-A implementation evidence — 2026-08-16

- fail-closed protected-environment settings, exact operational limits, organization-level outstanding/active serialization, anti-starvation, provider concurrency, and logging redaction implemented in the frozen source scope;
- separate release topology, immutable base/tool pins, non-root multi-stage image, capability/read-only/resource controls, manual serialized release-request workflow, preflight/security CLIs, changelog, manifest, and deploy/security/rollback runbooks created;
- concurrent ingestion initially exposed an Organization-lock/FK deadlock; corrected with PostgreSQL `FOR NO KEY UPDATE`, then the race passed three focused repetitions and the full clean Gate 4 PostgreSQL regression;
- compileall PASS, Ruff PASS, and targeted mypy PASS;
- G10-A complete suite: 27 passed, 0 skipped, 0 failed, 0 errors, including real image build/runtime hardening, PostgreSQL 15 organization limits/anti-starvation, and disposable least-privilege Writer evidence;
- regressions: Gates 1–3 90 passed; Gate 4 unit 68 passed with 12 environment skips; clean Gate 4 PostgreSQL 126 passed; Gates 5–7/9 unit 227 passed with 1 environment skip; Gates 6–8 PostgreSQL/E2E 135 passed;
- pinned full-history Gitleaks found five exact historical fixture/documentation false positives, each disposed only by exact fingerprint/path/rule/reviewer/reason; no unresolved secret finding remained;
- an explicit pinned worktree Gitleaks mode scans exactly Git-tracked plus non-ignored untracked release-candidate files, excluding ignored local `.env`/virtual environments by Git scope rather than secret allowlists; the final physical rerun completed with five historical and three worktree fixture/documentation matches, all disposed only by exact fingerprint/path/rule/reviewer/reason, and zero unresolved secret findings;
- pinned Trivy completed and blocks closure on the three vulnerability findings recorded above;
- no staging, VPS, production, client database, remote database, real secret/identifier, DNS, firewall, SSH, WUZAPI administration, tag, deployment, migration, dependency, or lockfile mutation occurred.
- all invocation-owned `C:\tmp` test/evidence directories, orphaned pytest/uv/python processes, and the named disposable PostgreSQL regression container were cleaned after Docker Desktop stabilized.
