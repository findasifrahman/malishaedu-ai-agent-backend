"""
Migration: Replace rag_documents and rag_embeddings with filtered-retrieval schema
Creates rag_sources and rag_chunks tables with doc_type filtering
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text, inspect
from app.config import settings

def migrate():
    db_url = settings.DATABASE_URL
    if not db_url.startswith('postgresql://') and not db_url.startswith('postgresql+psycopg2://'):
        if db_url.startswith('postgres://'):
            db_url = db_url.replace('postgres://', 'postgresql+psycopg2://', 1)
        else:
            db_url = f'postgresql+psycopg2://{db_url}'
    engine = create_engine(db_url)
    
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            print("Creating rag_sources table...")
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS rag_sources (
                  id BIGSERIAL PRIMARY KEY,
                  name TEXT NOT NULL,
                  doc_type TEXT NOT NULL,
                  audience TEXT NOT NULL DEFAULT 'student',
                  version TEXT,
                  status TEXT NOT NULL DEFAULT 'active',
                  source_url TEXT,
                  last_verified_at TIMESTAMPTZ,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
            """))
            
            print("Creating indexes on rag_sources...")
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS rag_sources_doc_type_idx ON rag_sources (doc_type);
                CREATE INDEX IF NOT EXISTS rag_sources_status_idx ON rag_sources (status);
            """))
            
            print("Creating rag_chunks table...")
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS rag_chunks (
                  id BIGSERIAL PRIMARY KEY,
                  source_id BIGINT NOT NULL REFERENCES rag_sources(id) ON DELETE CASCADE,
                  chunk_index INT NOT NULL,
                  chunk_hash TEXT NOT NULL,
                  content TEXT NOT NULL,
                  embedding vector(1536) NOT NULL,
                  priority SMALLINT NOT NULL DEFAULT 3,
                  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  UNIQUE (source_id, chunk_index),
                  UNIQUE (chunk_hash)
                );
            """))
            
            print("Creating indexes on rag_chunks...")
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS rag_chunks_source_id_idx ON rag_chunks (source_id);
                CREATE INDEX IF NOT EXISTS rag_chunks_priority_idx ON rag_chunks (priority);
                CREATE INDEX IF NOT EXISTS rag_chunks_metadata_gin_idx ON rag_chunks USING GIN (metadata);
            """))
            
            print("Creating HNSW index on rag_chunks.embedding...")
            try:
                conn.execute(text("""
                    CREATE INDEX IF NOT EXISTS rag_chunks_embedding_hnsw_idx 
                    ON rag_chunks USING hnsw (embedding vector_cosine_ops);
                """))
            except Exception as e:
                print(f"Note: HNSW index creation may require pgvector extension: {e}")
                print("If pgvector is not installed, run: CREATE EXTENSION IF NOT EXISTS vector;")
            
            inspector = inspect(conn)
            existing_tables = inspector.get_table_names()
            
            # Check if new tables already have data
            result = conn.execute(text("SELECT COUNT(*) FROM rag_sources")).scalar()
            if result > 0:
                print(f"New tables already contain {result} sources. Skipping data migration.")
                print("If you want to re-migrate, please clear the new tables first:")
                print("  DELETE FROM rag_chunks;")
                print("  DELETE FROM rag_sources;")
            elif 'rag_documents' in existing_tables and 'rag_embeddings' in existing_tables:
                print("Migrating data from old tables...")
                conn.execute(text("""
                    INSERT INTO rag_sources (name, doc_type, audience, version, status, source_url, last_verified_at)
                    SELECT
                      COALESCE(d.filename, 'legacy_' || d.id::text) AS name,
                      COALESCE(d.metadata->>'doc_type', 'general') AS doc_type,
                      COALESCE(d.metadata->>'audience', 'student') AS audience,
                      d.metadata->>'version' AS version,
                      COALESCE(d.metadata->>'status', 'active') AS status,
                      d.metadata->>'source_url' AS source_url,
                      (d.metadata->>'last_verified_at')::timestamptz AS last_verified_at
                    FROM rag_documents d
                    WHERE NOT EXISTS (
                      SELECT 1 FROM rag_sources rs 
                      WHERE rs.name = COALESCE(d.filename, 'legacy_' || d.id::text)
                    );
                """))
                
                conn.execute(text("""
                    INSERT INTO rag_chunks (source_id, chunk_index, chunk_hash, content, embedding, priority, metadata)
                    SELECT
                      s.id AS source_id,
                      e.chunk_index,
                      md5(COALESCE(e.chunk_text,'') || '|' || COALESCE((e.metadata::text),'') || '|' || COALESCE(s.name,'')) AS chunk_hash,
                      e.chunk_text,
                      e.embedding,
                      COALESCE((e.metadata->>'priority')::smallint, 3) AS priority,
                      COALESCE(e.metadata::jsonb, '{}'::jsonb) || jsonb_build_object('legacy_document_id', e.document_id)
                    FROM rag_embeddings e
                    JOIN rag_documents d ON d.id = e.document_id
                    JOIN rag_sources s ON s.name = COALESCE(d.filename, 'legacy_' || d.id::text)
                    WHERE NOT EXISTS (
                      SELECT 1 FROM rag_chunks rc 
                      WHERE rc.source_id = s.id AND rc.chunk_index = e.chunk_index
                    )
                    AND NOT EXISTS (
                      SELECT 1 FROM rag_chunks rc 
                      WHERE rc.chunk_hash = md5(COALESCE(e.chunk_text,'') || '|' || COALESCE((e.metadata::text),'') || '|' || COALESCE(s.name,''))
                    );
                """))
                
                print("Migration of data completed. Old tables preserved for rollback.")
                print("To drop old tables after verification, run:")
                print("  DROP TABLE rag_embeddings;")
                print("  DROP TABLE rag_documents;")
            else:
                print("Old tables not found. Skipping data migration.")
            
            trans.commit()
            print("Migration completed successfully!")
            
        except Exception as e:
            trans.rollback()
            print(f"Migration failed: {e}")
            raise

if __name__ == "__main__":
    migrate()

