from __future__ import annotations

import secrets
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.exc import IntegrityError

from observability.middleware import CorrelationIdMiddleware
from db_writer.canonicalizer import canonicalize_payload
from db_writer.config import get_db_writer_settings
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


# --- Database Connection Initialization ---

settings = get_db_writer_settings()
engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- Authentication Dependency ---

def verify_internal_auth(authorization: Optional[str] = Header(None)) -> str:
    """Verifies internal Bearer token using constant-time comparison."""
    token = settings.db_writer_internal_token
    if not authorization or len(authorization) > 2048 or not authorization.startswith("Bearer "):
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


@app.post("/internal/write", response_model=WriteResponse)
def write_business_record(
    req: WriteRequest,
    db: Session = Depends(get_db),
    auth: str = Depends(verify_internal_auth),
):
    """Executes a business write operation atomically with durable idempotency."""
    if req.schema_version != "1.0" or req.payload.schema_version != "1.0":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported schema version",
        )

    now = datetime.now(timezone.utc)
    raw_payload_dict = req.payload.model_dump()
    canonical_hash = canonicalize_payload(raw_payload_dict)

    # 1. Idempotency Check
    existing_ledger = (
        db.query(WriteLedger)
        .filter(WriteLedger.idempotency_key == req.idempotency_key)
        .with_for_update()
        .first()
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

    # 2. Business Validation
    amount_val = req.payload.amount
    direction_val = req.payload.direction

    try:
        dec_amount = Decimal(str(amount_val)) if amount_val is not None else Decimal("0")
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
                existing = db.query(WriteLedger).filter(WriteLedger.idempotency_key == req.idempotency_key).first()
                if existing:
                    if existing.canonical_payload_hash != canonical_hash:
                        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Idempotency key payload mismatch")
                    return WriteResponse(status=existing.status, idempotency_key=existing.idempotency_key, processing_item_id=existing.processing_item_id, error_code=existing.error_code)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database integrity error")
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
            existing = db.query(WriteLedger).filter(WriteLedger.idempotency_key == req.idempotency_key).first()
            if existing:
                if existing.canonical_payload_hash != canonical_hash:
                    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Idempotency key payload mismatch")
                return WriteResponse(
                    status=existing.status,
                    idempotency_key=existing.idempotency_key,
                    processing_item_id=existing.processing_item_id,
                    committed_record_id=existing.committed_record_id,
                    error_code=existing.error_code,
                )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database integrity error")

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
