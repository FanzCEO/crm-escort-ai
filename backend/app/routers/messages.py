from fastapi import APIRouter, HTTPException, status, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from sqlalchemy.orm import joinedload
from typing import List, Optional, Dict
from datetime import datetime
import uuid

from app.database import get_db
from app.models import User, Message as MessageModel, Contact
from app.auth import get_current_user

router = APIRouter()


class MessageResponse(BaseModel):
    id: str
    content: str
    sender: str
    source: str
    received_at: datetime
    processed: bool
    extracted_data: Optional[Dict] = None
    contact_id: Optional[str] = None
    contact_name: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class MessageCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)
    sender: str = Field(..., min_length=1, max_length=255)
    source: str = Field(default="manual", pattern="^(manual|sms|email|rm_chat)$")


class MessageUpdate(BaseModel):
    content: Optional[str] = Field(None, min_length=1, max_length=10000)
    processed: Optional[bool] = None
    extracted_data: Optional[Dict] = None


@router.get("/", response_model=List[MessageResponse])
async def get_messages(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    processed: Optional[bool] = Query(None),
    source: Optional[str] = Query(None),
    search: Optional[str] = Query(None)
) -> List[MessageResponse]:
    """Get all user messages with optional filtering"""
    
    query = select(MessageModel).options(joinedload(MessageModel.contact)).where(
        MessageModel.user_id == current_user.id
    )
    
    # Apply filters
    if processed is not None:
        query = query.where(MessageModel.processed == processed)
    
    if source:
        query = query.where(MessageModel.source == source)
    
    if search:
        query = query.where(
            or_(
                MessageModel.content.ilike(f"%{search}%"),
                MessageModel.sender.ilike(f"%{search}%")
            )
        )
    
    # Order by received_at descending, add pagination
    query = query.order_by(MessageModel.received_at.desc()).offset(offset).limit(limit)
    
    result = await db.execute(query)
    messages = result.scalars().all()
    
    return [
        MessageResponse(
            id=str(msg.id),
            content=msg.content,
            sender=msg.sender,
            source=msg.source,
            received_at=msg.received_at,
            processed=msg.processed,
            extracted_data=msg.extracted_data,
            contact_id=str(msg.contact_id) if msg.contact_id else None,
            contact_name=msg.contact.name if msg.contact else None,
            created_at=msg.created_at
        )
        for msg in messages
    ]


@router.post("/", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
async def create_message(
    message_data: MessageCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> MessageResponse:
    """Create a new message and trigger AI processing"""
    
    # Create new message
    message = MessageModel(
        id=uuid.uuid4(),
        user_id=current_user.id,
        content=message_data.content,
        sender=message_data.sender,
        source=message_data.source,
        received_at=datetime.utcnow(),
        processed=False,
        created_at=datetime.utcnow()
    )
    
    db.add(message)
    await db.commit()
    await db.refresh(message)
    
    # TODO: Trigger AI processing in background worker
    # queue_message_processing.delay(str(message.id))
    
    return MessageResponse(
        id=str(message.id),
        content=message.content,
        sender=message.sender,
        source=message.source,
        received_at=message.received_at,
        processed=message.processed,
        extracted_data=message.extracted_data,
        contact_id=str(message.contact_id) if message.contact_id else None,
        contact_name=None,
        created_at=message.created_at
    )


@router.get("/{message_id}", response_model=MessageResponse)
async def get_message(
    message_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> MessageResponse:
    """Get a specific message by ID"""
    
    try:
        msg_uuid = uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid message ID format")
    
    query = select(MessageModel).options(joinedload(MessageModel.contact)).where(
        and_(MessageModel.id == msg_uuid, MessageModel.user_id == current_user.id)
    )
    
    result = await db.execute(query)
    message = result.scalar_one_or_none()
    
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    return MessageResponse(
        id=str(message.id),
        content=message.content,
        sender=message.sender,
        source=message.source,
        received_at=message.received_at,
        processed=message.processed,
        extracted_data=message.extracted_data,
        contact_id=str(message.contact_id) if message.contact_id else None,
        contact_name=message.contact.name if message.contact else None,
        created_at=message.created_at
    )


@router.put("/{message_id}", response_model=MessageResponse)
async def update_message(
    message_id: str,
    message_data: MessageUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> MessageResponse:
    """Update a message"""
    
    try:
        msg_uuid = uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid message ID format")
    
    query = select(MessageModel).options(joinedload(MessageModel.contact)).where(
        and_(MessageModel.id == msg_uuid, MessageModel.user_id == current_user.id)
    )
    
    result = await db.execute(query)
    message = result.scalar_one_or_none()
    
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    # Update fields if provided
    if message_data.content is not None:
        message.content = message_data.content
    if message_data.processed is not None:
        message.processed = message_data.processed
    if message_data.extracted_data is not None:
        message.extracted_data = message_data.extracted_data
    
    await db.commit()
    await db.refresh(message)
    
    return MessageResponse(
        id=str(message.id),
        content=message.content,
        sender=message.sender,
        source=message.source,
        received_at=message.received_at,
        processed=message.processed,
        extracted_data=message.extracted_data,
        contact_id=str(message.contact_id) if message.contact_id else None,
        contact_name=message.contact.name if message.contact else None,
        created_at=message.created_at
    )


@router.post("/{message_id}/process")
async def process_message(
    message_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, str]:
    """Manually trigger AI processing for a message"""
    
    try:
        msg_uuid = uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid message ID format")
    
    query = select(MessageModel).where(
        and_(MessageModel.id == msg_uuid, MessageModel.user_id == current_user.id)
    )
    
    result = await db.execute(query)
    message = result.scalar_one_or_none()
    
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    # TODO: Trigger AI extraction in background worker
    # queue_message_processing.delay(str(message.id))
    
    return {"message": f"Processing triggered for message {message_id}"}


@router.delete("/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_message(
    message_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete a message"""
    
    try:
        msg_uuid = uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid message ID format")
    
    query = select(MessageModel).where(
        and_(MessageModel.id == msg_uuid, MessageModel.user_id == current_user.id)
    )
    
    result = await db.execute(query)
    message = result.scalar_one_or_none()
    
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    await db.delete(message)
    await db.commit()
    
    return None
