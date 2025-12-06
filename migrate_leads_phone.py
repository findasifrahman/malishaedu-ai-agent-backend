"""
Migration script to add phone column to leads table if it doesn't exist
Run this if you have an existing database without the phone column
"""
from sqlalchemy import text
from app.database import engine

def migrate_leads_phone():
    """Add phone column to leads table if it doesn't exist"""
    with engine.connect() as conn:
        # Add phone column to leads table
        try:
            conn.execute(text("""
                ALTER TABLE leads 
                ADD COLUMN IF NOT EXISTS phone VARCHAR
            """))
            conn.commit()
            print("✓ Added phone column to leads table")
        except Exception as e:
            print(f"Note: phone column may already exist: {e}")
            conn.rollback()
        
        print("\nMigration completed successfully!")
        print("The leads table now has a phone column.")

if __name__ == "__main__":
    migrate_leads_phone()

