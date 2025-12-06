"""
Migration: Update ScholarshipPreference enum to include new types
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
            print("Updating ScholarshipPreference enum type...")
            
            # First, create a new enum type with all values
            conn.execute(text("""
                DO $$ BEGIN
                    CREATE TYPE scholarshippreference_new AS ENUM (
                        'Type-A',
                        'Type-B',
                        'Type-C',
                        'Type-D',
                        'Partial-Low',
                        'Partial-Mid',
                        'Partial-High',
                        'Self-Paid',
                        'None'
                    );
                EXCEPTION
                    WHEN duplicate_object THEN null;
                END $$;
            """))
            
            # First, update existing values to match new format
            print("Updating existing scholarship_preference values...")
            conn.execute(text("""
                UPDATE applications 
                SET scholarship_preference = 
                    CASE 
                        WHEN scholarship_preference::text = 'TYPE_A' THEN 'Type-A'
                        WHEN scholarship_preference::text = 'TYPE_B' THEN 'Type-B'
                        WHEN scholarship_preference::text = 'TYPE_C' THEN 'Type-C'
                        WHEN scholarship_preference::text = 'TYPE_D' THEN 'Type-D'
                        WHEN scholarship_preference::text = 'NONE' THEN 'None'
                        ELSE scholarship_preference::text
                    END::text
                WHERE scholarship_preference IS NOT NULL;
            """))
            
            # Alter the column to use the new enum type
            print("Altering applications.scholarship_preference column...")
            conn.execute(text("""
                ALTER TABLE applications 
                ALTER COLUMN scholarship_preference 
                TYPE scholarshippreference_new 
                USING CASE 
                    WHEN scholarship_preference::text = 'Type-A' THEN 'Type-A'::scholarshippreference_new
                    WHEN scholarship_preference::text = 'Type-B' THEN 'Type-B'::scholarshippreference_new
                    WHEN scholarship_preference::text = 'Type-C' THEN 'Type-C'::scholarshippreference_new
                    WHEN scholarship_preference::text = 'Type-D' THEN 'Type-D'::scholarshippreference_new
                    WHEN scholarship_preference::text = 'None' THEN 'None'::scholarshippreference_new
                    ELSE NULL
                END;
            """))
            
            # Drop the old enum type and rename the new one
            print("Dropping old enum type and renaming new one...")
            conn.execute(text("""
                DROP TYPE IF EXISTS scholarshippreference CASCADE;
                ALTER TYPE scholarshippreference_new RENAME TO scholarshippreference;
            """))
            
            # Commit transaction
            trans.commit()
            print("Migration completed successfully!")
            
        except Exception as e:
            trans.rollback()
            print(f"Migration failed: {e}")
            print("\nNote: If the enum type already exists with new values, this is expected.")
            print("The migration will skip creating the enum if it already exists.")
            raise

if __name__ == "__main__":
    migrate()

