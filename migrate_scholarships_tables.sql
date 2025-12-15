-- ===================================================================
-- Migration Script: Create scholarships and program_intake_scholarships Tables
-- ===================================================================
-- This script creates the scholarships and program_intake_scholarships tables
-- to manage scholarship information for program intakes
-- ===================================================================

-- Create scholarships table
CREATE TABLE IF NOT EXISTS scholarships (
    id SERIAL PRIMARY KEY,
    name VARCHAR NOT NULL,
    provider VARCHAR,
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE
);

-- Create program_intake_scholarships table
CREATE TABLE IF NOT EXISTS program_intake_scholarships (
    id SERIAL PRIMARY KEY,
    program_intake_id INTEGER NOT NULL REFERENCES program_intakes(id) ON DELETE CASCADE,
    scholarship_id INTEGER NOT NULL REFERENCES scholarships(id) ON DELETE CASCADE,
    covers_tuition BOOLEAN,
    covers_accommodation BOOLEAN,
    covers_insurance BOOLEAN,
    tuition_waiver_percent INTEGER,
    living_allowance_monthly FLOAT,
    living_allowance_yearly FLOAT,
    first_year_only BOOLEAN,
    renewal_required BOOLEAN,
    deadline DATE,
    eligibility_note TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT fk_program_intake FOREIGN KEY (program_intake_id) REFERENCES program_intakes(id) ON DELETE CASCADE,
    CONSTRAINT fk_scholarship FOREIGN KEY (scholarship_id) REFERENCES scholarships(id) ON DELETE CASCADE
);

-- Create indexes for faster lookups
CREATE INDEX IF NOT EXISTS idx_program_intake_scholarships_program_intake_id ON program_intake_scholarships(program_intake_id);
CREATE INDEX IF NOT EXISTS idx_program_intake_scholarships_scholarship_id ON program_intake_scholarships(scholarship_id);
CREATE INDEX IF NOT EXISTS idx_scholarships_name ON scholarships(name);

-- ===================================================================
-- Sample Data Insert (Optional - for testing)
-- ===================================================================

-- Example inserts (uncomment and modify as needed):
/*
INSERT INTO scholarships (name, provider, notes) VALUES
('CSC Scholarship', 'CSC', 'Chinese Government Scholarship'),
('HuaShan Scholarship', 'University', 'First year only, renewal required'),
('Freshman Scholarship', 'University', 'For first-year students only'),
('Merit Scholarship', 'University', 'Based on academic performance'),
('Need-based Scholarship', 'University', 'For students with financial need');
*/

-- ===================================================================
-- Migration Complete!
-- ===================================================================

