from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from app.database import get_db
from app.models import Student, User, Document, DocumentType, Application
from app.routers.auth import get_current_user
from app.services.r2_service import R2Service
from app.services.document_parser import DocumentParser
from datetime import datetime

router = APIRouter()

r2_service = R2Service()
document_parser = DocumentParser()

class DocumentResponse(BaseModel):
    id: int
    document_type: str
    filename: str
    r2_url: str
    verified: bool
    created_at: str

@router.post("/upload")
async def upload_document(
    document_type: str,
    application_id: Optional[int] = None,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Upload a document"""
    # Validate document type
    try:
        doc_type_enum = DocumentType(document_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid document type: {document_type}")
    
    # Get student
    student = db.query(Student).filter(Student.user_id == current_user.id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student profile not found")
    
    # Validate application if provided
    if application_id:
        application = db.query(Application).filter(
            Application.id == application_id,
            Application.student_id == student.id
        ).first()
        if not application:
            raise HTTPException(status_code=404, detail="Application not found")
    
    # Read file content
    file_content = await file.read()
    file_size = len(file_content)
    
    # Upload to R2 (pass bytes directly)
    r2_url = r2_service.upload_file(
        file=file_content,
        filename=file.filename,
        folder="documents"
    )
    
    # Parse document if passport
    extracted_data = {}
    if doc_type_enum == DocumentType.PASSPORT:
        extracted_data = document_parser.parse_passport(file_content, file.filename)
        
        # Update student profile with passport data
        if extracted_data.get("passport_number"):
            student.passport_number = extracted_data["passport_number"]
        if extracted_data.get("name"):
            student.passport_name = extracted_data["name"]
        if extracted_data.get("date_of_birth"):
            try:
                student.date_of_birth = datetime.fromisoformat(extracted_data["date_of_birth"])
            except:
                pass
        if extracted_data.get("nationality"):
            student.nationality = extracted_data["nationality"]
        if extracted_data.get("expiry_date"):
            try:
                student.passport_expiry = datetime.fromisoformat(extracted_data["expiry_date"])
            except:
                pass
        # Update passport_scanned_url
        student.passport_scanned_url = r2_url
    
    # Update Student table URL fields based on document type
    if doc_type_enum == DocumentType.PHOTO:
        student.passport_photo_url = r2_url
    elif doc_type_enum == DocumentType.DIPLOMA:
        student.highest_degree_diploma_url = r2_url
    elif doc_type_enum == DocumentType.TRANSCRIPT:
        student.academic_transcript_url = r2_url
    elif doc_type_enum == DocumentType.NON_CRIMINAL:
        student.police_clearance_url = r2_url
    elif doc_type_enum == DocumentType.PHYSICAL_EXAM:
        student.physical_examination_form_url = r2_url
    elif doc_type_enum == DocumentType.BANK_STATEMENT:
        student.bank_statement_url = r2_url
    elif doc_type_enum == DocumentType.RECOMMENDATION_LETTER:
        # Use recommendation_letter_1_url if empty, else recommendation_letter_2_url
        if not student.recommendation_letter_1_url:
            student.recommendation_letter_1_url = r2_url
        elif not student.recommendation_letter_2_url:
            student.recommendation_letter_2_url = r2_url
    elif doc_type_enum == DocumentType.STUDY_PLAN:
        student.study_plan_url = r2_url
    elif doc_type_enum == DocumentType.ENGLISH_PROFICIENCY:
        student.english_certificate_url = r2_url
    elif doc_type_enum == DocumentType.PASSPORT_PAGE:
        student.passport_page_url = r2_url
    elif doc_type_enum == DocumentType.CV_RESUME:
        student.cv_resume_url = r2_url
    elif doc_type_enum == DocumentType.JW202_JW201:
        student.jw202_jw201_url = r2_url
    elif doc_type_enum == DocumentType.GUARANTEE_LETTER:
        student.guarantee_letter_url = r2_url
    elif doc_type_enum == DocumentType.BANK_GUARANTOR_LETTER:
        student.bank_guarantor_letter_url = r2_url
    
    # Create document record
    document = Document(
        student_id=student.id,
        application_id=application_id,
        document_type=doc_type_enum,
        r2_url=r2_url,
        filename=file.filename,
        file_size=file_size,
        extracted_data=extracted_data,
        verified=False
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    
    return DocumentResponse(
        id=document.id,
        document_type=document.document_type.value,
        filename=document.filename,
        r2_url=document.r2_url,
        verified=document.verified,
        created_at=document.created_at.isoformat() if document.created_at else ""
    )

@router.get("/")
async def get_documents(
    application_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all documents for current student"""
    student = db.query(Student).filter(Student.user_id == current_user.id).first()
    if not student:
        return []
    
    query = db.query(Document).filter(Document.student_id == student.id)
    if application_id:
        query = query.filter(Document.application_id == application_id)
    
    documents = query.all()
    
    return [
        {
            "id": doc.id,
            "document_type": doc.document_type.value,
            "filename": doc.filename,
            "r2_url": doc.r2_url,
            "verified": doc.verified,
            "extracted_data": doc.extracted_data,
            "created_at": doc.created_at.isoformat() if doc.created_at else None
        }
        for doc in documents
    ]

@router.delete("/{document_id}")
async def delete_document(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a document"""
    student = db.query(Student).filter(Student.user_id == current_user.id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student profile not found")
    
    document = db.query(Document).filter(
        Document.id == document_id,
        Document.student_id == student.id
    ).first()
    
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Delete from R2
    r2_service.delete_file(document.r2_url)
    
    # Delete from database
    db.delete(document)
    db.commit()
    
    return {"message": "Document deleted successfully"}

