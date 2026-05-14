from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Enum, BigInteger
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

class AgentRole(enum.Enum):
    COORDINATOR = "coordinator"
    RESEARCHER = "researcher"
    CRITIC = "critic"
    EXECUTOR = "executor"
    ANALYST = "analyst"
    PROGRAMMER = "programmer"
    COPYWRITER = "copywriter"
    DESIGNER = "designer"
    MARKETER = "marketer"
    SECURITY = "security"
    TESTER = "tester"
    IDEATOR = "ideator"

class Task(Base):
    __tablename__ = "tasks"
    
    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, nullable=False, index=True)
    user_id = Column(BigInteger, nullable=False)
    description = Column(Text, nullable=False)
    status = Column(Enum(TaskStatus), default=TaskStatus.PENDING)
    current_step = Column(Integer, default=0)
    max_steps = Column(Integer, default=25)
    model = Column(String(200), nullable=True)
    context_summary = Column(Text, nullable=True)
    final_answer = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    messages = relationship("Message", back_populates="task", cascade="all, delete-orphan")

class Message(Base):
    __tablename__ = "messages"
    
    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False)
    role = Column(String(50), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=func.now())
    
    task = relationship("Task", back_populates="messages")

class ChatSettings(Base):
    __tablename__ = "chat_settings"
    
    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, nullable=False, unique=True, index=True)
    model = Column(String(200), nullable=True)
    team = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
