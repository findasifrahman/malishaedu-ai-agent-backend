-- Migration script to add application_fee and accommodation_fee to program_intakes table
-- and update applications table to link to program_intake_id

-- Add application_fee and accommodation_fee to program_intakes
ALTER TABLE program_intakes 
ADD COLUMN IF NOT EXISTS application_fee FLOAT,
ADD COLUMN IF NOT EXISTS accommodation_fee FLOAT;

-- Update applications table structure
-- Check if program_intake_id column exists, if not add it
DO $$ 
BEGIN
    -- Add new columns if they don't exist
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name='applications' AND column_name='program_intake_id'
    ) THEN
        ALTER TABLE applications
        ADD COLUMN program_intake_id INTEGER,
        ADD COLUMN application_fee_paid BOOLEAN DEFAULT FALSE,
        ADD COLUMN application_fee_amount FLOAT,
        ADD COLUMN admin_notes TEXT,
        ADD COLUMN submitted_at TIMESTAMP WITH TIME ZONE,
        ADD COLUMN admin_reviewed_at TIMESTAMP WITH TIME ZONE,
        ADD COLUMN result VARCHAR,
        ADD COLUMN result_notes TEXT;
        
        -- Add foreign key constraint
        ALTER TABLE applications 
        ADD CONSTRAINT applications_program_intake_id_fkey 
        FOREIGN KEY (program_intake_id) REFERENCES program_intakes(id);
    END IF;
END $$;

-- Migration completed!

