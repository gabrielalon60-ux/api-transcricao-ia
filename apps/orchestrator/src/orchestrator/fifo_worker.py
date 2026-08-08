from __future__ import annotations

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
    select_question_type,
    dispatch_user_prompt,
)
from orchestrator.services.persistence_service import (
    transition_validating_to_persisting,
    claim_persistence_dispatch,
    dispatch_persistence_write,
    reconcile_persistence_outcomes,
    recover_stale_persistence_items,
)
from db.models import ProcessingItem

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
        self.worker_id = worker_id if worker_id.startswith("worker-") else f"worker-{worker_id}"
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
        logger.info(f"Startup scan for worker {self.worker_id} recovered {len(recovered_ids)} active claims from DB.")
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
                    logger.warning(f"Ownership lost for item {item_id}; removing from worker {self.worker_id} claim tracker.")
                    self.remove_claim(item_id)
            except Exception as exc:
                logger.error(f"Error renewing heartbeat for item {item_id} by worker {self.worker_id}: {exc}")
        return successful


def run_fifo_worker_loop(worker_id: str = "worker-1", poll_interval: float = POLL_INTERVAL_SECONDS) -> None:
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
      - Zero Database Writer or PERSISTING interactions.
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

    logger.info(f"Bot DF FIFO Worker {worker_id} started (poll_interval={poll_interval}s).")

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
                        logger.info(f"Worker {worker_id} renewed heartbeats for {renewed} claims.")
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
                    reconciled_persist = reconcile_persistence_outcomes(db)
                    if recovered_active or recovered_val or recovered_pers or expired_waiting or reconciled_persist:
                        logger.info(
                            f"Sweeper summary: recovered_active={recovered_active}, recovered_validating={recovered_val}, "
                            f"recovered_persistence={recovered_pers}, expired_waiting={expired_waiting}, "
                            f"reconciled_persistence={reconciled_persist}"
                        )
            except Exception as exc:
                logger.error(f"Error in stale recovery / expiration / reconciliation sweeper: {exc}", exc_info=True)
            last_sweeper_run = now_ts

        # 3. FIFO claim worker iteration (independent of sweeper failures)
        try:
            with SessionLocal() as db:
                claimed = claim_next_ready_item(db, worker_id=worker_id)
                if claimed:
                    logger.info(f"Worker {worker_id} claimed item {claimed.id} (sequence={claimed.sequence}).")
                    claim_tracker.add_claim(claimed.id)

                    # Transition ACTIVE -> VALIDATING
                    validating = transition_active_to_validating(db, claimed.id, worker_id=worker_id)
                    if validating:
                        logger.info(f"Worker {worker_id} transitioned item {claimed.id} to VALIDATING.")

                        # Validation / Question Selection
                        question_type = select_question_type(validating)
                        if question_type:
                            # Missing required field — dispatch prompt to user
                            logger.info(
                                f"Worker {worker_id} dispatching prompt for item {validating.id} "
                                f"(question_type={question_type})."
                            )
                            interaction = dispatch_user_prompt(
                                db,
                                validating.id,
                                question_type,
                            )
                            if interaction and interaction.status in ("WAITING", "OUTBOUND_OUTCOME_UNKNOWN"):
                                # Item transitioned to WAITING_USER_INPUT; release claim
                                claim_tracker.remove_claim(claimed.id)
                                logger.info(
                                    f"Worker {worker_id} released claim for item {claimed.id} "
                                    f"(WAITING_USER_INPUT, question_type={question_type})."
                                )
                        else:
                            # All required fields present — validation complete -> transition to PERSISTING
                            logger.info(
                                f"Worker {worker_id} item {validating.id} validation complete; transitioning to PERSISTING."
                            )
                            persisting = transition_validating_to_persisting(db, validating.id, worker_id=worker_id)
                            if persisting:
                                claim_res = claim_persistence_dispatch(db, persisting.id, worker_id=worker_id)
                                if claim_res:
                                    _, dispatch_token, _ = claim_res
                                    final_item = dispatch_persistence_write(
                                        db, persisting.id, dispatch_token=dispatch_token
                                    )
                                    if final_item and final_item.status in ("COMPLETED", "PERSISTENCE_FAILED"):
                                        claim_tracker.remove_claim(claimed.id)
                                        logger.info(
                                            f"Worker {worker_id} item {claimed.id} reached terminal state {final_item.status}; claim released."
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
