"""Migration script to add new fields to program_intakes table"""
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
        print("Adding new columns to program_intakes...")
        
        # Add new columns
        cursor.execute("""
            ALTER TABLE program_intakes 
            ADD COLUMN IF NOT EXISTS service_fee FLOAT,
            ADD COLUMN IF NOT EXISTS medical_insurance_fee FLOAT,
            ADD COLUMN IF NOT EXISTS teaching_language VARCHAR,
            ADD COLUMN IF NOT EXISTS duration_years FLOAT,
            ADD COLUMN IF NOT EXISTS degree_type VARCHAR;
        """)
        
        # Add comments to clarify
        cursor.execute("""
            COMMENT ON COLUMN program_intakes.accommodation_fee IS 'Accommodation fee per year (not per semester)';
        """)
        
        cursor.execute("""
            COMMENT ON COLUMN program_intakes.service_fee IS 'MalishaEdu service fee - only charged for successful application';
        """)
        
        cursor.execute("""
            COMMENT ON COLUMN program_intakes.medical_insurance_fee IS 'Medical insurance fee - taken by university after successful application and arriving in China';
        """)
        
        cursor.execute("""
            COMMENT ON COLUMN program_intakes.scholarship_info IS 'Scholarship amount and conditions - LLM must parse and calculate actual costs after scholarship';
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

