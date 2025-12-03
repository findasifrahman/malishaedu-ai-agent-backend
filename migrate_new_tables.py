"""
Migration script to create new tables: universities, majors, program_intakes
and add new columns to students table
Run this after updating models.py
"""
from sqlalchemy import text, inspect
from app.database import engine, Base
from app.models import University, Major, ProgramIntake, Student
import sys

def migrate_new_tables():
    """Create new tables and migrate student table"""
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()
    
    with engine.connect() as conn:
        # Create new tables if they don't exist
        if 'universities' not in existing_tables:
            print("Creating universities table...")
            University.__table__.create(engine, checkfirst=True)
            print("✓ Created universities table")
        else:
            print("✓ universities table already exists")
        
        if 'majors' not in existing_tables:
            print("Creating majors table...")
            Major.__table__.create(engine, checkfirst=True)
            print("✓ Created majors table")
        else:
            print("✓ majors table already exists")
        
        if 'program_intakes' not in existing_tables:
            print("Creating program_intakes table...")
            ProgramIntake.__table__.create(engine, checkfirst=True)
            print("✓ Created program_intakes table")
        else:
            print("✓ program_intakes table already exists")
        
        # Migrate students table - add new columns if they don't exist
        if 'students' in existing_tables:
            print("\nMigrating students table...")
            student_columns = [col['name'] for col in inspector.get_columns('students')]
            
            new_columns = {
                'full_name': 'VARCHAR',
                'given_name': 'VARCHAR',
                'family_name': 'VARCHAR',
                'father_name': 'VARCHAR',
                'mother_name': 'VARCHAR',
                'gender': 'VARCHAR',
                'country_of_citizenship': 'VARCHAR',
                'current_country_of_residence': 'VARCHAR',
                'date_of_birth': 'TIMESTAMP WITH TIME ZONE',
                'phone': 'VARCHAR',
                'email': 'VARCHAR',
                'wechat_id': 'VARCHAR',
                'passport_number': 'VARCHAR',
                'passport_expiry_date': 'TIMESTAMP WITH TIME ZONE',
                'passport_scanned_url': 'VARCHAR',
                'passport_photo_url': 'VARCHAR',
                'hsk_level': 'INTEGER',
                'hsk_certificate_url': 'VARCHAR',
                'csca_status': 'VARCHAR',
                'csca_score_math': 'FLOAT',
                'csca_score_specialized_chinese': 'FLOAT',
                'csca_score_physics': 'FLOAT',
                'csca_score_chemistry': 'FLOAT',
                'csca_report_url': 'VARCHAR',
                'english_test_type': 'VARCHAR',
                'english_test_score': 'FLOAT',
                'english_certificate_url': 'VARCHAR',
                'highest_degree_diploma_url': 'VARCHAR',
                'highest_degree_name': 'VARCHAR',
                'highest_degree_institution': 'VARCHAR',
                'highest_degree_country': 'VARCHAR',
                'academic_transcript_url': 'VARCHAR',
                'physical_examination_form_url': 'VARCHAR',
                'police_clearance_url': 'VARCHAR',
                'bank_statement_url': 'VARCHAR',
                'recommendation_letter_1_url': 'VARCHAR',
                'recommendation_letter_2_url': 'VARCHAR',
                'guarantee_letter_url': 'VARCHAR',
                'residence_permit_url': 'VARCHAR',
                'study_certificate_china_url': 'VARCHAR',
                'application_form_url': 'VARCHAR',
                'chinese_language_certificate_url': 'VARCHAR',
                'study_plan_url': 'VARCHAR',
                'others_1_url': 'VARCHAR',
                'others_2_url': 'VARCHAR',
                'target_university_id': 'INTEGER',
                'target_major_id': 'INTEGER',
                'target_intake_id': 'INTEGER',
                'study_level': 'VARCHAR',
                'scholarship_preference': 'VARCHAR',
                'application_stage': 'VARCHAR',
                'missing_documents': 'TEXT',
                # COVA fields
                'home_address': 'TEXT',
                'current_address': 'TEXT',
                'emergency_contact_name': 'VARCHAR',
                'emergency_contact_phone': 'VARCHAR',
                'emergency_contact_relationship': 'VARCHAR',
                'education_history': 'JSONB',
                'employment_history': 'JSONB',
                'family_members': 'JSONB',
                'planned_arrival_date': 'TIMESTAMP WITH TIME ZONE',
                'intended_address_china': 'TEXT',
                'previous_visa_china': 'BOOLEAN',
                'previous_visa_details': 'TEXT',
                'previous_travel_to_china': 'BOOLEAN',
                'previous_travel_details': 'TEXT'
            }
            
            for col_name, col_type in new_columns.items():
                if col_name not in student_columns:
                    try:
                        # Handle JSONB and BOOLEAN fields
                        if col_type == 'JSONB':
                            conn.execute(text(f"""
                                ALTER TABLE students 
                                ADD COLUMN IF NOT EXISTS {col_name} {col_type}
                            """))
                            conn.commit()
                            print(f"✓ Added {col_name} ({col_type})")
                        elif col_type == 'BOOLEAN':
                            conn.execute(text(f"""
                                ALTER TABLE students 
                                ADD COLUMN IF NOT EXISTS {col_name} {col_type} DEFAULT FALSE
                            """))
                            conn.commit()
                            print(f"✓ Added {col_name} ({col_type})")
                        elif 'INTEGER' in col_type and 'target' in col_name:
                            # Add foreign key constraint
                            try:
                                conn.execute(text(f"""
                                    ALTER TABLE students 
                                    ADD COLUMN IF NOT EXISTS {col_name} {col_type}
                                """))
                                conn.commit()
                                
                                # Add foreign key constraint (PostgreSQL doesn't support IF NOT EXISTS for constraints)
                                constraint_name = f"fk_students_{col_name}"
                                if 'university' in col_name:
                                    try:
                                        conn.execute(text(f"""
                                            ALTER TABLE students
                                            ADD CONSTRAINT {constraint_name}
                                            FOREIGN KEY (target_university_id) REFERENCES universities(id)
                                        """))
                                        conn.commit()
                                    except Exception as e:
                                        if 'already exists' not in str(e).lower():
                                            print(f"Note: Constraint may already exist: {e}")
                                elif 'major' in col_name:
                                    try:
                                        conn.execute(text(f"""
                                            ALTER TABLE students
                                            ADD CONSTRAINT {constraint_name}
                                            FOREIGN KEY (target_major_id) REFERENCES majors(id)
                                        """))
                                        conn.commit()
                                    except Exception as e:
                                        if 'already exists' not in str(e).lower():
                                            print(f"Note: Constraint may already exist: {e}")
                                elif 'intake' in col_name:
                                    try:
                                        conn.execute(text(f"""
                                            ALTER TABLE students
                                            ADD CONSTRAINT {constraint_name}
                                            FOREIGN KEY (target_intake_id) REFERENCES program_intakes(id)
                                        """))
                                        conn.commit()
                                    except Exception as e:
                                        if 'already exists' not in str(e).lower():
                                            print(f"Note: Constraint may already exist: {e}")
                            except Exception as e:
                                if 'already exists' not in str(e).lower() and 'duplicate' not in str(e).lower():
                                    print(f"Note: Column {col_name} may already exist or error: {e}")
                                conn.rollback()
                        else:
                            conn.execute(text(f"""
                                ALTER TABLE students 
                                ADD COLUMN IF NOT EXISTS {col_name} {col_type}
                            """))
                        print(f"✓ Added column: {col_name}")
                    except Exception as e:
                        print(f"Note: Column {col_name} may already exist or error: {e}")
            
            conn.commit()
            print("✓ Students table migration completed")
        
        # Migrate leads table - add new columns
        if 'leads' in existing_tables:
            print("\nMigrating leads table...")
            lead_columns = [col['name'] for col in inspector.get_columns('leads')]
            
            if 'interested_university_id' not in lead_columns:
                try:
                    conn.execute(text("""
                        ALTER TABLE leads 
                        ADD COLUMN IF NOT EXISTS interested_university_id INTEGER
                    """))
                    conn.commit()
                    try:
                        conn.execute(text("""
                            ALTER TABLE leads
                            ADD CONSTRAINT fk_leads_university
                            FOREIGN KEY (interested_university_id) REFERENCES universities(id)
                        """))
                        conn.commit()
                    except Exception as e:
                        if 'already exists' not in str(e).lower():
                            print(f"Note: Constraint may already exist: {e}")
                        conn.rollback()
                    print("✓ Added interested_university_id to leads")
                except Exception as e:
                    if 'already exists' not in str(e).lower():
                        print(f"Note: {e}")
                    conn.rollback()
            
            if 'interested_major_id' not in lead_columns:
                try:
                    conn.execute(text("""
                        ALTER TABLE leads 
                        ADD COLUMN IF NOT EXISTS interested_major_id INTEGER
                    """))
                    conn.commit()
                    try:
                        conn.execute(text("""
                            ALTER TABLE leads
                            ADD CONSTRAINT fk_leads_major
                            FOREIGN KEY (interested_major_id) REFERENCES majors(id)
                        """))
                        conn.commit()
                    except Exception as e:
                        if 'already exists' not in str(e).lower():
                            print(f"Note: Constraint may already exist: {e}")
                        conn.rollback()
                    print("✓ Added interested_major_id to leads")
                except Exception as e:
                    if 'already exists' not in str(e).lower():
                        print(f"Note: {e}")
                    conn.rollback()
            
            if 'notes' not in lead_columns:
                try:
                    conn.execute(text("""
                        ALTER TABLE leads 
                        ADD COLUMN IF NOT EXISTS notes TEXT
                    """))
                    conn.commit()
                    print("✓ Added notes to leads")
                except Exception as e:
                    if 'already exists' not in str(e).lower():
                        print(f"Note: {e}")
                    conn.rollback()
            
            conn.commit()
            print("✓ Leads table migration completed")
        
        print("\n✅ Migration completed successfully!")

if __name__ == "__main__":
    try:
        migrate_new_tables()
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

