# AdvoAI Backend Codebase Audit

This document serves as a comprehensive technical audit of the current state of the AdvoAI backend, highlighting recent architectural upgrades, design patterns, security measures, and areas for potential future improvement.

---

## 1. Architectural Highlights

### Agentic RAG Implementation
The core chat pipeline has been successfully upgraded to an "Agentic RAG" pattern.
- **Router Agent (`llm_client.py: route_query`)**: Uses `gemma-4-31b-it` to classify user intent. If `legal_rag` is triggered, the model dynamically generates an optimized semantic search query rather than relying on the raw user input.
- **Strict Guardrails (`prompts.py`)**: General knowledge questions and basic definitions (e.g., "What is a constitution?") are strictly flagged as `conversational` to prevent wasteful vector database searches. Tool calling is strictly forbidden via prompting, forcing the model to rely solely on structured JSON outputs.

### High-Performance Asynchronous Offloading
Heavy LLM operations have been successfully decoupled from the HTTP response cycle.
- **Background Tasks (`chat.py`)**: Both chat session title generation and the sliding-window archive summarization are pushed to `FastAPI BackgroundTasks`. This allows the API to return answers instantly, improving the UX significantly.

### Robust LLM Communication
- **Exponential Backoff (`llm_client.py: _generate_with_retry`)**: A custom retry loop using exponential backoff (1s, 2s, 4s) ensures that the system gracefully recovers from intermittent network failures or LLM API rate limits.

---

## 2. Core Modules Review

### `app/database/`
- **`connection.py`**: Properly utilizes `psycopg2.pool.ThreadedConnectionPool` configured during the FastAPI lifespan. This thread-safe implementation allows background tasks to safely query the database concurrently without exhausting connection limits.
- **`queries.py` & `schema_unified.sql`**: Schema is idempotent. `pgvector` indexing relies on HNSW, which provides high recall and scale.

### `app/services/`
- **`llm_client.py`**: The `GeminiClient` is clean and adheres to the Single Responsibility Principle. It isolates all GenAI SDK logic from the API layer.
- **`rag_pipeline.py`**: Implements Parent-Child chunk retrieval (Mega-chunking), effectively bypassing context fragmentation issues commonly found in standard vector setups.

### `app/routes/`
- **`chat.py`**: The main endpoint `/api/chat/` is highly efficient. It manages session context cleanly and correctly isolates non-essential operations to background threads. Dependency injection is used appropriately for rate limiters.

### `tests/`
- **Test Coverage**: 12 comprehensive unit and integration tests written using `pytest`.
- **Mocks**: External APIs (like the GenAI SDK) are securely mocked using `unittest.mock`, validating that the routing logic, dictionary returns, and fallback mechanisms perform perfectly under varied conditions.

---

## 3. Security and Stability Assessment

- **Authentication / Authorization**: Handled cleanly in `middleware.py` utilizing secure JWTs.
- **Error Handling**: The `_generate_with_retry` function captures generic exceptions and logs effectively. 
- **Database Thread Safety**: Since `BackgroundTasks` run in the same process via `anyio`, thread pooling is correctly managed by `psycopg2.pool.ThreadedConnectionPool`. Tests confirmed there are no connection leaks.

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

## 5. Future Recommendations / Next Steps

1. **Async DB Drivers**: Currently, the application uses `psycopg2` (synchronous) combined with FastAPI's `def` routes, which delegates blocking calls to a thread pool. Upgrading to `asyncpg` with `SQLAlchemy 2.0` could increase throughput under extremely high concurrency.
2. **Dynamic Top-K Expansion**: The RAG pipeline currently accepts a static `top_k`. The Router Agent could be modified to also output an "ideal `top_k`" based on the complexity of the legal query it formulates.
3. **Analytics Logging**: Consider storing the "Optimized Search Queries" generated by Gemma 4 alongside the raw user queries in the database to allow admins to evaluate router performance over time.
