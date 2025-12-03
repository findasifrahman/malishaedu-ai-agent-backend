from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from app.database import get_db
from app.models import Student, User, Application, Document, DocumentType, ApplicationStatus
from app.routers.auth import get_current_user

router = APIRouter()

class StudentProfile(BaseModel):
    # Basic identification
    full_name: Optional[str] = None
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    gender: Optional[str] = None
    date_of_birth: Optional[datetime] = None
    country_of_citizenship: Optional[str] = None
    current_country_of_residence: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    wechat_id: Optional[str] = None
    
    # Passport information
    passport_number: Optional[str] = None
    passport_expiry_date: Optional[datetime] = None
    
    # Application intent
    target_university_id: Optional[int] = None
    target_major_id: Optional[int] = None
    target_intake_id: Optional[int] = None
    study_level: Optional[str] = None
    scholarship_preference: Optional[str] = None
    
    # COVA information
    home_address: Optional[str] = None
    current_address: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    emergency_contact_relationship: Optional[str] = None
    planned_arrival_date: Optional[datetime] = None
    intended_address_china: Optional[str] = None
    previous_visa_china: Optional[bool] = None
    previous_visa_details: Optional[str] = None
    previous_travel_to_china: Optional[bool] = None
    previous_travel_details: Optional[str] = None

class ApplicationCreate(BaseModel):
    program_intake_id: int  # Link to specific program intake

class ApplicationResponse(BaseModel):
    id: int
    program_intake_id: int
    university_name: str
    major_name: str
    intake_term: str
    intake_year: int
    application_fee: Optional[float] = None
    application_fee_paid: bool
    status: str
    submitted_at: Optional[str] = None
    created_at: str

@router.get("/me")
async def get_student_profile(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get current student profile"""
    student = db.query(Student).filter(Student.user_id == current_user.id).first()
    if not student:
        return {"message": "Student profile not created yet"}
    
    from fastapi.encoders import jsonable_encoder
    
    # Convert student to dict and handle datetime serialization
    student_dict = {
        "id": student.id,
        "user_id": student.user_id,
        "full_name": student.full_name,
        "given_name": student.given_name,
        "family_name": student.family_name,
        "gender": student.gender,
        "date_of_birth": student.date_of_birth.isoformat() if student.date_of_birth else None,
        "country_of_citizenship": student.country_of_citizenship,
        "current_country_of_residence": student.current_country_of_residence,
        "phone": student.phone,
        "email": student.email,
        "wechat_id": student.wechat_id,
        "passport_number": student.passport_number,
        "passport_expiry_date": student.passport_expiry_date.isoformat() if student.passport_expiry_date else None,
        "target_university_id": student.target_university_id,
        "target_major_id": student.target_major_id,
        "target_intake_id": student.target_intake_id,
        "study_level": student.study_level.value if student.study_level else None,
        "scholarship_preference": student.scholarship_preference.value if student.scholarship_preference else None,
        "application_stage": student.application_stage.value if student.application_stage else None,
        "home_address": student.home_address,
        "current_address": student.current_address,
        "emergency_contact_name": student.emergency_contact_name,
        "emergency_contact_phone": student.emergency_contact_phone,
        "emergency_contact_relationship": student.emergency_contact_relationship,
        "planned_arrival_date": student.planned_arrival_date.isoformat() if student.planned_arrival_date else None,
        "intended_address_china": student.intended_address_china,
        "previous_visa_china": student.previous_visa_china,
        "previous_visa_details": student.previous_visa_details,
        "previous_travel_to_china": student.previous_travel_to_china,
        "previous_travel_details": student.previous_travel_details,
        "education_history": student.education_history,
        "employment_history": student.employment_history,
        "family_members": student.family_members,
    }
    
    return jsonable_encoder(student_dict)

@router.put("/me")
async def update_student_profile(
    profile: StudentProfile,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update student profile"""
    student = db.query(Student).filter(Student.user_id == current_user.id).first()
    
    if not student:
        student = Student(user_id=current_user.id)
        db.add(student)
    
    # Basic identification
    if profile.full_name is not None:
        student.full_name = profile.full_name
    if profile.given_name is not None:
        student.given_name = profile.given_name
    if profile.family_name is not None:
        student.family_name = profile.family_name
    if profile.gender is not None:
        student.gender = profile.gender
    if profile.date_of_birth is not None:
        student.date_of_birth = profile.date_of_birth
    if profile.country_of_citizenship is not None:
        student.country_of_citizenship = profile.country_of_citizenship
    if profile.current_country_of_residence is not None:
        student.current_country_of_residence = profile.current_country_of_residence
    if profile.phone is not None:
        student.phone = profile.phone
    if profile.email is not None:
        student.email = profile.email
    if profile.wechat_id is not None:
        student.wechat_id = profile.wechat_id
    
    # Passport information
    if profile.passport_number is not None:
        student.passport_number = profile.passport_number
    if profile.passport_expiry_date is not None:
        student.passport_expiry_date = profile.passport_expiry_date
    
    # Application intent
    if profile.target_university_id is not None:
        student.target_university_id = profile.target_university_id
    if profile.target_major_id is not None:
        student.target_major_id = profile.target_major_id
    if profile.target_intake_id is not None:
        student.target_intake_id = profile.target_intake_id
    if profile.study_level is not None:
        from app.models import DegreeLevel
        try:
            student.study_level = DegreeLevel(profile.study_level)
        except ValueError:
            pass
    if profile.scholarship_preference is not None:
        from app.models import ScholarshipPreference
        try:
            student.scholarship_preference = ScholarshipPreference(profile.scholarship_preference)
        except ValueError:
            pass
    
    # COVA information
    if profile.home_address is not None:
        student.home_address = profile.home_address
    if profile.current_address is not None:
        student.current_address = profile.current_address
    if profile.emergency_contact_name is not None:
        student.emergency_contact_name = profile.emergency_contact_name
    if profile.emergency_contact_phone is not None:
        student.emergency_contact_phone = profile.emergency_contact_phone
    if profile.emergency_contact_relationship is not None:
        student.emergency_contact_relationship = profile.emergency_contact_relationship
    if profile.planned_arrival_date is not None:
        student.planned_arrival_date = profile.planned_arrival_date
    if profile.intended_address_china is not None:
        student.intended_address_china = profile.intended_address_china
    if profile.previous_visa_china is not None:
        student.previous_visa_china = profile.previous_visa_china
    if profile.previous_visa_details is not None:
        student.previous_visa_details = profile.previous_visa_details
    if profile.previous_travel_to_china is not None:
        student.previous_travel_to_china = profile.previous_travel_to_china
    if profile.previous_travel_details is not None:
        student.previous_travel_details = profile.previous_travel_details
    
    db.commit()
    db.refresh(student)
    
    return {"message": "Profile updated successfully", "student_id": student.id}

@router.post("/applications", response_model=ApplicationResponse)
async def create_application(
    application: ApplicationCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new application to a program intake"""
    student = db.query(Student).filter(Student.user_id == current_user.id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student profile not found. Please complete your profile first.")
    
    # Verify program intake exists
    from app.models import ProgramIntake
    program_intake = db.query(ProgramIntake).filter(ProgramIntake.id == application.program_intake_id).first()
    if not program_intake:
        raise HTTPException(status_code=404, detail="Program intake not found")
    
    # Check if student already has an application for this intake
    existing = db.query(Application).filter(
        Application.student_id == student.id,
        Application.program_intake_id == application.program_intake_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="You already have an application for this program intake")
    
    # Create new application
    new_application = Application(
        student_id=student.id,
        program_intake_id=application.program_intake_id,
        application_fee_amount=program_intake.application_fee,
        application_fee_paid=False,  # Fee payment handled separately
        status=ApplicationStatus.DRAFT
    )
    db.add(new_application)
    db.commit()
    db.refresh(new_application)
    
    return ApplicationResponse(
        id=new_application.id,
        program_intake_id=new_application.program_intake_id,
        university_name=program_intake.university.name,
        major_name=program_intake.major.name,
        intake_term=program_intake.intake_term.value,
        intake_year=program_intake.intake_year,
        application_fee=program_intake.application_fee,
        application_fee_paid=new_application.application_fee_paid,
        status=new_application.status.value,
        submitted_at=new_application.submitted_at.isoformat() if new_application.submitted_at else None,
        created_at=new_application.created_at.isoformat() if new_application.created_at else None
    )

@router.get("/applications")
async def get_applications(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all applications for current student with program intake details"""
    student = db.query(Student).filter(Student.user_id == current_user.id).first()
    if not student:
        return []
    
    applications = db.query(Application).filter(Application.student_id == student.id).all()
    
    result = []
    for app in applications:
        if app.program_intake:
            intake = app.program_intake
            result.append({
                "id": app.id,
                "program_intake_id": app.program_intake_id,
                "university_name": intake.university.name,
                "major_name": intake.major.name,
                "intake_term": intake.intake_term.value,
                "intake_year": intake.intake_year,
                "application_fee": intake.application_fee,
                "application_fee_paid": app.application_fee_paid,
                "status": app.status.value,
                "submitted_at": app.submitted_at.isoformat() if app.submitted_at else None,
                "result": app.result,
                "created_at": app.created_at.isoformat() if app.created_at else None
            })
    
    return result

@router.get("/documents/status")
async def get_document_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get document submission status"""
    student = db.query(Student).filter(Student.user_id == current_user.id).first()
    if not student:
        return {"submitted": 0, "total": 9, "documents": []}
    
    documents = db.query(Document).filter(Document.student_id == student.id).all()
    
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
    
    submitted_types = {doc.document_type for doc in documents}
    
    doc_status = []
    for doc_type in required_docs:
        doc_status.append({
            "type": doc_type.value,
            "submitted": doc_type in submitted_types,
            "verified": any(d.verified for d in documents if d.document_type == doc_type)
        })
    
    return {
        "submitted": len(submitted_types),
        "total": len(required_docs),
        "documents": doc_status
    }

