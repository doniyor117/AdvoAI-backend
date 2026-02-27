# Basira AI - Legal Chatbot with RAG

```text
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

---

## Data Pipeline (Ingestion Flow)

This is the **offline** pipeline that populates the vector database with Uzbek legal documents.

```text
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

###  Backend (Separate Repo)
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

###  Database (Neon PostgreSQL)
| Table | Purpose |
|-------|---------|
| `documents` | Raw document metadata (doc_id, title, act_type, date, status, source_url) |
| `chunks` | Text chunks with vector embeddings (chunk_id, doc_id FK, text, embedding vector, metadata JSONB) |
| `conversations` | (Phase 2) Chat history per user session |
| `users` | (Phase 2) User accounts for marketplace |

**Why Neon?** Free tier, serverless PostgreSQL, native pgvector support, auto-scaling.

###  External Services
| Service | Purpose | Cost |
|---------|---------|------|
| **Google AI Studio** | Gemini 2.5 Flash API + Context Caching | Free tier (generous) |
| **Neon** | Managed PostgreSQL + pgvector | Free tier (512MB) |
| **Vercel** | Frontend hosting | Free tier |
| **Railway/Render** | Backend hosting | Free tier |
| **HuggingFace** | BGE-M3 model weights | Free (downloaded once) |

---
## Basira: (Arabic) "Insight" or "Inner Vision."
