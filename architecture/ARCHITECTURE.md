# Project Basira — Architecture & Process Flow

> **Basira** (بصيرة): "Insight" or "Inner Vision" — An AI-powered legal chatbot for Uzbekistan's legal system, built on RAG (Retrieval-Augmented Generation).

---

## 1. High-Level Overview

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
                        │  (w/ Caching)    │
                        └──────────────────┘
```

**Core Idea:** User asks a legal question → Backend retrieves relevant law chunks from the vector DB → Gemini generates a grounded answer citing specific articles/laws.

---

## 2. System Architecture (Detailed)

### 2.1 Frontend (Separate Repo)
| Component | Tech | Purpose |
|-----------|------|---------|
| Framework | **React** or **Next.js** | SPA / SSR chatbot UI |
| Styling | **Tailwind CSS** | Clean, responsive legal interface |
| State | **React Context / Zustand** | Chat history, session management |
| HTTP Client | **Axios / fetch** | REST calls to backend API |
| Auth (future) | **Clerk / NextAuth** | User accounts for marketplace |
| Deployment | **Vercel** | Free tier, auto-deploy from GitHub |

**Key Pages:**
- `/` — Landing page (what is Basira, features)
- `/chat` — Main chatbot interface
- `/chat/:id` — Saved conversation view
- `/marketplace` — (Phase 2) Legal services marketplace

### 2.2 Backend (Separate Repo)
| Component | Tech | Purpose |
|-----------|------|---------|
| Framework | **FastAPI** | Async Python API server |
| ORM/DB Driver | **psycopg2-binary + pgvector** | Direct PostgreSQL + vector ops |
| Embeddings | **BAAI/bge-m3** (HuggingFace) | Multilingual embeddings (Uzbek/Russian/English) |
| Chunking | **SemanticChunker** (LangChain) | Meaning-based text splitting |
| Reranker | **Cross-Encoder** (planned) | Quality control on retrieved chunks |
| LLM | **Gemini 2.5 Flash** (Google AI API) | Answer generation with context caching |
| Caching | **Gemini Context Caching** | Cache system prompt + legal context to cut costs |
| Scraper | **LexParser** (custom) | Lex.uz document ingestion |
| Deployment | **Railway / Render / Fly.io** | Python hosting with free tiers |

### 2.3 Database (Neon PostgreSQL)
| Table | Purpose |
|-------|---------|
| `documents` | Raw document metadata (doc_id, title, act_type, date, status, source_url) |
| `chunks` | Text chunks with vector embeddings (chunk_id, doc_id FK, text, embedding vector, metadata JSONB) |
| `conversations` | (Phase 2) Chat history per user session |
| `users` | (Phase 2) User accounts for marketplace |

**Why Neon?** Free tier, serverless PostgreSQL, native pgvector support, auto-scaling.

### 2.4 External Services
| Service | Purpose | Cost |
|---------|---------|------|
| **Google AI Studio** | Gemini 2.5 Flash API + Context Caching | Free tier (generous) |
| **Neon** | Managed PostgreSQL + pgvector | Free tier (512MB) |
| **Vercel** | Frontend hosting | Free tier |
| **Railway/Render** | Backend hosting | Free tier |
| **HuggingFace** | BGE-M3 model weights | Free (downloaded once) |

---

## 3. Data Pipeline (Ingestion Flow)

This is the **offline** pipeline that populates the vector database with Uzbek legal documents.

```
Step 1: SCRAPE                Step 2: PARSE               Step 3: CHUNK
┌──────────────┐         ┌──────────────────┐        ┌──────────────────┐
│  lex.uz URL  │────────▶│   LexParser      │───────▶│ SemanticChunker  │
│  (HTML doc)  │         │  + Unstructured  │        │  (BGE-M3 based)  │
└──────────────┘         │  + Metadata      │        └────────┬─────────┘
                         └──────────────────┘                 │
                                                              ▼
Step 4: EMBED                                    Step 5: STORE
┌──────────────────┐                         ┌──────────────────────┐
│   BGE-M3 Model   │◀───────────────────────│   Chunk texts        │
│ (1024-dim vectors)│                        └──────────────────────┘
└────────┬─────────┘
         │
         ▼
┌──────────────────────┐
│  Neon PostgreSQL     │
│  pgvector (HNSW)     │
│  + metadata (JSONB)  │
└──────────────────────┘
```

### Pipeline Details:

| Phase | Module | Input | Output | Notes |
|-------|--------|-------|--------|-------|
| **1. Scrape** | `lex_parser.py` | Lex.uz URL | Raw HTML | Custom headers, handles `?type=doc` format |
| **2. Parse** | `lex_parser.py` + `unstructured` | Raw HTML | Structured elements + metadata | Extracts doc_id, date, act_type, title, active status |
| **3. Clean** | `cleaner.py` | HTML elements | Clean text | Removes UI noise (audio buttons, suggestion links) |
| **4. Chunk** | `chunker.py` (SemanticChunker) | Full document text | Semantic chunks | BGE-M3 embeddings, percentile-based splitting (90th) |
| **5. Embed** | `embedder.py` (BGE-M3) | Chunk text | 1024-dim vectors | Normalized, multilingual |
| **6. Store** | `neon_client.py` | Vectors + metadata | PostgreSQL rows | pgvector HNSW index for fast ANN search |

---

## 4. Query Flow (Runtime / Chat)

This is the **online** pipeline that handles user questions in real-time.

```
┌─────────────────────────────────────────────────────────────────────┐
│                        USER ASKS A QUESTION                        │
│              "Mehnat kodeksida ishdan bo'shatish tartibi?"          │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌───────────────────────────────────────────────────────────────┐
│  Step 1: EMBED QUERY                                          │
│  BGE-M3 converts user question → 1024-dim vector              │
└───────────────────────────────┬───────────────────────────────┘
                                │
                                ▼
┌───────────────────────────────────────────────────────────────┐
│  Step 2: VECTOR SEARCH (pgvector)                             │
│  SELECT * FROM chunks ORDER BY embedding <=> query_vec        │
│  LIMIT 20                                                     │
│  (cosine similarity, HNSW index)                              │
└───────────────────────────────┬───────────────────────────────┘
                                │
                                ▼
┌───────────────────────────────────────────────────────────────┐
│  Step 3: RERANK (Cross-Encoder)                               │
│  Score each of 20 candidates against original question        │
│  Keep top 5-8 most relevant chunks                            │
└───────────────────────────────┬───────────────────────────────┘
                                │
                                ▼
┌───────────────────────────────────────────────────────────────┐
│  Step 4: GENERATE (Gemini 2.5 Flash)                          │
│                                                               │
│  System Prompt (CACHED):                                      │
│  "You are Basira, an Uzbek legal assistant..."                │
│  + Retrieved chunks as context                                │
│                                                               │
│  → Gemini generates answer citing specific articles           │
│  → Context caching reduces cost by ~90% on system prompt      │
└───────────────────────────────┬───────────────────────────────┘
                                │
                                ▼
┌───────────────────────────────────────────────────────────────┐
│  Step 5: RESPOND                                              │
│  Formatted answer with:                                       │
│  - Direct answer to the question                              │
│  - Cited articles/laws (with lex.uz links)                    │
│  - Disclaimer: "This is not legal advice"                     │
└───────────────────────────────────────────────────────────────┘
```

---

## 5. API Endpoints (Backend)

### Phase 1 (Chatbot MVP)
| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/chat` | Send message, get AI response |
| `GET` | `/api/health` | Health check |
| `POST` | `/api/ingest` | Trigger document ingestion (admin) |

### Phase 2 (Marketplace)
| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/auth/register` | User registration |
| `POST` | `/api/auth/login` | User login |
| `GET` | `/api/conversations` | List user's chat history |
| `GET` | `/api/conversations/:id` | Get specific conversation |
| `GET` | `/api/marketplace/lawyers` | Browse lawyers |
| `POST` | `/api/marketplace/consult` | Request consultation |

---

## 6. Cost Analysis (Free Tier Strategy)

| Service | Free Tier | Limit | Enough? |
|---------|-----------|-------|---------|
| Gemini 2.5 Flash | Free | 15 RPM, 1M TPM | ✅ For MVP |
| Gemini Context Caching | Free | Included | ✅ Massive savings |
| Neon PostgreSQL | Free | 512MB storage, 0.25 vCPU | ✅ ~5,000 docs |
| Vercel | Free | 100GB bandwidth | ✅ For MVP |
| Railway | Free | $5/month credit | ✅ For MVP |
| BGE-M3 | Free | Self-hosted | ✅ Always |

**Estimated Monthly Cost for MVP: $0**

---

## 7. Repo Structure (Proposed)

### Backend (`basira-backend/`)
```
basira-backend/
├── app/
│   ├── main.py              # FastAPI app entry
│   ├── config.py            # Settings (env vars)
│   ├── routes/
│   │   ├── chat.py          # POST /api/chat
│   │   ├── ingest.py        # POST /api/ingest (admin)
│   │   └── health.py        # GET /api/health
│   ├── services/
│   │   ├── rag_pipeline.py  # Orchestrates embed → search → rerank → generate
│   │   ├── embedder.py      # BGE-M3 wrapper
│   │   ├── reranker.py      # Cross-Encoder wrapper
│   │   └── llm_client.py    # Gemini API + caching
│   ├── ingestion/
│   │   ├── lex_parser.py    # Lex.uz scraper
│   │   ├── cleaner.py       # Text cleaning
│   │   └── chunker.py       # Semantic chunking
│   └── database/
│       ├── connection.py    # Neon connection pool
│       ├── schema.sql       # Table definitions
│       └── queries.py       # Vector search queries
├── data/
│   └── golden_dataset.csv   # Evaluation set
├── tests/
│   ├── test_parser.py
│   ├── test_chunker.py
│   └── test_rag.py
├── .env.example
├── requirements.txt
├── Dockerfile
└── README.md
```

### Frontend (`basira-frontend/`)
```
basira-frontend/
├── src/
│   ├── app/
│   │   ├── page.tsx         # Landing page
│   │   ├── chat/
│   │   │   └── page.tsx     # Chat interface
│   │   └── layout.tsx       # Root layout
│   ├── components/
│   │   ├── ChatWindow.tsx   # Message display area
│   │   ├── MessageBubble.tsx
│   │   ├── InputBar.tsx     # Text input + send
│   │   ├── SourceCard.tsx   # Shows cited law articles
│   │   └── Sidebar.tsx      # Chat history list
│   ├── hooks/
│   │   └── useChat.ts       # Chat logic hook
│   ├── lib/
│   │   └── api.ts           # Backend API client
│   └── styles/
│       └── globals.css      # Tailwind config
├── public/
│   └── basira-logo.svg
├── package.json
├── tailwind.config.ts
├── tsconfig.json
└── README.md
```

---

## 8. Development Phases

### Phase 1: MVP (Chatbot Only) — 4-6 weeks
| Week | Task |
|------|------|
| 1 | Finalize ingestion pipeline, scrape 50+ key laws from lex.uz |
| 2 | Set up Neon DB, implement vector search + HNSW indexing |
| 3 | Build RAG pipeline (embed → search → rerank → generate) |
| 4 | Create FastAPI endpoints, test with golden dataset |
| 5 | Build React/Next.js chat UI |
| 6 | Integration testing, deploy to Vercel + Railway |

### Phase 2: Marketplace — 4-6 weeks
| Week | Task |
|------|------|
| 7-8 | User auth (Clerk/NextAuth), conversation persistence |
| 9-10 | Lawyer profiles, consultation booking system |
| 11-12 | Payment integration, review system, polish |

---

## 9. Key Technical Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| **Embedding Model** | BGE-M3 | Best multilingual model, handles Uzbek+Russian+English natively |
| **Chunking** | Semantic (not fixed-size) | Legal docs have variable structure; semantic preserves meaning |
| **Vector DB** | pgvector (not Pinecone/Weaviate) | Free, SQL-native, no vendor lock-in, Neon hosting |
| **LLM** | Gemini 2.5 Flash | Cheap, fast, context caching slashes costs, good multilingual |
| **Reranker** | Cross-Encoder | Dramatically improves retrieval quality over pure vector search |
| **Context Caching** | Gemini native | Cache the system prompt + few-shot examples to pay ~90% less |
| **Separate Repos** | Frontend + Backend | Independent deployment, different tech stacks, cleaner CI/CD |

---

## 10. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Lex.uz blocks scraping | No data | Respectful rate limiting, cache responses locally |
| BGE-M3 too slow on CPU | Slow ingestion | Batch embed, use GPU for ingestion (MX330), cache embeddings |
| Gemini free tier limit | Service downtime | Context caching reduces token usage; fallback to cached responses |
| Legal accuracy concerns | Liability | Clear "not legal advice" disclaimers, cite sources always |
| Neon 512MB limit | DB full | Prioritize most-referenced laws first, compress metadata |

---

*Generated by Jarvis for Project Basira — February 27, 2026*
