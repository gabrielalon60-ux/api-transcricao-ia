from pydantic import BaseModel


class UsageSummary(BaseModel):
    total_requests: int
    total_input_tokens: int
    total_output_tokens: int
    total_cost: float


class UsagePerApplication(BaseModel):
    application_id: str
    application_name: str
    total_requests: int
    total_cost: float
    total_input_tokens: int
    total_output_tokens: int


class UsageResponse(BaseModel):
    summary: UsageSummary
    per_application: list[UsagePerApplication]
