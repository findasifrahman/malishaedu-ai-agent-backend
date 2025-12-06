"""
Check the actual column type in the database
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from app.config import settings

def check():
    db_url = settings.DATABASE_URL
    if not db_url.startswith('postgresql://') and not db_url.startswith('postgresql+psycopg2://'):
        if db_url.startswith('postgres://'):
            db_url = db_url.replace('postgres://', 'postgresql+psycopg2://', 1)
        else:
            db_url = f'postgresql+psycopg2://{db_url}'
    engine = create_engine(db_url)
    
    with engine.connect() as conn:
        # Check applications table
        result = conn.execute(text("""
            SELECT data_type, udt_name 
            FROM information_schema.columns 
            WHERE table_name = 'applications' 
            AND column_name = 'scholarship_preference'
        """))
        row = result.fetchone()
        if row:
            print(f"applications.scholarship_preference: data_type={row[0]}, udt_name={row[1]}")
        else:
            print("Column not found in applications table")
        
        # Check students table
        result = conn.execute(text("""
            SELECT data_type, udt_name 
            FROM information_schema.columns 
            WHERE table_name = 'students' 
            AND column_name = 'scholarship_preference'
        """))
        row = result.fetchone()
        if row:
            print(f"students.scholarship_preference: data_type={row[0]}, udt_name={row[1]}")
        else:
            print("Column not found in students table")
        
        # Check if enum type still exists
        result = conn.execute(text("""
            SELECT typname FROM pg_type WHERE typname = 'scholarshippreference'
        """))
        row = result.fetchone()
        if row:
            print(f"WARNING: Enum type 'scholarshippreference' still exists in database!")
        else:
            print("Enum type 'scholarshippreference' does not exist (good)")

if __name__ == "__main__":
    check()

