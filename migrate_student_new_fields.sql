-- Migration script to add new fields to students table
-- Run this SQL directly in your PostgreSQL database client (pgAdmin, psql, etc.)

-- Create enum types
DO $$ BEGIN
    CREATE TYPE degreemedium AS ENUM ('English', 'Chinese', 'Native');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE maritalstatus AS ENUM ('Single', 'Married');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE religion AS ENUM ('Islam', 'Christianity', 'Catholicism', 'Buddhism', 'Other', 'No Religion');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE hskklevel AS ENUM ('Beginner', 'Elementary', 'Intermediate', 'Advanced');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Add new columns
ALTER TABLE students ADD COLUMN IF NOT EXISTS highest_degree_medium degreemedium;
ALTER TABLE students ADD COLUMN IF NOT EXISTS marital_status maritalstatus;
ALTER TABLE students ADD COLUMN IF NOT EXISTS religion religion;
ALTER TABLE students ADD COLUMN IF NOT EXISTS occupation VARCHAR;
ALTER TABLE students ADD COLUMN IF NOT EXISTS hsk_score FLOAT;
ALTER TABLE students ADD COLUMN IF NOT EXISTS hsk_certificate_date TIMESTAMP;
ALTER TABLE students ADD COLUMN IF NOT EXISTS hskk_level hskklevel;
ALTER TABLE students ADD COLUMN IF NOT EXISTS hskk_score FLOAT;
ALTER TABLE students ADD COLUMN IF NOT EXISTS number_of_published_papers INTEGER;

-- Migrate hsk_level to hsk_score if hsk_level exists and hsk_score is null
UPDATE students 
SET hsk_score = hsk_level 
WHERE hsk_level IS NOT NULL AND hsk_score IS NULL;

-- Drop hsk_level column (only if it exists)
DO $$ 
BEGIN
    IF EXISTS (
        SELECT 1 
        FROM information_schema.columns 
        WHERE table_name = 'students' 
        AND column_name = 'hsk_level'
    ) THEN
        ALTER TABLE students DROP COLUMN hsk_level;
    END IF;
END $$;

