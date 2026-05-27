---
title: AdvoAI Backend
emoji: ⚖️
colorFrom: blue
colorTo: indigo
sdk: docker
sdk_version: "1.0.0"
python_version: "3.11"
app_file: app.py
pinned: false
---

# AdvoAI Backend

**FastAPI backend powering the AdvoAI AI Legal Assistant.**

A hybrid Parent-Child RAG (Retrieval-Augmented Generation) system for Uzbekistan's legal documents, backed by PostgreSQL + pgvector and Google Gemini.

---

## Quick Start

```bash
# Environment
conda create -n basira_libs python=3.12 -y
conda activate basira_libs
pip install -r requirements.txt

# Configuration
cp .env.example .env
# Fill in DATABASE_URL, GOOGLE_API_KEY, JWT_SECRET_KEY

# Database
psql "$DATABASE_URL" -f app/database/schema_unified.sql

# Run
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at `http://localhost:8000`. Interactive docs at `/docs`.

---

## Project Structure

```
app/
├── main.py                  # FastAPI app + CORS + route mounting
├── config.py                # Pydantic settings loaded from .env
├── middleware.py             # JWT auth, role checks
├── database/
│   ├── connection.py         # PostgreSQL connection pool (psycopg2)
│   ├── queries.py            # All SQL query functions (DRY)
│   └── schema_unified.sql    # Complete DB schema (idempotent, safe to re-run)
├── ingestion/
│   ├── lex_parser.py         # Lex.uz HTML → structured Markdown
│   ├── chunker.py            # Semantic element-based chunking
│   └── main_ingest.py        # End-to-end ingestion orchestrator
├── routes/
│   ├── auth.py               # Register, login, Google OAuth, profile update
│   ├── chat.py               # POST /api/chat/ — RAG chat endpoint
│   ├── sessions.py           # CRUD for chat sessions
│   ├── admin.py              # Admin panel APIs (settings, docs, users)
│   └── health.py             # GET /api/health/
└── services/
    ├── embedder.py            # Gemini Embedding 2 service (1536-dim)
    ├── llm_client.py          # Google Gemini wrapper + retry logic
    ├── rate_limiter.py        # Atomic rate limiting
    ├── prompts.py             # System prompts for legal analysis
    └── rag_pipeline.py        # Full RAG: embed → search → reconstruct → generate
```

---

## Core Concepts

### RAG Pipeline (`services/rag_pipeline.py`)

1. **Embed** user query with Gemini Embedding 2
2. **Search** pgvector for top-K similar chunks (HNSW cosine)
3. **Reconstruct** full parent documents from matched chunks
4. **Generate** answer with Gemini using full document context + chat history

### Ingestion Pipeline (`ingestion/`)

```
Lex.uz URL → lex_parser.py → Markdown + metadata
                                    ↓
                              chunker.py → semantic chunks
                                    ↓
                             embedder.py → 1536-dim vectors
                                    ↓
                            PostgreSQL (documents + chunks tables)
```

### Authentication (`middleware.py` + `routes/auth.py`)

- **JWT** tokens in HTTP-only cookies (`advoai_token`)
- **bcrypt** password hashing
- **Google OAuth** via ID token verification
- **Middleware**: `require_auth` extracts user from cookie, checks ban status
- **Middleware**: `require_admin` verifies `role = 'admin'`
- **Rate limiting**: guest (by fingerprint) and registered user (by user ID)

### Rolling Chat Memory (`routes/chat.py`)

Each session maintains a `rolling_summary` field. After every exchange, the LLM condenses the full conversation into a concise summary. This summary is sent as context in subsequent messages, enabling coherent multi-turn conversations without exceeding token limits.

---

## API Routes

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `POST` | `/api/auth/register` | — | Register with email/password |
| `POST` | `/api/auth/login` | — | Login |
| `POST` | `/api/auth/google` | — | Google OAuth |
| `POST` | `/api/auth/logout` | — | Clear cookie |
| `GET` | `/api/auth/me` | JWT | Current user |
| `PATCH` | `/api/auth/me` | JWT | Update profile |
| `POST` | `/api/chat/` | — | RAG chat (supports guests) |
| `GET` | `/api/sessions` | JWT | List sessions |
| `POST` | `/api/sessions` | JWT | Create session |
| `PATCH` | `/api/sessions/{id}` | JWT | Rename/pin session |
| `DELETE` | `/api/sessions/{id}` | JWT | Delete session |
| `GET` | `/api/admin/stats` | Admin | Dashboard stats |
| `GET/PATCH` | `/api/admin/settings` | Admin | System settings |
| `GET/PATCH/DELETE` | `/api/admin/documents/{id}` | Admin | Document CRUD |
| `GET` | `/api/admin/users` | Admin | List users |
| `PATCH` | `/api/admin/users/{id}/ban` | Admin | Toggle ban |
| `PATCH` | `/api/admin/users/{id}/role` | Admin | Change role |
| `GET` | `/api/admin/users/{id}/stats` | Admin | User analytics |

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | ✅ | — | PostgreSQL connection string |
| `GOOGLE_API_KEY` | ✅ | — | Google Gemini API key |
| `JWT_SECRET_KEY` | ✅ | — | Secret for JWT signing |
| `JWT_ALGORITHM` | — | `HS256` | JWT algorithm |
| `JWT_EXPIRY_HOURS` | — | `72` | Token expiry |
| `GOOGLE_CLIENT_ID` | — | — | For Google OAuth |
| `GOOGLE_CLIENT_SECRET` | — | — | For Google OAuth |
| `EMBEDDING_MODEL` | — | `gemini-embedding-2` | Google embedding model |
| `EMBEDDING_DIMENSIONS` | — | `1536` | Output dimensions |
| `ENVIRONMENT` | — | `development` | `development` / `production` |
| `GUEST_MESSAGE_LIMIT` | — | `3` | Overridden by DB `system_settings` |
| `FREE_DAILY_LIMIT` | — | `20` | Overridden by DB `system_settings` |
| `CORS_ORIGINS` | — | `http://localhost:3000` | Comma-separated CORS list |

---

## Database

Uses **Neon PostgreSQL** with **pgvector** extension.

Apply the schema (idempotent — safe to re-run):

```bash
psql "$DATABASE_URL" -f app/database/schema_unified.sql
```

| Table | Purpose |
|---|---|
| `documents` | Full legal documents with metadata |
| `chunks` | Vector-indexed search chunks (FK → documents, CASCADE) |
| `users` | User accounts (email/Google, roles, ban status) |
| `chat_sessions` | Chat history with rolling summaries |
| `usage_logs` | Daily message counts per user |
| `guest_usage` | Guest rate limiting by browser fingerprint |
| `system_settings` | Admin-configurable key-value store |

---

## Scripts

| Script | Usage |
|---|---|
| `scripts/run_schema.py` | `python scripts/run_schema.py` — Apply schema using `.env` |
| `scripts/repair_titles.py` | `python scripts/repair_titles.py` — Re-extract titles from Lex.uz |

---

## Docker

```bash
docker build -t advoai-backend .
docker run -p 8000:8000 --env-file .env advoai-backend
```