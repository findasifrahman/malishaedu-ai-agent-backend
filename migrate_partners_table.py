"""
Migration script to create partners table and add default partner
Run this script to set up the partners table and create the default MalishaEdu partner
"""
import os
import sys
import bcrypt
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app.config import settings

# Database URL
DATABASE_URL = settings.DATABASE_URL

def migrate():
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    try:
        # Create partners table
        session.execute(text("""
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
            )
        """))
        
        # Create index
        session.execute(text("CREATE INDEX IF NOT EXISTS idx_partners_email ON partners(email)"))
        
        # Add partner_id to students table
        session.execute(text("ALTER TABLE students ADD COLUMN IF NOT EXISTS partner_id INTEGER REFERENCES partners(id)"))
        session.execute(text("CREATE INDEX IF NOT EXISTS idx_students_partner_id ON students(partner_id)"))
        
        # Hash password for default partner
        password = "12345678"
        hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        
        # Insert default partner (MalishaEdu)
        session.execute(text("""
            INSERT INTO partners (name, company_name, email, password) 
            VALUES (:name, :company_name, :email, :password)
            ON CONFLICT (email) DO NOTHING
        """), {
            'name': 'MalishaEdu',
            'company_name': 'MalishaEdu',
            'email': 'malishaedu@gmail.com',
            'password': hashed_password
        })
        
        session.commit()
        print("✅ Partners table created successfully!")
        print("✅ Default partner (MalishaEdu) created with email: malishaedu@gmail.com, password: 12345678")
        
    except Exception as e:
        session.rollback()
        print(f"❌ Error during migration: {e}")
        raise
    finally:
        session.close()

if __name__ == "__main__":
    migrate()

