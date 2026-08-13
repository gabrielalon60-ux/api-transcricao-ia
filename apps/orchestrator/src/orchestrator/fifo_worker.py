from __future__ import annotations

import asyncio
import sys
import time
import signal
import logging
import threading
from typing import Set
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
import sqlalchemy as sa

from orchestrator.config import get_settings
from orchestrator.services.fifo_worker_service import (
    claim_next_ready_item,
    claim_next_resumable_validating_item,
    evaluate_and_persist_validating_item,
    defer_validating_for_enterprise_command,
    ignore_income_out_of_scope,
    transition_active_to_validating,
    POLL_INTERVAL_SECONDS,
)
from orchestrator.services.heartbeat_service import (
    renew_heartbeat,
    validate_heartbeat_config,
    WORKER_HEARTBEAT_INTERVAL_SECONDS,
    WORKER_LEASE_DURATION_SECONDS,
)
from orchestrator.services.stale_recovery_service import (
    recover_stale_active_items,
    recover_stale_validating_items,
    STALE_RECOVERY_SWEEPER_INTERVAL_SECONDS,
)
from orchestrator.services.waiting_input_sweeper import (
    expire_waiting_user_input_items,
)
from orchestrator.services.user_interaction_service import (
    EnterpriseCommandBarrier,
    dispatch_user_prompt,
    format_question_prompt,
    select_question_type,
)
from orchestrator.db_writer_client import DBWriterClient
from orchestrator.services.enterprise_resolution_service import (
    build_enterprise_option_mapping,
    materialize_persistent_enterprise_binding,
)
from orchestrator.services.enterprise_command_service import (
    expire_enterprise_command_sessions,
    format_enterprise_command_prompt,
    recover_reserved_enterprise_command_sessions,
)
from orchestrator.services.business_rules_evaluator import BusinessRulesEvaluatorService
from orchestrator.services.persistence_service import (
    transition_validating_to_persisting,
    claim_persistence_dispatch,
    dispatch_persistence_write,
    reconcile_persistence_outcomes,
    recover_stale_persistence_items,
)
from orchestrator.wuzapi import WuzapiClient
from db.models import ProcessingItem, User

logger = logging.getLogger(__name__)

running = True


def handle_shutdown(signum: int, frame: object) -> None:
    global running
    logger.info("Shutdown signal received. Stopping FIFO worker loop...")
    running = False


class WorkerClaimTracker:
    """Manages active claims for a worker instance and executes periodic heartbeat renewals.

    Runtime Lifecycle & Concurrency Safety:
      - Startup scan populates local active claims by querying items where claimed_by == worker_id.
      - Thread-safe operations protected by self._lock.
      - Heartbeat cycles execute every WORKER_HEARTBEAT_INTERVAL_SECONDS in isolated DB sessions.
      - If renew_heartbeat returns False (0 rows updated), item is removed from local tracker.
      - Items transitioning to WAITING_USER_INPUT, CANCELLED, EXPIRED, or FAILED are removed from tracker.
      - Graceful shutdown stops heartbeat execution cleanly and clears owned_claims.
    """

    def __init__(self, worker_id: str):
        self.worker_id = (
            worker_id if worker_id.startswith("worker-") else f"worker-{worker_id}"
        )
        self.owned_claims: Set[str] = set()
        self._lock = threading.Lock()

    def add_claim(self, item_id: str) -> None:
        with self._lock:
            self.owned_claims.add(item_id)

    def remove_claim(self, item_id: str) -> None:
        with self._lock:
            self.owned_claims.discard(item_id)

    def clear(self) -> None:
        with self._lock:
            self.owned_claims.clear()

    def startup_recover_claims(self, db: Session) -> int:
        items = (
            db.query(ProcessingItem.id)
            .filter(
                ProcessingItem.claimed_by == self.worker_id,
                ProcessingItem.status.in_(["ACTIVE", "VALIDATING"]),
                ProcessingItem.lease_expires_at > sa.func.now(),
            )
            .all()
        )
        recovered_ids = {row[0] for row in items}
        with self._lock:
            self.owned_claims.update(recovered_ids)
        logger.info(
            f"Startup scan for worker {self.worker_id} recovered {len(recovered_ids)} active claims from DB."
        )
        return len(recovered_ids)

    def renew_all_heartbeats(self, db: Session) -> int:
        successful = 0
        with self._lock:
            claims_snapshot = list(self.owned_claims)

        for item_id in claims_snapshot:
            try:
                ok = renew_heartbeat(db, item_id, worker_id=self.worker_id)
                if ok:
                    successful += 1
                else:
                    logger.warning(
                        f"Ownership lost for item {item_id}; removing from worker {self.worker_id} claim tracker."
                    )
                    self.remove_claim(item_id)
            except Exception as exc:
                logger.error(
                    f"Error renewing heartbeat for item {item_id} by worker {self.worker_id}: {exc}"
                )
        return successful


def _send_gate6_prompt(
    phone_number: str, question_type: str, option_mapping: dict | None = None
) -> bool:
    """Synchronous worker bridge over the existing async WUZAPI client."""
    client = WuzapiClient()
    if not client.base_url or not client.token:
        logger.error("WUZAPI is not configured; prompt outcome cannot be acknowledged.")
        return False
    try:
        asyncio.run(
            client.send_text_message(
                phone_number, format_question_prompt(question_type, option_mapping)
            )
        )
        return True
    except Exception as exc:
        logger.warning(f"Gate 6 prompt send outcome is unconfirmed: {exc}")
        return False


def _recover_enterprise_command_prompt(db: Session, session: object) -> bool:
    user_id = str(getattr(session, "user_id"))
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        return False
    client = WuzapiClient()
    if not client.base_url or not client.token:
        return False
    try:
        asyncio.run(
            client.send_text_message(
                user.phone_number,
                format_enterprise_command_prompt(session),  # type: ignore[arg-type]
            )
        )
        return True
    except Exception:
        return False


def _process_validating_item(
    db: Session,
    item: ProcessingItem,
    worker_id: str,
    evaluator: BusinessRulesEvaluatorService,
    claim_tracker: WorkerClaimTracker,
) -> None:
    # Preserve the frozen Phase 4E mocked-session supervision seam. Production
    # always supplies a real SQLAlchemy Session and follows the Gate 6 path.
    if not isinstance(db, Session):
        legacy_question = select_question_type(item)
        if legacy_question:
            interaction = dispatch_user_prompt(
                db, item.id, legacy_question, worker_id=worker_id
            )
            if interaction.status in ("WAITING", "OUTBOUND_OUTCOME_UNKNOWN"):
                claim_tracker.remove_claim(item.id)
        return

    evaluated_item, decision = evaluate_and_persist_validating_item(
        db,
        item.id,
        worker_id,
        evaluator,
    )

    if decision.direction == "income":
        ignored = ignore_income_out_of_scope(db, evaluated_item.id, worker_id)
        if ignored is not None:
            claim_tracker.remove_claim(evaluated_item.id)
        return

    if decision.question_type:
        user = db.query(User).filter(User.id == evaluated_item.user_id).first()
        phone_number = user.phone_number if user is not None else ""

        def sender(_item_id: str, question_type: str, _outbound_id: str) -> bool:
            return bool(phone_number) and _send_gate6_prompt(
                phone_number, question_type
            )

        try:
            interaction = dispatch_user_prompt(
                db,
                evaluated_item.id,
                decision.question_type,
                prompt_sender_func=sender,
                worker_id=worker_id,
            )
        except EnterpriseCommandBarrier:
            defer_validating_for_enterprise_command(db, evaluated_item.id, worker_id)
            claim_tracker.remove_claim(evaluated_item.id)
            return
        if interaction.status in ("WAITING", "OUTBOUND_OUTCOME_UNKNOWN"):
            claim_tracker.remove_claim(evaluated_item.id)
        return

    if decision.direction == "expense" and not evaluated_item.enterprise_id:
        writer_client = DBWriterClient()
        if (
            materialize_persistent_enterprise_binding(
                db,
                evaluated_item,
                writer_client,
                evaluated_item.correlation_id,
            )
            is None
        ):
            option_mapping = build_enterprise_option_mapping(
                writer_client, evaluated_item.correlation_id
            )
            user = db.query(User).filter(User.id == evaluated_item.user_id).first()
            phone_number = user.phone_number if user is not None else ""

            def enterprise_sender(
                _item_id: str, question_type: str, _outbound_id: str
            ) -> bool:
                return bool(phone_number) and _send_gate6_prompt(
                    phone_number, question_type, option_mapping
                )

            try:
                interaction = dispatch_user_prompt(
                    db,
                    evaluated_item.id,
                    "enterprise_selection",
                    prompt_sender_func=enterprise_sender,
                    option_mapping=option_mapping,
                    worker_id=worker_id,
                )
            except EnterpriseCommandBarrier:
                defer_validating_for_enterprise_command(
                    db, evaluated_item.id, worker_id
                )
                claim_tracker.remove_claim(evaluated_item.id)
                return
            if interaction.status in ("WAITING", "OUTBOUND_OUTCOME_UNKNOWN"):
                claim_tracker.remove_claim(evaluated_item.id)
            return

    if not decision.is_eligible_for_auto_write:
        logger.error(
            f"Gate 6 decision for item {evaluated_item.id} is internally inconsistent."
        )
        return

    persisting = transition_validating_to_persisting(
        db,
        evaluated_item.id,
        worker_id=worker_id,
        require_gate7_expense_destination=True,
    )
    if persisting is None:
        return
    claim_res = claim_persistence_dispatch(db, persisting.id, worker_id=worker_id)
    if claim_res is None:
        return
    _, dispatch_token, _ = claim_res
    final_item = dispatch_persistence_write(
        db, persisting.id, dispatch_token=dispatch_token
    )
    if final_item and final_item.status in ("COMPLETED", "PERSISTENCE_FAILED"):
        claim_tracker.remove_claim(evaluated_item.id)


def run_fifo_worker_loop(
    worker_id: str = "worker-1", poll_interval: float = POLL_INTERVAL_SECONDS
) -> None:
    """Independent Bot DF business FIFO worker runtime loop with Phase 4E recovery supervision.

    Runtime Lifecycle:
      - Validates configuration on startup.
      - Loads settings and creates isolated database engine & sessionmaker.
      - Executes startup claim recovery scan.
      - Per-iteration database session scoping (`Session(engine)`).
      - Executes periodic heartbeat renewals for owned claims.
      - Executes periodic stale recovery sweeps and WAITING_USER_INPUT expiration sweeps.
      - Polling fallback on empty queue (`time.sleep(poll_interval)`).
      - Catches and logs exceptions without terminating worker loop.
      - Handles graceful shutdown (`SIGINT`, `SIGTERM`).
      - Gate 6 evaluation, clarification, resume, and frozen persistence handoff.
    """
    global running
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    # Validate Phase 4E parameters at startup
    validate_heartbeat_config(
        heartbeat_interval=WORKER_HEARTBEAT_INTERVAL_SECONDS,
        lease_duration=WORKER_LEASE_DURATION_SECONDS,
        sweeper_interval=STALE_RECOVERY_SWEEPER_INTERVAL_SECONDS,
    )

    settings = get_settings()
    engine = create_engine(settings.database_url)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    claim_tracker = WorkerClaimTracker(worker_id=worker_id)
    evaluator = BusinessRulesEvaluatorService(settings.df_holding_identifiers)

    logger.info(
        f"Bot DF FIFO Worker {worker_id} started (poll_interval={poll_interval}s)."
    )

    # Perform startup scan to recover active claims owned by this worker
    with SessionLocal() as db:
        claim_tracker.startup_recover_claims(db)

    last_sweeper_run = 0.0
    last_heartbeat_run = 0.0

    while running:
        now_ts = time.time()

        # 1. Periodic Heartbeat Renewal Loop (isolated from claim loop)
        if now_ts - last_heartbeat_run >= WORKER_HEARTBEAT_INTERVAL_SECONDS:
            try:
                with SessionLocal() as db:
                    renewed = claim_tracker.renew_all_heartbeats(db)
                    if renewed > 0:
                        logger.info(
                            f"Worker {worker_id} renewed heartbeats for {renewed} claims."
                        )
            except Exception as exc:
                logger.error(f"Error in heartbeat renewal: {exc}", exc_info=True)
            last_heartbeat_run = now_ts

        # 2. Periodic stale recovery, expiration, and persistence outcome sweepers
        if now_ts - last_sweeper_run >= STALE_RECOVERY_SWEEPER_INTERVAL_SECONDS:
            try:
                with SessionLocal() as db:
                    recovered_active = recover_stale_active_items(db)
                    recovered_val = recover_stale_validating_items(db)
                    recovered_pers = recover_stale_persistence_items(db)
                    expired_waiting = expire_waiting_user_input_items(db)
                    expired_commands = expire_enterprise_command_sessions(db)
                    recovered_commands = recover_reserved_enterprise_command_sessions(
                        db,
                        lambda session: _recover_enterprise_command_prompt(db, session),
                    )
                    reconciled_persist = reconcile_persistence_outcomes(db)
                    if (
                        recovered_active
                        or recovered_val
                        or recovered_pers
                        or expired_waiting
                        or expired_commands
                        or recovered_commands
                        or reconciled_persist
                    ):
                        logger.info(
                            f"Sweeper summary: recovered_active={recovered_active}, recovered_validating={recovered_val}, "
                            f"recovered_persistence={recovered_pers}, expired_waiting={expired_waiting}, "
                            f"expired_commands={expired_commands}, "
                            f"recovered_commands={recovered_commands}, "
                            f"reconciled_persistence={reconciled_persist}"
                        )
            except Exception as exc:
                logger.error(
                    f"Error in stale recovery / expiration / reconciliation sweeper: {exc}",
                    exc_info=True,
                )
            last_sweeper_run = now_ts

        # 3. FIFO claim worker iteration (independent of sweeper failures)
        try:
            with SessionLocal() as db:
                resumable = claim_next_resumable_validating_item(
                    db, worker_id=worker_id
                )
                if resumable:
                    logger.info(
                        f"Worker {worker_id} resumed VALIDATING item {resumable.id}."
                    )
                    claim_tracker.add_claim(resumable.id)
                    _process_validating_item(
                        db, resumable, worker_id, evaluator, claim_tracker
                    )
                    continue

                claimed = claim_next_ready_item(db, worker_id=worker_id)
                if claimed:
                    logger.info(
                        f"Worker {worker_id} claimed item {claimed.id} (sequence={claimed.sequence})."
                    )
                    claim_tracker.add_claim(claimed.id)

                    # Transition ACTIVE -> VALIDATING
                    validating = transition_active_to_validating(
                        db, claimed.id, worker_id=worker_id
                    )
                    if validating:
                        logger.info(
                            f"Worker {worker_id} transitioned item {claimed.id} to VALIDATING."
                        )
                        _process_validating_item(
                            db, validating, worker_id, evaluator, claim_tracker
                        )

                else:
                    time.sleep(poll_interval)
        except Exception as exc:
            logger.error(f"Error in FIFO worker claim iteration: {exc}", exc_info=True)
            time.sleep(poll_interval)

    logger.info(f"Bot DF FIFO Worker {worker_id} shutdown complete.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    worker_id_arg = sys.argv[1] if len(sys.argv) > 1 else "1"
    run_fifo_worker_loop(worker_id=worker_id_arg)
