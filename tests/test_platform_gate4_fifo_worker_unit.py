from __future__ import annotations

from orchestrator.services.fifo_worker_service import (
    TERMINAL_STATES,
    BLOCKING_STATES,
    WORKER_LEASE_DURATION_SECONDS,
    POLL_INTERVAL_SECONDS,
    PHYSICAL_PARTIAL_INDEX_NAME,
)
from orchestrator.fifo_worker import handle_shutdown


def test_fifo_worker_constants_and_defaults():
    assert "COMPLETED" in TERMINAL_STATES
    assert "EXTRACTION_FAILED" in TERMINAL_STATES
    assert "PERSISTENCE_FAILED" in TERMINAL_STATES
    assert "FAILED" in TERMINAL_STATES
    assert "EXPIRED" in TERMINAL_STATES
    assert "CANCELLED" in TERMINAL_STATES

    assert "ACTIVE" in BLOCKING_STATES
    assert "VALIDATING" in BLOCKING_STATES
    assert "WAITING_USER_INPUT" in BLOCKING_STATES
    assert "PERSISTING" in BLOCKING_STATES

    assert WORKER_LEASE_DURATION_SECONDS == 60
    assert POLL_INTERVAL_SECONDS == 1.0
    assert PHYSICAL_PARTIAL_INDEX_NAME == "uq_processing_items_one_active_per_conversation"


def test_worker_handle_shutdown():
    import orchestrator.fifo_worker as fw
    fw.running = True
    handle_shutdown(15, None)
    assert fw.running is False
    fw.running = True  # reset for subsequent tests
