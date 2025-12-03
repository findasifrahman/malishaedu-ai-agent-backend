-- Migration script to add new fields to program_intakes table
-- New fields: service_fee, medical_insurance_fee, teaching_language, duration_years, degree_type
-- Also update accommodation_fee comment to clarify it's per year

-- Add new columns
ALTER TABLE program_intakes 
ADD COLUMN IF NOT EXISTS service_fee FLOAT,
ADD COLUMN IF NOT EXISTS medical_insurance_fee FLOAT,
ADD COLUMN IF NOT EXISTS teaching_language VARCHAR,
ADD COLUMN IF NOT EXISTS duration_years FLOAT,
ADD COLUMN IF NOT EXISTS degree_type VARCHAR;

-- Add comments to clarify
COMMENT ON COLUMN program_intakes.accommodation_fee IS 'Accommodation fee per year (not per semester)';
COMMENT ON COLUMN program_intakes.service_fee IS 'MalishaEdu service fee - only charged for successful application';
COMMENT ON COLUMN program_intakes.medical_insurance_fee IS 'Medical insurance fee - taken by university after successful application and arriving in China';
COMMENT ON COLUMN program_intakes.scholarship_info IS 'Scholarship amount and conditions - LLM must parse and calculate actual costs after scholarship';

-- Migration completed!

