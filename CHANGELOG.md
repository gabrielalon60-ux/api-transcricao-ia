# Changelog

## Unreleased — Gate 10-A repository hardening

- Added a separate immutable-image release topology with private internal/database networks and no published internal-service ports.
- Added multi-stage non-root application images and release container hardening controls.
- Added fail-closed protected-environment configuration, organization capacity limits, provider-call concurrency limits, and log redaction.
- Replaced push-to-main deployment with a serialized, manually approved immutable release-request workflow.
- Added local release preflight/security-audit tooling and deployment, security-validation, and rollback procedures.

No database migration, production schema adoption, tag, staging deployment, or production release is included.
