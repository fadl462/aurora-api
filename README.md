# Aurora AI OS — Backend

The real Phase 3 implementation of `docs/06-api-specification.md`: auth, conversations, agents, and projects — all backed by an actual database, with a real model integration and a real automated test suite.

## Stack

FastAPI · SQLAlchemy · SQLite (dev) / Postgres (production) · pytest · Anthropic SDK — matching [`docs/09-tech-stack.md`](../aurora-ai-os/docs/09-tech-stack.md)'s backend choice.

## Running it locally

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/uvicorn app.main:app --reload
```

API docs (auto-generated from the real schemas): http://localhost:8000/docs

```bash
./venv/bin/pytest tests/ -v
```

96 tests, all passing.

## Document upload

`POST /v1/files/extract` accepts a real file upload and returns real extracted text — PDF, Word (.docx), PowerPoint (.pptx), Excel (.xlsx), and plain-text/code files (`.py`, `.js`, `.ts`, `.md`, `.json`, etc. — see `TEXT_LIKE_EXTENSIONS` in `app/document_extraction.py` for the full list). Extraction is tested against real generated files of each type (`tests/test_files.py`), not canned byte strings. Extracted text is capped at ~12,000 characters with a clear `truncated` flag rather than silently cutting content. Unsupported file types get a clear 415 error rather than failing unpredictably.

**Not yet built:** generating new documents (e.g., a PowerPoint from a prompt) — this endpoint only reads files, it doesn't write them. That's a distinct, larger feature.

## Usage tracking

Every account gets a token balance (`STARTING_TOKEN_BALANCE` in `app/models.py` — a placeholder figure for local dev, not a real billing tier). Every message sent deducts from it — **real token counts from Anthropic's own response when a model is connected, a clearly-flagged estimate when it isn't** (`tokens_are_estimated` in the orchestration response). `GET /v1/usage` exposes the current balance for the frontend's always-visible meter. Hitting zero returns a real `402` — the send is blocked before anything is written to the database, not silently allowed through, which is the entire point: nobody should ever be surprised by running out.

## Connecting a real model (optional but recommended)

Without any configuration, chat replies come from a clearly-labeled placeholder — this is intentional (see "What's real vs. placeholder" below), not a bug. To connect a real Claude model:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."      # your own key from console.anthropic.com
export ANTHROPIC_MODEL="claude-sonnet-5"    # optional — defaults to this if unset
```

Restart the server after setting these. Once set, chat replies are real model output. **Citations and confidence scores stay `null` in general chat mode** — that's deliberate. A plain model call with no search tool attached has no honest basis for either; fabricating them would be worse than omitting them. See "Research Engine" below for where citations do become real.

If the API key is invalid, the model ID doesn't exist, or the Anthropic API is unreachable, the app falls back gracefully to the labeled placeholder rather than returning a 500 — verified with a dedicated test for each failure mode (`tests/test_orchestration.py`).

## Research Engine

`mode="research"` on `POST /v1/conversations/{id}/messages` (used by the frontend's Research page) attaches Anthropic's real server-side web search tool (`web_search_20260209`) — Claude decides for itself whether/what to search, retrieves real results, and the response comes back with real citations attached to specific claims. `app/orchestration.py` extracts those into the same `Citation` shape the API already returns, deduplicated by URL.

General chat mode (no `mode`, or any mode other than `"research"`) never attaches this tool — a plain chat reply has no business searching the web on every message, and Anthropic bills web search per-search on top of normal tokens. Confidence scores stay `null` in both modes — having real citations doesn't give us a legitimate way to score confidence, so we don't fabricate one just because we can now show sources.

## Multi-model routing

The chat model picker in the frontend is real, not decorative — the `model` field on `POST /v1/conversations/{id}/messages` is actually threaded through to a real model choice in `app/orchestration.py`:

| Frontend choice | Real Anthropic model | When |
|---|---|---|
| `fast` | `claude-haiku-4-5-20251001` | Cheapest, fastest — simple/short asks |
| `balanced` | `claude-sonnet-5` | Default — solid reasoning, real-time speed |
| `ultra` | `claude-opus-4-8` | Deepest reasoning, slowest, most expensive |
| `auto` | one of the above | Chosen automatically by message length as a simple, legible proxy for task complexity — no extra model call spent "deciding" |

We deliberately do **not** expose GPT or Gemini as pickable options anywhere in the product yet — this backend only ever calls Anthropic, and a picker option with no real model behind it would be actively misleading rather than a placeholder worth having.

## What's real vs. placeholder

**Real:** the database layer (SQLAlchemy models, actual persistence — verified with real HTTP requests, not mocked), the full request/response contract from the API spec, error handling with the documented `{error: {code, message, request_id}}` shape, input validation, bcrypt password hashing + JWT sessions, ownership-scoped conversations/agents/projects/documents (each with a dedicated cross-user-isolation test), real project-scoped context walls for conversations and documents, a real Anthropic model integration with graceful failure handling and real multi-model routing, a real Research Engine with Anthropic's web search tool and real extracted citations, real document CRUD backing the Canvas surface, real document *generation* (`.pptx`/`.docx`/`.xlsx` from a prompt, downloadable), real per-account token usage tracking with hard enforcement at zero, real file upload and text extraction (PDF/Word/PowerPoint/Excel/code files), and a 125-test suite.

**Placeholder, by design:** confidence scores are always `null` — see "Research Engine" above for why. No SSO/MFA (`docs/07-security-and-compliance.md`) — email/password only.

## Deploying (Render)

1. Push this repo to GitHub (you've already done this).
2. On [render.com](https://render.com), New → Web Service → connect the `aurora-api` repo.
3. **Build command:** `pip install -r requirements.txt`
4. **Start command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
5. Add these environment variables in Render's dashboard:
   - `AURORA_SECRET_KEY` — generate one with `python3 -c "import secrets; print(secrets.token_hex(32))"`
   - `ANTHROPIC_API_KEY` — your key, if you want real replies
   - `ALLOWED_ORIGINS` — your Vercel frontend URL once you have it (e.g. `https://aurora-web.vercel.app`); comma-separate multiple origins
   - `DATABASE_URL` — see below
6. Deploy. Render gives you a URL like `https://aurora-api-xxxx.onrender.com` — that's what the frontend's `NEXT_PUBLIC_API_URL` needs to point at.

**About the database on Render:** without setting `DATABASE_URL`, this defaults to a local SQLite file — which lives on Render's *ephemeral* filesystem and gets wiped on every redeploy. Fine for a first test, not fine for anything real. To fix: add a free Postgres instance from Render's dashboard, copy its "Internal Database URL," set it as `DATABASE_URL`, and add `psycopg2-binary` to `requirements.txt` (not included by default since not everyone needs Postgres). Render's Postgres URLs sometimes start with `postgres://` — SQLAlchemy wants `postgresql://`; if you hit a connection error, that's the first thing to check.

## Known limitations (intentional, for this phase)

- **If you're updating from an earlier version of this repo, delete `aurora.db` before restarting** — this update added a new column to the `users` table, and this project doesn't have a migration tool (Alembic) set up yet. `Base.metadata.create_all()` only creates missing tables, it doesn't alter existing ones, so an old `aurora.db` file will be missing the new column and every request will error. Deleting the file and letting the app recreate it is the local-dev equivalent of a migration for now.
- `AURORA_SECRET_KEY` defaults to an insecure dev value with a loud warning if unset — always set a real one before deploying anywhere.
- SQLite by default — see the Render section above for why this matters for deployment specifically.
- Auth is email/password only — no SSO/MFA yet.
- Token pricing/balances are a placeholder figure, not tied to any real billing plan yet.
