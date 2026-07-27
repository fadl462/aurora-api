"""
Request/response schemas. These are the actual implementation of the
JSON shapes documented in docs/06-api-specification.md — that doc's
examples and this file are required to stay in sync.
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    project_id: Optional[str] = None


class DocumentUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    content: str
    project_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class GeneratedDocumentCreate(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=4000)
    format: Literal["pptx", "docx", "xlsx"]


class GeneratedDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    format: Literal["pptx", "docx", "xlsx"]
    prompt: str
    is_placeholder: bool
    size_bytes: int
    created_at: datetime


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
    name: Optional[str] = Field(None, max_length=80)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    name: Optional[str] = None
    created_at: datetime


class UserUpdate(BaseModel):
    # Optional[str] with no Field(min_length=...) deliberately — an
    # empty string here means "clear my name back to the email-derived
    # default," which is a legitimate real choice, not invalid input.
    # The router strips and treats whitespace-only the same way.
    name: Optional[str] = None


class LoginEventOut(BaseModel):
    """Deliberately does NOT include ip_address or user_agent — the
    person needs to recognize "was this me," which the derived
    device/location labels answer; their own raw IP echoed back adds
    no value and is unnecessary exposure of data already logged
    server-side for security purposes."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    device_label: str
    location_label: Optional[str] = None
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


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(min_length=1, max_length=300)
    system_prompt: str = Field(min_length=1, max_length=4000)
    tools: list[ToolSpec] = []

    @field_validator("name")
    @classmethod
    def name_must_have_visible_characters(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("name can't be empty or whitespace-only")
        return stripped


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


class PendingApprovalWithAgentOut(PendingApprovalOut):
    """Same shape as PendingApprovalOut, plus just enough agent context
    (name/avatar) for a unified inbox to render without a second
    round-trip per approval to look up which agent it belongs to."""

    agent_name: str
    agent_avatar_letter: str
    agent_avatar_color_class: str
