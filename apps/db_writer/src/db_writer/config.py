from __future__ import annotations

from functools import lru_cache
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DBWriterSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://postgres:postgres@localhost:55432/db_writer_gate4_test"
    db_writer_internal_token: str = "dev-db-writer-token-secret-123"
    environment: str = "development"

    @field_validator("db_writer_internal_token")
    @classmethod
    def validate_token(cls, v: str) -> str:
        if not v or len(v) < 16:
            raise ValueError("db_writer_internal_token must be at least 16 characters long.")
        return v

    def validate_environment(self) -> None:
        if self.environment != "development" and self.db_writer_internal_token == "dev-db-writer-token-secret-123":
            raise ValueError("Default development token cannot be used in non-development environments.")


@lru_cache()
def get_db_writer_settings() -> DBWriterSettings:
    settings = DBWriterSettings()
    settings.validate_environment()
    return settings
