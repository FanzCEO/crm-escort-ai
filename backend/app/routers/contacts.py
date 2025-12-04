from fastapi import APIRouter, HTTPException, status, Depends, Query
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func
from sqlalchemy.orm import joinedload
from typing import List, Optional, Dict
from datetime import datetime
import uuid

from app.database import get_db
from app.models import User, Contact as ContactModel, Message
from app.auth import get_current_user

router = APIRouter()


class ContactResponse(BaseModel):
    id: str
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    organization: Optional[str] = None
    role: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[List[str]] = None
    created_at: datetime
    updated_at: datetime
    last_contact: Optional[datetime] = None
    message_count: int = 0

    class Config:
        from_attributes = True


class ContactCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    phone: Optional[str] = Field(None, max_length=50)
    email: Optional[EmailStr] = None
    organization: Optional[str] = Field(None, max_length=255)
    role: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = None
    tags: Optional[List[str]] = None


class ContactUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    phone: Optional[str] = Field(None, max_length=50)
    email: Optional[EmailStr] = None
    organization: Optional[str] = Field(None, max_length=255)
    role: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = None
    tags: Optional[List[str]] = None


@router.get("/", response_model=List[ContactResponse])
async def get_contacts(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = Query(None)
) -> List[ContactResponse]:
    """Get all contacts with optional search"""
    
    # Base query with message count
    query = select(
        ContactModel,
        func.count(Message.id).label("message_count")
    ).outerjoin(Message).where(
        ContactModel.user_id == current_user.id
    )
    
    # Apply search filter
    if search:
        query = query.where(
            or_(
                ContactModel.name.ilike(f"%{search}%"),
                ContactModel.email.ilike(f"%{search}%"),
                ContactModel.organization.ilike(f"%{search}%"),
                ContactModel.phone.ilike(f"%{search}%")
            )
        )
    
    # Group by contact and order by name
    query = query.group_by(ContactModel.id).order_by(ContactModel.name).offset(offset).limit(limit)
    
    result = await db.execute(query)
    contacts_with_count = result.all()
    
    return [
        ContactResponse(
            id=str(contact.id),
            name=contact.name,
            phone=contact.phone,
            email=contact.email,
            organization=contact.organization,
            role=contact.role,
            notes=contact.notes,
            tags=contact.tags,
            created_at=contact.created_at,
            updated_at=contact.updated_at,
            last_contact=contact.last_contact,
            message_count=message_count
        )
        for contact, message_count in contacts_with_count
    ]


@router.post("/", response_model=ContactResponse, status_code=status.HTTP_201_CREATED)
async def create_contact(
    contact_data: ContactCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ContactResponse:
    """Create a new contact"""
    
    # Check for duplicate email if provided
    if contact_data.email:
        existing = await db.execute(
            select(ContactModel).where(
                and_(
                    ContactModel.user_id == current_user.id,
                    ContactModel.email == contact_data.email
                )
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Contact with this email already exists")
    
    # Create new contact
    contact = ContactModel(
        id=uuid.uuid4(),
        user_id=current_user.id,
        name=contact_data.name,
        phone=contact_data.phone,
        email=contact_data.email,
        organization=contact_data.organization,
        role=contact_data.role,
        notes=contact_data.notes,
        tags=contact_data.tags,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    
    db.add(contact)
    await db.commit()
    await db.refresh(contact)
    
    return ContactResponse(
        id=str(contact.id),
        name=contact.name,
        phone=contact.phone,
        email=contact.email,
        organization=contact.organization,
        role=contact.role,
        notes=contact.notes,
        tags=contact.tags,
        created_at=contact.created_at,
        updated_at=contact.updated_at,
        last_contact=contact.last_contact,
        message_count=0
    )


@router.get("/{contact_id}", response_model=ContactResponse)
async def get_contact(
    contact_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ContactResponse:
    """Get a specific contact by ID"""
    
    try:
        contact_uuid = uuid.UUID(contact_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid contact ID format")
    
    # Query with message count
    query = select(
        ContactModel,
        func.count(Message.id).label("message_count")
    ).outerjoin(Message).where(
        and_(ContactModel.id == contact_uuid, ContactModel.user_id == current_user.id)
    ).group_by(ContactModel.id)
    
    result = await db.execute(query)
    contact_with_count = result.one_or_none()
    
    if not contact_with_count:
        raise HTTPException(status_code=404, detail="Contact not found")
    
    contact, message_count = contact_with_count
    
    return ContactResponse(
        id=str(contact.id),
        name=contact.name,
        phone=contact.phone,
        email=contact.email,
        organization=contact.organization,
        role=contact.role,
        notes=contact.notes,
        tags=contact.tags,
        created_at=contact.created_at,
        updated_at=contact.updated_at,
        last_contact=contact.last_contact,
        message_count=message_count
    )


@router.put("/{contact_id}", response_model=ContactResponse)
async def update_contact(
    contact_id: str,
    contact_data: ContactUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ContactResponse:
    """Update an existing contact"""
    
    try:
        contact_uuid = uuid.UUID(contact_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid contact ID format")
    
    query = select(ContactModel).where(
        and_(ContactModel.id == contact_uuid, ContactModel.user_id == current_user.id)
    )
    
    result = await db.execute(query)
    contact = result.scalar_one_or_none()
    
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    
    # Check for duplicate email if being updated
    if contact_data.email and contact_data.email != contact.email:
        existing = await db.execute(
            select(ContactModel).where(
                and_(
                    ContactModel.user_id == current_user.id,
                    ContactModel.email == contact_data.email,
                    ContactModel.id != contact_uuid
                )
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Contact with this email already exists")
    
    # Update fields if provided
    if contact_data.name is not None:
        contact.name = contact_data.name
    if contact_data.phone is not None:
        contact.phone = contact_data.phone
    if contact_data.email is not None:
        contact.email = contact_data.email
    if contact_data.organization is not None:
        contact.organization = contact_data.organization
    if contact_data.role is not None:
        contact.role = contact_data.role
    if contact_data.notes is not None:
        contact.notes = contact_data.notes
    if contact_data.tags is not None:
        contact.tags = contact_data.tags
    
    contact.updated_at = datetime.utcnow()
    
    await db.commit()
    await db.refresh(contact)
    
    # Get message count
    count_query = select(func.count(Message.id)).where(Message.contact_id == contact_uuid)
    count_result = await db.execute(count_query)
    message_count = count_result.scalar()
    
    return ContactResponse(
        id=str(contact.id),
        name=contact.name,
        phone=contact.phone,
        email=contact.email,
        organization=contact.organization,
        role=contact.role,
        notes=contact.notes,
        tags=contact.tags,
        created_at=contact.created_at,
        updated_at=contact.updated_at,
        last_contact=contact.last_contact,
        message_count=message_count
    )


@router.delete("/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contact(
    contact_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete a contact"""
    
    try:
        contact_uuid = uuid.UUID(contact_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid contact ID format")
    
    query = select(ContactModel).where(
        and_(ContactModel.id == contact_uuid, ContactModel.user_id == current_user.id)
    )
    
    result = await db.execute(query)
    contact = result.scalar_one_or_none()
    
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    
    await db.delete(contact)
    await db.commit()
    
    return None


class MessageSummary(BaseModel):
    id: str
    content: str
    sender: str
    source: str
    received_at: datetime
    processed: bool

    class Config:
        from_attributes = True


@router.get("/{contact_id}/messages", response_model=List[MessageSummary])
async def get_contact_messages(
    contact_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0)
) -> List[MessageSummary]:
    """Get all messages for a specific contact"""
    
    try:
        contact_uuid = uuid.UUID(contact_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid contact ID format")
    
    # Verify contact exists and belongs to user
    contact_query = select(ContactModel).where(
        and_(ContactModel.id == contact_uuid, ContactModel.user_id == current_user.id)
    )
    contact_result = await db.execute(contact_query)
    contact = contact_result.scalar_one_or_none()
    
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    
    # Get messages for contact
    query = select(Message).where(
        Message.contact_id == contact_uuid
    ).order_by(Message.received_at.desc()).offset(offset).limit(limit)
    
    result = await db.execute(query)
    messages = result.scalars().all()
    
    return [
        MessageSummary(
            id=str(msg.id),
            content=msg.content,
            sender=msg.sender,
            source=msg.source,
            received_at=msg.received_at,
            processed=msg.processed
        )
        for msg in messages
    ]
