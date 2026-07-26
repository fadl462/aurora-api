"""
Request/response schemas. These are the actual implementation of the
JSON shapes documented in docs/06-api-specification.md — that doc's
examples and this file are required to stay in sync.
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class Citation(BaseModel):
    source: str
    page: Optional[int] = None


class ConversationCreate(BaseModel):
    title: Optional[str] = None
    project_id: Optional[str] = None


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: Optional[str]
    project_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    color: str = "bg-aurora-2"


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    color: str
    created_at: datetime
    thread_count: int = 0


class DocumentCreate(BaseModel):
    title: str = "Untitled document"
    content: str = ""


class DocumentUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    content: str
    created_at: datetime
    updated_at: datetime


class MessageCreate(BaseModel):
    content: str = Field(..., min_length=1)
    model: str = "auto"
    mode: Optional[str] = None


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True, protected_namespaces=())

    id: str = Field(..., alias="message_id")
    conversation_id: str
    role: Literal["user", "assistant"]
    content: str
    model_used: Optional[str] = None
    citations: Optional[list[Citation]] = None
    confidence: Optional[float] = None
    created_at: datetime


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class UserCreate(BaseModel):
    email: str
    password: str = Field(..., min_length=8)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UsageOut(BaseModel):
    balance: int
    starting_balance: int
    percent_remaining: float


class ToolSpec(BaseModel):
    name: str
    tier: Literal["read", "low", "medium", "high"]


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str
    system_prompt: str
    tools: list[ToolSpec]
    status: Literal["active", "idle"]
    avatar_letter: str
    avatar_color_class: str


class AgentRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_id: str
    title: str
    status: Literal["done", "pending", "running"]
    meta: Optional[str]
    created_at: datetime


class PendingApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_id: str
    tier: Literal["read", "low", "medium", "high"]
    action: str
    status: Literal["pending", "approved", "denied"]
    created_at: datetime
    decided_at: Optional[datetime]
