-- ============================================================
-- Yurika — Schema V2: Auth, Sessions, Usage Tracking
-- Extends existing documents + chunks tables (schema.sql)
-- Run AFTER schema.sql has been applied.
-- ============================================================

BEGIN;

-- 1. Users table
CREATE TABLE IF NOT EXISTS public.users (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email           varchar(255) UNIQUE NOT NULL,
    password_hash   text,                                    -- NULL for OAuth-only users
    full_name       varchar(255),
    role            varchar(20) NOT NULL DEFAULT 'free'
                    CHECK (role IN ('guest', 'free', 'admin')),
    auth_provider   varchar(20) NOT NULL DEFAULT 'email'
                    CHECK (auth_provider IN ('email', 'google')),
    google_id       varchar(255) UNIQUE,                     -- For Google OAuth linking
    email_verified  boolean DEFAULT FALSE,
    is_active       boolean DEFAULT TRUE,
    created_at      timestamptz DEFAULT NOW(),
    last_login_at   timestamptz
);

-- 2. Chat sessions table (linked to users)
CREATE TABLE IF NOT EXISTS public.chat_sessions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    title           varchar(255) DEFAULT 'New Chat',
    rolling_summary text DEFAULT '',
    is_pinned       boolean DEFAULT FALSE,
    created_at      timestamptz DEFAULT NOW(),
    updated_at      timestamptz DEFAULT NOW()
);

-- 3. Usage tracking for registered users (daily message count)
CREATE TABLE IF NOT EXISTS public.usage_logs (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    usage_date      date NOT NULL DEFAULT CURRENT_DATE,
    message_count   integer NOT NULL DEFAULT 0,
    UNIQUE (user_id, usage_date)
);

-- 4. Guest usage tracking (by browser fingerprint via FingerprintJS)
CREATE TABLE IF NOT EXISTS public.guest_usage (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    fingerprint     varchar(64) NOT NULL UNIQUE,
    message_count   integer NOT NULL DEFAULT 0,
    first_seen_at   timestamptz DEFAULT NOW(),
    last_seen_at    timestamptz DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON public.chat_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON public.chat_sessions(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_usage_logs_user_date ON public.usage_logs(user_id, usage_date);
CREATE INDEX IF NOT EXISTS idx_users_email ON public.users(email);
CREATE INDEX IF NOT EXISTS idx_users_google_id ON public.users(google_id);

COMMIT;
