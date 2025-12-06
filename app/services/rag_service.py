from sqlalchemy.orm import Session
from sqlalchemy import text
from app.models import RAGDocument, RAGEmbedding
from app.services.openai_service import OpenAIService
from typing import List, Dict, Optional
import numpy as np

class RAGService:
    def __init__(self):
        self.openai_service = OpenAIService()
        self.top_k = 5  # Default top_k for retrieval
    
    def search_similar(self, db: Session, query: str, top_k: int = None) -> List[Dict]:
        """Search for similar documents using vector similarity"""
        if top_k is None:
            top_k = self.top_k
        
        # Generate query embedding - handle errors gracefully
        try:
            query_embedding = self.openai_service.generate_embedding(query)
        except Exception as e:
            # If embedding generation fails (e.g., regional restriction), return empty results
            print(f"RAG search skipped due to embedding error: {e}")
            return []
        
        # Convert to PostgreSQL array format string (safe since it's numeric)
        embedding_str = '[' + ','.join(map(str, query_embedding)) + ']'
        
        # Vector similarity search using pgvector
        # Note: We embed the embedding string directly since it's numeric data we generate
        # and pgvector requires the ::vector cast which doesn't work with SQLAlchemy parameters
        # Database column is 'metadata' (Python attribute is 'meta_data')
        query_sql = text(f"""
            SELECT 
                e.id,
                e.chunk_text,
                e.metadata,
                e.document_id,
                d.filename,
                d.metadata as doc_metadata,
                1 - (e.embedding <=> '{embedding_str}'::vector) as similarity
            FROM rag_embeddings e
            JOIN rag_documents d ON e.document_id = d.id
            ORDER BY e.embedding <=> '{embedding_str}'::vector
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
    
    def format_rag_context(self, results: List[Dict]) -> str:
        """Format RAG search results into context string"""
        if not results:
            return "No relevant information found in knowledge base."
        
        context = "Relevant Information from Knowledge Base:\n\n"
        for i, result in enumerate(results, 1):
            context += f"Source {i} ({result.get('filename', 'Unknown')}):\n"
            if result.get('doc_metadata'):
                context += f"Metadata: {result['doc_metadata']}\n"
            context += f"{result['chunk_text']}\n"
            context += f"(Similarity: {result['similarity']:.2f})\n\n"
        
        return context
    
    def create_embeddings_for_document(
        self, 
        db: Session, 
        document_id: int, 
        chunks: List[str],
        metadata: Optional[Dict] = None
    ):
        """Create embeddings for document chunks"""
        # Generate embeddings in batch
        embeddings = self.openai_service.generate_embeddings_batch(chunks)
        
        # Store embeddings
        for idx, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            rag_embedding = RAGEmbedding(
                document_id=document_id,
                chunk_text=chunk,
                embedding=embedding,
                chunk_index=idx,
                metadata=metadata or {}
            )
            db.add(rag_embedding)
        
        db.commit()

