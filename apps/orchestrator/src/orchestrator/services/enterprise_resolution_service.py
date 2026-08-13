from __future__ import annotations

import unicodedata
from typing import Optional

from sqlalchemy.orm import Session

from db.models import ProcessingItem, WhatsappChatEnterpriseBinding
from orchestrator.db_writer_client import DBWriterClient


def _sort_key(row: dict[str, str]) -> tuple[str, str]:
    normalized = unicodedata.normalize("NFKD", row["display_name"])
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return normalized.casefold(), row["id"]


def build_enterprise_option_mapping(
    client: DBWriterClient,
    correlation_id: str,
) -> dict[str, dict[str, str]]:
    rows = sorted(client.list_enterprises(correlation_id), key=_sort_key)
    return {
        str(position): {"enterprise_id": row["id"], "display_name": row["display_name"]}
        for position, row in enumerate(rows, start=1)
    }


def materialize_persistent_enterprise_binding(
    db: Session,
    item: ProcessingItem,
    client: DBWriterClient,
    correlation_id: str,
) -> Optional[str]:
    if item.enterprise_id:
        return item.enterprise_id
    binding = (
        db.query(WhatsappChatEnterpriseBinding)
        .filter(
            WhatsappChatEnterpriseBinding.organization_id == item.organization_id,
            WhatsappChatEnterpriseBinding.instance_id == item.instance_id,
            WhatsappChatEnterpriseBinding.user_id == item.user_id,
        )
        .first()
    )
    if binding is None:
        return None
    available_ids = {row["id"] for row in client.list_enterprises(correlation_id)}
    if binding.enterprise_id not in available_ids:
        return None
    item.enterprise_id = binding.enterprise_id
    db.commit()
    db.refresh(item)
    return item.enterprise_id
