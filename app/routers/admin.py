from fastapi import APIRouter, Depends, HTTPException, Response, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import uuid
import threading
from fastapi import Body
from datetime import datetime, timedelta, timezone
import re
import json
from app.database import get_db
from app.models import (
    User, UserRole, Lead, Complaint, Student, Document, 
    Application, AdminSettings, DocumentType, ApplicationStatus, ProgramIntake, StudentDocument
)
from app.routers.auth import get_current_user
from app.services.application_automation import ApplicationAutomation
from app.services.r2_service import R2Service
from app.services.portals import HITPortal, BeihangPortal, BNUZPortal
from app.services.document_verification_service import DocumentVerificationService
from app.services.sql_generator_service import SQLGeneratorService
from fastapi import UploadFile, File, Form
from typing import Tuple
import io
from PIL import Image

router = APIRouter()

# In-memory job store for SQL generation (use Redis in production for persistence)
sql_generation_jobs: Dict[str, Dict[str, Any]] = {}
sql_jobs_lock = threading.Lock()

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
    
    # is_ is a method on SQLAlchemy columns, not a direct import
    students_without_diploma = db.query(Student).outerjoin(
        Document, and_(
            Document.student_id == Student.id,
            Document.document_type == DocumentType.DIPLOMA
        )
    ).filter(Document.id.is_(None)).count()
    
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

@router.get("/students")
async def get_students(
    page: int = 1,
    page_size: int = 20,
    search: Optional[str] = None,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Get students with pagination and search"""
    from sqlalchemy import or_
    
    query = db.query(Student)
    
    # Apply search filter if provided
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                Student.given_name.ilike(search_term),
                Student.family_name.ilike(search_term),
                Student.email.ilike(search_term),
                Student.phone.ilike(search_term),
                Student.country_of_citizenship.ilike(search_term),
                Student.passport_number.ilike(search_term)
            )
        )
    
    # Get total count
    total = query.count()
    
    # Apply pagination
    offset = (page - 1) * page_size
    students = query.order_by(Student.created_at.desc()).offset(offset).limit(page_size).all()
    
    # Get document counts for each student (lightweight query)
    student_ids = [s.id for s in students]
    document_counts = db.query(
        Document.student_id,
        func.count(Document.id).label('doc_count')
    ).filter(
        Document.student_id.in_(student_ids)
    ).group_by(Document.student_id).all()
    
    doc_count_map = {sid: count for sid, count in document_counts}
    
    # Get application counts for each student
    application_counts = db.query(
        Application.student_id,
        func.count(Application.id).label('app_count')
    ).filter(
        Application.student_id.in_(student_ids)
    ).group_by(Application.student_id).all()
    
    app_count_map = {sid: count for sid, count in application_counts}
    
    return {
        "items": [
            {
                "id": student.id,
                "user_id": student.user_id,
                "full_name": f"{student.given_name or ''} {student.family_name or ''}".strip() or None,
                "given_name": student.given_name,
                "family_name": student.family_name,
                "email": student.email,
                "phone": student.phone,
                "passport_number": student.passport_number,
                "country_of_citizenship": student.country_of_citizenship,
                "created_at": student.created_at.isoformat() if student.created_at else None,
                "document_count": doc_count_map.get(student.id, 0),
                "application_count": app_count_map.get(student.id, 0)
            }
            for student in students
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size
    }

@router.get("/students/progress")
async def get_student_progress(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Get student document submission progress (lightweight - only counts)"""
    # This endpoint is kept for backward compatibility but should be used sparingly
    # Consider using /admin/students with pagination instead
    students = db.query(Student).limit(100).all()  # Limit to prevent timeout
    
    progress_data = []
    for student in students:
        doc_count = db.query(Document).filter(Document.student_id == student.id).count()
        
        progress_data.append({
            "student_id": student.id,
            "user_id": student.user_id,
            "submitted": doc_count,
            "total": 9,  # Standard required docs
            "percentage": (doc_count / 9) * 100 if doc_count > 0 else 0
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
                "student_name": f"{app.student.given_name or ''} {app.student.family_name or ''}".strip() if app.student else None,
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
                "full_name": f"{app.student.given_name or ''} {app.student.family_name or ''}".strip() if app.student else None,
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


class ApplicationAutomationRequest(BaseModel):
    student_id: int
    apply_url: str
    username: Optional[str] = None
    password: Optional[str] = None
    portal_type: Optional[str] = None  # "hit", "beihang", "bnuz", etc.


@router.post("/automation/run")
async def run_application_automation(
    request: ApplicationAutomationRequest,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Run application automation for a student"""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    
    def run_automation_in_thread(student_id, apply_url, username, password, portal_type):
        """Run automation in a thread with a new event loop"""
        # Set event loop policy for Windows before creating loop
        import asyncio
        import sys
        if sys.platform == 'win32':
            # Windows requires ProactorEventLoop for subprocess support
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        
        # Create a new event loop for this thread (Playwright sync API needs it)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            # Get portal override if specified
            portal_override = None
            if portal_type:
                portal_map = {
                    "hit": HITPortal(),
                    "beihang": BeihangPortal(),
                    "bnuz": BNUZPortal(),
                }
                portal_override = portal_map.get(portal_type.lower())
            
            # Initialize services (need to get a new DB session for this thread)
            from app.database import SessionLocal
            thread_db = SessionLocal()
            try:
                r2_service = R2Service()
                automation = ApplicationAutomation(thread_db, r2_service)
                
                # Run automation (sync Playwright will work with the new event loop)
                result = automation.run(
                    student_id=student_id,
                    apply_url=apply_url,
                    username=username,
                    password=password,
                    portal_override=portal_override
                )
                return result
            finally:
                thread_db.close()
        finally:
            try:
                # Close all pending tasks
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except:
                pass
            loop.close()
    
    try:
        # Run automation in a thread executor with a new event loop
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            result = await loop.run_in_executor(
                executor,
                run_automation_in_thread,
                request.student_id,
                request.apply_url,
                request.username,
                request.password,
                request.portal_type
            )
        
        return result
    
    except Exception as e:
        error_msg = str(e)
        # Check if it's a network/connection error
        if "network" in error_msg.lower() or "connection" in error_msg.lower():
            error_msg = f"Network Error: {error_msg}. This may occur on servers without a display. Try running on localhost or ensure the server has Xvfb installed for headless browser support."
        raise HTTPException(status_code=500, detail=f"Automation failed: {error_msg}")

@router.get("/students/{student_id}/applications")
async def get_student_applications(
    student_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Get all applications for a specific student (admin only)"""
    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    
    applications = db.query(Application).filter(Application.student_id == student_id).all()
    
    return [
        {
            "id": app.id,
            "program_intake_id": app.program_intake_id,
            "university_name": app.program_intake.university.name if app.program_intake and app.program_intake.university else None,
            "major_name": app.program_intake.major.name if app.program_intake and app.program_intake.major else None,
            "intake_term": app.program_intake.intake_term.value if app.program_intake and hasattr(app.program_intake.intake_term, 'value') else str(app.program_intake.intake_term) if app.program_intake else None,
            "intake_year": app.program_intake.intake_year if app.program_intake else None,
            "status": app.status.value if hasattr(app.status, 'value') else str(app.status),
            "scholarship_preference": app.scholarship_preference,
            "degree_level": app.degree_level,
            "application_fee": app.program_intake.application_fee if app.program_intake else None,
            "application_fee_paid": app.application_fee_paid,
            "created_at": app.created_at.isoformat() if app.created_at else None
        }
        for app in applications
    ]

@router.get("/students/{student_id}/profile")
async def get_student_profile_admin(
    student_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Get student profile (admin can view any student)"""
    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    
    # Convert student to dict
    student_dict = {}
    for column in Student.__table__.columns:
        value = getattr(student, column.name)
        if isinstance(value, datetime):
            student_dict[column.name] = value.isoformat()
        elif hasattr(value, 'value'):  # Enum
            student_dict[column.name] = value.value
        else:
            student_dict[column.name] = value
    
    return student_dict

@router.get("/students/{student_id}/password")
async def get_student_password(
    student_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Get student's password hash (admin only - for password recovery)"""
    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    
    user = db.query(User).filter(User.id == student.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found for this student")
    
    return {
        "student_id": student.id,
        "email": user.email,
        "has_password": bool(user.hashed_password),
        "note": "Password is hashed and cannot be retrieved. Use set_password endpoint to set a new password."
    }

class SetPasswordRequest(BaseModel):
    password: str

@router.post("/students/{student_id}/set-password")
async def set_student_password(
    student_id: int,
    request: SetPasswordRequest,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Set student password (admin only)"""
    from app.routers.auth import get_password_hash
    
    if len(request.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters long")
    
    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    
    user = db.query(User).filter(User.id == student.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found for this student")
    
    user.hashed_password = get_password_hash(request.password)
    db.commit()
    
    return {
        "message": "Password updated successfully",
        "student_id": student.id,
        "email": user.email
    }

class StudentCreate(BaseModel):
    email: str
    password: Optional[str] = None
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    phone: Optional[str] = None
    country_of_citizenship: Optional[str] = None
    passport_number: Optional[str] = None

class StudentUpdate(BaseModel):
    email: Optional[str] = None
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    phone: Optional[str] = None
    country_of_citizenship: Optional[str] = None
    passport_number: Optional[str] = None
    # Add other fields as needed

@router.post("/students")
async def create_student(
    student_data: StudentCreate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Create a new student (admin only)"""
    from app.routers.auth import get_password_hash
    
    # Check if user with email already exists
    existing_user = db.query(User).filter(User.email == student_data.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="User with this email already exists")
    
    # Generate a random password if not provided
    import secrets
    password = student_data.password or secrets.token_urlsafe(12)
    
    # Create user
    full_name = f"{student_data.given_name or ''} {student_data.family_name or ''}".strip() if (student_data.given_name or student_data.family_name) else None
    user = User(
        email=student_data.email,
        name=full_name or student_data.email.split('@')[0],
        phone=student_data.phone,
        country=student_data.country_of_citizenship,
        hashed_password=get_password_hash(password),
        role=UserRole.STUDENT
    )
    db.add(user)
    db.flush()  # Get user.id without committing
    
    # Get default partner
    from app.models import Partner
    default_partner = db.query(Partner).filter(Partner.email == 'malishaedu@gmail.com').first()
    
    # Create student
    student = Student(
        user_id=user.id,
        partner_id=default_partner.id if default_partner else None,
        email=student_data.email,
        given_name=student_data.given_name,
        family_name=student_data.family_name,
        phone=student_data.phone,
        country_of_citizenship=student_data.country_of_citizenship,
        passport_number=student_data.passport_number
    )
    db.add(student)
    db.commit()
    db.refresh(student)
    
    return {
        "id": student.id,
        "user_id": user.id,
        "email": user.email,
        "full_name": f"{student.given_name or ''} {student.family_name or ''}".strip() or None,
        "given_name": student.given_name,
        "family_name": student.family_name,
        "phone": student.phone,
        "password": password,  # Return password so admin can share it
        "message": "Student created successfully"
    }

@router.put("/students/{student_id}")
async def update_student(
    student_id: int,
    student_data: StudentUpdate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Update student basic information (admin only)"""
    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    
    user = db.query(User).filter(User.id == student.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found for this student")
    
    # Update student fields
    if student_data.given_name is not None:
        student.given_name = student_data.given_name
    if student_data.family_name is not None:
        student.family_name = student_data.family_name
    # Update user name if either given_name or family_name changed
    if student_data.given_name is not None or student_data.family_name is not None:
        full_name = f"{student.given_name or ''} {student.family_name or ''}".strip()
        user.name = full_name if full_name else user.name
    if student_data.phone is not None:
        student.phone = student_data.phone
        user.phone = student_data.phone
    if student_data.country_of_citizenship is not None:
        student.country_of_citizenship = student_data.country_of_citizenship
        user.country = student_data.country_of_citizenship
    if student_data.passport_number is not None:
        student.passport_number = student_data.passport_number
    if student_data.email is not None and student_data.email != user.email:
        # Check if new email is already taken
        existing_user = db.query(User).filter(User.email == student_data.email, User.id != user.id).first()
        if existing_user:
            raise HTTPException(status_code=400, detail="Email already in use")
        student.email = student_data.email
        user.email = student_data.email
    
    db.commit()
    db.refresh(student)
    
    return {
        "id": student.id,
        "email": student.email,
        "full_name": f"{student.given_name or ''} {student.family_name or ''}".strip() or None,
        "given_name": student.given_name,
        "family_name": student.family_name,
        "phone": student.phone,
        "country_of_citizenship": student.country_of_citizenship,
        "passport_number": student.passport_number,
        "message": "Student updated successfully"
    }

@router.put("/students/{student_id}/profile")
async def update_student_profile_admin(
    student_id: int,
    profile: Any = Body(...),  # Accept full profile dict from request body
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Update full student profile (admin only - allows updating any field)"""
    from app.routers.students import StudentProfile
    from datetime import datetime, date
    
    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    
    # Convert dict to StudentProfile for validation
    try:
        if isinstance(profile, dict):
            profile_data = StudentProfile(**profile)
        else:
            profile_data = profile
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid profile data: {str(e)}")
    
    # Update all fields (same logic as /students/me endpoint)
    if profile_data.given_name is not None:
        student.given_name = profile_data.given_name
    if profile_data.family_name is not None:
        student.family_name = profile_data.family_name
    if profile_data.father_name is not None:
        student.father_name = profile_data.father_name
    if profile_data.mother_name is not None:
        student.mother_name = profile_data.mother_name
    if profile_data.gender is not None:
        student.gender = profile_data.gender
    if profile_data.date_of_birth is not None:
        student.date_of_birth = profile_data.date_of_birth
    if profile_data.country_of_citizenship is not None:
        student.country_of_citizenship = profile_data.country_of_citizenship
    if profile_data.current_country_of_residence is not None:
        student.current_country_of_residence = profile_data.current_country_of_residence
    if profile_data.phone is not None:
        student.phone = profile_data.phone
    if profile_data.email is not None:
        student.email = profile_data.email
    if profile_data.wechat_id is not None:
        student.wechat_id = profile_data.wechat_id
    
    # Passport
    if profile_data.passport_number is not None:
        student.passport_number = profile_data.passport_number
    if profile_data.passport_expiry_date is not None:
        if isinstance(profile_data.passport_expiry_date, date) and not isinstance(profile_data.passport_expiry_date, datetime):
            student.passport_expiry_date = datetime.combine(profile_data.passport_expiry_date, datetime.min.time())
        else:
            student.passport_expiry_date = profile_data.passport_expiry_date
    
    # Scores - update all score fields
    if profile_data.hsk_score is not None:
        student.hsk_score = profile_data.hsk_score
    if profile_data.hsk_certificate_date is not None:
        if isinstance(profile_data.hsk_certificate_date, date) and not isinstance(profile_data.hsk_certificate_date, datetime):
            student.hsk_certificate_date = datetime.combine(profile_data.hsk_certificate_date, datetime.min.time())
        else:
            student.hsk_certificate_date = profile_data.hsk_certificate_date
    if profile_data.hskk_level is not None:
        from app.models import HSKKLevel
        try:
            student.hskk_level = HSKKLevel(profile_data.hskk_level)
        except ValueError:
            pass
    if profile_data.hskk_score is not None:
        student.hskk_score = profile_data.hskk_score
    if profile_data.csca_status is not None:
        from app.models import CSCAStatus
        try:
            student.csca_status = CSCAStatus(profile_data.csca_status)
        except ValueError:
            pass
    if profile_data.csca_score_math is not None:
        student.csca_score_math = profile_data.csca_score_math
    if profile_data.csca_score_specialized_chinese is not None:
        student.csca_score_specialized_chinese = profile_data.csca_score_specialized_chinese
    if profile_data.csca_score_physics is not None:
        student.csca_score_physics = profile_data.csca_score_physics
    if profile_data.csca_score_chemistry is not None:
        student.csca_score_chemistry = profile_data.csca_score_chemistry
    if profile_data.english_test_type is not None:
        student.english_test_type = profile_data.english_test_type
    if profile_data.english_test_score is not None:
        student.english_test_score = profile_data.english_test_score
    
    # Application intent
    if profile_data.target_university_id is not None:
        student.target_university_id = profile_data.target_university_id
    if profile_data.target_major_id is not None:
        student.target_major_id = profile_data.target_major_id
    if profile_data.target_intake_id is not None:
        student.target_intake_id = profile_data.target_intake_id
    if profile_data.study_level is not None:
        from app.models import DegreeLevel
        try:
            student.study_level = DegreeLevel(profile_data.study_level)
        except ValueError:
            pass
    if profile_data.scholarship_preference is not None:
        from app.models import ScholarshipPreference
        try:
            student.scholarship_preference = ScholarshipPreference(profile_data.scholarship_preference)
        except ValueError:
            pass
    
    # COVA information
    if profile_data.home_address is not None:
        student.home_address = profile_data.home_address
    if profile_data.current_address is not None:
        student.current_address = profile_data.current_address
    if profile_data.emergency_contact_name is not None:
        student.emergency_contact_name = profile_data.emergency_contact_name
    if profile_data.emergency_contact_phone is not None:
        student.emergency_contact_phone = profile_data.emergency_contact_phone
    if profile_data.emergency_contact_relationship is not None:
        student.emergency_contact_relationship = profile_data.emergency_contact_relationship
    if profile_data.planned_arrival_date is not None:
        if isinstance(profile_data.planned_arrival_date, date) and not isinstance(profile_data.planned_arrival_date, datetime):
            student.planned_arrival_date = datetime.combine(profile_data.planned_arrival_date, datetime.min.time())
        else:
            student.planned_arrival_date = profile_data.planned_arrival_date
    if profile_data.intended_address_china is not None:
        student.intended_address_china = profile_data.intended_address_china
    if profile_data.previous_visa_china is not None:
        student.previous_visa_china = profile_data.previous_visa_china
    if profile_data.previous_visa_details is not None:
        student.previous_visa_details = profile_data.previous_visa_details
    if profile_data.previous_travel_to_china is not None:
        student.previous_travel_to_china = profile_data.previous_travel_to_china
    if profile_data.previous_travel_details is not None:
        student.previous_travel_details = profile_data.previous_travel_details
    
    # Personal Information
    if profile_data.marital_status is not None:
        from app.models import MaritalStatus
        try:
            student.marital_status = MaritalStatus(profile_data.marital_status)
        except ValueError:
            pass
    if profile_data.religion is not None:
        from app.models import Religion
        try:
            student.religion = Religion(profile_data.religion)
        except ValueError:
            pass
    if profile_data.occupation is not None:
        student.occupation = profile_data.occupation
    
    # Highest degree information
    if hasattr(profile_data, 'highest_degree_name') and profile_data.highest_degree_name is not None:
        student.highest_degree_name = profile_data.highest_degree_name
    if profile_data.highest_degree_medium is not None and profile_data.highest_degree_medium != '':
        from app.models import DegreeMedium
        try:
            student.highest_degree_medium = DegreeMedium(profile_data.highest_degree_medium)
        except (ValueError, TypeError):
            pass
    if hasattr(profile_data, 'highest_degree_institution') and profile_data.highest_degree_institution is not None:
        student.highest_degree_institution = profile_data.highest_degree_institution
    if hasattr(profile_data, 'highest_degree_country') and profile_data.highest_degree_country is not None:
        student.highest_degree_country = profile_data.highest_degree_country
    if hasattr(profile_data, 'highest_degree_year') and profile_data.highest_degree_year is not None:
        student.highest_degree_year = profile_data.highest_degree_year
    if hasattr(profile_data, 'highest_degree_cgpa') and profile_data.highest_degree_cgpa is not None:
        student.highest_degree_cgpa = profile_data.highest_degree_cgpa
    if profile_data.number_of_published_papers is not None:
        student.number_of_published_papers = profile_data.number_of_published_papers
    
    # Guarantor information
    if hasattr(profile_data, 'relation_with_guarantor') and profile_data.relation_with_guarantor is not None:
        student.relation_with_guarantor = profile_data.relation_with_guarantor
    if hasattr(profile_data, 'is_the_bank_guarantee_in_students_name') and profile_data.is_the_bank_guarantee_in_students_name is not None:
        student.is_the_bank_guarantee_in_students_name = profile_data.is_the_bank_guarantee_in_students_name
    
    # English test type
    if profile_data.english_test_type is not None:
        from app.models import EnglishTestType
        try:
            student.english_test_type = EnglishTestType(profile_data.english_test_type)
        except ValueError:
            pass
    
    db.commit()
    db.refresh(student)
    
    return {
        "id": student.id,
        "message": "Student profile updated successfully"
    }

# Initialize services for document management
verification_service = DocumentVerificationService()
r2_service = R2Service()

def validate_passport_photo(file_content: bytes, filename: str) -> Tuple[bool, str]:
    """
    Validate passport photo requirements before AI processing.
    Validates: format (JPG/JPEG), size (100-500KB), dimensions (min 295x413), ratio (4:3), orientation (width < height).
    
    Returns:
        (is_valid, error_message)
    """
    try:
        # Check file format
        filename_lower = filename.lower()
        if not (filename_lower.endswith('.jpg') or filename_lower.endswith('.jpeg')):
            return False, "Passport photo must be in JPG or JPEG format. Current format is not supported."
        
        # Check file size (100KB - 500KB)
        file_size_kb = len(file_content) / 1024
        if file_size_kb < 100:
            return False, f"File size ({file_size_kb:.1f} KB) is too small. Minimum size is 100 KB."
        if file_size_kb > 500:
            return False, f"File size ({file_size_kb:.1f} KB) exceeds maximum allowed size of 500 KB."
        
        # Check image dimensions and ratio
        try:
            image = Image.open(io.BytesIO(file_content))
            width, height = image.size
            
            # Check minimum dimensions (no less than 295*413 pixels)
            if width < 295 or height < 413:
                return False, f"Image dimensions ({width}x{height} pixels) are too small. Minimum required: 295x413 pixels."
            
            # Check orientation (width must be less than height - portrait orientation)
            if width >= height:
                return False, f"Image orientation is incorrect. Width ({width}px) must be less than height ({height}px) for portrait orientation."
            
            # Check aspect ratio (4:3 ratio, with some tolerance)
            ratio = height / width
            if ratio < 1.25 or ratio > 1.5:
                return False, f"Image aspect ratio ({ratio:.2f}) is incorrect. Required ratio is approximately 4:3 (height:width between 1.25 and 1.5)."
            
            # Check if image is colored (not grayscale)
            if image.mode in ('L', 'LA', 'P'):
                if image.mode == 'P':
                    image = image.convert('RGB')
                else:
                    return False, "Image must be colored (not grayscale)."
            
            return True, ""
            
        except Exception as e:
            return False, f"Failed to read image: {str(e)}"
            
    except Exception as e:
        return False, f"Validation error: {str(e)}"

@router.get("/students/{student_id}/documents")
async def get_student_documents_admin(
    student_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Get all verified documents for a student (admin only)"""
    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    
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

@router.post("/students/{student_id}/documents/verify-and-upload")
async def verify_and_upload_document_admin(
    student_id: int,
    file: UploadFile = File(...),
    doc_type: str = Form(...),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Upload and verify a document for a student (admin only)"""
    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    
    # Read file content
    file_content = await file.read()
    file_size = len(file_content)
    
    # Special validation for passport photos
    if doc_type.lower() in ('photo', 'passport_photo', 'passport_size_photo'):
        is_valid, error_msg = validate_passport_photo(file_content, file.filename)
        if not is_valid:
            raise HTTPException(
                status_code=400,
                detail=error_msg
            )
    
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
        except Exception as e:
            print(f"⚠️  Warning: Could not delete temp file {temp_url}: {e}")
        
        # Return detailed error message
        error_message = f"Document verification failed: {verification_result.get('reason', 'Unknown reason')}"
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
        "extracted_data": verification_result.get("extracted", {}),
        "verified": student_doc.verified
    }

@router.delete("/students/{student_id}/documents/{document_id}")
async def delete_student_document_admin(
    student_id: int,
    document_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Delete a verified document for a student (admin only)"""
    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    
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

# Initialize SQL generator service
sql_generator_service = SQLGeneratorService()

class SQLExecuteRequest(BaseModel):
    sql: str

class SQLValidationResponse(BaseModel):
    valid: bool
    errors: List[str]
    warnings: List[str]

class SQLGenerationResponse(BaseModel):
    sql: str
    validation: SQLValidationResponse
    document_text_preview: str

@router.post("/document-import/generate-sql")
async def generate_sql_from_document(
    file: UploadFile = File(...),
    current_user: User = Depends(require_admin)
):
    """Upload a document and generate SQL script for importing university programs"""
    # Note: We don't need DB session for this endpoint - LLM call can take long time
    # and DB connection may timeout. We only need admin authentication.
    import asyncio
    
    try:
        # Read file content
        file_content = await file.read()
        
        # Extract text from document
        document_text = sql_generator_service.extract_text_from_document(
            file_content, 
            file.filename or "document"
        )
        
        if not document_text.strip():
            raise HTTPException(
                status_code=400,
                detail="No text content could be extracted from the document"
            )
        
        # Generate SQL (this can take a long time with LLM)
        # Run in thread pool to avoid blocking the event loop
        # We don't need the DB session for this, so errors here won't affect DB connection
        print("🔄 Starting SQL generation (this may take 60-120 seconds)...")
        sql_script = await asyncio.to_thread(
            sql_generator_service.generate_sql_from_text,
            document_text
        )
        print("✅ SQL generation completed")
        
        # Check if SQL generation returned empty or error SQL
        if not sql_script or not sql_script.strip():
            raise HTTPException(
                status_code=500,
                detail="SQL generation returned empty result. Please check the document content and try again."
            )
        
        # Check if it's an error SQL (starts with error comment)
        if sql_script.strip().startswith('-- SQL Generation Error'):
            # Extract error message from SQL (get text after "SQL Generation Error:" until newline or SELECT)
            error_match = re.search(r'SQL Generation Error:\s*([^\n]+)', sql_script)
            if error_match:
                error_msg = error_match.group(1).strip()
            else:
                error_msg = "SQL generation failed due to an unknown error"
            print(f"❌ Detected error SQL, raising HTTPException: {error_msg}")
            raise HTTPException(
                status_code=500,
                detail=error_msg  # Return the user-friendly error message directly
            )
        
        # Validate SQL
        validation = sql_generator_service.validate_sql(sql_script)
        
        # Log success
        print(f"✅ SQL generated successfully: {len(sql_script)} characters")
        print(f"📊 Validation: valid={validation['valid']}, errors={len(validation['errors'])}, warnings={len(validation['warnings'])}")
        
        # Prepare response data - ensure it matches SQLGenerationResponse model
        try:
            # Create validation dict that matches SQLValidationResponse
            validation_dict = {
                "valid": bool(validation.get('valid', False)),
                "errors": list(validation.get('errors', [])),
                "warnings": list(validation.get('warnings', []))
            }
            
            # Return dict - FastAPI will validate against response_model
            response_data = {
                "sql": sql_script,
                "validation": validation_dict,
                "document_text_preview": document_text[:500] + "..." if len(document_text) > 500 else document_text
            }
            
            # Log response size
            import json
            try:
                response_json = json.dumps(response_data)
                response_size = len(response_json)
                print(f"📦 Response size: {response_size:,} bytes ({response_size / 1024:.2f} KB)")
            except Exception as json_error:
                print(f"⚠️  Warning: Could not serialize response for size check: {json_error}")
            
            # Log right before returning
            print("📤 Sending response to client...")
            
            # Return response - FastAPI will serialize it using response_model
            # Note: If this fails, it might be due to response size or timeout
            # 18K characters should be fine, but if issues persist, consider streaming
            try:
                # Validate response can be serialized
                json_str = json.dumps(response_data)
                print(f"✅ Response validated and serialized ({len(json_str)} bytes)")
                
                # Return the response data as dict - FastAPI will validate against response_model
                # Using dict instead of model instance to avoid serialization issues
                print("✅ Returning response data...")
                
                # Use JSONResponse to ensure response is sent immediately
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    content=response_data,
                    status_code=200,
                    headers={
                        "Content-Type": "application/json",
                        "Cache-Control": "no-cache, no-store, must-revalidate",
                        "X-Accel-Buffering": "no"  # Disable nginx buffering
                    }
                )
            except Exception as send_error:
                print(f"❌ Error during response return: {str(send_error)}")
                import traceback
                print(traceback.format_exc())
                raise HTTPException(
                    status_code=500,
                    detail=f"Error sending response: {str(send_error)}"
                )
        except Exception as response_error:
            print(f"❌ Error preparing response: {str(response_error)}")
            import traceback
            print(traceback.format_exc())
            raise HTTPException(
                status_code=500,
                detail=f"Error preparing response: {str(response_error)}"
            )
        
    except ValueError as e:
        print(f"❌ ValueError in SQL generation: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        # Log the full error for debugging
        import traceback
        error_trace = traceback.format_exc()
        print(f"❌ SQL Generation Error: {str(e)}")
        print(f"Traceback: {error_trace}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate SQL: {str(e)}"
        )

@router.post("/document-import/generate-sql-start")
async def start_sql_generation(
    file: UploadFile = File(...),
    current_user: User = Depends(require_admin),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    """Start SQL generation in background, return job ID immediately (< 1 second)"""
    import asyncio
    
    # Generate unique job ID
    job_id = str(uuid.uuid4())
    
    # Read file content before starting background task
    file_content = await file.read()
    filename = file.filename or "document"
    
    # Initialize job status
    with sql_jobs_lock:
        sql_generation_jobs[job_id] = {
            "status": "processing",
            "progress": "Reading document...",
            "result": None,
            "error": None,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
    
    # Start background task
    background_tasks.add_task(
        generate_sql_background,
        job_id,
        file_content,
        filename
    )
    
    print(f"🚀 Started SQL generation job: {job_id}")
    return {
        "job_id": job_id,
        "status": "processing",
        "message": "SQL generation started. Poll /generate-sql-status/{job_id} for results."
    }

async def generate_sql_background(job_id: str, file_content: bytes, filename: str):
    """Background task to generate SQL"""
    import asyncio
    
    try:
        # Update progress
        with sql_jobs_lock:
            if job_id in sql_generation_jobs:
                sql_generation_jobs[job_id]["progress"] = "Extracting text from document..."
        
        # Extract text from document
        document_text = sql_generator_service.extract_text_from_document(
            file_content,
            filename
        )
        
        if not document_text.strip():
            with sql_jobs_lock:
                if job_id in sql_generation_jobs:
                    sql_generation_jobs[job_id].update({
                        "status": "failed",
                        "error": "No text content could be extracted from the document"
                    })
            return
        
        # Update progress
        with sql_jobs_lock:
            if job_id in sql_generation_jobs:
                sql_generation_jobs[job_id]["progress"] = "Generating SQL with AI (this may take 60-120 seconds)..."
        
        # Generate SQL (this can take a long time with LLM)
        print(f"🔄 [Job {job_id}] Starting SQL generation...")
        sql_script = await asyncio.to_thread(
            sql_generator_service.generate_sql_from_text,
            document_text
        )
        print(f"✅ [Job {job_id}] SQL generation completed")
        
        # Check if SQL generation returned empty or error SQL
        if not sql_script or not sql_script.strip():
            with sql_jobs_lock:
                if job_id in sql_generation_jobs:
                    sql_generation_jobs[job_id].update({
                        "status": "failed",
                        "error": "SQL generation returned empty result. Please check the document content and try again."
                    })
            return
        
        # Check if it's an error SQL
        if sql_script.strip().startswith('-- SQL Generation Error'):
            error_match = re.search(r'SQL Generation Error:\s*([^\n]+)', sql_script)
            error_msg = error_match.group(1).strip() if error_match else "SQL generation failed due to an unknown error"
            with sql_jobs_lock:
                if job_id in sql_generation_jobs:
                    sql_generation_jobs[job_id].update({
                        "status": "failed",
                        "error": error_msg
                    })
            return
        
        # Validate SQL
        validation = sql_generator_service.validate_sql(sql_script)
        
        # Create validation dict
        validation_dict = {
            "valid": bool(validation.get('valid', False)),
            "errors": list(validation.get('errors', [])),
            "warnings": list(validation.get('warnings', []))
        }
        
        # Store result
        with sql_jobs_lock:
            if job_id in sql_generation_jobs:
                sql_generation_jobs[job_id].update({
                    "status": "completed",
                    "progress": "Complete",
                    "result": {
                        "sql": sql_script,
                        "validation": validation_dict,
                        "document_text_preview": document_text[:500] + "..." if len(document_text) > 500 else document_text
                    }
                })
        
        print(f"✅ [Job {job_id}] SQL generation completed successfully: {len(sql_script)} characters")
        
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        error_msg = str(e)
        print(f"❌ [Job {job_id}] SQL generation failed: {error_msg}")
        print(f"Traceback: {error_trace}")
        
        with sql_jobs_lock:
            if job_id in sql_generation_jobs:
                sql_generation_jobs[job_id].update({
                    "status": "failed",
                    "error": error_msg
                })

@router.get("/document-import/generate-sql-status/{job_id}")
async def get_sql_generation_status(
    job_id: str,
    current_user: User = Depends(require_admin)
):
    """Get SQL generation job status"""
    with sql_jobs_lock:
        if job_id not in sql_generation_jobs:
            raise HTTPException(status_code=404, detail="Job not found")
        
        job = sql_generation_jobs[job_id].copy()
    
    return job

@router.post("/document-import/execute-sql")
async def execute_generated_sql(
    request: SQLExecuteRequest,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Execute the generated SQL script (admin only, with safety checks)"""
    # Debug: log what we received
    print(f"🔍 Received SQL execution request")
    print(f"📄 SQL type: {type(request.sql)}")
    print(f"📄 SQL length: {len(str(request.sql)) if request.sql else 0}")
    print(f"📄 First 200 chars: {str(request.sql)[:200] if request.sql else 'None'}")
    
    # Ensure sql is a string
    if not isinstance(request.sql, str):
        print(f"❌ ERROR: SQL is not a string! Type: {type(request.sql)}, Value: {request.sql}")
        raise HTTPException(
            status_code=400, 
            detail=f"SQL must be a string, got {type(request.sql).__name__}"
        )
    
    sql = request.sql.strip()
    
    if not sql:
        raise HTTPException(status_code=400, detail="SQL script is empty")
    
    # Additional safety validation
    dangerous_patterns = [
        r'\bDROP\s+TABLE\b',
        r'\bTRUNCATE\b',
        r'\bDELETE\s+FROM\s+universities\b',
        r'\bDELETE\s+FROM\s+majors\b',
        r'\bDELETE\s+FROM\s+program_intakes\b',
        r'\bALTER\s+TABLE\b',
        r'\bCREATE\s+TABLE\b',
        r'\bDROP\s+DATABASE\b',
    ]
    
    for pattern in dangerous_patterns:
        if re.search(pattern, sql, re.IGNORECASE):
            raise HTTPException(
                status_code=400,
                detail=f"Dangerous SQL operation detected. Execution blocked for safety."
            )
    
    try:
        # Execute SQL using raw connection
        from sqlalchemy import text
        
        # Remove trailing semicolon if present (for single statement execution)
        sql_clean = sql.rstrip().rstrip(';').strip()
        
        print(f"🔍 Executing SQL (length: {len(sql_clean)} chars)")
        print(f"📄 First 200 chars: {sql_clean[:200]}...")
        
        # Check if SQL contains university lookup (for debugging)
        if 'university_cte' in sql_clean.lower() or 'universities' in sql_clean.lower():
            # Try to extract university name for logging
            uni_match = re.search(r"lower\(name\)\s*=\s*lower\(['\"]([^'\"]+)['\"]\)", sql_clean, re.IGNORECASE)
            if uni_match:
                uni_name = uni_match.group(1)
                print(f"🔍 Looking for university: {uni_name}")
                # Check if university exists
                check_uni = db.execute(
                    text("SELECT id, name FROM universities WHERE lower(name) = lower(:name) LIMIT 1"),
                    {"name": uni_name}
                ).fetchone()
                if check_uni:
                    print(f"✅ University found: ID={check_uni[0]}, Name={check_uni[1]}")
                else:
                    print(f"⚠️  WARNING: University '{uni_name}' not found in database!")
        
        # Check enum values for debugging and fix if needed
        try:
            enum_values = db.execute(
                text("SELECT unnest(enum_range(NULL::intaketerm))::text")
            ).fetchall()
            enum_list = [row[0] for row in enum_values]
            print(f"📋 Available intaketerm enum values: {enum_list}")
            
            # If SQL uses incorrect enum values, try to fix them
            if enum_list:
                # Map common variations to actual enum values
                enum_map = {}
                for val in enum_list:
                    enum_map[val.lower()] = val
                    enum_map[val] = val
                
                # Check and replace incorrect enum casts
                if "'March'::intaketerm" in sql_clean:
                    if 'March' not in enum_list:
                        # Try to find the correct value
                        if 'march' in enum_map:
                            correct_value = enum_map['march']
                            print(f"⚠️  Fixing enum value: 'March' -> '{correct_value}'")
                            sql_clean = sql_clean.replace("'March'::intaketerm", f"'{correct_value}'::intaketerm")
                            sql_clean = sql_clean.replace("'March'::intaketerm", f"'{correct_value}'::intaketerm")
                        else:
                            print(f"❌ ERROR: 'March' not found in enum values! Available: {enum_list}")
                            raise HTTPException(
                                status_code=400,
                                detail=f"Invalid enum value 'March'. Available values: {enum_list}. Please update the SQL generator to use the correct enum values."
                            )
                
                if "'September'::intaketerm" in sql_clean and 'September' not in enum_list:
                    if 'september' in enum_map:
                        correct_value = enum_map['september']
                        print(f"⚠️  Fixing enum value: 'September' -> '{correct_value}'")
                        sql_clean = sql_clean.replace("'September'::intaketerm", f"'{correct_value}'::intaketerm")
                
                if "'Other'::intaketerm" in sql_clean and 'Other' not in enum_list:
                    if 'other' in enum_map:
                        correct_value = enum_map['other']
                        print(f"⚠️  Fixing enum value: 'Other' -> '{correct_value}'")
                        sql_clean = sql_clean.replace("'Other'::intaketerm", f"'{correct_value}'::intaketerm")
        except Exception as enum_check_error:
            print(f"⚠️  Could not check/fix enum values: {enum_check_error}")
        
        # Execute the entire SQL script as one statement (handles CTEs properly)
        result = db.execute(text(sql_clean))
        
        # Check if it's a SELECT statement (should return results)
        if sql_clean.strip().upper().startswith('WITH') or sql_clean.strip().upper().startswith('SELECT'):
            rows = result.fetchall()
            columns = result.keys() if hasattr(result, 'keys') else []
            
            # Convert rows to dict format
            rows_dict = []
            for row in rows:
                row_dict = {}
                for i, col in enumerate(columns):
                    value = row[i] if i < len(row) else None
                    # Handle array types (like errors text[])
                    if isinstance(value, list):
                        row_dict[col] = value
                    else:
                        row_dict[col] = value
                rows_dict.append(row_dict)
            
            print(f"✅ SQL executed successfully. Returned {len(rows_dict)} row(s)")
            if rows_dict:
                print(f"📊 First row: {rows_dict[0]}")
            
            # The final SELECT should return one row with counts and errors
            final_result = rows_dict[0] if rows_dict else None
            
            # Commit transaction
            db.commit()
            
            return {
                "success": True,
                "message": "SQL executed successfully",
                "results": [{
                    "type": "SELECT",
                    "rows": rows_dict,
                    "row_count": len(rows_dict)
                }],
                "summary": final_result
            }
        else:
            # For INSERT/UPDATE/DELETE, get rowcount
            rows_affected = result.rowcount if hasattr(result, 'rowcount') else 0
            db.commit()
            
            print(f"✅ SQL executed successfully. Rows affected: {rows_affected}")
            
            return {
                "success": True,
                "message": "SQL executed successfully",
                "results": [{
                    "type": "DML",
                    "rows_affected": rows_affected
                }],
                "summary": {"rows_affected": rows_affected}
            }
        
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"SQL execution failed: {str(e)}"
        )

