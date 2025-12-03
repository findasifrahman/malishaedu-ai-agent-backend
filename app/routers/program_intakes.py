from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from app.database import get_db
from app.models import ProgramIntake, University, Major, User, IntakeTerm
from app.routers.auth import get_current_user

router = APIRouter()

class ProgramIntakeCreate(BaseModel):
    university_id: int
    major_id: int
    intake_term: IntakeTerm
    intake_year: int
    application_deadline: datetime
    documents_required: str  # Comma-separated list
    tuition_per_semester: Optional[float] = None
    tuition_per_year: Optional[float] = None
    application_fee: Optional[float] = None  # Non-refundable application fee
    accommodation_fee: Optional[float] = None  # Accommodation fee per year
    service_fee: Optional[float] = None  # MalishaEdu service fee (only for successful application)
    medical_insurance_fee: Optional[float] = None  # Medical insurance fee
    teaching_language: Optional[str] = None  # Changed from TeachingLanguage enum to str
    duration_years: Optional[float] = None  # Duration in years (float)
    degree_type: Optional[str] = None  # Changed from DegreeLevel enum to str
    arrival_medical_checkup_fee: Optional[float] = None  # One-time medical checkup fee upon arrival
    admission_process: Optional[str] = None  # Admission process description/file_type
    accommodation_note: Optional[str] = None  # Notes about accommodation
    visa_extension_fee: Optional[float] = None  # Visa extension fee required each year
    notes: Optional[str] = None
    scholarship_info: Optional[str] = None  # Scholarship amount and conditions

class ProgramIntakeUpdate(BaseModel):
    intake_term: Optional[IntakeTerm] = None
    intake_year: Optional[int] = None
    application_deadline: Optional[datetime] = None
    documents_required: Optional[str] = None
    tuition_per_semester: Optional[float] = None
    tuition_per_year: Optional[float] = None
    application_fee: Optional[float] = None
    accommodation_fee: Optional[float] = None
    service_fee: Optional[float] = None
    medical_insurance_fee: Optional[float] = None
    teaching_language: Optional[str] = None
    duration_years: Optional[float] = None
    degree_type: Optional[str] = None
    arrival_medical_checkup_fee: Optional[float] = None
    admission_process: Optional[str] = None
    accommodation_note: Optional[str] = None
    visa_extension_fee: Optional[float] = None
    notes: Optional[str] = None
    scholarship_info: Optional[str] = None

class ProgramIntakeResponse(BaseModel):
    id: int
    university_id: int
    major_id: int
    intake_term: str
    intake_year: int
    application_deadline: str
    documents_required: str
    tuition_per_semester: Optional[float]
    tuition_per_year: Optional[float]
    application_fee: Optional[float]
    accommodation_fee: Optional[float]  # Per year
    service_fee: Optional[float]  # MalishaEdu service fee (only for successful application)
    medical_insurance_fee: Optional[float]  # Medical insurance fee (after arrival in China)
    teaching_language: Optional[str]
    duration_years: Optional[float]  # Duration in years
    degree_type: Optional[str]
    arrival_medical_checkup_fee: Optional[float]  # One-time medical checkup fee upon arrival
    admission_process: Optional[str]  # Admission process description/file_type
    accommodation_note: Optional[str]  # Notes about accommodation
    visa_extension_fee: Optional[float]  # Visa extension fee required each year
    notes: Optional[str]
    scholarship_info: Optional[str]  # Scholarship amount and conditions
    created_at: str
    updated_at: Optional[str]
    
    class Config:
        from_attributes = True

@router.get("/", response_model=List[ProgramIntakeResponse])
async def list_program_intakes(
    university_id: Optional[int] = None,
    major_id: Optional[int] = None,
    intake_term: Optional[IntakeTerm] = None,
    intake_year: Optional[int] = None,
    upcoming_only: bool = False,
    db: Session = Depends(get_db)
):
    """List all program intakes with optional filters"""
    query = db.query(ProgramIntake)
    
    if university_id:
        query = query.filter(ProgramIntake.university_id == university_id)
    if major_id:
        query = query.filter(ProgramIntake.major_id == major_id)
    if intake_term:
        query = query.filter(ProgramIntake.intake_term == intake_term)
    if intake_year:
        query = query.filter(ProgramIntake.intake_year == intake_year)
    if upcoming_only:
        now = datetime.utcnow()
        query = query.filter(ProgramIntake.application_deadline >= now)
    
    intakes = query.order_by(ProgramIntake.application_deadline).all()
    result = []
    for intake in intakes:
        result.append({
            'id': intake.id,
            'university_id': intake.university_id,
            'major_id': intake.major_id,
            'intake_term': intake.intake_term.value if hasattr(intake.intake_term, 'value') else str(intake.intake_term),
            'intake_year': intake.intake_year,
            'application_deadline': intake.application_deadline.isoformat() if intake.application_deadline else None,
            'documents_required': intake.documents_required,
            'tuition_per_semester': intake.tuition_per_semester,
            'tuition_per_year': intake.tuition_per_year,
            'application_fee': intake.application_fee,
            'accommodation_fee': intake.accommodation_fee,
            'service_fee': intake.service_fee,
            'medical_insurance_fee': intake.medical_insurance_fee,
            'teaching_language': intake.teaching_language if isinstance(intake.teaching_language, str) else (intake.teaching_language.value if intake.teaching_language else None),
            'duration_years': intake.duration_years,
            'degree_type': intake.degree_type if isinstance(intake.degree_type, str) else (intake.degree_type.value if intake.degree_type else None),
            'arrival_medical_checkup_fee': intake.arrival_medical_checkup_fee,
            'admission_process': intake.admission_process,
            'accommodation_note': intake.accommodation_note,
            'visa_extension_fee': intake.visa_extension_fee,
            'notes': intake.notes,
            'scholarship_info': intake.scholarship_info,
            'created_at': intake.created_at.isoformat() if intake.created_at else None,
            'updated_at': intake.updated_at.isoformat() if intake.updated_at else None,
        })
    return result

@router.get("/{intake_id}", response_model=ProgramIntakeResponse)
async def get_program_intake(intake_id: int, db: Session = Depends(get_db)):
    """Get a specific program intake by ID"""
    intake = db.query(ProgramIntake).filter(ProgramIntake.id == intake_id).first()
    if not intake:
        raise HTTPException(status_code=404, detail="Program intake not found")
    return {
        'id': intake.id,
        'university_id': intake.university_id,
        'major_id': intake.major_id,
        'intake_term': intake.intake_term.value if hasattr(intake.intake_term, 'value') else str(intake.intake_term),
        'intake_year': intake.intake_year,
        'application_deadline': intake.application_deadline.isoformat() if intake.application_deadline else None,
        'documents_required': intake.documents_required,
        'tuition_per_semester': intake.tuition_per_semester,
        'tuition_per_year': intake.tuition_per_year,
        'application_fee': intake.application_fee,
        'accommodation_fee': intake.accommodation_fee,
        'service_fee': intake.service_fee,
        'medical_insurance_fee': intake.medical_insurance_fee,
        'teaching_language': intake.teaching_language if isinstance(intake.teaching_language, str) else (intake.teaching_language.value if intake.teaching_language else None),
        'duration_years': intake.duration_years,
        'degree_type': intake.degree_type if isinstance(intake.degree_type, str) else (intake.degree_type.value if intake.degree_type else None),
        'arrival_medical_checkup_fee': intake.arrival_medical_checkup_fee,
        'admission_process': intake.admission_process,
        'accommodation_note': intake.accommodation_note,
        'visa_extension_fee': intake.visa_extension_fee,
        'notes': intake.notes,
        'scholarship_info': intake.scholarship_info,
        'created_at': intake.created_at.isoformat() if intake.created_at else None,
        'updated_at': intake.updated_at.isoformat() if intake.updated_at else None,
    }

@router.post("/", response_model=ProgramIntakeResponse, status_code=status.HTTP_201_CREATED)
async def create_program_intake(
    intake_data: ProgramIntakeCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new program intake (admin only)"""
    if current_user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Verify university and major exist
    university = db.query(University).filter(University.id == intake_data.university_id).first()
    if not university:
        raise HTTPException(status_code=404, detail="University not found")
    
    major = db.query(Major).filter(Major.id == intake_data.major_id).first()
    if not major:
        raise HTTPException(status_code=404, detail="Major not found")
    
    if major.university_id != university.id:
        raise HTTPException(status_code=400, detail="Major does not belong to the specified university")
    
    intake = ProgramIntake(**intake_data.dict())
    db.add(intake)
    db.commit()
    db.refresh(intake)
    return {
        'id': intake.id,
        'university_id': intake.university_id,
        'major_id': intake.major_id,
        'intake_term': intake.intake_term.value if hasattr(intake.intake_term, 'value') else str(intake.intake_term),
        'intake_year': intake.intake_year,
        'application_deadline': intake.application_deadline.isoformat() if intake.application_deadline else None,
        'documents_required': intake.documents_required,
        'tuition_per_semester': intake.tuition_per_semester,
        'tuition_per_year': intake.tuition_per_year,
        'application_fee': intake.application_fee,
        'accommodation_fee': intake.accommodation_fee,
        'service_fee': intake.service_fee,
        'medical_insurance_fee': intake.medical_insurance_fee,
        'teaching_language': intake.teaching_language if isinstance(intake.teaching_language, str) else (intake.teaching_language.value if intake.teaching_language else None),
        'duration_years': intake.duration_years,
        'degree_type': intake.degree_type if isinstance(intake.degree_type, str) else (intake.degree_type.value if intake.degree_type else None),
        'arrival_medical_checkup_fee': intake.arrival_medical_checkup_fee,
        'admission_process': intake.admission_process,
        'accommodation_note': intake.accommodation_note,
        'visa_extension_fee': intake.visa_extension_fee,
        'notes': intake.notes,
        'scholarship_info': intake.scholarship_info,
        'created_at': intake.created_at.isoformat() if intake.created_at else None,
        'updated_at': intake.updated_at.isoformat() if intake.updated_at else None,
    }

@router.put("/{intake_id}", response_model=ProgramIntakeResponse)
async def update_program_intake(
    intake_id: int,
    intake_data: ProgramIntakeUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update a program intake (admin only)"""
    if current_user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    intake = db.query(ProgramIntake).filter(ProgramIntake.id == intake_id).first()
    if not intake:
        raise HTTPException(status_code=404, detail="Program intake not found")
    
    update_data = intake_data.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(intake, field, value)
    
    db.commit()
    db.refresh(intake)
    return {
        'id': intake.id,
        'university_id': intake.university_id,
        'major_id': intake.major_id,
        'intake_term': intake.intake_term.value if hasattr(intake.intake_term, 'value') else str(intake.intake_term),
        'intake_year': intake.intake_year,
        'application_deadline': intake.application_deadline.isoformat() if intake.application_deadline else None,
        'documents_required': intake.documents_required,
        'tuition_per_semester': intake.tuition_per_semester,
        'tuition_per_year': intake.tuition_per_year,
        'application_fee': intake.application_fee,
        'accommodation_fee': intake.accommodation_fee,
        'service_fee': intake.service_fee,
        'medical_insurance_fee': intake.medical_insurance_fee,
        'teaching_language': intake.teaching_language if isinstance(intake.teaching_language, str) else (intake.teaching_language.value if intake.teaching_language else None),
        'duration_years': intake.duration_years,
        'degree_type': intake.degree_type if isinstance(intake.degree_type, str) else (intake.degree_type.value if intake.degree_type else None),
        'arrival_medical_checkup_fee': intake.arrival_medical_checkup_fee,
        'admission_process': intake.admission_process,
        'accommodation_note': intake.accommodation_note,
        'visa_extension_fee': intake.visa_extension_fee,
        'notes': intake.notes,
        'scholarship_info': intake.scholarship_info,
        'created_at': intake.created_at.isoformat() if intake.created_at else None,
        'updated_at': intake.updated_at.isoformat() if intake.updated_at else None,
    }

@router.delete("/{intake_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_program_intake(
    intake_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a program intake (admin only)"""
    if current_user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    intake = db.query(ProgramIntake).filter(ProgramIntake.id == intake_id).first()
    if not intake:
        raise HTTPException(status_code=404, detail="Program intake not found")
    
    db.delete(intake)
    db.commit()
    return None

