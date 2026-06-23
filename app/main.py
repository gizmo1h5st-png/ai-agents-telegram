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
agent_bots = []
redis_client = None

POLLING_LOCK_KEY = "ai_agents_telegram:multi_bot_polling_lock"
polling_instance_id = None
polling_lock_acquired = False
polling_started = False


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes", "y", "on")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global polling_tasks, agent_bots, redis_client
    global polling_instance_id, polling_lock_acquired, polling_started

    polling_tasks = []
    agent_bots = []
    redis_client = None
    polling_lock_acquired = False
    polling_started = False
    polling_instance_id = f"{os.environ.get('RAILWAY_DEPLOYMENT_ID', '')}:{uuid.uuid4()}"
    lock_refresher = None

    if settings.multi_bot_mode:
        import redis.asyncio as aioredis
        from app.multibot.engine import AgentBot
        from app.db.session import init_db

        await init_db()
        redis_client = aioredis.from_url(settings.REDIS_URL)

        lock_ttl = int(os.environ.get("POLLING_LOCK_TTL", "45"))
        lock_wait = int(os.environ.get("POLLING_LOCK_WAIT", "60"))
        clear_on_start = _env_bool("CLEAR_POLLING_LOCK_ON_START", False)
        force_clear_wait = int(os.environ.get("POLLING_FORCE_CLEAR_WAIT", "20"))

        if clear_on_start:
            old_owner = await redis_client.get(POLLING_LOCK_KEY)
            await redis_client.delete(POLLING_LOCK_KEY)
            logger.warning(
                f"FORCE CLEARED old polling lock: {old_owner.decode() if old_owner else 'None'}; "
                f"waiting {force_clear_wait}s before acquiring to let old poller stop"
            )
            # Important: old container may still be polling for a few seconds.
            # Waiting avoids TelegramConflictError after force-clearing the lock.
            await asyncio.sleep(force_clear_wait)

        for attempt in range(lock_wait):
            polling_lock_acquired = bool(
                await redis_client.set(POLLING_LOCK_KEY, polling_instance_id, nx=True, ex=lock_ttl)
            )
            if polling_lock_acquired:
                logger.info(f"✅ Polling lock acquired: {polling_instance_id}")
                break

            owner = await redis_client.get(POLLING_LOCK_KEY)
            if attempt == 0 or (attempt + 1) % 5 == 0:
                logger.warning(
                    f"Another bot polling instance is active. Waiting... "
                    f"attempt={attempt + 1}/{lock_wait} owner={owner.decode() if owner else 'unknown'}"
                )
            await asyncio.sleep(1)

        async def refresh_lock():
            while True:
                await asyncio.sleep(max(10, lock_ttl // 3))
                try:
                    owner = await redis_client.get(POLLING_LOCK_KEY)
                    if owner and owner.decode() == polling_instance_id:
                        await redis_client.expire(POLLING_LOCK_KEY, lock_ttl)
                    else:
                        # Do not kill the whole FastAPI process here.
                        # Just stop refreshing. Health will show degraded if polling is not active.
                        logger.error("Lost polling lock. Lock refresher stopped.")
                        return
                except asyncio.CancelledError:
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

        if polling_lock_acquired:
            lock_refresher = asyncio.create_task(refresh_lock())
            for role, token in bots_config.items():
                if token:
                    bot = AgentBot(role, token, redis_client)
                    agent_bots.append(bot)
                    task = asyncio.create_task(bot.start())
                    polling_tasks.append(task)

            polling_started = bool(polling_tasks)
            logger.info(f"Multi-bot mode: {len(agent_bots)} bots started (lock={polling_instance_id})")
        else:
            # Let FastAPI start, but report degraded health.
            # This avoids 502 during lock wait and makes diagnosis easier.
            logger.error("Could not acquire polling lock. FastAPI will start, Telegram polling will NOT run in this container.")

        yield

        logger.info("Application shutdown: stopping bot polling tasks")
        for task in polling_tasks:
            task.cancel()
        for bot in agent_bots:
            try:
                await bot.stop()
            except Exception as e:
                logger.warning(f"Bot stop failed: {e}")
        if lock_refresher:
            lock_refresher.cancel()
        try:
            owner = await redis_client.get(POLLING_LOCK_KEY)
            if owner and owner.decode() == polling_instance_id:
                await redis_client.delete(POLLING_LOCK_KEY)
                logger.info("Polling lock released")
        except Exception as e:
            logger.error(f"Polling lock release error: {e}")
        await redis_client.close()

    else:
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
        polling_started = True

        yield

        for t in polling_tasks:
            t.cancel()
        await bot.session.close()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    tasks_total = len(polling_tasks)
    tasks_alive = sum(1 for t in polling_tasks if not t.done())

    ok = True
    reason = "ok"

    if settings.multi_bot_mode:
        if not polling_lock_acquired:
            ok = False
            reason = "polling_lock_not_acquired"
        elif tasks_total == 0:
            ok = False
            reason = "no_polling_tasks"
        elif tasks_alive != tasks_total:
            ok = False
            reason = "some_polling_tasks_dead"

    return {
        "status": "ok" if ok else "degraded",
        "reason": reason,
        "mode": "multi" if settings.multi_bot_mode else "single",
        "polling_lock_acquired": polling_lock_acquired,
        "polling_instance_id": polling_instance_id,
        "polling_started": polling_started,
        "polling_tasks_total": tasks_total,
        "polling_tasks_alive": tasks_alive,
        "bots_total": len(agent_bots),
    }


@app.get("/")
async def root():
    return {"message": "AI Agents Team", "mode": "multi" if settings.multi_bot_mode else "single"}
