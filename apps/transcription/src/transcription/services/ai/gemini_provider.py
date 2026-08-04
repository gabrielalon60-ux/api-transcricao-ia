import asyncio
import json
import os
import re
from decimal import Decimal, ROUND_HALF_UP
from google import genai
from google.genai import types as genai_types
from transcription.services.ai.provider import AIProvider, ExtractionResult
from transcription.core.config import get_settings
from transcription.core.logging import get_logger
from transcription.services.prompt_service import PromptService

logger = get_logger(__name__)

# Gemini pricing per 1M tokens (gemini-2.0-flash-lite, as of 2025)
GEMINI_INPUT_COST_PER_1M = 0.075  # USD
GEMINI_OUTPUT_COST_PER_1M = 0.30  # USD


class GeminiProvider(AIProvider):
    """Google Gemini implementation of the AI provider interface (google-genai SDK)."""

    def __init__(self):
        settings = get_settings()
        self.client = genai.Client(api_key=settings.gemini_api_key)
        self.model_name = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
        logger.info(f"GeminiProvider initialized with model: {self.model_name}")

    async def extract(self, image_bytes: bytes) -> ExtractionResult:
        """Send image to Gemini. Parse JSON response."""
        logger.info(f"Sending extraction request to Gemini ({self.model_name}).")

        # Detect image MIME type from magic bytes
        mime_type = self._detect_mime(image_bytes)

        # Build content parts using new google-genai SDK
        image_part = genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type)

        contents = [genai_types.Content(parts=[image_part], role="user")]

        config = genai_types.GenerateContentConfig(
            system_instruction=PromptService.load_prompt(),
            response_mime_type="application/json",
        )

        # Run sync SDK call in thread pool to avoid blocking async loop
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self.client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=config,
            ),
        )

        raw_text = response.text.strip() if response.text else ""
        logger.debug(f"Gemini raw response: {raw_text[:200]}")

        data = self._parse_json(raw_text)

        # Token counts — guard against None (metadata absent on some model responses)
        usage = response.usage_metadata
        input_tokens = usage.prompt_token_count if usage and usage.prompt_token_count is not None else None
        output_tokens = usage.candidates_token_count if usage and usage.candidates_token_count is not None else None
        total_tokens = usage.total_token_count if usage and usage.total_token_count is not None else None
        estimated_cost = self._calc_cost(input_tokens, output_tokens)

        logger.info(
            f"Gemini extraction complete. "
            f"Tokens in={input_tokens} out={output_tokens} "
            f"cost={estimated_cost}"
        )

        return ExtractionResult(
            data=data,
            model_name=self.model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            estimated_cost=estimated_cost,
            usage_status="AVAILABLE" if input_tokens is not None or output_tokens is not None else "UNAVAILABLE",
            pricing_version=get_settings().pricing_version,
            currency=get_settings().usage_currency,
        )

    def _parse_json(self, text: str) -> dict:
        """Parse JSON from AI response. Strip markdown fences if present."""
        # Strip ```json ... ``` or ``` ... ```
        cleaned = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.error(
                f"Failed to parse Gemini JSON response: {e}. Raw: {text[:300]}"
            )
            raise ValueError(f"AI returned invalid JSON: {e}") from e

    def _detect_mime(self, image_bytes: bytes) -> str:
        """Detect MIME type from magic bytes."""
        if image_bytes[:3] == b"\xff\xd8\xff":
            return "image/jpeg"
        if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
            return "image/webp"
        if image_bytes[:5] == b"%PDF-":
            return "application/pdf"
        raise ValueError("UNSUPPORTED_FILE_TYPE")

    def _calc_cost(self, input_tokens: int | None, output_tokens: int | None) -> Decimal | None:
        if input_tokens is None or output_tokens is None:
            return None
        cost = (Decimal(input_tokens) / Decimal(1_000_000)) * Decimal(str(GEMINI_INPUT_COST_PER_1M)) + (
            Decimal(output_tokens) / Decimal(1_000_000)
        ) * Decimal(str(GEMINI_OUTPUT_COST_PER_1M))
        return cost.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
