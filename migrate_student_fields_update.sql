-- Migration script to update Student table fields
-- Remove full_name, update enums, add new fields

-- Step 1: Remove full_name and wechat_id columns (data should be migrated if needed)
-- Note: Before dropping, you may want to backup data
-- ALTER TABLE students DROP COLUMN IF EXISTS full_name;
-- ALTER TABLE students DROP COLUMN IF EXISTS wechat_id;  -- Removed, now in social_media_accounts JSON

-- Step 2: Add new enum types (if using PostgreSQL ENUM types, otherwise handled by SQLAlchemy)
-- Note: These are handled by SQLAlchemy enums, but we'll add the columns

-- Step 3: Add new fields
ALTER TABLE students ADD COLUMN IF NOT EXISTS native_language VARCHAR(50);
ALTER TABLE students ADD COLUMN IF NOT EXISTS video_url VARCHAR(500);  -- 3-5 Minutes Video Url
ALTER TABLE students ADD COLUMN IF NOT EXISTS acceptance_letter_url VARCHAR(500);  -- Acceptance Letter URL
ALTER TABLE students ADD COLUMN IF NOT EXISTS employer_or_institution_affiliated TEXT;
ALTER TABLE students ADD COLUMN IF NOT EXISTS health_status TEXT;
ALTER TABLE students ADD COLUMN IF NOT EXISTS hobby TEXT;
ALTER TABLE students ADD COLUMN IF NOT EXISTS is_ethnic_chinese BOOLEAN DEFAULT FALSE;
ALTER TABLE students ADD COLUMN IF NOT EXISTS chinese_language_proficiency VARCHAR(20);
ALTER TABLE students ADD COLUMN IF NOT EXISTS english_language_proficiency VARCHAR(20);
ALTER TABLE students ADD COLUMN IF NOT EXISTS other_language_proficiency TEXT;
ALTER TABLE students ADD COLUMN IF NOT EXISTS level_of_hsk VARCHAR(20);
ALTER TABLE students ADD COLUMN IF NOT EXISTS hsk_test_score_report_no VARCHAR(255);
ALTER TABLE students ADD COLUMN IF NOT EXISTS other_certificate_english_name VARCHAR(255);

-- Step 4: Add COVA form fields
ALTER TABLE students ADD COLUMN IF NOT EXISTS criminal_record BOOLEAN DEFAULT FALSE;
ALTER TABLE students ADD COLUMN IF NOT EXISTS criminal_record_details TEXT;
ALTER TABLE students ADD COLUMN IF NOT EXISTS financial_supporter JSONB;
ALTER TABLE students ADD COLUMN IF NOT EXISTS guarantor_in_china JSONB;
ALTER TABLE students ADD COLUMN IF NOT EXISTS social_media_accounts JSONB;
ALTER TABLE students ADD COLUMN IF NOT EXISTS studied_in_china BOOLEAN DEFAULT FALSE;
ALTER TABLE students ADD COLUMN IF NOT EXISTS studied_in_china_details TEXT;
ALTER TABLE students ADD COLUMN IF NOT EXISTS work_experience BOOLEAN DEFAULT FALSE;
ALTER TABLE students ADD COLUMN IF NOT EXISTS work_experience_details JSONB;
ALTER TABLE students ADD COLUMN IF NOT EXISTS worked_in_china BOOLEAN DEFAULT FALSE;
ALTER TABLE students ADD COLUMN IF NOT EXISTS worked_in_china_details TEXT;

-- Step 5: Update existing columns to use enum types (handled by SQLAlchemy)
-- Note: The enum values are stored as VARCHAR, SQLAlchemy handles the conversion

-- Step 6: Migrate full_name data to given_name + family_name if needed (optional)
-- UPDATE students 
-- SET given_name = COALESCE(given_name, SPLIT_PART(full_name, ' ', 1)),
--     family_name = COALESCE(family_name, SPLIT_PART(full_name, ' ', 2))
-- WHERE full_name IS NOT NULL AND (given_name IS NULL OR family_name IS NULL);

-- Step 7: Drop full_name column (uncomment after data migration)
-- ALTER TABLE students DROP COLUMN IF EXISTS full_name;

