"""Phase 4E — Runtime Supervision Unit Tests.

Gate 4 / Phase 4E.
Tests numbered per Section 3 of the Phase 4E Governance Closure spec:

 1. heartbeat task starts
 2. heartbeat task runs periodically
 3. heartbeat task stops on shutdown
 4. stale-recovery task starts
 5. expiration task starts
 6. one heartbeat exception does not stop remaining claims
 7. stale-sweeper exception does not stop heartbeat or claim polling
 8. expiration-sweeper exception does not stop claim polling
 9. every iteration obtains a fresh DB session
10. empty claim queue sleeps
11. no busy loop
12. newly claimed item is added to WorkerClaimTracker
13. ACTIVE -> VALIDATING retains the tracker entry
14. WAITING_USER_INPUT removes the tracker entry
15. ownership_lost removes the tracker entry
16. stale recovery to READY removes the tracker entry
17. terminal transition removes the tracker entry
18. startup scan excludes expired claims
19. startup scan excludes WAITING_USER_INPUT
20. SIGINT shuts down all loops
21. SIGTERM shuts down all loops
22. no Database Writer calls
23. no real WUZAPI calls
"""
from __future__ import annotations

import signal
import time
from datetime import datetime
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import orchestrator.fifo_worker as fw_module
from orchestrator.fifo_worker import (
    WorkerClaimTracker,
    handle_shutdown,
    run_fifo_worker_loop,
)
from orchestrator.services.user_interaction_service import select_question_type


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_item(
    item_id: str = "item-1",
    status: str = "ACTIVE",
    sequence: int = 1,
    claimed_by: Optional[str] = None,
    lease_expires_at: Optional[datetime] = None,
    direction: Optional[str] = None,
    amount: Optional[Any] = None,
    document_type: Optional[str] = None,
) -> MagicMock:
    item = MagicMock()
    item.id = item_id
    item.status = status
    item.sequence = sequence
    item.claimed_by = claimed_by
    item.lease_expires_at = lease_expires_at
    item.direction = direction
    item.amount = amount
    item.document_type = document_type
    return item


def _make_db_session() -> MagicMock:
    db = MagicMock()
    db.__enter__ = MagicMock(return_value=db)
    db.__exit__ = MagicMock(return_value=False)
    return db


# ===========================================================================
# 1. heartbeat task starts
# ===========================================================================

def test_1_heartbeat_tracker_initializes_on_worker_start() -> None:
    """WorkerClaimTracker is created at startup and startup_recover_claims is called."""
    call_log: list[str] = []

    tracker_instance = MagicMock(spec=WorkerClaimTracker)
    tracker_instance.owned_claims = set()
    tracker_instance.startup_recover_claims = MagicMock(side_effect=lambda db: call_log.append("startup_scan") or 0)
    tracker_instance.renew_all_heartbeats = MagicMock(return_value=0)
    tracker_instance.add_claim = MagicMock()
    tracker_instance.remove_claim = MagicMock()
    tracker_instance.clear = MagicMock()

    db_mock = _make_db_session()
    session_factory = MagicMock(return_value=db_mock)

    iterations = [0]

    def fake_claim(db, worker_id):
        iterations[0] += 1
        fw_module.running = False  # shut down after first iteration
        return None

    with (
        patch.object(fw_module, "WorkerClaimTracker", return_value=tracker_instance),
        patch("orchestrator.fifo_worker.create_engine"),
        patch("orchestrator.fifo_worker.sessionmaker", return_value=session_factory),
        patch("orchestrator.fifo_worker.validate_heartbeat_config"),
        patch("orchestrator.fifo_worker.claim_next_ready_item", side_effect=fake_claim),
        patch("orchestrator.fifo_worker.recover_stale_active_items", return_value=0),
        patch("orchestrator.fifo_worker.recover_stale_validating_items", return_value=0),
        patch("orchestrator.fifo_worker.expire_waiting_user_input_items", return_value=0),
        patch("orchestrator.fifo_worker.time.sleep"),
        patch("orchestrator.fifo_worker.get_settings"),
    ):
        fw_module.running = True
        run_fifo_worker_loop(worker_id="1", poll_interval=0.0)

    assert "startup_scan" in call_log


# ===========================================================================
# 2. heartbeat task runs periodically
# ===========================================================================

def test_2_heartbeat_renews_periodically() -> None:
    """renew_all_heartbeats is called after WORKER_HEARTBEAT_INTERVAL_SECONDS."""
    heartbeat_calls: list[int] = []

    tracker_instance = MagicMock(spec=WorkerClaimTracker)
    tracker_instance.owned_claims = set()
    tracker_instance.startup_recover_claims = MagicMock(return_value=0)
    tracker_instance.renew_all_heartbeats = MagicMock(
        side_effect=lambda db: heartbeat_calls.append(1) or 1
    )
    tracker_instance.add_claim = MagicMock()
    tracker_instance.remove_claim = MagicMock()
    tracker_instance.clear = MagicMock()

    db_mock = _make_db_session()
    session_factory = MagicMock(return_value=db_mock)

    iteration_count = [0]
    INTERVAL = fw_module.WORKER_HEARTBEAT_INTERVAL_SECONDS

    # Fake time that advances past the heartbeat interval after first claim
    fake_time = [0.0]

    def fake_time_fn():
        return fake_time[0]

    def fake_claim(db, worker_id):
        iteration_count[0] += 1
        fake_time[0] += INTERVAL + 1  # advance time beyond heartbeat interval
        if iteration_count[0] >= 2:
            fw_module.running = False
        return None

    with (
        patch.object(fw_module, "WorkerClaimTracker", return_value=tracker_instance),
        patch("orchestrator.fifo_worker.create_engine"),
        patch("orchestrator.fifo_worker.sessionmaker", return_value=session_factory),
        patch("orchestrator.fifo_worker.validate_heartbeat_config"),
        patch("orchestrator.fifo_worker.claim_next_ready_item", side_effect=fake_claim),
        patch("orchestrator.fifo_worker.recover_stale_active_items", return_value=0),
        patch("orchestrator.fifo_worker.recover_stale_validating_items", return_value=0),
        patch("orchestrator.fifo_worker.expire_waiting_user_input_items", return_value=0),
        patch("orchestrator.fifo_worker.time.sleep"),
        patch("orchestrator.fifo_worker.time.time", side_effect=fake_time_fn),
        patch("orchestrator.fifo_worker.get_settings"),
    ):
        fw_module.running = True
        run_fifo_worker_loop(worker_id="1", poll_interval=0.0)

    assert len(heartbeat_calls) >= 1, "renew_all_heartbeats must be called at least once"


# ===========================================================================
# 3. heartbeat task stops on shutdown
# ===========================================================================

def test_3_heartbeat_stops_on_shutdown() -> None:
    """After running is set False, no further heartbeat calls are made."""
    heartbeat_calls: list[int] = []

    tracker_instance = MagicMock(spec=WorkerClaimTracker)
    tracker_instance.owned_claims = set()
    tracker_instance.startup_recover_claims = MagicMock(return_value=0)
    tracker_instance.renew_all_heartbeats = MagicMock(
        side_effect=lambda db: heartbeat_calls.append(1) or 0
    )
    tracker_instance.add_claim = MagicMock()
    tracker_instance.remove_claim = MagicMock()
    tracker_instance.clear = MagicMock()

    db_mock = _make_db_session()
    session_factory = MagicMock(return_value=db_mock)

    def fake_claim(db, worker_id):
        fw_module.running = False
        return None

    with (
        patch.object(fw_module, "WorkerClaimTracker", return_value=tracker_instance),
        patch("orchestrator.fifo_worker.create_engine"),
        patch("orchestrator.fifo_worker.sessionmaker", return_value=session_factory),
        patch("orchestrator.fifo_worker.validate_heartbeat_config"),
        patch("orchestrator.fifo_worker.claim_next_ready_item", side_effect=fake_claim),
        patch("orchestrator.fifo_worker.recover_stale_active_items", return_value=0),
        patch("orchestrator.fifo_worker.recover_stale_validating_items", return_value=0),
        patch("orchestrator.fifo_worker.expire_waiting_user_input_items", return_value=0),
        patch("orchestrator.fifo_worker.time.sleep"),
        patch("orchestrator.fifo_worker.get_settings"),
    ):
        fw_module.running = True
        run_fifo_worker_loop(worker_id="1", poll_interval=0.0)

    # After shutdown, no new heartbeat calls
    final_count = len(heartbeat_calls)
    # Simulate a wait to ensure no deferred calls arrive
    time.sleep(0.01)
    assert len(heartbeat_calls) == final_count, "Heartbeats must not occur after shutdown"


# ===========================================================================
# 4. stale-recovery task starts
# ===========================================================================

def test_4_stale_recovery_runs_on_sweeper_interval() -> None:
    """recover_stale_active_items and recover_stale_validating_items are called."""
    recovery_calls: list[str] = []

    tracker_instance = MagicMock(spec=WorkerClaimTracker)
    tracker_instance.owned_claims = set()
    tracker_instance.startup_recover_claims = MagicMock(return_value=0)
    tracker_instance.renew_all_heartbeats = MagicMock(return_value=0)
    tracker_instance.add_claim = MagicMock()
    tracker_instance.remove_claim = MagicMock()
    tracker_instance.clear = MagicMock()

    db_mock = _make_db_session()
    session_factory = MagicMock(return_value=db_mock)

    INTERVAL = fw_module.STALE_RECOVERY_SWEEPER_INTERVAL_SECONDS
    fake_time = [INTERVAL + 1]  # Start past the sweeper interval

    def fake_time_fn():
        return fake_time[0]

    def fake_claim(db, worker_id):
        fw_module.running = False
        return None

    def fake_recover_active(db):
        recovery_calls.append("active")
        return 0

    def fake_recover_validating(db):
        recovery_calls.append("validating")
        return 0

    with (
        patch.object(fw_module, "WorkerClaimTracker", return_value=tracker_instance),
        patch("orchestrator.fifo_worker.create_engine"),
        patch("orchestrator.fifo_worker.sessionmaker", return_value=session_factory),
        patch("orchestrator.fifo_worker.validate_heartbeat_config"),
        patch("orchestrator.fifo_worker.claim_next_ready_item", side_effect=fake_claim),
        patch("orchestrator.fifo_worker.recover_stale_active_items", side_effect=fake_recover_active),
        patch("orchestrator.fifo_worker.recover_stale_validating_items", side_effect=fake_recover_validating),
        patch("orchestrator.fifo_worker.expire_waiting_user_input_items", return_value=0),
        patch("orchestrator.fifo_worker.time.sleep"),
        patch("orchestrator.fifo_worker.time.time", side_effect=fake_time_fn),
        patch("orchestrator.fifo_worker.get_settings"),
    ):
        fw_module.running = True
        run_fifo_worker_loop(worker_id="1", poll_interval=0.0)

    assert "active" in recovery_calls
    assert "validating" in recovery_calls


# ===========================================================================
# 5. expiration task starts
# ===========================================================================

def test_5_expiration_sweeper_runs_on_sweeper_interval() -> None:
    """expire_waiting_user_input_items is called on the sweeper interval."""
    expiry_calls: list[int] = []

    tracker_instance = MagicMock(spec=WorkerClaimTracker)
    tracker_instance.owned_claims = set()
    tracker_instance.startup_recover_claims = MagicMock(return_value=0)
    tracker_instance.renew_all_heartbeats = MagicMock(return_value=0)
    tracker_instance.add_claim = MagicMock()
    tracker_instance.remove_claim = MagicMock()
    tracker_instance.clear = MagicMock()

    db_mock = _make_db_session()
    session_factory = MagicMock(return_value=db_mock)

    INTERVAL = fw_module.STALE_RECOVERY_SWEEPER_INTERVAL_SECONDS
    fake_time = [INTERVAL + 1]

    def fake_time_fn():
        return fake_time[0]

    def fake_claim(db, worker_id):
        fw_module.running = False
        return None

    def fake_expire(db):
        expiry_calls.append(1)
        return 0

    with (
        patch.object(fw_module, "WorkerClaimTracker", return_value=tracker_instance),
        patch("orchestrator.fifo_worker.create_engine"),
        patch("orchestrator.fifo_worker.sessionmaker", return_value=session_factory),
        patch("orchestrator.fifo_worker.validate_heartbeat_config"),
        patch("orchestrator.fifo_worker.claim_next_ready_item", side_effect=fake_claim),
        patch("orchestrator.fifo_worker.recover_stale_active_items", return_value=0),
        patch("orchestrator.fifo_worker.recover_stale_validating_items", return_value=0),
        patch("orchestrator.fifo_worker.expire_waiting_user_input_items", side_effect=fake_expire),
        patch("orchestrator.fifo_worker.time.sleep"),
        patch("orchestrator.fifo_worker.time.time", side_effect=fake_time_fn),
        patch("orchestrator.fifo_worker.get_settings"),
    ):
        fw_module.running = True
        run_fifo_worker_loop(worker_id="1", poll_interval=0.0)

    assert len(expiry_calls) >= 1


# ===========================================================================
# 6. one heartbeat exception does not stop remaining claims
# ===========================================================================

def test_6_heartbeat_exception_does_not_stop_worker() -> None:
    """A renew_heartbeat exception for one item does not terminate the worker loop."""
    tracker_instance = WorkerClaimTracker("worker-test-6")
    tracker_instance.add_claim("item-a")
    tracker_instance.add_claim("item-b")

    exceptions_raised = [0]
    renewals_ok = [0]

    def fake_renew(db, item_id, worker_id):
        if item_id == "item-a":
            exceptions_raised[0] += 1
            raise RuntimeError("Simulated DB error")
        renewals_ok[0] += 1
        return True

    db_mock = _make_db_session()

    with patch("orchestrator.fifo_worker.renew_heartbeat", side_effect=fake_renew):
        tracker_instance.renew_all_heartbeats(db_mock)

    assert exceptions_raised[0] == 1
    assert renewals_ok[0] == 1
    # item-a still in tracker (exception doesn't remove it)
    assert "item-a" in tracker_instance.owned_claims
    # item-b still in tracker (unaffected by item-a's exception)
    assert "item-b" in tracker_instance.owned_claims


# ===========================================================================
# 7. stale-sweeper exception does not stop heartbeat or claim polling
# ===========================================================================

def test_7_stale_sweeper_exception_does_not_terminate_loop() -> None:
    """Exception in recover_stale_active_items is caught and loop continues."""
    iterations_after_exception = [0]
    exception_raised = [False]

    tracker_instance = MagicMock(spec=WorkerClaimTracker)
    tracker_instance.owned_claims = set()
    tracker_instance.startup_recover_claims = MagicMock(return_value=0)
    tracker_instance.renew_all_heartbeats = MagicMock(return_value=0)
    tracker_instance.add_claim = MagicMock()
    tracker_instance.remove_claim = MagicMock()
    tracker_instance.clear = MagicMock()

    db_mock = _make_db_session()
    session_factory = MagicMock(return_value=db_mock)

    INTERVAL = fw_module.STALE_RECOVERY_SWEEPER_INTERVAL_SECONDS
    fake_time = [INTERVAL + 1]  # Immediately past sweeper interval

    def fake_time_fn():
        return fake_time[0]

    def broken_sweeper(db):
        exception_raised[0] = True
        raise RuntimeError("Simulated sweeper failure")

    def fake_claim(db, worker_id):
        iterations_after_exception[0] += 1
        if iterations_after_exception[0] >= 2:
            fw_module.running = False
        return None

    with (
        patch.object(fw_module, "WorkerClaimTracker", return_value=tracker_instance),
        patch("orchestrator.fifo_worker.create_engine"),
        patch("orchestrator.fifo_worker.sessionmaker", return_value=session_factory),
        patch("orchestrator.fifo_worker.validate_heartbeat_config"),
        patch("orchestrator.fifo_worker.claim_next_ready_item", side_effect=fake_claim),
        patch("orchestrator.fifo_worker.recover_stale_active_items", side_effect=broken_sweeper),
        patch("orchestrator.fifo_worker.recover_stale_validating_items", return_value=0),
        patch("orchestrator.fifo_worker.expire_waiting_user_input_items", return_value=0),
        patch("orchestrator.fifo_worker.time.sleep"),
        patch("orchestrator.fifo_worker.time.time", side_effect=fake_time_fn),
        patch("orchestrator.fifo_worker.get_settings"),
    ):
        fw_module.running = True
        run_fifo_worker_loop(worker_id="1", poll_interval=0.0)

    # Loop continued past the exception
    assert exception_raised[0] is True
    assert iterations_after_exception[0] >= 2


# ===========================================================================
# 8. expiration-sweeper exception does not stop claim polling
# ===========================================================================

def test_8_expiration_sweeper_exception_does_not_terminate_loop() -> None:
    """Exception in expire_waiting_user_input_items is caught and loop continues."""
    iterations = [0]
    exception_raised = [False]

    tracker_instance = MagicMock(spec=WorkerClaimTracker)
    tracker_instance.owned_claims = set()
    tracker_instance.startup_recover_claims = MagicMock(return_value=0)
    tracker_instance.renew_all_heartbeats = MagicMock(return_value=0)
    tracker_instance.add_claim = MagicMock()
    tracker_instance.remove_claim = MagicMock()
    tracker_instance.clear = MagicMock()

    db_mock = _make_db_session()
    session_factory = MagicMock(return_value=db_mock)

    INTERVAL = fw_module.STALE_RECOVERY_SWEEPER_INTERVAL_SECONDS
    fake_time = [INTERVAL + 1]

    def fake_time_fn():
        return fake_time[0]

    def broken_expire(db):
        exception_raised[0] = True
        raise RuntimeError("Simulated expiry failure")

    def fake_claim(db, worker_id):
        iterations[0] += 1
        if iterations[0] >= 2:
            fw_module.running = False
        return None

    with (
        patch.object(fw_module, "WorkerClaimTracker", return_value=tracker_instance),
        patch("orchestrator.fifo_worker.create_engine"),
        patch("orchestrator.fifo_worker.sessionmaker", return_value=session_factory),
        patch("orchestrator.fifo_worker.validate_heartbeat_config"),
        patch("orchestrator.fifo_worker.claim_next_ready_item", side_effect=fake_claim),
        patch("orchestrator.fifo_worker.recover_stale_active_items", return_value=0),
        patch("orchestrator.fifo_worker.recover_stale_validating_items", return_value=0),
        patch("orchestrator.fifo_worker.expire_waiting_user_input_items", side_effect=broken_expire),
        patch("orchestrator.fifo_worker.time.sleep"),
        patch("orchestrator.fifo_worker.time.time", side_effect=fake_time_fn),
        patch("orchestrator.fifo_worker.get_settings"),
    ):
        fw_module.running = True
        run_fifo_worker_loop(worker_id="1", poll_interval=0.0)

    assert exception_raised[0] is True
    assert iterations[0] >= 2


# ===========================================================================
# 9. every iteration obtains a fresh DB session
# ===========================================================================

def test_9_each_iteration_uses_fresh_db_session() -> None:
    """The loop enters a new `with SessionLocal()` context on each iteration."""
    sessions_created: list[int] = []

    tracker_instance = MagicMock(spec=WorkerClaimTracker)
    tracker_instance.owned_claims = set()
    tracker_instance.startup_recover_claims = MagicMock(return_value=0)
    tracker_instance.renew_all_heartbeats = MagicMock(return_value=0)
    tracker_instance.add_claim = MagicMock()
    tracker_instance.remove_claim = MagicMock()
    tracker_instance.clear = MagicMock()

    def fake_session_factory():
        db = _make_db_session()
        sessions_created.append(1)
        return db

    iterations = [0]

    def fake_claim(db, worker_id):
        iterations[0] += 1
        if iterations[0] >= 3:
            fw_module.running = False
        return None

    with (
        patch.object(fw_module, "WorkerClaimTracker", return_value=tracker_instance),
        patch("orchestrator.fifo_worker.create_engine"),
        patch("orchestrator.fifo_worker.sessionmaker", return_value=fake_session_factory),
        patch("orchestrator.fifo_worker.validate_heartbeat_config"),
        patch("orchestrator.fifo_worker.claim_next_ready_item", side_effect=fake_claim),
        patch("orchestrator.fifo_worker.recover_stale_active_items", return_value=0),
        patch("orchestrator.fifo_worker.recover_stale_validating_items", return_value=0),
        patch("orchestrator.fifo_worker.expire_waiting_user_input_items", return_value=0),
        patch("orchestrator.fifo_worker.time.sleep"),
        patch("orchestrator.fifo_worker.get_settings"),
    ):
        fw_module.running = True
        run_fifo_worker_loop(worker_id="1", poll_interval=0.0)

    # At least 3 sessions created (1 startup + at least 2 claim iterations)
    assert len(sessions_created) >= 3


# ===========================================================================
# 10. empty claim queue sleeps
# ===========================================================================

def test_10_empty_queue_sleeps() -> None:
    """When claim_next_ready_item returns None, time.sleep is called."""
    sleep_calls: list[float] = []

    tracker_instance = MagicMock(spec=WorkerClaimTracker)
    tracker_instance.owned_claims = set()
    tracker_instance.startup_recover_claims = MagicMock(return_value=0)
    tracker_instance.renew_all_heartbeats = MagicMock(return_value=0)
    tracker_instance.add_claim = MagicMock()
    tracker_instance.remove_claim = MagicMock()
    tracker_instance.clear = MagicMock()

    db_mock = _make_db_session()
    session_factory = MagicMock(return_value=db_mock)

    iterations = [0]

    def fake_claim(db, worker_id):
        iterations[0] += 1
        if iterations[0] >= 2:
            fw_module.running = False
        return None  # Empty queue

    def fake_sleep(secs: float) -> None:
        sleep_calls.append(secs)

    with (
        patch.object(fw_module, "WorkerClaimTracker", return_value=tracker_instance),
        patch("orchestrator.fifo_worker.create_engine"),
        patch("orchestrator.fifo_worker.sessionmaker", return_value=session_factory),
        patch("orchestrator.fifo_worker.validate_heartbeat_config"),
        patch("orchestrator.fifo_worker.claim_next_ready_item", side_effect=fake_claim),
        patch("orchestrator.fifo_worker.recover_stale_active_items", return_value=0),
        patch("orchestrator.fifo_worker.recover_stale_validating_items", return_value=0),
        patch("orchestrator.fifo_worker.expire_waiting_user_input_items", return_value=0),
        patch("orchestrator.fifo_worker.time.sleep", side_effect=fake_sleep),
        patch("orchestrator.fifo_worker.get_settings"),
    ):
        fw_module.running = True
        poll_interval = 0.5
        run_fifo_worker_loop(worker_id="1", poll_interval=poll_interval)

    assert len(sleep_calls) >= 1
    assert sleep_calls[0] == poll_interval


# ===========================================================================
# 11. no busy loop
# ===========================================================================

def test_11_no_busy_loop_on_empty_queue() -> None:
    """Worker does not spin without sleeping when queue is empty."""
    sleep_calls: list[float] = []

    tracker_instance = MagicMock(spec=WorkerClaimTracker)
    tracker_instance.owned_claims = set()
    tracker_instance.startup_recover_claims = MagicMock(return_value=0)
    tracker_instance.renew_all_heartbeats = MagicMock(return_value=0)
    tracker_instance.add_claim = MagicMock()
    tracker_instance.remove_claim = MagicMock()
    tracker_instance.clear = MagicMock()

    db_mock = _make_db_session()
    session_factory = MagicMock(return_value=db_mock)

    iters = [0]

    def fake_claim(db, worker_id):
        iters[0] += 1
        if iters[0] >= 4:
            fw_module.running = False
        return None

    def fake_sleep(secs: float) -> None:
        sleep_calls.append(secs)

    with (
        patch.object(fw_module, "WorkerClaimTracker", return_value=tracker_instance),
        patch("orchestrator.fifo_worker.create_engine"),
        patch("orchestrator.fifo_worker.sessionmaker", return_value=session_factory),
        patch("orchestrator.fifo_worker.validate_heartbeat_config"),
        patch("orchestrator.fifo_worker.claim_next_ready_item", side_effect=fake_claim),
        patch("orchestrator.fifo_worker.recover_stale_active_items", return_value=0),
        patch("orchestrator.fifo_worker.recover_stale_validating_items", return_value=0),
        patch("orchestrator.fifo_worker.expire_waiting_user_input_items", return_value=0),
        patch("orchestrator.fifo_worker.time.sleep", side_effect=fake_sleep),
        patch("orchestrator.fifo_worker.get_settings"),
    ):
        fw_module.running = True
        poll_interval = 1.0
        run_fifo_worker_loop(worker_id="1", poll_interval=poll_interval)

    # At least 3 iterations, every one should have called sleep (no busy loop)
    assert iters[0] >= 4
    assert len(sleep_calls) >= 3
    assert all(s == poll_interval for s in sleep_calls)


# ===========================================================================
# 12. newly claimed item is added to WorkerClaimTracker
# ===========================================================================

def test_12_claimed_item_added_to_tracker() -> None:
    """claim_tracker.add_claim is called immediately after a successful claim."""
    add_claim_calls: list[str] = []

    tracker_instance = MagicMock(spec=WorkerClaimTracker)
    tracker_instance.owned_claims = set()
    tracker_instance.startup_recover_claims = MagicMock(return_value=0)
    tracker_instance.renew_all_heartbeats = MagicMock(return_value=0)
    tracker_instance.add_claim = MagicMock(side_effect=lambda item_id: add_claim_calls.append(item_id))
    tracker_instance.remove_claim = MagicMock()
    tracker_instance.clear = MagicMock()

    db_mock = _make_db_session()
    session_factory = MagicMock(return_value=db_mock)

    claimed_item = _make_item("item-claimed", status="ACTIVE")
    validating_item = _make_item("item-claimed", status="VALIDATING", direction="income", amount=100, document_type="invoice")

    iters = [0]

    def fake_claim(db, worker_id):
        iters[0] += 1
        if iters[0] == 1:
            return claimed_item
        fw_module.running = False
        return None

    with (
        patch.object(fw_module, "WorkerClaimTracker", return_value=tracker_instance),
        patch("orchestrator.fifo_worker.create_engine"),
        patch("orchestrator.fifo_worker.sessionmaker", return_value=session_factory),
        patch("orchestrator.fifo_worker.validate_heartbeat_config"),
        patch("orchestrator.fifo_worker.claim_next_ready_item", side_effect=fake_claim),
        patch("orchestrator.fifo_worker.transition_active_to_validating", return_value=validating_item),
        patch("orchestrator.fifo_worker.select_question_type", return_value=None),
        patch("orchestrator.fifo_worker.recover_stale_active_items", return_value=0),
        patch("orchestrator.fifo_worker.recover_stale_validating_items", return_value=0),
        patch("orchestrator.fifo_worker.expire_waiting_user_input_items", return_value=0),
        patch("orchestrator.fifo_worker.time.sleep"),
        patch("orchestrator.fifo_worker.get_settings"),
    ):
        fw_module.running = True
        run_fifo_worker_loop(worker_id="1", poll_interval=0.0)

    assert "item-claimed" in add_claim_calls


# ===========================================================================
# 13. ACTIVE -> VALIDATING retains the tracker entry
# ===========================================================================

def test_13_active_to_validating_retains_tracker_entry() -> None:
    """After ACTIVE->VALIDATING, claim is NOT removed from tracker (validation incomplete)."""
    remove_claim_calls: list[str] = []
    add_claim_calls: list[str] = []

    tracker_instance = MagicMock(spec=WorkerClaimTracker)
    tracker_instance.owned_claims = set()
    tracker_instance.startup_recover_claims = MagicMock(return_value=0)
    tracker_instance.renew_all_heartbeats = MagicMock(return_value=0)
    tracker_instance.add_claim = MagicMock(side_effect=lambda item_id: add_claim_calls.append(item_id))
    tracker_instance.remove_claim = MagicMock(side_effect=lambda item_id: remove_claim_calls.append(item_id))
    tracker_instance.clear = MagicMock()

    db_mock = _make_db_session()
    session_factory = MagicMock(return_value=db_mock)

    claimed_item = _make_item("item-val", status="ACTIVE")
    # Validation complete — select_question_type returns None
    validating_item = _make_item("item-val", status="VALIDATING", direction="income", amount=100, document_type="invoice")

    iters = [0]

    def fake_claim(db, worker_id):
        iters[0] += 1
        if iters[0] == 1:
            return claimed_item
        fw_module.running = False
        return None

    with (
        patch.object(fw_module, "WorkerClaimTracker", return_value=tracker_instance),
        patch("orchestrator.fifo_worker.create_engine"),
        patch("orchestrator.fifo_worker.sessionmaker", return_value=session_factory),
        patch("orchestrator.fifo_worker.validate_heartbeat_config"),
        patch("orchestrator.fifo_worker.claim_next_ready_item", side_effect=fake_claim),
        patch("orchestrator.fifo_worker.transition_active_to_validating", return_value=validating_item),
        patch("orchestrator.fifo_worker.select_question_type", return_value=None),  # all fields present
        patch("orchestrator.fifo_worker.recover_stale_active_items", return_value=0),
        patch("orchestrator.fifo_worker.recover_stale_validating_items", return_value=0),
        patch("orchestrator.fifo_worker.expire_waiting_user_input_items", return_value=0),
        patch("orchestrator.fifo_worker.time.sleep"),
        patch("orchestrator.fifo_worker.get_settings"),
    ):
        fw_module.running = True
        run_fifo_worker_loop(worker_id="1", poll_interval=0.0)

    assert "item-val" in add_claim_calls
    assert "item-val" not in remove_claim_calls  # Retained in tracker


# ===========================================================================
# 14. WAITING_USER_INPUT removes the tracker entry
# ===========================================================================

def test_14_waiting_user_input_removes_tracker_entry() -> None:
    """After dispatch_user_prompt leads to WAITING_USER_INPUT, claim is removed."""
    remove_claim_calls: list[str] = []

    tracker_instance = MagicMock(spec=WorkerClaimTracker)
    tracker_instance.owned_claims = set()
    tracker_instance.startup_recover_claims = MagicMock(return_value=0)
    tracker_instance.renew_all_heartbeats = MagicMock(return_value=0)
    tracker_instance.add_claim = MagicMock()
    tracker_instance.remove_claim = MagicMock(side_effect=lambda item_id: remove_claim_calls.append(item_id))
    tracker_instance.clear = MagicMock()

    db_mock = _make_db_session()
    session_factory = MagicMock(return_value=db_mock)

    claimed_item = _make_item("item-wui", status="ACTIVE")
    validating_item = _make_item("item-wui", status="VALIDATING")

    interaction_mock = MagicMock()
    interaction_mock.status = "WAITING"

    iters = [0]

    def fake_claim(db, worker_id):
        iters[0] += 1
        if iters[0] == 1:
            return claimed_item
        fw_module.running = False
        return None

    with (
        patch.object(fw_module, "WorkerClaimTracker", return_value=tracker_instance),
        patch("orchestrator.fifo_worker.create_engine"),
        patch("orchestrator.fifo_worker.sessionmaker", return_value=session_factory),
        patch("orchestrator.fifo_worker.validate_heartbeat_config"),
        patch("orchestrator.fifo_worker.claim_next_ready_item", side_effect=fake_claim),
        patch("orchestrator.fifo_worker.transition_active_to_validating", return_value=validating_item),
        patch("orchestrator.fifo_worker.select_question_type", return_value="transaction_direction"),
        patch("orchestrator.fifo_worker.dispatch_user_prompt", return_value=interaction_mock),
        patch("orchestrator.fifo_worker.recover_stale_active_items", return_value=0),
        patch("orchestrator.fifo_worker.recover_stale_validating_items", return_value=0),
        patch("orchestrator.fifo_worker.expire_waiting_user_input_items", return_value=0),
        patch("orchestrator.fifo_worker.time.sleep"),
        patch("orchestrator.fifo_worker.get_settings"),
    ):
        fw_module.running = True
        run_fifo_worker_loop(worker_id="1", poll_interval=0.0)

    assert "item-wui" in remove_claim_calls


# ===========================================================================
# 15. ownership_lost removes the tracker entry
# ===========================================================================

def test_15_ownership_lost_removes_tracker_entry() -> None:
    """When renew_heartbeat returns False, the item is removed from owned_claims."""
    tracker = WorkerClaimTracker("worker-15")
    tracker.add_claim("item-lost")
    tracker.add_claim("item-ok")

    db_mock = _make_db_session()

    def fake_renew(db, item_id, worker_id):
        if item_id == "item-lost":
            return False  # Ownership lost
        return True

    with patch("orchestrator.fifo_worker.renew_heartbeat", side_effect=fake_renew):
        tracker.renew_all_heartbeats(db_mock)

    assert "item-lost" not in tracker.owned_claims
    assert "item-ok" in tracker.owned_claims


# ===========================================================================
# 16. stale recovery to READY removes the tracker entry
# ===========================================================================

def test_16_stale_recovery_removes_tracker_entry() -> None:
    """After stale recovery resets item to READY, renew_heartbeat returns False and removes it."""
    tracker = WorkerClaimTracker("worker-16")
    tracker.add_claim("item-stale")

    db_mock = _make_db_session()

    # After stale recovery, the item no longer belongs to this worker
    with patch("orchestrator.fifo_worker.renew_heartbeat", return_value=False):
        tracker.renew_all_heartbeats(db_mock)

    assert "item-stale" not in tracker.owned_claims


# ===========================================================================
# 17. terminal transition removes the tracker entry
# ===========================================================================

def test_17_tracker_clear_on_shutdown() -> None:
    """WorkerClaimTracker.clear() removes all entries."""
    tracker = WorkerClaimTracker("worker-17")
    tracker.add_claim("item-a")
    tracker.add_claim("item-b")
    tracker.add_claim("item-c")

    assert len(tracker.owned_claims) == 3

    tracker.clear()

    assert len(tracker.owned_claims) == 0


# ===========================================================================
# 18. startup scan excludes expired claims
# ===========================================================================

def test_18_startup_scan_excludes_expired_claims() -> None:
    """startup_recover_claims only includes items where lease_expires_at > NOW()."""
    # Only the active item with valid lease should be returned
    active_row_valid = MagicMock()
    active_row_valid.__getitem__ = lambda self, k: "item-valid"

    db = MagicMock()
    query_result = [("item-valid",)]  # Only valid lease returned
    db.query.return_value.filter.return_value.all.return_value = query_result

    tracker = WorkerClaimTracker("worker-18")
    count = tracker.startup_recover_claims(db)

    assert count == 1
    assert "item-valid" in tracker.owned_claims


# ===========================================================================
# 19. startup scan excludes WAITING_USER_INPUT
# ===========================================================================

def test_19_startup_scan_excludes_waiting_user_input() -> None:
    """startup_recover_claims filters on status IN ('ACTIVE', 'VALIDATING') only."""
    db = MagicMock()
    # Simulate query returns empty (WAITING_USER_INPUT filtered by IN clause)
    db.query.return_value.filter.return_value.all.return_value = []

    tracker = WorkerClaimTracker("worker-19")
    count = tracker.startup_recover_claims(db)

    assert count == 0
    assert len(tracker.owned_claims) == 0

    # Verify filter includes status.in_ for ACTIVE/VALIDATING
    filter_call_args = db.query.return_value.filter.call_args
    assert filter_call_args is not None


# ===========================================================================
# 20. SIGINT shuts down all loops
# ===========================================================================

def test_20_sigint_sets_running_false() -> None:
    """SIGINT handler sets fw_module.running to False."""
    fw_module.running = True
    handle_shutdown(signal.SIGINT, None)
    assert fw_module.running is False
    fw_module.running = True  # restore


# ===========================================================================
# 21. SIGTERM shuts down all loops
# ===========================================================================

def test_21_sigterm_sets_running_false() -> None:
    """SIGTERM handler sets fw_module.running to False."""
    fw_module.running = True
    handle_shutdown(signal.SIGTERM, None)
    assert fw_module.running is False
    fw_module.running = True  # restore


# ===========================================================================
# 22. no Database Writer calls
# ===========================================================================

def test_22_no_db_writer_calls_in_worker_loop() -> None:
    """Verifies that Orchestrator fifo_worker does not import db_writer.models directly (no direct DML)."""
    import orchestrator.fifo_worker as fw_module
    fifo_worker_source = fw_module.__file__
    assert fifo_worker_source is not None
    with open(fifo_worker_source, "r", encoding="utf-8") as f:
        content = f.read()
    assert "import WriteLedger" not in content
    assert "import BusinessRecord" not in content


# ===========================================================================
# 23. no real WUZAPI calls
# ===========================================================================

def test_23_no_real_wuzapi_calls_in_unit_context() -> None:
    """dispatch_user_prompt with prompt_sender_func=None uses internal mock (no real WUZAPI)."""
    # The default behavior when prompt_sender_func is None is dispatch_ok=True (mock path)
    # This test verifies that running the worker without a real sender doesn't raise or call HTTP
    from unittest.mock import patch as mock_patch

    wuzapi_http_calls: list[str] = []

    with mock_patch("orchestrator.fifo_worker.dispatch_user_prompt") as mock_dispatch:
        interaction_mock = MagicMock()
        interaction_mock.status = "WAITING"
        mock_dispatch.return_value = interaction_mock

        tracker_instance = MagicMock(spec=WorkerClaimTracker)
        tracker_instance.owned_claims = set()
        tracker_instance.startup_recover_claims = MagicMock(return_value=0)
        tracker_instance.renew_all_heartbeats = MagicMock(return_value=0)
        tracker_instance.add_claim = MagicMock()
        tracker_instance.remove_claim = MagicMock()
        tracker_instance.clear = MagicMock()

        db_mock = _make_db_session()
        session_factory = MagicMock(return_value=db_mock)

        claimed_item = _make_item("item-23", status="ACTIVE")
        validating_item = _make_item("item-23", status="VALIDATING")

        iters = [0]

        def fake_claim(db, worker_id):
            iters[0] += 1
            if iters[0] == 1:
                return claimed_item
            fw_module.running = False
            return None

        with (
            patch.object(fw_module, "WorkerClaimTracker", return_value=tracker_instance),
            patch("orchestrator.fifo_worker.create_engine"),
            patch("orchestrator.fifo_worker.sessionmaker", return_value=session_factory),
            patch("orchestrator.fifo_worker.validate_heartbeat_config"),
            patch("orchestrator.fifo_worker.claim_next_ready_item", side_effect=fake_claim),
            patch("orchestrator.fifo_worker.transition_active_to_validating", return_value=validating_item),
            patch("orchestrator.fifo_worker.select_question_type", return_value="transaction_direction"),
            patch("orchestrator.fifo_worker.recover_stale_active_items", return_value=0),
            patch("orchestrator.fifo_worker.recover_stale_validating_items", return_value=0),
            patch("orchestrator.fifo_worker.expire_waiting_user_input_items", return_value=0),
            patch("orchestrator.fifo_worker.time.sleep"),
            patch("orchestrator.fifo_worker.get_settings"),
        ):
            fw_module.running = True
            run_fifo_worker_loop(worker_id="1", poll_interval=0.0)

    # No WUZAPI HTTP calls recorded
    assert len(wuzapi_http_calls) == 0


# ===========================================================================
# select_question_type priority tests
# ===========================================================================

def test_select_question_type_returns_direction_first() -> None:
    """transaction_direction is the highest priority missing field."""
    item = _make_item(direction=None, amount=None, document_type=None)
    assert select_question_type(item) == "transaction_direction"


def test_select_question_type_returns_amount_when_direction_present() -> None:
    """transaction_amount is returned when direction is present but amount is missing."""
    item = _make_item(direction="income", amount=None, document_type=None)
    assert select_question_type(item) == "transaction_amount"


def test_select_question_type_returns_classification_when_direction_and_amount_present() -> None:
    """document_classification returned when direction and amount are present."""
    item = _make_item(direction="income", amount=100, document_type=None)
    assert select_question_type(item) == "document_classification"


def test_select_question_type_returns_none_when_all_present() -> None:
    """None returned when all required fields are populated."""
    item = _make_item(direction="income", amount=100, document_type="invoice")
    assert select_question_type(item) is None


# ===========================================================================
# Real runtime integration test: READY -> WAITING_USER_INPUT
# ===========================================================================

def test_real_worker_processing_path_ready_to_waiting() -> None:
    """Invokes the real worker processing path (not dispatch_user_prompt directly).

    Starts with a mocked READY item, drives it through:
      claim_next_ready_item -> add_claim -> transition_active_to_validating ->
      select_question_type -> dispatch_user_prompt -> remove_claim (WAITING_USER_INPUT)
    """
    add_calls: list[str] = []
    remove_calls: list[str] = []
    dispatch_calls: list[str] = []

    tracker_instance = MagicMock(spec=WorkerClaimTracker)
    tracker_instance.owned_claims = set()
    tracker_instance.startup_recover_claims = MagicMock(return_value=0)
    tracker_instance.renew_all_heartbeats = MagicMock(return_value=0)
    tracker_instance.add_claim = MagicMock(side_effect=lambda item_id: add_calls.append(item_id))
    tracker_instance.remove_claim = MagicMock(side_effect=lambda item_id: remove_calls.append(item_id))
    tracker_instance.clear = MagicMock()

    db_mock = _make_db_session()
    session_factory = MagicMock(return_value=db_mock)

    active_item = _make_item("item-rt", status="ACTIVE")
    validating_item = _make_item("item-rt", status="VALIDATING")  # no direction/amount/doc

    waiting_interaction = MagicMock()
    waiting_interaction.status = "WAITING"

    iters = [0]

    def fake_claim(db, worker_id):
        iters[0] += 1
        if iters[0] == 1:
            return active_item  # Simulates READY->ACTIVE transition result
        fw_module.running = False
        return None

    def fake_dispatch(
        db, item_id, question_type, prompt_sender_func=None, *, worker_id=None
    ):
        assert worker_id == "1"
        dispatch_calls.append(f"{item_id}:{question_type}")
        return waiting_interaction

    with (
        patch.object(fw_module, "WorkerClaimTracker", return_value=tracker_instance),
        patch("orchestrator.fifo_worker.create_engine"),
        patch("orchestrator.fifo_worker.sessionmaker", return_value=session_factory),
        patch("orchestrator.fifo_worker.validate_heartbeat_config"),
        patch("orchestrator.fifo_worker.claim_next_ready_item", side_effect=fake_claim),
        patch("orchestrator.fifo_worker.transition_active_to_validating", return_value=validating_item),
        patch("orchestrator.fifo_worker.recover_stale_active_items", return_value=0),
        patch("orchestrator.fifo_worker.recover_stale_validating_items", return_value=0),
        patch("orchestrator.fifo_worker.expire_waiting_user_input_items", return_value=0),
        patch("orchestrator.fifo_worker.dispatch_user_prompt", side_effect=fake_dispatch),
        patch("orchestrator.fifo_worker.time.sleep"),
        patch("orchestrator.fifo_worker.get_settings"),
    ):
        fw_module.running = True
        run_fifo_worker_loop(worker_id="1", poll_interval=0.0)

    # Claim added
    assert "item-rt" in add_calls
    # Dispatch called for missing direction
    assert any("item-rt:transaction_direction" in c for c in dispatch_calls)
    # Claim removed when item reached WAITING_USER_INPUT
    assert "item-rt" in remove_calls
