"""
Background worker for CRM Escort AI
Handles async tasks like AI processing, calendar sync, etc.
"""
import os
import sys
import asyncio
from typing import Dict, Any, Optional, List
import uuid
from datetime import datetime
import structlog

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from celery import Celery
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select, and_

from app.database import DATABASE_URL, Base
from app.models import User, Message, Contact, Event, Task, Location
from app.ai_extractor import extract_message_data

# Configure logging
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer() if os.getenv("ENV") == "development" else structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(10),  # INFO level
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=False,
)
logger = structlog.get_logger()

# Environment variables
ENV = os.getenv("ENV", "development")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL)
RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", REDIS_URL)

# Initialize Celery
celery_app = Celery(
    "crm_escort_worker",
    broker=BROKER_URL,
    backend=RESULT_BACKEND,
    include=["app.workers.worker"]
)

# Celery configuration
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_routes={
        'app.workers.worker.process_message': {'queue': 'ai_processing'},
        'app.workers.worker.sync_calendar': {'queue': 'calendar_sync'},
        'app.workers.worker.execute_workflow': {'queue': 'workflow_execution'},
    }
)

# Database setup for worker
engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_async_session():
    """Get async database session for worker tasks"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


@celery_app.task(bind=True, name="process_message")
def process_message_task(self, message_id: str):
    """Celery task wrapper for async message processing"""
    return asyncio.run(process_message(message_id))


async def process_message(message_id: str) -> Dict[str, Any]:
    """
    Process a message with AI extraction
    
    Args:
        message_id: UUID of the message to process
        
    Returns:
        Processing results
    """
    try:
        await logger.ainfo("message_processing_started", message_id=message_id)
        
        async with AsyncSessionLocal() as db:
            # Get message with user and existing contacts
            query = select(Message).where(Message.id == uuid.UUID(message_id))
            result = await db.execute(query)
            message = result.scalar_one_or_none()
            
            if not message:
                raise ValueError(f"Message {message_id} not found")
            
            if message.processed:
                await logger.ainfo("message_already_processed", message_id=message_id)
                return {"status": "already_processed"}
            
            # Get user and existing contacts for context
            user_query = select(User).where(User.id == message.user_id)
            user_result = await db.execute(user_query)
            user = user_result.scalar_one_or_none()
            
            contacts_query = select(Contact).where(Contact.user_id == message.user_id)
            contacts_result = await db.execute(contacts_query)
            existing_contacts = contacts_result.scalars().all()
            
            # Build context for AI
            context = {
                "existing_contacts": [
                    {"name": c.name, "email": c.email, "phone": c.phone}
                    for c in existing_contacts
                ]
            }
            
            # Extract data using AI
            extracted_data = await extract_message_data(
                message_content=message.content,
                sender=message.sender,
                context=context
            )
            
            # Process extracted data
            results = await process_extracted_data(db, message, extracted_data)
            
            # Update message as processed
            message.processed = True
            message.extracted_data = extracted_data
            
            await db.commit()
            
            await logger.ainfo("message_processing_completed", 
                             message_id=message_id,
                             **results)
            
            return {
                "status": "completed",
                "message_id": message_id,
                "extracted_data": extracted_data,
                **results
            }
            
    except Exception as e:
        await logger.aerror("message_processing_failed", 
                          message_id=message_id,
                          error=str(e))
        raise


async def process_extracted_data(
    db, 
    message: Message, 
    extracted_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Process extracted data and create/update database records
    
    Args:
        db: Database session
        message: Source message
        extracted_data: AI extracted data
        
    Returns:
        Summary of created records
    """
    results = {
        "contacts_created": 0,
        "contacts_updated": 0,
        "events_created": 0,
        "tasks_created": 0,
        "locations_created": 0
    }
    
    # Process contacts
    for contact_data in extracted_data.get("contacts", []):
        contact = await find_or_create_contact(db, message.user_id, contact_data)
        if contact:
            if not message.contact_id:  # Link message to first contact found
                message.contact_id = contact.id
            results["contacts_created" if contact else "contacts_updated"] += 1
    
    # Process locations
    created_locations = {}
    for location_data in extracted_data.get("locations", []):
        location = await create_location(db, message.user_id, location_data)
        if location:
            created_locations[location_data["name"]] = location
            results["locations_created"] += 1
    
    # Process events
    for event_data in extracted_data.get("events", []):
        event = await create_event(db, message.user_id, event_data, created_locations)
        if event:
            results["events_created"] += 1
    
    # Process tasks
    for task_data in extracted_data.get("tasks", []):
        task = await create_task(db, message.user_id, message.id, task_data)
        if task:
            results["tasks_created"] += 1
    
    return results


async def find_or_create_contact(
    db, 
    user_id: uuid.UUID, 
    contact_data: Dict[str, Any]
) -> Optional[Contact]:
    """Find existing contact or create new one"""
    
    # Try to find existing contact by email or phone
    query = select(Contact).where(
        and_(
            Contact.user_id == user_id,
            (
                (Contact.email == contact_data.get("email")) |
                (Contact.phone == contact_data.get("phone"))
            )
        )
    )
    
    if contact_data.get("email") or contact_data.get("phone"):
        result = await db.execute(query)
        existing_contact = result.scalar_one_or_none()
        
        if existing_contact:
            # Update existing contact with new information
            if contact_data.get("organization") and not existing_contact.organization:
                existing_contact.organization = contact_data["organization"]
            if contact_data.get("role") and not existing_contact.role:
                existing_contact.role = contact_data["role"]
            existing_contact.last_contact = datetime.utcnow()
            existing_contact.updated_at = datetime.utcnow()
            return existing_contact
    
    # Create new contact
    contact = Contact(
        id=uuid.uuid4(),
        user_id=user_id,
        name=contact_data["name"],
        phone=contact_data.get("phone"),
        email=contact_data.get("email"),
        organization=contact_data.get("organization"),
        role=contact_data.get("role"),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        last_contact=datetime.utcnow()
    )
    
    db.add(contact)
    return contact


async def create_location(
    db, 
    user_id: uuid.UUID, 
    location_data: Dict[str, Any]
) -> Optional[Location]:
    """Create a new location"""
    
    location = Location(
        id=uuid.uuid4(),
        user_id=user_id,
        name=location_data["name"],
        address=location_data.get("address"),
        city=location_data.get("city"),
        state=location_data.get("state"),
        location_type=location_data.get("type", "other"),
        created_at=datetime.utcnow()
    )
    
    db.add(location)
    return location


async def create_event(
    db, 
    user_id: uuid.UUID, 
    event_data: Dict[str, Any],
    locations: Dict[str, Location]
) -> Optional[Event]:
    """Create a new event"""
    
    if not event_data.get("start_time"):
        return None
    
    # Try to match location
    location_id = None
    event_location = event_data.get("location", "")
    for loc_name, location in locations.items():
        if loc_name.lower() in event_location.lower():
            location_id = location.id
            break
    
    event = Event(
        id=uuid.uuid4(),
        user_id=user_id,
        title=event_data["title"],
        start_time=event_data["start_time"],
        end_time=event_data.get("end_time") or event_data["start_time"],
        location_id=location_id,
        attendees=event_data.get("attendees", []),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    
    db.add(event)
    return event


async def create_task(
    db, 
    user_id: uuid.UUID, 
    message_id: uuid.UUID,
    task_data: Dict[str, Any]
) -> Optional[Task]:
    """Create a new task"""
    
    task = Task(
        id=uuid.uuid4(),
        user_id=user_id,
        message_id=message_id,
        title=task_data["title"],
        description=task_data.get("description"),
        due_date=task_data.get("due_date"),
        priority=task_data.get("priority", "medium"),
        status="pending",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    
    db.add(task)
    return task


@celery_app.task(bind=True, name="sync_calendar")
def sync_calendar_task(self, user_id: str):
    """Celery task wrapper for calendar sync"""
    return asyncio.run(sync_calendar(user_id))


async def sync_calendar(user_id: str) -> Dict[str, Any]:
    """
    Sync with external calendars
    
    Args:
        user_id: UUID of the user
        
    Returns:
        Sync results
    """
    await logger.ainfo("calendar_sync_started", user_id=user_id)
    
    try:
        async with AsyncSessionLocal() as db:
            # TODO: Implement calendar sync
            # 1. Get user's calendar tokens
            # 2. Fetch events from Google Calendar API
            # 3. Fetch events from Outlook API
            # 4. Update local database
            # 5. Push local events to external calendars
            
            await logger.ainfo("calendar_sync_completed", user_id=user_id)
            return {"status": "completed", "user_id": user_id}
            
    except Exception as e:
        await logger.aerror("calendar_sync_failed", user_id=user_id, error=str(e))
        raise


@celery_app.task(bind=True, name="execute_workflow")
def execute_workflow_task(self, workflow_id: str, triggered_by: str):
    """Celery task wrapper for workflow execution"""
    return asyncio.run(execute_workflow(workflow_id, triggered_by))


async def execute_workflow(workflow_id: str, triggered_by: str) -> Dict[str, Any]:
    """
    Execute a workflow
    
    Args:
        workflow_id: UUID of the workflow
        triggered_by: UUID of the triggering entity
        
    Returns:
        Execution results
    """
    await logger.ainfo("workflow_execution_started", 
                     workflow_id=workflow_id,
                     triggered_by=triggered_by)
    
    try:
        async with AsyncSessionLocal() as db:
            # TODO: Implement workflow execution
            # 1. Get workflow definition
            # 2. Check conditions
            # 3. Execute actions (send SMS, create events, etc.)
            # 4. Log execution
            
            await logger.ainfo("workflow_execution_completed", workflow_id=workflow_id)
            return {"status": "completed", "workflow_id": workflow_id}
            
    except Exception as e:
        await logger.aerror("workflow_execution_failed", 
                          workflow_id=workflow_id,
                          error=str(e))
        raise


def main():
    """Main worker entry point"""
    print(f"🚀 CRM Escort AI Worker starting in {ENV} mode...")
    print(f"📡 Broker URL: {BROKER_URL}")
    print(f"💾 Result Backend: {RESULT_BACKEND}")
    
    # Start Celery worker
    celery_app.start([
        "worker",
        "--loglevel=info",
        "--concurrency=4",
        f"--hostname=crm-worker@%h"
    ])


if __name__ == "__main__":
    main()
