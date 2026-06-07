from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase, Session
from app.core.config import get_settings
from typing import Generator


class Base(DeclarativeBase):
    pass


# Lazily initialized singletons
_engine = None
_session_factory = None


def _get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
        )
    return _engine


def _get_session_factory():
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=_get_engine(),
        )
    return _session_factory


# Use `get_engine()` instead.


def get_engine():
    """Return the SQLAlchemy engine (creates it on first call)."""
    return _get_engine()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency. Yields DB session and closes after request."""
    db = _get_session_factory()()
    try:
        yield db
    finally:
        db.close()
