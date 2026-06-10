from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    database_url: str

    # AI Providers
    gemini_api_key: str
    gemini_model: str = "gemini-2.5-flash-lite"

    # App
    app_env: str = "production"
    app_debug: bool = False
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    return Settings()
