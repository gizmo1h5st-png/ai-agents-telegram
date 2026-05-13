from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from app.db.models import AgentRole, Message
import re

@dataclass
class AgentResponse:
    content: str
    should_continue: bool = True
    next_agent: Optional[AgentRole] = None
    is_final_answer: bool = False

class BaseAgent(ABC):
    role: AgentRole
    name: str
    emoji: str
    
    def __init__(self, llm_client):
        self.llm = llm_client
    
    @property
    @abstractmethod
    def system_prompt(self) -> str:
        pass
    
    async def think(
        self,
        task_description: str,
        messages: List[Message],
        context_summary: Optional[str] = None
    ) -> AgentResponse:
        formatted = self._format_messages(messages, context_summary)
        
        response = await self.llm.chat(
            system_prompt=self.system_prompt,
            messages=formatted,
            task=task_description
        )
        
        return self._parse_response(response)
    
    def _format_messages(self, messages: List[Message], context_summary: Optional[str]) -> List[Dict]:
        formatted = []
        
        if context_summary:
            formatted.append({
                "role": "system",
                "content": f"[КРАТКОЕ СОДЕРЖАНИЕ]\n{context_summary}"
            })
        
        for msg in messages:
            role = "assistant" if msg.role != "user" else "user"
            formatted.append({"role": role, "content": msg.content})
        
        return formatted
    
    def _parse_response(self, response: Dict) -> AgentResponse:
        content = response.get("content", "")
        
        is_final = "[ФИНАЛЬНЫЙ ОТВЕТ]" in content or "[FINAL]" in content
        should_continue = not is_final and "[СТОП]" not in content
        
        next_agent = None
        content_lower = content.lower()
        if "@researcher" in content_lower:
            next_agent = AgentRole.RESEARCHER
        elif "@critic" in content_lower:
            next_agent = AgentRole.CRITIC
        elif "@executor" in content_lower:
            next_agent = AgentRole.EXECUTOR
        elif "@coordinator" in content_lower:
            next_agent = AgentRole.COORDINATOR
        
        return AgentResponse(
            content=content,
            should_continue=should_continue,
            next_agent=next_agent,
            is_final_answer=is_final
        )
