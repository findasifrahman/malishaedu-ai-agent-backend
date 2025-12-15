-- Create partners table
CREATE TABLE IF NOT EXISTS partners (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  company_name TEXT,
  phone1 TEXT,
  phone2 TEXT,
  email VARCHAR UNIQUE NOT NULL,
  city TEXT,
  country TEXT,
  full_address TEXT,
  website TEXT,
  notes TEXT,
  password VARCHAR NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ
);

-- Create index on email
CREATE INDEX IF NOT EXISTS idx_partners_email ON partners(email);

-- Add partner_id column to students table (if students table exists)
ALTER TABLE students ADD COLUMN IF NOT EXISTS partner_id INTEGER REFERENCES partners(id);

-- Create index on partner_id in students table
CREATE INDEX IF NOT EXISTS idx_students_partner_id ON students(partner_id);

-- Insert default partner (MalishaEdu)
-- Email: malishaedu@gmail.com
-- Password: 12345678 (hashed with bcrypt)
INSERT INTO partners (name, company_name, email, password) 
VALUES (
  'MalishaEdu', 
  'MalishaEdu', 
  'malishaedu@gmail.com', 
  '$2b$12$CTydOer6Tv/Lk1s81cJR6eGRYLPQ4XSa3h8hDGHRMdyJ0Gb34fRrW'
)
ON CONFLICT (email) DO NOTHING;

