"""
Migration: Add new document fields to Student table and remove chinese_language_certificate_url
- Add: passport_page_url, cv_resume_url, jw202_jw201_url
- Remove: chinese_language_certificate_url
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
            # Add new columns
            print("Adding passport_page_url column...")
            conn.execute(text("""
                ALTER TABLE students 
                ADD COLUMN IF NOT EXISTS passport_page_url VARCHAR;
            """))
            
            print("Adding cv_resume_url column...")
            conn.execute(text("""
                ALTER TABLE students 
                ADD COLUMN IF NOT EXISTS cv_resume_url VARCHAR;
            """))
            
            print("Adding jw202_jw201_url column...")
            conn.execute(text("""
                ALTER TABLE students 
                ADD COLUMN IF NOT EXISTS jw202_jw201_url VARCHAR;
            """))
            
            # Remove chinese_language_certificate_url column
            print("Removing chinese_language_certificate_url column...")
            try:
                conn.execute(text("""
                    ALTER TABLE students 
                    DROP COLUMN IF EXISTS chinese_language_certificate_url;
                """))
            except Exception as e:
                print(f"Note: Column might not exist: {e}")
            
            # Commit transaction
            trans.commit()
            print("Migration completed successfully!")
            
        except Exception as e:
            trans.rollback()
            print(f"Migration failed: {e}")
            raise

if __name__ == "__main__":
    migrate()

