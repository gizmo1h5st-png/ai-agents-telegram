import logging
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

polling_tasks = []

@asynccontextmanager
async def lifespan(app: FastAPI):
    global polling_tasks
    
    if settings.multi_bot_mode:
        # Multi-bot mode
        import redis.asyncio as aioredis
        from app.multibot.engine import AgentBot
        from app.db.session import init_db
        
        await init_db()
        redis_client = aioredis.from_url(settings.REDIS_URL)
        
        bots_config = {
            "coordinator": settings.BOT_COORDINATOR_TOKEN,
            "researcher": settings.BOT_RESEARCHER_TOKEN,
            "critic": settings.BOT_CRITIC_TOKEN,
            "executor": settings.BOT_EXECUTOR_TOKEN,
        }
        
        agent_bots = []
        for role, token in bots_config.items():
            if token:
                bot = AgentBot(role, token, redis_client)
                agent_bots.append(bot)
                task = asyncio.create_task(bot.start())
                polling_tasks.append(task)
        
        logger.info(f"Multi-bot mode: {len(agent_bots)} bots started")
        
        yield
        
        for task in polling_tasks:
            task.cancel()
        for bot in agent_bots:
            await bot.stop()
        await redis_client.close()
    
    else:
        # Single-bot mode (backward compatible)
        from aiogram import Bot, Dispatcher
        from app.bot.handlers import router
        from app.db.session import init_db
        
        await init_db()
        bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
        dp = Dispatcher()
        dp.include_router(router)
        
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Single-bot mode: Bot ready!")
        
        task = asyncio.create_task(dp.start_polling(bot))
        polling_tasks.append(task)
        
        yield
        
        for t in polling_tasks:
            t.cancel()
        await bot.session.close()

app = FastAPI(lifespan=lifespan)

@app.get("/health")
async def health():
    return {"status": "ok", "mode": "multi" if settings.multi_bot_mode else "single"}

@app.get("/")
async def root():
    return {"message": "AI Agents Team", "mode": "multi" if settings.multi_bot_mode else "single"}
