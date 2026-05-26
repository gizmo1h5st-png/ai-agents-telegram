import logging
import asyncio
import os
import uuid
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

        # Telegram getUpdates допускает только ОДИН poller на один bot token.
        # На Railway во время redeploy/scale может на несколько секунд существовать 2 контейнера.
        # Этот Redis-lock не даёт второму контейнеру запускать polling и ловить TelegramConflictError.
        instance_id = f"{os.environ.get('RAILWAY_DEPLOYMENT_ID', '')}:{uuid.uuid4()}"
        lock_key = "ai_agents_telegram:multi_bot_polling_lock"
        lock_ttl = int(os.environ.get("POLLING_LOCK_TTL", "45"))
        lock_wait = int(os.environ.get("POLLING_LOCK_WAIT", "180"))
        lock_acquired = False
        lock_refresher = None

        # Emergency only: set CLEAR_POLLING_LOCK_ON_START=true once if Railway left a stale lock.
        if os.environ.get("CLEAR_POLLING_LOCK_ON_START", "").lower() in ("1", "true", "yes"):
            old_owner = await redis_client.get(lock_key)
            await redis_client.delete(lock_key)
            logger.warning(f"Polling lock force-cleared on start. old_owner={old_owner.decode() if old_owner else 'none'}")

        for attempt in range(lock_wait):
            lock_acquired = bool(await redis_client.set(lock_key, instance_id, nx=True, ex=lock_ttl))
            if lock_acquired:
                break
            owner = await redis_client.get(lock_key)
            if attempt == 0 or (attempt + 1) % 5 == 0:
                logger.warning(
                    f"Another bot polling instance is active. Waiting... attempt={attempt + 1}/{lock_wait} owner={owner.decode() if owner else 'unknown'}"
                )
            await asyncio.sleep(1)

        async def refresh_lock():
            while True:
                await asyncio.sleep(max(10, lock_ttl // 3))
                try:
                    owner = await redis_client.get(lock_key)
                    if owner and owner.decode() == instance_id:
                        await redis_client.expire(lock_key, lock_ttl)
                    else:
                        logger.error("Lost polling lock. Stopping lock refresher.")
                        return
                except Exception as e:
                    logger.error(f"Polling lock refresh error: {e}")

        bots_config = {
            "coordinator": settings.BOT_COORDINATOR_TOKEN,
            "researcher": settings.BOT_RESEARCHER_TOKEN,
            "architect": settings.BOT_ARCHITECT_TOKEN,
            "executor": settings.BOT_EXECUTOR_TOKEN,
            "qa": settings.BOT_QA_TOKEN,
            "critic": settings.BOT_CRITIC_TOKEN,
        }

        agent_bots = []
        if lock_acquired:
            lock_refresher = asyncio.create_task(refresh_lock())
            for role, token in bots_config.items():
                if token:
                    bot = AgentBot(role, token, redis_client)
                    agent_bots.append(bot)
                    task = asyncio.create_task(bot.start())
                    polling_tasks.append(task)
            logger.info(f"Multi-bot mode: {len(agent_bots)} bots started. polling_lock={instance_id}")
        else:
            logger.error("Could not acquire polling lock. This container will NOT start Telegram polling.")

        yield

        for task in polling_tasks:
            task.cancel()
        for bot in agent_bots:
            await bot.stop()
        if lock_refresher:
            lock_refresher.cancel()
        try:
            owner = await redis_client.get(lock_key)
            if owner and owner.decode() == instance_id:
                await redis_client.delete(lock_key)
                logger.info("Polling lock released")
        except Exception as e:
            logger.error(f"Polling lock release error: {e}")
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
