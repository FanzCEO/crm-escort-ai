"""
Outlook Calendar integration for CRM Escort AI
Handles OAuth flow and calendar sync operations
"""
import os
from datetime import datetime
from typing import Dict, List
import logging

logger = logging.getLogger(__name__)


class OutlookCalendarIntegration:
    """Microsoft Outlook/Office 365 Calendar API integration"""
    
    def __init__(self):
        self.client_id = os.getenv("OUTLOOK_CLIENT_ID")
        self.client_secret = os.getenv("OUTLOOK_CLIENT_SECRET")
        self.redirect_uri = os.getenv("OUTLOOK_REDIRECT_URI")
        self.scopes = ['https://graph.microsoft.com/calendars.readwrite']
        
        if not all([self.client_id, self.client_secret, self.redirect_uri]):
            logger.warning("Outlook Calendar credentials not configured")
    
    def get_auth_url(self, user_id: str) -> str:
        """Generate OAuth authorization URL for Microsoft Graph"""
        if not self.client_id:
            raise ValueError("Outlook Calendar not configured")
        
        # Microsoft Graph OAuth endpoint
        auth_url = (
            f"https://login.microsoftonline.com/common/oauth2/v2.0/authorize?"
            f"client_id={self.client_id}"
            f"&response_type=code"
            f"&redirect_uri={self.redirect_uri}"
            f"&response_mode=query"
            f"&scope={' '.join(self.scopes)}"
            f"&state={user_id}"
        )
        
        return auth_url
    
    def exchange_code(self, code: str, state: str) -> Dict:
        """Exchange authorization code for tokens"""
        import requests
        
        token_url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        
        data = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'code': code,
            'redirect_uri': self.redirect_uri,
            'grant_type': 'authorization_code',
            'scope': ' '.join(self.scopes)
        }
        
        response = requests.post(token_url, data=data)
        response.raise_for_status()
        
        token_data = response.json()
        return {
            "access_token": token_data.get("access_token"),
            "refresh_token": token_data.get("refresh_token"),
            "expires_in": token_data.get("expires_in"),
            "token_type": token_data.get("token_type"),
            "scope": token_data.get("scope")
        }
    
    def refresh_credentials(self, refresh_token: str) -> Dict:
        """Refresh expired credentials"""
        import requests
        
        token_url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        
        data = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'refresh_token': refresh_token,
            'grant_type': 'refresh_token',
            'scope': ' '.join(self.scopes)
        }
        
        response = requests.post(token_url, data=data)
        response.raise_for_status()
        
        token_data = response.json()
        return {
            "access_token": token_data.get("access_token"),
            "refresh_token": token_data.get("refresh_token"),
            "expires_in": token_data.get("expires_in")
        }
    
    def create_event(self, access_token: str, event_data: Dict) -> Dict:
        """Create calendar event"""
        import requests
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        # Convert to Microsoft Graph format
        graph_event = {
            "subject": event_data.get('title'),
            "body": {
                "contentType": "text",
                "content": event_data.get('description', '')
            },
            "start": {
                "dateTime": event_data.get('start_time'),
                "timeZone": event_data.get('timezone', 'UTC')
            },
            "end": {
                "dateTime": event_data.get('end_time'),
                "timeZone": event_data.get('timezone', 'UTC')
            }
        }
        
        if event_data.get('location'):
            graph_event['location'] = {
                "displayName": event_data['location']
            }
        
        if event_data.get('attendees'):
            graph_event['attendees'] = [
                {
                    "emailAddress": {
                        "address": email,
                        "name": email
                    }
                } for email in event_data['attendees']
            ]
        
        response = requests.post(
            "https://graph.microsoft.com/v1.0/me/events",
            headers=headers,
            json=graph_event
        )
        response.raise_for_status()
        
        event = response.json()
        logger.info(f"Outlook event created: {event.get('id')}")
        return event
    
    def list_events(self, access_token: str, start_date: datetime, end_date: datetime) -> List[Dict]:
        """List calendar events in date range"""
        import requests
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        # Microsoft Graph API endpoint with date filter
        url = (
            f"https://graph.microsoft.com/v1.0/me/events?"
            f"$filter=start/dateTime ge '{start_date.isoformat()}' and "
            f"end/dateTime le '{end_date.isoformat()}'"
            f"&$orderby=start/dateTime"
        )
        
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        
        events_data = response.json()
        events = events_data.get('value', [])
        
        # Convert to our format
        formatted_events = []
        for event in events:
            formatted_events.append({
                'external_id': event['id'],
                'title': event.get('subject', 'No title'),
                'description': event.get('body', {}).get('content', ''),
                'start_time': event['start']['dateTime'],
                'end_time': event['end']['dateTime'],
                'location': event.get('location', {}).get('displayName', ''),
                'attendees': [
                    attendee['emailAddress']['address'] 
                    for attendee in event.get('attendees', [])
                ],
                'source': 'outlook_calendar'
            })
        
        return formatted_events


outlook_calendar = OutlookCalendarIntegration()