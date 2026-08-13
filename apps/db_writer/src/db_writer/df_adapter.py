from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Callable, Optional

from sqlalchemy.orm import Session

from db_writer.models import Enterprise, FinancialRecord, Supplier


class DestinationRejected(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ExpenseWrite:
    amount: Decimal
    transaction_date: datetime
    enterprise_id: str
    supplier_cnpj_snapshot: Optional[str]
    origin: str
    processing_item_id: str


def normalize_cnpj(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if not (
        re.fullmatch(r"\d{14}", value)
        or re.fullmatch(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", value)
    ):
        raise DestinationRejected("INVALID_SUPPLIER_CNPJ")
    return re.sub(r"\D", "", value)


class LocalDFAdapter:
    """Local Phase A destination mapping. Production adaptation is separate."""

    def list_enterprises(self, db: Session) -> list[dict[str, str]]:
        rows = db.query(Enterprise).all()
        return [{"id": str(row.id), "display_name": row.name} for row in rows]

    def insert_expense(
        self,
        db: Session,
        expense: ExpenseWrite,
        before_db_operation: Optional[Callable[[], None]] = None,
    ) -> FinancialRecord:
        check = before_db_operation or (lambda: None)
        try:
            uuid.UUID(expense.enterprise_id)
        except (ValueError, TypeError) as exc:
            raise DestinationRejected("INVALID_ENTERPRISE_ID") from exc
        check()
        enterprise = (
            db.query(Enterprise).filter(Enterprise.id == expense.enterprise_id).first()
        )
        if enterprise is None:
            raise DestinationRejected("ENTERPRISE_NOT_FOUND")

        cnpj = normalize_cnpj(expense.supplier_cnpj_snapshot)
        supplier_id: Optional[str] = None
        if cnpj is not None:
            check()
            matches = db.query(Supplier).filter(Supplier.cnpj == cnpj).limit(2).all()
            if len(matches) > 1:
                raise DestinationRejected("DUPLICATE_SUPPLIER_CNPJ")
            if matches:
                supplier_id = str(matches[0].id)

        record = FinancialRecord(
            id=str(uuid.uuid4()),
            transaction_date=expense.transaction_date,
            expense_type_id=None,
            enterprise_id=expense.enterprise_id,
            amount=expense.amount,
            supplier_id=supplier_id,
            supplier_cnpj_snapshot=cnpj,
            comments=None,
            is_deleted=False,
            deleted_at=None,
            origin=expense.origin,
            processing_item_id=expense.processing_item_id,
        )
        check()
        db.add(record)
        db.flush()
        return record
