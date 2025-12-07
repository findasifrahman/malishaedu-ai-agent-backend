from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, field_validator
from typing import Optional, List, Union
from datetime import datetime, date
from app.database import get_db
from app.models import Student, User, Application, Document, DocumentType, ApplicationStatus
from app.routers.auth import get_current_user

router = APIRouter()

class StudentProfile(BaseModel):
    # Basic identification
    full_name: Optional[str] = None
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    father_name: Optional[str] = None
    mother_name: Optional[str] = None
    gender: Optional[str] = None
    date_of_birth: Optional[Union[str, datetime, date]] = None
    country_of_citizenship: Optional[str] = None
    current_country_of_residence: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    wechat_id: Optional[str] = None
    
    # Passport information
    passport_number: Optional[str] = None
    passport_expiry_date: Optional[Union[str, datetime, date]] = None
    
    class Config:
        # Allow empty strings to be converted to None
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None,
            date: lambda v: v.isoformat() if v else None
        }
    
    # Scores
    hsk_score: Optional[float] = None
    hsk_certificate_date: Optional[Union[str, datetime, date]] = None
    hskk_level: Optional[str] = None
    hskk_score: Optional[float] = None
    csca_status: Optional[str] = None
    csca_score_math: Optional[float] = None
    csca_score_specialized_chinese: Optional[float] = None
    csca_score_physics: Optional[float] = None
    csca_score_chemistry: Optional[float] = None
    english_test_type: Optional[str] = None
    english_test_score: Optional[float] = None
    
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
    planned_arrival_date: Optional[Union[str, datetime, date]] = None
    intended_address_china: Optional[str] = None
    previous_visa_china: Optional[bool] = None
    previous_visa_details: Optional[str] = None
    previous_travel_to_china: Optional[bool] = None
    previous_travel_details: Optional[str] = None
    
    # Personal Information
    marital_status: Optional[str] = None
    religion: Optional[str] = None
    occupation: Optional[str] = None
    
    # Highest degree information
    highest_degree_name: Optional[str] = None
    highest_degree_medium: Optional[str] = None
    highest_degree_institution: Optional[str] = None
    highest_degree_country: Optional[str] = None
    highest_degree_year: Optional[int] = None
    highest_degree_cgpa: Optional[float] = None
    number_of_published_papers: Optional[int] = None
    
    # Guarantor information
    relation_with_guarantor: Optional[str] = None
    is_the_bank_guarantee_in_students_name: Optional[bool] = None
    
    @field_validator('full_name', 'given_name', 'family_name', 'father_name', 'mother_name', 
                     'gender', 'country_of_citizenship', 'current_country_of_residence', 
                     'phone', 'email', 'wechat_id', 'passport_number', 'home_address', 
                     'current_address', 'emergency_contact_name', 'emergency_contact_phone', 
                     'emergency_contact_relationship', 'intended_address_china', 
                     'previous_visa_details', 'previous_travel_details', 'occupation', mode='before')
    @classmethod
    def empty_str_to_none(cls, v):
        """Convert empty strings to None for optional string fields"""
        if isinstance(v, str) and v.strip() == '':
            return None
        return v
    
    @field_validator('date_of_birth', 'passport_expiry_date', 'planned_arrival_date', 'hsk_certificate_date', mode='before')
    @classmethod
    def parse_date(cls, v):
        """Parse date string to datetime object"""
        if v is None or v == '':
            return None
        if isinstance(v, datetime) or isinstance(v, date):
            return v
        if isinstance(v, str):
            # Try parsing YYYY-MM-DD format (from HTML date input)
            try:
                parsed = datetime.strptime(v, '%Y-%m-%d')
                return parsed
            except:
                pass
            # Try parsing ISO format
            try:
                return datetime.fromisoformat(v.replace('Z', '+00:00'))
            except:
                pass
            # Try parsing other common formats
            try:
                return datetime.strptime(v, '%Y-%m-%dT%H:%M:%S')
            except:
                pass
        return v

class ApplicationCreate(BaseModel):
    program_intake_id: int  # Link to specific program intake
    degree_level: Optional[str] = None  # Degree level: Bachelor, Master, PhD, Language, etc.
    scholarship_preference: Optional[str] = None  # Type-A, Type-B, Type-C, Type-D, or None

class ApplicationResponse(BaseModel):
    id: int
    program_intake_id: int
    university_name: str
    major_name: str
    intake_term: str
    intake_year: int
    degree_level: Optional[str] = None
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
        "father_name": student.father_name,
        "mother_name": student.mother_name,
        "gender": student.gender,
        "date_of_birth": student.date_of_birth.isoformat() if student.date_of_birth else None,
        "country_of_citizenship": student.country_of_citizenship,
        "current_country_of_residence": student.current_country_of_residence,
        "phone": student.phone,
        "email": student.email,
        "wechat_id": student.wechat_id,
        "passport_number": student.passport_number,
        "passport_expiry_date": student.passport_expiry_date.isoformat() if student.passport_expiry_date else None,
        "hsk_score": student.hsk_score,
        "hsk_certificate_date": student.hsk_certificate_date.isoformat() if student.hsk_certificate_date else None,
        "hskk_level": student.hskk_level.value if student.hskk_level else None,
        "hskk_score": student.hskk_score,
        "csca_status": student.csca_status.value if student.csca_status else None,
        "csca_score_math": student.csca_score_math,
        "csca_score_specialized_chinese": student.csca_score_specialized_chinese,
        "csca_score_physics": student.csca_score_physics,
        "csca_score_chemistry": student.csca_score_chemistry,
        "english_test_type": student.english_test_type.value if student.english_test_type else None,
        "english_test_score": student.english_test_score,
        "marital_status": student.marital_status.value if student.marital_status else None,
        "religion": student.religion.value if student.religion else None,
        "occupation": student.occupation,
        "target_university_id": student.target_university_id,
        "target_major_id": student.target_major_id,
        "target_intake_id": student.target_intake_id,
        "study_level": student.study_level.value if student.study_level else None,
        "scholarship_preference": student.scholarship_preference.value if student.scholarship_preference else None,
        "application_stage": student.application_stage.value if student.application_stage else None,
        "home_address": student.home_address,
        "current_address": student.current_address,
        "highest_degree_name": student.highest_degree_name,
        "highest_degree_medium": student.highest_degree_medium.value if student.highest_degree_medium else None,
        "highest_degree_institution": student.highest_degree_institution,
        "highest_degree_country": student.highest_degree_country,
        "highest_degree_year": student.highest_degree_year,
        "highest_degree_cgpa": student.highest_degree_cgpa,
        "number_of_published_papers": student.number_of_published_papers,
        "relation_with_guarantor": student.relation_with_guarantor,
        "is_the_bank_guarantee_in_students_name": student.is_the_bank_guarantee_in_students_name,
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
        "created_at": student.created_at.isoformat() if student.created_at else None,
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
    if profile.father_name is not None:
        student.father_name = profile.father_name
    if profile.mother_name is not None:
        student.mother_name = profile.mother_name
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
        # Convert to datetime if it's a date object
        if isinstance(profile.passport_expiry_date, date) and not isinstance(profile.passport_expiry_date, datetime):
            student.passport_expiry_date = datetime.combine(profile.passport_expiry_date, datetime.min.time())
        else:
            student.passport_expiry_date = profile.passport_expiry_date
    
    # Scores
    if profile.hsk_score is not None:
        student.hsk_score = profile.hsk_score
    if profile.hsk_certificate_date is not None:
        if isinstance(profile.hsk_certificate_date, date) and not isinstance(profile.hsk_certificate_date, datetime):
            student.hsk_certificate_date = datetime.combine(profile.hsk_certificate_date, datetime.min.time())
        else:
            student.hsk_certificate_date = profile.hsk_certificate_date
    if profile.hskk_level is not None:
        from app.models import HSKKLevel
        try:
            student.hskk_level = HSKKLevel(profile.hskk_level)
        except ValueError:
            pass
    if profile.hskk_score is not None:
        student.hskk_score = profile.hskk_score
    if profile.csca_status is not None:
        from app.models import CSCAStatus
        try:
            student.csca_status = CSCAStatus(profile.csca_status)
        except ValueError:
            pass
    if profile.csca_score_math is not None:
        student.csca_score_math = profile.csca_score_math
    if profile.csca_score_specialized_chinese is not None:
        student.csca_score_specialized_chinese = profile.csca_score_specialized_chinese
    if profile.csca_score_physics is not None:
        student.csca_score_physics = profile.csca_score_physics
    if profile.csca_score_chemistry is not None:
        student.csca_score_chemistry = profile.csca_score_chemistry
    if profile.english_test_type is not None:
        from app.models import EnglishTestType
        try:
            student.english_test_type = EnglishTestType(profile.english_test_type)
        except ValueError:
            pass
    if profile.english_test_score is not None:
        student.english_test_score = profile.english_test_score
    
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
        # Convert to datetime if it's a date object
        if isinstance(profile.planned_arrival_date, date) and not isinstance(profile.planned_arrival_date, datetime):
            student.planned_arrival_date = datetime.combine(profile.planned_arrival_date, datetime.min.time())
        else:
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
    
    # Personal Information
    if profile.marital_status is not None:
        from app.models import MaritalStatus
        try:
            student.marital_status = MaritalStatus(profile.marital_status)
        except ValueError:
            pass
    if profile.religion is not None:
        from app.models import Religion
        try:
            student.religion = Religion(profile.religion)
        except ValueError:
            pass
    if profile.occupation is not None:
        student.occupation = profile.occupation
    
    # Highest degree information
    if hasattr(profile, 'highest_degree_name') and profile.highest_degree_name is not None:
        student.highest_degree_name = profile.highest_degree_name
    if profile.highest_degree_medium is not None and profile.highest_degree_medium != '':
        from app.models import DegreeMedium
        try:
            student.highest_degree_medium = DegreeMedium(profile.highest_degree_medium)
        except (ValueError, TypeError):
            # If invalid value, leave it unchanged
            pass
    if hasattr(profile, 'highest_degree_institution') and profile.highest_degree_institution is not None:
        student.highest_degree_institution = profile.highest_degree_institution
    if hasattr(profile, 'highest_degree_country') and profile.highest_degree_country is not None:
        student.highest_degree_country = profile.highest_degree_country
    if hasattr(profile, 'highest_degree_year') and profile.highest_degree_year is not None:
        student.highest_degree_year = profile.highest_degree_year
    if hasattr(profile, 'highest_degree_cgpa') and profile.highest_degree_cgpa is not None:
        student.highest_degree_cgpa = profile.highest_degree_cgpa
    if profile.number_of_published_papers is not None:
        student.number_of_published_papers = profile.number_of_published_papers
    
    # Guarantor information
    if hasattr(profile, 'relation_with_guarantor') and profile.relation_with_guarantor is not None:
        student.relation_with_guarantor = profile.relation_with_guarantor
    if hasattr(profile, 'is_the_bank_guarantee_in_students_name') and profile.is_the_bank_guarantee_in_students_name is not None:
        student.is_the_bank_guarantee_in_students_name = profile.is_the_bank_guarantee_in_students_name
    
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
    
    # Get degree_level from request or try to get from program_intake
    degree_level = application.degree_level
    if not degree_level and program_intake.degree_type:
        degree_level = program_intake.degree_type
    
    # Set scholarship preference if provided
    scholarship_pref = None
    if application.scholarship_preference:
        from app.models import ScholarshipPreference
        try:
            scholarship_pref = ScholarshipPreference(application.scholarship_preference)
        except ValueError:
            pass
    
    # Calculate payment fee required
    from app.services.service_charge_calculator import calculate_payment_fee_required
    
    # Get application fee from program_intakes table (use 0 if null or 0)
    university_application_fee = program_intake.application_fee if (program_intake.application_fee and program_intake.application_fee > 0) else 0.0
    
    payment_fee_required = calculate_payment_fee_required(
        application_fee_rmb=university_application_fee,  # From program_intakes.application_fee (or 0 if null/0)
        degree_level=degree_level or program_intake.degree_type or "",
        teaching_language=program_intake.teaching_language or "",
        scholarship_preference=application.scholarship_preference,
        tuition_per_year=program_intake.tuition_per_year,
        accommodation_fee=program_intake.accommodation_fee,
        scholarship_info=program_intake.scholarship_info
    )
    
    # Create new application
    new_application = Application(
        student_id=student.id,
        program_intake_id=application.program_intake_id,
        degree_level=degree_level,  # Store degree level for LLM understanding
        application_fee_amount=program_intake.application_fee,
        application_fee_paid=False,  # Fee payment handled separately
        payment_fee_required=payment_fee_required,  # Total payment required (application fee + deposit + service charge)
        payment_fee_due=payment_fee_required,  # Initially, all is due
        payment_fee_paid=0.0,  # Nothing paid yet
        scholarship_preference=scholarship_pref,
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
        degree_level=new_application.degree_level or program_intake.degree_type,
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
                "degree_level": app.degree_level or intake.degree_type,  # Use stored degree_level or fallback to program_intake
                "application_fee": intake.application_fee,
                "application_fee_paid": app.application_fee_paid,
                "application_state": app.application_state.value if app.application_state else (app.status.value if app.status else "not_applied"),
                "status": app.status.value if app.status else "draft",  # Legacy field
                "payment_fee_paid": app.payment_fee_paid or 0.0,
                "payment_fee_due": app.payment_fee_due or 0.0,
                "payment_fee_required": app.payment_fee_required or 0.0,
                "scholarship_preference": app.scholarship_preference.value if app.scholarship_preference else None,
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

class ApplicationUpdate(BaseModel):
    scholarship_preference: Optional[str] = None

@router.put("/applications/{application_id}")
async def update_application(
    application_id: int,
    application_update: ApplicationUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update an application (student can only update scholarship_preference)"""
    student = db.query(Student).filter(Student.user_id == current_user.id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student profile not found")
    
    application = db.query(Application).filter(
        Application.id == application_id,
        Application.student_id == student.id
    ).first()
    
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    
    # Student can only update scholarship_preference
    if application_update.scholarship_preference is not None:
        from app.models import ScholarshipPreference
        try:
            application.scholarship_preference = ScholarshipPreference(application_update.scholarship_preference)
            
            # Recalculate payment_fee_required when scholarship preference changes
            if application.program_intake:
                intake = application.program_intake
                from app.services.service_charge_calculator import calculate_payment_fee_required
                
                # Get application fee from program_intakes table (use 0 if null or 0)
                university_application_fee = intake.application_fee if (intake.application_fee and intake.application_fee > 0) else 0.0
                
                payment_fee_required = calculate_payment_fee_required(
                    application_fee_rmb=university_application_fee,  # From program_intakes.application_fee (or 0 if null/0)
                    degree_level=application.degree_level or intake.degree_type or "",
                    teaching_language=intake.teaching_language or "",
                    scholarship_preference=application_update.scholarship_preference,
                    tuition_per_year=intake.tuition_per_year,
                    accommodation_fee=intake.accommodation_fee,
                    scholarship_info=intake.scholarship_info
                )
                
                # Update payment fields
                old_payment_required = application.payment_fee_required or 0.0
                application.payment_fee_required = payment_fee_required
                
                # Adjust payment_fee_due based on the difference
                # If payment_fee_paid exists, recalculate due amount
                payment_paid = application.payment_fee_paid or 0.0
                application.payment_fee_due = max(0.0, payment_fee_required - payment_paid)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid scholarship preference")
    
    db.commit()
    db.refresh(application)
    
    # Return updated application
    if application.program_intake:
        intake = application.program_intake
        return {
            "id": application.id,
            "program_intake_id": application.program_intake_id,
            "university_name": intake.university.name,
            "major_name": intake.major.name,
            "intake_term": intake.intake_term.value,
            "intake_year": intake.intake_year,
            "application_state": application.application_state.value if application.application_state else "not_applied",
            "scholarship_preference": application.scholarship_preference.value if application.scholarship_preference else None,
            "payment_fee_paid": application.payment_fee_paid or 0.0,
            "payment_fee_due": application.payment_fee_due or 0.0,
            "payment_fee_required": application.payment_fee_required or 0.0
        }
    
    return {"message": "Application updated successfully"}

@router.delete("/applications/{application_id}")
async def delete_application(
    application_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete an application (only if not submitted)"""
    student = db.query(Student).filter(Student.user_id == current_user.id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student profile not found")
    
    application = db.query(Application).filter(
        Application.id == application_id,
        Application.student_id == student.id
    ).first()
    
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    
    # Only allow deletion if not submitted
    if application.application_state and application.application_state.value in ['applied', 'submitted', 'under_review', 'accepted', 'succeeded']:
        raise HTTPException(status_code=400, detail="Cannot delete submitted application")
    
    db.delete(application)
    db.commit()
    
    return {"message": "Application deleted successfully"}

