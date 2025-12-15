-- ===================================================================
-- Migration Script: Add New Fields to program_intakes Table
-- ===================================================================
-- This script adds all the new fields requested for the program_intakes table
-- Run this SQL script directly on your PostgreSQL database
-- ===================================================================

-- ========== Program Start & Deadline ==========
ALTER TABLE program_intakes ADD COLUMN IF NOT EXISTS program_start_date DATE;
ALTER TABLE program_intakes ADD COLUMN IF NOT EXISTS deadline_type VARCHAR;

-- ========== Scholarship ==========
ALTER TABLE program_intakes ADD COLUMN IF NOT EXISTS scholarship_available BOOLEAN;

-- ========== Age Requirements ==========
ALTER TABLE program_intakes ADD COLUMN IF NOT EXISTS age_min INTEGER;
ALTER TABLE program_intakes ADD COLUMN IF NOT EXISTS age_max INTEGER;

-- ========== Academic Requirements ==========
ALTER TABLE program_intakes ADD COLUMN IF NOT EXISTS min_average_score FLOAT;

-- ========== Test/Interview Requirements ==========
ALTER TABLE program_intakes ADD COLUMN IF NOT EXISTS interview_required BOOLEAN;
ALTER TABLE program_intakes ADD COLUMN IF NOT EXISTS written_test_required BOOLEAN;
ALTER TABLE program_intakes ADD COLUMN IF NOT EXISTS acceptance_letter_required BOOLEAN;

-- ========== Inside China Applicants ==========
ALTER TABLE program_intakes ADD COLUMN IF NOT EXISTS inside_china_applicants_allowed BOOLEAN;
ALTER TABLE program_intakes ADD COLUMN IF NOT EXISTS inside_china_extra_requirements TEXT;

-- ========== Bank Statement Requirements ==========
ALTER TABLE program_intakes ADD COLUMN IF NOT EXISTS bank_statement_required BOOLEAN;
ALTER TABLE program_intakes ADD COLUMN IF NOT EXISTS bank_statement_amount FLOAT;
ALTER TABLE program_intakes ADD COLUMN IF NOT EXISTS bank_statement_currency VARCHAR;
ALTER TABLE program_intakes ADD COLUMN IF NOT EXISTS bank_statement_note TEXT;

-- ========== Language Requirements ==========
ALTER TABLE program_intakes ADD COLUMN IF NOT EXISTS hsk_required BOOLEAN;
ALTER TABLE program_intakes ADD COLUMN IF NOT EXISTS hsk_level INTEGER;
ALTER TABLE program_intakes ADD COLUMN IF NOT EXISTS hsk_min_score INTEGER;
ALTER TABLE program_intakes ADD COLUMN IF NOT EXISTS english_test_required BOOLEAN;
ALTER TABLE program_intakes ADD COLUMN IF NOT EXISTS english_test_note TEXT;

-- ========== Currency & Fee Periods ==========
ALTER TABLE program_intakes ADD COLUMN IF NOT EXISTS currency VARCHAR DEFAULT 'CNY';
UPDATE program_intakes SET currency = 'CNY' WHERE currency IS NULL;
ALTER TABLE program_intakes ADD COLUMN IF NOT EXISTS accommodation_fee_period VARCHAR;
ALTER TABLE program_intakes ADD COLUMN IF NOT EXISTS medical_insurance_fee_period VARCHAR;
ALTER TABLE program_intakes ADD COLUMN IF NOT EXISTS arrival_medical_checkup_is_one_time BOOLEAN DEFAULT TRUE;
UPDATE program_intakes SET arrival_medical_checkup_is_one_time = TRUE WHERE arrival_medical_checkup_is_one_time IS NULL;

-- ===================================================================
-- Migration Complete!
-- ===================================================================
-- All new fields have been added to the program_intakes table.
-- Default values have been set where applicable:
--   - currency: 'CNY'
--   - arrival_medical_checkup_is_one_time: TRUE
-- ===================================================================

