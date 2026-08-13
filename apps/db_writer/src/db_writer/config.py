from __future__ import annotations

from functools import lru_cache
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class DBWriterSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", populate_by_name=True
    )

    database_url: str = Field(
        default="",
        validation_alias=AliasChoices("DF_DATABASE_URL", "df_database_url"),
    )
    db_writer_internal_token: str = "dev-db-writer-token-secret-123"
    environment: str = "development"
    allow_insecure_disposable_db: bool = False
    connect_timeout_seconds: int = 2
    lock_timeout_ms: int = 1000
    statement_timeout_ms: int = 5000
    handling_deadline_seconds: int = 8

    @field_validator("db_writer_internal_token")
    @classmethod
    def validate_token(cls, v: str) -> str:
        if not v or len(v) < 16:
            raise ValueError(
                "db_writer_internal_token must be at least 16 characters long."
            )
        return v

    def validate_environment(self) -> None:
        if (
            self.environment != "development"
            and self.db_writer_internal_token == "dev-db-writer-token-secret-123"
        ):
            raise ValueError(
                "Default development token cannot be used in non-development environments."
            )
        if not self.database_url:
            raise ValueError("DF_DATABASE_URL is required")
        try:
            url = make_url(self.database_url)
        except Exception as exc:
            raise ValueError("DF_DATABASE_URL is invalid") from exc
        sslmode = url.query.get("sslmode")
        if self.environment in {"staging", "production"}:
            if sslmode != "verify-full":
                raise ValueError("DF_DATABASE_URL requires verify-full TLS")
        elif sslmode != "verify-full" and not self.allow_insecure_disposable_db:
            raise ValueError(
                "Insecure database mode requires explicit disposable-test authorization"
            )

    def connection_args(self) -> dict[str, object]:
        return {
            "connect_timeout": self.connect_timeout_seconds,
            "options": (
                f"-c lock_timeout={self.lock_timeout_ms} "
                f"-c statement_timeout={self.statement_timeout_ms}"
            ),
        }


@lru_cache()
def get_db_writer_settings() -> DBWriterSettings:
    return DBWriterSettings()
