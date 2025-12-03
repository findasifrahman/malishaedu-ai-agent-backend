"""
Migration script to add application_fee and accommodation_fee to program_intakes table
and update applications table to link to program_intake_id

Run this script: python migrate_application_fields.py
Or run the SQL file directly: psql -U postgres -d malishaedu -f migrate_application_fields.sql
"""
import os
import sys

# Try to use psycopg2 if available
try:
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
    from dotenv import load_dotenv
    
    load_dotenv()
    
    def migrate():
        # Try to get DATABASE_URL first, then fall back to individual components
        database_url = os.getenv("DATABASE_URL")
        if database_url:
            # Parse postgresql://user:password@host:port/database
            # or postgresql+psycopg2://user:password@host:port/database
            database_url = database_url.replace("postgresql+psycopg2://", "postgresql://")
            database_url = database_url.replace("postgres://", "postgresql://")
            conn = psycopg2.connect(database_url)
        else:
            # Fall back to individual components
            conn = psycopg2.connect(
                host=os.getenv("DB_HOST", "localhost"),
                port=os.getenv("DB_PORT", "5432"),
                database=os.getenv("DB_NAME", "malishaedu"),
                user=os.getenv("DB_USER", "postgres"),
                password=os.getenv("DB_PASSWORD", "")
            )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cur = conn.cursor()
        
        try:
            # Read and execute SQL file
            sql_file_path = os.path.join(os.path.dirname(__file__), "migrate_application_fields.sql")
            with open(sql_file_path, 'r') as f:
                sql = f.read()
            
            print("Running migration...")
            cur.execute(sql)
            print("Migration completed successfully!")
            
        except Exception as e:
            print(f"Error during migration: {e}")
            raise
        finally:
            cur.close()
            conn.close()
    
    if __name__ == "__main__":
        migrate()
        
except ImportError:
    print("=" * 60)
    print("psycopg2 is not installed.")
    print("\nPlease run the SQL file directly in your database:")
    print("  psql -U postgres -d malishaedu -f migrate_application_fields.sql")
    print("\nOr install psycopg2-binary:")
    print("  pip install psycopg2-binary")
    print("=" * 60)
    sys.exit(1)
