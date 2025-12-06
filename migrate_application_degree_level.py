"""
Migration: Add degree_level field to applications table
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
            # Add new column
            print("Adding degree_level column to applications table...")
            conn.execute(text("""
                ALTER TABLE applications 
                ADD COLUMN IF NOT EXISTS degree_level VARCHAR(100);
            """))
            
            # Try to populate from program_intake if possible
            print("Populating degree_level from program_intakes...")
            try:
                conn.execute(text("""
                    UPDATE applications a
                    SET degree_level = pi.degree_type
                    FROM program_intakes pi
                    WHERE a.program_intake_id = pi.id
                    AND a.degree_level IS NULL
                    AND pi.degree_type IS NOT NULL;
                """))
                print("Populated degree_level from program_intakes where available")
            except Exception as e:
                print(f"Note: Could not populate from program_intakes: {e}")
            
            # Commit transaction
            trans.commit()
            print("Migration completed successfully!")
            
        except Exception as e:
            trans.rollback()
            print(f"Migration failed: {e}")
            raise

if __name__ == "__main__":
    migrate()

