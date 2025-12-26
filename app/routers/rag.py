from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from app.database import get_db
from app.models import RagSource, User, UserRole
from app.routers.auth import get_current_user
from app.services.rag_service import RAGService
from app.services.openai_service import OpenAIService
from app.services.document_parser import DocumentParser
import io
import json

router = APIRouter()

rag_service = RAGService()
openai_service = OpenAIService()
document_parser = DocumentParser()

def require_admin(current_user: User = Depends(get_current_user)):
    """Dependency to require admin role"""
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user

class RAGSearchRequest(BaseModel):
    query: str
    top_k: int = 5

class RAGSearchResponse(BaseModel):
    results: List[dict]
    count: int

def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> List[str]:
    """Split text into chunks with overlap"""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        chunks.append(chunk)
        start = end - overlap
    return chunks

class RAGTextUpload(BaseModel):
    text: str
    filename: Optional[str] = "plain_text.txt"
    metadata: Optional[str] = None

@router.post("/upload")
async def upload_rag_document(
    file: Optional[UploadFile] = File(None),
    text: Optional[str] = Form(None),
    metadata: Optional[str] = Form(None),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Upload a document or plain text to RAG system"""
    # Parse metadata
    doc_metadata = {}
    if metadata:
        try:
            doc_metadata = json.loads(metadata)
        except:
            pass
    
    # Get text content from file or plain text
    text_content = ""
    filename = "plain_text.txt"
    file_type = "txt"
    
    if text:
        # Plain text input
        text_content = text
        filename = doc_metadata.get("filename", "plain_text.txt")
    elif file:
        # File upload
        file_content = await file.read()
        filename = file.filename or "uploaded_file"
        file_type = filename.split('.')[-1].lower() if '.' in filename else "txt"
        
        if file_type == "pdf":
            text_content = document_parser.extract_text_from_pdf(file_content)
        elif file_type in ["doc", "docx"]:
            text_content = document_parser.extract_text_from_docx(file_content)
        elif file_type == "txt":
            text_content = file_content.decode('utf-8')
        elif file_type == "csv":
            import pandas as pd
            df = pd.read_csv(io.BytesIO(file_content))
            text_content = df.to_string()
        else:
            # Try to decode as text
            try:
                text_content = file_content.decode('utf-8')
            except:
                raise HTTPException(status_code=400, detail="Unsupported file type")
    else:
        raise HTTPException(status_code=400, detail="Either file or text must be provided")
    
    if not text_content.strip():
        raise HTTPException(status_code=400, detail="No text content provided")
    
    # Distill content using GPT to extract key information
    distilled = openai_service.distill_content(text_content, json.dumps(doc_metadata))
    
    # Use distilled text for embeddings (more focused and relevant)
    # But keep original text in content field for reference
    distilled_text = distilled if distilled else text_content
    
    # Use new ingestion method with filtered schema
    doc_type = doc_metadata.get('doc_type', 'b2c_study')
    audience = doc_metadata.get('audience', 'student')
    version = doc_metadata.get('version')
    source_url = doc_metadata.get('source_url')
    last_verified_at = doc_metadata.get('last_verified_at')
    
    try:
        result = rag_service.ingest_source(
            db=db,
            name=filename,
            doc_type=doc_type,
            full_text=distilled_text if distilled else text_content,
            audience=audience,
            version=version,
            source_url=source_url,
            last_verified_at=last_verified_at
        )
        
        return {
            "message": "Document uploaded and processed successfully",
            "source_id": result['source_id'],
            "filename": filename,
            "chunks_created": result['chunks_created'],
            "chunks_skipped": result['chunks_skipped'],
            "total_chunks": result['total_chunks'],
            "distilled": bool(distilled)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to ingest document: {str(e)}")

@router.post("/search", response_model=RAGSearchResponse)
async def search_rag(
    request: RAGSearchRequest,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Search RAG documents"""
    results = rag_service.search_similar(db, request.query, top_k=request.top_k)
    
    return RAGSearchResponse(
        results=results,
        count=len(results)
    )

@router.get("/documents")
async def list_rag_documents(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """List all RAG documents (from new rag_sources table)"""
    sources = db.query(RagSource).all()
    
    return [
        {
            "id": source.id,
            "name": source.name,
            "doc_type": source.doc_type,
            "audience": source.audience,
            "version": source.version,
            "status": source.status,
            "source_url": source.source_url,
            "last_verified_at": source.last_verified_at.isoformat() if source.last_verified_at else None,
            "created_at": source.created_at.isoformat() if source.created_at else None,
            "chunk_count": len(source.chunks) if source.chunks else 0
        }
        for source in sources
    ]

@router.get("/documents/{source_id}/content")
async def get_rag_document_content(
    source_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Get content of a RAG document by combining all chunks"""
    source = db.query(RagSource).filter(RagSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Get all chunks for this source, sorted by chunk_index
    chunks = sorted(source.chunks, key=lambda c: c.chunk_index)
    content = "\n\n".join([chunk.content for chunk in chunks])
    
    return {
        "id": source.id,
        "name": source.name,
        "doc_type": source.doc_type,
        "audience": source.audience,
        "version": source.version,
        "content": content,
        "chunk_count": len(chunks)
    }

@router.delete("/documents/{source_id}")
async def delete_rag_document(
    source_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Delete a RAG document (source and all its chunks)"""
    source = db.query(RagSource).filter(RagSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Delete source (chunks will be deleted via CASCADE)
    db.delete(source)
    db.commit()
    
    return {"message": "Document deleted successfully"}

