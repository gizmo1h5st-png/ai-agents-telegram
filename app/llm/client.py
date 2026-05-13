import httpx
import hashlib
import json
import logging
from typing import List, Dict, Optional, Any
from app.config import settings

logger = logging.getLogger(__name__)

class LLMClient:
    def __init__(self):
        self.base_url = settings.OPENROUTER_BASE_URL
        self.api_key = settings.OPENROUTER_API_KEY
        self._cache: Dict[str, Dict] = {}
        
    async def chat(
        self,
        system_prompt: str,
        messages: List[Dict],
        task: str,
        model: Optional[str] = None,
        max_tokens: int = None
    ) -> Dict[str, Any]:
        model = model or settings.DEFAULT_MODEL
        max_tokens = max_tokens or settings.MAX_TOKENS_PER_REQUEST
        
        # Простой кэш
        cache_key = self._make_cache_key(system_prompt, messages, task)
        if cache_key in self._cache:
            logger.info("Cache hit")
            return self._cache[cache_key]
        
        full_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"ЗАДАЧА: {task}"},
            *messages
        ]
        
        payload = {
            "model": model,
            "messages": full_messages,
            "max_tokens": max_tokens,
            "temperature": 0.7,
        }
        
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://github.com/ai-agents-telegram",
                        "X-Title": "AI Agents Telegram Bot"
                    },
                    json=payload
                )
                
                if response.status_code == 429:
                    logger.warning("Rate limited by OpenRouter")
                    return {"content": "⏸ Превышен лимит API. Подожди минуту и попробуй снова."}
                
                response.raise_for_status()
                data = response.json()
            
            choice = data["choices"][0]
            result = {"content": choice["message"].get("content", "")}
            
            # Кэшируем
            self._cache[cache_key] = result
            
            logger.info(f"LLM response received, tokens: {data.get('usage', {})}")
            return result
            
        except Exception as e:
            logger.error(f"LLM error: {e}")
            return {"content": f"❌ Ошибка API: {str(e)[:100]}"}
    
    async def summarize(self, text: str) -> str:
        response = await self.chat(
            system_prompt="Сократи текст до 2-3 ключевых предложений.",
            messages=[],
            task=f"Сократи:\n{text}",
            model=settings.SUMMARIZER_MODEL,
            max_tokens=200
        )
        return response["content"]
    
    def _make_cache_key(self, system: str, messages: List, task: str) -> str:
        data = json.dumps({"s": system[:100], "m": str(messages)[-200:], "t": task}, sort_keys=True)
        return hashlib.sha256(data.encode()).hexdigest()[:16]
