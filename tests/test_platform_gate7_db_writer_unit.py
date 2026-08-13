from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from db_writer.df_adapter import (
    DestinationRejected,
    ExpenseWrite,
    LocalDFAdapter,
    normalize_cnpj,
)
from db_writer import main
from db_writer.canonicalizer import canonicalize_payload
from db_writer.main import (
    WriteRequest,
    WriterDeadlineExceeded,
    _apply_statement_budget,
    _lookup_ledger_after_idempotency_race,
    _parse_v2_amount,
    _validate_v2_payload,
    write_business_record,
)


def _v2_body() -> dict:
    return {
        "idempotency_key": "write_item-1",
        "processing_item_id": "item-1",
        "organization_id": "org",
        "instance_id": "inst",
        "user_id": "user",
        "correlation_id": "corr",
        "document_type": "invoice",
        "schema_version": "2.0",
        "payload": {
            "amount": "10.00",
            "direction": "expense",
            "instance_id": "inst",
            "organization_id": "org",
            "processing_item_id": "item-1",
            "user_id": "user",
            "schema_version": "2.0",
            "transaction_date": datetime.now(timezone.utc).isoformat(),
            "date_source": "DOCUMENT",
            "enterprise_id": "9a47e223-9862-4fe3-af67-54a19d87ff90",
            "supplier_cnpj_snapshot": "12.345.678/0001-90",
            "origin": "WHATSAPP",
        },
    }


def test_v2_contract_accepts_required_fields() -> None:
    request = WriteRequest.model_validate(_v2_body())
    assert request.schema_version == request.payload.schema_version == "2.0"


def test_v2_contract_forbids_unknown_fields() -> None:
    body = _v2_body()
    body["payload"]["sql"] = "secret"
    with pytest.raises(ValidationError):
        WriteRequest.model_validate(body)


def test_supplier_cnpj_normalization() -> None:
    assert normalize_cnpj("12.345.678/0001-90") == "12345678000190"


def test_invalid_supplier_cnpj_fails_closed() -> None:
    with pytest.raises(DestinationRejected, match="INVALID_SUPPLIER_CNPJ"):
        normalize_cnpj("123")


def test_duplicate_supplier_lookup_fails_closed_before_insert() -> None:
    class Query:
        def __init__(self, result):
            self.result = result

        def filter(self, *args):
            return self

        def first(self):
            return self.result

        def limit(self, count):
            return self

        def all(self):
            return self.result

    class DB:
        def __init__(self):
            self.calls = 0
            self.added = []

        def query(self, model):
            self.calls += 1
            return Query(
                SimpleNamespace(id="enterprise")
                if self.calls == 1
                else [SimpleNamespace(id="a"), SimpleNamespace(id="b")]
            )

        def add(self, value):
            self.added.append(value)

        def flush(self):
            pass

    db = DB()
    with pytest.raises(DestinationRejected, match="DUPLICATE_SUPPLIER_CNPJ"):
        LocalDFAdapter().insert_expense(
            db,  # type: ignore[arg-type]
            ExpenseWrite(
                amount=Decimal("1.00"),
                transaction_date=datetime.now(timezone.utc),
                enterprise_id="9a47e223-9862-4fe3-af67-54a19d87ff90",
                supplier_cnpj_snapshot="12345678000190",
                origin="WHATSAPP",
                processing_item_id="item",
            ),
        )
    assert db.added == []


@pytest.mark.parametrize(
    "value",
    [1.25, "NaN", "Infinity", "-Infinity", "1.234", "0", "-1"],
)
def test_v2_amount_rejects_noncanonical_values(value) -> None:
    assert _parse_v2_amount(value) is None


@pytest.mark.parametrize("value", ["1", "1.2", "1.20", "999999.99"])
def test_v2_amount_accepts_finite_positive_decimal_text(value) -> None:
    assert _parse_v2_amount(value) == Decimal(value)


def test_writer_deadline_refuses_new_db_operation(monkeypatch) -> None:
    class DB:
        operations = 0

        def get_bind(self):
            self.operations += 1
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        def execute(self, *_args, **_kwargs):
            self.operations += 1

    db = DB()
    monkeypatch.setattr(main, "monotonic", lambda: 9.0)
    with pytest.raises(WriterDeadlineExceeded):
        _apply_statement_budget(db, deadline=8.0)  # type: ignore[arg-type]
    assert db.operations == 0


@pytest.mark.parametrize("value", [1.25, "NaN", "Infinity", "-Infinity", "1.234"])
def test_invalid_v2_amount_is_rejected_before_hash_or_database(
    value, monkeypatch
) -> None:
    body = _v2_body()
    body["payload"]["amount"] = value
    request = WriteRequest.model_validate(body)
    hash_calls: list[dict] = []
    monkeypatch.setattr(
        main,
        "canonicalize_payload",
        lambda payload: hash_calls.append(payload) or "unexpected",
    )

    class NoDatabase:
        def __getattr__(self, name):
            raise AssertionError(f"database operation attempted: {name}")

    response = write_business_record(request, NoDatabase(), "token")  # type: ignore[arg-type]
    assert response.status == "REJECTED"
    assert response.error_code == "INVALID_BUSINESS_PAYLOAD"
    assert hash_calls == []


def test_malformed_supplier_is_rejected_before_hash_or_database(monkeypatch) -> None:
    body = _v2_body()
    body["payload"]["supplier_cnpj_snapshot"] = "abc12.345.678/0001-90"
    request = WriteRequest.model_validate(body)
    hash_calls: list[dict] = []
    monkeypatch.setattr(
        main,
        "canonicalize_payload",
        lambda payload: hash_calls.append(payload) or "unexpected",
    )

    class NoDatabase:
        def __getattr__(self, name):
            raise AssertionError(f"database operation attempted: {name}")

    response = write_business_record(request, NoDatabase(), "token")  # type: ignore[arg-type]
    assert response.status == "REJECTED"
    assert response.error_code == "INVALID_SUPPLIER_CNPJ"
    assert hash_calls == []


def test_v2_canonical_hash_normalizes_amount_and_supplier_variants() -> None:
    first = _v2_body()
    second = _v2_body()
    second["payload"]["transaction_date"] = first["payload"]["transaction_date"]
    first["payload"]["amount"] = "10"
    second["payload"]["amount"] = "10.00"
    second["payload"]["supplier_cnpj_snapshot"] = "12345678000190"
    first_validated = _validate_v2_payload(WriteRequest.model_validate(first))
    second_validated = _validate_v2_payload(WriteRequest.model_validate(second))
    assert first_validated.amount == second_validated.amount == Decimal("10")
    assert first_validated.supplier_cnpj_snapshot == "12345678000190"
    assert first_validated.canonical_payload["amount"] == "10.00"
    assert canonicalize_payload(first_validated.canonical_payload) == canonicalize_payload(
        second_validated.canonical_payload
    )


def test_v2_materially_different_canonical_request_hashes_differently() -> None:
    first = _v2_body()
    second = _v2_body()
    second["payload"]["transaction_date"] = first["payload"]["transaction_date"]
    second["payload"]["amount"] = "10.01"
    first_hash = canonicalize_payload(
        _validate_v2_payload(WriteRequest.model_validate(first)).canonical_payload
    )
    second_hash = canonicalize_payload(
        _validate_v2_payload(WriteRequest.model_validate(second)).canonical_payload
    )
    assert first_hash != second_hash


def test_post_race_lookup_uses_remaining_budget(monkeypatch) -> None:
    class Query:
        def filter(self, *_args):
            return self

        def first(self):
            return "ledger"

    class DB:
        query_calls = 0
        statement_budget_calls = 0

        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        def execute(self, *_args, **_kwargs):
            self.statement_budget_calls += 1

        def query(self, *_args):
            self.query_calls += 1
            return Query()

    db = DB()
    monkeypatch.setattr(main, "monotonic", lambda: 1.0)
    assert _lookup_ledger_after_idempotency_race(db, "key", 2.0) == "ledger"  # type: ignore[arg-type]
    assert db.query_calls == 1
    assert db.statement_budget_calls == 1


def test_post_race_lookup_does_not_start_after_deadline(monkeypatch) -> None:
    class DB:
        operations = 0

        def get_bind(self):
            self.operations += 1
            raise AssertionError("statement budget should stop before bind lookup")

        def query(self, *_args):
            self.operations += 1
            raise AssertionError("ledger lookup must not start")

    db = DB()
    monkeypatch.setattr(main, "monotonic", lambda: 2.0)
    with pytest.raises(WriterDeadlineExceeded):
        _lookup_ledger_after_idempotency_race(db, "key", 2.0)  # type: ignore[arg-type]
    assert db.operations == 0
