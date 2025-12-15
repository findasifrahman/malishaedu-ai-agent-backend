-- Add partner_id column to students table if it doesn't exist
ALTER TABLE students ADD COLUMN IF NOT EXISTS partner_id INTEGER REFERENCES partners(id);

-- Create index on partner_id in students table for better query performance
CREATE INDEX IF NOT EXISTS idx_students_partner_id ON students(partner_id);

-- Optional: Set existing students without a partner to the default MalishaEdu partner
-- Uncomment the following if you want to assign existing students to the default partner
-- UPDATE students 
-- SET partner_id = (SELECT id FROM partners WHERE email = 'malishaedu@gmail.com' LIMIT 1)
-- WHERE partner_id IS NULL;

