from typing import Optional, List
from datetime import datetime
from app.db.models import Task, TaskStatus, AgentRole, Message
from app.db.crud import get_task, update_task, get_messages, add_message
from app.agents import CoordinatorAgent, ResearcherAgent, CriticAgent, ExecutorAgent
from app.llm.client import LLMClient
from app.config import settings
import logging

logger = logging.getLogger(__name__)

class OrchestrationEngine:
    def __init__(self):
        self.llm = LLMClient()
        self.agents = {
            AgentRole.COORDINATOR: CoordinatorAgent(self.llm),
            AgentRole.RESEARCHER: ResearcherAgent(self.llm),
            AgentRole.CRITIC: CriticAgent(self.llm),
            AgentRole.EXECUTOR: ExecutorAgent(self.llm),
        }
    
    async def run_step(self, task_id: int) -> dict:
        task = await get_task(task_id)
        if not task:
            return {"status": "error", "reason": "Task not found"}
        
        if task.status not in [TaskStatus.PENDING, TaskStatus.IN_PROGRESS]:
            return {"status": "skipped", "reason": f"Task status: {task.status}"}
        
        if task.current_step >= task.max_steps:
            return await self._finalize_task(task, "Достигнут лимит шагов. Формирую итоговый ответ на основе обсуждения.")
        
        # Определяем агента
        messages = await get_messages(task.id)
        next_role = self._select_next_agent(task, messages)
        agent = self.agents[next_role]
        
        logger.info(f"Task {task_id}, step {task.current_step + 1}, agent: {agent.name}")
        
        # Суммаризация если много сообщений
        context_summary = task.context_summary
        msgs_for_context = messages
        
        if len(messages) > settings.MAX_CONTEXT_MESSAGES:
            old_msgs = messages[:-settings.MAX_CONTEXT_MESSAGES]
            msgs_for_context = messages[-settings.MAX_CONTEXT_MESSAGES:]
            
            if not context_summary:
                old_text = "\n".join([f"{m.role}: {m.content[:100]}" for m in old_msgs])
                context_summary = await self.llm.summarize(old_text)
                await update_task(task.id, context_summary=context_summary)
        
        # Агент думает
        response = await agent.think(
            task_description=task.description,
            messages=msgs_for_context,
            context_summary=context_summary
        )
        
        # Сохраняем сообщение
        content = f"{agent.emoji} <b>{agent.name}:</b>\n{response.content}"
        await add_message(task.id, agent.role.value, content)
        
        # Обновляем задачу
        await update_task(task.id, current_step=task.current_step + 1, status=TaskStatus.IN_PROGRESS)
        
        # Проверяем завершение
        if response.is_final_answer:
            return await self._finalize_task(task, response.content)
        
        if not response.should_continue:
            return await self._finalize_task(task, "Обсуждение завершено.")
        
        return {
            "status": "continue",
            "content": content,
            "next_agent": response.next_agent,
            "step": task.current_step + 1
        }
    
    def _select_next_agent(self, task: Task, messages: List[Message]) -> AgentRole:
        if not messages:
            return AgentRole.COORDINATOR
        
        last = messages[-1]
        content = last.content.lower()
        
        if "@researcher" in content:
            return AgentRole.RESEARCHER
        if "@critic" in content:
            return AgentRole.CRITIC
        if "@executor" in content:
            return AgentRole.EXECUTOR
        if "@coordinator" in content:
            return AgentRole.COORDINATOR
        
        # Round-robin
        roles = [AgentRole.COORDINATOR, AgentRole.RESEARCHER, AgentRole.CRITIC, AgentRole.EXECUTOR]
        last_role = None
        for r in AgentRole:
            if r.value == last.role:
                last_role = r
                break
        
        if last_role:
            idx = roles.index(last_role)
            return roles[(idx + 1) % len(roles)]
        
        return AgentRole.COORDINATOR
    
    async def _finalize_task(self, task: Task, final_content: str) -> dict:
        await update_task(
            task.id,
            status=TaskStatus.COMPLETED,
            final_answer=final_content
        )
        
        return {
            "status": "completed",
            "final_answer": final_content
        }
