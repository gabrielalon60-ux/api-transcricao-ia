import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from typing import Generator

# Lazily initialized singletons
_engine = None
_session_factory = None


def get_engine():
    """Return the SQLAlchemy engine (creates it on first call)."""
    global _engine
    if _engine is None:
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            # fallback for development or warning
            database_url = "postgresql://postgres:postgres@localhost:5432/platform"
        _engine = create_engine(
            database_url,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
        )
    return _engine


def get_session_factory():
    """Return the SQLAlchemy sessionmaker."""
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=get_engine(),
        )
    return _session_factory


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency. Yields DB session and closes after request."""
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()
