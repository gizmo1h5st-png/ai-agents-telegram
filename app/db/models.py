from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Enum, BigInteger, Float, Boolean, JSON
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.sql import func
import enum

Base = declarative_base()

class TaskStatus(enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"

class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, nullable=False, index=True)
    user_id = Column(BigInteger, nullable=False)
    description = Column(Text, nullable=False)
    status = Column(Enum(TaskStatus), default=TaskStatus.PENDING)
    current_step = Column(Integer, default=0)
    max_steps = Column(Integer, default=50)
    model = Column(String(200), nullable=True)
    context_summary = Column(Text, nullable=True)
    final_answer = Column(Text, nullable=True)
    tokens_used = Column(Integer, default=0)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    messages = relationship("Message", back_populates="task", cascade="all", delete_orphan)

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False)
    role = Column(String(50), nullable=False)
    content = Column(Text, nullable=False)
    msg_type = Column(String(30), default="broadcast")
    tokens = Column(Integer, default=0)
    created_at = Column(DateTime, default=func.now())
    task = relationship("Task", back_populates="messages")

class AgentMemory(Base):
    __tablename__ = "agent_memory"
    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, nullable=False, index=True)
    category = Column(String(50), nullable=False)
    key = Column(String(200), nullable=False)
    value = Column(Text, nullable=False)
    confidence = Column(Float, default=1.0)
    source_task_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

class TokenUsageLog(Base):
    __tablename__ = "token_usage_log"
    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, nullable=True)
    model = Column(String(200), nullable=False)
    tokens_input = Column(Integer, default=0)
    tokens_output = Column(Integer, default=0)
    cached = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())

class ChatSettings(Base):
    __tablename__ = "chat_settings"
    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, nullable=False, unique=True, index=True)
    model = Column(String(200), nullable=True)
    team = Column(String(500), nullable=True)
    agent_models = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
