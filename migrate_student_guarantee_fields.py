"""
Migration: Add guarantee letter fields to Student table
- Add: guarantee_letter_url, bank_guarantor_letter_url, relation_with_guarantor, is_the_bank_guarantee_in_students_name
- Create student_documents table
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from app.config import settings

def migrate():
    db_url = settings.DATABASE_URL
    # Ensure postgresql:// prefix
    if not db_url.startswith('postgresql://') and not db_url.startswith('postgresql+psycopg2://'):
        if db_url.startswith('postgres://'):
            db_url = db_url.replace('postgres://', 'postgresql+psycopg2://', 1)
        else:
            db_url = f'postgresql+psycopg2://{db_url}'
    engine = create_engine(db_url)
    
    with engine.connect() as conn:
        # Start transaction
        trans = conn.begin()
        try:
            # Add new columns to students table
            print("Adding guarantee_letter_url column...")
            conn.execute(text("""
                ALTER TABLE students 
                ADD COLUMN IF NOT EXISTS guarantee_letter_url VARCHAR;
            """))
            
            print("Adding bank_guarantor_letter_url column...")
            conn.execute(text("""
                ALTER TABLE students 
                ADD COLUMN IF NOT EXISTS bank_guarantor_letter_url VARCHAR;
            """))
            
            print("Adding relation_with_guarantor column...")
            conn.execute(text("""
                ALTER TABLE students 
                ADD COLUMN IF NOT EXISTS relation_with_guarantor VARCHAR;
            """))
            
            print("Adding is_the_bank_guarantee_in_students_name column...")
            conn.execute(text("""
                ALTER TABLE students 
                ADD COLUMN IF NOT EXISTS is_the_bank_guarantee_in_students_name BOOLEAN DEFAULT TRUE NOT NULL;
            """))
            
            # Create student_documents table
            print("Creating student_documents table...")
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS student_documents (
                    id SERIAL PRIMARY KEY,
                    student_id INTEGER NOT NULL REFERENCES students(id),
                    document_type VARCHAR NOT NULL,
                    file_url VARCHAR NOT NULL,
                    r2_url VARCHAR,
                    filename VARCHAR NOT NULL,
                    file_size INTEGER,
                    verification_status VARCHAR NOT NULL,
                    verification_reason TEXT,
                    extracted_data JSONB,
                    verified BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE
                );
            """))
            
            # Create index
            print("Creating indexes...")
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_student_documents_student_id 
                ON student_documents(student_id);
            """))
            
            # Commit transaction
            trans.commit()
            print("Migration completed successfully!")
            
        except Exception as e:
            trans.rollback()
            print(f"Migration failed: {e}")
            raise

if __name__ == "__main__":
    migrate()

