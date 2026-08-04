from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    transcription_database_url: str | None = None

    # AI provider
    gemini_api_key: str
    gemini_model: str = "gemini-2.5-flash-lite"
    system_prompt_path: str | None = None
    max_system_prompt_size_bytes: int = Field(default=262144, gt=0)

    # Security
    api_key_hash_secret: str
    bot_to_transcription_token: str | None = None
    max_upload_size_mb: int = 10
    upload_chunk_size_bytes: int = 65536
    upload_spool_max_memory_bytes: int = 1024 * 1024
    max_image_width: int = 10000
    max_image_height: int = 10000
    max_image_pixels: int = 50_000_000
    max_pdf_pages: int = 10
    max_pdf_objects: int = 1000
    max_pdf_traversal_depth: int = 10
    upload_total_timeout_seconds: float = 30.0
    upload_chunk_read_timeout_seconds: float = 5.0
    document_validation_timeout_seconds: float = 10.0
    document_validation_termination_grace_seconds: float = 1.0
    max_concurrent_validations: int = 4
    validation_acquisition_timeout_seconds: float = 2.0
    provider_timeout_seconds: float = 30.0
    provider_max_retries: int = 2
    pricing_version: str = "2026-08-v1"
    usage_currency: str = "USD"

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

    @property
    def resolved_transcription_database_url(self) -> str:
        if self.transcription_database_url:
            return self.transcription_database_url
        raise RuntimeError("TRANSCRIPTION_DATABASE_URL is required")


@lru_cache()
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
