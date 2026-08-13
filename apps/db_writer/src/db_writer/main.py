from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from time import monotonic
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError

from observability.middleware import CorrelationIdMiddleware
from db_writer.canonicalizer import canonicalize_payload
from db_writer.config import get_db_writer_settings
from db_writer.df_adapter import (
    DestinationRejected,
    ExpenseWrite,
    LocalDFAdapter,
    normalize_cnpj,
)
from db_writer.models import WriteLedger, BusinessRecord


# --- Pydantic Request & Response Schemas ---


class WriteRequestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Optional[Any] = None
    direction: Optional[str] = None
    document_date: Optional[str] = None
    document_type: Optional[str] = None
    instance_id: str
    organization_id: str
    processing_item_id: str
    user_id: str
    schema_version: str = "1.0"
    transaction_date: Optional[datetime] = None
    date_source: Optional[str] = None
    enterprise_id: Optional[str] = None
    supplier_cnpj_snapshot: Optional[str] = None
    origin: Optional[str] = None


class WriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(..., min_length=1, max_length=512)
    processing_item_id: str
    organization_id: str
    instance_id: str
    user_id: str
    correlation_id: str
    document_type: str
    payload: WriteRequestPayload
    schema_version: str = "1.0"


class WriteResponse(BaseModel):
    status: str
    idempotency_key: str
    processing_item_id: str
    committed_record_id: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None


class EnterpriseChoice(BaseModel):
    id: str
    display_name: str


class EnterpriseListResponse(BaseModel):
    enterprises: list[EnterpriseChoice]


# --- Database Connection Initialization ---

settings = get_db_writer_settings()
SessionLocal = None


def _session_factory():
    global SessionLocal
    if SessionLocal is None:
        settings.validate_environment()
        engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            connect_args=settings.connection_args(),
        )
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return SessionLocal


def get_db():
    db = _session_factory()()
    try:
        yield db
    finally:
        db.close()


# --- Authentication Dependency ---


def verify_internal_auth(authorization: Optional[str] = Header(None)) -> str:
    """Verifies internal Bearer token using constant-time comparison."""
    token = settings.db_writer_internal_token
    if (
        not authorization
        or len(authorization) > 2048
        or not authorization.startswith("Bearer ")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )
    provided = authorization.split(" ", 1)[1]
    if not secrets.compare_digest(provided, token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal authorization token",
        )
    return provided


def _is_idempotency_key_race(exc: IntegrityError) -> bool:
    """Classifies a race ONLY when SQLSTATE is 23505 AND diag.constraint_name == 'uq_write_ledger_idempotency_key'."""
    orig = getattr(exc, "orig", None)
    if orig is None:
        return False

    diag = getattr(orig, "diag", None)
    if diag is None:
        return False

    sqlstate = getattr(diag, "sqlstate", None) or getattr(orig, "pgcode", None)
    constraint_name = getattr(diag, "constraint_name", None)

    return sqlstate == "23505" and constraint_name == "uq_write_ledger_idempotency_key"


class WriterDeadlineExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class ValidatedV2Payload:
    amount: Decimal
    transaction_date: datetime
    enterprise_id: str
    supplier_cnpj_snapshot: Optional[str]
    origin: str
    canonical_payload: dict[str, Any]


def _apply_statement_budget(db: Session, deadline: float) -> None:
    remaining_ms = int((deadline - monotonic()) * 1000)
    if remaining_ms <= 0:
        raise WriterDeadlineExceeded
    timeout_ms = min(settings.statement_timeout_ms, remaining_ms)
    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT set_config('statement_timeout', :timeout, true)"),
            {"timeout": f"{timeout_ms}ms"},
        )


def _retryable(req: WriteRequest, error_code: str) -> WriteResponse:
    return WriteResponse(
        status="RETRYABLE_FAILURE",
        idempotency_key=req.idempotency_key,
        processing_item_id=req.processing_item_id,
        error_code=error_code,
    )


def _outcome_unknown(req: WriteRequest, error_code: str) -> WriteResponse:
    return WriteResponse(
        status="OUTCOME_UNKNOWN",
        idempotency_key=req.idempotency_key,
        processing_item_id=req.processing_item_id,
        error_code=error_code,
    )


def _parse_v2_amount(value: Any) -> Decimal | None:
    if not isinstance(value, str):
        return None
    try:
        amount = Decimal(value)
    except (ValueError, ArithmeticError):
        return None
    exponent = amount.as_tuple().exponent
    if (
        not amount.is_finite()
        or amount <= 0
        or not isinstance(exponent, int)
        or exponent < -2
    ):
        return None
    return amount


def _validate_v2_payload(req: WriteRequest) -> ValidatedV2Payload:
    amount = _parse_v2_amount(req.payload.amount)
    transaction_date = req.payload.transaction_date
    invalid = (
        req.idempotency_key != f"write_{req.processing_item_id}"
        or req.payload.processing_item_id != req.processing_item_id
        or req.payload.organization_id != req.organization_id
        or req.payload.instance_id != req.instance_id
        or req.payload.user_id != req.user_id
        or req.payload.direction != "expense"
        or amount is None
        or transaction_date is None
        or transaction_date.utcoffset() is None
        or req.payload.date_source not in {"DOCUMENT", "MESSAGE_TIMESTAMP"}
        or not req.payload.enterprise_id
        or req.payload.origin != "WHATSAPP"
        or (
            req.payload.document_type is not None
            and req.payload.document_type != req.document_type
        )
    )
    if invalid:
        raise DestinationRejected("INVALID_BUSINESS_PAYLOAD")

    try:
        enterprise_id = str(uuid.UUID(req.payload.enterprise_id or ""))
    except (TypeError, ValueError, AttributeError) as exc:
        raise DestinationRejected("INVALID_ENTERPRISE_ID") from exc
    supplier_cnpj = normalize_cnpj(req.payload.supplier_cnpj_snapshot)
    assert amount is not None
    assert transaction_date is not None
    canonical_payload = req.payload.model_dump(mode="json")
    canonical_payload.update(
        {
            "amount": format(amount, ".2f"),
            "transaction_date": transaction_date.astimezone(timezone.utc).isoformat(),
            "enterprise_id": enterprise_id,
            "supplier_cnpj_snapshot": supplier_cnpj,
            "direction": "expense",
            "origin": "WHATSAPP",
        }
    )
    if req.payload.document_type is not None:
        canonical_payload["document_type"] = req.document_type
    return ValidatedV2Payload(
        amount=amount,
        transaction_date=transaction_date,
        enterprise_id=enterprise_id,
        supplier_cnpj_snapshot=supplier_cnpj,
        origin="WHATSAPP",
        canonical_payload=canonical_payload,
    )


def _lookup_ledger_after_idempotency_race(
    db: Session, idempotency_key: str, deadline: float
) -> Optional[WriteLedger]:
    _apply_statement_budget(db, deadline)
    return (
        db.query(WriteLedger)
        .filter(WriteLedger.idempotency_key == idempotency_key)
        .first()
    )


def _commit_rejected_ledger(
    db: Session,
    req: WriteRequest,
    canonical_hash: str,
    now: datetime,
    error_code: str,
    deadline: float,
) -> WriteResponse:
    db.add(
        WriteLedger(
            idempotency_key=req.idempotency_key,
            canonical_payload_hash=canonical_hash,
            processing_item_id=req.processing_item_id,
            organization_id=req.organization_id,
            instance_id=req.instance_id,
            user_id=req.user_id,
            schema_version=req.schema_version,
            status="REJECTED",
            error_code=error_code,
            attempt_count=1,
            created_at=now,
            updated_at=now,
        )
    )
    try:
        _apply_statement_budget(db, deadline)
        db.commit()
    except WriterDeadlineExceeded:
        db.rollback()
        return _retryable(req, "WRITER_DEADLINE_EXHAUSTED")
    except IntegrityError as exc:
        db.rollback()
        if _is_idempotency_key_race(exc):
            try:
                existing = _lookup_ledger_after_idempotency_race(
                    db, req.idempotency_key, deadline
                )
            except WriterDeadlineExceeded:
                return _retryable(req, "WRITER_DEADLINE_EXHAUSTED")
            if existing is not None:
                if existing.canonical_payload_hash != canonical_hash:
                    raise HTTPException(
                        status_code=409, detail="Idempotency key payload mismatch"
                    )
                return WriteResponse(
                    status=existing.status,
                    idempotency_key=existing.idempotency_key,
                    processing_item_id=existing.processing_item_id,
                    committed_record_id=existing.committed_record_id,
                    error_code=existing.error_code,
                )
        raise HTTPException(status_code=500, detail="Database integrity error")
    except OperationalError:
        db.rollback()
        return _outcome_unknown(req, "AMBIGUOUS_COMMIT")
    except DBAPIError:
        db.rollback()
        return _outcome_unknown(req, "AMBIGUOUS_COMMIT")
    return WriteResponse(
        status="REJECTED",
        idempotency_key=req.idempotency_key,
        processing_item_id=req.processing_item_id,
        error_code=error_code,
    )


# --- FastAPI App ---

app = FastAPI(title="Database Writer", version="1.0.0")
app.add_middleware(CorrelationIdMiddleware)


@app.middleware("http")
async def validate_content_length(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > 1048576:
        return JSONResponse(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            content={"detail": "Payload size exceeds 1MB limit"},
        )
    return await call_next(request)


@app.exception_handler(Exception)
def generic_exception_handler(request: Request, exc: Exception):
    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error occurred"},
    )


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "db-writer"}


@app.get("/internal/enterprises", response_model=EnterpriseListResponse)
def list_enterprises(
    db: Session = Depends(get_db),
    auth: str = Depends(verify_internal_auth),
):
    rows = LocalDFAdapter().list_enterprises(db)
    rows.sort(key=lambda row: (row["display_name"].casefold(), row["id"]))
    return EnterpriseListResponse(enterprises=[EnterpriseChoice(**row) for row in rows])


@app.post("/internal/write", response_model=WriteResponse)
def write_business_record(
    req: WriteRequest,
    db: Session = Depends(get_db),
    auth: str = Depends(verify_internal_auth),
):
    """Executes a business write operation atomically with durable idempotency."""
    if (
        req.schema_version not in {"1.0", "2.0"}
        or req.payload.schema_version != req.schema_version
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported schema version",
        )

    deadline = monotonic() + settings.handling_deadline_seconds
    validated_v2: Optional[ValidatedV2Payload] = None
    if req.schema_version == "2.0":
        try:
            validated_v2 = _validate_v2_payload(req)
        except DestinationRejected as exc:
            return WriteResponse(
                status="REJECTED",
                idempotency_key=req.idempotency_key,
                processing_item_id=req.processing_item_id,
                error_code=exc.code,
            )
        canonical_payload = validated_v2.canonical_payload
    else:
        canonical_payload = req.payload.model_dump(mode="json")
    canonical_hash = canonicalize_payload(canonical_payload)
    now = datetime.now(timezone.utc)

    try:
        _apply_statement_budget(db, deadline)
        if req.schema_version == "2.0" and db.get_bind().dialect.name == "postgresql":
            # Transaction-scoped serialization occurs before any destination DML.
            db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": req.idempotency_key},
            )
            _apply_statement_budget(db, deadline)
        existing_ledger = (
            db.query(WriteLedger)
            .filter(WriteLedger.idempotency_key == req.idempotency_key)
            .with_for_update()
            .first()
        )
    except (OperationalError, WriterDeadlineExceeded):
        db.rollback()
        return _retryable(req, "DESTINATION_TEMPORARILY_UNAVAILABLE")
    except DBAPIError:
        db.rollback()
        return WriteResponse(
            status="REJECTED",
            idempotency_key=req.idempotency_key,
            processing_item_id=req.processing_item_id,
            error_code="DESTINATION_SCHEMA_CONTRACT_ERROR",
        )

    if existing_ledger:
        if existing_ledger.canonical_payload_hash != canonical_hash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency key payload mismatch: canonical hash does not match original write",
            )
        return WriteResponse(
            status=existing_ledger.status,
            idempotency_key=existing_ledger.idempotency_key,
            processing_item_id=existing_ledger.processing_item_id,
            committed_record_id=existing_ledger.committed_record_id,
            error_code=existing_ledger.error_code,
        )

    if req.schema_version == "2.0":
        assert validated_v2 is not None

        adapter = LocalDFAdapter()
        try:
            record = adapter.insert_expense(
                db,
                ExpenseWrite(
                    amount=validated_v2.amount,
                    transaction_date=validated_v2.transaction_date,
                    enterprise_id=validated_v2.enterprise_id,
                    supplier_cnpj_snapshot=validated_v2.supplier_cnpj_snapshot,
                    origin=validated_v2.origin,
                    processing_item_id=req.processing_item_id,
                ),
                before_db_operation=lambda: _apply_statement_budget(db, deadline),
            )
        except DestinationRejected as exc:
            db.rollback()
            return _commit_rejected_ledger(
                db, req, canonical_hash, now, exc.code, deadline
            )
        except (OperationalError, WriterDeadlineExceeded):
            db.rollback()
            return _retryable(req, "DESTINATION_TEMPORARILY_UNAVAILABLE")
        except IntegrityError:
            db.rollback()
            return _commit_rejected_ledger(
                db,
                req,
                canonical_hash,
                now,
                "DESTINATION_CONSTRAINT_VIOLATION",
                deadline,
            )
        except DBAPIError:
            db.rollback()
            return _commit_rejected_ledger(
                db,
                req,
                canonical_hash,
                now,
                "DESTINATION_SCHEMA_CONTRACT_ERROR",
                deadline,
            )

        ledger = WriteLedger(
            idempotency_key=req.idempotency_key,
            canonical_payload_hash=canonical_hash,
            processing_item_id=req.processing_item_id,
            organization_id=req.organization_id,
            instance_id=req.instance_id,
            user_id=req.user_id,
            schema_version="2.0",
            status="COMMITTED",
            committed_record_id=str(record.id),
            attempt_count=1,
            created_at=now,
            updated_at=now,
            committed_at=now,
        )
        db.add(ledger)
        try:
            _apply_statement_budget(db, deadline)
            db.commit()
        except WriterDeadlineExceeded:
            db.rollback()
            return _retryable(req, "WRITER_DEADLINE_EXHAUSTED")
        except IntegrityError as exc:
            db.rollback()
            if _is_idempotency_key_race(exc):
                try:
                    existing = _lookup_ledger_after_idempotency_race(
                        db, req.idempotency_key, deadline
                    )
                except WriterDeadlineExceeded:
                    return _retryable(req, "WRITER_DEADLINE_EXHAUSTED")
                if existing is not None:
                    if existing.canonical_payload_hash != canonical_hash:
                        raise HTTPException(
                            status_code=409, detail="Idempotency key payload mismatch"
                        )
                    return WriteResponse(
                        status=existing.status,
                        idempotency_key=existing.idempotency_key,
                        processing_item_id=existing.processing_item_id,
                        committed_record_id=existing.committed_record_id,
                        error_code=existing.error_code,
                    )
            return WriteResponse(
                status="REJECTED",
                idempotency_key=req.idempotency_key,
                processing_item_id=req.processing_item_id,
                error_code="DESTINATION_CONSTRAINT_VIOLATION",
            )
        except OperationalError:
            db.rollback()
            return _outcome_unknown(req, "AMBIGUOUS_COMMIT")
        except DBAPIError:
            db.rollback()
            return _outcome_unknown(req, "AMBIGUOUS_COMMIT")
        return WriteResponse(
            status="COMMITTED",
            idempotency_key=req.idempotency_key,
            processing_item_id=req.processing_item_id,
            committed_record_id=str(record.id),
        )

    # 2. Business Validation
    amount_val = req.payload.amount
    direction_val = req.payload.direction

    try:
        dec_amount = (
            Decimal(str(amount_val)) if amount_val is not None else Decimal("0")
        )
    except Exception:
        dec_amount = Decimal("-1")

    is_valid_amount = dec_amount > Decimal("0")
    is_valid_direction = direction_val in ("income", "expense")

    if not is_valid_amount or not is_valid_direction:
        rejection_code = "INVALID_BUSINESS_PAYLOAD"
        ledger = WriteLedger(
            idempotency_key=req.idempotency_key,
            canonical_payload_hash=canonical_hash,
            processing_item_id=req.processing_item_id,
            organization_id=req.organization_id,
            instance_id=req.instance_id,
            user_id=req.user_id,
            status="REJECTED",
            error_code=rejection_code,
            attempt_count=1,
            created_at=now,
            updated_at=now,
        )
        db.add(ledger)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            if _is_idempotency_key_race(exc):
                existing = (
                    db.query(WriteLedger)
                    .filter(WriteLedger.idempotency_key == req.idempotency_key)
                    .first()
                )
                if existing:
                    if existing.canonical_payload_hash != canonical_hash:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail="Idempotency key payload mismatch",
                        )
                    return WriteResponse(
                        status=existing.status,
                        idempotency_key=existing.idempotency_key,
                        processing_item_id=existing.processing_item_id,
                        error_code=existing.error_code,
                    )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Database integrity error",
            )
        return WriteResponse(
            status="REJECTED",
            idempotency_key=req.idempotency_key,
            processing_item_id=req.processing_item_id,
            error_code=rejection_code,
            error_message="Business payload validation failed (amount must be > 0 and direction valid)",
        )

    # 3. Single DB Transaction: Business Record + Write Ledger Commit
    business_rec = BusinessRecord(
        organization_id=req.organization_id,
        instance_id=req.instance_id,
        user_id=req.user_id,
        processing_item_id=req.processing_item_id,
        document_type=req.document_type,
        direction=direction_val,
        amount=dec_amount,
        document_date=req.payload.document_date,
        created_at=now,
    )
    db.add(business_rec)
    db.flush()

    ledger = WriteLedger(
        idempotency_key=req.idempotency_key,
        canonical_payload_hash=canonical_hash,
        processing_item_id=req.processing_item_id,
        organization_id=req.organization_id,
        instance_id=req.instance_id,
        user_id=req.user_id,
        status="COMMITTED",
        committed_record_id=business_rec.id,
        attempt_count=1,
        created_at=now,
        updated_at=now,
        committed_at=now,
    )
    db.add(ledger)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if _is_idempotency_key_race(exc):
            existing = (
                db.query(WriteLedger)
                .filter(WriteLedger.idempotency_key == req.idempotency_key)
                .first()
            )
            if existing:
                if existing.canonical_payload_hash != canonical_hash:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Idempotency key payload mismatch",
                    )
                return WriteResponse(
                    status=existing.status,
                    idempotency_key=existing.idempotency_key,
                    processing_item_id=existing.processing_item_id,
                    committed_record_id=existing.committed_record_id,
                    error_code=existing.error_code,
                )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database integrity error",
        )

    return WriteResponse(
        status="COMMITTED",
        idempotency_key=req.idempotency_key,
        processing_item_id=req.processing_item_id,
        committed_record_id=business_rec.id,
    )


@app.get("/internal/writes/{idempotency_key}", response_model=WriteResponse)
def get_write_status(
    idempotency_key: str,
    db: Session = Depends(get_db),
    auth: str = Depends(verify_internal_auth),
):
    """Reconciliation endpoint returning durable outcome for a write idempotency key."""
    if not idempotency_key or len(idempotency_key) > 512 or " " in idempotency_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed idempotency key format",
        )

    ledger = (
        db.query(WriteLedger)
        .filter(WriteLedger.idempotency_key == idempotency_key)
        .first()
    )
    if not ledger:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Write idempotency key not found",
        )

    return WriteResponse(
        status=ledger.status,
        idempotency_key=ledger.idempotency_key,
        processing_item_id=ledger.processing_item_id,
        committed_record_id=ledger.committed_record_id,
        error_code=ledger.error_code,
    )
