from __future__ import annotations

import inspect

from orchestrator.services import fifo_worker_service


def test_income_is_terminal_and_non_blocking() -> None:
    assert "IGNORED" in fifo_worker_service.FIFO_TERMINAL_STATES
    assert "IGNORED" not in fifo_worker_service.BLOCKING_STATES


def test_known_income_skips_amount_requirement_in_decision_composition() -> None:
    source = inspect.getsource(fifo_worker_service.evaluate_and_persist_validating_item)
    assert 'effective_direction != "income"' in source
    assert 'effective_direction == "expense"' in source


def test_ignore_transition_is_non_error_and_clears_claim() -> None:
    source = inspect.getsource(fifo_worker_service.ignore_income_out_of_scope)
    assert 'outcome_reason = "INCOME_OUT_OF_SCOPE"' in source
    assert "item.error_code" not in source
    assert "item.claimed_by = None" in source
    assert "item.lease_expires_at = None" in source


def test_income_guard_precedes_questions_and_persistence() -> None:
    from orchestrator import fifo_worker

    source = inspect.getsource(fifo_worker._process_validating_item)
    income = source.index('decision.direction == "income"')
    question = source.index("if decision.question_type")
    persistence = source.index("transition_validating_to_persisting")
    assert income < question < persistence
