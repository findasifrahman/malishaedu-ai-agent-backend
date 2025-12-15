"""
Migration script to create program_documents table
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
        print("Starting migration: Create program_documents table...")
        print("=" * 60)
        
        # Check if table already exists
        cursor.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_name='program_documents'
        """)
        if cursor.fetchone():
            print("⏭️  program_documents table already exists. Skipping creation.")
            return
        
        # Create table
        print("Creating program_documents table...")
        cursor.execute("""
            CREATE TABLE program_documents (
                id SERIAL PRIMARY KEY,
                program_intake_id INTEGER NOT NULL REFERENCES program_intakes(id) ON DELETE CASCADE,
                name VARCHAR NOT NULL,
                is_required BOOLEAN DEFAULT TRUE,
                rules TEXT,
                applies_to VARCHAR,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE,
                CONSTRAINT fk_program_intake FOREIGN KEY (program_intake_id) REFERENCES program_intakes(id) ON DELETE CASCADE
            )
        """)
        
        # Create indexes
        print("Creating indexes...")
        cursor.execute("""
            CREATE INDEX idx_program_documents_program_intake_id ON program_documents(program_intake_id)
        """)
        cursor.execute("""
            CREATE INDEX idx_program_documents_name ON program_documents(name)
        """)
        
        conn.commit()
        print("\n" + "=" * 60)
        print("✅ Migration completed successfully!")
        print("✅ Created program_documents table with indexes")
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

