"""
Migration: Convert scholarship_preference from PostgreSQL enum to VARCHAR
This allows SQLAlchemy to properly map database values to enum values
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
            print("Converting scholarship_preference from enum to VARCHAR...")
            
            # First, convert the column to VARCHAR
            print("Altering applications.scholarship_preference column to VARCHAR...")
            conn.execute(text("""
                ALTER TABLE applications 
                ALTER COLUMN scholarship_preference 
                TYPE VARCHAR(50)
                USING scholarship_preference::text;
            """))
            
            # Also convert in students table if it exists
            print("Altering students.scholarship_preference column to VARCHAR...")
            try:
                conn.execute(text("""
                    ALTER TABLE students 
                    ALTER COLUMN scholarship_preference 
                    TYPE VARCHAR(50)
                    USING scholarship_preference::text;
                """))
            except Exception as e:
                print(f"Note: Could not alter students.scholarship_preference: {e}")
            
            # Drop the enum type (optional, but cleans up)
            print("Dropping old enum type...")
            try:
                conn.execute(text("DROP TYPE IF EXISTS scholarshippreference CASCADE;"))
            except Exception as e:
                print(f"Note: Could not drop enum type: {e}")
            
            # Commit transaction
            trans.commit()
            print("Migration completed successfully!")
            print("\nNote: The scholarship_preference column is now VARCHAR.")
            print("SQLAlchemy will now correctly map database values to enum values.")
            
        except Exception as e:
            trans.rollback()
            print(f"Migration failed: {e}")
            raise

if __name__ == "__main__":
    migrate()

