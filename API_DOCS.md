# CRM Escort AI - API Documentation

## Overview
CRM Escort AI is a comprehensive customer relationship management platform with AI-powered automation, SMS integration, calendar management, and workflow orchestration.

## Base URL
```
Production: https://your-domain.com/api/v1
Development: http://localhost:8000
```

## Authentication
All endpoints require JWT authentication via Bearer token in the Authorization header:
```
Authorization: Bearer <jwt_token>
```

### Authentication Endpoints

#### POST /auth/register
Register a new user account.

**Request Body:**
```json
{
  "email": "user@example.com",
  "password": "securepassword123",
  "full_name": "John Doe",
  "phone": "+1234567890"
}
```

**Response:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "user": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "email": "user@example.com",
    "full_name": "John Doe",
    "is_active": true
  }
}
```

#### POST /auth/login
Login with email and password.

**Request Body:**
```json
{
  "email": "user@example.com",
  "password": "securepassword123"
}
```

**Response:** Same as register

#### POST /auth/refresh
Refresh access token using refresh token.

**Request Body:**
```json
{
  "refresh_token": "eyJhbGciOiJIUzI1NiIs..."
}
```

## Contacts API

### GET /contacts
Get all contacts for the authenticated user.

**Query Parameters:**
- `limit` (int): Maximum number of contacts to return (1-500, default: 100)
- `offset` (int): Number of contacts to skip (default: 0)
- `search` (string): Search contacts by name, email, or phone
- `organization` (string): Filter by organization

**Response:**
```json
{
  "contacts": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "name": "Jane Smith",
      "email": "jane@example.com",
      "phone": "+1234567891",
      "organization": "Acme Corp",
      "notes": "VIP client",
      "tags": ["vip", "urgent"],
      "custom_fields": {"industry": "tech"},
      "created_at": "2024-01-01T10:00:00Z",
      "updated_at": "2024-01-01T10:00:00Z"
    }
  ],
  "total": 1,
  "limit": 100,
  "offset": 0
}
```

### POST /contacts
Create a new contact.

**Request Body:**
```json
{
  "name": "Jane Smith",
  "email": "jane@example.com",
  "phone": "+1234567891",
  "organization": "Acme Corp",
  "notes": "VIP client",
  "tags": ["vip", "urgent"],
  "custom_fields": {"industry": "tech"}
}
```

### GET /contacts/{contact_id}
Get a specific contact by ID.

### PUT /contacts/{contact_id}
Update an existing contact.

### DELETE /contacts/{contact_id}
Delete a contact.

## Messages API

### GET /messages
Get all messages for the authenticated user.

**Query Parameters:**
- `limit` (int): Maximum messages to return (1-500, default: 100)
- `offset` (int): Number of messages to skip (default: 0)
- `contact_id` (UUID): Filter by contact
- `processed` (bool): Filter by processing status
- `start_date` (datetime): Filter messages after date
- `end_date` (datetime): Filter messages before date

**Response:**
```json
{
  "messages": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "sender_phone": "+1234567891",
      "content": "Hi, I'd like to schedule an appointment for tomorrow at 3pm",
      "received_at": "2024-01-01T10:00:00Z",
      "processed": true,
      "contact_id": "550e8400-e29b-41d4-a716-446655440001",
      "extracted_data": {
        "contacts": [],
        "events": [
          {
            "title": "Appointment",
            "start_time": "2024-01-02T15:00:00Z",
            "description": "Appointment request"
          }
        ],
        "tasks": []
      },
      "ai_confidence": 0.95
    }
  ],
  "total": 1
}
```

### POST /messages
Create a new message (typically from SMS webhook).

**Request Body:**
```json
{
  "sender_phone": "+1234567891",
  "content": "Hi, I'd like to schedule an appointment",
  "received_at": "2024-01-01T10:00:00Z"
}
```

### POST /messages/{message_id}/process
Manually trigger AI processing for a message.

## Calendar API

### GET /calendar
Get calendar events.

**Query Parameters:**
- `start_date` (datetime): Filter events after date
- `end_date` (datetime): Filter events before date
- `limit` (int): Maximum events (1-500, default: 100)
- `offset` (int): Skip events (default: 0)

**Response:**
```json
{
  "events": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "title": "Client Meeting",
      "description": "Discuss project requirements",
      "start_time": "2024-01-02T15:00:00Z",
      "end_time": "2024-01-02T16:00:00Z",
      "all_day": false,
      "attendees": ["client@example.com"],
      "contact_id": "550e8400-e29b-41d4-a716-446655440001",
      "contact_name": "Jane Smith",
      "location_id": null,
      "location_name": null,
      "external_calendar_id": "google_cal_123",
      "external_calendar_type": "google",
      "created_at": "2024-01-01T10:00:00Z",
      "updated_at": "2024-01-01T10:00:00Z"
    }
  ]
}
```

### POST /calendar
Create a new calendar event.

**Request Body:**
```json
{
  "title": "Client Meeting",
  "description": "Discuss project requirements",
  "start_time": "2024-01-02T15:00:00Z",
  "end_time": "2024-01-02T16:00:00Z",
  "all_day": false,
  "attendees": ["client@example.com"],
  "contact_id": "550e8400-e29b-41d4-a716-446655440001"
}
```

### POST /calendar/auth/{provider}
Initiate OAuth flow for calendar provider (google/outlook).

**Response:**
```json
{
  "auth_url": "https://accounts.google.com/oauth/authorize?...",
  "provider": "google"
}
```

### POST /calendar/auth/callback
Handle OAuth callback for calendar authentication.

**Request Body:**
```json
{
  "provider": "google",
  "auth_code": "authorization_code_from_oauth",
  "redirect_uri": "https://yourapp.com/callback"
}
```

### POST /calendar/sync/{provider}
Sync with external calendar (google/outlook).

## Workflows API

### GET /workflows
Get all workflows for the authenticated user.

**Response:**
```json
{
  "workflows": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "name": "Appointment Reminder",
      "description": "Send SMS reminder 24 hours before appointment",
      "trigger_type": "time_based",
      "trigger_config": {
        "schedule": "24h_before_event"
      },
      "conditions": {
        "all": [
          {
            "type": "equals",
            "field": "event.type",
            "value": "appointment"
          }
        ]
      },
      "actions": [
        {
          "type": "send_sms",
          "to_number": "{{contact.phone}}",
          "message": "Reminder: You have an appointment tomorrow at {{event.start_time}}"
        }
      ],
      "enabled": true,
      "created_at": "2024-01-01T10:00:00Z",
      "updated_at": "2024-01-01T10:00:00Z"
    }
  ]
}
```

### POST /workflows
Create a new workflow.

**Request Body:**
```json
{
  "name": "Appointment Reminder",
  "description": "Send SMS reminder 24 hours before appointment",
  "trigger_type": "time_based",
  "trigger_config": {
    "schedule": "24h_before_event"
  },
  "conditions": {
    "all": [
      {
        "type": "equals",
        "field": "event.type",
        "value": "appointment"
      }
    ]
  },
  "actions": [
    {
      "type": "send_sms",
      "to_number": "{{contact.phone}}",
      "message": "Reminder: You have an appointment tomorrow at {{event.start_time}}"
    }
  ],
  "enabled": true
}
```

### POST /workflows/{workflow_id}/execute
Manually execute a workflow.

**Request Body:**
```json
{
  "context": {
    "contact": {
      "name": "Jane Smith",
      "phone": "+1234567891"
    },
    "event": {
      "title": "Appointment",
      "start_time": "2024-01-02T15:00:00Z"
    }
  }
}
```

## SMS API

### POST /sms/send
Send SMS message.

**Request Body:**
```json
{
  "to_number": "+1234567891",
  "message": "Your appointment is confirmed for tomorrow at 3pm"
}
```

**Response:**
```json
{
  "success": true,
  "sid": "SMS_MESSAGE_SID_123",
  "to": "+1234567891",
  "status": "sent"
}
```

### POST /sms/webhook
Webhook endpoint for receiving SMS messages (used by Twilio).

## Error Responses

All endpoints may return the following error responses:

### 400 Bad Request
```json
{
  "detail": "Invalid input data",
  "errors": [
    {
      "field": "email",
      "message": "Invalid email format"
    }
  ]
}
```

### 401 Unauthorized
```json
{
  "detail": "Invalid authentication credentials"
}
```

### 403 Forbidden
```json
{
  "detail": "Insufficient permissions"
}
```

### 404 Not Found
```json
{
  "detail": "Resource not found"
}
```

### 422 Validation Error
```json
{
  "detail": [
    {
      "loc": ["body", "email"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```

### 500 Internal Server Error
```json
{
  "detail": "Internal server error"
}
```

## Rate Limiting
API endpoints are rate limited to:
- Authentication endpoints: 10 requests per minute per IP
- All other endpoints: 100 requests per minute per user

Rate limit headers are included in responses:
```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 99
X-RateLimit-Reset: 1640995200
```

## Webhooks

### SMS Webhook Configuration
Configure your Twilio webhook URL to point to:
```
POST /sms/webhook
```

Expected webhook payload from Twilio:
```json
{
  "From": "+1234567891",
  "Body": "Message content",
  "MessageSid": "SMS123"
}
```

## SDK Examples

### Python
```python
import requests

# Authentication
response = requests.post('http://localhost:8000/auth/login', json={
    'email': 'user@example.com',
    'password': 'password'
})
token = response.json()['access_token']

headers = {'Authorization': f'Bearer {token}'}

# Create contact
contact_data = {
    'name': 'Jane Smith',
    'email': 'jane@example.com',
    'phone': '+1234567891'
}
response = requests.post('http://localhost:8000/contacts', 
                        json=contact_data, headers=headers)
```

### JavaScript
```javascript
// Authentication
const authResponse = await fetch('http://localhost:8000/auth/login', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({
    email: 'user@example.com',
    password: 'password'
  })
});
const {access_token} = await authResponse.json();

// Create contact
const contactResponse = await fetch('http://localhost:8000/contacts', {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${access_token}`,
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    name: 'Jane Smith',
    email: 'jane@example.com',
    phone: '+1234567891'
  })
});
```

## Environment Variables

Required environment variables for deployment:

```bash
# Database
DATABASE_URL=postgresql+asyncpg://user:pass@localhost/crm_db
REDIS_URL=redis://localhost:6379

# Security
SECRET_KEY=your-secret-key-here
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7

# External APIs
OPENAI_API_KEY=your-openai-key
TWILIO_ACCOUNT_SID=your-twilio-sid
TWILIO_AUTH_TOKEN=your-twilio-token
TWILIO_PHONE_NUMBER=+1234567890

# Email (optional)
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-app-password

# Calendar Integration (optional)
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret
MICROSOFT_CLIENT_ID=your-microsoft-client-id
MICROSOFT_CLIENT_SECRET=your-microsoft-client-secret
```