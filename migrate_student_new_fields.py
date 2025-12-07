"""
Migration script to add new fields to students table:
- highest_degree_medium (enum: English, Chinese, Native)
- marital_status (enum: Single, Married)
- religion (enum: Islam, Christianity, Catholicism, Buddhism, Other, No Religion)
- occupation (text)
- hsk_score (float, replaces hsk_level)
- hsk_certificate_date (date)
- hskk_level (enum: Beginner, Elementary, Intermediate, Advanced)
- hskk_score (float)
- number_of_published_papers (integer)

And remove:
- hsk_level (integer)

Also change highest_degree_name from enum to text (already text, but ensure it's editable)
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

def get_session():
    """Get a new session, reconnecting if needed"""
    return Session()

try:
    session = get_session()
    print("Starting migration...")
    
    # Create enum types
    print("Creating enum types...")
    
    # DegreeMedium enum
    try:
        session = get_session()
        session.execute(text("""
            DO $$ BEGIN
                CREATE TYPE degreemedium AS ENUM ('English', 'Chinese', 'Native');
            EXCEPTION
                WHEN duplicate_object THEN null;
            END $$;
        """))
        session.commit()
        session.close()
        print("  - Created degreemedium enum")
    except Exception as e:
        try:
            session.rollback()
            session.close()
        except:
            pass
        print(f"  - degreemedium enum may already exist: {e}")
    
    # MaritalStatus enum
    try:
        session.execute(text("""
            DO $$ BEGIN
                CREATE TYPE maritalstatus AS ENUM ('Single', 'Married');
            EXCEPTION
                WHEN duplicate_object THEN null;
            END $$;
        """))
        session.commit()
        print("  - Created maritalstatus enum")
    except Exception as e:
        session.rollback()
        print(f"  - maritalstatus enum may already exist: {e}")
    
    # Religion enum
    try:
        session.execute(text("""
            DO $$ BEGIN
                CREATE TYPE religion AS ENUM ('Islam', 'Christianity', 'Catholicism', 'Buddhism', 'Other', 'No Religion');
            EXCEPTION
                WHEN duplicate_object THEN null;
            END $$;
        """))
        session.commit()
        print("  - Created religion enum")
    except Exception as e:
        session.rollback()
        print(f"  - religion enum may already exist: {e}")
    
    # HSKKLevel enum
    try:
        session.execute(text("""
            DO $$ BEGIN
                CREATE TYPE hskklevel AS ENUM ('Beginner', 'Elementary', 'Intermediate', 'Advanced');
            EXCEPTION
                WHEN duplicate_object THEN null;
            END $$;
        """))
        session.commit()
        print("  - Created hskklevel enum")
    except Exception as e:
        session.rollback()
        print(f"  - hskklevel enum may already exist: {e}")
    
    # Add new columns
    print("Adding new columns...")
    
    columns_to_add = [
        ("highest_degree_medium", "degreemedium", "enum"),
        ("marital_status", "maritalstatus", "enum"),
        ("religion", "religion", "enum"),
        ("occupation", "VARCHAR", "text"),
        ("hsk_score", "FLOAT", "float"),
        ("hsk_certificate_date", "TIMESTAMP", "date"),
        ("hskk_level", "hskklevel", "enum"),
        ("hskk_score", "FLOAT", "float"),
        ("number_of_published_papers", "INTEGER", "integer"),
    ]
    
    for col_name, col_type, col_kind in columns_to_add:
        try:
            session = get_session()
            session.execute(text(f"""
                ALTER TABLE students 
                ADD COLUMN IF NOT EXISTS {col_name} {col_type};
            """))
            session.commit()
            session.close()
            print(f"  - Added column {col_name}")
        except Exception as e:
            try:
                session.rollback()
                session.close()
            except:
                pass
            # Check if column already exists
            if "already exists" in str(e).lower() or "duplicate" in str(e).lower():
                print(f"  - Column {col_name} already exists")
            else:
                print(f"  - Error adding column {col_name}: {e}")
    
    # Migrate hsk_level to hsk_score if hsk_level exists and hsk_score is null
    print("Migrating hsk_level to hsk_score...")
    session.execute(text("""
        UPDATE students 
        SET hsk_score = hsk_level 
        WHERE hsk_level IS NOT NULL AND hsk_score IS NULL;
    """))
    
    # Drop hsk_level column
    print("Dropping hsk_level column...")
    session.execute(text("""
        ALTER TABLE students 
        DROP COLUMN IF EXISTS hsk_level;
    """))
    
    session.commit()
    print("Migration completed successfully!")
    
except Exception as e:
    session.rollback()
    print(f"ERROR: Migration failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
finally:
    session.close()

