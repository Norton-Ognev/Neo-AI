from pydantic import BaseModel, Field

from .event import EventRead

class TextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    session_id: str = Field(default="default", min_length=1, max_length=100)
    request_id: str | None = Field(default=None, max_length=100)
    speak: bool = True

    def normalized_text(self) -> str:
        return self.text.strip()

class TextResponse(BaseModel):
    action: str
    response_text: str
    event: EventRead | None = None
    events: list[EventRead] = Field(default_factory=list)
    speech_queued: bool = False
