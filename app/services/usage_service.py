from sqlalchemy.orm import Session
from sqlalchemy import func
from app.database.models import UsageLog, Request, Application
from app.schemas.usage import UsageSummary, UsagePerApplication, UsageResponse
from app.core.logging import get_logger

logger = get_logger(__name__)


class UsageService:
    def __init__(self, db: Session):
        self.db = db

    def get_usage_summary(self, application_id: str) -> UsageResponse:
        """Aggregate token usage and costs for a specific application."""

        # Global totals for this app
        totals_query = self.db.query(
            func.count(UsageLog.id).label("total_requests"),
            func.coalesce(func.sum(UsageLog.input_tokens), 0).label("total_input_tokens"),
            func.coalesce(func.sum(UsageLog.output_tokens), 0).label("total_output_tokens"),
            func.coalesce(func.sum(UsageLog.estimated_cost), 0.0).label("total_cost"),
        ).join(Request, Request.id == UsageLog.request_id).filter(Request.application_id == application_id)
        
        totals = totals_query.one()

        summary = UsageSummary(
            total_requests=totals.total_requests,
            total_input_tokens=totals.total_input_tokens,
            total_output_tokens=totals.total_output_tokens,
            total_cost=float(totals.total_cost),
        )

        # Per-application breakdown (only the current one)
        per_app_rows = (
            self.db.query(
                Application.id.label("application_id"),
                Application.name.label("application_name"),
                func.count(UsageLog.id).label("total_requests"),
                func.coalesce(func.sum(UsageLog.estimated_cost), 0.0).label("total_cost"),
                func.coalesce(func.sum(UsageLog.input_tokens), 0).label("total_input_tokens"),
                func.coalesce(func.sum(UsageLog.output_tokens), 0).label("total_output_tokens"),
            )
            .join(Request, Request.id == UsageLog.request_id)
            .join(Application, Application.id == Request.application_id)
            .filter(Application.id == application_id)
            .group_by(Application.id, Application.name)
            .all()
        )

        per_application = [
            UsagePerApplication(
                application_id=str(row.application_id),
                application_name=row.application_name,
                total_requests=row.total_requests,
                total_cost=float(row.total_cost),
                total_input_tokens=row.total_input_tokens,
                total_output_tokens=row.total_output_tokens,
            )
            for row in per_app_rows
        ]

        return UsageResponse(summary=summary, per_application=per_application)
