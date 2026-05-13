from celery import Celery
from app.config import settings
import asyncio
import logging

logger = logging.getLogger(__name__)

celery_app = Celery(
    "ai_agents",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    task_soft_time_limit=120,
    task_time_limit=180,
    worker_prefetch_multiplier=1,
)

def run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

@celery_app.task(bind=True, max_retries=2, default_retry_delay=30)
def run_discussion_step(self, task_id: int):
    """Выполняет шаг обсуждения"""
    
    async def _run():
        from app.orchestrator.engine import OrchestrationEngine
        from app.db.crud import get_task
        from aiogram import Bot
        
        engine = OrchestrationEngine()
        
        try:
            result = await engine.run_step(task_id)
            
            task = await get_task(task_id)
            if not task:
                return {"status": "error"}
            
            # Отправляем в Telegram
            bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
            
            try:
                if result["status"] == "continue":
                    await bot.send_message(task.chat_id, result["content"], parse_mode="HTML")
                    # Следующий шаг через 2 секунды
                    run_discussion_step.apply_async(args=[task_id], countdown=2)
                    
                elif result["status"] == "completed":
                    final = f"✅ <b>ЗАДАЧА ВЫПОЛНЕНА</b>\n\n{result['final_answer']}"
                    await bot.send_message(task.chat_id, final, parse_mode="HTML")
                    
            finally:
                await bot.session.close()
            
            return result
            
        except Exception as e:
            logger.error(f"Error in step: {e}")
            raise self.retry(exc=e)
    
    return run_async(_run())
