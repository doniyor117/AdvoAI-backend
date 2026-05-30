-- ============================================================
-- AdvoAI — Comprehensive Database Schema
-- PostgreSQL + pgvector (Neon)
-- Safe to run multiple times (idempotent).
-- ============================================================

-- 1. Create the vector extension BEFORE the transaction block
CREATE EXTENSION IF NOT EXISTS vector;

BEGIN;

-- ──────────────────────────────────────────────────────────────
-- V1: Documents, Parts, & Vector Chunks (Hierarchy)
-- ──────────────────────────────────────────────────────────────

-- Root documents table (Metadata only)
CREATE TABLE IF NOT EXISTS public.documents
(
    id              uuid NOT NULL,
    source_doc_id   character varying(64) NOT NULL UNIQUE,   -- From URL (e.g. '111189'), for dedup
    title           text DEFAULT 'Unknown',
    act_type        character varying(50) DEFAULT 'Unknown',
    doc_date        date DEFAULT NULL,
    source_url      text NOT NULL,
    category        varchar(50) DEFAULT 'General',
    is_active       boolean DEFAULT TRUE,
    created_at      timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
);

-- Mega-chunks / Child Parents table (Passed to LLM)
CREATE TABLE IF NOT EXISTS public.document_parts
(
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     uuid NOT NULL REFERENCES public.documents(id) ON DELETE CASCADE,
    part_title      text NOT NULL,
    text            text NOT NULL,
    part_index      integer NOT NULL DEFAULT 0,
    created_at      timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

-- Search chunks table (vector index)
CREATE TABLE IF NOT EXISTS public.chunks
(
    id                uuid NOT NULL,
    document_part_id  uuid NOT NULL REFERENCES public.document_parts(id) ON DELETE CASCADE,
    text              text NOT NULL,
    embedding         vector(1536),
    chunk_metadata    jsonb,
    created_at        timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS idx_document_parts_doc_id 
    ON public.document_parts(document_id);

CREATE INDEX IF NOT EXISTS idx_chunks_embedding 
    ON public.chunks USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_chunks_part_id 
    ON public.chunks(document_part_id);


-- ──────────────────────────────────────────────────────────────
-- V2: Users, Sessions, Chat History, and Usage Logs
-- ──────────────────────────────────────────────────────────────

-- Users table
CREATE TABLE IF NOT EXISTS public.users (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email           varchar(255) UNIQUE NOT NULL,
    password_hash   text,
    full_name       varchar(255),
    role            varchar(20) NOT NULL DEFAULT 'free'
                    CHECK (role IN ('guest', 'free', 'admin')),
    auth_provider   varchar(20) NOT NULL DEFAULT 'email'
                    CHECK (auth_provider IN ('email', 'google')),
    google_id       varchar(255) UNIQUE,
    email_verified  boolean DEFAULT FALSE,
    is_active       boolean DEFAULT TRUE,
    admin_password_hash varchar(255),
    created_at      timestamptz DEFAULT NOW(),
    last_login_at   timestamptz
);

-- OTP / Verification Codes table
CREATE TABLE IF NOT EXISTS public.verification_codes (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid REFERENCES public.users(id) ON DELETE CASCADE, -- NULL if pre-registration
    email           varchar(255) NOT NULL,
    code            varchar(10) NOT NULL,
    type            varchar(20) NOT NULL CHECK (type IN ('registration', 'password_reset', 'email_change')),
    expires_at      timestamptz NOT NULL,
    created_at      timestamptz DEFAULT NOW()
);

-- Chat sessions table (contains Archive Summary)
CREATE TABLE IF NOT EXISTS public.chat_sessions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    title           varchar(255) DEFAULT 'New Chat',
    session_summary text DEFAULT '',
    is_pinned       boolean DEFAULT FALSE,
    created_at      timestamptz DEFAULT NOW(),
    updated_at      timestamptz DEFAULT NOW()
);

-- Chat messages table (for Sliding Window)
CREATE TABLE IF NOT EXISTS public.chat_messages (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      uuid NOT NULL REFERENCES public.chat_sessions(id) ON DELETE CASCADE,
    role            varchar(20) NOT NULL CHECK (role IN ('user', 'assistant')),
    content         text NOT NULL,
    created_at      timestamptz DEFAULT NOW()
);

-- Ingestion Jobs table (for background batch ingestion)
CREATE TABLE IF NOT EXISTS public.ingestion_jobs (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    url           text NOT NULL,
    status        varchar(20) NOT NULL DEFAULT 'pending', -- pending, processing, completed, failed
    error_message text,
    created_at    timestamptz DEFAULT NOW(),
    updated_at    timestamptz DEFAULT NOW()
);

-- Usage tracking
CREATE TABLE IF NOT EXISTS public.usage_logs (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    usage_date      date NOT NULL DEFAULT CURRENT_DATE,
    message_count   integer NOT NULL DEFAULT 0,
    doc_upload_count integer NOT NULL DEFAULT 0,
    image_upload_count integer NOT NULL DEFAULT 0,
    UNIQUE (user_id, usage_date)
);

-- Guest usage tracking
CREATE TABLE IF NOT EXISTS public.guest_usage (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    fingerprint     varchar(64) NOT NULL UNIQUE,
    message_count   integer NOT NULL DEFAULT 0,
    doc_upload_count integer NOT NULL DEFAULT 0,
    image_upload_count integer NOT NULL DEFAULT 0,
    first_seen_at   timestamptz DEFAULT NOW(),
    last_seen_at    timestamptz DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON public.chat_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON public.chat_sessions(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_session_id ON public.chat_messages(session_id);
CREATE INDEX IF NOT EXISTS idx_usage_logs_user_date ON public.usage_logs(user_id, usage_date);
CREATE INDEX IF NOT EXISTS idx_users_email ON public.users(email);
CREATE INDEX IF NOT EXISTS idx_users_google_id ON public.users(google_id);

-- ──────────────────────────────────────────────────────────────
-- V3: Admin Settings and Modifications
-- ──────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.system_settings (
    key    VARCHAR(64) PRIMARY KEY,
    value  TEXT NOT NULL
);

INSERT INTO system_settings (key, value) VALUES
    ('current_llm_model', 'gemini-3.1-flash-lite'),
    ('guest_message_limit', '3'),
    ('free_daily_limit', '20'),
    ('free_daily_doc_limit', '10'),
    ('free_daily_image_limit', '10'),
    ('guest_doc_limit', '2'),
    ('guest_image_limit', '2'),
    ('ui_welcome_title', 'Welcome to AdvoAI'),
    ('ui_welcome_message', 'How can I assist you with Uzbekistan law today?'),
    ('ui_support_email', 'support@advoai.uz'),
    ('ui_footer_text', 'AdvoAI provides AI-generated guidance. Please consult a human lawyer for official advice.')
ON CONFLICT (key) DO NOTHING;

-- Safely add is_banned column to users (idempotent)
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS is_banned BOOLEAN DEFAULT FALSE;

-- ──────────────────────────────────────────────────────────────
-- V4: Analytics & Logging
-- ──────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.router_analytics (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      uuid REFERENCES public.chat_sessions(id) ON DELETE CASCADE,
    raw_query       text NOT NULL,
    intent          varchar(50) NOT NULL,
    optimized_query text,
    ideal_top_k     integer,
    created_at      timestamptz DEFAULT NOW()
);

-- V5: Missing columns and Performance Tracking (safe) -- User Settings & Privacy
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS allow_data_collection BOOLEAN DEFAULT TRUE;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS terms_accepted_at TIMESTAMPTZ;
ALTER TABLE public.chat_messages ADD COLUMN IF NOT EXISTS sources JSONB DEFAULT '[]'::jsonb;
ALTER TABLE public.router_analytics ADD COLUMN IF NOT EXISTS router_time_ms INTEGER;
ALTER TABLE public.router_analytics ADD COLUMN IF NOT EXISTS llm_time_ms INTEGER;

COMMIT;
