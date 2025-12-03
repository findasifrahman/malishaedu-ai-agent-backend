"""Migration script to add additional fields to program_intakes table and change majors table column types"""
import psycopg2
from dotenv import load_dotenv
import os

load_dotenv()

def migrate():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable not set.")
    
    # Use the connection string directly
    conn = psycopg2.connect(database_url)
    conn.autocommit = False
    cursor = conn.cursor()
    
    try:
        print("Step 1: Changing majors.degree_level and majors.teaching_language from enum to VARCHAR...")
        
        # First, check if columns are enum type and convert to VARCHAR
        # PostgreSQL doesn't support direct ALTER COLUMN from enum to varchar, so we need to:
        # 1. Add new VARCHAR columns
        # 2. Copy data
        # 3. Drop old columns
        # 4. Rename new columns
        
        # Check current type
        cursor.execute("""
            SELECT data_type 
            FROM information_schema.columns 
            WHERE table_name='majors' AND column_name='degree_level';
        """)
        degree_level_type = cursor.fetchone()
        
        if degree_level_type and 'enum' in degree_level_type[0].lower():
            print("Converting degree_level from enum to VARCHAR...")
            cursor.execute("""
                ALTER TABLE majors 
                ADD COLUMN IF NOT EXISTS degree_level_new VARCHAR;
            """)
            cursor.execute("""
                UPDATE majors 
                SET degree_level_new = degree_level::text;
            """)
            cursor.execute("""
                ALTER TABLE majors 
                DROP COLUMN IF EXISTS degree_level;
            """)
            cursor.execute("""
                ALTER TABLE majors 
                RENAME COLUMN degree_level_new TO degree_level;
            """)
        
        cursor.execute("""
            SELECT data_type 
            FROM information_schema.columns 
            WHERE table_name='majors' AND column_name='teaching_language';
        """)
        teaching_lang_type = cursor.fetchone()
        
        if teaching_lang_type and 'enum' in teaching_lang_type[0].lower():
            print("Converting teaching_language from enum to VARCHAR...")
            cursor.execute("""
                ALTER TABLE majors 
                ADD COLUMN IF NOT EXISTS teaching_language_new VARCHAR;
            """)
            cursor.execute("""
                UPDATE majors 
                SET teaching_language_new = teaching_language::text;
            """)
            cursor.execute("""
                ALTER TABLE majors 
                DROP COLUMN IF EXISTS teaching_language;
            """)
            cursor.execute("""
                ALTER TABLE majors 
                RENAME COLUMN teaching_language_new TO teaching_language;
            """)
        
        print("Step 2: Adding new columns to program_intakes...")
        
        # Add new columns to program_intakes
        cursor.execute("""
            ALTER TABLE program_intakes 
            ADD COLUMN IF NOT EXISTS arrival_medical_checkup_fee FLOAT DEFAULT 0,
            ADD COLUMN IF NOT EXISTS admission_process TEXT,
            ADD COLUMN IF NOT EXISTS accommodation_note TEXT,
            ADD COLUMN IF NOT EXISTS visa_extension_fee FLOAT DEFAULT 0;
        """)
        
        # Also update teaching_language and degree_type if they are enum
        cursor.execute("""
            SELECT data_type 
            FROM information_schema.columns 
            WHERE table_name='program_intakes' AND column_name='teaching_language';
        """)
        intake_teaching_lang_type = cursor.fetchone()
        
        if intake_teaching_lang_type and 'enum' in intake_teaching_lang_type[0].lower():
            print("Converting program_intakes.teaching_language from enum to VARCHAR...")
            cursor.execute("""
                ALTER TABLE program_intakes 
                ADD COLUMN IF NOT EXISTS teaching_language_new VARCHAR;
            """)
            cursor.execute("""
                UPDATE program_intakes 
                SET teaching_language_new = teaching_language::text;
            """)
            cursor.execute("""
                ALTER TABLE program_intakes 
                DROP COLUMN IF EXISTS teaching_language;
            """)
            cursor.execute("""
                ALTER TABLE program_intakes 
                RENAME COLUMN teaching_language_new TO teaching_language;
            """)
        
        cursor.execute("""
            SELECT data_type 
            FROM information_schema.columns 
            WHERE table_name='program_intakes' AND column_name='degree_type';
        """)
        intake_degree_type = cursor.fetchone()
        
        if intake_degree_type and 'enum' in intake_degree_type[0].lower():
            print("Converting program_intakes.degree_type from enum to VARCHAR...")
            cursor.execute("""
                ALTER TABLE program_intakes 
                ADD COLUMN IF NOT EXISTS degree_type_new VARCHAR;
            """)
            cursor.execute("""
                UPDATE program_intakes 
                SET degree_type_new = degree_type::text;
            """)
            cursor.execute("""
                ALTER TABLE program_intakes 
                DROP COLUMN IF EXISTS degree_type;
            """)
            cursor.execute("""
                ALTER TABLE program_intakes 
                RENAME COLUMN degree_type_new TO degree_type;
            """)
        
        # Add comments to clarify new fields
        cursor.execute("""
            COMMENT ON COLUMN program_intakes.arrival_medical_checkup_fee IS 'One-time medical checkup fee upon arrival in China (LLM should know this is one-time)';
        """)
        
        cursor.execute("""
            COMMENT ON COLUMN program_intakes.visa_extension_fee IS 'Visa extension fee required each year (LLM should know this is annual)';
        """)
        
        conn.commit()
        print("Migration completed successfully!")
        
    except Exception as e:
        conn.rollback()
        print(f"Error during migration: {e}")
        raise
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    migrate()

