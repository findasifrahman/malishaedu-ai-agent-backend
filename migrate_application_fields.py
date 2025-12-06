"""
Migration script to add new fields to applications table:
- application_state (replaces/extends status)
- payment_fee_paid
- payment_fee_due
- payment_fee_required
- scholarship_preference
"""
from sqlalchemy import create_engine, text
from app.config import settings

def migrate():
    db_url = settings.DATABASE_URL
    # Ensure postgresql:// prefix
    if not db_url.startswith('postgresql://') and not db_url.startswith('postgresql+psycopg2://'):
        if db_url.startswith('postgres://'):
            db_url = db_url.replace('postgres://', 'postgresql+psycopg2://', 1)
        else:
            db_url = f'postgresql+psycopg2://{db_url}'
    engine = create_engine(db_url)
    
    with engine.connect() as conn:
        # Start transaction
        trans = conn.begin()
        try:
            # Add new columns
            print("Adding application_state column...")
            conn.execute(text("""
                ALTER TABLE applications 
                ADD COLUMN IF NOT EXISTS application_state VARCHAR(50) DEFAULT 'not_applied'
            """))
            
            print("Adding payment_fee_paid column...")
            conn.execute(text("""
                ALTER TABLE applications 
                ADD COLUMN IF NOT EXISTS payment_fee_paid FLOAT DEFAULT 0.0
            """))
            
            print("Adding payment_fee_due column...")
            conn.execute(text("""
                ALTER TABLE applications 
                ADD COLUMN IF NOT EXISTS payment_fee_due FLOAT DEFAULT 0.0
            """))
            
            print("Adding payment_fee_required column...")
            conn.execute(text("""
                ALTER TABLE applications 
                ADD COLUMN IF NOT EXISTS payment_fee_required FLOAT DEFAULT 0.0
            """))
            
            print("Adding scholarship_preference column...")
            conn.execute(text("""
                ALTER TABLE applications 
                ADD COLUMN IF NOT EXISTS scholarship_preference VARCHAR(50)
            """))
            
            # Migrate existing status to application_state
            print("Migrating existing status to application_state...")
            # Cast enum to text for comparison
            conn.execute(text("""
                UPDATE applications 
                SET application_state = CASE 
                    WHEN status::text = 'draft' THEN 'not_applied'
                    WHEN status::text = 'submitted' THEN 'applied'
                    WHEN status::text = 'under_review' THEN 'applied'
                    WHEN status::text = 'accepted' THEN 'succeeded'
                    WHEN status::text = 'rejected' THEN 'rejected'
                    ELSE 'not_applied'
                END
                WHERE application_state IS NULL OR application_state = 'not_applied'
            """))
            
            # Commit transaction
            trans.commit()
            print("Migration completed successfully!")
            
        except Exception as e:
            trans.rollback()
            print(f"Migration failed: {e}")
            raise

if __name__ == "__main__":
    migrate()
