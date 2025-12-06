"""
Document Verification Router
Handles document verification using OpenAI Vision API
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from app.database import get_db
from app.models import Student, User, StudentDocument, DocumentType
from app.routers.auth import get_current_user
from app.services.document_verification_service import DocumentVerificationService
from app.services.r2_service import R2Service
import base64
import io

router = APIRouter()

verification_service = DocumentVerificationService()
r2_service = R2Service()

class VerifyDocumentRequest(BaseModel):
    file_url: str
    doc_type: str

class VerifyDocumentResponse(BaseModel):
    status: str  # "ok" | "blurry" | "fake" | "incomplete"
    reason: str
    extracted: dict

@router.post("/verify", response_model=VerifyDocumentResponse)
async def verify_document(
    request: VerifyDocumentRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Verify a document using OpenAI Vision API
    """
    # Get student
    student = db.query(Student).filter(Student.user_id == current_user.id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student profile not found")
    
    # Verify document
    result = verification_service.verify_document(
        file_url=request.file_url,
        doc_type=request.doc_type
    )
    
    return VerifyDocumentResponse(
        status=result["status"],
        reason=result["reason"],
        extracted=result.get("extracted", {})
    )

@router.post("/verify-and-upload")
async def verify_and_upload_document(
    doc_type: str = Form(...),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Verify document first, then upload to Cloudflare if verified
    Flow:
    1. Upload file temporarily to get URL
    2. Verify using Vision API
    3. If status=ok, upload to "verified" folder in R2
    4. Save to student_documents table
    """
    # Get student
    student = db.query(Student).filter(Student.user_id == current_user.id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student profile not found")
    
    # Read file content
    file_content = await file.read()
    file_size = len(file_content)
    
    print(f"\n📤 FILE UPLOAD REQUEST:")
    print(f"{'='*80}")
    print(f"📄 Document Type: {doc_type}")
    print(f"📁 Filename: {file.filename}")
    print(f"📦 File Size: {file_size} bytes ({file_size / 1024:.2f} KB)")
    print(f"👤 Student ID: {student.id}")
    print(f"{'='*80}\n")
    
    if file_size == 0:
        raise HTTPException(status_code=400, detail="File is empty")
    
    # Check file size limit (1MB = 1048576 bytes)
    MAX_FILE_SIZE = 1048576  # 1MB
    if file_size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400, 
            detail=f"File size ({file_size / 1024:.2f} KB) exceeds maximum allowed size of 1MB. Please compress or resize the file."
        )
    
    # Upload temporarily to get URL for verification
    try:
        temp_url = r2_service.upload_file(
            file=file_content,
            filename=file.filename,
            folder="temp"
        )
    except Exception as e:
        error_msg = str(e)
        if "AccessDenied" in error_msg or "Access Denied" in error_msg:
            raise HTTPException(
                status_code=500,
                detail=f"R2 Storage Access Denied. Please check your R2 credentials and bucket permissions. Error: {error_msg}"
            )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to upload file to R2 storage: {error_msg}"
        )
    
    # Verify document using Vision API
    verification_result = verification_service.verify_document(
        file_url=temp_url,
        doc_type=doc_type,
        file_content=file_content
    )
    
    # Check verification status
    if verification_result["status"] != "ok":
        # Delete temp file - verification failed, don't keep it
        try:
            r2_service.delete_file(temp_url)
            print(f"🗑️  Deleted temp file after failed verification: {temp_url}")
        except Exception as e:
            print(f"⚠️  Warning: Could not delete temp file {temp_url}: {e}")
        
        # Return detailed error message
        error_message = f"Document verification failed: {verification_result.get('reason', 'Unknown reason')}"
        print(f"\n❌ VERIFICATION FAILED - Document NOT uploaded:")
        print(f"{'='*80}")
        print(f"Status: {verification_result['status']}")
        print(f"Reason: {verification_result.get('reason', 'No reason provided')}")
        print(f"{'='*80}\n")
        
        raise HTTPException(
            status_code=400,
            detail=error_message
        )
    
    # If verified, upload to "verified" folder
    try:
        verified_url = r2_service.upload_file(
            file=file_content,
            filename=file.filename,
            folder="verified"
        )
    except Exception as e:
        error_msg = str(e)
        # Try to delete temp file even if verified upload fails
        try:
            r2_service.delete_file(temp_url)
        except:
            pass
        if "AccessDenied" in error_msg or "Access Denied" in error_msg:
            raise HTTPException(
                status_code=500,
                detail=f"R2 Storage Access Denied. Please check your R2 credentials and bucket permissions. Error: {error_msg}"
            )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to upload verified file to R2 storage: {error_msg}"
        )
    
    # Delete temp file
    try:
        r2_service.delete_file(temp_url)
    except:
        pass
    
    # Save to student_documents table
    student_doc = StudentDocument(
        student_id=student.id,
        document_type=doc_type,
        file_url=temp_url,  # Keep original URL for reference
        r2_url=verified_url,  # Public URL from Cloudflare R2
        filename=file.filename,
        file_size=file_size,
        verification_status=verification_result["status"],
        verification_reason=verification_result["reason"],
        extracted_data=verification_result.get("extracted", {}),
        verified=True
    )
    db.add(student_doc)
    
    # Also update Student table URL fields based on document type
    from app.models import DocumentType
    try:
        doc_type_enum = DocumentType(doc_type)
        if doc_type_enum == DocumentType.PASSPORT:
            student.passport_scanned_url = verified_url
        elif doc_type_enum == DocumentType.PASSPORT_PAGE:
            student.passport_page_url = verified_url
        elif doc_type_enum == DocumentType.PHOTO:
            student.passport_photo_url = verified_url
        elif doc_type_enum == DocumentType.DIPLOMA:
            student.highest_degree_diploma_url = verified_url
        elif doc_type_enum == DocumentType.TRANSCRIPT:
            student.academic_transcript_url = verified_url
        elif doc_type_enum == DocumentType.NON_CRIMINAL:
            student.police_clearance_url = verified_url
        elif doc_type_enum == DocumentType.PHYSICAL_EXAM:
            student.physical_examination_form_url = verified_url
        elif doc_type_enum == DocumentType.BANK_STATEMENT:
            student.bank_statement_url = verified_url
        elif doc_type_enum == DocumentType.RECOMMENDATION_LETTER:
            if not student.recommendation_letter_1_url:
                student.recommendation_letter_1_url = verified_url
            elif not student.recommendation_letter_2_url:
                student.recommendation_letter_2_url = verified_url
        elif doc_type_enum == DocumentType.STUDY_PLAN:
            student.study_plan_url = verified_url
        elif doc_type_enum == DocumentType.ENGLISH_PROFICIENCY:
            student.english_certificate_url = verified_url
        elif doc_type_enum == DocumentType.CV_RESUME:
            student.cv_resume_url = verified_url
        elif doc_type_enum == DocumentType.JW202_JW201:
            student.jw202_jw201_url = verified_url
        elif doc_type_enum == DocumentType.GUARANTEE_LETTER:
            student.guarantee_letter_url = verified_url
        elif doc_type_enum == DocumentType.BANK_GUARANTOR_LETTER:
            student.bank_guarantor_letter_url = verified_url
    except ValueError:
        pass  # Invalid document type, skip Student table update
    
    db.commit()
    db.refresh(student_doc)
    
    return {
        "id": student_doc.id,
        "document_type": student_doc.document_type,
        "r2_url": student_doc.r2_url,
        "filename": student_doc.filename,
        "verification_status": student_doc.verification_status,
        "verification_reason": student_doc.verification_reason,
        "extracted_data": student_doc.extracted_data,
        "verified": student_doc.verified
    }

@router.get("/student-documents")
async def get_student_documents(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all verified documents for current student"""
    student = db.query(Student).filter(Student.user_id == current_user.id).first()
    if not student:
        return []
    
    documents = db.query(StudentDocument).filter(
        StudentDocument.student_id == student.id
    ).all()
    
    return [
        {
            "id": doc.id,
            "document_type": doc.document_type,
            "r2_url": doc.r2_url,
            "filename": doc.filename,
            "file_size": doc.file_size,
            "verification_status": doc.verification_status,
            "verification_reason": doc.verification_reason,
            "extracted_data": doc.extracted_data,
            "verified": doc.verified,
            "created_at": doc.created_at.isoformat() if doc.created_at else None
        }
        for doc in documents
    ]

@router.delete("/student-documents/{document_id}")
async def delete_student_document(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a verified document"""
    student = db.query(Student).filter(Student.user_id == current_user.id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student profile not found")
    
    document = db.query(StudentDocument).filter(
        StudentDocument.id == document_id,
        StudentDocument.student_id == student.id
    ).first()
    
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Delete from Cloudflare R2
    if document.r2_url:
        try:
            r2_service.delete_file(document.r2_url)
        except Exception as e:
            print(f"Error deleting file from R2: {e}")
            # Continue with DB deletion even if R2 deletion fails
    
    # Also clear the corresponding Student table URL field
    try:
        doc_type_enum = DocumentType(document.document_type)
        if doc_type_enum == DocumentType.PASSPORT:
            student.passport_scanned_url = None
        elif doc_type_enum == DocumentType.PASSPORT_PAGE:
            student.passport_page_url = None
        elif doc_type_enum == DocumentType.PHOTO:
            student.passport_photo_url = None
        elif doc_type_enum == DocumentType.DIPLOMA:
            student.highest_degree_diploma_url = None
        elif doc_type_enum == DocumentType.TRANSCRIPT:
            student.academic_transcript_url = None
        elif doc_type_enum == DocumentType.NON_CRIMINAL:
            student.police_clearance_url = None
        elif doc_type_enum == DocumentType.PHYSICAL_EXAM:
            student.physical_examination_form_url = None
        elif doc_type_enum == DocumentType.BANK_STATEMENT:
            student.bank_statement_url = None
        elif doc_type_enum == DocumentType.RECOMMENDATION_LETTER:
            if student.recommendation_letter_1_url == document.r2_url:
                student.recommendation_letter_1_url = None
            elif student.recommendation_letter_2_url == document.r2_url:
                student.recommendation_letter_2_url = None
        elif doc_type_enum == DocumentType.STUDY_PLAN:
            student.study_plan_url = None
        elif doc_type_enum == DocumentType.ENGLISH_PROFICIENCY:
            student.english_certificate_url = None
        elif doc_type_enum == DocumentType.CV_RESUME:
            student.cv_resume_url = None
        elif doc_type_enum == DocumentType.JW202_JW201:
            student.jw202_jw201_url = None
        elif doc_type_enum == DocumentType.GUARANTEE_LETTER:
            student.guarantee_letter_url = None
        elif doc_type_enum == DocumentType.BANK_GUARANTOR_LETTER:
            student.bank_guarantor_letter_url = None
    except ValueError:
        pass  # Invalid document type, skip Student table update
    
    # Delete from database
    db.delete(document)
    db.commit()
    
    return {"message": "Document deleted successfully"}

