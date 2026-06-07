from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database.session import get_db
from app.database.models import Application
from app.schemas.applications import ApplicationOut, ApplicationListResponse, ApplicationCreate
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/applications", tags=["Applications"])


@router.get(
    "",
    response_model=ApplicationListResponse,
    summary="List all registered applications",
)
def list_applications(db: Session = Depends(get_db)):
    apps = db.query(Application).order_by(Application.created_at.desc()).all()
    return ApplicationListResponse(
        total=len(apps),
        items=[ApplicationOut.model_validate(app) for app in apps]
    )


@router.post(
    "",
    response_model=ApplicationOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new application",
)
def create_application(payload: ApplicationCreate, db: Session = Depends(get_db)):
    existing = db.query(Application).filter(Application.api_key == payload.api_key).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="API key already exists.",
        )

    app = Application(name=payload.name, api_key=payload.api_key)
    db.add(app)
    db.commit()
    db.refresh(app)
    logger.info(f"Application '{app.name}' registered (id={app.id}).")
    return app


@router.patch(
    "/{application_id}/deactivate",
    response_model=ApplicationOut,
    summary="Deactivate an application",
)
def deactivate_application(application_id: str, db: Session = Depends(get_db)):
    app = db.query(Application).filter(Application.id == application_id).first()
    if not app:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found.")
    app.active = False
    db.commit()
    db.refresh(app)
    logger.info(f"Application '{app.name}' deactivated.")
    return app
