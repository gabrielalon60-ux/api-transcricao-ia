import uuid
from datetime import datetime
from pydantic import BaseModel


class ApplicationOut(BaseModel):
    id: uuid.UUID
    name: str
    api_key: str
    active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class ApplicationListResponse(BaseModel):
    total: int
    items: list[ApplicationOut]


class ApplicationCreate(BaseModel):
    name: str
    api_key: str
