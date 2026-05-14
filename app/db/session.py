from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text
from contextlib import asynccontextmanager
from app.config import settings
from app.db.models import Base
import logging

logger = logging.getLogger(__name__)

engine = create_async_engine(
    settings.async_database_url,
    echo=False,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
)

async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)

async def init_db():
    logger.info("Connecting to database...")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            
            try:
                await conn.execute(text("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS model VARCHAR(200)"))
            except:
                pass
            try:
                await conn.execute(text("ALTER TABLE chat_settings ADD COLUMN IF NOT EXISTS team VARCHAR(500)"))
            except:
                pass
                
        logger.info("Database initialized!")
    except Exception as e:
        logger.error(f"DB error: {e}")
        raise

@asynccontextmanager
async def get_session():
    session = async_session_maker()
    try:
        yield session
    finally:
        await session.close()
