from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.models import Execution, ProcessingItem
from orchestrator.config import get_settings
from orchestrator.db_writer_client import DBWriterClient

logger = logging.getLogger(__name__)

LEASE_DURATION_SECONDS = 30


def transition_validating_to_persisting(
    db: Session,
    item_id: str,
    worker_id: str = "worker-1",
) -> Optional[ProcessingItem]:
    """Atomically transitions a ProcessingItem from VALIDATING -> PERSISTING.

    Invariants:
      - Item must be in VALIDATING status.
      - Sets writer_idempotency_key = f"write_{item.id}".
      - Increments persistence_generation += 1.
      - Resets persistence_claimed_by = None.
      - Creates PERSISTENCE_DISPATCH_RESERVED execution checkpoint (generation-bounded).
      - Commits transaction BEFORE external Database Writer call.
    """
    now = datetime.now(timezone.utc)
    item = (
        db.query(ProcessingItem)
        .filter(
            ProcessingItem.id == item_id,
            ProcessingItem.status == "VALIDATING",
        )
        .with_for_update()
        .first()
    )

    if not item:
        logger.info(f"Item {item_id} not eligible for PERSISTING transition.")
        return None

    writer_key = f"write_{item.id}"
    item.status = "PERSISTING"
    item.writer_idempotency_key = writer_key
    item.external_operation_status = "RESERVED"
    item.persistence_generation += 1
    item.persistence_claimed_by = None
    item.persistence_lease_expires_at = None

    gen = item.persistence_generation
    attempt = max(1, item.attempt_count)

    sp = db.begin_nested()
    try:
        db.add(
            Execution(
                processing_item_id=item.id,
                event_id=item.event_id,
                correlation_id=item.correlation_id,
                component="ORCHESTRATOR",
                operation="PERSISTENCE_DISPATCH_RESERVED",
                external_reference=writer_key,
                operation_idempotency_key=f"{item.id}:PERSISTENCE_DISPATCH_RESERVED:{writer_key}:{gen}",
                status="SUCCESS",
                effect_status="DISPATCHED",
                attempt=attempt,
                started_at=now,
                completed_at=now,
            )
        )
        sp.commit()
    except IntegrityError as exc:
        sp.rollback()
        logger.info(f"PERSISTENCE_DISPATCH_RESERVED already written for item {item.id} gen {gen}: {exc}")

    db.commit()
    db.refresh(item)
    return item


def claim_persistence_dispatch(
    db: Session,
    item_id: str,
    worker_id: str = "worker-1",
) -> Optional[Tuple[ProcessingItem, str, int]]:
    """Exclusively claims dispatch ownership for a PERSISTING processing item across Orchestrator replicas.

    Returns (item, dispatch_token, generation) if claimed successfully, else None.
    """
    now = datetime.now(timezone.utc)
    dispatch_token = str(uuid.uuid4())

    item = (
        db.query(ProcessingItem)
        .filter(
            ProcessingItem.id == item_id,
            ProcessingItem.status == "PERSISTING",
        )
        .with_for_update()
        .first()
    )

    if not item:
        return None

    # Check if already claimed with an active lease
    if item.persistence_claimed_by is not None and item.persistence_lease_expires_at is not None:
        if item.persistence_lease_expires_at > now:
            logger.info(f"Item {item_id} dispatch already claimed by {item.persistence_claimed_by}")
            return None

    item.persistence_claimed_by = dispatch_token
    item.persistence_claim_kind = "DISPATCH"
    item.persistence_lease_expires_at = now + timedelta(seconds=LEASE_DURATION_SECONDS)
    gen = item.persistence_generation

    db.commit()
    db.refresh(item)
    return item, dispatch_token, gen


def dispatch_persistence_write(
    db: Session,
    item_id: str,
    dispatch_token: Optional[str] = None,
    client: Optional[DBWriterClient] = None,
) -> Optional[ProcessingItem]:
    """Dispatches a PERSISTING processing item to Database Writer service with exclusive dispatch token guard.

    Ordering:
      1. Verify item is in PERSISTING status and claimed by dispatch_token (if provided).
      2. Record PERSISTENCE_DISPATCHED execution checkpoint.
      3. Call Database Writer POST /internal/write outside Platform DB transaction.
      4. Lock item for update, verify persistence_generation, and apply result:
         - COMMITTED -> item.status = COMPLETED (unblocks conversation)
         - REJECTED -> item.status = PERSISTENCE_FAILED (unblocks conversation)
         - RETRYABLE_FAILURE -> item.status = PERSIST_RETRYABLE (remains blocking)
         - OUTCOME_UNKNOWN -> item.status = PERSIST_OUTCOME_UNKNOWN (remains blocking)
    """
    now = datetime.now(timezone.utc)
    item = (
        db.query(ProcessingItem)
        .filter(
            ProcessingItem.id == item_id,
            ProcessingItem.status == "PERSISTING",
        )
        .first()
    )

    if not item or not item.writer_idempotency_key:
        logger.info(f"Item {item_id} not ready for persistence dispatch.")
        return None

    # Guard claim token if provided
    if dispatch_token and item.persistence_claimed_by and item.persistence_claimed_by != dispatch_token:
        logger.info(f"Dispatch token mismatch for item {item_id}: expected {dispatch_token}, found {item.persistence_claimed_by}")
        return None

    writer_key = item.writer_idempotency_key
    claimed_gen = item.persistence_generation

    # Record PERSISTENCE_DISPATCHED checkpoint
    sp_disp = db.begin_nested()
    try:
        db.add(
            Execution(
                processing_item_id=item.id,
                event_id=item.event_id,
                correlation_id=item.correlation_id,
                component="ORCHESTRATOR",
                operation="PERSISTENCE_DISPATCHED",
                external_reference=writer_key,
                operation_idempotency_key=f"{item.id}:PERSISTENCE_DISPATCHED:{writer_key}:{claimed_gen}",
                status="SUCCESS",
                effect_status="DISPATCHED",
                attempt=max(1, item.persistence_attempt_count + 1),
                started_at=now,
                completed_at=now,
            )
        )
        sp_disp.commit()
    except IntegrityError:
        sp_disp.rollback()

    db.commit()

    # Call Database Writer outside DB transaction
    if client is None:
        client = DBWriterClient()

    payload_data = {
        "amount": str(item.amount) if item.amount is not None else None,
        "direction": item.direction,
        "document_date": item.document_date,
        "document_type": item.document_type,
        "instance_id": item.instance_id,
        "organization_id": item.organization_id,
        "processing_item_id": item.id,
        "user_id": item.user_id,
        "schema_version": "1.0",
    }

    res = client.write(
        idempotency_key=writer_key,
        processing_item_id=item.id,
        organization_id=item.organization_id,
        instance_id=item.instance_id,
        user_id=item.user_id,
        correlation_id=item.correlation_id,
        document_type=item.document_type or "unknown",
        payload=payload_data,
    )

    outcome_status = res.get("status", "OUTCOME_UNKNOWN")

    # Lock item for update and apply result (guarded by persistence_generation)
    item_locked = (
        db.query(ProcessingItem)
        .filter(ProcessingItem.id == item_id)
        .with_for_update()
        .first()
    )

    if not item_locked or item_locked.status != "PERSISTING":
        logger.info(f"Item {item_id} state changed during dispatch execution.")
        return item_locked

    if item_locked.persistence_generation != claimed_gen:
        logger.warning(
            f"Stale persistence dispatch result ignored for item {item_id}: "
            f"claimed_gen={claimed_gen}, current_gen={item_locked.persistence_generation}"
        )
        return item_locked

    now_post = datetime.now(timezone.utc)
    settings = get_settings()
    item_locked.persistence_attempt_count += 1

    if outcome_status == "COMMITTED":
        item_locked.status = "COMPLETED"
        item_locked.external_operation_status = "COMMITTED"
        item_locked.completed_at = now_post
        item_locked.claimed_by = None
        item_locked.heartbeat_at = None
        item_locked.lease_expires_at = None
        item_locked.persistence_claimed_by = None
        item_locked.persistence_claim_kind = None
        item_locked.persistence_lease_expires_at = None

        sp_com = db.begin_nested()
        try:
            db.add(
                Execution(
                    processing_item_id=item_locked.id,
                    event_id=item_locked.event_id,
                    correlation_id=item_locked.correlation_id,
                    component="ORCHESTRATOR",
                    operation="PERSISTENCE_COMMITTED",
                    external_reference=res.get("committed_record_id"),
                    operation_idempotency_key=f"{item_locked.id}:PERSISTENCE_COMMITTED:{writer_key}:{claimed_gen}",
                    status="SUCCESS",
                    effect_status="ACKNOWLEDGED",
                    attempt=item_locked.persistence_attempt_count,
                    started_at=now_post,
                    completed_at=now_post,
                )
            )
            sp_com.commit()
        except IntegrityError:
            sp_com.rollback()

    elif outcome_status == "REJECTED":
        item_locked.status = "PERSISTENCE_FAILED"
        item_locked.external_operation_status = "REJECTED"
        item_locked.error_code = res.get("error_code", "INVALID_BUSINESS_PAYLOAD")
        item_locked.error_message_sanitized = "Database Writer rejected business payload"
        item_locked.claimed_by = None
        item_locked.heartbeat_at = None
        item_locked.lease_expires_at = None
        item_locked.persistence_claimed_by = None
        item_locked.persistence_claim_kind = None
        item_locked.persistence_lease_expires_at = None

        sp_rej = db.begin_nested()
        try:
            db.add(
                Execution(
                    processing_item_id=item_locked.id,
                    event_id=item_locked.event_id,
                    correlation_id=item_locked.correlation_id,
                    component="ORCHESTRATOR",
                    operation="PERSISTENCE_FAILED_FINAL",
                    external_reference=writer_key,
                    operation_idempotency_key=f"{item_locked.id}:PERSISTENCE_FAILED_FINAL:{writer_key}:{claimed_gen}",
                    status="FAILED",
                    effect_status="FAILED",
                    attempt=max(1, item_locked.persistence_attempt_count + 1),
                    started_at=now_post,
                    completed_at=now_post,
                    error_code=item_locked.error_code,
                )
            )
            sp_rej.commit()
        except IntegrityError:
            sp_rej.rollback()

    elif outcome_status == "RETRYABLE_FAILURE":
        item_locked.persistence_claimed_by = None
        item_locked.persistence_claim_kind = None
        item_locked.persistence_lease_expires_at = None

        max_attempts = getattr(settings, "persistence_max_dispatch_attempts", 5)
        base_backoff = getattr(settings, "persistence_base_backoff_seconds", 5)
        max_backoff = getattr(settings, "persistence_max_backoff_seconds", 300)

        if item_locked.persistence_attempt_count >= max_attempts:
            # Reached max attempts on retryable failure where write is known not committed
            item_locked.status = "PERSISTENCE_FAILED"
            item_locked.external_operation_status = "MAX_ATTEMPTS_EXCEEDED"
            item_locked.error_code = "MAX_PERSISTENCE_ATTEMPTS_EXCEEDED"
            item_locked.claimed_by = None
            item_locked.heartbeat_at = None
            item_locked.lease_expires_at = None
            item_locked.persistence_claimed_by = None
            item_locked.persistence_claim_kind = None
            item_locked.persistence_lease_expires_at = None

            sp_max = db.begin_nested()
            try:
                db.add(
                    Execution(
                        processing_item_id=item_locked.id,
                        event_id=item_locked.event_id,
                        correlation_id=item_locked.correlation_id,
                        component="ORCHESTRATOR",
                        operation="PERSISTENCE_FAILED_FINAL",
                        external_reference=writer_key,
                        operation_idempotency_key=f"{item_locked.id}:PERSISTENCE_FAILED_FINAL:{writer_key}:{claimed_gen}",
                        status="FAILED",
                        effect_status="FAILED",
                        attempt=item_locked.persistence_attempt_count,
                        started_at=now_post,
                        completed_at=now_post,
                        error_code="MAX_PERSISTENCE_ATTEMPTS_EXCEEDED",
                    )
                )
                sp_max.commit()
            except IntegrityError:
                sp_max.rollback()
        else:
            item_locked.status = "PERSIST_RETRYABLE"
            item_locked.external_operation_status = "RETRYABLE_FAILURE"
            backoff = min(base_backoff * (2 ** (item_locked.persistence_attempt_count - 1)), max_backoff)
            item_locked.persistence_next_attempt_at = now_post + timedelta(seconds=backoff)

            sp_ret = db.begin_nested()
            try:
                db.add(
                    Execution(
                        processing_item_id=item_locked.id,
                        event_id=item_locked.event_id,
                        correlation_id=item_locked.correlation_id,
                        component="ORCHESTRATOR",
                        operation="PERSISTENCE_RETRYABLE",
                        external_reference=writer_key,
                        operation_idempotency_key=f"{item_locked.id}:PERSISTENCE_RETRYABLE:{writer_key}:{claimed_gen}:{item_locked.persistence_attempt_count}",
                        status="FAILED",
                        effect_status="FAILED",
                        attempt=item_locked.persistence_attempt_count,
                        started_at=now_post,
                        completed_at=now_post,
                        error_code=res.get("error_code", "RETRYABLE_FAILURE"),
                    )
                )
                sp_ret.commit()
            except IntegrityError:
                sp_ret.rollback()

    else:
        # OUTCOME_UNKNOWN (timeout, connection loss)
        item_locked.status = "PERSIST_OUTCOME_UNKNOWN"
        item_locked.external_operation_status = "OUTCOME_UNKNOWN"
        item_locked.persistence_claimed_by = None
        item_locked.persistence_claim_kind = None
        item_locked.persistence_lease_expires_at = None

        sp_unk = db.begin_nested()
        try:
            db.add(
                Execution(
                    processing_item_id=item_locked.id,
                    event_id=item_locked.event_id,
                    correlation_id=item_locked.correlation_id,
                    component="ORCHESTRATOR",
                    operation="PERSISTENCE_OUTCOME_UNKNOWN",
                    external_reference=writer_key,
                    operation_idempotency_key=f"{item_locked.id}:PERSISTENCE_OUTCOME_UNKNOWN:{writer_key}:{claimed_gen}",
                    status="FAILED",
                    effect_status="OUTBOUND_OUTCOME_UNKNOWN",
                    attempt=max(1, item_locked.persistence_attempt_count),
                    started_at=now_post,
                    completed_at=now_post,
                    error_code=res.get("error_code", "OUTCOME_UNKNOWN"),
                )
            )
            sp_unk.commit()
        except IntegrityError:
            sp_unk.rollback()

    db.commit()
    db.refresh(item_locked)
    return item_locked


def recover_stale_persistence_items(
    db: Session,
    stale_threshold_seconds: int = 60,
) -> int:
    """Recovers expired PERSISTING and PERSIST_RETRYABLE items.

    Rules:
      - Expired PERSISTING with NO DISPATCHED execution checkpoint:
        remains PERSISTING, increments persistence_generation, clears claim token.
      - Expired PERSISTING WITH DISPATCHED execution checkpoint:
        transitions to PERSIST_OUTCOME_UNKNOWN, creates PERSISTENCE_OUTCOME_UNKNOWN checkpoint once.
      - PERSIST_RETRYABLE with now >= persistence_next_attempt_at:
        transitions PERSIST_RETRYABLE -> PERSISTING, increments persistence_generation, preserves writer_idempotency_key.
    """
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(seconds=stale_threshold_seconds)
    recovered_count = 0

    # 1. Recover stale PERSISTING items
    stale_persisting = (
        db.query(ProcessingItem)
        .filter(
            ProcessingItem.status == "PERSISTING",
            (
                (ProcessingItem.persistence_lease_expires_at.isnot(None)) & (ProcessingItem.persistence_lease_expires_at < now)
            ) | (
                (ProcessingItem.persistence_lease_expires_at.is_(None)) & (ProcessingItem.updated_at < stale_cutoff)
            ),
        )
        .with_for_update(skip_locked=True)
        .all()
    )

    for item in stale_persisting:
        gen = item.persistence_generation
        writer_key = item.writer_idempotency_key or f"write_{item.id}"

        # Check if PERSISTENCE_DISPATCHED execution was ever written for this generation
        disp_exec = (
            db.query(Execution)
            .filter(
                Execution.processing_item_id == item.id,
                Execution.operation == "PERSISTENCE_DISPATCHED",
                Execution.operation_idempotency_key == f"{item.id}:PERSISTENCE_DISPATCHED:{writer_key}:{gen}",
            )
            .first()
        )

        if disp_exec:
            # Dispatched but outcome unknown -> transition to PERSIST_OUTCOME_UNKNOWN
            item.status = "PERSIST_OUTCOME_UNKNOWN"
            item.external_operation_status = "OUTCOME_UNKNOWN"
            item.persistence_claimed_by = None
            item.persistence_claim_kind = None
            item.persistence_lease_expires_at = None

            sp = db.begin_nested()
            try:
                db.add(
                    Execution(
                        processing_item_id=item.id,
                        event_id=item.event_id,
                        correlation_id=item.correlation_id,
                        component="ORCHESTRATOR",
                        operation="PERSISTENCE_OUTCOME_UNKNOWN",
                        external_reference=writer_key,
                        operation_idempotency_key=f"{item.id}:PERSISTENCE_OUTCOME_UNKNOWN:{writer_key}:{gen}",
                        status="FAILED",
                        effect_status="OUTBOUND_OUTCOME_UNKNOWN",
                        attempt=max(1, item.persistence_attempt_count),
                        started_at=now,
                        completed_at=now,
                        error_code="STALE_DISPATCH_TIMEOUT",
                    )
                )
                sp.commit()
            except IntegrityError:
                sp.rollback()
        else:
            # Never dispatched -> make dispatchable again under new generation
            item.persistence_generation += 1
            item.persistence_claimed_by = None
            item.persistence_claim_kind = None
            item.persistence_lease_expires_at = None

        recovered_count += 1

    # 2. Recover PERSIST_RETRYABLE items eligible for re-dispatch
    retryable_items = (
        db.query(ProcessingItem)
        .filter(
            ProcessingItem.status == "PERSIST_RETRYABLE",
            (
                (ProcessingItem.persistence_next_attempt_at.is_(None)) | (ProcessingItem.persistence_next_attempt_at <= now)
            ),
        )
        .with_for_update(skip_locked=True)
        .all()
    )

    for item in retryable_items:
        item.status = "PERSISTING"
        item.persistence_generation += 1
        item.persistence_claimed_by = None
        item.persistence_lease_expires_at = None
        item.persistence_next_attempt_at = None
        recovered_count += 1

    db.commit()
    return recovered_count


def reconcile_persistence_outcomes(
    db: Session,
    client: Optional[DBWriterClient] = None,
) -> int:
    """Reconciles items in PERSIST_OUTCOME_UNKNOWN using Section 8 8-Step Boundary.

    Boundary:
      1. Select and claim reconciliation candidate with SKIP LOCKED.
      2. Persist reconciliation token & lease in Platform DB.
      3. Commit Platform DB transaction before network GET call.
      4. Call Database Writer GET /internal/writes/{key} OUTSIDE Platform DB transaction.
      5. Open NEW Platform DB transaction.
      6. Lock item with FOR UPDATE.
      7. Verify reconciliation claim token and status == PERSIST_OUTCOME_UNKNOWN.
      8. Finalize guarded result.
    """
    now = datetime.now(timezone.utc)

    # Step 1: Select candidate
    candidates = (
        db.query(ProcessingItem)
        .filter(
            ProcessingItem.status == "PERSIST_OUTCOME_UNKNOWN",
            (
                (ProcessingItem.persistence_claimed_by.is_(None)) | (ProcessingItem.persistence_lease_expires_at < now)
            ),
        )
        .with_for_update(skip_locked=True)
        .all()
    )

    if not candidates:
        return 0

    reconciled_count = 0
    if client is None:
        client = DBWriterClient()

    for candidate in candidates:
        if not candidate.writer_idempotency_key:
            continue

        item_id = candidate.id
        writer_key = candidate.writer_idempotency_key
        corr_id = candidate.correlation_id

        # Step 2: Set claim token, claim kind, and lease
        rec_token = f"rec_{uuid.uuid4()}"
        candidate.persistence_claimed_by = rec_token
        candidate.persistence_claim_kind = "RECONCILIATION"
        candidate.persistence_lease_expires_at = now + timedelta(seconds=LEASE_DURATION_SECONDS)

        # Step 3: Commit claim BEFORE network GET
        db.commit()

        # Step 4: Call GET outside Platform DB transaction
        res = client.get_write_status(writer_key, correlation_id=corr_id)
        status_code = res.get("status")

        # Step 5 & 6: Open new transaction and lock item for update
        item_locked = (
            db.query(ProcessingItem)
            .filter(ProcessingItem.id == item_id)
            .with_for_update()
            .first()
        )

        if not item_locked or item_locked.status != "PERSIST_OUTCOME_UNKNOWN":
            logger.info(f"Reconciliation state changed for item {item_id}")
            continue

        # Step 7: Verify claim token
        if item_locked.persistence_claimed_by != rec_token:
            logger.warning(f"Reconciliation token mismatch for item {item_id}: expected {rec_token}")
            continue

        now_final = datetime.now(timezone.utc)
        gen = item_locked.persistence_generation

        # Step 8: Finalize result
        if status_code == "COMMITTED":
            item_locked.status = "COMPLETED"
            item_locked.external_operation_status = "COMMITTED"
            item_locked.completed_at = now_final
            item_locked.claimed_by = None
            item_locked.claimed_by = None
            item_locked.heartbeat_at = None
            item_locked.lease_expires_at = None
            item_locked.persistence_claimed_by = None
            item_locked.persistence_claim_kind = None
            item_locked.persistence_lease_expires_at = None

            sp = db.begin_nested()
            try:
                db.add(
                    Execution(
                        processing_item_id=item_locked.id,
                        event_id=item_locked.event_id,
                        correlation_id=item_locked.correlation_id,
                        component="ORCHESTRATOR",
                        operation="PERSISTENCE_RECONCILED_COMMITTED",
                        external_reference=res.get("committed_record_id"),
                        operation_idempotency_key=f"{item_locked.id}:PERSISTENCE_RECONCILED_COMMITTED:{writer_key}:{gen}",
                        status="SUCCESS",
                        effect_status="ACKNOWLEDGED",
                        attempt=max(1, item_locked.persistence_attempt_count),
                        started_at=now_final,
                        completed_at=now_final,
                    )
                )
                sp.commit()
            except IntegrityError:
                sp.rollback()
            reconciled_count += 1

        elif status_code == "REJECTED":
            item_locked.status = "PERSISTENCE_FAILED"
            item_locked.external_operation_status = "REJECTED"
            item_locked.error_code = res.get("error_code", "INVALID_BUSINESS_PAYLOAD")
            item_locked.claimed_by = None
            item_locked.heartbeat_at = None
            item_locked.lease_expires_at = None
            item_locked.persistence_claimed_by = None
            item_locked.persistence_claim_kind = None
            item_locked.persistence_lease_expires_at = None

            sp = db.begin_nested()
            try:
                db.add(
                    Execution(
                        processing_item_id=item_locked.id,
                        event_id=item_locked.event_id,
                        correlation_id=item_locked.correlation_id,
                        component="ORCHESTRATOR",
                        operation="PERSISTENCE_RECONCILED_REJECTED",
                        external_reference=writer_key,
                        operation_idempotency_key=f"{item_locked.id}:PERSISTENCE_RECONCILED_REJECTED:{writer_key}:{gen}",
                        status="FAILED",
                        effect_status="FAILED",
                        attempt=max(1, item_locked.persistence_attempt_count),
                        started_at=now_final,
                        completed_at=now_final,
                        error_code=item_locked.error_code,
                    )
                )
                sp.commit()
            except IntegrityError:
                sp.rollback()
            reconciled_count += 1
        else:
            # Network call failed or unknown status -> release claim token for next sweep
            item_locked.persistence_claimed_by = None
            item_locked.persistence_claim_kind = None
            item_locked.persistence_lease_expires_at = None

        db.commit()

    return reconciled_count
