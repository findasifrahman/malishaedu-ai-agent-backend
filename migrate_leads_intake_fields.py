"""
Migration script to add intake_term and intake_year columns to leads table
Run this if you have an existing database without these columns
"""
from sqlalchemy import text
from app.database import engine

def migrate_leads_intake_fields():
    """Add intake_term and intake_year columns to leads table"""
    with engine.connect() as conn:
        # Add intake_term column to leads table
        try:
            conn.execute(text("""
                ALTER TABLE leads 
                ADD COLUMN IF NOT EXISTS intake_term VARCHAR
            """))
            conn.commit()
            print("✓ Added intake_term column to leads table")
        except Exception as e:
            print(f"Note: intake_term column may already exist: {e}")
            conn.rollback()
        
        # Add intake_year column to leads table
        try:
            conn.execute(text("""
                ALTER TABLE leads 
                ADD COLUMN IF NOT EXISTS intake_year INTEGER
            """))
            conn.commit()
            print("✓ Added intake_year column to leads table")
        except Exception as e:
            print(f"Note: intake_year column may already exist: {e}")
            conn.rollback()
        
        print("\nMigration completed successfully!")
        print("The leads table now has intake_term and intake_year columns.")

if __name__ == "__main__":
    migrate_leads_intake_fields()

