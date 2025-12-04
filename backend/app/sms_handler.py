"""
Twilio SMS integration for CRM Escort AI
Handles incoming and outgoing SMS messages
"""
import os
from typing import Dict, Optional, Any
from fastapi import APIRouter, HTTPException, status, Depends, Request, Form
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from twilio.rest import Client
from twilio.request_validator import RequestValidator
import structlog
import uuid
from datetime import datetime

from app.database import get_db
from app.models import User, Message, Contact
from app.workers.worker import process_message_task

logger = structlog.get_logger()
router = APIRouter()

# Twilio configuration
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER")
TWILIO_WEBHOOK_URL = os.getenv("TWILIO_WEBHOOK_URL")

# Initialize Twilio client
if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
    twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    request_validator = RequestValidator(TWILIO_AUTH_TOKEN)
else:
    twilio_client = None
    request_validator = None


class SMSMessage(BaseModel):
    to: str
    message: str
    from_number: Optional[str] = None


class TwilioWebhookData(BaseModel):
    MessageSid: str
    From: str
    To: str
    Body: str
    FromCity: Optional[str] = None
    FromState: Optional[str] = None
    FromCountry: Optional[str] = None
    ToCity: Optional[str] = None
    ToState: Optional[str] = None
    ToCountry: Optional[str] = None
    AccountSid: str
    ApiVersion: str


async def find_user_by_phone(db: AsyncSession, phone_number: str) -> Optional[User]:
    """
    Find user by their registered phone number or contact association
    This is a simple implementation - in production, you'd have user phone registration
    """
    # For now, we'll create a default user or use first user
    # In production, you'd have proper phone number to user mapping
    query = select(User).limit(1)
    result = await db.execute(query)
    return result.scalar_one_or_none()


def validate_twilio_request(request: Request, twilio_signature: str) -> bool:
    """Validate that the request is from Twilio"""
    if not request_validator:
        return False
    
    # Get the URL and form data
    url = str(request.url)
    form_data = {}
    
    # Note: In production, you'd need to get the raw form data
    # This is a simplified version
    return request_validator.validate(url, form_data, twilio_signature)


@router.post("/webhook")
async def twilio_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    MessageSid: str = Form(...),
    From: str = Form(...),
    To: str = Form(...),
    Body: str = Form(...),
    FromCity: Optional[str] = Form(None),
    FromState: Optional[str] = Form(None),
    FromCountry: Optional[str] = Form(None),
    AccountSid: str = Form(...),
    ApiVersion: str = Form(...)
):
    """
    Handle incoming SMS messages from Twilio
    """
    try:
        await logger.ainfo("sms_webhook_received", 
                         from_number=From,
                         to_number=To,
                         message_preview=Body[:100])
        
        # Validate Twilio signature (in production)
        # twilio_signature = request.headers.get("X-Twilio-Signature", "")
        # if not validate_twilio_request(request, twilio_signature):
        #     raise HTTPException(status_code=400, detail="Invalid Twilio signature")
        
        # Find the user for this phone number
        user = await find_user_by_phone(db, To)
        if not user:
            await logger.awarning("sms_webhook_no_user", to_number=To)
            return {"status": "no_user_found"}
        
        # Check if contact exists for sender
        contact = await find_or_create_contact_by_phone(db, user.id, From, FromCity, FromState, FromCountry)
        
        # Create message record
        message = Message(
            id=uuid.uuid4(),
            user_id=user.id,
            contact_id=contact.id if contact else None,
            content=Body,
            sender=From,
            source="sms",
            received_at=datetime.utcnow(),
            processed=False,
            created_at=datetime.utcnow()
        )
        
        db.add(message)
        await db.commit()
        await db.refresh(message)
        
        # Queue message for AI processing
        process_message_task.delay(str(message.id))
        
        await logger.ainfo("sms_webhook_processed", 
                         message_id=str(message.id),
                         user_id=str(user.id),
                         contact_id=str(contact.id) if contact else None)
        
        # Return TwiML response (empty for now - no auto-reply)
        return {
            "status": "processed",
            "message_id": str(message.id)
        }
        
    except Exception as e:
        await logger.aerror("sms_webhook_error", error=str(e))
        raise HTTPException(status_code=500, detail="Internal server error")


async def find_or_create_contact_by_phone(
    db: AsyncSession, 
    user_id: uuid.UUID, 
    phone: str,
    city: Optional[str] = None,
    state: Optional[str] = None,
    country: Optional[str] = None
) -> Optional[Contact]:
    """Find or create contact by phone number"""
    
    # Try to find existing contact by phone
    query = select(Contact).where(
        Contact.user_id == user_id,
        Contact.phone == phone
    )
    result = await db.execute(query)
    contact = result.scalar_one_or_none()
    
    if contact:
        return contact
    
    # Create new contact
    contact = Contact(
        id=uuid.uuid4(),
        user_id=user_id,
        name=f"Contact {phone}",  # Placeholder name
        phone=phone,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        last_contact=datetime.utcnow()
    )
    
    db.add(contact)
    return contact


@router.post("/send")
async def send_sms(
    sms_data: SMSMessage,
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    Send SMS message via Twilio
    """
    if not twilio_client:
        raise HTTPException(
            status_code=503,
            detail="SMS service not configured"
        )
    
    try:
        await logger.ainfo("sms_send_started", 
                         to=sms_data.to,
                         message_preview=sms_data.message[:100])
        
        # Send via Twilio
        message = twilio_client.messages.create(
            body=sms_data.message,
            from_=sms_data.from_number or TWILIO_FROM_NUMBER,
            to=sms_data.to
        )
        
        await logger.ainfo("sms_send_completed", 
                         to=sms_data.to,
                         message_sid=message.sid)
        
        return {
            "status": "sent",
            "message_sid": message.sid,
            "to": sms_data.to
        }
        
    except Exception as e:
        await logger.aerror("sms_send_error", 
                          to=sms_data.to,
                          error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to send SMS: {str(e)}")


@router.get("/status/{message_sid}")
async def get_sms_status(message_sid: str) -> Dict[str, Any]:
    """
    Get SMS delivery status from Twilio
    """
    if not twilio_client:
        raise HTTPException(
            status_code=503,
            detail="SMS service not configured"
        )
    
    try:
        message = twilio_client.messages(message_sid).fetch()
        
        return {
            "message_sid": message.sid,
            "status": message.status,
            "direction": message.direction,
            "from": message.from_,
            "to": message.to,
            "date_created": message.date_created.isoformat() if message.date_created else None,
            "date_updated": message.date_updated.isoformat() if message.date_updated else None,
            "price": message.price,
            "price_unit": message.price_unit,
            "error_code": message.error_code,
            "error_message": message.error_message
        }
        
    except Exception as e:
        await logger.aerror("sms_status_error", 
                          message_sid=message_sid,
                          error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to get SMS status: {str(e)}")


# Utility function for workflows to send SMS
async def send_workflow_sms(
    to: str, 
    message: str, 
    template_data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Send SMS from workflow with template substitution
    
    Args:
        to: Phone number to send to
        message: Message template with {{variables}}
        template_data: Data for template substitution
        
    Returns:
        Send result
    """
    if not twilio_client:
        return {"status": "error", "error": "SMS not configured"}
    
    try:
        # Simple template substitution
        if template_data:
            for key, value in template_data.items():
                message = message.replace(f"{{{{{key}}}}}", str(value))
        
        result = twilio_client.messages.create(
            body=message,
            from_=TWILIO_FROM_NUMBER,
            to=to
        )
        
        return {
            "status": "sent",
            "message_sid": result.sid,
            "to": to
        }
        
    except Exception as e:
        await logger.aerror("workflow_sms_error", to=to, error=str(e))
        return {
            "status": "error",
            "error": str(e)
        }