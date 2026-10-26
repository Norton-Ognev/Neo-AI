from pydantic import BaseModel, Field
from typing import Optional, Literal

class Intent(BaseModel):
    action: Literal["create_task", "create_meeting", "list_events", "search_events", "clarify"] = Field(
        ..., description="Тип действия, который выбрал пользователь"
    )
    content: Optional[str] = Field(
        None, description="Суть задачи или встречи (для create_task и create_meeting)"
    )
    datetime_iso: Optional[str] = Field(
        None, description="Дата и время в формате ISO 8601 (например, 2026-10-18T15:00:00+03:00)"
    )
    search_query: Optional[str] = Field(
        None, description="Поисковый запрос по ключевым словам (для search_events)"
    )
    clarify_question: Optional[str] = Field(
        None, description="Короткий уточняющий вопрос пользователю, если не хватает данных для заполнения слотов"
    )
