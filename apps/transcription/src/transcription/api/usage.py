from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from transcription.database.session import get_db
from transcription.database.models import Application
from transcription.services.usage_service import UsageService
from transcription.schemas.usage import UsageResponse
from transcription.auth.api_key_auth import get_current_application
from transcription.core.logging import get_logger

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
    db: Session = Depends(get_db),
):
    service = UsageService(db=db)
    return service.get_usage_summary(application_id=str(current_app.id))
