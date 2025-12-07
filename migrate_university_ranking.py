"""
Migration script to add university_ranking column to universities table
"""
import os
import sys
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not found in environment variables")
    sys.exit(1)

# Ensure psycopg2 is used for PostgreSQL (same approach as database.py)
if DATABASE_URL.startswith("postgresql://") and "+psycopg2" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)
elif DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)

try:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=3600)
    Session = sessionmaker(bind=engine)
except Exception as e:
    print(f"ERROR: Failed to create engine: {e}")
    print("Make sure psycopg2-binary is installed: pip install psycopg2-binary")
    sys.exit(1)

def migrate():
    """Add university_ranking column to universities table"""
    session = Session()
    
    try:
        print("Starting migration: Add university_ranking to universities table...")
        
        # Check if column already exists
        check_query = text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='universities' AND column_name='university_ranking'
        """)
        result = session.execute(check_query).fetchone()
        
        if result:
            print("Column 'university_ranking' already exists. Skipping migration.")
            return
        
        # Add the column
        alter_query = text("""
            ALTER TABLE universities 
            ADD COLUMN IF NOT EXISTS university_ranking INTEGER NULL
        """)
        session.execute(alter_query)
        session.commit()
        
        print("✅ Successfully added 'university_ranking' column to 'universities' table")
        
    except Exception as e:
        session.rollback()
        print(f"❌ Error during migration: {e}")
        raise
    finally:
        session.close()

if __name__ == "__main__":
    migrate()
