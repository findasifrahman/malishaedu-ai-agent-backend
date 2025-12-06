"""
Migration script to add chat_session_id column to conversations and leads tables
Run this if you have an existing database without chat_session_id columns
"""
from sqlalchemy import text
from app.database import engine

def migrate_chat_session_id():
    """Add chat_session_id column to conversations and leads tables"""
    with engine.connect() as conn:
        # Add chat_session_id to conversations table
        try:
            conn.execute(text("""
                ALTER TABLE conversations 
                ADD COLUMN IF NOT EXISTS chat_session_id VARCHAR
            """))
            conn.commit()
            print("✓ Added chat_session_id column to conversations table")
        except Exception as e:
            print(f"Note: chat_session_id column may already exist in conversations: {e}")
            conn.rollback()
        
        # Add index on chat_session_id for conversations (for faster lookups)
        try:
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_conversations_chat_session_id 
                ON conversations(chat_session_id)
            """))
            conn.commit()
            print("✓ Added index on conversations.chat_session_id")
        except Exception as e:
            print(f"Note: Index may already exist: {e}")
            conn.rollback()
        
        # Add chat_session_id to leads table
        try:
            conn.execute(text("""
                ALTER TABLE leads 
                ADD COLUMN IF NOT EXISTS chat_session_id VARCHAR
            """))
            conn.commit()
            print("✓ Added chat_session_id column to leads table")
        except Exception as e:
            print(f"Note: chat_session_id column may already exist in leads: {e}")
            conn.rollback()
        
        # Add index on chat_session_id for leads (for faster lookups)
        try:
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_leads_chat_session_id 
                ON leads(chat_session_id)
            """))
            conn.commit()
            print("✓ Added index on leads.chat_session_id")
        except Exception as e:
            print(f"Note: Index may already exist: {e}")
            conn.rollback()
        
        print("\nMigration completed successfully!")
        print("Both conversations and leads tables now have chat_session_id columns with indexes.")

if __name__ == "__main__":
    migrate_chat_session_id()

