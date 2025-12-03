"""
Database initialization script
Run this to create tables and enable pgvector extension
"""
from sqlalchemy import text
from app.database import engine, Base
from app.models import *

def init_database():
    """Initialize database with tables and pgvector extension"""
    # Enable pgvector extension
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
    
    # Create all tables
    Base.metadata.create_all(bind=engine)
    print("Database initialized successfully!")

if __name__ == "__main__":
    init_database()

