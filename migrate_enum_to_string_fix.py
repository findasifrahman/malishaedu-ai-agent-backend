"""Migration script to properly convert enum columns to VARCHAR in majors and program_intakes tables"""
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
        print("Step 1: Checking current column types...")
        
        # Check majors.degree_level
        cursor.execute("""
            SELECT data_type, udt_name
            FROM information_schema.columns 
            WHERE table_name='majors' AND column_name='degree_level';
        """)
        result = cursor.fetchone()
        if result:
            print(f"majors.degree_level current type: {result[0]} ({result[1]})")
        
        # Check majors.teaching_language
        cursor.execute("""
            SELECT data_type, udt_name
            FROM information_schema.columns 
            WHERE table_name='majors' AND column_name='teaching_language';
        """)
        result = cursor.fetchone()
        if result:
            print(f"majors.teaching_language current type: {result[0]} ({result[1]})")
        
        print("\nStep 2: Converting majors.degree_level from enum to VARCHAR...")
        
        # Check if column is enum type (PostgreSQL shows enums as USER-DEFINED with udt_name)
        cursor.execute("""
            SELECT data_type, udt_name
            FROM information_schema.columns 
            WHERE table_name='majors' AND column_name='degree_level';
        """)
        col_info = cursor.fetchone()
        
        # Check if it's an enum: data_type is USER-DEFINED and udt_name doesn't contain 'varchar'
        is_enum = col_info and col_info[0] == 'USER-DEFINED' and 'varchar' not in col_info[1].lower()
        
        if is_enum:
            print("  - Column is enum type, converting...")
            # Add temporary VARCHAR column
            cursor.execute("""
                ALTER TABLE majors 
                ADD COLUMN IF NOT EXISTS degree_level_temp VARCHAR;
            """)
            # Copy data (convert enum to text)
            cursor.execute("""
                UPDATE majors 
                SET degree_level_temp = degree_level::text;
            """)
            # Drop old enum column
            cursor.execute("""
                ALTER TABLE majors 
                DROP COLUMN degree_level;
            """)
            # Rename temp column
            cursor.execute("""
                ALTER TABLE majors 
                RENAME COLUMN degree_level_temp TO degree_level;
            """)
            print("  - Conversion complete!")
        else:
            print("  - Column is already VARCHAR or different type")
        
        print("\nStep 3: Converting majors.teaching_language from enum to VARCHAR...")
        
        cursor.execute("""
            SELECT data_type, udt_name
            FROM information_schema.columns 
            WHERE table_name='majors' AND column_name='teaching_language';
        """)
        col_info = cursor.fetchone()
        
        is_enum = col_info and col_info[0] == 'USER-DEFINED' and 'varchar' not in col_info[1].lower()
        
        if is_enum:
            print("  - Column is enum type, converting...")
            # Add temporary VARCHAR column
            cursor.execute("""
                ALTER TABLE majors 
                ADD COLUMN IF NOT EXISTS teaching_language_temp VARCHAR;
            """)
            # Copy data (convert enum to text)
            cursor.execute("""
                UPDATE majors 
                SET teaching_language_temp = teaching_language::text;
            """)
            # Drop old enum column
            cursor.execute("""
                ALTER TABLE majors 
                DROP COLUMN teaching_language;
            """)
            # Rename temp column
            cursor.execute("""
                ALTER TABLE majors 
                RENAME COLUMN teaching_language_temp TO teaching_language;
            """)
            print("  - Conversion complete!")
        else:
            print("  - Column is already VARCHAR or different type")
        
        print("\nStep 4: Converting program_intakes.teaching_language from enum to VARCHAR...")
        
        cursor.execute("""
            SELECT data_type, udt_name
            FROM information_schema.columns 
            WHERE table_name='program_intakes' AND column_name='teaching_language';
        """)
        col_info = cursor.fetchone()
        
        is_enum = col_info and col_info[0] == 'USER-DEFINED' and 'varchar' not in col_info[1].lower()
        
        if is_enum:
            print("  - Column is enum type, converting...")
            cursor.execute("""
                ALTER TABLE program_intakes 
                ADD COLUMN IF NOT EXISTS teaching_language_temp VARCHAR;
            """)
            cursor.execute("""
                UPDATE program_intakes 
                SET teaching_language_temp = teaching_language::text;
            """)
            cursor.execute("""
                ALTER TABLE program_intakes 
                DROP COLUMN teaching_language;
            """)
            cursor.execute("""
                ALTER TABLE program_intakes 
                RENAME COLUMN teaching_language_temp TO teaching_language;
            """)
            print("  - Conversion complete!")
        else:
            print("  - Column is already VARCHAR or different type")
        
        print("\nStep 5: Converting program_intakes.degree_type from enum to VARCHAR...")
        
        cursor.execute("""
            SELECT data_type, udt_name
            FROM information_schema.columns 
            WHERE table_name='program_intakes' AND column_name='degree_type';
        """)
        col_info = cursor.fetchone()
        
        is_enum = col_info and col_info[0] == 'USER-DEFINED' and 'varchar' not in col_info[1].lower()
        
        if is_enum:
            print("  - Column is enum type, converting...")
            cursor.execute("""
                ALTER TABLE program_intakes 
                ADD COLUMN IF NOT EXISTS degree_type_temp VARCHAR;
            """)
            cursor.execute("""
                UPDATE program_intakes 
                SET degree_type_temp = degree_type::text;
            """)
            cursor.execute("""
                ALTER TABLE program_intakes 
                DROP COLUMN degree_type;
            """)
            cursor.execute("""
                ALTER TABLE program_intakes 
                RENAME COLUMN degree_type_temp TO degree_type;
            """)
            print("  - Conversion complete!")
        else:
            print("  - Column is already VARCHAR or different type")
        
        print("\nStep 6: Verifying column types after conversion...")
        
        cursor.execute("""
            SELECT column_name, data_type, udt_name
            FROM information_schema.columns 
            WHERE table_name='majors' AND column_name IN ('degree_level', 'teaching_language')
            ORDER BY column_name;
        """)
        for row in cursor.fetchall():
            print(f"  - majors.{row[0]}: {row[1]} ({row[2]})")
        
        cursor.execute("""
            SELECT column_name, data_type, udt_name
            FROM information_schema.columns 
            WHERE table_name='program_intakes' AND column_name IN ('teaching_language', 'degree_type')
            ORDER BY column_name;
        """)
        for row in cursor.fetchall():
            print(f"  - program_intakes.{row[0]}: {row[1]} ({row[2]})")
        
        conn.commit()
        print("\n✅ Migration completed successfully!")
        
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

