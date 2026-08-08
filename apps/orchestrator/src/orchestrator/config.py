from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str

    # Queue configuration
    max_queue_items_per_conversation: int = Field(default=10, gt=0)

    # WUZAPI (outbound and webhook validation)
    wuzapi_base_url: str = ""
    wuzapi_token: str = ""
    wuzapi_webhook_secret: str = ""

    # Security Peppers/Secrets
    api_key_hash_secret: str = ""
    registration_secret_pepper: str = ""
    log_pii_hash_key: str = ""

    # Service-to-Service Token
    orchestrator_to_bot_token: str = ""
    bot_df_url: str = "http://localhost:8003"
    orchestrator_to_writer_token: str = "dev-db-writer-token-secret-123"
    db_writer_url: str = "http://localhost:8004"

    # Persistence Retry & Backoff Configuration
    persistence_max_dispatch_attempts: int = Field(default=5, gt=0)
    persistence_base_backoff_seconds: int = Field(default=5, gt=0)
    persistence_max_backoff_seconds: int = Field(default=300, gt=0)

    # Application settings
    app_env: str = "production"
    app_debug: bool = False
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
