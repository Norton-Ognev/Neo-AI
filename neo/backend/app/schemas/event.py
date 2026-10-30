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
    """Заполняет редактируемые поля для существующего события."""

class EventCompletionUpdate(BaseModel):
    """Точечное изменение статуса: выполнено / вернуть в работу.

    Отдельная схема, чтобы не гонять полный payload события через PUT
    ради одной галочки (и не потерять поля при гонках между клиентами).
    """

    is_completed: bool = Field(
        description="Отметить событие выполненным (true) или вернуть в работу (false)"
    )

class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type: EventType
    content: str
    datetime_iso: datetime | None
    created_at: datetime
    is_completed: bool
