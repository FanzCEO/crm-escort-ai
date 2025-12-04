from fastapi import APIRouter, HTTPException, status, Depends, Query
from pydantic import BaseModel, Field, validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from sqlalchemy.orm import joinedload
from typing import List, Optional, Dict
from datetime import datetime
import uuid
import logging

from app.database import get_db
from app.models import User, Event as EventModel, Contact, Location
from app.auth import get_current_user
from app.google_calendar import GoogleCalendarManager
from app.outlook_calendar import OutlookCalendarManager

router = APIRouter()
logger = logging.getLogger(__name__)


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

    @validator('end_time')
    def validate_end_time(cls, v, values):
        if 'start_time' in values and v <= values['start_time']:
            raise ValueError('End time must be after start time')
        return v


class CalendarAuthRequest(BaseModel):
    provider: str = Field(..., regex="^(google|outlook)$")
    auth_code: Optional[str] = None
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

    @validator('end_time')
    def validate_end_time(cls, v, values):
        if v and 'start_time' in values and values['start_time'] and v <= values['start_time']:
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


@router.post("/auth/{provider}")
async def initiate_calendar_auth(
    provider: str,
    current_user: User = Depends(get_current_user)
) -> Dict[str, str]:
    """Initiate OAuth flow for calendar provider"""
    
    if provider == "google":
        try:
            manager = GoogleCalendarManager()
            auth_url = await manager.get_authorization_url()
            return {"auth_url": auth_url, "provider": "google"}
        except Exception as e:
            logger.error(f"Error initiating Google calendar auth: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to initiate Google auth: {str(e)}")
    
    elif provider == "outlook":
        try:
            manager = OutlookCalendarManager()
            auth_url = await manager.get_authorization_url()
            return {"auth_url": auth_url, "provider": "outlook"}
        except Exception as e:
            logger.error(f"Error initiating Outlook calendar auth: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to initiate Outlook auth: {str(e)}")
    
    else:
        raise HTTPException(status_code=400, detail="Unsupported calendar provider")


@router.post("/auth/callback")
async def handle_calendar_auth_callback(
    auth_data: CalendarAuthRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, str]:
    """Handle OAuth callback and store credentials"""
    
    try:
        if auth_data.provider == "google":
            manager = GoogleCalendarManager()
            credentials = await manager.exchange_auth_code(
                auth_data.auth_code, 
                auth_data.redirect_uri
            )
            # Store credentials for user (implement in User model or separate table)
            # TODO: Store credentials securely
            
        elif auth_data.provider == "outlook":
            manager = OutlookCalendarManager()
            credentials = await manager.exchange_auth_code(
                auth_data.auth_code, 
                auth_data.redirect_uri
            )
            # Store credentials for user
            # TODO: Store credentials securely
            
        else:
            raise HTTPException(status_code=400, detail="Unsupported calendar provider")
        
        return {"message": f"Successfully connected {auth_data.provider} calendar", "status": "success"}
        
    except Exception as e:
        logger.error(f"Error handling calendar auth callback: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to complete authentication: {str(e)}")


@router.post("/sync/{provider}", response_model=CalendarSyncResponse)
async def sync_specific_calendar(
    provider: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> CalendarSyncResponse:
    """Sync with specific external calendar service"""
    
    try:
        events_synced = 0
        
        if provider == "google":
            manager = GoogleCalendarManager()
            # TODO: Get stored credentials for user
            # events = await manager.get_events()
            # Process and sync events
            events_synced = 0  # Placeholder
            
        elif provider == "outlook":
            manager = OutlookCalendarManager()
            # TODO: Get stored credentials for user  
            # events = await manager.get_events()
            # Process and sync events
            events_synced = 0  # Placeholder
            
        else:
            raise HTTPException(status_code=400, detail="Unsupported calendar provider")
        
        return CalendarSyncResponse(
            provider=provider,
            events_synced=events_synced,
            success=True,
            message=f"Successfully synced {events_synced} events from {provider}"
        )
        
    except Exception as e:
        logger.error(f"Error syncing {provider} calendar: {e}")
        return CalendarSyncResponse(
            provider=provider,
            events_synced=0,
            success=False,
            message=f"Failed to sync {provider} calendar: {str(e)}"
        )
