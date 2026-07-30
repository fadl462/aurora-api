import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import models
from .database import Base, engine
from .routers import agents, auth, billing, conversations, document_generation, documents, files, projects, usage

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Aurora AI OS API",
    description="Backend implementation of docs/06-api-specification.md",
    version="0.1.0",
)

# CORS origins are environment-driven so production doesn't stay locked
# to localhost:3000. ALLOWED_ORIGINS is a comma-separated list, e.g.
# "https://aurora-web.vercel.app,https://app.aurora.ai" — see
# docs/07-security-and-compliance.md for why this shouldn't be a
# wildcard in production.
_default_origins = "http://localhost:3000"
allowed_origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", _default_origins).split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(conversations.router)
app.include_router(agents.router)
app.include_router(projects.router)
app.include_router(documents.router)
app.include_router(document_generation.router)
app.include_router(usage.router)
app.include_router(files.router)
app.include_router(billing.router)


@app.get("/health")
def health():
    return {"status": "ok"}
