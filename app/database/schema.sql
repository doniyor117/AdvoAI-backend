-- ============================================================
-- Project Basira — Database Schema
-- PostgreSQL + pgvector (Neon)
-- ============================================================

-- Enable pgvector extension (run once on the database)
CREATE EXTENSION IF NOT EXISTS vector;

-- ──────────────────────────────────────────────────────────────
-- Parent documents table
-- Stores the full Markdown representation of each legal document.
-- This is what gets sent to Gemini as LLM context.
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS documents (
    id              UUID PRIMARY KEY,               -- UUID4 generated in Python
    source_doc_id   VARCHAR(64) UNIQUE NOT NULL,     -- Lex.uz doc number (e.g. '111189'), for dedup
    title           TEXT NOT NULL DEFAULT 'Untitled Document',
    metadata        JSONB DEFAULT '{}',              -- act_type, date, source_url, etc.
    full_markdown   TEXT NOT NULL,                    -- Entire document in Markdown (sent to LLM)
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Index on source_doc_id for fast dedup lookups
CREATE INDEX IF NOT EXISTS idx_documents_source_doc_id 
    ON documents(source_doc_id);

-- ──────────────────────────────────────────────────────────────
-- Search chunks table (vector index)
-- Small semantically-grouped chunks used ONLY for vector search.
-- Each chunk links back to its parent document via FK.
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chunks (
    id              UUID PRIMARY KEY,               -- UUID4 generated in Python
    parent_id       UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    text            TEXT NOT NULL,                    -- Chunk text (for embedding)
    embedding       vector(1024),                    -- BGE-M3 embedding (1024 dims)
    chunk_metadata  JSONB DEFAULT '{}',              -- unstructured metadata
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- FK index for efficient parent lookups
CREATE INDEX IF NOT EXISTS idx_chunks_parent_id 
    ON chunks(parent_id);

-- HNSW index for fast approximate nearest neighbor search
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw 
    ON chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
