from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..services.settings_service import get_settings, update_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])

class SettingsPayload(BaseModel):
    reminder_days_ahead: int = Field(ge=1, le=365)

@router.get("")
def read_settings() -> dict:
    return get_settings()

@router.put("")
def write_settings(payload: SettingsPayload) -> dict:
    return update_settings({"reminder_days_ahead": payload.reminder_days_ahead})
