from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from datetime import datetime
import uuid

from app.database import get_db
from app.models import User, UserSettings
from app.auth import get_current_user

router = APIRouter()


# Default settings values
DEFAULT_SETTINGS = {
    "email_notifications": True,
    "sms_notifications": True,
    "push_notifications": True,
    "auto_extract_contacts": True,
    "auto_create_events": True,
    "auto_create_tasks": True,
    "theme": "system",
    "language": "en",
    "timezone": "UTC",
    "date_format": "YYYY-MM-DD",
    "time_format": "24h",
    "default_event_duration": 60,
    "week_starts_on": "monday",
    "show_online_status": True,
}


class SettingsResponse(BaseModel):
    id: str
    user_id: str
    email_notifications: bool
    sms_notifications: bool
    push_notifications: bool
    auto_extract_contacts: bool
    auto_create_events: bool
    auto_create_tasks: bool
    theme: str
    language: str
    timezone: str
    date_format: str
    time_format: str
    default_event_duration: int
    week_starts_on: str
    show_online_status: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SettingsUpdate(BaseModel):
    email_notifications: Optional[bool] = None
    sms_notifications: Optional[bool] = None
    push_notifications: Optional[bool] = None
    auto_extract_contacts: Optional[bool] = None
    auto_create_events: Optional[bool] = None
    auto_create_tasks: Optional[bool] = None
    theme: Optional[str] = Field(None, pattern="^(light|dark|system)$")
    language: Optional[str] = Field(None, max_length=10)
    timezone: Optional[str] = Field(None, max_length=50)
    date_format: Optional[str] = Field(None, max_length=20)
    time_format: Optional[str] = Field(None, pattern="^(12h|24h)$")
    default_event_duration: Optional[int] = Field(None, ge=15, le=480)
    week_starts_on: Optional[str] = Field(None, pattern="^(sunday|monday)$")
    show_online_status: Optional[bool] = None


def _settings_to_response(settings: UserSettings) -> SettingsResponse:
    """Convert UserSettings model to response"""
    return SettingsResponse(
        id=str(settings.id),
        user_id=str(settings.user_id),
        email_notifications=settings.email_notifications,
        sms_notifications=settings.sms_notifications,
        push_notifications=settings.push_notifications,
        auto_extract_contacts=settings.auto_extract_contacts,
        auto_create_events=settings.auto_create_events,
        auto_create_tasks=settings.auto_create_tasks,
        theme=settings.theme,
        language=settings.language,
        timezone=settings.timezone,
        date_format=settings.date_format,
        time_format=settings.time_format,
        default_event_duration=settings.default_event_duration,
        week_starts_on=settings.week_starts_on,
        show_online_status=settings.show_online_status,
        created_at=settings.created_at,
        updated_at=settings.updated_at,
    )


async def _get_or_create_settings(
    user_id: uuid.UUID, db: AsyncSession
) -> UserSettings:
    """Get existing settings or create with defaults"""
    query = select(UserSettings).where(UserSettings.user_id == user_id)
    result = await db.execute(query)
    settings = result.scalar_one_or_none()

    if not settings:
        settings = UserSettings(
            id=uuid.uuid4(),
            user_id=user_id,
            **DEFAULT_SETTINGS,
        )
        db.add(settings)
        await db.commit()
        await db.refresh(settings)

    return settings


@router.get("/", response_model=SettingsResponse)
async def get_settings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SettingsResponse:
    """Get current user's settings"""
    settings = await _get_or_create_settings(current_user.id, db)
    return _settings_to_response(settings)


@router.put("/", response_model=SettingsResponse)
async def update_settings(
    settings_data: SettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SettingsResponse:
    """Update user settings"""
    settings = await _get_or_create_settings(current_user.id, db)

    # Update only provided fields
    update_data = settings_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(settings, field, value)

    settings.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(settings)

    return _settings_to_response(settings)


@router.post("/restore-defaults", response_model=SettingsResponse)
async def restore_default_settings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SettingsResponse:
    """Restore all settings to default values"""
    settings = await _get_or_create_settings(current_user.id, db)

    # Reset all settings to defaults
    for field, value in DEFAULT_SETTINGS.items():
        setattr(settings, field, value)

    settings.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(settings)

    return _settings_to_response(settings)


@router.get("/defaults", response_model=dict)
async def get_default_settings(
    current_user: User = Depends(get_current_user),
) -> dict:
    """Get the default settings values (without applying them)"""
    return DEFAULT_SETTINGS
