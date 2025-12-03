-- Add missing columns to students table
-- Run this SQL script directly on your PostgreSQL database

-- Basic fields
ALTER TABLE students ADD COLUMN IF NOT EXISTS phone VARCHAR;
ALTER TABLE students ADD COLUMN IF NOT EXISTS email VARCHAR;
ALTER TABLE students ADD COLUMN IF NOT EXISTS date_of_birth TIMESTAMP WITH TIME ZONE;
ALTER TABLE students ADD COLUMN IF NOT EXISTS passport_number VARCHAR;

-- Study plan field
ALTER TABLE students ADD COLUMN IF NOT EXISTS study_plan_url VARCHAR;

-- COVA (China Visa Application) fields
ALTER TABLE students ADD COLUMN IF NOT EXISTS home_address TEXT;
ALTER TABLE students ADD COLUMN IF NOT EXISTS current_address TEXT;
ALTER TABLE students ADD COLUMN IF NOT EXISTS emergency_contact_name VARCHAR;
ALTER TABLE students ADD COLUMN IF NOT EXISTS emergency_contact_phone VARCHAR;
ALTER TABLE students ADD COLUMN IF NOT EXISTS emergency_contact_relationship VARCHAR;
ALTER TABLE students ADD COLUMN IF NOT EXISTS education_history JSONB;
ALTER TABLE students ADD COLUMN IF NOT EXISTS employment_history JSONB;
ALTER TABLE students ADD COLUMN IF NOT EXISTS family_members JSONB;
ALTER TABLE students ADD COLUMN IF NOT EXISTS planned_arrival_date TIMESTAMP WITH TIME ZONE;
ALTER TABLE students ADD COLUMN IF NOT EXISTS intended_address_china TEXT;
ALTER TABLE students ADD COLUMN IF NOT EXISTS previous_visa_china BOOLEAN DEFAULT FALSE;
ALTER TABLE students ADD COLUMN IF NOT EXISTS previous_visa_details TEXT;
ALTER TABLE students ADD COLUMN IF NOT EXISTS previous_travel_to_china BOOLEAN DEFAULT FALSE;
ALTER TABLE students ADD COLUMN IF NOT EXISTS previous_travel_details TEXT;

-- Verify columns were added
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'students' 
AND column_name IN ('phone', 'email', 'date_of_birth', 'passport_number', 'study_plan_url', 'home_address', 'current_address');
