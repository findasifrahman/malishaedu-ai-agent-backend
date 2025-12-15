from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from app.database import get_db
from app.models import ProgramDocument, ProgramIntake, User
from app.routers.auth import get_current_user

router = APIRouter()

class ProgramDocumentCreate(BaseModel):
    program_intake_id: int
    name: str
    is_required: bool = True
    rules: Optional[str] = None
    applies_to: Optional[str] = None

class ProgramDocumentUpdate(BaseModel):
    name: Optional[str] = None
    is_required: Optional[bool] = None
    rules: Optional[str] = None
    applies_to: Optional[str] = None

class ProgramDocumentResponse(BaseModel):
    id: int
    program_intake_id: int
    name: str
    is_required: bool
    rules: Optional[str]
    applies_to: Optional[str]
    created_at: str
    updated_at: Optional[str]
    
    class Config:
        from_attributes = True

@router.get("/program-intakes/{intake_id}/documents")
async def list_program_documents(
    intake_id: int,
    db: Session = Depends(get_db)
):
    """Get all documents for a specific program intake"""
    # Verify program intake exists
    intake = db.query(ProgramIntake).filter(ProgramIntake.id == intake_id).first()
    if not intake:
        raise HTTPException(status_code=404, detail="Program intake not found")
    
    documents = db.query(ProgramDocument).filter(
        ProgramDocument.program_intake_id == intake_id
    ).order_by(ProgramDocument.name).all()
    
    return [
        {
            'id': doc.id,
            'program_intake_id': doc.program_intake_id,
            'name': doc.name,
            'is_required': doc.is_required,
            'rules': doc.rules,
            'applies_to': doc.applies_to,
            'created_at': doc.created_at.isoformat() if doc.created_at else None,
            'updated_at': doc.updated_at.isoformat() if doc.updated_at else None,
        }
        for doc in documents
    ]

@router.get("/{document_id}", response_model=ProgramDocumentResponse)
async def get_program_document(
    document_id: int,
    db: Session = Depends(get_db)
):
    """Get a specific program document by ID"""
    document = db.query(ProgramDocument).filter(ProgramDocument.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Program document not found")
    
    return {
        'id': document.id,
        'program_intake_id': document.program_intake_id,
        'name': document.name,
        'is_required': document.is_required,
        'rules': document.rules,
        'applies_to': document.applies_to,
        'created_at': document.created_at.isoformat() if document.created_at else None,
        'updated_at': document.updated_at.isoformat() if document.updated_at else None,
    }

@router.post("", response_model=ProgramDocumentResponse, status_code=status.HTTP_201_CREATED)
async def create_program_document(
    document_data: ProgramDocumentCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new program document (admin only)"""
    if current_user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Verify program intake exists
    intake = db.query(ProgramIntake).filter(ProgramIntake.id == document_data.program_intake_id).first()
    if not intake:
        raise HTTPException(status_code=404, detail="Program intake not found")
    
    document = ProgramDocument(**document_data.dict())
    db.add(document)
    db.commit()
    db.refresh(document)
    
    return {
        'id': document.id,
        'program_intake_id': document.program_intake_id,
        'name': document.name,
        'is_required': document.is_required,
        'rules': document.rules,
        'applies_to': document.applies_to,
        'created_at': document.created_at.isoformat() if document.created_at else None,
        'updated_at': document.updated_at.isoformat() if document.updated_at else None,
    }

@router.post("/", response_model=ProgramDocumentResponse, status_code=status.HTTP_201_CREATED)
async def create_program_document_with_slash(
    document_data: ProgramDocumentCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new program document (admin only) - with trailing slash"""
    return await create_program_document(document_data=document_data, current_user=current_user, db=db)

@router.post("/bulk")
async def create_program_documents_bulk(
    intake_id: int,
    documents: List[ProgramDocumentCreate],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create multiple program documents at once (admin only)"""
    if current_user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Verify program intake exists
    intake = db.query(ProgramIntake).filter(ProgramIntake.id == intake_id).first()
    if not intake:
        raise HTTPException(status_code=404, detail="Program intake not found")
    
    created_documents = []
    for doc_data in documents:
        doc_data.program_intake_id = intake_id  # Ensure intake_id matches
        document = ProgramDocument(**doc_data.dict())
        db.add(document)
        created_documents.append(document)
    
    db.commit()
    
    # Refresh all documents
    for doc in created_documents:
        db.refresh(doc)
    
    return [
        {
            'id': doc.id,
            'program_intake_id': doc.program_intake_id,
            'name': doc.name,
            'is_required': doc.is_required,
            'rules': doc.rules,
            'applies_to': doc.applies_to,
            'created_at': doc.created_at.isoformat() if doc.created_at else None,
            'updated_at': doc.updated_at.isoformat() if doc.updated_at else None,
        }
        for doc in created_documents
    ]

@router.put("/{document_id}", response_model=ProgramDocumentResponse)
async def update_program_document(
    document_id: int,
    document_data: ProgramDocumentUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update a program document (admin only)"""
    if current_user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    document = db.query(ProgramDocument).filter(ProgramDocument.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Program document not found")
    
    update_data = document_data.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(document, field, value)
    
    db.commit()
    db.refresh(document)
    
    return {
        'id': document.id,
        'program_intake_id': document.program_intake_id,
        'name': document.name,
        'is_required': document.is_required,
        'rules': document.rules,
        'applies_to': document.applies_to,
        'created_at': document.created_at.isoformat() if document.created_at else None,
        'updated_at': document.updated_at.isoformat() if document.updated_at else None,
    }

@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_program_document(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a program document (admin only)"""
    if current_user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    document = db.query(ProgramDocument).filter(ProgramDocument.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Program document not found")
    
    db.delete(document)
    db.commit()
    return None

