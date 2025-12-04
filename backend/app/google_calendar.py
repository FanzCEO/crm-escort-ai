"""
Google Calendar Integration for CRM Escort AI
Provides OAuth authentication and calendar event management
"""
import os
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import structlog
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import User

logger = structlog.get_logger(__name__)

class GoogleCalendarManager:
    """Google Calendar integration manager"""
    
    def __init__(self):
        self.client_id = os.getenv("GOOGLE_CLIENT_ID")
        self.client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
        self.redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/calendar/google/callback")
        
        # OAuth 2.0 scopes
        self.scopes = [
            'https://www.googleapis.com/auth/calendar.readonly',
            'https://www.googleapis.com/auth/calendar.events'
        ]
    
    def get_authorization_url(self, user_id: str) -> str:
        """Get OAuth authorization URL"""
        if not self.client_id or not self.client_secret:
            raise ValueError("Google OAuth credentials not configured")
        
        client_config = {
            "web": {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [self.redirect_uri]
            }
        }
        
        flow = Flow.from_client_config(
            client_config,
            scopes=self.scopes,
            redirect_uri=self.redirect_uri
        )
        
        authorization_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            state=user_id
        )
        
        return authorization_url
    
    async def handle_oauth_callback(self, code: str, state: str, db: AsyncSession) -> Dict[str, Any]:
        """Handle OAuth callback and store credentials"""
        if not self.client_id or not self.client_secret:
            raise ValueError("Google OAuth credentials not configured")
        
        try:
            client_config = {
                "web": {
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [self.redirect_uri]
                }
            }
            
            flow = Flow.from_client_config(
                client_config,
                scopes=self.scopes,
                redirect_uri=self.redirect_uri,
                state=state
            )
            
            # Exchange code for tokens
            flow.fetch_token(code=code)
            credentials = flow.credentials
            
            # Store credentials in user settings
            user_id = state
            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()
            
            if user:
                if not user.settings:
                    user.settings = {}
                
                user.settings["google_calendar"] = {
                    "token": credentials.token,
                    "refresh_token": credentials.refresh_token,
                    "token_uri": credentials.token_uri,
                    "client_id": credentials.client_id,
                    "client_secret": credentials.client_secret,
                    "scopes": credentials.scopes,
                    "expiry": credentials.expiry.isoformat() if credentials.expiry else None
                }
                
                await db.commit()
                
                logger.info("Google Calendar connected", user_id=user_id)
                return {"status": "success", "message": "Google Calendar connected successfully"}
            
            else:
                raise ValueError(f"User {user_id} not found")
                
        except Exception as e:
            logger.error("Google Calendar OAuth error", error=str(e))
            raise ValueError(f"OAuth callback failed: {str(e)}")
    
    def _get_credentials(self, user: User) -> Optional[Credentials]:
        """Get Google credentials for user"""
        if not user.settings or "google_calendar" not in user.settings:
            return None
        
        cred_data = user.settings["google_calendar"]
        
        credentials = Credentials(
            token=cred_data.get("token"),
            refresh_token=cred_data.get("refresh_token"),
            token_uri=cred_data.get("token_uri"),
            client_id=cred_data.get("client_id"),
            client_secret=cred_data.get("client_secret"),
            scopes=cred_data.get("scopes")
        )
        
        if cred_data.get("expiry"):
            credentials.expiry = datetime.fromisoformat(cred_data["expiry"])
        
        return credentials
    
    async def list_events(self, user: User, start_date: Optional[datetime] = None, 
                         end_date: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """List events from Google Calendar"""
        credentials = self._get_credentials(user)
        if not credentials:
            raise ValueError("Google Calendar not connected")
        
        try:
            # Refresh credentials if needed
            if credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
            
            service = build('calendar', 'v3', credentials=credentials)
            
            # Default date range if not provided
            if not start_date:
                start_date = datetime.utcnow()
            if not end_date:
                end_date = start_date + timedelta(days=30)
            
            # Get events
            events_result = service.events().list(
                calendarId='primary',
                timeMin=start_date.isoformat() + 'Z',
                timeMax=end_date.isoformat() + 'Z',
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            events = events_result.get('items', [])
            
            # Format events
            formatted_events = []
            for event in events:
                formatted_event = {
                    'id': event.get('id'),
                    'title': event.get('summary', 'No Title'),
                    'description': event.get('description', ''),
                    'start': self._parse_datetime(event.get('start', {})),
                    'end': self._parse_datetime(event.get('end', {})),
                    'location': event.get('location', ''),
                    'attendees': [
                        attendee.get('email') for attendee in event.get('attendees', [])
                    ],
                    'source': 'google'
                }
                formatted_events.append(formatted_event)
            
            return formatted_events
            
        except Exception as e:
            logger.error("Error fetching Google Calendar events", error=str(e))
            raise ValueError(f"Failed to fetch events: {str(e)}")
    
    async def create_event(self, user: User, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create event in Google Calendar"""
        credentials = self._get_credentials(user)
        if not credentials:
            raise ValueError("Google Calendar not connected")
        
        try:
            # Refresh credentials if needed
            if credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
            
            service = build('calendar', 'v3', credentials=credentials)
            
            # Format event for Google Calendar
            google_event = {
                'summary': event_data.get('title'),
                'description': event_data.get('description', ''),
                'start': self._format_datetime(event_data['start_time']) if event_data.get('start_time') else None,
                'end': self._format_datetime(event_data['end_time']) if event_data.get('end_time') else None,
                'location': event_data.get('location', ''),
                'attendees': [
                    {'email': email} for email in event_data.get('attendees', [])
                ]
            }
            
            # Validate required fields
            if not google_event['start'] or not google_event['end']:
                raise ValueError("Event start_time and end_time are required")
            
            # Create event
            created_event = service.events().insert(
                calendarId='primary',
                body=google_event
            ).execute()
            
            return {
                'id': created_event.get('id'),
                'title': created_event.get('summary'),
                'html_link': created_event.get('htmlLink'),
                'status': 'created'
            }
            
        except Exception as e:
            logger.error("Error creating Google Calendar event", error=str(e))
            raise ValueError(f"Failed to create event: {str(e)}")
    
    async def disconnect(self, user: User, db: AsyncSession) -> Dict[str, Any]:
        """Disconnect Google Calendar"""
        if user.settings and "google_calendar" in user.settings:
            del user.settings["google_calendar"]
            await db.commit()
            logger.info("Google Calendar disconnected", user_id=user.id)
        
        return {"status": "success", "message": "Google Calendar disconnected"}
    
    def _parse_datetime(self, datetime_data: Dict) -> Optional[datetime]:
        """Parse Google Calendar datetime"""
        if 'dateTime' in datetime_data:
            return datetime.fromisoformat(datetime_data['dateTime'].replace('Z', '+00:00'))
        elif 'date' in datetime_data:
            return datetime.fromisoformat(datetime_data['date'])
        return None
    
    def _format_datetime(self, dt: datetime) -> Dict[str, str]:
        """Format datetime for Google Calendar"""
        return {
            'dateTime': dt.isoformat(),
            'timeZone': 'UTC'
        }