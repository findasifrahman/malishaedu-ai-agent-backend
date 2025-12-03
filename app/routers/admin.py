from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from pydantic import BaseModel
from typing import Optional, Dict, Any
from datetime import datetime, timedelta, timezone
from app.database import get_db
from app.models import (
    User, UserRole, Lead, Complaint, Student, Document, 
    Application, AdminSettings, DocumentType, ApplicationStatus, ProgramIntake
)
from app.routers.auth import get_current_user

router = APIRouter()

class ModelTuningParams(BaseModel):
    temperature: Optional[float] = None
    top_k: Optional[int] = None
    top_p: Optional[float] = None

def require_admin(current_user: User = Depends(get_current_user)):
    """Dependency to require admin role"""
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user

@router.get("/stats")
async def get_admin_stats(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Get admin dashboard statistics"""
    # Leads today
    today = datetime.utcnow().date()
    leads_today = db.query(Lead).filter(
        func.date(Lead.created_at) == today
    ).count()
    
    # Total leads
    total_leads = db.query(Lead).count()
    
    # Complaints
    pending_complaints = db.query(Complaint).filter(
        Complaint.status == "pending"
    ).count()
    total_complaints = db.query(Complaint).count()
    
    # Students
    total_students = db.query(Student).count()
    
    # Documents
    total_documents = db.query(Document).count()
    verified_documents = db.query(Document).filter(Document.verified == True).count()
    
    # Applications
    total_applications = db.query(Application).count()
    submitted_applications = db.query(Application).filter(
        Application.status == "submitted"
    ).count()
    
    # Document submission stats
    students_with_passport = db.query(Document).filter(
        Document.document_type == DocumentType.PASSPORT
    ).distinct(Document.student_id).count()
    
    students_without_diploma = db.query(Student).outerjoin(
        Document, and_(
            Document.student_id == Student.id,
            Document.document_type == DocumentType.DIPLOMA
        )
    ).filter(Document.id == None).count()
    
    return {
        "leads": {
            "today": leads_today,
            "total": total_leads
        },
        "complaints": {
            "pending": pending_complaints,
            "total": total_complaints
        },
        "students": {
            "total": total_students
        },
        "documents": {
            "total": total_documents,
            "verified": verified_documents
        },
        "applications": {
            "total": total_applications,
            "submitted": submitted_applications
        },
        "insights": {
            "students_with_passport": students_with_passport,
            "students_without_diploma": students_without_diploma
        }
    }

@router.get("/leads")
async def get_leads(
    days: int = 7,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Get leads for the last N days"""
    cutoff_date = datetime.utcnow() - timedelta(days=days)
    leads = db.query(Lead).filter(Lead.created_at >= cutoff_date).all()
    
    return [
        {
            "id": lead.id,
            "name": lead.name,
            "email": lead.email,
            "phone": lead.phone,
            "country": lead.country,
            "created_at": lead.created_at.isoformat() if lead.created_at else None
        }
        for lead in leads
    ]

@router.get("/complaints")
async def get_all_complaints(
    status: Optional[str] = None,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Get all complaints (admin only)"""
    query = db.query(Complaint)
    if status:
        query = query.filter(Complaint.status == status)
    
    complaints = query.order_by(Complaint.created_at.desc()).all()
    
    return [
        {
            "id": comp.id,
            "user_id": comp.user_id,
            "subject": comp.subject,
            "message": comp.message,
            "status": comp.status.value,
            "admin_response": comp.admin_response,
            "created_at": comp.created_at.isoformat() if comp.created_at else None,
            "resolved_at": comp.resolved_at.isoformat() if comp.resolved_at else None
        }
        for comp in complaints
    ]

@router.get("/students/progress")
async def get_student_progress(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Get student document submission progress"""
    students = db.query(Student).all()
    
    progress_data = []
    for student in students:
        documents = db.query(Document).filter(Document.student_id == student.id).all()
        submitted_types = {doc.document_type for doc in documents}
        
        required_docs = [
            DocumentType.PASSPORT,
            DocumentType.PHOTO,
            DocumentType.DIPLOMA,
            DocumentType.TRANSCRIPT,
            DocumentType.NON_CRIMINAL,
            DocumentType.PHYSICAL_EXAM,
            DocumentType.BANK_STATEMENT,
            DocumentType.RECOMMENDATION_LETTER,
            DocumentType.SELF_INTRO_VIDEO
        ]
        
        progress_data.append({
            "student_id": student.id,
            "user_id": student.user_id,
            "submitted": len(submitted_types),
            "total": len(required_docs),
            "percentage": (len(submitted_types) / len(required_docs)) * 100 if required_docs else 0
        })
    
    return progress_data

@router.post("/tune")
async def update_model_settings(
    params: ModelTuningParams,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Update model tuning parameters"""
    settings_dict = {}
    if params.temperature is not None:
        settings_dict["temperature"] = params.temperature
    if params.top_k is not None:
        settings_dict["top_k"] = params.top_k
    if params.top_p is not None:
        settings_dict["top_p"] = params.top_p
    
    # Store in admin_settings table
    for key, value in settings_dict.items():
        setting = db.query(AdminSettings).filter(
            AdminSettings.setting_key == key
        ).first()
        
        if setting:
            setting.setting_value = value
            setting.updated_by = current_user.id
        else:
            setting = AdminSettings(
                setting_key=key,
                setting_value=value,
                description=f"Model parameter: {key}",
                updated_by=current_user.id
            )
            db.add(setting)
    
    db.commit()
    
    return {"message": "Model settings updated", "settings": settings_dict}

@router.get("/settings")
async def get_model_settings(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Get current model settings"""
    settings = db.query(AdminSettings).all()
    
    return {
        setting.setting_key: setting.setting_value
        for setting in settings
    }

@router.get("/conversations")
async def get_conversations(
    user_id: Optional[int] = None,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Get conversations (admin can view all or filter by user_id)"""
    from app.models import Conversation
    
    query = db.query(Conversation)
    if user_id:
        query = query.filter(Conversation.user_id == user_id)
    
    conversations = query.order_by(Conversation.updated_at.desc()).limit(100).all()
    
    result = []
    for conv in conversations:
        user_info = None
        if conv.user_id:
            user = db.query(User).filter(User.id == conv.user_id).first()
            if user:
                user_info = {
                    "id": user.id,
                    "name": user.name,
                    "email": user.email
                }
        
        result.append({
            "id": conv.id,
            "user_id": conv.user_id,
            "user": user_info,
            "device_fingerprint": conv.device_fingerprint,
            "messages": conv.messages or [],
            "message_count": len(conv.messages) if conv.messages else 0,
            "created_at": conv.created_at.isoformat() if conv.created_at else None,
            "updated_at": conv.updated_at.isoformat() if conv.updated_at else None
        })
    
    return result

@router.get("/users")
async def get_all_users(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Get all users (students and admins)"""
    users = db.query(User).order_by(User.created_at.desc()).all()
    
    return [
        {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "phone": user.phone,
            "country": user.country,
            "role": user.role.value,
            "created_at": user.created_at.isoformat() if user.created_at else None
        }
        for user in users
    ]

@router.get("/applications")
async def get_all_applications(
    status: Optional[str] = None,
    university_id: Optional[int] = None,
    student_id: Optional[int] = None,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Get all student applications with filters"""
    query = db.query(Application)
    
    # Apply filters
    if status:
        try:
            status_enum = ApplicationStatus(status)
            query = query.filter(Application.status == status_enum)
        except ValueError:
            pass
    
    if student_id:
        query = query.filter(Application.student_id == student_id)
    
    if university_id:
        query = query.join(ProgramIntake).filter(ProgramIntake.university_id == university_id)
    
    applications = query.order_by(Application.created_at.desc()).all()
    
    result = []
    for app in applications:
        if app.program_intake:
            intake = app.program_intake
            result.append({
                "id": app.id,
                "student_id": app.student_id,
                "student_name": app.student.full_name if app.student else None,
                "student_email": app.student.user.email if app.student and app.student.user else None,
                "program_intake_id": app.program_intake_id,
                "university_name": intake.university.name,
                "major_name": intake.major.name,
                "intake_term": intake.intake_term.value,
                "intake_year": intake.intake_year,
                "application_fee": intake.application_fee,
                "application_fee_paid": app.application_fee_paid,
                "application_fee_amount": app.application_fee_amount,
                "status": app.status.value,
                "submitted_at": app.submitted_at.isoformat() if app.submitted_at else None,
                "admin_reviewed_at": app.admin_reviewed_at.isoformat() if app.admin_reviewed_at else None,
                "admin_notes": app.admin_notes,
                "result": app.result,
                "result_notes": app.result_notes,
                "created_at": app.created_at.isoformat() if app.created_at else None,
                "updated_at": app.updated_at.isoformat() if app.updated_at else None
            })
    
    return result

@router.get("/applications/{application_id}")
async def get_application_details(
    application_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Get detailed information about a specific application"""
    app = db.query(Application).filter(Application.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    
    if app.program_intake:
        intake = app.program_intake
        return {
            "id": app.id,
            "student_id": app.student_id,
            "student": {
                "id": app.student.id if app.student else None,
                "full_name": app.student.full_name if app.student else None,
                "email": app.student.user.email if app.student and app.student.user else None,
                "phone": app.student.phone if app.student else None,
                "country": app.student.country_of_citizenship if app.student else None
            },
            "program_intake": {
                "id": intake.id,
                "university_name": intake.university.name,
                "major_name": intake.major.name,
                "intake_term": intake.intake_term.value,
                "intake_year": intake.intake_year,
                "application_deadline": intake.application_deadline.isoformat() if intake.application_deadline else None,
                "documents_required": intake.documents_required,
                "tuition_per_semester": intake.tuition_per_semester,
                "tuition_per_year": intake.tuition_per_year,
                "application_fee": intake.application_fee,
                "accommodation_fee": intake.accommodation_fee
            },
            "application_fee_paid": app.application_fee_paid,
            "application_fee_amount": app.application_fee_amount,
            "status": app.status.value,
            "submitted_at": app.submitted_at.isoformat() if app.submitted_at else None,
            "admin_reviewed_at": app.admin_reviewed_at.isoformat() if app.admin_reviewed_at else None,
            "admin_notes": app.admin_notes,
            "result": app.result,
            "result_notes": app.result_notes,
            "created_at": app.created_at.isoformat() if app.created_at else None,
            "updated_at": app.updated_at.isoformat() if app.updated_at else None
        }
    raise HTTPException(status_code=404, detail="Application program intake not found")

class ApplicationUpdate(BaseModel):
    status: Optional[str] = None
    admin_notes: Optional[str] = None
    result: Optional[str] = None
    result_notes: Optional[str] = None
    application_fee_paid: Optional[bool] = None

@router.put("/applications/{application_id}")
async def update_application(
    application_id: int,
    update_data: ApplicationUpdate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Update application status, notes, or result"""
    app = db.query(Application).filter(Application.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    
    # Update status
    if update_data.status:
        try:
            app.status = ApplicationStatus(update_data.status)
            # If status is being set to submitted, set submitted_at
            if update_data.status == "submitted" and not app.submitted_at:
                app.submitted_at = datetime.now(timezone.utc)
            # If status is being set to under_review, set admin_reviewed_at
            if update_data.status == "under_review" and not app.admin_reviewed_at:
                app.admin_reviewed_at = datetime.now(timezone.utc)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {update_data.status}")
    
    # Update admin notes
    if update_data.admin_notes is not None:
        app.admin_notes = update_data.admin_notes
    
    # Update result
    if update_data.result is not None:
        app.result = update_data.result
    
    # Update result notes
    if update_data.result_notes is not None:
        app.result_notes = update_data.result_notes
    
    # Update fee payment status
    if update_data.application_fee_paid is not None:
        app.application_fee_paid = update_data.application_fee_paid
    
    db.commit()
    db.refresh(app)
    
    return {
        "message": "Application updated successfully",
        "application_id": app.id,
        "status": app.status.value
    }

@router.post("/query")
async def natural_language_query(
    query: str,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Natural language query to database (using LLM to generate SQL)"""
    # This would use an LLM to convert natural language to SQL
    # For now, return a placeholder
    # In production, you'd use OpenAI to generate SQL queries
    
    return {
        "query": query,
        "message": "Natural language to SQL conversion not fully implemented. Use direct database queries for now."
    }

