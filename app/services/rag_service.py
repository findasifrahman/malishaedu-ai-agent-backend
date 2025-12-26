from sqlalchemy.orm import Session
from sqlalchemy import text
from app.models import RagSource, RagChunk
from app.services.openai_service import OpenAIService
from typing import List, Dict, Optional
import hashlib
import tiktoken

class RAGService:
    def __init__(self):
        self.openai_service = OpenAIService()
        self.top_k = 3  # Default top_k for retrieval
        self.max_tokens = 1200  # Max combined context tokens
        self.encoding = tiktoken.get_encoding("cl100k_base")  # For token counting
    
    def embed_query(self, text: str) -> Optional[List[float]]:
        """Generate embedding for a query text"""
        try:
            return self.openai_service.generate_embedding(text)
        except Exception as e:
            print(f"RAG embedding generation failed: {e}")
            return None
    
    def retrieve(
        self, 
        db: Session, 
        query: str, 
        doc_type: str, 
        audience: Optional[str] = None, 
        top_k: int = 3
    ) -> List[Dict]:
        """
        Retrieve chunks filtered by doc_type and optionally audience.
        NEVER scans all chunks - always filters before vector search.
        Returns at most top_k chunks with max combined context of 1200 tokens.
        """
        try:
            if top_k is None:
                top_k = self.top_k
            
            # Generate query embedding
            query_embedding = self.embed_query(query)
            if not query_embedding:
                return []
            
            # Convert to PostgreSQL array format string
            embedding_str = '[' + ','.join(map(str, query_embedding)) + ']'
            
            # Build WHERE clause with filters - use parameterized queries for safety
            # embedding_str is safe to insert as literal since it's generated from our own data
            where_conditions = ["s.doc_type = :doc_type", "s.status = 'active'"]
            params = {"doc_type": doc_type, "top_k": top_k}
            
            if audience:
                # Allow exact audience match OR audience='Both' (if exists)
                where_conditions.append("(s.audience = :audience OR s.audience = 'Both')")
                params["audience"] = audience
            
            where_clause = " AND ".join(where_conditions)
            
            # Get highest version for this doc_type + audience combination
            # First, find the highest version (handle NULL versions)
            version_where = " AND ".join([wc for wc in where_conditions])
            version_params = {k: v for k, v in params.items() if k != "top_k"}
            version_query = text(f"""
                SELECT MAX(s.version) as max_version
                FROM rag_sources s
                WHERE {version_where}
            """)
            version_result = db.execute(version_query, version_params)
            max_version_row = version_result.fetchone()
            max_version = max_version_row[0] if max_version_row and max_version_row[0] else None
            
            # If version exists, filter by it; otherwise get all versions (including NULL)
            if max_version:
                where_conditions.append("(s.version = :max_version OR s.version IS NULL)")
                params["max_version"] = max_version
                where_clause = " AND ".join(where_conditions)
            
            # Vector similarity search with filtering
            # Use cosine distance with HNSW index (vector_cosine_ops)
            # Build SQL using string concatenation to avoid f-string parameter parsing issues
            # embedding_str is inserted as literal (safe - we control the value)
            # Other parameters use proper :param_name binding
            base_sql = """
                SELECT 
                    c.id,
                    c.content,
                    c.metadata,
                    c.source_id,
                    c.priority,
                    s.name as source_name,
                    s.doc_type,
                    s.audience,
                    s.version,
                    1 - (c.embedding <=> '""" + embedding_str + """'::vector) as similarity
                FROM rag_chunks c
                JOIN rag_sources s ON c.source_id = s.id
                WHERE """ + where_clause + """
                ORDER BY c.embedding <=> '""" + embedding_str + """'::vector, c.priority ASC
                LIMIT :top_k
            """
            
            query_sql = text(base_sql)
            result = db.execute(query_sql, params)
            
            chunks = []
            total_tokens = 0
            
            for row in result:
                chunk_text = row.content
                chunk_tokens = len(self.encoding.encode(chunk_text))
                
                # Stop if adding this chunk would exceed max tokens
                if total_tokens + chunk_tokens > self.max_tokens:
                    break
                
                chunks.append({
                    "id": row.id,
                    "content": chunk_text,
                    "metadata": row.metadata or {},
                    "source_id": row.source_id,
                    "source_name": row.source_name,
                    "doc_type": row.doc_type,
                    "audience": row.audience,
                    "version": row.version,
                    "priority": row.priority,
                    "similarity": float(row.similarity)
                })
                
                total_tokens += chunk_tokens
            
            return chunks
        except Exception as e:
            print(f"RAG retrieve error: {e}")
            # Rollback any failed transaction
            try:
                db.rollback()
            except:
                pass
            return []
    
    def format_rag_context(self, results: List[Dict]) -> str:
        """Format RAG search results into context string - summarize chunks, don't paste large blocks"""
        if not results:
            return "No relevant information found in knowledge base."
        
        context = "Relevant Information from Knowledge Base:\n\n"
        for i, result in enumerate(results, 1):
            source_name = result.get('source_name', 'Unknown')
            doc_type = result.get('doc_type', '')
            content = result.get('content', '')
            # Summarize if chunk is too long (>500 chars), otherwise use as-is
            if len(content) > 500:
                # Extract key sentences (first 300 chars + last 200 chars)
                summary = content[:300] + "...[truncated]..." + content[-200:] if len(content) > 500 else content
                context += f"Source {i} ({source_name}, {doc_type}):\n{summary}\n(Similarity: {result.get('similarity', 0):.2f})\n\n"
            else:
                context += f"Source {i} ({source_name}, {doc_type}):\n{content}\n(Similarity: {result.get('similarity', 0):.2f})\n\n"
        
        return context
    
    def chunk_text(
        self, 
        text: str, 
        chunk_size: int = 700, 
        overlap: int = 120
    ) -> List[str]:
        """
        Split text into chunks with overlap.
        Default: ~500-800 chars (approximately 700 chars) with 100-150 char overlap (120).
        """
        chunks = []
        start = 0
        text_length = len(text)
        
        while start < text_length:
            end = start + chunk_size
            chunk = text[start:end]
            chunks.append(chunk)
            start = end - overlap
            if start >= text_length:
                break
        
        return chunks
    
    def compute_chunk_hash(
        self, 
        chunk_text: str, 
        doc_type: str, 
        version: Optional[str] = None
    ) -> str:
        """Compute MD5 hash for chunk deduplication"""
        hash_input = f"{chunk_text}|{doc_type}|{version or ''}"
        return hashlib.md5(hash_input.encode('utf-8')).hexdigest()
    
    def ingest_source(
        self,
        db: Session,
        name: str,
        doc_type: str,
        full_text: str,
        audience: str = 'student',
        version: Optional[str] = None,
        source_url: Optional[str] = None,
        last_verified_at: Optional[str] = None,
        chunk_size: int = 700,
        overlap: int = 120
    ) -> Dict:
        """
        Ingest a source document into the RAG system.
        Chunks the text, computes embeddings, and stores with deduplication.
        Returns dict with source_id, chunks_created, chunks_skipped.
        """
        # Create or get source
        source = db.query(RagSource).filter(
            RagSource.name == name,
            RagSource.doc_type == doc_type,
            RagSource.audience == audience
        ).first()
        
        if not source:
            source = RagSource(
                name=name,
                doc_type=doc_type,
                audience=audience,
                version=version,
                status='active',
                source_url=source_url,
                last_verified_at=last_verified_at
            )
            db.add(source)
            db.flush()
        else:
            # Update source metadata
            if version:
                source.version = version
            if source_url:
                source.source_url = source_url
            if last_verified_at:
                source.last_verified_at = last_verified_at
            source.status = 'active'
            db.flush()
        
        # Chunk the text
        chunks = self.chunk_text(full_text, chunk_size=chunk_size, overlap=overlap)
        
        # Generate embeddings in batch
        try:
            embeddings = self.openai_service.generate_embeddings_batch(chunks)
        except Exception as e:
            print(f"Failed to generate embeddings: {e}")
            raise
        
        # Insert chunks with deduplication
        chunks_created = 0
        chunks_skipped = 0
        
        for idx, (chunk_text, embedding) in enumerate(zip(chunks, embeddings)):
            chunk_hash = self.compute_chunk_hash(chunk_text, doc_type, version)
            
            # Check if chunk already exists
            existing = db.query(RagChunk).filter(
                RagChunk.chunk_hash == chunk_hash
            ).first()
            
            if existing:
                chunks_skipped += 1
                continue
            
            # Create new chunk
            chunk = RagChunk(
                source_id=source.id,
                chunk_index=idx,
                chunk_hash=chunk_hash,
                content=chunk_text,
                embedding=embedding,
                priority=3,  # Default priority
                meta_data={}
            )
            db.add(chunk)
            chunks_created += 1
        
        db.commit()
        
        return {
            "source_id": source.id,
            "chunks_created": chunks_created,
            "chunks_skipped": chunks_skipped,
            "total_chunks": len(chunks)
        }
    
    # Legacy method for backward compatibility
    def search_similar(self, db: Session, query: str, top_k: int = None) -> List[Dict]:
        """
        Legacy method - searches across all doc_types.
        For new code, use retrieve() with specific doc_type.
        """
        if top_k is None:
            top_k = self.top_k
        
        query_embedding = self.embed_query(query)
        if not query_embedding:
            return []
        
        embedding_str = '[' + ','.join(map(str, query_embedding)) + ']'
        
        query_sql = text(f"""
            SELECT 
                c.id,
                c.content as chunk_text,
                c.metadata,
                c.source_id as document_id,
                s.name as filename,
                s.metadata as doc_metadata,
                1 - (c.embedding <=> '{embedding_str}'::vector) as similarity
            FROM rag_chunks c
            JOIN rag_sources s ON c.source_id = s.id
            WHERE s.status = 'active'
            ORDER BY c.embedding <=> '{embedding_str}'::vector
            LIMIT {top_k}
        """)
        
        result = db.execute(query_sql)
        
        results = []
        for row in result:
            results.append({
                "chunk_text": row.chunk_text,
                "metadata": row.metadata,
                "document_id": row.document_id,
                "filename": row.filename,
                "doc_metadata": row.doc_metadata,
                "similarity": float(row.similarity)
            })
        
        return results
    
    # Legacy method for backward compatibility
    def create_embeddings_for_document(
        self, 
        db: Session, 
        document_id: int, 
        chunks: List[str],
        metadata: Optional[Dict] = None
    ):
        """
        Legacy method - kept for backward compatibility.
        For new code, use ingest_source().
        """
        # This method is deprecated - use ingest_source instead
        print("WARNING: create_embeddings_for_document is deprecated. Use ingest_source() instead.")
        pass
