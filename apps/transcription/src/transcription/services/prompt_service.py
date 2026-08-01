from pathlib import Path
from functools import lru_cache


class PromptService:

    PROMPT_PATH = Path("app/prompts/prompt.md")

    @classmethod
    @lru_cache
    def load_prompt(cls) -> str:

        if not cls.PROMPT_PATH.exists():
            raise RuntimeError(
                "Prompt file not found: app/prompts/prompt.md"
            )

        prompt = cls.PROMPT_PATH.read_text(
            encoding="utf-8"
        )

        if not prompt.strip():
            raise RuntimeError(
                "Prompt file is empty."
            )

        return prompt
