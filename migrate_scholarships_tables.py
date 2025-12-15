"""
Migration script to create scholarships and program_intake_scholarships tables
"""
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
        print("Starting migration: Create scholarships tables...")
        print("=" * 60)
        
        # Check if scholarships table already exists
        cursor.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_name='scholarships'
        """)
        if cursor.fetchone():
            print("⏭️  scholarships table already exists. Skipping creation.")
        else:
            # Create scholarships table
            print("Creating scholarships table...")
            cursor.execute("""
                CREATE TABLE scholarships (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    provider VARCHAR,
                    notes TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE
                )
            """)
            print("✅ Created scholarships table")
        
        # Check if program_intake_scholarships table already exists
        cursor.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_name='program_intake_scholarships'
        """)
        if cursor.fetchone():
            print("⏭️  program_intake_scholarships table already exists. Skipping creation.")
        else:
            # Create program_intake_scholarships table
            print("Creating program_intake_scholarships table...")
            cursor.execute("""
                CREATE TABLE program_intake_scholarships (
                    id SERIAL PRIMARY KEY,
                    program_intake_id INTEGER NOT NULL REFERENCES program_intakes(id) ON DELETE CASCADE,
                    scholarship_id INTEGER NOT NULL REFERENCES scholarships(id) ON DELETE CASCADE,
                    covers_tuition BOOLEAN,
                    covers_accommodation BOOLEAN,
                    covers_insurance BOOLEAN,
                    tuition_waiver_percent INTEGER,
                    living_allowance_monthly FLOAT,
                    living_allowance_yearly FLOAT,
                    first_year_only BOOLEAN,
                    renewal_required BOOLEAN,
                    deadline DATE,
                    eligibility_note TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE,
                    CONSTRAINT fk_program_intake FOREIGN KEY (program_intake_id) REFERENCES program_intakes(id) ON DELETE CASCADE,
                    CONSTRAINT fk_scholarship FOREIGN KEY (scholarship_id) REFERENCES scholarships(id) ON DELETE CASCADE
                )
            """)
            print("✅ Created program_intake_scholarships table")
        
        # Create indexes
        print("Creating indexes...")
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_program_intake_scholarships_program_intake_id 
            ON program_intake_scholarships(program_intake_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_program_intake_scholarships_scholarship_id 
            ON program_intake_scholarships(scholarship_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_scholarships_name ON scholarships(name)
        """)
        
        conn.commit()
        print("\n" + "=" * 60)
        print("✅ Migration completed successfully!")
        print("✅ Created scholarships and program_intake_scholarships tables with indexes")
        print("=" * 60)
        
    except Exception as e:
        conn.rollback()
        print(f"\n❌ Error during migration: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    migrate()

