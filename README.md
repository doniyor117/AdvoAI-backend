# **Project Basira — Architecture & Process Flow**

**Basira** (بصيرة): "Insight" or "Inner Vision" — An AI-powered legal chatbot for Uzbekistan's legal system, built on a Hybrid Parent-Child RAG (Retrieval-Augmented Generation) architecture.

## **1. High-Level Overview**

```
User Question (Uzbek/Russian)
        │
        ▼
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────────┐
│   Frontend      │────▶│   Backend API    │────▶│   Neon PostgreSQL   │
│   (React/Next)  │◀────│   (FastAPI)      │◀────│   + pgvector        │
└─────────────────┘     └──────────────────┘     └─────────────────────┘
                              │      ▲
                              ▼      │
                        ┌──────────────────┐
                        │  Google AI API   │
                        │  Gemini 2.5 Flash│
                        │  (1M Context)    │
                        └──────────────────┘
```

**Core Idea:** User asks a legal question → Backend searches small chunks in the vector DB to find a match → Backend retrieves the **ENTIRE Markdown document** linked to that chunk → Gemini generates a grounded answer using the full document's context.

## **2. System Architecture (Detailed)**

### **2.1 Frontend (Separate Repo)**

| Component | Tech | Purpose |
| :--- | :--- | :--- |
| Framework | **React** or **Next.js** | SPA / SSR chatbot UI |
| Styling | **Tailwind CSS** | Clean, responsive legal interface |
| State | **React Context / Zustand** | Chat history, session management |
| HTTP Client | **Axios / fetch** | REST calls to backend API |
| Auth (future) | **Clerk / NextAuth** | User accounts for marketplace |
| Deployment | **Vercel** | Free tier, auto-deploy from GitHub |

### **2.2 Backend (Separate Repo)**

| Component | Tech | Purpose |
| :--- | :--- | :--- |
| Framework | **FastAPI** | Async Python API server |
| ORM/DB Driver | **psycopg2-binary + pgvector** | Direct PostgreSQL + vector ops |
| Embeddings | **BAAI/bge-m3** (HuggingFace) | Multilingual embeddings (Uzbek/Russian/English) |
| Document Parser | **MarkItDown** (Microsoft) | Converts HTML to LLM-friendly Markdown |
| Chunking | **unstructured** | Semantically chunks text for search indexing |
| Reranker | **BAAI/bge-reranker-v2-m3** | Config-driven toggle for high precision vs high speed |
| LLM | **Gemini 2.5 Flash** (Google AI API) | Answer generation using large context windows |
| Deployment | **Hugging Face Spaces / Railway** | Python hosting with free tiers |

### **2.3 Database (Neon PostgreSQL)**

| Table | Purpose |
| :--- | :--- |
| `documents` | Parent documents (id UUID PK, source_doc_id, title, metadata JSONB, **full_markdown**) |
| `chunks` | Search index (id UUID PK, **parent_id FK**, text, embedding vector) |
| `conversations` | (Phase 2) Chat history per user session |
| `users` | (Phase 2) User accounts for marketplace |

**Why Neon?** Free tier, serverless PostgreSQL, native pgvector support, auto-scaling.

## **3. Data Pipeline (Ingestion Flow)**

This is the **offline** pipeline that populates the database using the "Small-to-Big" approach.

```
Step 1: SCRAPE & CLEAN                        Step 2: SPLIT PATHS (Parent/Child)
┌──────────────┐                        ┌────────────────────────────────────────┐
│  lex.uz URL  │────▶ LexParser ───────▶│ MarkItDown ───▶ Full Markdown Document │
│  (HTML doc)  │      (Clean HTML)      │ unstructured ─▶ Search Chunks          │
└──────────────┘                        └────────────────────────────────────────┘
                                                               │
                                                               ▼
Step 4: STORE                                Step 3: EMBED CHUNKS
┌────────────────────────┐                   ┌────────────────────────┐
│ Neon DB                │                   │      BGE-M3 Model      │
│ 1. `documents` (MD)    │◀──────────────────│  (1024-dim vectors)    │
│ 2. `chunks` (Vectors)  │                   └────────────────────────┘
└────────────────────────┘
```

### **Pipeline Details:**

| Phase | Module | Action | Output |
| :--- | :--- | :--- | :--- |
| **1. Parse** | `lex_parser.py` | Scrapes Lex.uz, cleans UI noise, extracts metadata, runs MarkItDown. | metadata, full_markdown |
| **2. Chunk** | `chunker.py` | Uses unstructured (`chunk_by_title`) on cleaned HTML. | List of chunks with parent_doc_id. |
| **3. Orchestrate** | `main_ingest.py` | Ties parser and chunker together, manages relations. | Parent Document & Child Chunks |
| **4. Embed** | `embedder.py` | Generates BGE-M3 vectors for the small chunks only. | 1024-dim vectors |
| **5. Store** | `database/` | Saves full MD to `documents`, saves vectors to `chunks`. | Indexed PostgreSQL rows |

## **4. Query Flow (Runtime / Chat)**

This is the **online** pipeline that handles user questions in real-time, utilizing Parent-Child retrieval.

```
┌─────────────────────────────────────────────────────────────────────┐
│                        USER ASKS A QUESTION                        │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌───────────────────────────────────────────────────────────────┐
│  Step 1: VECTOR SEARCH & RERANK (The "Small" Chunks)          │
│  Embed query ─▶ SELECT parent_id FROM chunks ORDER BY <=>     │
│  (Optional: Rerank top 15 chunks using BGE Cross-Encoder)     │
└───────────────────────────────┬───────────────────────────────┘
                                │
                                ▼
┌───────────────────────────────────────────────────────────────┐
│  Step 2: FETCH PARENT (The "Big" Window)                      │
│  SELECT full_markdown FROM documents                          │
│  WHERE id IN (retrieved_parent_ids)                           │
└───────────────────────────────┬───────────────────────────────┘
                                │
                                ▼
┌───────────────────────────────────────────────────────────────┐
│  Step 3: GENERATE (Gemini 2.5 Flash)                          │
│  Send System Prompt + User Query + ENTIRE Markdown Document   │
│  Gemini sees tables, lists, and context perfectly intact      │
└───────────────────────────────┬───────────────────────────────┘
                                │
                                ▼
┌───────────────────────────────────────────────────────────────┐
│  Step 4: RESPOND                                              │
│  Formatted answer citing specific articles with Lex.uz links  │
└───────────────────────────────────────────────────────────────┘
```

## **5. API Endpoints (Backend)**

### **Phase 1 (Chatbot MVP)**

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| POST | `/api/chat` | Send message, get AI response |
| GET | `/api/health` | Health check |
| POST | `/api/ingest` | Trigger document ingestion via `main_ingest.py` |

### **Phase 2 (Marketplace)**

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| POST | `/api/auth/register` | User registration |
| POST | `/api/auth/login` | User login |
| GET | `/api/conversations` | List user's chat history |
| GET | `/api/marketplace/lawyers` | Browse lawyers |

## **6. Cost Analysis (Free Tier Strategy)**

| Service | Free Tier | Limit | Enough? |
| :--- | :--- | :--- | :--- |
| Gemini 2.5 Flash | Free | 15 RPM, 1M TPM | ✅ For MVP |
| Neon PostgreSQL | Free | 512MB storage | ✅ Efficient (only small chunks embedded) |
| Vercel | Free | 100GB bandwidth | ✅ For MVP |
| Railway | Free | $5/month credit | ✅ For MVP |
| BGE-M3 | Free | Self-hosted | ✅ Always |

## **7. Repo Structure (Proposed)**

### **Backend (basira-backend/)**

```
basira-backend/
├── app/
│   ├── main.py              # FastAPI app entry
│   ├── config.py            # Settings (env vars)
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── chat.py
│   │   ├── ingest.py
│   │   └── health.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── rag_pipeline.py  # Orchestrates embed → search → fetch parent → generate
│   │   ├── embedder.py      # BGE-M3 wrapper
│   │   ├── reranker.py      # Cross-Encoder wrapper
│   │   └── llm_client.py    # Gemini API
│   ├── ingestion/
│   │   ├── __init__.py
│   │   ├── lex_parser.py    # Scraper + MarkItDown converter
│   │   ├── chunker.py       # Unstructured search index builder
│   │   └── main_ingest.py   # Ingestion pipeline orchestrator
│   └── database/
│       ├── __init__.py
│       ├── connection.py    # Neon connection pool
│       ├── schema.sql       # Table definitions (documents, chunks)
│       └── queries.py       # Vector & relational queries
├── data/
│   └── golden_dataset.csv   # Evaluation set
├── tests/
├── .env.example
├── requirements.txt
└── README.md
```

## **7.5 Deployment (Hugging Face Spaces)**

The easiest and most cost-effective way to run `basira-backend` is on a Hugging Face Space (Docker).

1. Create a new Space on [Hugging Face](https://huggingface.co/spaces) and select **Docker** as the environment.
2. Push your repository to the Space.
3. Go to the Space's **Settings > Variables and secrets** and define:
   - `DATABASE_URL` (Your Neon connection string)
   - `GOOGLE_API_KEY` (Your Gemini API key)
   - `USE_RERANKER` (`True` or `False`)
4. The Space will automatically build the `Dockerfile`, bake the BGE models into the image so they are cached, and boot the FastAPI server on port 7860.

## **8. Development Phases**

### **Phase 1: MVP (Chatbot Only) — 4-6 weeks**

| Week | Task |
| :--- | :--- |
| 1 | Finalize `main_ingest.py` pipeline (MarkItDown + Unstructured) |
| 2 | Set up Neon DB, implement vector search + HNSW indexing |
| 3 | Build Parent-Child RAG pipeline (search chunk → fetch MD → generate) |
| 4 | Create FastAPI endpoints, test with golden dataset |
| 5 | Build React/Next.js chat UI |
| 6 | Integration testing, deploy to Vercel + Railway |

### **Phase 2: Marketplace — 4-6 weeks**

| Week | Task |
| :--- | :--- |
| 7-8 | User auth (Clerk/NextAuth), conversation persistence |
| 9-10 | Lawyer profiles, consultation booking system |

## **9. Key Technical Decisions**

| Decision | Choice | Why |
| :--- | :--- | :--- |
| **RAG Strategy** | Parent-Child (Small-to-Big) | Solves the "lost context" problem. Small chunks for precise searching; full Markdown documents for perfect LLM context. |
| **Document Parsing** | MarkItDown (Microsoft) | Converts complex HTML tables/lists into perfect Markdown that Gemini natively understands. |
| **Embedding Model** | BGE-M3 | Best multilingual model, handles Uzbek+Russian+English natively. |
| **Reranker** | BGE-Reranker-v2-m3 | Config controlled (`USE_RERANKER`). Toggle on for max precision, off for max speed on CPU. |
| **Chunking** | Unstructured (`chunk_by_title`) | Groups logical HTML elements together perfectly for the search index. |
| **Vector DB** | pgvector on Neon | Free, SQL-native. Allows storing vectors (chunks) and raw text (documents) in the same database. |
| **LLM** | Gemini 2.5 Flash | Massive 1M+ token window allows feeding entire legal acts safely and cheaply. |

## **10. Risks & Mitigations**

| Risk | Impact | Mitigation |
| :--- | :--- | :--- |
| Lex.uz blocks scraping | No data | Respectful rate limiting, cache responses locally. |
| TPM Limits | Rate Limiting | Monitor Gemini 1M TPM limit if sending massive civil codes repeatedly. |
| Legal accuracy concerns | Liability | Clear "not legal advice" disclaimers, cite sources always. |
| Neon 512MB limit | DB full | Small-to-Big RAG actually saves space! (Only chunking what's needed for search, not generating overlapping window chunks). |

*Updated for Project Basira Architecture V2 — February 2026*