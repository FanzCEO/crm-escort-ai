from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import os
import sys
import logging
from typing import Dict
import structlog
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Import database
from app.database import init_db, close_db

# Import routers
from app.routers import auth, messages, contacts, calendar, workflows
from app.sms_handler import router as sms_router

# Configure structured logging
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer() if os.getenv("ENV") == "development" else structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=False,
)
logger = structlog.get_logger()

# Environment configuration
ENV = os.getenv("ENV", "development")
DEBUG = os.getenv("DEBUG", "true").lower() == "true"
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:3100").split(",")

# Rate limiting
limiter = Limiter(key_func=get_remote_address, default_limits=["100/minute"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    # Startup
    await logger.ainfo("startup", env=ENV, debug=DEBUG)
    
    try:
        # Initialize database
        await init_db()
        await logger.ainfo("database_initialized")
    except Exception as e:
        await logger.aerror("startup_failed", error=str(e))
        sys.exit(1)
    
    yield
    
    # Shutdown
    await logger.ainfo("shutdown_starting")
    await close_db()
    await logger.ainfo("shutdown_complete")


# Initialize FastAPI app
app = FastAPI(
    title="CRM Escort AI",
    description="AI Chief of Staff for Messages - Automate contacts, meetings, and follow-ups",
    version="0.1.0",
    docs_url="/docs" if DEBUG else None,
    redoc_url="/redoc" if DEBUG else None,
    lifespan=lifespan,
)

# Add rate limiting state
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all HTTP requests with structured logging"""
    await logger.ainfo(
        "request_started",
        method=request.method,
        path=request.url.path,
        client=request.client.host if request.client else None,
    )
    response = await call_next(request)
    await logger.ainfo(
        "request_completed",
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
    )
    return response


# Health check endpoint
@app.get("/health")
async def health_check() -> Dict[str, str]:
    """Health check endpoint for monitoring"""
    return {
        "status": "healthy",
        "service": "crm-escort-ai",
        "version": "0.1.0",
        "environment": ENV,
    }


# Root endpoint
@app.get("/")
async def root() -> Dict[str, str]:
    """Root endpoint with API information"""
    return {
        "service": "CRM Escort AI",
        "version": "0.1.0",
        "docs": "/docs" if DEBUG else "disabled",
        "health": "/health",
    }


# Include routers
app.include_router(auth.router, prefix="/api/auth", tags=["Authentication"])
app.include_router(messages.router, prefix="/api/messages", tags=["Messages"])
app.include_router(contacts.router, prefix="/api/contacts", tags=["Contacts"])
app.include_router(calendar.router, prefix="/api/calendar", tags=["Calendar"])
app.include_router(workflows.router, prefix="/api/workflows", tags=["Workflows"])
app.include_router(sms_router, prefix="/api/sms", tags=["SMS"])


# Exception handlers
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Handle HTTP exceptions"""
    await logger.awarning(
        "http_exception",
        status_code=exc.status_code,
        detail=exc.detail,
        path=request.url.path,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail},
    )

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all exception handler"""
    await logger.aerror(
        "unhandled_exception",
        error=str(exc),
        error_type=type(exc).__name__,
        path=request.url.path,
    )
    
    if DEBUG:
        raise exc
    
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "Internal server error",
            "detail": str(exc) if DEBUG else "An unexpected error occurred",
        },
    )
