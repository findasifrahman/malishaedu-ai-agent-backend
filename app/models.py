from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey, JSON, Float, Enum as SQLEnum, TypeDecorator
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector
from app.database import Base
import enum

class UserRole(str, enum.Enum):
    STUDENT = "student"
    ADMIN = "admin"

class DocumentType(str, enum.Enum):
    PASSPORT = "passport"
    PASSPORT_PAGE = "passport_page"
    PHOTO = "photo"
    DIPLOMA = "diploma"
    TRANSCRIPT = "transcript"
    NON_CRIMINAL = "non_criminal"
    PHYSICAL_EXAM = "physical_exam"
    BANK_STATEMENT = "bank_statement"
    RECOMMENDATION_LETTER = "recommendation_letter"
    SELF_INTRO_VIDEO = "self_intro_video"
    STUDY_PLAN = "study_plan"
    ENGLISH_PROFICIENCY = "english_proficiency"
    CV_RESUME = "cv_resume"
    JW202_JW201 = "jw202_jw201"
    GUARANTEE_LETTER = "guarantee_letter"
    BANK_GUARANTOR_LETTER = "bank_guarantor_letter"

class ApplicationStatus(str, enum.Enum):
    NOT_APPLIED = "not_applied"
    DRAFT = "draft"
    APPLIED = "applied"
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUCCEEDED = "succeeded"

class ComplaintStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"

class DegreeLevel(str, enum.Enum):
    NON_DEGREE = "Non-degree"
    ASSOCIATE = "Associate"
    BACHELOR = "Bachelor"
    MASTER = "Master"
    DOCTORAL = "Doctoral (PhD)"
    LANGUAGE = "Language"
    SHORT_PROGRAM = "Short Program"
    STUDY_TOUR = "Study Tour Program"
    UPGRADE_JUNIOR_COLLEGE = "Upgrade from Junior College Student to University Student"

class TeachingLanguage(str, enum.Enum):
    CHINESE = "Chinese"
    ENGLISH = "English"
    BILINGUAL = "Bilingual"

class IntakeTerm(str, enum.Enum):
    MARCH = "March"
    SEPTEMBER = "September"
    OTHER = "Other"

class ApplicationStage(str, enum.Enum):
    LEAD = "lead"
    PRE_APPLICATION = "pre-application"
    SUBMITTED = "submitted"
    UNIVERSITY_OFFER = "university_offer"
    VISA_PROCESSING = "visa_processing"
    ARRIVED_IN_CHINA = "arrived_in_china"
    ENROLLED = "enrolled"

class ScholarshipPreference(str, enum.Enum):
    TYPE_A = "Type-A"  # Tuition free, accommodation free, stipend up to 35000 CNY (depends on university and major)
    TYPE_B = "Type-B"  # Tuition free, accommodation free, no stipend
    TYPE_C = "Type-C"  # Only tuition fee free
    TYPE_D = "Type-D"  # Only tuition fee free (alternative)
    PARTIAL_LOW = "Partial-Low"  # Partial Scholarship (<5000 CNY/year): 500 USD
    PARTIAL_MID = "Partial-Mid"  # Partial Scholarship (5100-10000 CNY/year): 350 USD
    PARTIAL_HIGH = "Partial-High"  # Partial Scholarship (10000-15000 CNY/year): 300 USD
    SELF_PAID = "Self-Paid"  # Self-Paid: 150 USD
    NONE = "None"  # No scholarship (for Language programs)

# Custom TypeDecorator to handle enum value mapping for VARCHAR columns
class ScholarshipPreferenceType(TypeDecorator):
    """Custom type that maps VARCHAR values to ScholarshipPreference enum values"""
    impl = String
    cache_ok = True
    
    def __init__(self):
        super().__init__(50)  # VARCHAR(50)
    
    def process_bind_param(self, value, dialect):
        """Convert enum to string value when writing to database"""
        if value is None:
            return None
        if isinstance(value, ScholarshipPreference):
            return value.value
        if isinstance(value, str):
            # Validate that the string is a valid enum value
            try:
                return ScholarshipPreference(value).value
            except ValueError:
                # If it's already a valid value string, return it
                valid_values = [e.value for e in ScholarshipPreference]
                if value in valid_values:
                    return value
                raise ValueError(f"Invalid scholarship preference: {value}")
        return str(value)
    
    def process_result_value(self, value, dialect):
        """Convert string value to enum when reading from database"""
        if value is None:
            return None
        if isinstance(value, ScholarshipPreference):
            return value
        # Map string value to enum member
        try:
            # Find enum member by value
            for member in ScholarshipPreference:
                if member.value == value:
                    return member
            # If not found, try direct lookup (for backwards compatibility)
            return ScholarshipPreference(value)
        except (ValueError, KeyError):
            # If value doesn't match any enum, return None or raise
            return None

class CSCAStatus(str, enum.Enum):
    NOT_REGISTERED = "not_registered"
    REGISTERED = "registered"
    TAKEN = "taken"
    SCORE_AVAILABLE = "score_available"

class EnglishTestType(str, enum.Enum):
    IELTS = "IELTS"
    TOEFL = "TOEFL"
    DUOLINGO = "Duolingo"
    NONE = "None"

# Users table
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    name = Column(String)
    phone = Column(String)
    country = Column(String)
    hashed_password = Column(String)
    role = Column(SQLEnum(UserRole), default=UserRole.STUDENT)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    conversations = relationship("Conversation", back_populates="user")
    student = relationship("Student", back_populates="user", uselist=False)
    complaints = relationship("Complaint", back_populates="user")

# Leads table
class Lead(Base):
    __tablename__ = "leads"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    email = Column(String)
    phone = Column(String)
    country = Column(String)
    device_fingerprint = Column(String, nullable=True)  # Keep for backward compatibility
    chat_session_id = Column(String, nullable=True, index=True)  # New: per-chat session identifier
    source = Column(String, default="chat")
    interested_university_id = Column(Integer, ForeignKey("universities.id"), nullable=True)
    interested_major_id = Column(Integer, ForeignKey("majors.id"), nullable=True)
    intake_term = Column(String, nullable=True)  # "March", "September", "Other"
    intake_year = Column(Integer, nullable=True)  # e.g., 2026
    notes = Column(Text, nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # Link to user when converted
    converted_at = Column(DateTime(timezone=True), nullable=True)  # When lead was converted to user
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    interested_university = relationship("University", foreign_keys=[interested_university_id])
    interested_major = relationship("Major", foreign_keys=[interested_major_id])

# Conversations table
class Conversation(Base):
    __tablename__ = "conversations"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    device_fingerprint = Column(String, nullable=True)  # Keep for backward compatibility
    chat_session_id = Column(String, nullable=True, index=True)  # New: per-chat session identifier for anonymous users
    messages = Column(JSON)  # Store last 12 messages
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    user = relationship("User", back_populates="conversations")

# RAG Documents table
class RAGDocument(Base):
    __tablename__ = "rag_documents"
    
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String)
    file_type = Column(String)  # pdf, txt, docx, csv
    content = Column(Text)
    meta_data = Column(JSON, name="metadata")  # university, program, intake, etc.
    uploaded_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    embeddings = relationship("RAGEmbedding", back_populates="document")

# RAG Embeddings table (with pgvector)
class RAGEmbedding(Base):
    __tablename__ = "rag_embeddings"
    
    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("rag_documents.id"))
    chunk_text = Column(Text)
    embedding = Column(Vector(1536))  # text-embedding-3-small dimension
    chunk_index = Column(Integer)
    meta_data = Column(JSON, name="metadata")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    document = relationship("RAGDocument", back_populates="embeddings")

# Universities table
class University(Base):
    __tablename__ = "universities"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    city = Column(String)
    province = Column(String)
    country = Column(String, default="China")
    is_partner = Column(Boolean, default=True)
    logo_url = Column(String, nullable=True)
    description = Column(Text)
    website = Column(String, nullable=True)
    contact_email = Column(String, nullable=True)
    contact_wechat = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    majors = relationship("Major", back_populates="university", cascade="all, delete-orphan")
    program_intakes = relationship("ProgramIntake", back_populates="university", cascade="all, delete-orphan")

# Majors table
class Major(Base):
    __tablename__ = "majors"
    
    id = Column(Integer, primary_key=True, index=True)
    university_id = Column(Integer, ForeignKey("universities.id"), nullable=False)
    name = Column(String, nullable=False)
    degree_level = Column(String)  # Changed from SQLEnum to String for flexibility
    teaching_language = Column(String)  # Changed from SQLEnum to String for flexibility
    duration_years = Column(Float)
    description = Column(Text)
    discipline = Column(String)  # Engineering, Business, Medicine, etc.
    is_featured = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    university = relationship("University", back_populates="majors")
    program_intakes = relationship("ProgramIntake", back_populates="major", cascade="all, delete-orphan")

# Program Intakes table
class ProgramIntake(Base):
    __tablename__ = "program_intakes"
    
    id = Column(Integer, primary_key=True, index=True)
    university_id = Column(Integer, ForeignKey("universities.id"), nullable=False)
    major_id = Column(Integer, ForeignKey("majors.id"), nullable=False)
    intake_term = Column(SQLEnum(IntakeTerm))
    intake_year = Column(Integer, nullable=False)
    application_deadline = Column(DateTime(timezone=True))
    documents_required = Column(Text)  # Comma-separated list, LLM will parse
    tuition_per_semester = Column(Float, nullable=True)
    tuition_per_year = Column(Float, nullable=True)
    application_fee = Column(Float, nullable=True)  # Non-refundable application fee
    accommodation_fee = Column(Float, nullable=True)  # Accommodation fee per year (LLM must know this is per year)
    service_fee = Column(Float, nullable=True)  # MalishaEdu service fee (only for successful application)
    medical_insurance_fee = Column(Float, nullable=True)  # Medical insurance fee (taken by university after successful application and arriving in China)
    teaching_language = Column(String, nullable=True)  # Override major's teaching language if different (changed from SQLEnum to String)
    duration_years = Column(Float, nullable=True)  # Duration in years (float, can override major's duration)
    degree_type = Column(String, nullable=True)  # Degree type for this specific intake (changed from SQLEnum to String)
    arrival_medical_checkup_fee = Column(Float, nullable=True, default=0)  # One-time medical checkup fee upon arrival (LLM should know this is one-time)
    admission_process = Column(Text, nullable=True)  # Admission process description/file_type
    accommodation_note = Column(Text, nullable=True)  # Notes about accommodation
    visa_extension_fee = Column(Float, nullable=True, default=0)  # Visa extension fee required each year (LLM should know this is annual)
    notes = Column(Text)  # Extra info like age requirements, interview needs, etc.
    scholarship_info = Column(Text)  # Scholarship amount and conditions - LLM must parse and calculate actual costs
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    university = relationship("University", back_populates="program_intakes")
    major = relationship("Major", back_populates="program_intakes")
    students = relationship("Student", back_populates="target_intake")
    applications = relationship("Application", back_populates="program_intake")

# Students table (enhanced)
class Student(Base):
    __tablename__ = "students"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True)
    
    # Identification & Contact
    full_name = Column(String)
    given_name = Column(String)
    family_name = Column(String)
    father_name = Column(String, nullable=True)
    mother_name = Column(String, nullable=True)
    gender = Column(String)
    country_of_citizenship = Column(String)
    current_country_of_residence = Column(String)
    date_of_birth = Column(DateTime)
    phone = Column(String)
    email = Column(String)
    wechat_id = Column(String, nullable=True)
    
    # Passport & Scores
    passport_number = Column(String)
    passport_expiry_date = Column(DateTime)
    passport_scanned_url = Column(String, nullable=True)
    passport_photo_url = Column(String, nullable=True)
    hsk_level = Column(Integer, nullable=True)  # 0-6
    hsk_certificate_url = Column(String, nullable=True)
    csca_status = Column(SQLEnum(CSCAStatus), default=CSCAStatus.NOT_REGISTERED)
    csca_score_math = Column(Float, nullable=True)
    csca_score_specialized_chinese = Column(Float, nullable=True)
    csca_score_physics = Column(Float, nullable=True)
    csca_score_chemistry = Column(Float, nullable=True)
    csca_report_url = Column(String, nullable=True)
    english_test_type = Column(SQLEnum(EnglishTestType), default=EnglishTestType.NONE)
    english_test_score = Column(Float, nullable=True)
    english_certificate_url = Column(String, nullable=True)
    
    # Academic Docs
    highest_degree_diploma_url = Column(String, nullable=True)
    highest_degree_name = Column(String, nullable=True)
    highest_degree_institution = Column(String, nullable=True)
    highest_degree_country = Column(String, nullable=True)
    highest_degree_year = Column(Integer, nullable=True)  # Year of graduation
    highest_degree_cgpa = Column(Float, nullable=True)  # CGPA/GPA score
    academic_transcript_url = Column(String, nullable=True)
    
    # Other Required Docs
    physical_examination_form_url = Column(String, nullable=True)
    police_clearance_url = Column(String, nullable=True)
    bank_statement_url = Column(String, nullable=True)
    recommendation_letter_1_url = Column(String, nullable=True)
    recommendation_letter_2_url = Column(String, nullable=True)
    guarantee_letter_url = Column(String, nullable=True)
    residence_permit_url = Column(String, nullable=True)
    study_certificate_china_url = Column(String, nullable=True)
    application_form_url = Column(String, nullable=True)
    study_plan_url = Column(String, nullable=True)  # Study plan / motivation letter
    passport_page_url = Column(String, nullable=True)  # Additional passport pages
    cv_resume_url = Column(String, nullable=True)  # CV/Resume
    jw202_jw201_url = Column(String, nullable=True)  # JW202/JW201 form
    bank_guarantor_letter_url = Column(String, nullable=True)  # Bank guarantor letter (if bank guarantee is not in student's name)
    relation_with_guarantor = Column(String, nullable=True)  # Relationship with guarantor (optional)
    is_the_bank_guarantee_in_students_name = Column(Boolean, nullable=False, default=True)  # Mandatory field
    others_1_url = Column(String, nullable=True)
    others_2_url = Column(String, nullable=True)
    
    # Application Intent
    target_university_id = Column(Integer, ForeignKey("universities.id"), nullable=True)
    target_major_id = Column(Integer, ForeignKey("majors.id"), nullable=True)
    target_intake_id = Column(Integer, ForeignKey("program_intakes.id"), nullable=True)
    study_level = Column(SQLEnum(DegreeLevel), nullable=True)
    scholarship_preference = Column(ScholarshipPreferenceType(), nullable=True)
    application_stage = Column(SQLEnum(ApplicationStage), default=ApplicationStage.LEAD)
    missing_documents = Column(Text, nullable=True)  # Auto-filled summary
    
    # COVA (China Visa Application) Information
    home_address = Column(Text, nullable=True)  # Permanent home address
    current_address = Column(Text, nullable=True)  # Current residence address
    emergency_contact_name = Column(String, nullable=True)
    emergency_contact_phone = Column(String, nullable=True)
    emergency_contact_relationship = Column(String, nullable=True)  # e.g., "Father", "Mother", "Spouse"
    education_history = Column(JSON, nullable=True)  # JSON array of education records
    employment_history = Column(JSON, nullable=True)  # JSON array of employment records
    family_members = Column(JSON, nullable=True)  # JSON array of family member info
    planned_arrival_date = Column(DateTime, nullable=True)  # When student plans to arrive in China
    intended_address_china = Column(Text, nullable=True)  # Usually university dorm address
    previous_visa_china = Column(Boolean, default=False)  # Has student had a Chinese visa before?
    previous_visa_details = Column(Text, nullable=True)  # Details about previous visa if any
    previous_travel_to_china = Column(Boolean, default=False)  # Has student traveled to China before?
    previous_travel_details = Column(Text, nullable=True)  # Details about previous travel
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    user = relationship("User", back_populates="student")
    applications = relationship("Application", back_populates="student")
    documents = relationship("Document", back_populates="student")
    target_university = relationship("University", foreign_keys=[target_university_id])
    target_major = relationship("Major", foreign_keys=[target_major_id])
    target_intake = relationship("ProgramIntake", foreign_keys=[target_intake_id])

# Applications table - tracks multiple applications per student
class Application(Base):
    __tablename__ = "applications"
    
    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    program_intake_id = Column(Integer, ForeignKey("program_intakes.id"), nullable=False)  # Link to specific intake
    degree_level = Column(String, nullable=True)  # Degree level: Bachelor, Master, PhD, Language, etc. (for LLM understanding)
    application_state = Column(SQLEnum(ApplicationStatus), default=ApplicationStatus.NOT_APPLIED)  # Application state: not_applied, applied, rejected, succeeded
    application_fee_paid = Column(Boolean, default=False)  # Whether application fee has been paid
    application_fee_amount = Column(Float, nullable=True)  # Amount paid (stored at time of application)
    payment_fee_paid = Column(Float, default=0.0)  # Total payment fee paid so far
    payment_fee_due = Column(Float, default=0.0)  # Total payment fee due
    payment_fee_required = Column(Float, default=0.0)  # Total payment fee required for this program
    scholarship_preference = Column(ScholarshipPreferenceType(), nullable=True)  # Type-A, Type-B, Type-C, Type-D, or None for Language programs
    status = Column(SQLEnum(ApplicationStatus), default=ApplicationStatus.DRAFT)  # Legacy field, use application_state instead
    admin_notes = Column(Text, nullable=True)  # Admin can add notes about the application
    submitted_at = Column(DateTime(timezone=True), nullable=True)  # When student submitted application
    admin_reviewed_at = Column(DateTime(timezone=True), nullable=True)  # When admin started processing
    result = Column(String, nullable=True)  # "accepted", "rejected", "waitlisted", etc.
    result_notes = Column(Text, nullable=True)  # Admin notes about result
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    student = relationship("Student", back_populates="applications")
    program_intake = relationship("ProgramIntake")

# Documents table
class Document(Base):
    __tablename__ = "documents"
    
    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"))
    application_id = Column(Integer, ForeignKey("applications.id"), nullable=True)
    document_type = Column(SQLEnum(DocumentType))
    r2_url = Column(String)
    filename = Column(String)
    file_size = Column(Integer)
    extracted_data = Column(JSON)  # For passport parsing, etc.
    verified = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    student = relationship("Student", back_populates="documents")

# Student Documents table - stores verification results
class StudentDocument(Base):
    __tablename__ = "student_documents"
    
    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    document_type = Column(String, nullable=False)  # e.g., "passport", "diploma", etc.
    file_url = Column(String, nullable=False)  # Temporary URL before verification
    r2_url = Column(String, nullable=True)  # Cloudflare R2 URL after verification
    filename = Column(String, nullable=False)
    file_size = Column(Integer, nullable=True)
    verification_status = Column(String, nullable=False)  # "ok", "blurry", "fake", "incomplete"
    verification_reason = Column(Text, nullable=True)  # AI explanation
    extracted_data = Column(JSON, nullable=True)  # Extracted data from AI
    verified = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    student = relationship("Student")

# Complaints table
class Complaint(Base):
    __tablename__ = "complaints"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    device_fingerprint = Column(String, nullable=True)
    subject = Column(String)
    message = Column(Text)
    status = Column(SQLEnum(ComplaintStatus), default=ComplaintStatus.PENDING)
    admin_response = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    
    user = relationship("User", back_populates="complaints")

# Admin Settings table
class AdminSettings(Base):
    __tablename__ = "admin_settings"
    
    id = Column(Integer, primary_key=True, index=True)
    setting_key = Column(String, unique=True)
    setting_value = Column(JSON)
    description = Column(Text)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    updated_by = Column(Integer, ForeignKey("users.id"))

