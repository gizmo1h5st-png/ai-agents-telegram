from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from contextlib import asynccontextmanager
from app.config import settings
from app.db.models import Base
import logging

logger = logging.getLogger(__name__)

# Используем async_database_url который конвертирует postgres:// -> postgresql+asyncpg://
engine = create_async_engine(
    settings.async_database_url,
    echo=False,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,  # Проверка соединения перед использованием
)

async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)

async def init_db():
    logger.info(f"Connecting to database...")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            
            # Добавляем колонку model если её нет
            await conn.execute(text("""
                ALTER TABLE tasks ADD COLUMN IF NOT EXISTS model VARCHAR(200)
            """))
            
        logger.info("Database initialized successfully!")
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        raise

@asynccontextmanager
async def get_session():
    session = async_session_maker()
    try:
        yield session
    finally:
        await session.close()
