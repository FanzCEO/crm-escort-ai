"""
OpenAI integration for CRM Escort AI
Handles message extraction and processing
"""
import os
from typing import Dict, List, Optional, Any
import openai
from openai import AsyncOpenAI
import json
import re
from datetime import datetime, timedelta
import structlog

logger = structlog.get_logger()

# Initialize OpenAI client with optional API key
api_key = os.getenv("OPENAI_API_KEY")
if api_key:
    client = AsyncOpenAI(api_key=api_key)
else:
    client = None
    logger.warning("OpenAI API key not set. AI extraction features will be disabled.")

# AI extraction prompt
EXTRACTION_PROMPT = """
You are an AI assistant that extracts structured data from messages. 
Extract the following information from the given message:

1. CONTACTS: People mentioned with their details
   - name (required)
   - phone (if mentioned)
   - email (if mentioned)
   - organization (if mentioned)
   - role (if mentioned)

2. EVENTS: Meetings, appointments, bookings
   - title (what is the meeting about)
   - start_time (ISO format, infer timezone if not specified)
   - end_time (ISO format, estimate duration if not specified)
   - location (if mentioned)
   - attendees (list of names/emails)

3. TASKS: Action items, follow-ups, todos
   - title (what needs to be done)
   - description (more details if available)
   - due_date (ISO format if deadline mentioned)
   - priority (low/medium/high/urgent based on language)

4. LOCATIONS: Places mentioned
   - name (hotel name, address, landmark)
   - address (if provided)
   - city (if mentioned)
   - state (if mentioned)
   - type (hotel/airbnb/office/home/other)

5. INTENT: Overall message intent
   - type (meeting/booking/collaboration/urgent/casual/question/confirmation)
   - confidence (0.0 to 1.0)

Return ONLY valid JSON with the structure:
{
  "contacts": [...],
  "events": [...],
  "tasks": [...],
  "locations": [...],
  "intent": {...}
}

If no information is found for a category, return an empty array or null.
Be conservative - only extract information you are confident about.
"""


class AIExtractor:
    """OpenAI-powered data extractor"""
    
    def __init__(self):
        self.model = "gpt-4-turbo-preview"
        self.max_retries = 3
    
    async def extract_message_data(
        self, 
        message_content: str,
        sender: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Extract structured data from a message using OpenAI
        
        Args:
            message_content: The message text to analyze
            sender: Who sent the message
            context: Additional context (previous messages, contact history, etc.)
            
        Returns:
            Extracted data as dictionary
        """
        # Return empty result if no OpenAI client available
        if not client:
            logger.warning("OpenAI client not available. Returning empty extraction results.")
            return {
                "contacts": [],
                "events": [],
                "tasks": [],
                "locations": [],
                "intent": None
            }
        
        try:
            # Build the prompt with context
            full_prompt = self._build_prompt(message_content, sender, context)
            
            await logger.ainfo("ai_extraction_started", 
                             message_preview=message_content[:100],
                             sender=sender)
            
            # Call OpenAI API
            response = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": EXTRACTION_PROMPT},
                    {"role": "user", "content": full_prompt}
                ],
                temperature=0.1,  # Low temperature for consistent extraction
                max_tokens=2000,
                response_format={"type": "json_object"}
            )
            
            # Parse response
            extracted_text = response.choices[0].message.content
            extracted_data = json.loads(extracted_text)
            
            # Post-process and validate
            validated_data = self._validate_extracted_data(extracted_data)
            
            await logger.ainfo("ai_extraction_completed", 
                             contacts_found=len(validated_data.get("contacts", [])),
                             events_found=len(validated_data.get("events", [])),
                             tasks_found=len(validated_data.get("tasks", [])),
                             locations_found=len(validated_data.get("locations", [])))
            
            return validated_data
            
        except json.JSONDecodeError as e:
            await logger.aerror("ai_extraction_json_error", error=str(e))
            return self._empty_result()
            
        except Exception as e:
            await logger.aerror("ai_extraction_error", error=str(e))
            return self._empty_result()
    
    def _build_prompt(
        self, 
        message_content: str, 
        sender: str, 
        context: Optional[Dict[str, Any]] = None
    ) -> str:
        """Build the extraction prompt with context"""
        
        prompt_parts = [
            f"MESSAGE FROM: {sender}",
            f"MESSAGE CONTENT:\n{message_content}"
        ]
        
        if context:
            if context.get("previous_messages"):
                prompt_parts.append(f"PREVIOUS MESSAGES CONTEXT:\n{context['previous_messages']}")
            
            if context.get("existing_contacts"):
                prompt_parts.append(f"KNOWN CONTACTS:\n{context['existing_contacts']}")
        
        return "\n\n".join(prompt_parts)
    
    def _validate_extracted_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and clean extracted data"""
        
        result: Dict[str, Any] = {
            "contacts": [],
            "events": [],
            "tasks": [],
            "locations": [],
            "intent": None
        }
        
        # Validate contacts
        for contact in data.get("contacts", []):
            if isinstance(contact, dict) and contact.get("name"):
                clean_contact = {
                    "name": str(contact["name"]).strip(),
                    "phone": self._clean_phone(contact.get("phone")),
                    "email": self._clean_email(contact.get("email")),
                    "organization": contact.get("organization"),
                    "role": contact.get("role")
                }
                result["contacts"].append(clean_contact)
        
        # Validate events
        for event in data.get("events", []):
            if isinstance(event, dict) and event.get("title"):
                clean_event = {
                    "title": str(event["title"]).strip(),
                    "start_time": self._parse_datetime(event.get("start_time")),
                    "end_time": self._parse_datetime(event.get("end_time")),
                    "location": event.get("location"),
                    "attendees": event.get("attendees", [])
                }
                if clean_event["start_time"]:  # Only add if we have a valid start time
                    result["events"].append(clean_event)
        
        # Validate tasks
        for task in data.get("tasks", []):
            if isinstance(task, dict) and task.get("title"):
                clean_task = {
                    "title": str(task["title"]).strip(),
                    "description": task.get("description"),
                    "due_date": self._parse_datetime(task.get("due_date")),
                    "priority": self._validate_priority(task.get("priority"))
                }
                result["tasks"].append(clean_task)
        
        # Validate locations
        for location in data.get("locations", []):
            if isinstance(location, dict) and location.get("name"):
                clean_location = {
                    "name": str(location["name"]).strip(),
                    "address": location.get("address"),
                    "city": location.get("city"),
                    "state": location.get("state"),
                    "type": self._validate_location_type(location.get("type"))
                }
                result["locations"].append(clean_location)
        
        # Validate intent
        intent = data.get("intent")
        if isinstance(intent, dict):
            result["intent"] = {
                "type": self._validate_intent_type(intent.get("type")),
                "confidence": min(max(float(intent.get("confidence", 0.5)), 0.0), 1.0)
            }
        
        return result
    
    def _clean_phone(self, phone: Optional[str]) -> Optional[str]:
        """Clean and validate phone number"""
        if not phone:
            return None
        
        # Remove all non-digit characters
        digits = re.sub(r'[^\d+]', '', str(phone))
        
        # Basic validation (at least 10 digits)
        if len(digits.replace('+', '')) >= 10:
            return digits
        
        return None
    
    def _clean_email(self, email: Optional[str]) -> Optional[str]:
        """Clean and validate email"""
        if not email:
            return None
        
        email = str(email).strip().lower()
        
        # Basic email validation
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if re.match(email_pattern, email):
            return email
        
        return None
    
    def _parse_datetime(self, dt_str: Optional[str]) -> Optional[datetime]:
        """Parse datetime string"""
        if not dt_str:
            return None
        
        try:
            # Try ISO format first
            return datetime.fromisoformat(str(dt_str).replace('Z', '+00:00'))
        except ValueError:
            try:
                # Try other common formats
                for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d']:
                    return datetime.strptime(str(dt_str), fmt)
            except ValueError:
                return None
        
        return None
    
    def _validate_priority(self, priority: Optional[str]) -> str:
        """Validate task priority"""
        if not priority:
            return "medium"
        
        priority = str(priority).lower()
        if priority in ["low", "medium", "high", "urgent"]:
            return priority
        
        return "medium"
    
    def _validate_location_type(self, loc_type: Optional[str]) -> str:
        """Validate location type"""
        if not loc_type:
            return "other"
        
        loc_type = str(loc_type).lower()
        if loc_type in ["home", "hotel", "airbnb", "office", "other"]:
            return loc_type
        
        return "other"
    
    def _validate_intent_type(self, intent_type: Optional[str]) -> str:
        """Validate intent type"""
        if not intent_type:
            return "casual"
        
        intent_type = str(intent_type).lower()
        valid_types = ["meeting", "booking", "collaboration", "urgent", "casual", "question", "confirmation"]
        
        if intent_type in valid_types:
            return intent_type
        
        return "casual"
    
    def _empty_result(self) -> Dict[str, Any]:
        """Return empty extraction result"""
        return {
            "contacts": [],
            "events": [],
            "tasks": [],
            "locations": [],
            "intent": {"type": "casual", "confidence": 0.0}
        }


# Global extractor instance
ai_extractor = AIExtractor()


async def extract_message_data(
    message_content: str,
    sender: str,
    context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Main function to extract data from a message
    
    Args:
        message_content: The message text
        sender: Who sent the message
        context: Additional context
        
    Returns:
        Extracted structured data
    """
    return await ai_extractor.extract_message_data(message_content, sender, context)