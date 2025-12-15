"""
Migration script to add new fields to universities and majors tables:
- Universities: name_cn, aliases, world_ranking_band, national_ranking, project_tags, default_currency, is_active
- Majors: name_cn, is_active, category, keywords
"""
import psycopg2
from dotenv import load_dotenv
import os

load_dotenv()

def migrate():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable not set.")
    
    # Use the connection string directly with connection parameters
    try:
        conn = psycopg2.connect(
            database_url,
            connect_timeout=10,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=5
        )
        conn.autocommit = False
        cursor = conn.cursor()
    except psycopg2.OperationalError as e:
        print(f"❌ Failed to connect to database: {e}")
        print("\n💡 Alternative: You can run the SQL migration script directly:")
        print("   Run: migrate_universities_majors_new_fields.sql")
        raise
    
    try:
        print("Starting migration: Add new fields to universities and majors tables...")
        print("=" * 60)
        
        # ========== UNIVERSITIES TABLE ==========
        print("\n📚 Migrating universities table...")
        
        # Check and add columns
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='universities' AND column_name='name_cn'
        """)
        if not cursor.fetchone():
            print("  Adding name_cn column...")
            cursor.execute("ALTER TABLE universities ADD COLUMN name_cn VARCHAR")
            print("  ✅ Added name_cn")
        else:
            print("  ⏭️  name_cn already exists")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='universities' AND column_name='aliases'
        """)
        if not cursor.fetchone():
            print("  Adding aliases column (JSONB)...")
            cursor.execute("ALTER TABLE universities ADD COLUMN aliases JSONB")
            print("  ✅ Added aliases")
        else:
            print("  ⏭️  aliases already exists")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='universities' AND column_name='world_ranking_band'
        """)
        if not cursor.fetchone():
            print("  Adding world_ranking_band column...")
            cursor.execute("ALTER TABLE universities ADD COLUMN world_ranking_band VARCHAR")
            print("  ✅ Added world_ranking_band")
        else:
            print("  ⏭️  world_ranking_band already exists")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='universities' AND column_name='national_ranking'
        """)
        if not cursor.fetchone():
            print("  Adding national_ranking column...")
            cursor.execute("ALTER TABLE universities ADD COLUMN national_ranking INTEGER")
            print("  ✅ Added national_ranking")
        else:
            print("  ⏭️  national_ranking already exists")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='universities' AND column_name='project_tags'
        """)
        if not cursor.fetchone():
            print("  Adding project_tags column (JSONB)...")
            cursor.execute("ALTER TABLE universities ADD COLUMN project_tags JSONB")
            print("  ✅ Added project_tags")
        else:
            print("  ⏭️  project_tags already exists")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='universities' AND column_name='default_currency'
        """)
        if not cursor.fetchone():
            print("  Adding default_currency column...")
            cursor.execute("ALTER TABLE universities ADD COLUMN default_currency VARCHAR DEFAULT 'CNY'")
            cursor.execute("UPDATE universities SET default_currency = 'CNY' WHERE default_currency IS NULL")
            print("  ✅ Added default_currency")
        else:
            print("  ⏭️  default_currency already exists")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='universities' AND column_name='is_active'
        """)
        if not cursor.fetchone():
            print("  Adding is_active column...")
            cursor.execute("ALTER TABLE universities ADD COLUMN is_active BOOLEAN DEFAULT TRUE")
            cursor.execute("UPDATE universities SET is_active = TRUE WHERE is_active IS NULL")
            print("  ✅ Added is_active")
        else:
            print("  ⏭️  is_active already exists")
        
        # ========== MAJORS TABLE ==========
        print("\n🎓 Migrating majors table...")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='majors' AND column_name='name_cn'
        """)
        if not cursor.fetchone():
            print("  Adding name_cn column...")
            cursor.execute("ALTER TABLE majors ADD COLUMN name_cn VARCHAR")
            print("  ✅ Added name_cn")
        else:
            print("  ⏭️  name_cn already exists")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='majors' AND column_name='is_active'
        """)
        if not cursor.fetchone():
            print("  Adding is_active column...")
            cursor.execute("ALTER TABLE majors ADD COLUMN is_active BOOLEAN DEFAULT TRUE")
            cursor.execute("UPDATE majors SET is_active = TRUE WHERE is_active IS NULL")
            print("  ✅ Added is_active")
        else:
            print("  ⏭️  is_active already exists")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='majors' AND column_name='category'
        """)
        if not cursor.fetchone():
            print("  Adding category column...")
            cursor.execute("ALTER TABLE majors ADD COLUMN category VARCHAR")
            print("  ✅ Added category")
        else:
            print("  ⏭️  category already exists")
        
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='majors' AND column_name='keywords'
        """)
        if not cursor.fetchone():
            print("  Adding keywords column (JSONB)...")
            cursor.execute("ALTER TABLE majors ADD COLUMN keywords JSONB")
            print("  ✅ Added keywords")
        else:
            print("  ⏭️  keywords already exists")
        
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
