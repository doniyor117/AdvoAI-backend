-- ============================================================
-- Yurika — Schema V3: System Settings + User Ban
-- Run AFTER schema_v2.sql has been applied.
-- ============================================================

BEGIN;

-- 1. System settings table (key-value store for admin-configurable values)
CREATE TABLE IF NOT EXISTS public.system_settings (
    key    VARCHAR(64) PRIMARY KEY,
    value  TEXT NOT NULL
);

-- Seed defaults (only if not already present)
INSERT INTO system_settings (key, value) VALUES
    ('current_llm_model', 'gemini-2.5-flash'),
    ('guest_message_limit', '3'),
    ('free_daily_limit', '20')
ON CONFLICT (key) DO NOTHING;

-- 2. Add is_banned column to users table
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS is_banned BOOLEAN DEFAULT FALSE;

COMMIT;
