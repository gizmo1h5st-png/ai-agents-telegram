import logging
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from aiogram import Bot, Dispatcher

from app.config import settings
from app.bot.handlers import router
from app.db.session import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
dp.include_router(router)

polling_task = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global polling_task
    
    # Startup
    logger.info("Starting up...")
    await init_db()
    
    # Удаляем старый вебхук и запускаем polling
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Bot ready! Starting polling...")
    
    # Запускаем polling в фоне
    polling_task = asyncio.create_task(dp.start_polling(bot))
    
    yield
    
    # Shutdown
    if polling_task:
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass
    await bot.session.close()
    logger.info("Shutdown complete")

app = FastAPI(lifespan=lifespan)

@app.get("/health")
async def health():
    return {"status": "ok", "bot": "running"}

@app.get("/")
async def root():
    return {"message": "AI Agents Team Bot", "status": "running"}
