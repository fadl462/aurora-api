"""
ORM models. Field names and relationships mirror the CONVERSATION and
MESSAGE entities in docs/05-database-design.md. UUIDs are stored as
strings for SQLite compatibility; Postgres would use the native UUID
type instead.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, LargeBinary, String, Text
from sqlalchemy.orm import relationship

from .database import Base


def new_uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Starting token allowance for a new account. This is a placeholder
# figure for local development — a real product would tie this to a
# billing plan (docs/02-product-requirements.md's tiers), not a
# hardcoded constant. Real, current usage from real API calls is what
# actually decrements this — see app/orchestration.py.
STARTING_TOKEN_BALANCE = 50_000


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=new_uuid)
    email = Column(String, nullable=False, unique=True, index=True)
    name = Column(String, nullable=True)
    hashed_password = Column(String, nullable=False)
    token_balance = Column(Integer, nullable=False, default=STARTING_TOKEN_BALANCE)
    created_at = Column(DateTime, default=utcnow)

    conversations = relationship("Conversation", back_populates="user", cascade="all, delete-orphan")
    agents = relationship("Agent", back_populates="user", cascade="all, delete-orphan")
    projects = relationship("Project", back_populates="user", cascade="all, delete-orphan")
    documents = relationship("Document", back_populates="user", cascade="all, delete-orphan")
    generated_documents = relationship("GeneratedDocument", back_populates="user", cascade="all, delete-orphan")


class Project(Base):
    __tablename__ = "projects"

    id = Column(String, primary_key=True, default=new_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    color = Column(String, nullable=False, default="bg-aurora-2")
    created_at = Column(DateTime, default=utcnow)

    user = relationship("User", back_populates="projects")
    conversations = relationship("Conversation", back_populates="project")
    documents = relationship("Document", back_populates="project")


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(String, primary_key=True, default=new_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    project_id = Column(String, ForeignKey("projects.id"), nullable=True)
    title = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    user = relationship("User", back_populates="conversations")
    project = relationship("Project", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id = Column(String, primary_key=True, default=new_uuid)
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=False)
    role = Column(String, nullable=False)  # "user" | "assistant"
    content = Column(Text, nullable=False)
    model_used = Column(String, nullable=True)
    citations = Column(JSON, nullable=True)  # list[{source, page?}]
    confidence = Column(String, nullable=True)  # "high" | "moderate" | "low"
    created_at = Column(DateTime, default=utcnow)

    conversation = relationship("Conversation", back_populates="messages")


class Agent(Base):
    """
    Mirrors the AGENT entity in docs/05-database-design.md, plus the
    display fields (avatar_letter/avatar_color_class) the frontend needs
    that the doc's schema sketch didn't itemize.

    Every user gets their own copy of the five starter agents on
    registration (see routers/auth.py) rather than agents being a shared
    global catalog — this reuses the exact same ownership-scoping
    pattern already proven for conversations, instead of inventing a
    second permission model for a "shared template" concept.
    """

    __tablename__ = "agents"

    id = Column(String, primary_key=True, default=new_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    system_prompt = Column(Text, nullable=False)
    tools = Column(JSON, nullable=False)  # list[{name, tier}]
    status = Column(String, nullable=False, default="idle")  # "active" | "idle"
    avatar_letter = Column(String, nullable=False)
    avatar_color_class = Column(String, nullable=False)
    created_at = Column(DateTime, default=utcnow)

    user = relationship("User", back_populates="agents")
    runs = relationship("AgentRun", back_populates="agent", cascade="all, delete-orphan")
    approvals = relationship("PendingApproval", back_populates="agent", cascade="all, delete-orphan")


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id = Column(String, primary_key=True, default=new_uuid)
    agent_id = Column(String, ForeignKey("agents.id"), nullable=False)
    title = Column(String, nullable=False)
    status = Column(String, nullable=False)  # "done" | "pending" | "running"
    meta = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    agent = relationship("Agent", back_populates="runs")


class PendingApproval(Base):
    __tablename__ = "pending_approvals"

    id = Column(String, primary_key=True, default=new_uuid)
    agent_id = Column(String, ForeignKey("agents.id"), nullable=False)
    tier = Column(String, nullable=False)  # "read" | "low" | "medium" | "high"
    action = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="pending")  # "pending" | "approved" | "denied"
    created_at = Column(DateTime, default=utcnow)
    decided_at = Column(DateTime, nullable=True)

    agent = relationship("Agent", back_populates="approvals")


class Document(Base):
    """
    Backs the Canvas surface's Document panel — a real, persisted,
    user-editable document, distinct from a conversation. Deliberately
    plain (title + content, no versioning/rich-text yet) — this is the
    smallest real slice that makes Canvas an actual document workspace
    rather than a static mockup of one.
    """

    __tablename__ = "documents"

    id = Column(String, primary_key=True, default=new_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    project_id = Column(String, ForeignKey("projects.id"), nullable=True)
    title = Column(String, nullable=False, default="Untitled document")
    content = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    user = relationship("User", back_populates="documents")
    project = relationship("Project", back_populates="documents")


class GeneratedDocument(Base):
    """
    Backs real document generation (app/document_generation.py) — the
    write-side counterpart to Document above, which only ever backed
    reading/editing Canvas text. A GeneratedDocument is an actual
    .pptx/.docx/.xlsx file, stored as bytes and served back for
    download, not editable plain text.

    Storing bytes directly in the DB row (LargeBinary) rather than the
    filesystem — see database.py's note on Render's free-tier ephemeral
    disk; a generated file living only on disk would vanish on the next
    redeploy, but a DB row survives exactly as long as the database does.
    """

    __tablename__ = "generated_documents"

    id = Column(String, primary_key=True, default=new_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False, default="Untitled")
    format = Column(String, nullable=False)  # "pptx" | "docx" | "xlsx"
    prompt = Column(Text, nullable=False)
    file_data = Column(LargeBinary, nullable=False)
    is_placeholder = Column(Integer, nullable=False, default=0)  # bool as int for SQLite portability
    created_at = Column(DateTime, default=utcnow)

    user = relationship("User", back_populates="generated_documents")
