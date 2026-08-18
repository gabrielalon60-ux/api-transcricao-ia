from functools import lru_cache
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings


_NON_PRODUCTION_ENVS = {"development", "local", "test"}
_PROTECTED_ENVS = {"staging", "production"}


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    return (
        not normalized
        or "placeholder" in normalized
        or normalized.startswith("dev-")
        or normalized in {"changeme", "change-me", "secret", "token"}
    )


class Settings(BaseSettings):
    # Database
    database_url: str

    # Queue configuration
    max_queue_items_per_conversation: int = Field(default=10, gt=0)
    max_organization_outstanding_items: int = Field(default=100, gt=0)
    max_organization_active_items: int = Field(default=20, gt=0)

    # Registration abuse control. Local/test preserve the frozen Gate 2 default;
    # release configuration must explicitly set the Gate 10 values.
    registration_max_failed_attempts: int = Field(default=3, gt=0)
    registration_window_seconds: int = Field(
        default=300,
        gt=0,
        validation_alias=AliasChoices(
            "REGISTRATION_FAILURE_WINDOW_SECONDS",
            "REGISTRATION_WINDOW_SECONDS",
            "registration_window_seconds",
        ),
    )
    registration_block_seconds: int = Field(default=300, gt=0)

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
    bot_to_transcription_token: str = "dev_bot_token_secret_123"
    transcription_service_url: str = "http://localhost:8000"
    orchestrator_to_writer_token: str = "dev-db-writer-token-secret-123"
    db_writer_url: str = "http://localhost:8004"

    # Persistence Retry & Backoff Configuration
    persistence_max_dispatch_attempts: int = Field(default=5, gt=0)
    persistence_base_backoff_seconds: int = Field(default=5, gt=0)
    persistence_max_backoff_seconds: int = Field(default=300, gt=0)

    # DF Holding CPF/CNPJ Identifiers (PRD RN-012 placeholders)
    # Normalized digit-only strings. Production replacement: G10-T01.
    df_holding_identifiers: list[str] = Field(
        default_factory=lambda: [
            "00000000000000",  # CNPJ_1
            "11111111111111",  # CNPJ_2
            "00000000000",  # CPF_1
            "11111111111",  # CPF_2
        ]
    )

    # Application settings
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

    def validate_environment(self) -> None:
        environment = self.app_env.strip().lower()
        if environment not in _NON_PRODUCTION_ENVS | _PROTECTED_ENVS:
            raise ValueError("APP_ENV must be development, local, test, staging, or production")
        if environment not in _PROTECTED_ENVS:
            return

        required_secrets = {
            "WUZAPI_TOKEN": self.wuzapi_token,
            "WUZAPI_WEBHOOK_SECRET": self.wuzapi_webhook_secret,
            "API_KEY_HASH_SECRET": self.api_key_hash_secret,
            "REGISTRATION_SECRET_PEPPER": self.registration_secret_pepper,
            "LOG_PII_HASH_KEY": self.log_pii_hash_key,
            "ORCHESTRATOR_TO_BOT_TOKEN": self.orchestrator_to_bot_token,
            "BOT_TO_TRANSCRIPTION_TOKEN": self.bot_to_transcription_token,
            "ORCHESTRATOR_TO_WRITER_TOKEN": self.orchestrator_to_writer_token,
        }
        invalid = [name for name, value in required_secrets.items() if len(value) < 32 or _is_placeholder(value)]
        if invalid:
            raise ValueError("Required production secrets are missing or unsafe")

        urls = (
            self.wuzapi_base_url,
            self.bot_df_url,
            self.transcription_service_url,
            self.db_writer_url,
        )
        if any(not value.strip() or "localhost" in value.lower() or "127.0.0.1" in value for value in urls):
            raise ValueError("Production service URLs must be explicit non-loopback URLs")

        normalized_identifiers = [value.strip() for value in self.df_holding_identifiers]
        if not normalized_identifiers or any(
            not value.isdigit()
            or len(value) not in {11, 14}
            or len(set(value)) == 1
            for value in normalized_identifiers
        ):
            raise ValueError("DF Holding identifiers must be non-placeholder CPF/CNPJ digits")

        if (
            self.registration_max_failed_attempts != 5
            or self.registration_window_seconds != 3600
            or self.registration_block_seconds != 86400
            or self.max_queue_items_per_conversation != 10
            or self.max_organization_outstanding_items != 100
            or self.max_organization_active_items != 20
        ):
            raise ValueError("Protected-environment operational limits must match Gate 10")


@lru_cache()
def get_settings() -> Settings:
    settings = Settings()  # type: ignore[call-arg]
    settings.validate_environment()
    return settings
