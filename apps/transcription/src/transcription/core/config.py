from functools import lru_cache

from pydantic import AliasChoices, Field
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
    max_concurrent_provider_calls: int = Field(
        default=2,
        gt=0,
        validation_alias=AliasChoices(
            "MAX_PROVIDER_CONCURRENT_CALLS",
            "MAX_CONCURRENT_PROVIDER_CALLS",
            "max_concurrent_provider_calls",
        ),
    )
    provider_acquisition_timeout_seconds: float = Field(
        default=2.0,
        gt=0,
        validation_alias=AliasChoices(
            "PROVIDER_CAPACITY_ACQUIRE_TIMEOUT_SECONDS",
            "PROVIDER_ACQUISITION_TIMEOUT_SECONDS",
            "provider_acquisition_timeout_seconds",
        ),
    )
    pricing_version: str = "2026-08-v1"
    usage_currency: str = "USD"

    # WhatsApp / WUZAPI
    wuzapi_base_url: str = ""
    wuzapi_instance: str = ""
    wuzapi_token: str = ""
    wuzapi_application_id: str = ""

    # Application
    app_env: str = Field(
        default="development",
        validation_alias=AliasChoices("APP_ENV", "ENV", "app_env"),
    )
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

    def validate_environment(self) -> None:
        environment = self.app_env.strip().lower()
        if environment not in {"development", "local", "test", "staging", "production"}:
            raise ValueError("APP_ENV is invalid")
        if environment not in {"staging", "production"}:
            return
        secrets = (
            self.gemini_api_key,
            self.api_key_hash_secret,
            self.bot_to_transcription_token or "",
        )
        if any(
            len(value) < 32
            or "placeholder" in value.lower()
            or value.lower().startswith("dev-")
            for value in secrets
        ):
            raise ValueError("Required Transcription secrets are missing or unsafe")
        if not self.transcription_database_url:
            raise ValueError("TRANSCRIPTION_DATABASE_URL is required")
        if self.max_upload_size_mb != 25:
            raise ValueError("MAX_UPLOAD_SIZE_MB must match Gate 10")
        if self.max_concurrent_validations != 4:
            raise ValueError("MAX_CONCURRENT_VALIDATIONS must match Gate 10")
        if self.max_concurrent_provider_calls != 2:
            raise ValueError("MAX_CONCURRENT_PROVIDER_CALLS must match Gate 10")
        if self.provider_acquisition_timeout_seconds != 2.0:
            raise ValueError("PROVIDER_ACQUISITION_TIMEOUT_SECONDS must match Gate 10")


@lru_cache()
def get_settings() -> Settings:
    settings = Settings()  # type: ignore[call-arg]
    settings.validate_environment()
    return settings
