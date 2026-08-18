# Gate 10 — Contract Closure Input

> **Purpose**: collect non-secret decisions and approved references required before any Gate 10 implementation or environment access.
> **Status**: CLOSED CONSERVATIVE / CORRECTION PASS 2 COMPLETE / IMPLEMENTATION HOLD
> **Do not place here**: passwords, tokens, API keys, private keys, DSNs, database contents, real CPF/CNPJ values, recovery codes, or client data.

References may identify an approved secret manager item, private document, infrastructure ticket, or named owner without copying the protected value into Git or chat.

## G10-D01 — Production DF schema/adoption

- Business/technical owner: Gabriel
- Approved private schema reference: PENDING - private DF schema document/owner reference required
- Actual enterprise table contract approved: NO
- `financial_records` destination contract approved: NO
- supplier lookup contract approved: NO
- Writer role/grants contract approved: NO
- TLS CA/hostname contract approved: NO
- Production adoption/migration owner: PENDING
- Rollback boundary approved: NO
- Production Phase B separately authorized: NO

## G10-D02 — Real DF identifiers

- Business owner: Gabriel
- Secure configuration/secret reference (not the values): PENDING - approved secret-store item required
- Expected count of CPF entries: PENDING
- Expected count of CNPJ entries: PENDING
- Normalized digit-only validation approved: YES
- Placeholder rejection outside development/test approved: YES
- Change/rotation procedure owner: Gabriel / PENDING formal procedure

## G10-D03 — Staging/production infrastructure

- Infrastructure owner: Gabriel
- Dokploy/project inventory reference: PENDING - private infrastructure inventory required
- Staging topology reference: PENDING
- Production topology reference: PENDING
- Domain/DNS owner: Gabriel / PENDING provider reference
- Traefik/certificate-resolver policy reference: PENDING
- Approved public routes: PENDING - expected only HTTPS edge routes
- Approved private services/networks: PENDING - DB/internal services must remain private
- Persistent-volume/backup-destination reference: PENDING
- Current OS/Docker inventory reference: PENDING

## G10-D04 — SSH/firewall

- Host/security owner: Gabriel
- Key-only SSH required: YES
- Root login disabled: YES
- Password login disabled: YES
- Approved deployment identity: PENDING - named non-root deploy user required
- Source allowlist, VPN, or access-proxy policy reference: PENDING
- Host/cloud firewall policy reference: PENDING
- Brute-force protection policy: PENDING - fail2ban/cloud equivalent required
- Emergency-access owner/procedure reference: Gabriel / PENDING documented break-glass procedure

## G10-D05 — Deployed WUZAPI contract

- WUZAPI owner: Gabriel
- Exact version/image digest reference: PENDING - pinned image/digest required
- Webhook signature header: PENDING - must match deployed WUZAPI capability
- Signature algorithm/encoding reference: PENDING
- Approved public webhook route: PENDING - HTTPS webhook route only
- Admin route/port and restriction policy reference: PENDING - admin must not be public
- Authentication policy reference: PENDING
- Staging instance/test identity reference: PENDING
- Media storage path reference: PENDING
- Media retention duration/policy: PENDING - default target: shortest operationally viable retention
- Deletion verification method: PENDING
- Real payload fixture reference with client data removed: PENDING

## G10-D06 — Secret inventory/rotation

- Security owner: Gabriel
- Approved secret-store/project reference: PENDING - Bitwarden/1Password/Dokploy/GitHub Secrets reference required
- Inventory covers every application, database, WUZAPI, SSH, edge, and CI secret: NO / PENDING
- Separate token per service boundary: YES
- New production values required: YES
- Rotation date/window: PENDING
- Revocation/rollback procedure reference: PENDING
- Audit owner: Gabriel

## G10-D07 — Operational limits

- Product/operations owner: Gabriel
- Webhook limit and burst: 60/minute sustained, burst 120/minute per public route
- Per-IP limit: 30/minute sustained, burst 60/minute
- Per-organization limit: 20 active/in-flight processing items
- Registration limit/window/block duration: 5 failed attempts/hour per `(organization_id, phone_number)`, block 24h
- Maximum HTTP body/upload size: 25 MB
- Organization outstanding capacity: 100 items across the exact outstanding status set below
- Extraction/validation concurrency: 4 workers per service instance
- Provider concurrency: 2 concurrent provider calls per service instance
- Organization outstanding status set: `RECEIVED`, `EXTRACTING`, `EXTRACTED`, `READY`, `ACTIVE`, `VALIDATING`, `WAITING_USER_INPUT`, `PERSISTING`, `PERSIST_RETRYABLE`, `PERSIST_OUTCOME_UNKNOWN`
- Organization active-processing status set: `ACTIVE`, `VALIDATING`, `WAITING_USER_INPUT`, `PERSISTING`, `PERSIST_RETRYABLE`, `PERSIST_OUTCOME_UNKNOWN`
- Organization-limit serialization: lock the durable Organization row, count and revalidate inside the same transaction; saturated organizations must not starve eligible organizations
- Provider permit acquisition timeout: 2 seconds
- Provider permit ownership: one permit per physical provider I/O attempt; release in `finally`; never hold during retry backoff, parsing, or persistence
- Provider capacity before I/O: `PROVIDER_CAPACITY_EXCEEDED`, retryable, zero provider-attempt usage rows
- Request/edge timeouts: 30s edge request timeout; long processing async only
- Evidence or load-test reference: PENDING - validate in staging before production

Registration is initiated by a WhatsApp user through WUZAPI, so the application does not receive a trustworthy end-user source IP. The public-edge per-IP limit remains separate and applies to network callers; registration abuse control uses the durable organization-and-phone identity already owned by the application.

## G10-D08 — Staging E2E inventory

- Staging owner: Gabriel
- Staging project/environment reference: PENDING
- Isolated WhatsApp/WUZAPI test identity available: PENDING
- Staging Platform DB available: PENDING
- Staging DF DB and least-privilege Writer role available: PENDING
- Non-production provider/Gemini policy reference: PENDING
- Test organization/phone ownership reference: PENDING
- Test-data cleanup policy: PENDING
- Abrupt restart authorized: NO - requires separate staging authorization
- Malformed/security traffic authorized: NO - requires separate staging authorization
- External/internal port scan authorized: NO - requires separate staging authorization
- Backup/restore authorized: NO - requires separate staging authorization
- Rollback exercise authorized: NO - requires separate staging authorization

## G10-D09 — Release/rollback authority

- Release owner: Gabriel
- Final go/no-go authority: Gabriel
- Version/tag convention: vMAJOR.MINOR.PATCH, starting with first production candidate after Gate 10 approval
- Changelog owner: Gabriel
- Immutable image/artifact policy: YES - deploy only pinned immutable image/artifact
- Deployment approval mechanism: REQUIRED - manual approval before production deploy
- Maintenance window: PENDING
- Previous-known-good release reference: PENDING - required before production release
- Required health/readiness checks: HTTPS health endpoints, internal service health, DB connectivity, queue/drain status, Gate 9 operational reports
- Maximum rollback time: PENDING - target <= 15 minutes after rollback decision
- Database compatibility/forward-only policy: PENDING - no production migration authorized in Gate 10-A
- Release workflow may replace direct-on-main deploy: YES

## Closure declaration

- [x] G10-D01 CLOSED
- [x] G10-D02 CLOSED
- [x] G10-D03 CLOSED
- [x] G10-D04 CLOSED
- [x] G10-D05 CLOSED
- [x] G10-D06 CLOSED
- [x] G10-D07 CLOSED
- [x] G10-D08 CLOSED
- [x] G10-D09 CLOSED
- [x] Exact G10-A repository file scope approved for implementation HOLD review
- [x] No secret/protected value was copied into this document

The exact G10-A repository scope is frozen in `.agents/IMPLEMENTATION_PLAN_GATE_10.md`. The final HOLD review passed and the user explicitly authorized G10-A implementation on 2026-08-16.

G10-A authorization does not authorize G10-B staging access, G10-C production release, Production Phase B, migrations, secret rotation, tagging, or deployment. Each requires its own explicit authorization.

## G10-D10 — Local vulnerability correction — OPEN / BLOCKING G10-A CLOSURE

- `CVE-2026-71852` and `CVE-2026-71870`: Medium, `pypdf 6.14.2`, fixed version `6.15.0`; dependency/lockfile correction scope is not yet authorized.
- `CVE-2024-23342`: High, `ecdsa 0.19.2`, no fixed version reported by the pinned audit; remediation/replacement is preferred. Any temporary mitigation must name the exact immutable artifact, owner, reachability evidence, mitigation, approval date, and expiry no longer than 30 days, and requires explicit user approval.
- No exception or accepted-risk disposition has been granted.
