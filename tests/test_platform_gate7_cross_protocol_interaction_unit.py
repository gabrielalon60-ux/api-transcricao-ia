from __future__ import annotations

import inspect

from orchestrator.repositories.queue_repository import (
    lock_or_create_conversation_counter,
)
from orchestrator.services import enterprise_command_service, user_interaction_service


def test_shared_lock_is_race_safe_create_then_for_update() -> None:
    source = inspect.getsource(lock_or_create_conversation_counter)
    assert "ON CONFLICT" in source
    assert ".with_for_update()" in source


def test_both_open_protocols_use_shared_counter_lock() -> None:
    prompt = inspect.getsource(user_interaction_service.dispatch_user_prompt)
    command = inspect.getsource(
        enterprise_command_service.open_enterprise_command_session
    )
    assert "lock_or_create_conversation_counter" in prompt
    assert "lock_or_create_conversation_counter" in command


def test_ready_claim_has_query_and_locked_command_barrier() -> None:
    from orchestrator.services.fifo_worker_service import claim_next_ready_item

    source = inspect.getsource(claim_next_ready_item)
    assert "command_barrier" in source
    assert "lock_or_create_conversation_counter" in source


def test_command_answer_does_not_reuse_user_answer() -> None:
    source = inspect.getsource(
        enterprise_command_service.apply_enterprise_command_answer
    )
    assert "EnterpriseCommandAnswer" in source
    assert "UserAnswer" not in source
