"""
Migration script to add new fields to program_intakes table
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
        print("Starting migration: Add new fields to program_intakes table...")
        print("=" * 60)
        
        # ========== Program Start & Deadline ==========
        print("\n📅 Migrating Program Start & Deadline fields...")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='program_intakes' AND column_name='program_start_date'
        """)
        if not cursor.fetchone():
            print("  Adding program_start_date column...")
            cursor.execute("ALTER TABLE program_intakes ADD COLUMN program_start_date DATE")
            print("  ✅ Added program_start_date")
        else:
            print("  ⏭️  program_start_date already exists")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='program_intakes' AND column_name='deadline_type'
        """)
        if not cursor.fetchone():
            print("  Adding deadline_type column...")
            cursor.execute("ALTER TABLE program_intakes ADD COLUMN deadline_type VARCHAR")
            print("  ✅ Added deadline_type")
        else:
            print("  ⏭️  deadline_type already exists")
        
        # ========== Scholarship ==========
        print("\n💰 Migrating Scholarship fields...")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='program_intakes' AND column_name='scholarship_available'
        """)
        if not cursor.fetchone():
            print("  Adding scholarship_available column...")
            cursor.execute("ALTER TABLE program_intakes ADD COLUMN scholarship_available BOOLEAN")
            print("  ✅ Added scholarship_available")
        else:
            print("  ⏭️  scholarship_available already exists")
        
        # ========== Age Requirements ==========
        print("\n👤 Migrating Age Requirements fields...")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='program_intakes' AND column_name='age_min'
        """)
        if not cursor.fetchone():
            print("  Adding age_min column...")
            cursor.execute("ALTER TABLE program_intakes ADD COLUMN age_min INTEGER")
            print("  ✅ Added age_min")
        else:
            print("  ⏭️  age_min already exists")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='program_intakes' AND column_name='age_max'
        """)
        if not cursor.fetchone():
            print("  Adding age_max column...")
            cursor.execute("ALTER TABLE program_intakes ADD COLUMN age_max INTEGER")
            print("  ✅ Added age_max")
        else:
            print("  ⏭️  age_max already exists")
        
        # ========== Academic Requirements ==========
        print("\n📚 Migrating Academic Requirements fields...")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='program_intakes' AND column_name='min_average_score'
        """)
        if not cursor.fetchone():
            print("  Adding min_average_score column...")
            cursor.execute("ALTER TABLE program_intakes ADD COLUMN min_average_score FLOAT")
            print("  ✅ Added min_average_score")
        else:
            print("  ⏭️  min_average_score already exists")
        
        # ========== Test/Interview Requirements ==========
        print("\n📝 Migrating Test/Interview Requirements fields...")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='program_intakes' AND column_name='interview_required'
        """)
        if not cursor.fetchone():
            print("  Adding interview_required column...")
            cursor.execute("ALTER TABLE program_intakes ADD COLUMN interview_required BOOLEAN")
            print("  ✅ Added interview_required")
        else:
            print("  ⏭️  interview_required already exists")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='program_intakes' AND column_name='written_test_required'
        """)
        if not cursor.fetchone():
            print("  Adding written_test_required column...")
            cursor.execute("ALTER TABLE program_intakes ADD COLUMN written_test_required BOOLEAN")
            print("  ✅ Added written_test_required")
        else:
            print("  ⏭️  written_test_required already exists")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='program_intakes' AND column_name='acceptance_letter_required'
        """)
        if not cursor.fetchone():
            print("  Adding acceptance_letter_required column...")
            cursor.execute("ALTER TABLE program_intakes ADD COLUMN acceptance_letter_required BOOLEAN")
            print("  ✅ Added acceptance_letter_required")
        else:
            print("  ⏭️  acceptance_letter_required already exists")
        
        # ========== Inside China Applicants ==========
        print("\n🇨🇳 Migrating Inside China Applicants fields...")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='program_intakes' AND column_name='inside_china_applicants_allowed'
        """)
        if not cursor.fetchone():
            print("  Adding inside_china_applicants_allowed column...")
            cursor.execute("ALTER TABLE program_intakes ADD COLUMN inside_china_applicants_allowed BOOLEAN")
            print("  ✅ Added inside_china_applicants_allowed")
        else:
            print("  ⏭️  inside_china_applicants_allowed already exists")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='program_intakes' AND column_name='inside_china_extra_requirements'
        """)
        if not cursor.fetchone():
            print("  Adding inside_china_extra_requirements column...")
            cursor.execute("ALTER TABLE program_intakes ADD COLUMN inside_china_extra_requirements TEXT")
            print("  ✅ Added inside_china_extra_requirements")
        else:
            print("  ⏭️  inside_china_extra_requirements already exists")
        
        # ========== Bank Statement Requirements ==========
        print("\n💳 Migrating Bank Statement Requirements fields...")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='program_intakes' AND column_name='bank_statement_required'
        """)
        if not cursor.fetchone():
            print("  Adding bank_statement_required column...")
            cursor.execute("ALTER TABLE program_intakes ADD COLUMN bank_statement_required BOOLEAN")
            print("  ✅ Added bank_statement_required")
        else:
            print("  ⏭️  bank_statement_required already exists")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='program_intakes' AND column_name='bank_statement_amount'
        """)
        if not cursor.fetchone():
            print("  Adding bank_statement_amount column...")
            cursor.execute("ALTER TABLE program_intakes ADD COLUMN bank_statement_amount FLOAT")
            print("  ✅ Added bank_statement_amount")
        else:
            print("  ⏭️  bank_statement_amount already exists")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='program_intakes' AND column_name='bank_statement_currency'
        """)
        if not cursor.fetchone():
            print("  Adding bank_statement_currency column...")
            cursor.execute("ALTER TABLE program_intakes ADD COLUMN bank_statement_currency VARCHAR")
            print("  ✅ Added bank_statement_currency")
        else:
            print("  ⏭️  bank_statement_currency already exists")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='program_intakes' AND column_name='bank_statement_note'
        """)
        if not cursor.fetchone():
            print("  Adding bank_statement_note column...")
            cursor.execute("ALTER TABLE program_intakes ADD COLUMN bank_statement_note TEXT")
            print("  ✅ Added bank_statement_note")
        else:
            print("  ⏭️  bank_statement_note already exists")
        
        # ========== Language Requirements ==========
        print("\n🌐 Migrating Language Requirements fields...")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='program_intakes' AND column_name='hsk_required'
        """)
        if not cursor.fetchone():
            print("  Adding hsk_required column...")
            cursor.execute("ALTER TABLE program_intakes ADD COLUMN hsk_required BOOLEAN")
            print("  ✅ Added hsk_required")
        else:
            print("  ⏭️  hsk_required already exists")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='program_intakes' AND column_name='hsk_level'
        """)
        if not cursor.fetchone():
            print("  Adding hsk_level column...")
            cursor.execute("ALTER TABLE program_intakes ADD COLUMN hsk_level INTEGER")
            print("  ✅ Added hsk_level")
        else:
            print("  ⏭️  hsk_level already exists")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='program_intakes' AND column_name='hsk_min_score'
        """)
        if not cursor.fetchone():
            print("  Adding hsk_min_score column...")
            cursor.execute("ALTER TABLE program_intakes ADD COLUMN hsk_min_score INTEGER")
            print("  ✅ Added hsk_min_score")
        else:
            print("  ⏭️  hsk_min_score already exists")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='program_intakes' AND column_name='english_test_required'
        """)
        if not cursor.fetchone():
            print("  Adding english_test_required column...")
            cursor.execute("ALTER TABLE program_intakes ADD COLUMN english_test_required BOOLEAN")
            print("  ✅ Added english_test_required")
        else:
            print("  ⏭️  english_test_required already exists")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='program_intakes' AND column_name='english_test_note'
        """)
        if not cursor.fetchone():
            print("  Adding english_test_note column...")
            cursor.execute("ALTER TABLE program_intakes ADD COLUMN english_test_note TEXT")
            print("  ✅ Added english_test_note")
        else:
            print("  ⏭️  english_test_note already exists")
        
        # ========== Currency & Fee Periods ==========
        print("\n💵 Migrating Currency & Fee Periods fields...")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='program_intakes' AND column_name='currency'
        """)
        if not cursor.fetchone():
            print("  Adding currency column...")
            cursor.execute("ALTER TABLE program_intakes ADD COLUMN currency VARCHAR DEFAULT 'CNY'")
            cursor.execute("UPDATE program_intakes SET currency = 'CNY' WHERE currency IS NULL")
            print("  ✅ Added currency")
        else:
            print("  ⏭️  currency already exists")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='program_intakes' AND column_name='accommodation_fee_period'
        """)
        if not cursor.fetchone():
            print("  Adding accommodation_fee_period column...")
            cursor.execute("ALTER TABLE program_intakes ADD COLUMN accommodation_fee_period VARCHAR")
            print("  ✅ Added accommodation_fee_period")
        else:
            print("  ⏭️  accommodation_fee_period already exists")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='program_intakes' AND column_name='medical_insurance_fee_period'
        """)
        if not cursor.fetchone():
            print("  Adding medical_insurance_fee_period column...")
            cursor.execute("ALTER TABLE program_intakes ADD COLUMN medical_insurance_fee_period VARCHAR")
            print("  ✅ Added medical_insurance_fee_period")
        else:
            print("  ⏭️  medical_insurance_fee_period already exists")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='program_intakes' AND column_name='arrival_medical_checkup_is_one_time'
        """)
        if not cursor.fetchone():
            print("  Adding arrival_medical_checkup_is_one_time column...")
            cursor.execute("ALTER TABLE program_intakes ADD COLUMN arrival_medical_checkup_is_one_time BOOLEAN DEFAULT TRUE")
            cursor.execute("UPDATE program_intakes SET arrival_medical_checkup_is_one_time = TRUE WHERE arrival_medical_checkup_is_one_time IS NULL")
            print("  ✅ Added arrival_medical_checkup_is_one_time")
        else:
            print("  ⏭️  arrival_medical_checkup_is_one_time already exists")
        
        conn.commit()
        print("\n" + "=" * 60)
        print("✅ Migration completed successfully!")
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
