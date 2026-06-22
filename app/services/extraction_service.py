import time
import uuid
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.database.models import Request, Extraction, UsageLog, RequestStatus
from app.services.ai.provider import AIProvider
from app.core.logging import get_logger

logger = get_logger(__name__)


class ExtractionService:
    def __init__(self, db: Session, ai_provider: AIProvider):
        self.db = db
        self.ai_provider = ai_provider

    async def process(
        self,
        application_id: uuid.UUID,
        image_bytes: bytes,
        image_filename: str | None = None,
    ) -> tuple[uuid.UUID, dict]:
        """
        Full extraction pipeline:
        1. Create pending Request record.
        2. Call AI provider.
        3. Store Extraction + UsageLog.
        4. Update Request status.
        Returns (request_id, extracted_data).
        """
        # Step 1 — Create pending request
        request = Request(
            application_id=application_id,
            status=RequestStatus.PENDING,
        )
        self.db.add(request)
        self.db.commit()
        self.db.refresh(request)
        request_id = request.id
        logger.info(f"Request {request_id} created for app {application_id}.")

        # Step 2 — Mark as processing
        request.status = RequestStatus.PROCESSING
        self.db.commit()

        start_ms = time.monotonic()
        try:
            # Step 3 — Call AI
            result = await self.ai_provider.extract(image_bytes)

            elapsed_ms = int((time.monotonic() - start_ms) * 1000)

            # Step 4 — Persist Extraction
            extraction = Extraction(
                request_id=request_id,
                prompt="externalized",
                response_json=result.data,
                image_reference=image_filename,
            )
            self.db.add(extraction)

            # Step 5 — Persist UsageLog
            usage = UsageLog(
                request_id=request_id,
                model_name=result.model_name,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                estimated_cost=result.estimated_cost,
            )
            self.db.add(usage)

            # Step 6 — Complete Request
            request.status = RequestStatus.COMPLETED
            request.completed_at = datetime.now(timezone.utc)
            request.processing_time_ms = elapsed_ms

            self.db.commit()
            logger.info(f"Request {request_id} completed in {elapsed_ms}ms.")

            return request_id, result.data

        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start_ms) * 1000)
            logger.error(f"Request {request_id} failed: {exc}")

            request.status = RequestStatus.FAILED
            request.completed_at = datetime.now(timezone.utc)
            request.processing_time_ms = elapsed_ms
            self.db.commit()

            raise
