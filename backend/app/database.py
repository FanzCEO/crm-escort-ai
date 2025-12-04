"""
Database configuration and session management
"""
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import NullPool
import os

# Database URL from environment
DATABASE_URL = os.getenv("DB_URL", "postgresql+asyncpg://crm_user:password@localhost:5432/crm_escort")

# Convert postgresql:// to postgresql+asyncpg:// for async
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
elif "psycopg" in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)

# Create async engine
engine = create_async_engine(
    DATABASE_URL,
    echo=os.getenv("DEBUG", "false").lower() == "true",
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_recycle=3600,
    # Use NullPool for serverless/short-lived connections
    poolclass=NullPool if os.getenv("ENV") == "serverless" else None,
)

# Create async session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# Base class for models
Base = declarative_base()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency for getting async database sessions.
    
    Usage in FastAPI routes:
        @router.get("/items")
        async def get_items(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """
    Initialize database tables.
    This creates all tables defined in models.
    """
    async with engine.begin() as conn:
        # Import all models here to ensure they're registered
        import app.models  # noqa: F401
        
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """
    Close database connections.
    Call this on application shutdown.
    """
    await engine.dispose()
