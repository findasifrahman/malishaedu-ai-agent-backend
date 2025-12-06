"""
Migration: Add highest_degree_year and highest_degree_cgpa to Student table
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
            print("Adding highest_degree_year column...")
            conn.execute(text("""
                ALTER TABLE students 
                ADD COLUMN IF NOT EXISTS highest_degree_year INTEGER;
            """))
            
            print("Adding highest_degree_cgpa column...")
            conn.execute(text("""
                ALTER TABLE students 
                ADD COLUMN IF NOT EXISTS highest_degree_cgpa FLOAT;
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

