-- Sarvam Telecom Bot — Supabase Schema
-- Run this once in the Supabase SQL Editor to create the calls table.

CREATE TABLE IF NOT EXISTS calls (
  id                 BIGSERIAL PRIMARY KEY,
  call_id            VARCHAR     UNIQUE NOT NULL,
  customer_phone     VARCHAR     DEFAULT '',
  customer_name      VARCHAR     DEFAULT '',
  language           VARCHAR     DEFAULT 'hi',
  conversation       JSONB       DEFAULT '[]',
  status             VARCHAR     DEFAULT 'active',
  started_at         TIMESTAMPTZ,
  updated_at         TIMESTAMPTZ DEFAULT NOW(),
  ended_at           TIMESTAMPTZ,
  duration_seconds   INTEGER     DEFAULT 0,
  ticket_id          VARCHAR,
  escalated          BOOLEAN     DEFAULT FALSE,
  escalation_status  VARCHAR
);

-- Index for fast lookups by call_id (used on every turn)
CREATE INDEX IF NOT EXISTS idx_calls_call_id ON calls (call_id);

-- Auto-update updated_at on every row change
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER calls_updated_at
  BEFORE UPDATE ON calls
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();
