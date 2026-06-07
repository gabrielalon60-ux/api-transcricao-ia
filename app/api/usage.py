from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database.session import get_db
from app.services.usage_service import UsageService
from app.schemas.usage import UsageResponse
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/usage", tags=["Usage"])


@router.get(
    "",
    response_model=UsageResponse,
    summary="Get usage and cost statistics",
    description="Returns aggregate token usage and estimated costs, global and per application.",
)
def get_usage(db: Session = Depends(get_db)):
    service = UsageService(db=db)
    return service.get_usage_summary()
