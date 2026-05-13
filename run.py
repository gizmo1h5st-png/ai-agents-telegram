#!/usr/bin/env python3
"""Точка входа для Railway - определяет режим по переменной APP_MODE"""
import os
import sys

def run_bot():
    """Запуск веб-сервера с ботом"""
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    print(f"Starting BOT on port {port}...")
    uvicorn.run("app.main:app", host="0.0.0.0", port=port)

def run_worker():
    """Запуск Celery worker"""
    from app.workers.tasks import celery_app
    print("Starting CELERY WORKER...")
    celery_app.worker_main(["worker", "-l", "info"])

if __name__ == "__main__":
    mode = os.environ.get("APP_MODE", "bot").lower()
    
    if mode == "worker":
        run_worker()
    else:
        run_bot()
