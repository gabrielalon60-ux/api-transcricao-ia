from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    database_url: str

    # AI Providers
    gemini_api_key: str
    gemini_model: str = "gemini-2.5-flash-lite"

    # Security
    api_key_hash_secret: str  # Required: HMAC secret for API key hashing
    max_upload_size_mb: int = 10  # Max file upload size in MB

    # WhatsApp / WUZAPI
    wuzapi_base_url: str = ""           # e.g. http://localhost:8080
    wuzapi_instance: str = ""           # WUZAPI instance name
    wuzapi_token: str = ""              # WUZAPI API token
    wuzapi_application_id: str = ""    # UUID of Application row for WhatsApp requests

    # App
    app_env: str = "production"
    app_debug: bool = False
    log_level: str = "INFO"

    # WhatsApp / WUZAPI
    wuzapi_base_url: str = ""        # e.g. http://localhost:8080
    wuzapi_instance: str = ""        # WUZAPI instance name
    wuzapi_token: str = ""           # WUZAPI API token
    wuzapi_application_id: str = ""  # UUID of the DB Application row for WhatsApp

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra="ignore"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    return Settings()
