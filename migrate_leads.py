"""
Migration script to add user_id and converted_at columns to leads table
Run this if you have an existing database with leads table
"""
from sqlalchemy import text
from app.database import engine

def migrate_leads():
    """Add user_id and converted_at columns to leads table"""
    with engine.connect() as conn:
        # Add user_id column if it doesn't exist
        try:
            conn.execute(text("""
                ALTER TABLE leads 
                ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id)
            """))
            conn.commit()
            print("✓ Added user_id column to leads table")
        except Exception as e:
            print(f"Note: user_id column may already exist: {e}")
        
        # Add converted_at column if it doesn't exist
        try:
            conn.execute(text("""
                ALTER TABLE leads 
                ADD COLUMN IF NOT EXISTS converted_at TIMESTAMP WITH TIME ZONE
            """))
            conn.commit()
            print("✓ Added converted_at column to leads table")
        except Exception as e:
            print(f"Note: converted_at column may already exist: {e}")
        
        print("Migration completed successfully!")

if __name__ == "__main__":
    migrate_leads()

