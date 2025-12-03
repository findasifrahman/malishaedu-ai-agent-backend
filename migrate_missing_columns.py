"""
Quick migration script to add missing phone, email, date_of_birth, and passport_number columns to students table
"""
from sqlalchemy import text, inspect
from app.database import engine
import sys

def migrate_missing_columns():
    """Add missing columns to students table if they don't exist"""
    try:
        inspector = inspect(engine)
        
        if 'students' not in inspector.get_table_names():
            print("Students table does not exist. Run migrate_new_tables.py first.")
            sys.exit(1)
        
        print("\nChecking students table for missing columns...")
        student_columns = [col['name'] for col in inspector.get_columns('students')]
        
        missing_columns = {
            'phone': 'VARCHAR',
            'email': 'VARCHAR',
            'date_of_birth': 'TIMESTAMP WITH TIME ZONE',
            'passport_number': 'VARCHAR'
        }
        
        with engine.connect() as conn:
            for col_name, col_type in missing_columns.items():
                if col_name not in student_columns:
                    print(f"Adding missing column: {col_name}")
                    conn.execute(text(f"""
                        ALTER TABLE students 
                        ADD COLUMN IF NOT EXISTS {col_name} {col_type}
                    """))
                    conn.commit()
                    print(f"✓ Added {col_name}")
                else:
                    print(f"✓ Column {col_name} already exists")
        
        print("\nMigration completed successfully!")
                
    except Exception as e:
        print(f"\nError during migration: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    migrate_missing_columns()

