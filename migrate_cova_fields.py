"""
Migration script to add COVA (China Visa Application) related fields to students table
Based on AdmissionAgent prompt requirements
"""
from sqlalchemy import text, inspect
from app.database import engine
import sys

def migrate_cova_fields():
    """Add COVA-related fields to students table if they don't exist"""
    try:
        inspector = inspect(engine)
        
        if 'students' not in inspector.get_table_names():
            print("Students table does not exist. Run migrate_new_tables.py first.")
            sys.exit(1)
        
        print("\nChecking students table for COVA-related fields...")
        student_columns = [col['name'] for col in inspector.get_columns('students')]
        
        # COVA-related fields from AdmissionAgent prompt
        cova_fields = {
            'home_address': 'TEXT',
            'current_address': 'TEXT',
            'emergency_contact_name': 'VARCHAR',
            'emergency_contact_phone': 'VARCHAR',
            'emergency_contact_relationship': 'VARCHAR',
            'education_history': 'JSONB',  # PostgreSQL JSONB for better performance
            'employment_history': 'JSONB',
            'family_members': 'JSONB',
            'planned_arrival_date': 'TIMESTAMP WITH TIME ZONE',
            'intended_address_china': 'TEXT',
            'previous_visa_china': 'BOOLEAN',
            'previous_visa_details': 'TEXT',
            'previous_travel_to_china': 'BOOLEAN',
            'previous_travel_details': 'TEXT',
            'study_plan_url': 'VARCHAR'  # Study plan / motivation letter URL
        }
        
        with engine.connect() as conn:
            for col_name, col_type in cova_fields.items():
                if col_name not in student_columns:
                    print(f"Adding COVA field: {col_name}")
                    # Set default for boolean fields
                    default_clause = ""
                    if col_type == 'BOOLEAN':
                        default_clause = "DEFAULT FALSE"
                    
                    conn.execute(text(f"""
                        ALTER TABLE students 
                        ADD COLUMN IF NOT EXISTS {col_name} {col_type} {default_clause}
                    """))
                    conn.commit()
                    print(f"✓ Added {col_name}")
                else:
                    print(f"✓ Column {col_name} already exists")
        
        print("\nCOVA fields migration completed successfully!")
                
    except Exception as e:
        print(f"\nError during migration: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    migrate_cova_fields()

