"""
Microsoft Outlook Calendar Integration for CRM Escort AI
Provides OAuth authentication and calendar event management
"""
import os
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import structlog
from msal import ConfidentialClientApplication
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import httpx

from app.models import User

logger = structlog.get_logger(__name__)

class OutlookCalendarManager:
    """Microsoft Outlook Calendar integration manager"""
    
    def __init__(self):
        self.client_id = os.getenv("OUTLOOK_CLIENT_ID")
        self.client_secret = os.getenv("OUTLOOK_CLIENT_SECRET")
        self.tenant_id = os.getenv("OUTLOOK_TENANT_ID", "common")
        self.redirect_uri = os.getenv("OUTLOOK_REDIRECT_URI", "http://localhost:8000/calendar/outlook/callback")
        
        # Microsoft Graph scopes
        self.scopes = [
            "https://graph.microsoft.com/Calendars.ReadWrite",
            "https://graph.microsoft.com/User.Read"
        ]
        
        # Graph API endpoints
        self.graph_url = "https://graph.microsoft.com/v1.0"
    
    def get_authorization_url(self, user_id: str) -> str:
        """Get OAuth authorization URL"""
        if not self.client_id or not self.client_secret:
            raise ValueError("Outlook OAuth credentials not configured")
        
        app = ConfidentialClientApplication(
            self.client_id,
            authority=f"https://login.microsoftonline.com/{self.tenant_id}",
            client_credential=self.client_secret
        )
        
        auth_url = app.get_authorization_request_url(
            scopes=self.scopes,
            redirect_uri=self.redirect_uri,
            state=user_id
        )
        
        return auth_url
    
    async def handle_oauth_callback(self, code: str, state: str, db: AsyncSession) -> Dict[str, Any]:
        """Handle OAuth callback and store credentials"""
        if not self.client_id or not self.client_secret:
            raise ValueError("Outlook OAuth credentials not configured")
        
        try:
            app = ConfidentialClientApplication(
                self.client_id,
                authority=f"https://login.microsoftonline.com/{self.tenant_id}",
                client_credential=self.client_secret
            )
            
            # Exchange code for token
            result = app.acquire_token_by_authorization_code(
                code,
                scopes=self.scopes,
                redirect_uri=self.redirect_uri
            )
            
            if "error" in result:
                raise ValueError(f"OAuth error: {result.get('error_description', 'Unknown error')}")
            
            # Store credentials in user settings
            user_id = state
            db_result = await db.execute(select(User).where(User.id == user_id))
            user = db_result.scalar_one_or_none()
            
            if user:
                if not user.settings:
                    user.settings = {}
                
                user.settings["outlook_calendar"] = {
                    "access_token": result.get("access_token"),
                    "refresh_token": result.get("refresh_token"),
                    "expires_in": result.get("expires_in"),
                    "scope": result.get("scope"),
                    "token_type": result.get("token_type", "Bearer")
                }
                
                await db.commit()
                
                logger.info("Outlook Calendar connected", user_id=user_id)
                return {"status": "success", "message": "Outlook Calendar connected successfully"}
            
            else:
                raise ValueError(f"User {user_id} not found")
                
        except Exception as e:
            logger.error("Outlook Calendar OAuth error", error=str(e))
            raise ValueError(f"OAuth callback failed: {str(e)}")
    
    def _get_access_token(self, user: User) -> Optional[str]:
        """Get access token for user"""
        if not user.settings or "outlook_calendar" not in user.settings:
            return None
        
        cred_data = user.settings["outlook_calendar"]
        return cred_data.get("access_token")
    
    async def list_events(self, user: User, start_date: Optional[datetime] = None, 
                         end_date: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """List events from Outlook Calendar"""
        access_token = self._get_access_token(user)
        if not access_token:
            raise ValueError("Outlook Calendar not connected")
        
        try:
            # Default date range if not provided
            if not start_date:
                start_date = datetime.utcnow()
            if not end_date:
                end_date = start_date + timedelta(days=30)
            
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }
            
            # Format dates for Microsoft Graph API
            start_str = start_date.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            end_str = end_date.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            
            # Get events from Microsoft Graph
            url = f"{self.graph_url}/me/calendar/calendarView"
            params = {
                "startDateTime": start_str,
                "endDateTime": end_str,
                "$orderby": "start/dateTime"
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()
            
            events = data.get("value", [])
            
            # Format events
            formatted_events = []
            for event in events:
                formatted_event = {
                    'id': event.get('id'),
                    'title': event.get('subject', 'No Title'),
                    'description': event.get('body', {}).get('content', ''),
                    'start': self._parse_datetime(event.get('start', {})),
                    'end': self._parse_datetime(event.get('end', {})),
                    'location': event.get('location', {}).get('displayName', ''),
                    'attendees': [
                        attendee.get('emailAddress', {}).get('address') 
                        for attendee in event.get('attendees', [])
                    ],
                    'source': 'outlook'
                }
                formatted_events.append(formatted_event)
            
            return formatted_events
            
        except Exception as e:
            logger.error("Error fetching Outlook Calendar events", error=str(e))
            raise ValueError(f"Failed to fetch events: {str(e)}")
    
    async def create_event(self, user: User, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create event in Outlook Calendar"""
        access_token = self._get_access_token(user)
        if not access_token:
            raise ValueError("Outlook Calendar not connected")
        
        try:
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }
            
            # Format attendees
            attendees = []
            for email in event_data.get('attendees', []):
                attendees.append({
                    "emailAddress": {
                        "address": email,
                        "name": email.split('@')[0]  # Use part before @ as name
                    },
                    "type": "required"
                })
            
            # Format event for Microsoft Graph
            outlook_event = {
                "subject": event_data.get('title'),
                "body": {
                    "contentType": "text",
                    "content": event_data.get('description', '')
                },
                "start": self._format_datetime(event_data['start_time']) if event_data.get('start_time') else None,
                "end": self._format_datetime(event_data['end_time']) if event_data.get('end_time') else None,
                "location": {
                    "displayName": event_data.get('location', '')
                },
                "attendees": attendees
            }
            
            # Validate required fields
            if not outlook_event['start'] or not outlook_event['end']:
                raise ValueError("Event start_time and end_time are required")
            
            # Create event
            url = f"{self.graph_url}/me/calendar/events"
            
            async with httpx.AsyncClient() as client:
                response = await client.post(url, headers=headers, json=outlook_event)
                response.raise_for_status()
                created_event = response.json()
            
            return {
                'id': created_event.get('id'),
                'title': created_event.get('subject'),
                'web_link': created_event.get('webLink'),
                'status': 'created'
            }
            
        except Exception as e:
            logger.error("Error creating Outlook Calendar event", error=str(e))
            raise ValueError(f"Failed to create event: {str(e)}")
    
    async def disconnect(self, user: User, db: AsyncSession) -> Dict[str, Any]:
        """Disconnect Outlook Calendar"""
        if user.settings and "outlook_calendar" in user.settings:
            del user.settings["outlook_calendar"]
            await db.commit()
            logger.info("Outlook Calendar disconnected", user_id=user.id)
        
        return {"status": "success", "message": "Outlook Calendar disconnected"}
    
    def _parse_datetime(self, datetime_data: Dict) -> Optional[datetime]:
        """Parse Microsoft Graph datetime"""
        date_time = datetime_data.get('dateTime')
        if date_time:
            # Microsoft Graph returns ISO format
            return datetime.fromisoformat(date_time.replace('Z', '+00:00'))
        return None
    
    def _format_datetime(self, dt: datetime) -> Dict[str, str]:
        """Format datetime for Microsoft Graph"""
        return {
            "dateTime": dt.isoformat(),
            "timeZone": "UTC"
        }