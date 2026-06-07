from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class ExtractionResult:
    """Result returned by any AI provider."""
    data: dict[str, Any]
    model_name: str
    input_tokens: int
    output_tokens: int
    estimated_cost: float


class AIProvider(ABC):
    """
    Abstract base class for all AI providers.
    New providers must implement the `extract` method.
    """

    @abstractmethod
    async def extract(self, image_bytes: bytes, prompt: str) -> ExtractionResult:
        """
        Analyze the image and extract information based on the prompt.

        Args:
            image_bytes: Raw image bytes.
            prompt: User-defined extraction instructions.

        Returns:
            ExtractionResult with parsed JSON data and token usage.
        """
        raise NotImplementedError
