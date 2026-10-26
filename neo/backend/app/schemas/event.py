from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


EventType = Literal["task", "meeting", "reminder"]


class EventCreate(BaseModel):
    event_type: EventType
    content: str = Field(min_length=1, max_length=2000)
    datetime_iso: datetime | None = None

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Текст события не должен быть пустым")
        return value


class EventUpdate(EventCreate):
    """Complete editable fields for an existing event."""


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type: EventType
    content: str
    datetime_iso: datetime | None
    created_at: datetime
    is_completed: bool
