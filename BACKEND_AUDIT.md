# AdvoAI Backend Codebase Audit

This document serves as a comprehensive technical audit of the current state of the AdvoAI backend, highlighting recent architectural upgrades, design patterns, security measures, and areas for potential future improvement.

---

## 1. Architectural Highlights

### Agentic RAG Implementation
The core chat pipeline has been successfully upgraded to an "Agentic RAG" pattern.
- **Router Agent (`llm_client.py: route_query`)**: Uses `gemma-4-31b-it` to classify user intent. If `legal_rag` is triggered, the model dynamically generates an optimized semantic search query rather than relying on the raw user input.
- **Strict Guardrails (`prompts.py`)**: General knowledge questions and basic definitions (e.g., "What is a constitution?") are strictly flagged as `conversational` to prevent wasteful vector database searches. Tool calling is strictly forbidden via prompting, forcing the model to rely solely on structured JSON outputs.

### High-Performance Asynchronous Offloading
Heavy LLM operations and blocking I/O have been successfully decoupled from the HTTP response cycle.
- **Background Tasks (`chat.py`)**: Both chat session title generation and the sliding-window archive summarization are pushed to `FastAPI BackgroundTasks`. This allows the API to return answers instantly, improving the UX significantly.
- **Thread Offloading (`main_ingest.py`)**: Synchronous network calls (e.g. `requests.get` via `LexParser`) and CPU-intensive parsing (`BeautifulSoup`, `MarkItDown`) are wrapped in `asyncio.to_thread()` so they do not block the Uvicorn event loop during document ingestion.

### Robust LLM Communication
- **Exponential Backoff (`llm_client.py: _generate_with_retry`)**: A custom retry loop using exponential backoff (1s, 2s, 4s) ensures that the system gracefully recovers from intermittent network failures or LLM API rate limits.

---

## 2. Core Modules Review

The entire backend operates on a **fully asynchronous, non-blocking architecture** designed for high concurrency and zero event-loop blocking.

### `app/database/`
- **`connection.py`**: Manages a global `asyncpg.Pool` initialized during the FastAPI lifespan (`@asynccontextmanager`). It uses `yield pool` so all routers can safely borrow connections. Context managers like `get_db_connection()` are used across all queries to ensure safe releasing of connections back to the pool.
- **`queries.py` & `schema_unified.sql`**: Contains all SQL functions parameterized securely. The schema uses `pgvector` with HNSW indexes (`m = 16, ef_construction = 64`) for extremely fast, high-recall vector search. 

### `app/services/`
- **`llm_client.py` (`GeminiClient`)**: Implements `google-genai` SDK natively via the `aio` (async) properties (`await client.aio.models.generate_content`). This isolation prevents network blocking. It includes a custom retry loop with exponential backoff using `asyncio.sleep()`.
- **`embedder.py` (`GeminiEmbedder`)**: Similarly uses the async `aio` SDK to interface with `gemini-embedding-2`. It handles automatic dimension normalization to 1536-dim and handles `429 Too Many Requests` elegantly using an async retry loop.
- **`rag_pipeline.py`**: The "Mega-chunking" or "Parent-Child" RAG engine. It embeds the query, searches for top matching child chunks in `pgvector`, extracts the `document_part_id`, and fetches the *full parent document markdown* (up to 4000 tokens per part) from the database to supply as context to the LLM. This solves the classic RAG fragmentation problem.

### `app/routes/`
- **`chat.py`**: The core conversational endpoint. Highly optimized. It routes query intents (`legal_rag` vs `conversational`) synchronously, delegates context retrieval, and delegates history trimming and auto-titling to `FastAPI BackgroundTasks` running safely in the async event loop.
- **`auth.py`**: Handles user authentication via standard email/password or Google OAuth. It uses `asyncio.to_thread()` to safely wrap synchronous Google SDK token validation requests.

### `tests/`
- **Test Coverage**: 13 comprehensive `pytest` cases across 4 files (`test_rag.py`, `test_ingestion.py`, `test_chat_route.py`, `test_llm_client.py`).
- **Async Mocks**: External endpoints, database operations, and Google GenAI SDK methods are fully mocked using `unittest.mock.AsyncMock` and `@pytest.mark.asyncio`.

---

## 3. Security and Stability Assessment

- **Authentication / Authorization**: Handled cleanly in `app/middleware.py` utilizing secure JWTs attached via `HTTPOnly` cookies or Bearer headers.
- **Event Loop Stability**: The backend strictly avoids `requests`, `urllib`, and `time.sleep()`. All external I/O uses native async interfaces or `asyncio.to_thread()` offloading, ensuring that `uvicorn` can shut down gracefully without `Address already in use` zombie processes.
- **Database Thread Safety**: Managed perfectly via `asyncpg.Pool`. Connections are never leaked due to strict reliance on asynchronous context managers (`async with pool.acquire()`).

---

## 4. API Endpoints Catalog

The application exposes the following RESTful endpoints grouped by their respective routers.

### Health (`/api/health`)
- `GET /api/health/`: Standard health check indicating the API is online.
- `GET /api/health/db`: Deep health check that verifies the PostgreSQL connection pool is functioning.

### Auth (`/api/auth`)
- `POST /api/auth/register`: Creates a new user account with a securely hashed password.
- `POST /api/auth/login`: Authenticates a user and returns an HTTP-only JWT cookie (`advoai_token`).
- `POST /api/auth/google`: Handles Google OAuth login/registration by verifying Google ID tokens.
- `POST /api/auth/logout`: Clears the JWT session cookie.
- `GET /api/auth/me`: Retrieves the currently authenticated user's profile and limits.
- `PATCH /api/auth/me`: Allows a user to update their profile information.

### Chat (`/api/chat`)
- `POST /api/chat/`: The core Agentic RAG chat endpoint. Accepts a JSON payload containing the user's `question` and an optional `session_id`. Automatically routes the query, retrieves context, and responds. Rate-limited and utilizes background tasks for history compression.

### Sessions (`/api/sessions`)
- `GET /api/sessions/`: Lists all chat sessions belonging to the authenticated user.
- `POST /api/sessions/`: Explicitly creates a new empty chat session.
- `GET /api/sessions/{session_id}`: Retrieves the full message history (raw messages and summary) for a specific session.
- `PATCH /api/sessions/{session_id}`: Allows the user to manually rename a chat session.
- `DELETE /api/sessions/{session_id}`: Permanently deletes a chat session and its history.

### Admin (`/api/admin`) *(Requires `is_admin=True`)*
- `GET /api/admin/stats`: Returns global system metrics including total users, total queries, and ingested document counts.
- `GET /api/admin/settings`: Lists dynamic system configurations (e.g., the current active LLM models).
- `PATCH /api/admin/settings`: Updates dynamic system configurations in the database.
- `GET /api/admin/users`: Lists all registered users on the platform.
- `PATCH /api/admin/users/{user_id}/role`: Elevates or demotes a user's role (e.g., granting admin privileges).
- `PATCH /api/admin/users/{user_id}/ban`: Bans or unbans a user from the platform.
- `GET /api/admin/users/{user_id}/stats`: Retrieves usage statistics and limits for a specific user.
- `GET /api/admin/documents`: Lists all ingested Lex.uz legal documents currently in the vector database.
- `GET /api/admin/documents/{doc_id}`: Retrieves deep metadata and chunk information for a specific document.
- `PATCH /api/admin/documents/{doc_id}`: Modifies a document's metadata (e.g., toggling its `is_active` status).
- `DELETE /api/admin/documents/{doc_id}`: Permanently purges a document and all its vector chunks from the database.
- `POST /api/admin/ingest`: Manually triggers the ingestion pipeline for a new Lex.uz document URL.

---

## 5. API Response Examples

This section provides typical JSON response structures for key endpoints in the AdvoAI backend, useful for frontend integration and debugging.

### Chat Endpoint
**`POST /api/chat/`**
```json
{
  "answer": "The Constitution of the Republic of Uzbekistan is the supreme law...",
  "model_used": "gemini-3.1-flash-lite",
  "citations": [
    {
      "id": "doc-uuid-1234",
      "title": "Constitution of the Republic of Uzbekistan",
      "source_url": "https://lex.uz/docs/...",
      "text": "Full markdown text of the retrieved chunk..."
    }
  ],
  "session_id": "session-uuid-5678",
  "intent": "legal_rag",
  "metadata": {
    "context_length_chars": 12050,
    "prompt_length_chars": 12500,
    "chunks_used": 3,
    "documents_used": 1
  }
}
```

### Auth Endpoints
**`POST /api/auth/login` (and `/register`, `/google`)**
```json
{
  "message": "Login successful.",
  "user": {
    "id": "user-uuid-1234",
    "email": "user@example.com",
    "full_name": "John Doe",
    "role": "user",
    "auth_provider": "email",
    "email_verified": true
  },
  "token": "eyJhbGciOiJIUzI1NiIsInR5..."
}
```

**`GET /api/auth/me`**
```json
{
  "user": {
    "id": "user-uuid-1234",
    "email": "user@example.com",
    "full_name": "John Doe",
    "role": "user",
    "auth_provider": "email",
    "email_verified": true
  }
}
```

### Sessions Endpoints
**`GET /api/sessions/`**
```json
{
  "sessions": [
    {
      "id": "session-uuid-123",
      "title": "Contract Dispute Inquiry",
      "is_pinned": false,
      "created_at": "2026-05-28T10:00:00"
    }
  ]
}
```

**`GET /api/sessions/{session_id}`**
```json
{
  "session": {
    "id": "session-uuid-123",
    "user_id": "user-uuid-1234",
    "title": "Contract Dispute Inquiry",
    "session_summary": "User asked about breach of contract penalties under the Civil Code.",
    "is_pinned": false,
    "messages": [
      {
        "id": "msg-uuid-1",
        "role": "user",
        "content": "What are the penalties for late payment?",
        "created_at": "2026-05-28T10:05:00"
      },
      {
        "id": "msg-uuid-2",
        "role": "assistant",
        "content": "According to the Civil Code...",
        "created_at": "2026-05-28T10:05:05"
      }
    ]
  }
}
```

**`POST /api/sessions/`**
```json
{
  "session": {
    "id": "new-session-uuid",
    "user_id": "user-uuid-1234",
    "title": "New Chat",
    "session_summary": null,
    "is_pinned": false,
    "created_at": "2026-05-28T12:00:00"
  }
}
```

### Admin Endpoints (Requires `role="admin"`)
**`GET /api/admin/stats`**
```json
{
  "stats": {
    "total_users": 15,
    "total_documents": 42,
    "total_sessions": 128
  }
}
```

**`GET /api/admin/settings`**
```json
{
  "settings": [
    {
      "key": "current_llm_model",
      "value": "gemini-3.1-flash-lite"
    }
  ]
}
```

**`PATCH /api/admin/settings`**
```json
{
  "message": "Updated: current_llm_model",
  "settings": [
    {
      "key": "current_llm_model",
      "value": "gemini-3.1-flash-lite"
    }
  ]
}
```

**`GET /api/admin/users`**
```json
{
  "users": [
    {
      "id": "user-uuid",
      "email": "user@example.com",
      "role": "free",
      "is_banned": false
    }
  ]
}
```

**`PATCH /api/admin/users/{user_id}/role`**
```json
{
  "message": "User role updated to 'admin'."
}
```

**`PATCH /api/admin/users/{user_id}/ban`**
```json
{
  "message": "User banned.",
  "is_banned": true
}
```

**`GET /api/admin/documents`**
```json
{
  "documents": [
    {
      "id": "doc-uuid",
      "source_doc_id": "111189",
      "title": "Civil Code",
      "is_active": true
    }
  ]
}
```

**`GET /api/admin/documents/{doc_id}`**
```json
{
  "document": {
    "id": "doc-uuid",
    "source_doc_id": "111189",
    "title": "Civil Code",
    "full_markdown": "Full text of the document..."
  }
}
```

**`POST /api/admin/ingest`**
```json
{
  "status": "success",
  "message": "Document ingested successfully.",
  "data": {
    "doc_id": "uuid",
    "chunks_created": 50
  }
}
```

### Health Endpoints
**`GET /api/health/`**
```json
{
  "status": "online",
  "message": "AdvoAI API is running"
}
```

**`GET /api/health/db`**
```json
{
  "status": "healthy",
  "message": "Database connection successful."
}
```
