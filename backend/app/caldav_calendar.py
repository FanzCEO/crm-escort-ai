"""
CalDAV Calendar Integration for Apple iCloud and Samsung Calendar
Supports syncing events to/from external calendar providers
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from enum import Enum

import caldav
from icalendar import Calendar, Event, vText
from icalendar.prop import vDatetime

logger = logging.getLogger(__name__)


class CalendarProvider(str, Enum):
    APPLE = "apple"
    SAMSUNG = "samsung"
    INTERNAL = "internal"


@dataclass
class CalendarConfig:
    """Configuration for CalDAV calendar providers"""
    provider: CalendarProvider
    url: str
    username: str
    password: str  # App-specific password for Apple, account password for Samsung
    calendar_name: Optional[str] = None  # Specific calendar to use


# CalDAV server URLs
CALDAV_URLS = {
    CalendarProvider.APPLE: "https://caldav.icloud.com",
    CalendarProvider.SAMSUNG: "https://caldav.samsung.com",
}


class CalDAVCalendarManager:
    """
    Manages CalDAV calendar sync for Apple iCloud and Samsung Calendar
    """

    def __init__(self, config: CalendarConfig):
        self.config = config
        self.client: Optional[caldav.DAVClient] = None
        self.principal: Optional[caldav.Principal] = None
        self.calendar: Optional[caldav.Calendar] = None

    async def connect(self) -> bool:
        """Connect to the CalDAV server"""
        try:
            url = self.config.url or CALDAV_URLS.get(self.config.provider)
            if not url:
                raise ValueError(f"No URL configured for provider {self.config.provider}")

            self.client = caldav.DAVClient(
                url=url,
                username=self.config.username,
                password=self.config.password,
            )

            self.principal = self.client.principal()

            # Get specific calendar or default
            calendars = self.principal.calendars()
            if not calendars:
                logger.warning(f"No calendars found for {self.config.provider}")
                return False

            if self.config.calendar_name:
                for cal in calendars:
                    if cal.name == self.config.calendar_name:
                        self.calendar = cal
                        break
                if not self.calendar:
                    logger.warning(f"Calendar '{self.config.calendar_name}' not found, using default")
                    self.calendar = calendars[0]
            else:
                self.calendar = calendars[0]

            logger.info(f"Connected to {self.config.provider} calendar: {self.calendar.name}")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to {self.config.provider}: {e}")
            return False

    async def list_calendars(self) -> List[Dict[str, str]]:
        """List all available calendars"""
        if not self.principal:
            await self.connect()

        calendars = []
        for cal in self.principal.calendars():
            calendars.append({
                "id": str(cal.id) if hasattr(cal, 'id') else cal.url.path,
                "name": cal.name,
                "url": str(cal.url),
            })
        return calendars

    async def create_event(
        self,
        title: str,
        start_time: datetime,
        end_time: datetime,
        description: Optional[str] = None,
        location: Optional[str] = None,
        contact_name: Optional[str] = None,
    ) -> Optional[str]:
        """
        Create a new calendar event
        Returns the event UID if successful
        """
        if not self.calendar:
            if not await self.connect():
                return None

        try:
            # Create iCalendar event
            cal = Calendar()
            cal.add('prodid', '-//CRM Escort AI//caldav_calendar//EN')
            cal.add('version', '2.0')

            event = Event()
            event.add('summary', title)
            event.add('dtstart', start_time)
            event.add('dtend', end_time)
            event.add('dtstamp', datetime.utcnow())

            if description:
                event.add('description', description)
            if location:
                event.add('location', location)
            if contact_name:
                event.add('x-crm-contact', contact_name)

            # Generate UID
            uid = f"crm-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{hash(title) % 10000}@escort-ai"
            event.add('uid', uid)

            cal.add_component(event)

            # Save to CalDAV server
            self.calendar.save_event(cal.to_ical().decode('utf-8'))

            logger.info(f"Created event '{title}' on {self.config.provider} calendar")
            return uid

        except Exception as e:
            logger.error(f"Failed to create event on {self.config.provider}: {e}")
            return None

    async def get_events(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get events from the calendar
        """
        if not self.calendar:
            if not await self.connect():
                return []

        try:
            # Default to next 30 days if no range specified
            if not start_date:
                start_date = datetime.now()
            if not end_date:
                end_date = start_date + timedelta(days=30)

            events = self.calendar.search(
                start=start_date,
                end=end_date,
                event=True,
                expand=True,
            )

            result = []
            for event in events:
                try:
                    ical = Calendar.from_ical(event.data)
                    for component in ical.walk():
                        if component.name == "VEVENT":
                            result.append({
                                "uid": str(component.get('uid', '')),
                                "title": str(component.get('summary', 'No Title')),
                                "start": component.get('dtstart').dt if component.get('dtstart') else None,
                                "end": component.get('dtend').dt if component.get('dtend') else None,
                                "description": str(component.get('description', '')),
                                "location": str(component.get('location', '')),
                                "provider": self.config.provider.value,
                            })
                except Exception as e:
                    logger.warning(f"Failed to parse event: {e}")
                    continue

            return result

        except Exception as e:
            logger.error(f"Failed to get events from {self.config.provider}: {e}")
            return []

    async def update_event(
        self,
        uid: str,
        title: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        description: Optional[str] = None,
        location: Optional[str] = None,
    ) -> bool:
        """Update an existing event by UID"""
        if not self.calendar:
            if not await self.connect():
                return False

        try:
            # Search for the event
            events = self.calendar.events()
            target_event = None

            for event in events:
                ical = Calendar.from_ical(event.data)
                for component in ical.walk():
                    if component.name == "VEVENT" and str(component.get('uid', '')) == uid:
                        target_event = event
                        break
                if target_event:
                    break

            if not target_event:
                logger.warning(f"Event {uid} not found on {self.config.provider}")
                return False

            # Parse and update
            ical = Calendar.from_ical(target_event.data)
            for component in ical.walk():
                if component.name == "VEVENT":
                    if title:
                        component['summary'] = vText(title)
                    if start_time:
                        component['dtstart'] = vDatetime(start_time)
                    if end_time:
                        component['dtend'] = vDatetime(end_time)
                    if description:
                        component['description'] = vText(description)
                    if location:
                        component['location'] = vText(location)

            target_event.save(ical.to_ical().decode('utf-8'))
            logger.info(f"Updated event {uid} on {self.config.provider}")
            return True

        except Exception as e:
            logger.error(f"Failed to update event on {self.config.provider}: {e}")
            return False

    async def delete_event(self, uid: str) -> bool:
        """Delete an event by UID"""
        if not self.calendar:
            if not await self.connect():
                return False

        try:
            events = self.calendar.events()

            for event in events:
                ical = Calendar.from_ical(event.data)
                for component in ical.walk():
                    if component.name == "VEVENT" and str(component.get('uid', '')) == uid:
                        event.delete()
                        logger.info(f"Deleted event {uid} from {self.config.provider}")
                        return True

            logger.warning(f"Event {uid} not found on {self.config.provider}")
            return False

        except Exception as e:
            logger.error(f"Failed to delete event on {self.config.provider}: {e}")
            return False


# Factory functions for easy initialization
def get_apple_calendar(
    username: str,
    app_password: str,
    calendar_name: Optional[str] = None,
) -> CalDAVCalendarManager:
    """
    Get Apple iCloud calendar manager

    Args:
        username: Apple ID email
        app_password: App-specific password (generate at appleid.apple.com)
        calendar_name: Optional specific calendar name
    """
    config = CalendarConfig(
        provider=CalendarProvider.APPLE,
        url=CALDAV_URLS[CalendarProvider.APPLE],
        username=username,
        password=app_password,
        calendar_name=calendar_name,
    )
    return CalDAVCalendarManager(config)


def get_samsung_calendar(
    username: str,
    password: str,
    calendar_name: Optional[str] = None,
) -> CalDAVCalendarManager:
    """
    Get Samsung calendar manager

    Args:
        username: Samsung account email
        password: Samsung account password
        calendar_name: Optional specific calendar name
    """
    config = CalendarConfig(
        provider=CalendarProvider.SAMSUNG,
        url=CALDAV_URLS[CalendarProvider.SAMSUNG],
        username=username,
        password=password,
        calendar_name=calendar_name,
    )
    return CalDAVCalendarManager(config)


class MultiCalendarSync:
    """
    Sync events across multiple calendar providers
    """

    def __init__(self):
        self.providers: Dict[CalendarProvider, CalDAVCalendarManager] = {}

    def add_provider(self, manager: CalDAVCalendarManager):
        """Add a calendar provider"""
        self.providers[manager.config.provider] = manager

    async def sync_event_to_all(
        self,
        title: str,
        start_time: datetime,
        end_time: datetime,
        description: Optional[str] = None,
        location: Optional[str] = None,
        contact_name: Optional[str] = None,
    ) -> Dict[str, Optional[str]]:
        """
        Create event on all configured providers
        Returns dict of provider -> UID mappings
        """
        results = {}
        for provider, manager in self.providers.items():
            uid = await manager.create_event(
                title=title,
                start_time=start_time,
                end_time=end_time,
                description=description,
                location=location,
                contact_name=contact_name,
            )
            results[provider.value] = uid
        return results

    async def get_all_events(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Get events from all providers"""
        all_events = []
        for provider, manager in self.providers.items():
            events = await manager.get_events(start_date, end_date)
            all_events.extend(events)

        # Sort by start time
        all_events.sort(key=lambda x: x.get('start') or datetime.min)
        return all_events
