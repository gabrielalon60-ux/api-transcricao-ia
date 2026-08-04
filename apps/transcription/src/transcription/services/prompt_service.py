from functools import lru_cache
from pathlib import Path

from transcription.core.config import get_settings


class PromptConfigurationError(RuntimeError):
    error_code = "SYSTEM_PROMPT_INVALID"

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__("SYSTEM_PROMPT_INVALID")


class PromptService:
    PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "prompt.md"

    @classmethod
    @lru_cache
    def load_prompt(cls) -> str:
        settings = get_settings()
        prompt_path = Path(settings.system_prompt_path) if settings.system_prompt_path else cls.PROMPT_PATH
        max_bytes = settings.max_system_prompt_size_bytes

        try:
            if not prompt_path.exists():
                raise PromptConfigurationError("missing")
            if prompt_path.is_dir():
                raise PromptConfigurationError("directory")
            if prompt_path.stat().st_size > max_bytes:
                raise PromptConfigurationError("oversized")
            with prompt_path.open("rb") as handle:
                raw = handle.read(max_bytes + 1)
        except PromptConfigurationError:
            raise
        except OSError as exc:
            raise PromptConfigurationError("unreadable") from exc

        if len(raw) > max_bytes:
            raise PromptConfigurationError("oversized")
        try:
            prompt = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise PromptConfigurationError("invalid_utf8") from exc

        if not prompt.strip():
            raise PromptConfigurationError("empty")

        return prompt

    @classmethod
    def source_classification(cls) -> str:
        try:
            return "explicit-config" if get_settings().system_prompt_path else "package-default"
        except Exception:
            return "configuration-invalid"
