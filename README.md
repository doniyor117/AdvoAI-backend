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

# ⚖️ AdvoAI Backend

**The intelligent backend powering the AdvoAI Legal Assistant for Uzbekistan.**

AdvoAI is a state-of-the-art **Agentic RAG (Retrieval-Augmented Generation)** system built with FastAPI, PostgreSQL (`pgvector`), and the Google GenAI SDK. It provides accurate, context-aware, and highly conversational legal guidance based exclusively on the official laws and codes of the Republic of Uzbekistan.

---

## 🎯 Purpose

AdvoAI is designed to bridge the gap between complex legal documents ("legalese") and everyday citizens. Instead of searching through hundreds of pages of the Civil Code or Tax Code, users can ask natural language questions. AdvoAI:
1. Understands the intent behind the query.
2. Formulates an optimal search strategy.
3. Retrieves the exact relevant legal articles.
4. Synthesizes a clear, accurate, and easy-to-understand answer.

---

## 🧠 How It Works (Agentic RAG Architecture)

Unlike standard RAG pipelines that blindly inject user queries into a vector database, AdvoAI utilizes a dual-model **Agentic RAG** approach.

### 1. The Router Agent (`gemma-4-31b-it`)
When a message arrives, it is intercepted by our powerful reasoning model (Gemma 4). The model acts as a query-optimization agent:
- **Intent Classification**: It determines if the query is `conversational` (e.g., "What is a constitution?", "Thank you") or if it requires deep document retrieval (`legal_rag`).
- **Query Formulation**: If RAG is required, the router does NOT pass the raw user question to the database. Instead, it extracts core legal concepts, synonyms, and keywords to formulate an *optimized semantic search query*.

### 2. The Retrieval Pipeline (`pgvector`)
- The optimized query is embedded using `gemini-embedding-2` (1536 dimensions).
- A cosine-similarity search (via HNSW indexing) is performed against PostgreSQL to find the most relevant chunks.
- **Mega-Chunking**: To provide maximum context, the system uses a Parent-Child retrieval pattern. It finds the best chunks but retrieves and feeds the *entire parent document* (or section) to the LLM, preventing context fragmentation.

### 3. The Generator (`gemini-3.1-flash-lite`)
- The retrieved parent documents, the optimized context, and the full multi-turn conversational history are fed into the main Gemini model.
- **Implicit Web Scraping**: If the user pastes a URL in their prompt, the backend automatically intercepts it, fetches the webpage concurrently, converts the HTML to Markdown via `MarkItDown`, and injects it into the LLM context along with properly formatted citations.
- The model generates a highly accurate, grounded, and conversational legal response based *only* on the provided context and uploaded attachments.

### 4. Hybrid Sliding Window History
To maintain context over long conversations without blowing up token limits:
- The last 6 messages are kept raw for immediate multi-turn context.
- Older messages are periodically pushed into a background worker (`FastAPI BackgroundTasks`), where `gemma-4` seamlessly integrates them into a running **Archive Summary**.

### 5. Dynamic Admin Dashboard & Advanced Control
The AdvoAI Admin panel empowers administrators to tune the AI and manage the system at scale:
- **No-Code Prompt Injection**: Dynamically override the System Prompts for both the Router and Main LLM directly from the database.
- **Global UI Copy Control**: Manage frontend UI elements (Welcome titles, Support Emails, Footer text) completely from the backend without editing React code.
- **RAG Playground**: A dedicated endpoint to query the `pgvector` database directly and diagnose semantic search quality, bypassing the LLM layer.
- **Bulk Data Ingestion & Categorization**: An asynchronous background processing system that allows admins to paste dozens of URLs and ingest laws into specific Categories/Folders seamlessly without blocking the UI.
- **Session Auditing & Privacy**: Admins can securely review user chat transcripts to ensure quality. A robust privacy toggle (`allow_data_collection`) guarantees that opted-out users' data is strictly redacted.
- **Admin Password Security**: A secondary authentication layer (Admin Lock Screen) protects sensitive dashboard routes, ensuring that even if an account is compromised, the admin panel remains secure.

---

## 🚀 Quick Start & Deployment

### Prerequisites
- Python 3.11+
- PostgreSQL 15+ with `pgvector` extension installed.
- A Google Gemini API Key.

### 1. Environment Setup

```bash
# Create a virtual environment
conda create -n advoai_backend python=3.12 -y
conda activate advoai_backend

# Install dependencies
pip install -r requirements.txt
```

### 2. Configuration

Copy the example environment file and fill in your details:
```bash
cp .env.example .env
```

Ensure your `.env` contains:
```env
DATABASE_URL="postgresql://user:password@localhost:5432/advoai"
GOOGLE_API_KEY="your_google_genai_key"
JWT_SECRET_KEY="your_super_secret_key"
```

### 3. Database Initialization

Execute the unified schema script to set up your tables and vector indexes:
```bash
psql "$DATABASE_URL" -f app/database/schema_unified.sql
```

### 4. Running the Server

Start the FastAPI application:
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
- API is live at `http://localhost:8000`
- Swagger UI (Interactive API Docs) at `http://localhost:8000/docs`

---

## 🏗️ Project Structure

```text
app/
├── main.py                  # FastAPI app + CORS + route mounting
├── config.py                # Pydantic settings loaded from .env
├── middleware.py             # JWT auth, rate limiting
├── database/
│   ├── connection.py         # Thread-safe PostgreSQL connection pool (psycopg2)
│   ├── queries.py            # Centralized SQL query execution
│   └── schema_unified.sql    # Complete DB schema (Idempotent)
├── ingestion/
│   ├── lex_parser.py         # Lex.uz HTML → structured Markdown pipeline
│   ├── chunker.py            # Semantic Mega-Chunking logic
│   └── main_ingest.py        # End-to-end ingestion orchestrator
├── routes/
│   ├── auth.py               # Registration, Login, Google OAuth
│   ├── chat.py               # 💬 Core RAG Chat Endpoint
│   ├── sessions.py           # Chat history management
│   └── admin.py              # System configuration and document management
└── services/
    ├── embedder.py            # Text embedding via Gemini
    ├── llm_client.py          # 🧠 Agentic Routing, Generation, and Retries
    ├── prompts.py             # System Prompts & Agent Instructions
    └── rag_pipeline.py        # pgvector Semantic Search Pipeline

tests/                         # Comprehensive Pytest Suite
```

---

## 🛡️ Robustness & Testing

- **Resilience**: All external LLM calls are wrapped in exponential backoff retry loops, ensuring stability even during network hiccups or rate limits.
- **Background Processing**: Heavy tasks like history summarization and chat title generation run asynchronously, guaranteeing instant API response times.
- **Testing**: A full suite of unit and integration tests (via `pytest` and `FastAPI TestClient`) ensures the stability of the routing engine, chunking logic, and REST endpoints. Run tests with: `python3 -m pytest tests/ -v`.