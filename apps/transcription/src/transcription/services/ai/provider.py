from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass
class ExtractionResult:
    """Result returned by any AI provider."""

    data: dict[str, Any]
    model_name: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None = None
    cached_tokens: int | None = None
    estimated_cost: Decimal | None = None
    provider: str = "google"
    usage_status: str | None = None
    pricing_version: str | None = None
    currency: str | None = None


class AIProvider(ABC):
    """
    Abstract base class for all AI providers.
    New providers must implement the `extract` method.
    """

    @abstractmethod
    async def extract(self, image_bytes: bytes) -> ExtractionResult:
        """
        Analyze the image and extract information based on the prompt.

        Args:
            image_bytes: Raw image bytes.

        Returns:
            ExtractionResult with parsed JSON data and token usage.
        """
        raise NotImplementedError
