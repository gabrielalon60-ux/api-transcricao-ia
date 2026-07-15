from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str

    # AI provider
    gemini_api_key: str
    gemini_model: str = "gemini-2.5-flash-lite"

    # Security
    api_key_hash_secret: str
    max_upload_size_mb: int = 10

    # WhatsApp / WUZAPI
    wuzapi_base_url: str = ""
    wuzapi_instance: str = ""
    wuzapi_token: str = ""
    wuzapi_application_id: str = ""

    # Application
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
    return Settings()