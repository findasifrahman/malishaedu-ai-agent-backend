from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, date
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
    
    # ========== NEW FIELDS - Program Start & Deadline ==========
    program_start_date: Optional[date] = None  # Program start date
    deadline_type: Optional[str] = None  # University deadline vs CSC deadline, etc.
    
    # ========== NEW FIELDS - Scholarship ==========
    scholarship_available: Optional[bool] = None  # NULL=unknown, True/False confirmed
    
    # ========== NEW FIELDS - Age Requirements ==========
    age_min: Optional[int] = None  # Minimum age requirement
    age_max: Optional[int] = None  # Maximum age requirement
    
    # ========== NEW FIELDS - Academic Requirements ==========
    min_average_score: Optional[float] = None  # Minimum average score requirement
    
    # ========== NEW FIELDS - Test/Interview Requirements ==========
    interview_required: Optional[bool] = None  # Interview required
    written_test_required: Optional[bool] = None  # Written test required
    acceptance_letter_required: Optional[bool] = None  # Acceptance letter required
    
    # ========== NEW FIELDS - Inside China Applicants ==========
    inside_china_applicants_allowed: Optional[bool] = None  # Inside China applicants allowed
    inside_china_extra_requirements: Optional[str] = None  # Extra requirements for inside China applicants
    
    # ========== NEW FIELDS - Bank Statement Requirements ==========
    bank_statement_required: Optional[bool] = None  # Bank statement required
    bank_statement_amount: Optional[float] = None  # Required bank statement amount
    bank_statement_currency: Optional[str] = None  # USD/CNY
    bank_statement_note: Optional[str] = None  # e.g., "≥ $5000"
    
    # ========== NEW FIELDS - Language Requirements ==========
    hsk_required: Optional[bool] = None  # HSK required
    hsk_level: Optional[int] = None  # HSK level required (e.g., 5)
    hsk_min_score: Optional[int] = None  # HSK minimum score (e.g., 180)
    english_test_required: Optional[bool] = None  # English test required
    english_test_note: Optional[str] = None  # IELTS/TOEFL/PTE etc when you have it
    
    # ========== NEW FIELDS - Currency & Fee Periods ==========
    currency: Optional[str] = "CNY"  # Currency for fees
    accommodation_fee_period: Optional[str] = None  # month/year/semester; docs vary
    medical_insurance_fee_period: Optional[str] = None  # often per year
    arrival_medical_checkup_is_one_time: Optional[bool] = True  # One-time medical checkup

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
    
    # ========== NEW FIELDS - Program Start & Deadline ==========
    program_start_date: Optional[date] = None
    deadline_type: Optional[str] = None
    
    # ========== NEW FIELDS - Scholarship ==========
    scholarship_available: Optional[bool] = None
    
    # ========== NEW FIELDS - Age Requirements ==========
    age_min: Optional[int] = None
    age_max: Optional[int] = None
    
    # ========== NEW FIELDS - Academic Requirements ==========
    min_average_score: Optional[float] = None
    
    # ========== NEW FIELDS - Test/Interview Requirements ==========
    interview_required: Optional[bool] = None
    written_test_required: Optional[bool] = None
    acceptance_letter_required: Optional[bool] = None
    
    # ========== NEW FIELDS - Inside China Applicants ==========
    inside_china_applicants_allowed: Optional[bool] = None
    inside_china_extra_requirements: Optional[str] = None
    
    # ========== NEW FIELDS - Bank Statement Requirements ==========
    bank_statement_required: Optional[bool] = None
    bank_statement_amount: Optional[float] = None
    bank_statement_currency: Optional[str] = None
    bank_statement_note: Optional[str] = None
    
    # ========== NEW FIELDS - Language Requirements ==========
    hsk_required: Optional[bool] = None
    hsk_level: Optional[int] = None
    hsk_min_score: Optional[int] = None
    english_test_required: Optional[bool] = None
    english_test_note: Optional[str] = None
    
    # ========== NEW FIELDS - Currency & Fee Periods ==========
    currency: Optional[str] = None
    accommodation_fee_period: Optional[str] = None
    medical_insurance_fee_period: Optional[str] = None
    arrival_medical_checkup_is_one_time: Optional[bool] = None

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
    
    # ========== NEW FIELDS - Program Start & Deadline ==========
    program_start_date: Optional[str]  # Program start date
    deadline_type: Optional[str]  # University deadline vs CSC deadline, etc.
    
    # ========== NEW FIELDS - Scholarship ==========
    scholarship_available: Optional[bool]  # NULL=unknown, True/False confirmed
    
    # ========== NEW FIELDS - Age Requirements ==========
    age_min: Optional[int]  # Minimum age requirement
    age_max: Optional[int]  # Maximum age requirement
    
    # ========== NEW FIELDS - Academic Requirements ==========
    min_average_score: Optional[float]  # Minimum average score requirement
    
    # ========== NEW FIELDS - Test/Interview Requirements ==========
    interview_required: Optional[bool]  # Interview required
    written_test_required: Optional[bool]  # Written test required
    acceptance_letter_required: Optional[bool]  # Acceptance letter required
    
    # ========== NEW FIELDS - Inside China Applicants ==========
    inside_china_applicants_allowed: Optional[bool]  # Inside China applicants allowed
    inside_china_extra_requirements: Optional[str]  # Extra requirements for inside China applicants
    
    # ========== NEW FIELDS - Bank Statement Requirements ==========
    bank_statement_required: Optional[bool]  # Bank statement required
    bank_statement_amount: Optional[float]  # Required bank statement amount
    bank_statement_currency: Optional[str]  # USD/CNY
    bank_statement_note: Optional[str]  # e.g., "≥ $5000"
    
    # ========== NEW FIELDS - Language Requirements ==========
    hsk_required: Optional[bool]  # HSK required
    hsk_level: Optional[int]  # HSK level required (e.g., 5)
    hsk_min_score: Optional[int]  # HSK minimum score (e.g., 180)
    english_test_required: Optional[bool]  # English test required
    english_test_note: Optional[str]  # IELTS/TOEFL/PTE etc when you have it
    
    # ========== NEW FIELDS - Currency & Fee Periods ==========
    currency: Optional[str]  # Currency for fees
    accommodation_fee_period: Optional[str]  # month/year/semester; docs vary
    medical_insurance_fee_period: Optional[str]  # often per year
    arrival_medical_checkup_is_one_time: Optional[bool]  # One-time medical checkup
    
    created_at: str
    updated_at: Optional[str]
    
    class Config:
        from_attributes = True

async def _list_program_intakes(
    university_id: Optional[int] = None,
    major_id: Optional[int] = None,
    intake_term: Optional[IntakeTerm] = None,
    intake_year: Optional[int] = None,
    teaching_language: Optional[str] = None,
    upcoming_only: bool = False,
    search: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db)
):
    """List program intakes with optional filters, search, and pagination"""
    from sqlalchemy import or_
    
    query = db.query(ProgramIntake)
    
    if university_id:
        query = query.filter(ProgramIntake.university_id == university_id)
    if major_id:
        query = query.filter(ProgramIntake.major_id == major_id)
    if intake_term:
        query = query.filter(ProgramIntake.intake_term == intake_term)
    if intake_year:
        query = query.filter(ProgramIntake.intake_year == intake_year)
    if teaching_language:
        query = query.filter(ProgramIntake.teaching_language.ilike(f"%{teaching_language}%"))
    if upcoming_only:
        now = datetime.utcnow()
        query = query.filter(ProgramIntake.application_deadline >= now)
    
    # Search functionality
    if search:
        search_filter = or_(
            ProgramIntake.notes.ilike(f"%{search}%"),
            ProgramIntake.scholarship_info.ilike(f"%{search}%"),
            ProgramIntake.admission_process.ilike(f"%{search}%")
        )
        query = query.filter(search_filter)
    
    # Get total count before pagination
    total = query.count()
    
    # Apply pagination
    offset = (page - 1) * page_size
    intakes = query.order_by(ProgramIntake.application_deadline).offset(offset).limit(page_size).all()
    
    def build_intake_response(intake):
        """Helper function to build intake response dictionary"""
        return {
            'id': intake.id,
            'university_id': intake.university_id,
            'university_name': intake.university.name if intake.university else None,
            'major_id': intake.major_id,
            'major_name': intake.major.name if intake.major else None,
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
            # ========== NEW FIELDS ==========
            'program_start_date': intake.program_start_date.isoformat() if intake.program_start_date else None,
            'deadline_type': intake.deadline_type,
            'scholarship_available': intake.scholarship_available,
            'age_min': intake.age_min,
            'age_max': intake.age_max,
            'min_average_score': intake.min_average_score,
            'interview_required': intake.interview_required,
            'written_test_required': intake.written_test_required,
            'acceptance_letter_required': intake.acceptance_letter_required,
            'inside_china_applicants_allowed': intake.inside_china_applicants_allowed,
            'inside_china_extra_requirements': intake.inside_china_extra_requirements,
            'bank_statement_required': intake.bank_statement_required,
            'bank_statement_amount': intake.bank_statement_amount,
            'bank_statement_currency': intake.bank_statement_currency,
            'bank_statement_note': intake.bank_statement_note,
            'hsk_required': intake.hsk_required,
            'hsk_level': intake.hsk_level,
            'hsk_min_score': intake.hsk_min_score,
            'english_test_required': intake.english_test_required,
            'english_test_note': intake.english_test_note,
            'currency': intake.currency,
            'accommodation_fee_period': intake.accommodation_fee_period,
            'medical_insurance_fee_period': intake.medical_insurance_fee_period,
            'arrival_medical_checkup_is_one_time': intake.arrival_medical_checkup_is_one_time,
            'created_at': intake.created_at.isoformat() if intake.created_at else None,
            'updated_at': intake.updated_at.isoformat() if intake.updated_at else None,
        }
    
    result = []
    for intake in intakes:
        result.append(build_intake_response(intake))
    
    return {
        'items': result,
        'total': total,
        'page': page,
        'page_size': page_size,
        'total_pages': (total + page_size - 1) // page_size
    }

@router.get("")
async def list_program_intakes(
    university_id: Optional[int] = None,
    major_id: Optional[int] = None,
    intake_term: Optional[IntakeTerm] = None,
    intake_year: Optional[int] = None,
    teaching_language: Optional[str] = None,
    upcoming_only: bool = False,
    search: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db)
):
    return await _list_program_intakes(university_id=university_id, major_id=major_id, intake_term=intake_term, intake_year=intake_year, teaching_language=teaching_language, upcoming_only=upcoming_only, search=search, page=page, page_size=page_size, db=db)

@router.get("/")
async def list_program_intakes_with_slash(
    university_id: Optional[int] = None,
    major_id: Optional[int] = None,
    intake_term: Optional[IntakeTerm] = None,
    intake_year: Optional[int] = None,
    teaching_language: Optional[str] = None,
    upcoming_only: bool = False,
    search: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db)
):
    return await _list_program_intakes(university_id=university_id, major_id=major_id, intake_term=intake_term, intake_year=intake_year, teaching_language=teaching_language, upcoming_only=upcoming_only, search=search, page=page, page_size=page_size, db=db)

@router.get("/{intake_id}", response_model=ProgramIntakeResponse)
async def get_program_intake(intake_id: int, db: Session = Depends(get_db)):
    """Get a specific program intake by ID"""
    intake = db.query(ProgramIntake).filter(ProgramIntake.id == intake_id).first()
    if not intake:
        raise HTTPException(status_code=404, detail="Program intake not found")
    
    def build_intake_response(intake):
        """Helper function to build intake response dictionary"""
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
            # ========== NEW FIELDS ==========
            'program_start_date': intake.program_start_date.isoformat() if intake.program_start_date else None,
            'deadline_type': intake.deadline_type,
            'scholarship_available': intake.scholarship_available,
            'age_min': intake.age_min,
            'age_max': intake.age_max,
            'min_average_score': intake.min_average_score,
            'interview_required': intake.interview_required,
            'written_test_required': intake.written_test_required,
            'acceptance_letter_required': intake.acceptance_letter_required,
            'inside_china_applicants_allowed': intake.inside_china_applicants_allowed,
            'inside_china_extra_requirements': intake.inside_china_extra_requirements,
            'bank_statement_required': intake.bank_statement_required,
            'bank_statement_amount': intake.bank_statement_amount,
            'bank_statement_currency': intake.bank_statement_currency,
            'bank_statement_note': intake.bank_statement_note,
            'hsk_required': intake.hsk_required,
            'hsk_level': intake.hsk_level,
            'hsk_min_score': intake.hsk_min_score,
            'english_test_required': intake.english_test_required,
            'english_test_note': intake.english_test_note,
            'currency': intake.currency,
            'accommodation_fee_period': intake.accommodation_fee_period,
            'medical_insurance_fee_period': intake.medical_insurance_fee_period,
            'arrival_medical_checkup_is_one_time': intake.arrival_medical_checkup_is_one_time,
            'created_at': intake.created_at.isoformat() if intake.created_at else None,
            'updated_at': intake.updated_at.isoformat() if intake.updated_at else None,
        }
    
    return build_intake_response(intake)

async def _create_program_intake(
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
    
    def build_intake_response(intake):
        """Helper function to build intake response dictionary"""
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
            # ========== NEW FIELDS ==========
            'program_start_date': intake.program_start_date.isoformat() if intake.program_start_date else None,
            'deadline_type': intake.deadline_type,
            'scholarship_available': intake.scholarship_available,
            'age_min': intake.age_min,
            'age_max': intake.age_max,
            'min_average_score': intake.min_average_score,
            'interview_required': intake.interview_required,
            'written_test_required': intake.written_test_required,
            'acceptance_letter_required': intake.acceptance_letter_required,
            'inside_china_applicants_allowed': intake.inside_china_applicants_allowed,
            'inside_china_extra_requirements': intake.inside_china_extra_requirements,
            'bank_statement_required': intake.bank_statement_required,
            'bank_statement_amount': intake.bank_statement_amount,
            'bank_statement_currency': intake.bank_statement_currency,
            'bank_statement_note': intake.bank_statement_note,
            'hsk_required': intake.hsk_required,
            'hsk_level': intake.hsk_level,
            'hsk_min_score': intake.hsk_min_score,
            'english_test_required': intake.english_test_required,
            'english_test_note': intake.english_test_note,
            'currency': intake.currency,
            'accommodation_fee_period': intake.accommodation_fee_period,
            'medical_insurance_fee_period': intake.medical_insurance_fee_period,
            'arrival_medical_checkup_is_one_time': intake.arrival_medical_checkup_is_one_time,
            'created_at': intake.created_at.isoformat() if intake.created_at else None,
            'updated_at': intake.updated_at.isoformat() if intake.updated_at else None,
        }
    
    return build_intake_response(intake)

@router.post("", response_model=ProgramIntakeResponse, status_code=status.HTTP_201_CREATED)
async def create_program_intake(
    intake_data: ProgramIntakeCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return await _create_program_intake(intake_data=intake_data, current_user=current_user, db=db)

@router.post("/", response_model=ProgramIntakeResponse, status_code=status.HTTP_201_CREATED)
async def create_program_intake_with_slash(
    intake_data: ProgramIntakeCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return await _create_program_intake(intake_data=intake_data, current_user=current_user, db=db)

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
    
    def build_intake_response(intake):
        """Helper function to build intake response dictionary"""
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
            # ========== NEW FIELDS ==========
            'program_start_date': intake.program_start_date.isoformat() if intake.program_start_date else None,
            'deadline_type': intake.deadline_type,
            'scholarship_available': intake.scholarship_available,
            'age_min': intake.age_min,
            'age_max': intake.age_max,
            'min_average_score': intake.min_average_score,
            'interview_required': intake.interview_required,
            'written_test_required': intake.written_test_required,
            'acceptance_letter_required': intake.acceptance_letter_required,
            'inside_china_applicants_allowed': intake.inside_china_applicants_allowed,
            'inside_china_extra_requirements': intake.inside_china_extra_requirements,
            'bank_statement_required': intake.bank_statement_required,
            'bank_statement_amount': intake.bank_statement_amount,
            'bank_statement_currency': intake.bank_statement_currency,
            'bank_statement_note': intake.bank_statement_note,
            'hsk_required': intake.hsk_required,
            'hsk_level': intake.hsk_level,
            'hsk_min_score': intake.hsk_min_score,
            'english_test_required': intake.english_test_required,
            'english_test_note': intake.english_test_note,
            'currency': intake.currency,
            'accommodation_fee_period': intake.accommodation_fee_period,
            'medical_insurance_fee_period': intake.medical_insurance_fee_period,
            'arrival_medical_checkup_is_one_time': intake.arrival_medical_checkup_is_one_time,
            'created_at': intake.created_at.isoformat() if intake.created_at else None,
            'updated_at': intake.updated_at.isoformat() if intake.updated_at else None,
        }
    
    return build_intake_response(intake)

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

