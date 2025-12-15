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

CREATE INDEX IF NOT EXISTS idx_partners_email ON partners(email);

-- Add partner_id to students table
ALTER TABLE students ADD COLUMN IF NOT EXISTS partner_id INTEGER REFERENCES partners(id);

CREATE INDEX IF NOT EXISTS idx_students_partner_id ON students(partner_id);

-- Insert default partner (MalishaEdu)
-- Password: 12345678 (will be hashed by bcrypt)
-- Note: You need to hash this password using bcrypt before inserting
-- For now, this is a placeholder - you'll need to run a Python script to hash it properly
INSERT INTO partners (name, company_name, email, password) 
VALUES ('MalishaEdu', 'MalishaEdu', 'malishaedu@gmail.com', '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewY5GyY5Y5Y5Y5Y5')
ON CONFLICT (email) DO NOTHING;

-- Note: The password hash above is a placeholder. 
-- You should generate the actual bcrypt hash for '12345678' using Python:
-- import bcrypt
-- bcrypt.hashpw(b'12345678', bcrypt.gensalt()).decode('utf-8')

