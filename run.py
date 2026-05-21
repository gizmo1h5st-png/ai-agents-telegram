#!/usr/bin/env python3
import os
import uvicorn

def run_bot():
    port = int(os.environ.get("PORT", 8000))
    print(f"Starting BOT on port {port}...")
    uvicorn.run("app.main:app", host="0.0.0.0", port=port)

def run_worker():
    # Worker НЕ запускает ботов
    os.environ["BOT_COORDINATOR_TOKEN"] = ""
    os.environ["BOT_RESEARCHER_TOKEN"] = ""
    os.environ["BOT_CRITIC_TOKEN"] = ""
    os.environ["BOT_EXECUTOR_TOKEN"] = ""
    
    from app.workers.tasks import celery_app
    print("Starting CELERY WORKER...")
    celery_app.worker_main([
        "worker",
        "-l", "info",
        "--concurrency", "2",
        "--pool", "solo",
    ])

if __name__ == "__main__":
    mode = os.environ.get("APP_MODE", "bot").lower()
    if mode == "worker":
        run_worker()
    else:
        run_bot()
