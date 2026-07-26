"""
Starter agents seeded for every new user on registration. Content
deliberately mirrors what was previously hardcoded in the frontend's
lib/mock-data.ts, so the transition from mock to real data doesn't
change what anyone sees — only where it comes from.
"""

from sqlalchemy.orm import Session

from . import models


def seed_starter_projects(db: Session, user_id: str) -> None:
    db.add_all(
        [
            models.Project(user_id=user_id, name="JoblyHub Outreach", color="bg-aurora-2"),
            models.Project(user_id=user_id, name="JMK Tender Monitor", color="bg-aurora-1"),
            models.Project(user_id=user_id, name="RoutePilot AI", color="bg-aurora-3"),
            models.Project(user_id=user_id, name="Freelancer Profile Repositioning", color="bg-aurora-4"),
        ]
    )
    db.commit()


def seed_starter_agents(db: Session, user_id: str) -> None:
    researcher = models.Agent(
        user_id=user_id,
        name="Researcher",
        description="Multi-source search, citation, and fact-checking for open-ended questions.",
        system_prompt=(
            "You are a research agent. Search web, academic, and news sources for the user's "
            "query. Always cite sources and return a confidence score. Never fabricate a source. "
            "Prefer primary sources over aggregators when both are available."
        ),
        tools=[
            {"name": "web_search", "tier": "read"},
            {"name": "academic_search", "tier": "read"},
            {"name": "pdf_extract", "tier": "read"},
            {"name": "save_to_knowledge_vault", "tier": "low"},
        ],
        status="active",
        avatar_letter="R",
        avatar_color_class="bg-aurora-1/10 text-aurora-1",
    )
    developer = models.Agent(
        user_id=user_id,
        name="Developer",
        description="Code generation, debugging, and PR review scoped to connected repos.",
        system_prompt=(
            "You are a coding agent scoped to the repositories the user has connected. Write "
            "tests alongside code changes. Never force-push. Open a draft PR rather than "
            "merging directly."
        ),
        tools=[
            {"name": "git", "tier": "medium"},
            {"name": "sandbox_execute", "tier": "low"},
            {"name": "docs_read", "tier": "read"},
        ],
        status="active",
        avatar_letter="D",
        avatar_color_class="bg-aurora-2/10 text-aurora-2",
    )
    outreach = models.Agent(
        user_id=user_id,
        name="Outreach Manager",
        description="Drafts and tracks cold outreach sequences with follow-up cadence.",
        system_prompt=(
            "You manage outreach sequences. Draft messages for review before send. Track "
            "replies and update follow-up cadence. Never send externally without explicit "
            "approval."
        ),
        tools=[
            {"name": "email_draft", "tier": "low"},
            {"name": "email_send", "tier": "high"},
            {"name": "sheets", "tier": "medium"},
        ],
        status="idle",
        avatar_letter="O",
        avatar_color_class="bg-aurora-3/10 text-aurora-3",
    )
    analyst = models.Agent(
        user_id=user_id,
        name="Data Analyst",
        description="SQL, spreadsheet cleanup, and dashboard generation from raw exports.",
        system_prompt=(
            "You clean and analyze tabular data. Show your working (queries, transformations) "
            "before presenting conclusions. Flag data quality issues rather than silently "
            "working around them."
        ),
        tools=[
            {"name": "sql_query", "tier": "read"},
            {"name": "excel_edit", "tier": "low"},
            {"name": "chart_generate", "tier": "low"},
        ],
        status="idle",
        avatar_letter="A",
        avatar_color_class="bg-aurora-4/10 text-aurora-4",
    )
    proposal_writer = models.Agent(
        user_id=user_id,
        name="Proposal Writer",
        description="Drafts technical and financial proposals from a brief and past templates.",
        system_prompt=(
            "You draft proposals from a brief plus the user's Knowledge Vault templates. Match "
            "the house style found in past approved proposals. Flag any figures you couldn't "
            "source."
        ),
        tools=[
            {"name": "docs_write", "tier": "low"},
            {"name": "knowledge_vault_read", "tier": "read"},
        ],
        status="idle",
        avatar_letter="P",
        avatar_color_class="bg-warning/10 text-warning",
    )

    db.add_all([researcher, developer, outreach, analyst, proposal_writer])
    db.flush()  # populate IDs without committing yet

    db.add_all(
        [
            models.AgentRun(
                agent_id=researcher.id,
                title="Competitor pricing — AI orchestration platforms",
                status="done",
                meta="5 sources · high confidence",
            ),
            models.AgentRun(
                agent_id=researcher.id,
                title="GTVP tracer study — misclassification audit sources",
                status="done",
                meta="9 sources · moderate confidence",
            ),
            models.AgentRun(
                agent_id=researcher.id,
                title="Ghana teacher deployment governance — literature scan",
                status="running",
                meta="Running · 3 sources so far",
            ),
        ]
    )

    db.add(
        models.PendingApproval(
            agent_id=researcher.id,
            tier="medium",
            action='save_to_knowledge_vault("GTVP misclassification findings — 493 records")',
        )
    )

    db.commit()
