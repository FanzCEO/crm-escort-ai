from fastapi import APIRouter, HTTPException, status, Depends, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from sqlalchemy.orm import joinedload
from typing import List, Optional, Dict, Any
from datetime import datetime
import uuid
import logging

from app.database import get_db
from app.models import User, Event as EventModel, Contact, Location
from app.auth import get_current_user
from app.caldav_calendar import (
    CalDAVCalendarManager,
    CalendarProvider,
    CalendarConfig,
    get_apple_calendar,
    get_samsung_calendar,
    MultiCalendarSync,
    CALDAV_URLS
)
import os

router = APIRouter()
logger = logging.getLogger(__name__)

# Calendar manager cache
_calendar_managers: Dict[str, CalDAVCalendarManager] = {}


class EventResponse(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    start_time: datetime
    end_time: datetime
    all_day: bool
    attendees: List[str] = []
    contact_id: Optional[str] = None
    contact_name: Optional[str] = None
    location_id: Optional[str] = None
    location_name: Optional[str] = None
    external_calendar_id: Optional[str] = None
    external_calendar_type: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class EventCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    start_time: datetime
    end_time: datetime
    all_day: bool = False
    attendees: List[str] = []
    contact_id: Optional[str] = None
    location_id: Optional[str] = None

    @field_validator('end_time')
    @classmethod
    def validate_end_time(cls, v, info):
        if 'start_time' in info.data and v <= info.data['start_time']:
            raise ValueError('End time must be after start time')
        return v


class CalendarAuthRequest(BaseModel):
    provider: str = Field(..., pattern="^(apple|samsung|google|outlook)$")
    username: Optional[str] = None  # For CalDAV (Apple/Samsung)
    password: Optional[str] = None  # App-specific password for Apple
    calendar_name: Optional[str] = None
    auth_code: Optional[str] = None  # For OAuth (Google/Outlook)
    redirect_uri: Optional[str] = None


class CalendarSyncResponse(BaseModel):
    provider: str
    events_synced: int
    success: bool
    message: str


class EventUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    all_day: Optional[bool] = None
    attendees: Optional[List[str]] = None
    contact_id: Optional[str] = None
    location_id: Optional[str] = None

    @field_validator('end_time')
    @classmethod
    def validate_end_time(cls, v, info):
        if v and 'start_time' in info.data and info.data['start_time'] and v <= info.data['start_time']:
            raise ValueError('End time must be after start time')
        return v


@router.get("/", response_model=List[EventResponse])
async def get_events(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0)
) -> List[EventResponse]:
    """Get calendar events with optional date range filter"""
    
    query = select(EventModel).options(
        joinedload(EventModel.contact),
        joinedload(EventModel.location)
    ).where(EventModel.user_id == current_user.id)
    
    # Apply date filters
    if start_date:
        query = query.where(EventModel.end_time >= start_date)
    if end_date:
        query = query.where(EventModel.start_time <= end_date)
    
    # Order by start time
    query = query.order_by(EventModel.start_time).offset(offset).limit(limit)
    
    result = await db.execute(query)
    events = result.scalars().all()
    
    return [
        EventResponse(
            id=str(event.id),
            title=event.title,
            description=event.description,
            start_time=event.start_time,
            end_time=event.end_time,
            all_day=event.all_day,
            attendees=event.attendees or [],
            contact_id=str(event.contact_id) if event.contact_id else None,
            contact_name=event.contact.name if event.contact else None,
            location_id=str(event.location_id) if event.location_id else None,
            location_name=event.location.name if event.location else None,
            external_calendar_id=event.external_calendar_id,
            external_calendar_type=event.external_calendar_type,
            created_at=event.created_at,
            updated_at=event.updated_at
        )
        for event in events
    ]


@router.post("/", response_model=EventResponse, status_code=status.HTTP_201_CREATED)
async def create_event(
    event_data: EventCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> EventResponse:
    """Create a new calendar event"""
    
    # Validate contact_id if provided
    if event_data.contact_id:
        try:
            contact_uuid = uuid.UUID(event_data.contact_id)
            contact_query = select(Contact).where(
                and_(Contact.id == contact_uuid, Contact.user_id == current_user.id)
            )
            contact_result = await db.execute(contact_query)
            contact = contact_result.scalar_one_or_none()
            if not contact:
                raise HTTPException(status_code=400, detail="Contact not found")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid contact ID format")
    
    # Validate location_id if provided
    if event_data.location_id:
        try:
            location_uuid = uuid.UUID(event_data.location_id)
            location_query = select(Location).where(Location.id == location_uuid)
            location_result = await db.execute(location_query)
            location = location_result.scalar_one_or_none()
            if not location:
                raise HTTPException(status_code=400, detail="Location not found")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid location ID format")
    
    # Create new event
    event = EventModel(
        id=uuid.uuid4(),
        user_id=current_user.id,
        title=event_data.title,
        description=event_data.description,
        start_time=event_data.start_time,
        end_time=event_data.end_time,
        all_day=event_data.all_day,
        attendees=event_data.attendees,
        contact_id=uuid.UUID(event_data.contact_id) if event_data.contact_id else None,
        location_id=uuid.UUID(event_data.location_id) if event_data.location_id else None,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    
    db.add(event)
    await db.commit()
    await db.refresh(event, ['contact', 'location'])
    
    # TODO: Sync with external calendars (Google, Outlook)
    
    return EventResponse(
        id=str(event.id),
        title=event.title,
        description=event.description,
        start_time=event.start_time,
        end_time=event.end_time,
        all_day=event.all_day,
        attendees=event.attendees or [],
        contact_id=str(event.contact_id) if event.contact_id else None,
        contact_name=event.contact.name if event.contact else None,
        location_id=str(event.location_id) if event.location_id else None,
        location_name=event.location.name if event.location else None,
        external_calendar_id=event.external_calendar_id,
        external_calendar_type=event.external_calendar_type,
        created_at=event.created_at,
        updated_at=event.updated_at
    )


@router.get("/{event_id}", response_model=EventResponse)
async def get_event(
    event_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> EventResponse:
    """Get a specific event by ID"""
    
    try:
        event_uuid = uuid.UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid event ID format")
    
    query = select(EventModel).options(
        joinedload(EventModel.contact),
        joinedload(EventModel.location)
    ).where(
        and_(EventModel.id == event_uuid, EventModel.user_id == current_user.id)
    )
    
    result = await db.execute(query)
    event = result.scalar_one_or_none()
    
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    
    return EventResponse(
        id=str(event.id),
        title=event.title,
        description=event.description,
        start_time=event.start_time,
        end_time=event.end_time,
        all_day=event.all_day,
        attendees=event.attendees or [],
        contact_id=str(event.contact_id) if event.contact_id else None,
        contact_name=event.contact.name if event.contact else None,
        location_id=str(event.location_id) if event.location_id else None,
        location_name=event.location.name if event.location else None,
        external_calendar_id=event.external_calendar_id,
        external_calendar_type=event.external_calendar_type,
        created_at=event.created_at,
        updated_at=event.updated_at
    )


@router.put("/{event_id}", response_model=EventResponse)
async def update_event(
    event_id: str,
    event_data: EventUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> EventResponse:
    """Update an existing event"""
    
    try:
        event_uuid = uuid.UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid event ID format")
    
    query = select(EventModel).options(
        joinedload(EventModel.contact),
        joinedload(EventModel.location)
    ).where(
        and_(EventModel.id == event_uuid, EventModel.user_id == current_user.id)
    )
    
    result = await db.execute(query)
    event = result.scalar_one_or_none()
    
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    
    # Validate and update fields if provided
    if event_data.title is not None:
        event.title = event_data.title
    if event_data.description is not None:
        event.description = event_data.description
    if event_data.start_time is not None:
        event.start_time = event_data.start_time
    if event_data.end_time is not None:
        event.end_time = event_data.end_time
    if event_data.all_day is not None:
        event.all_day = event_data.all_day
    if event_data.attendees is not None:
        event.attendees = event_data.attendees
    
    # Validate contact_id if being updated
    if event_data.contact_id is not None:
        if event_data.contact_id:
            try:
                contact_uuid = uuid.UUID(event_data.contact_id)
                contact_query = select(Contact).where(
                    and_(Contact.id == contact_uuid, Contact.user_id == current_user.id)
                )
                contact_result = await db.execute(contact_query)
                contact = contact_result.scalar_one_or_none()
                if not contact:
                    raise HTTPException(status_code=400, detail="Contact not found")
                event.contact_id = contact_uuid
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid contact ID format")
        else:
            event.contact_id = None
    
    # Validate location_id if being updated
    if event_data.location_id is not None:
        if event_data.location_id:
            try:
                location_uuid = uuid.UUID(event_data.location_id)
                location_query = select(Location).where(Location.id == location_uuid)
                location_result = await db.execute(location_query)
                location = location_result.scalar_one_or_none()
                if not location:
                    raise HTTPException(status_code=400, detail="Location not found")
                event.location_id = location_uuid
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid location ID format")
        else:
            event.location_id = None
    
    # Validate time order
    if event.end_time <= event.start_time:
        raise HTTPException(status_code=400, detail="End time must be after start time")
    
    event.updated_at = datetime.utcnow()
    
    await db.commit()
    await db.refresh(event, ['contact', 'location'])
    
    # TODO: Sync with external calendars
    
    return EventResponse(
        id=str(event.id),
        title=event.title,
        description=event.description,
        start_time=event.start_time,
        end_time=event.end_time,
        all_day=event.all_day,
        attendees=event.attendees or [],
        contact_id=str(event.contact_id) if event.contact_id else None,
        contact_name=event.contact.name if event.contact else None,
        location_id=str(event.location_id) if event.location_id else None,
        location_name=event.location.name if event.location else None,
        external_calendar_id=event.external_calendar_id,
        external_calendar_type=event.external_calendar_type,
        created_at=event.created_at,
        updated_at=event.updated_at
    )


@router.delete("/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(
    event_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete an event"""
    
    try:
        event_uuid = uuid.UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid event ID format")
    
    query = select(EventModel).where(
        and_(EventModel.id == event_uuid, EventModel.user_id == current_user.id)
    )
    
    result = await db.execute(query)
    event = result.scalar_one_or_none()
    
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    
    await db.delete(event)
    await db.commit()
    
    # TODO: Delete from external calendars
    
    return None


@router.get("/providers")
async def list_calendar_providers(
    current_user: User = Depends(get_current_user)
) -> Dict[str, Any]:
    """List available calendar providers and their status"""

    providers = {
        "internal": {
            "name": "Built-in Calendar",
            "status": "active",
            "type": "internal",
            "description": "CRM's internal calendar - always available"
        },
        "apple": {
            "name": "Apple iCloud Calendar",
            "status": "available",
            "type": "caldav",
            "description": "Sync with iCloud Calendar (requires app-specific password)",
            "setup_url": "https://appleid.apple.com/account/manage"
        },
        "samsung": {
            "name": "Samsung Calendar",
            "status": "available",
            "type": "caldav",
            "description": "Sync with Samsung Calendar"
        }
    }

    # Check if providers are configured
    if os.getenv("APPLE_CALENDAR_USERNAME"):
        providers["apple"]["status"] = "configured"
    if os.getenv("SAMSUNG_CALENDAR_USERNAME"):
        providers["samsung"]["status"] = "configured"

    return {"providers": providers}


@router.post("/connect")
async def connect_calendar(
    auth_data: CalendarAuthRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Connect to a calendar provider (Apple/Samsung via CalDAV)"""

    try:
        if auth_data.provider == "apple":
            if not auth_data.username or not auth_data.password:
                raise HTTPException(
                    status_code=400,
                    detail="Apple Calendar requires username (Apple ID) and app-specific password"
                )

            manager = get_apple_calendar(
                username=auth_data.username,
                app_password=auth_data.password,
                calendar_name=auth_data.calendar_name
            )

            if await manager.connect():
                # Cache the manager for this user
                _calendar_managers[f"{current_user.id}_apple"] = manager
                calendars = await manager.list_calendars()
                return {
                    "status": "connected",
                    "provider": "apple",
                    "message": "Successfully connected to Apple iCloud Calendar",
                    "calendars": calendars
                }
            else:
                raise HTTPException(status_code=401, detail="Failed to authenticate with Apple iCloud")

        elif auth_data.provider == "samsung":
            if not auth_data.username or not auth_data.password:
                raise HTTPException(
                    status_code=400,
                    detail="Samsung Calendar requires username and password"
                )

            manager = get_samsung_calendar(
                username=auth_data.username,
                password=auth_data.password,
                calendar_name=auth_data.calendar_name
            )

            if await manager.connect():
                _calendar_managers[f"{current_user.id}_samsung"] = manager
                calendars = await manager.list_calendars()
                return {
                    "status": "connected",
                    "provider": "samsung",
                    "message": "Successfully connected to Samsung Calendar",
                    "calendars": calendars
                }
            else:
                raise HTTPException(status_code=401, detail="Failed to authenticate with Samsung")

        else:
            raise HTTPException(status_code=400, detail="Use /connect for Apple/Samsung. Google/Outlook not supported.")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error connecting to {auth_data.provider}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to connect: {str(e)}")


@router.get("/external/{provider}")
async def get_external_events(
    provider: str,
    current_user: User = Depends(get_current_user),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
) -> Dict[str, Any]:
    """Get events from external calendar (Apple/Samsung)"""

    if provider not in ["apple", "samsung"]:
        raise HTTPException(status_code=400, detail="Provider must be 'apple' or 'samsung'")

    manager_key = f"{current_user.id}_{provider}"
    manager = _calendar_managers.get(manager_key)

    if not manager:
        # Try to connect from environment variables
        if provider == "apple":
            username = os.getenv("APPLE_CALENDAR_USERNAME")
            password = os.getenv("APPLE_CALENDAR_PASSWORD")
            if username and password:
                manager = get_apple_calendar(username, password)
                if await manager.connect():
                    _calendar_managers[manager_key] = manager
        elif provider == "samsung":
            username = os.getenv("SAMSUNG_CALENDAR_USERNAME")
            password = os.getenv("SAMSUNG_CALENDAR_PASSWORD")
            if username and password:
                manager = get_samsung_calendar(username, password)
                if await manager.connect():
                    _calendar_managers[manager_key] = manager

    if not manager:
        raise HTTPException(
            status_code=400,
            detail=f"{provider.title()} calendar not connected. Use POST /api/calendar/connect first."
        )

    events = await manager.get_events(start_date, end_date)
    return {
        "provider": provider,
        "events": events,
        "count": len(events)
    }


@router.post("/external/{provider}/create")
async def create_external_event(
    provider: str,
    event_data: EventCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Create event on external calendar and optionally in CRM"""

    if provider not in ["apple", "samsung"]:
        raise HTTPException(status_code=400, detail="Provider must be 'apple' or 'samsung'")

    manager_key = f"{current_user.id}_{provider}"
    manager = _calendar_managers.get(manager_key)

    if not manager:
        raise HTTPException(
            status_code=400,
            detail=f"{provider.title()} calendar not connected. Use POST /api/calendar/connect first."
        )

    # Get contact name if contact_id provided
    contact_name = None
    if event_data.contact_id:
        try:
            contact_uuid = uuid.UUID(event_data.contact_id)
            contact_query = select(Contact).where(
                and_(Contact.id == contact_uuid, Contact.user_id == current_user.id)
            )
            contact_result = await db.execute(contact_query)
            contact = contact_result.scalar_one_or_none()
            if contact:
                contact_name = contact.name
        except:
            pass

    # Create on external calendar
    external_uid = await manager.create_event(
        title=event_data.title,
        start_time=event_data.start_time,
        end_time=event_data.end_time,
        description=event_data.description,
        contact_name=contact_name
    )

    if not external_uid:
        raise HTTPException(status_code=500, detail=f"Failed to create event on {provider}")

    # Also create in internal CRM calendar
    event = EventModel(
        id=uuid.uuid4(),
        user_id=current_user.id,
        title=event_data.title,
        description=event_data.description,
        start_time=event_data.start_time,
        end_time=event_data.end_time,
        all_day=event_data.all_day,
        attendees=event_data.attendees,
        contact_id=uuid.UUID(event_data.contact_id) if event_data.contact_id else None,
        location_id=uuid.UUID(event_data.location_id) if event_data.location_id else None,
        external_calendar_id=external_uid,
        external_calendar_type=provider,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )

    db.add(event)
    await db.commit()

    return {
        "status": "created",
        "internal_id": str(event.id),
        "external_id": external_uid,
        "provider": provider,
        "message": f"Event created on both CRM and {provider.title()} calendar"
    }


@router.post("/sync/{provider}", response_model=CalendarSyncResponse)
async def sync_specific_calendar(
    provider: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> CalendarSyncResponse:
    """Sync with specific external calendar service (Apple/Samsung)"""

    if provider not in ["apple", "samsung"]:
        raise HTTPException(status_code=400, detail="Provider must be 'apple' or 'samsung'")

    try:
        manager_key = f"{current_user.id}_{provider}"
        manager = _calendar_managers.get(manager_key)

        if not manager:
            return CalendarSyncResponse(
                provider=provider,
                events_synced=0,
                success=False,
                message=f"{provider.title()} calendar not connected. Use POST /api/calendar/connect first."
            )

        # Get events from external calendar
        external_events = await manager.get_events()
        events_synced = 0

        for ext_event in external_events:
            # Check if event already exists in CRM
            existing_query = select(EventModel).where(
                and_(
                    EventModel.user_id == current_user.id,
                    EventModel.external_calendar_id == ext_event.get('uid'),
                    EventModel.external_calendar_type == provider
                )
            )
            existing_result = await db.execute(existing_query)
            existing = existing_result.scalar_one_or_none()

            if not existing and ext_event.get('start') and ext_event.get('end'):
                # Import new event
                new_event = EventModel(
                    id=uuid.uuid4(),
                    user_id=current_user.id,
                    title=ext_event.get('title', 'Imported Event'),
                    description=ext_event.get('description'),
                    start_time=ext_event['start'],
                    end_time=ext_event['end'],
                    all_day=False,
                    external_calendar_id=ext_event.get('uid'),
                    external_calendar_type=provider,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                )
                db.add(new_event)
                events_synced += 1

        await db.commit()

        return CalendarSyncResponse(
            provider=provider,
            events_synced=events_synced,
            success=True,
            message=f"Successfully synced {events_synced} new events from {provider.title()}"
        )

    except Exception as e:
        logger.error(f"Error syncing {provider} calendar: {e}")
        return CalendarSyncResponse(
            provider=provider,
            events_synced=0,
            success=False,
            message=f"Failed to sync {provider} calendar: {str(e)}"
        )
