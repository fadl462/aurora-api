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


# Starting token allowance for a new (free-tier) account. Kept in sync
# with app/billing.py's "free" Plan.token_allowance — that's the real
# source of truth for all plan tiers now; this constant exists because
# a brand-new user needs a starting balance before any plan logic runs
# at all (e.g. before their first login/session even happens).
STARTING_TOKEN_BALANCE = 50_000


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=new_uuid)
    email = Column(String, nullable=False, unique=True, index=True)
    name = Column(String, nullable=True)
    hashed_password = Column(String, nullable=False)
    token_balance = Column(Integer, nullable=False, default=STARTING_TOKEN_BALANCE)
    # "free" until a real Stripe checkout actually completes — see
    # app/billing.py and the webhook handler in routers/billing.py.
    # Never set directly from client input; only the webhook (which
    # verifies Stripe's signature) or an admin action should change this.
    plan_tier = Column(String, nullable=False, default="free")
    stripe_customer_id = Column(String, nullable=True, unique=True)
    stripe_subscription_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    conversations = relationship("Conversation", back_populates="user", cascade="all, delete-orphan")
    agents = relationship("Agent", back_populates="user", cascade="all, delete-orphan")
    projects = relationship("Project", back_populates="user", cascade="all, delete-orphan")
    documents = relationship("Document", back_populates="user", cascade="all, delete-orphan")
    generated_documents = relationship("GeneratedDocument", back_populates="user", cascade="all, delete-orphan")
    login_events = relationship("LoginEvent", back_populates="user", cascade="all, delete-orphan")
    refresh_tokens = relationship("RefreshToken", back_populates="user", cascade="all, delete-orphan")


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
    user-editable document, distinct from a conversation.
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
    versions = relationship(
        "DocumentVersion", back_populates="document", cascade="all, delete-orphan", order_by="desc(DocumentVersion.created_at)"
    )


class DocumentVersion(Base):
    """A real snapshot of a Document's prior state, taken before an
    update overwrites it — this is what makes Canvas's autosave safe
    rather than a one-way door. See routers/documents.py's
    update_document for exactly when a snapshot gets taken: not on
    every single autosave tick (that would flood the table for no
    real benefit, since most autosaves are seconds apart mid-sentence),
    but throttled to one snapshot per VERSION_SNAPSHOT_MIN_INTERVAL, so
    the history reads like meaningful checkpoints rather than a keystroke
    log."""

    __tablename__ = "document_versions"

    id = Column(String, primary_key=True, default=new_uuid)
    document_id = Column(String, ForeignKey("documents.id"), nullable=False)
    title = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow)

    document = relationship("Document", back_populates="versions")


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


class LoginEvent(Base):
    """A real record of each successful login, powering the Settings
    page's 'Recent sign-ins' list — the honest way to answer 'where is
    someone accessing this from': a transparent, user-visible security
    log, not covert tracking.

    ip_address is stored for the geolocation lookup and potential abuse
    investigation, but deliberately never returned by the API — see
    LoginEventOut in schemas.py. What the person actually needs is the
    derived device/location labels, not their own raw IP echoed back.
    """

    __tablename__ = "login_events"

    id = Column(String, primary_key=True, default=new_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    ip_address = Column(String, nullable=False)
    user_agent = Column(String, nullable=True)
    device_label = Column(String, nullable=False, default="Unknown device")
    # Null, not a fabricated placeholder, when geolocation genuinely
    # couldn't be resolved (private/dev IP, lookup timeout, lookup
    # failure) — see resolve_location() in routers/auth.py.
    location_label = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    user = relationship("User", back_populates="login_events")


class RefreshToken(Base):
    """Backs real 'stay logged in' sessions. Only ever stores a hash of
    the actual token (see auth.py's _hash_refresh_token) — same
    principle as password storage: the raw value exists only in the
    client's hands and briefly in memory at issuance time.

    login_event_id ties a refresh token back to the LoginEvent it was
    issued alongside, so a future 'sign out this device' feature on the
    Settings page's sign-in list has something concrete to revoke.
    """

    __tablename__ = "refresh_tokens"

    id = Column(String, primary_key=True, default=new_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    login_event_id = Column(String, ForeignKey("login_events.id"), nullable=True)
    token_hash = Column(String, nullable=False, unique=True, index=True)
    created_at = Column(DateTime, default=utcnow)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="refresh_tokens")
