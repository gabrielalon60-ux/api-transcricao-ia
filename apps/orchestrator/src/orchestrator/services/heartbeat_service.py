from __future__ import annotations

import logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session
import sqlalchemy as sa

from db.models import ProcessingItem

logger = logging.getLogger(__name__)

# Configured defaults
WORKER_HEARTBEAT_INTERVAL_SECONDS = 15
WORKER_LEASE_DURATION_SECONDS = 60


def validate_heartbeat_config(
    heartbeat_interval: float = WORKER_HEARTBEAT_INTERVAL_SECONDS,
    lease_duration: float = WORKER_LEASE_DURATION_SECONDS,
    sweeper_interval: float = 30.0,
) -> None:
    """Startup configuration validator for Phase 4E parameters."""
    if heartbeat_interval <= 0:
        raise ValueError(f"WORKER_HEARTBEAT_INTERVAL_SECONDS must be positive, got {heartbeat_interval}")
    if lease_duration <= 0:
        raise ValueError(f"WORKER_LEASE_DURATION_SECONDS must be positive, got {lease_duration}")
    if sweeper_interval <= 0:
        raise ValueError(f"STALE_RECOVERY_SWEEPER_INTERVAL_SECONDS must be positive, got {sweeper_interval}")
    if heartbeat_interval >= lease_duration:
        raise ValueError(
            f"WORKER_HEARTBEAT_INTERVAL_SECONDS ({heartbeat_interval}) must be strictly less than WORKER_LEASE_DURATION_SECONDS ({lease_duration})"
        )
    if sweeper_interval > lease_duration:
        raise ValueError(
            f"STALE_RECOVERY_SWEEPER_INTERVAL_SECONDS ({sweeper_interval}) must be less than or equal to WORKER_LEASE_DURATION_SECONDS ({lease_duration})"
        )


def _normalize_worker_id(worker_id: str) -> str:
    return worker_id if worker_id.startswith("worker-") else f"worker-{worker_id}"


def renew_heartbeat(
    db: Session,
    item_id: str,
    worker_id: str,
    lease_duration_seconds: int = WORKER_LEASE_DURATION_SECONDS,
) -> bool:
    """Atomically extends the lease of an ACTIVE or VALIDATING item currently owned by worker_id.

    Guards:
      - claimed_by == worker_id
      - status IN ('ACTIVE', 'VALIDATING')
      - lease_expires_at IS NOT NULL
      - lease_expires_at > NOW() (current valid lease)

    Returns:
      - True if heartbeat renewed (1 row updated)
      - False if ownership lost or lease expired (0 rows updated)
    """
    full_worker_id = _normalize_worker_id(worker_id)
    now = datetime.now(timezone.utc)
    interval_str = f"INTERVAL '{int(lease_duration_seconds)} seconds'"

    stmt = (
        sa.update(ProcessingItem)
        .where(
            ProcessingItem.id == item_id,
            ProcessingItem.claimed_by == full_worker_id,
            ProcessingItem.status.in_(["ACTIVE", "VALIDATING"]),
            ProcessingItem.lease_expires_at.isnot(None),
            ProcessingItem.lease_expires_at > sa.func.now(),
        )
        .values(
            heartbeat_at=now,
            lease_expires_at=sa.func.now() + sa.text(interval_str),
        )
    )

    result = db.execute(stmt)
    db.commit()

    rowcount = getattr(result, "rowcount", 0)
    if rowcount == 1:
        logger.debug(f"Worker {full_worker_id} successfully renewed heartbeat for item {item_id}")
        return True
    else:
        logger.warning(
            f"Worker {full_worker_id} heartbeat rejected for item {item_id} (lease expired, status changed, or claimed_by mismatched)"
        )
        return False
