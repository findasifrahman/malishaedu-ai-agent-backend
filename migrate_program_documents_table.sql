-- ===================================================================
-- Migration Script: Create program_documents Table
-- ===================================================================
-- This script creates the program_documents table to normalize documents_required
-- from the program_intakes table
-- ===================================================================

-- Create program_documents table
CREATE TABLE IF NOT EXISTS program_documents (
    id SERIAL PRIMARY KEY,
    program_intake_id INTEGER NOT NULL REFERENCES program_intakes(id) ON DELETE CASCADE,
    name VARCHAR NOT NULL,
    is_required BOOLEAN DEFAULT TRUE,
    rules TEXT,
    applies_to VARCHAR,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT fk_program_intake FOREIGN KEY (program_intake_id) REFERENCES program_intakes(id) ON DELETE CASCADE
);

-- Create index for faster lookups
CREATE INDEX IF NOT EXISTS idx_program_documents_program_intake_id ON program_documents(program_intake_id);
CREATE INDEX IF NOT EXISTS idx_program_documents_name ON program_documents(name);

-- ===================================================================
-- Sample Data Insert (Optional - for testing)
-- ===================================================================

-- Common document types that can be reused
-- Note: These are just examples. In practice, documents are linked to specific program intakes.

-- Example inserts (uncomment and modify as needed):
/*
INSERT INTO program_documents (program_intake_id, name, is_required, rules, applies_to) VALUES
(1, 'Passport', true, 'Valid passport with at least 6 months validity', NULL),
(1, 'Passport Photo', true, 'Colored 2-inch bare-headed photo, white background, 4:3 ratio, 100-500KB, JPG format', NULL),
(1, 'Academic Transcript', true, 'Official transcript from previous institution', NULL),
(1, 'Diploma', true, 'Highest degree diploma', NULL),
(1, 'Study Plan', true, 'Study plan 800+ words', NULL),
(1, 'Bank Statement', true, '≥ $5000 USD, last 3 months', NULL),
(1, 'Police Clearance Certificate', true, 'Issued within last 6 months', NULL),
(1, 'Physical Examination Form', true, 'Completed medical examination form', NULL),
(1, 'HSK Certificate', false, 'HSK-5 180+ score', 'chinese_taught_only'),
(1, 'IELTS Certificate', false, 'IELTS 6.0+ or TOEFL 80+', 'english_taught_only'),
(1, 'Recommendation Letter', true, 'Two recommendation letters from professors', NULL),
(1, 'CV/Resume', true, 'Updated CV with academic and work experience', NULL),
(1, 'Video Introduction', false, 'Video 3–5 minutes introducing yourself', 'inside_china_only');
*/

-- ===================================================================
-- Migration Complete!
-- ===================================================================

