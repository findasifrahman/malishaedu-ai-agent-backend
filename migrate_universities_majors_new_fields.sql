-- Migration script to add new fields to universities and majors tables
-- Run this SQL script directly on your database if the Python migration script fails

-- ========== UNIVERSITIES TABLE ==========
-- Add name_cn column
ALTER TABLE universities ADD COLUMN IF NOT EXISTS name_cn VARCHAR;

-- Add aliases column (JSONB)
ALTER TABLE universities ADD COLUMN IF NOT EXISTS aliases JSONB;

-- Add world_ranking_band column
ALTER TABLE universities ADD COLUMN IF NOT EXISTS world_ranking_band VARCHAR;

-- Add national_ranking column
ALTER TABLE universities ADD COLUMN IF NOT EXISTS national_ranking INTEGER;

-- Add project_tags column (JSONB)
ALTER TABLE universities ADD COLUMN IF NOT EXISTS project_tags JSONB;

-- Add default_currency column
ALTER TABLE universities ADD COLUMN IF NOT EXISTS default_currency VARCHAR DEFAULT 'CNY';
UPDATE universities SET default_currency = 'CNY' WHERE default_currency IS NULL;

-- Add is_active column
ALTER TABLE universities ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;
UPDATE universities SET is_active = TRUE WHERE is_active IS NULL;

-- ========== MAJORS TABLE ==========
-- Add name_cn column
ALTER TABLE majors ADD COLUMN IF NOT EXISTS name_cn VARCHAR;

-- Add is_active column
ALTER TABLE majors ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;
UPDATE majors SET is_active = TRUE WHERE is_active IS NULL;

-- Add category column
ALTER TABLE majors ADD COLUMN IF NOT EXISTS category VARCHAR;

-- Add keywords column (JSONB)
ALTER TABLE majors ADD COLUMN IF NOT EXISTS keywords JSONB;

