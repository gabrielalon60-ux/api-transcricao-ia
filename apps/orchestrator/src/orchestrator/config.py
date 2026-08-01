from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str

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
