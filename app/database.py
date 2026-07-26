"""
Database engine and session management.

Defaults to SQLite for local development. In production (e.g. on
Render), set DATABASE_URL to a real Postgres connection string —
that's the only change needed; the schema and queries are already
written against standard SQLAlchemy, not SQLite-specific features.

Note: SQLite on most hosting platforms' free tiers lives on an
ephemeral filesystem — data is wiped on every redeploy. This is fine
for local development but NOT fine for any real deployment; use a real
Postgres instance (Render's free tier includes one) for anything
beyond local testing.
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./aurora.db")

# SQLite needs this specific flag for FastAPI's threaded request
# handling; Postgres and other real databases don't use this argument
# at all, so it's only applied conditionally.
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
