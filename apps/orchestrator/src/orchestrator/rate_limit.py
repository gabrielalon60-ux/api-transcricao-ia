from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from db.models import RegistrationRateLimit, RegistrationAttempt
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert


def check_and_record_registration(
    db: Session,
    organization_id: str,
    phone_number: str,
    instance_id: str | None,
    correlation_id: str,
    secret_validator_fn,
    submitted_secret: str,
    pepper: str,
    max_failed_attempts: int = 3,
    window_seconds: int = 300,
    block_seconds: int = 300,
) -> tuple[bool, str, str | None]:
    """
    Checks rate limits, validates secret, and updates operational and audit tables.
    Returns (success, user_message, error_code).
    """
    now_dt = datetime.now(timezone.utc)
    values = {
        "organization_id": organization_id,
        "phone_number": phone_number,
        "failure_count": 0,
        "window_started_at": now_dt,
        "blocked_until": None,
        "updated_at": now_dt,
    }

    # Dialect-aware insert
    from typing import Any

    bind = db.get_bind()
    dialect = bind.dialect.name
    stmt: Any
    if dialect == "postgresql":
        stmt = (
            pg_insert(RegistrationRateLimit)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["organization_id", "phone_number"])
        )
    else:
        stmt = (
            sqlite_insert(RegistrationRateLimit)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["organization_id", "phone_number"])
        )

    db.execute(stmt)
    db.flush()

    # Lock row using SELECT ... FOR UPDATE
    rate_limit = (
        db.query(RegistrationRateLimit)
        .filter_by(organization_id=organization_id, phone_number=phone_number)
        .with_for_update()
        .first()
    )

    if not rate_limit:
        return (
            False,
            "Não foi possível processar sua solicitação neste momento.\nTente novamente mais tarde.",
            "INTERNAL_ERROR",
        )

    # Normalize datetimes for dialect compatibility (SQLite naive vs PostgreSQL aware)
    window_started = rate_limit.window_started_at
    if window_started and window_started.tzinfo is None:
        window_started = window_started.replace(tzinfo=timezone.utc)

    blocked_until = rate_limit.blocked_until
    if blocked_until and blocked_until.tzinfo is None:
        blocked_until = blocked_until.replace(tzinfo=timezone.utc)

    if max_failed_attempts <= 0 or window_seconds <= 0 or block_seconds <= 0:
        raise ValueError("Registration rate-limit values must be positive")

    # A live block always wins over window rollover.
    if blocked_until and blocked_until > now_dt:
        attempt = RegistrationAttempt(
            organization_id=organization_id,
            instance_id=instance_id,
            phone_number=phone_number,
            correlation_id=correlation_id,
            success=False,
            failure_reason="REGISTRATION_RATE_LIMITED",
        )
        db.add(attempt)
        return (
            False,
            "⚠️ Muitas tentativas de cadastro foram realizadas.\n\nTente novamente mais tarde.",
            "REGISTRATION_RATE_LIMITED",
        )

    # Expired blocks and anchored windows begin a fresh window.
    if blocked_until or now_dt - window_started >= timedelta(seconds=window_seconds):
        rate_limit.failure_count = 0
        rate_limit.window_started_at = now_dt
        window_started = now_dt
        rate_limit.blocked_until = None
        blocked_until = None

    # Validate the secret (only when not blocked)
    is_valid = secret_validator_fn(submitted_secret)

    if is_valid:
        # Success path: reset counts, keep row and audit history
        rate_limit.failure_count = 0
        rate_limit.blocked_until = None
        rate_limit.updated_at = now_dt

        attempt = RegistrationAttempt(
            organization_id=organization_id,
            instance_id=instance_id,
            phone_number=phone_number,
            correlation_id=correlation_id,
            success=True,
        )
        db.add(attempt)
        return (
            True,
            "✅ Cadastro realizado com sucesso.\n\nVocê já pode enviar seus comprovantes.",
            None,
        )
    else:
        # Failure path: increment counts and block at the configured threshold.
        rate_limit.failure_count += 1
        rate_limit.updated_at = now_dt

        reason = "INVALID_REGISTRATION_SECRET"
        if rate_limit.failure_count >= max_failed_attempts:
            rate_limit.blocked_until = now_dt + timedelta(seconds=block_seconds)
            reason = "REGISTRATION_RATE_LIMITED"

        attempt = RegistrationAttempt(
            organization_id=organization_id,
            instance_id=instance_id,
            phone_number=phone_number,
            correlation_id=correlation_id,
            success=False,
            failure_reason=reason,
        )
        db.add(attempt)

        if rate_limit.failure_count >= max_failed_attempts:
            return (
                False,
                "⚠️ Muitas tentativas de cadastro foram realizadas.\n\nTente novamente mais tarde.",
                "REGISTRATION_RATE_LIMITED",
            )
        else:
            return (
                False,
                "❌ Senha de cadastro inválida.\n\nVerifique a senha e tente novamente.",
                "INVALID_REGISTRATION_SECRET",
            )
