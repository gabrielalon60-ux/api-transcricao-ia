from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from app.database.session import get_db
from app.database.models import Application
from app.core.logging import get_logger

logger = get_logger(__name__)

bearer_scheme = HTTPBearer()


def get_current_application(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Application:
    """
    FastAPI dependency. Validates Bearer API key and returns the Application.
    Raises 401 if key is missing or invalid.
    Raises 403 if application is inactive.
    """
    from app.auth.hash import hash_api_key
    raw_api_key = credentials.credentials
    api_key_hash = hash_api_key(raw_api_key)

    app = db.query(Application).filter(
        Application.api_key_hash == api_key_hash
    ).first()

    if not app:
        logger.warning("Auth failed: invalid API key attempt.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
        )

    if not app.active:
        logger.warning(f"Auth failed: application '{app.name}' is inactive.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Application is inactive.",
        )

    return app
