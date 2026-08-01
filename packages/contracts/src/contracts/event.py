from pydantic import BaseModel
from typing import Optional


class InstanceInfo(BaseModel):
    external_id: str
    receiver_phone: str


class MediaInfo(BaseModel):
    mime_type: str
    filename: Optional[str] = None
    size: int
    transient_reference: str


class NormalizedEvent(BaseModel):
    correlation_id: str
    provider: str
    external_instance_id: str
    external_message_id: str
    organization_id: Optional[str] = None
    instance_id: Optional[str] = None
    user_id: Optional[str] = None
    message_type: str  # text, image, pdf
    message_timestamp: str  # ISO-8601
    text: Optional[str] = None
    media: Optional[MediaInfo] = None
