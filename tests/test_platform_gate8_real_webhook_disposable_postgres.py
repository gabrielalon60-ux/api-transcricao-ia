from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.models import Event, ProcessingItem, UserAnswer
from orchestrator.services.extraction_dispatcher import (
    ExtractionDispatcher,
    claim_next_received_item_for_extraction,
)
from orchestrator.services.final_notification_service import INCOME_OUT_OF_SCOPE_MESSAGE
import test_platform_gate8_e2e_disposable_postgres as e2e

import pytest
pytestmark = pytest.mark.real_pg15


# Reuse the isolated Platform/Writer lifecycle and real local Writer bridge.
engine = e2e.engine
writer_engine = e2e.writer_engine
clean = e2e.clean
writer_client = e2e.writer_client


def test_real_original_webhook_expense_replay_has_one_final_logical_outcome(
    engine, writer_engine, writer_client
) -> None:
    context = e2e.create_context(engine, writer_engine)
    external_id = f"gate8-real-replay-{uuid.uuid4()}"
    first = e2e.post_webhook(engine, context, external_message_id=external_id)
    replay = e2e.post_webhook(engine, context, external_message_id=external_id)
    assert first.status_code == replay.status_code == 200
    assert replay.json()["detail"] == "duplicate"
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Event)) == 1
        item = claim_next_received_item_for_extraction(db, "gate8-real-replay")
        assert item is not None
        item_id = item.id
        extraction = e2e.FakeExtraction(e2e.expense_normalized())
        asyncio.run(ExtractionDispatcher(extraction).process_item(db, item, b"doc"))
    assert e2e.process_next_business(engine) == item_id
    assert e2e.writer_counts(writer_engine, item_id) == (1, 1)
    assert len(e2e.notify_all(engine)) == 1
    assert not e2e.notify_all(engine)


def test_real_webhook_direction_clarification_reaches_writer_committed(
    engine, writer_engine, writer_client
) -> None:
    context = e2e.create_context(engine, writer_engine)
    item_id, sent = e2e._clarification_flow(
        engine,
        writer_engine,
        context,
        e2e.expense_normalized(ambiguous=True),
        "despesa",
    )
    assert e2e.writer_counts(writer_engine, item_id) == (1, 1)
    assert len(sent) == 1 and "Gravado com sucesso" in sent[0][0]
    with Session(engine) as db:
        answer = db.scalar(select(UserAnswer).where(UserAnswer.processing_item_id == item_id))
        assert answer is not None and answer.status == "APPLIED"


def test_real_webhook_amount_clarification_reaches_writer_committed(
    engine, writer_engine, writer_client
) -> None:
    context = e2e.create_context(engine, writer_engine)
    item_id, sent = e2e._clarification_flow(
        engine,
        writer_engine,
        context,
        e2e.expense_normalized(amount=None),
        "1.200,00",
    )
    assert e2e.writer_counts(writer_engine, item_id) == (1, 1)
    assert len(sent) == 1 and "R$ 1.200,00" in sent[0][0]


def test_real_webhook_income_runs_guard_with_physical_zero_writer_rows(
    engine, writer_engine, writer_client
) -> None:
    context = e2e.create_context(engine, writer_engine)
    item_id, _ = e2e.ingest_and_extract(engine, context, e2e.income_normalized())
    before = writer_client.write_calls
    assert e2e.process_next_business(engine) == item_id
    assert writer_client.write_calls == before
    assert e2e.writer_counts(writer_engine, item_id) == (0, 0)
    sent = e2e.notify_all(engine)
    assert len(sent) == 1 and sent[0][0] == INCOME_OUT_OF_SCOPE_MESSAGE
    with Session(engine) as db:
        item = db.get(ProcessingItem, item_id)
        assert item is not None and item.status == "IGNORED"
