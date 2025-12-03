from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List
from app.database import get_db
from app.services.openai_service import OpenAIService

router = APIRouter()

openai_service = OpenAIService()

class EmbeddingRequest(BaseModel):
    text: str

class EmbeddingBatchRequest(BaseModel):
    texts: List[str]

class EmbeddingResponse(BaseModel):
    embedding: List[float]
    dimension: int

class EmbeddingBatchResponse(BaseModel):
    embeddings: List[List[float]]
    dimension: int
    count: int

@router.post("/", response_model=EmbeddingResponse)
async def create_embedding(
    request: EmbeddingRequest,
    db: Session = Depends(get_db)
):
    """Generate embedding for a single text"""
    embedding = openai_service.generate_embedding(request.text)
    
    return EmbeddingResponse(
        embedding=embedding,
        dimension=len(embedding)
    )

@router.post("/batch", response_model=EmbeddingBatchResponse)
async def create_embeddings_batch(
    request: EmbeddingBatchRequest,
    db: Session = Depends(get_db)
):
    """Generate embeddings for multiple texts"""
    embeddings = openai_service.generate_embeddings_batch(request.texts)
    
    return EmbeddingBatchResponse(
        embeddings=embeddings,
        dimension=len(embeddings[0]) if embeddings else 0,
        count=len(embeddings)
    )

