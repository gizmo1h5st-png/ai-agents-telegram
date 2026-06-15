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
        import redis.asyncio as aioredis
        from app.multibot.engine import AgentBot
        from app.db.session import init_db
        
        await init_db()
        redis_client = aioredis.from_url(settings.REDIS_URL)

        # === RAILWAY POLLING LOCK (FIXED VERSION) ===
        # The biggest problem was blocking the lifespan for too long.
        # Railway kills containers that don't answer /health quickly.
        railway_id = os.environ.get('RAILWAY_DEPLOYMENT_ID', str(uuid.uuid4())[:8])
        instance_id = f"{railway_id}:{uuid.uuid4()}"
        lock_key = "ai_agents_telegram:multi_bot_polling_lock"

        lock_ttl = int(os.environ.get("POLLING_LOCK_TTL", "1800"))   # 30 minutes
        lock_acquired = False
        lock_refresher = None
        active_bots = []

        # Emergency clear (only when you manually set the env var)
        if os.environ.get("CLEAR_POLLING_LOCK_ON_START", "").lower() in ("1", "true", "yes"):
            try:
                old = await redis_client.get(lock_key)
                await redis_client.delete(lock_key)
                logger.warning(f"FORCE CLEARED old lock: {old}")
            except Exception:
                pass

        # Fast non-blocking attempt + short wait + steal
        try:
            lock_acquired = bool(await redis_client.set(lock_key, instance_id, nx=True, ex=lock_ttl))
            
            if not lock_acquired:
                # Wait max ~12 seconds (Railway overlap is rarely longer)
                for i in range(12):
                    await asyncio.sleep(1)
                    lock_acquired = bool(await redis_client.set(lock_key, instance_id, nx=True, ex=lock_ttl))
                    if lock_acquired:
                        break
                    if i in (0, 3, 6, 9):
                        owner = await redis_client.get(lock_key)
                        logger.info(f"Lock busy (attempt {i+1}/12), owner={owner.decode() if owner else 'unknown'}")

            if not lock_acquired:
                # Steal — this is the correct behavior on Railway during deploys
                logger.warning("Lock still held by previous container. STEALING it now.")
                await redis_client.set(lock_key, instance_id, ex=lock_ttl)
                lock_acquired = True

        except Exception as e:
            logger.error(f"Redis lock error: {e}. Starting anyway (risk of duplicate polling).")
            lock_acquired = True

        if lock_acquired:
            logger.info(f"✅ Polling lock acquired: {instance_id}")

        # === CRITICAL: Yield immediately so Railway sees the service as healthy ===
        yield

        # === Start bots ONLY after the HTTP server is running ===
        if lock_acquired:
            bots_config = {
                "coordinator": settings.BOT_COORDINATOR_TOKEN,
                "researcher": settings.BOT_RESEARCHER_TOKEN,
                "architect": settings.BOT_ARCHITECT_TOKEN,
                "executor": settings.BOT_EXECUTOR_TOKEN,
                "qa": settings.BOT_QA_TOKEN,
                "critic": settings.BOT_CRITIC_TOKEN,
            }

            async def refresh_lock():
                while True:
                    await asyncio.sleep(45)
                    try:
                        owner = await redis_client.get(lock_key)
                        if owner and owner.decode() == instance_id:
                            await redis_client.expire(lock_key, lock_ttl)
                        else:
                            logger.error("Lost polling lock — cancelling bots in this container")
                            for t in polling_tasks:
                                t.cancel()
                            return
                    except Exception as e:
                        logger.error(f"Lock refresh error: {e}")

            lock_refresher = asyncio.create_task(refresh_lock())

            for role, token in bots_config.items():
                if token:
                    try:
                        bot = AgentBot(role, token, redis_client)
                        active_bots.append(bot)
                        task = asyncio.create_task(bot.start())
                        polling_tasks.append(task)
                    except Exception as e:
                        logger.error(f"Failed to start {role} bot: {e}")

            logger.info(f"Multi-bot mode: {len(active_bots)} bots started (lock={instance_id})")
        else:
            logger.warning("No polling lock — bots will not run in this container.")

        # Keep process alive until shutdown
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            # Cleanup on shutdown
            for task in polling_tasks:
                task.cancel()
            for bot in active_bots:
                try:
                    await bot.stop()
                except:
                    pass
            if lock_refresher:
                lock_refresher.cancel()
            try:
                owner = await redis_client.get(lock_key)
                if owner and owner.decode() == instance_id:
                    await redis_client.delete(lock_key)
                    logger.info("Released polling lock")
            except:
                pass
            await redis_client.close()

    else:
        # Legacy single bot
        from aiogram import Bot, Dispatcher
        from app.bot.handlers import router
        from app.db.session import init_db
        
        await init_db()
        bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
        dp = Dispatcher()
        dp.include_router(router)
        
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Single-bot mode ready")
        
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
