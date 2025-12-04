"""
Google Calendar integration for CRM Escort AI
Handles OAuth flow and calendar sync operations
"""
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import structlog

logger = structlog.get_logger(__name__)


class GoogleCalendarIntegration:
    """Google Calendar API integration"""
    
    def __init__(self):
        self.client_id = os.getenv("GOOGLE_CLIENT_ID")
        self.client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
        self.redirect_uri = os.getenv("GOOGLE_REDIRECT_URI")
        self.scopes = ['https://www.googleapis.com/auth/calendar']
        
        if not all([self.client_id, self.client_secret, self.redirect_uri]):
            logger.warning("Google Calendar credentials not configured")
    
    def get_auth_url(self, user_id: str) -> str:
        """Generate OAuth authorization URL"""
        if not self.client_id:
            raise ValueError("Google Calendar not configured")
        
        flow = Flow.from_client_config({
            "web": {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [self.redirect_uri]
            }
        }, self.scopes)
        
        flow.redirect_uri = self.redirect_uri
        auth_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            state=user_id
        )
        
        return auth_url
    
    def exchange_code(self, code: str, state: str) -> Dict:
        """Exchange authorization code for tokens"""
        flow = Flow.from_client_config({
            "web": {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [self.redirect_uri]
            }
        }, self.scopes)
        
        flow.redirect_uri = self.redirect_uri
        flow.fetch_token(code=code)
        
        credentials = flow.credentials
        return {
            "access_token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "token_uri": credentials.token_uri,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "scopes": credentials.scopes,
            "expiry": credentials.expiry.isoformat() if credentials.expiry else None
        }
    
    def refresh_credentials(self, refresh_token: str) -> Dict:
        """Refresh expired credentials"""
        credentials = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self.client_id,
            client_secret=self.client_secret
        )
        
        credentials.refresh(GoogleRequest())
        
        return {
            "access_token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "expiry": credentials.expiry.isoformat() if credentials.expiry else None
        }
    
    def create_event(self, credentials_data: Dict, event_data: Dict) -> Dict:
        """Create calendar event"""
        credentials = Credentials(
            token=credentials_data.get("access_token"),
            refresh_token=credentials_data.get("refresh_token"),
            token_uri=credentials_data.get("token_uri"),
            client_id=credentials_data.get("client_id"),
            client_secret=credentials_data.get("client_secret"),
            scopes=credentials_data.get("scopes")
        )
        
        service = build('calendar', 'v3', credentials=credentials)
        
        # Convert event data to Google Calendar format
        google_event = {
            'summary': event_data.get('title'),
            'description': event_data.get('description'),
            'start': {
                'dateTime': event_data.get('start_time'),
                'timeZone': event_data.get('timezone', 'UTC'),
            },
            'end': {
                'dateTime': event_data.get('end_time'),
                'timeZone': event_data.get('timezone', 'UTC'),
            }
        }
        
        if event_data.get('location'):
            google_event['location'] = event_data['location']
        
        if event_data.get('attendees'):
            google_event['attendees'] = [
                {'email': email} for email in event_data['attendees']
            ]
        
        event = service.events().insert(
            calendarId='primary',
            body=google_event
        ).execute()
        
        logger.info("google_event_created", event_id=event.get('id'))
        return event
    
    def list_events(self, credentials_data: Dict, start_date: datetime, end_date: datetime) -> List[Dict]:
        """List calendar events in date range"""
        credentials = Credentials(
            token=credentials_data.get("access_token"),
            refresh_token=credentials_data.get("refresh_token"),
            token_uri=credentials_data.get("token_uri"),
            client_id=credentials_data.get("client_id"),
            client_secret=credentials_data.get("client_secret"),
            scopes=credentials_data.get("scopes")
        )
        
        service = build('calendar', 'v3', credentials=credentials)
        
        events_result = service.events().list(
            calendarId='primary',
            timeMin=start_date.isoformat() + 'Z',
            timeMax=end_date.isoformat() + 'Z',
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        
        # Convert to our format
        formatted_events = []
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            end = event['end'].get('dateTime', event['end'].get('date'))
            
            formatted_events.append({
                'external_id': event['id'],
                'title': event.get('summary', 'No title'),
                'description': event.get('description', ''),
                'start_time': start,
                'end_time': end,
                'location': event.get('location', ''),
                'attendees': [attendee.get('email') for attendee in event.get('attendees', [])],
                'source': 'google_calendar'
            })
        
        return formatted_events


google_calendar = GoogleCalendarIntegration()