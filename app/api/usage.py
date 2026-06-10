from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database.session import get_db
from app.database.models import Application
from app.services.usage_service import UsageService
from app.schemas.usage import UsageResponse
from app.auth.api_key_auth import get_current_application
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/usage", tags=["Usage"])


@router.get(
    "",
    response_model=UsageResponse,
    summary="Get usage and cost statistics",
    description="Returns aggregate token usage and estimated costs for the current application.",
)
def get_usage(
    current_app: Application = Depends(get_current_application),
    db: Session = Depends(get_db)
):
    service = UsageService(db=db)
    return service.get_usage_summary(application_id=current_app.id)

