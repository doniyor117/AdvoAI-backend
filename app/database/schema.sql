-- ============================================================
-- Project Basira — Database Schema
-- PostgreSQL + pgvector (Neon)
-- ============================================================

-- 1. Create the vector extension BEFORE the transaction block
CREATE EXTENSION IF NOT EXISTS vector;

BEGIN;

-- ──────────────────────────────────────────────────────────────
-- Parent documents table
-- Stores the full Markdown representation of each legal document.
-- This is what gets sent to Gemini as LLM context.
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.documents
(
    id              uuid NOT NULL,
    source_doc_id   character varying(64) NOT NULL UNIQUE,   -- From URL (e.g. '111189'), for dedup
    title           text DEFAULT 'Unknown',
    act_type        character varying(50) DEFAULT 'Unknown',
    doc_date        date DEFAULT NULL,
    source_url      text NOT NULL,
    is_active       boolean DEFAULT TRUE,
    full_markdown   text NOT NULL,
    created_at      timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
);

-- ──────────────────────────────────────────────────────────────
-- Search chunks table (vector index)
-- Small semantically-grouped chunks used ONLY for vector search.
-- Each chunk links back to its parent document via FK.
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.chunks
(
    id              uuid NOT NULL,
    parent_id       uuid NOT NULL,                           -- FK → documents.id
    text            text NOT NULL,
    embedding       vector(1024),                            -- Nullable: inserted before embedding
    chunk_metadata  jsonb,
    created_at      timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
);

-- 2. Add the Foreign Key with CASCADE so child chunks delete when parents are deleted
ALTER TABLE IF EXISTS public.chunks
    ADD CONSTRAINT fk_parent_doc
    FOREIGN KEY (parent_id)
    REFERENCES public.documents (id) MATCH SIMPLE
    ON UPDATE CASCADE
    ON DELETE CASCADE;

-- 3. Add the HNSW index crucial for fast vector searching
CREATE INDEX IF NOT EXISTS idx_chunks_embedding 
    ON public.chunks USING hnsw (embedding vector_cosine_ops);

-- 4. Add standard index on the foreign key for fast relational lookups
CREATE INDEX IF NOT EXISTS idx_chunks_parent_id 
    ON public.chunks(parent_id);

COMMIT;
