import uuid
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.database.session import get_db
from app.database.models import Request, RequestStatus, Application
from app.schemas.requests import RequestOut, RequestListResponse
from app.auth.api_key_auth import get_current_application
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/requests", tags=["Requests"])


@router.get(
    "",
    response_model=RequestListResponse,
    summary="List extraction requests",
    description="Returns paginated request history. Filter by application, date range, or status.",
)
def list_requests(
    application_id: Optional[uuid.UUID] = Query(None, description="Filter by application UUID."),
    status: Optional[RequestStatus] = Query(None, description="Filter by status."),
    date_from: Optional[datetime] = Query(None, description="Filter requests created after this date (ISO 8601)."),
    date_to: Optional[datetime] = Query(None, description="Filter requests created before this date (ISO 8601)."),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_app: Application = Depends(get_current_application),
    db: Session = Depends(get_db),
):
    query = db.query(Request).filter(Request.application_id == current_app.id)

    if status:
        query = query.filter(Request.status == status)
    if date_from:
        query = query.filter(Request.created_at >= date_from)
    if date_to:
        query = query.filter(Request.created_at <= date_to)

    total = query.count()
    items = query.order_by(Request.created_at.desc()).offset(offset).limit(limit).all()

    return RequestListResponse(
        total=total, 
        items=[RequestOut.model_validate(req) for req in items]
    )
