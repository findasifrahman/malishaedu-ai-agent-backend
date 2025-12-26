from sqlalchemy import Column, Integer, BigInteger, String, Text, DateTime, Date, Boolean, ForeignKey, JSON, Float, Enum as SQLEnum, TypeDecorator, SmallInteger
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector
from app.database import Base
from typing import Optional
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
    ACCEPTANCE_LETTER = "acceptance_letter"

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
    JUNIOR_HIGH = "Junior high"
    SENIOR_HIGH = "Senior high"
    NON_DEGREE = "Non Degree"
    ASSOCIATE = "Associate"
    VOCATIONAL_COLLEGE = "Vocational College"
    BACHELOR = "Bachelor"
    MASTER = "Master"
    PHD = "Phd"
    LANGUAGE_PROGRAM = "Language Program"
    
    @staticmethod
    def canonicalize(value: Optional[str]) -> Optional[str]:
        """
        Canonicalize degree level string to enum value.
        Returns canonical value or None if not recognized.
        """
        if not value:
            return None
        
        value_lower = str(value).strip().lower()
        
        # Mapping to canonical values
        mapping = {
            "bachelor": "Bachelor",
            "bsc": "Bachelor",
            "b.sc": "Bachelor",
            "undergraduate": "Bachelor",
            "undergrad": "Bachelor",
            "master": "Master",
            "masters": "Master",
            "msc": "Master",
            "m.sc": "Master",
            "postgraduate": "Master",
            "post-graduate": "Master",
            "graduate": "Master",
            "phd": "Phd",
            "ph.d": "Phd",
            "ph.d.": "Phd",
            "doctorate": "Phd",
            "doctoral": "Phd",
            "dphil": "Phd",
            "language": "Language Program",
            "language program": "Language Program",
            "non-degree": "Non Degree",
            "non degree": "Non Degree",
            "foundation": "Language Program",
            "foundation program": "Language Program",
            "diploma": "Associate",
            "associate": "Associate",
            "assoc": "Associate",
            "vocational college": "Vocational College",
            "vocational": "Vocational College",
            "junior high": "Junior high",
            "senior high": "Senior high"
        }
        
        return mapping.get(value_lower, None)

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
    NONE = "None"
    IELTS = "IELTS"
    TOEFL = "TOEFL"
    GRE = "GRE"
    GMAT = "GMAT"
    DUOLINGO = "Duolingo"
    TOEIC = "TOEIC"
    PTE = "PTE"
    NATIVE_LANGUAGE = "Native Language"
    OTHER = "Other"

class DegreeMedium(str, enum.Enum):
    ENGLISH = "English"
    CHINESE = "Chinese"
    NATIVE = "Native"

class MaritalStatus(str, enum.Enum):
    SINGLE = "Single"
    MARRIED = "Married"

class Religion(str, enum.Enum):
    ANGLICAN = "Anglican"
    ATHEISM = "Atheism"
    MORMON = "Mormon"
    CHRISTIANITY = "Christianity"
    JUDAISM = "Judaism"
    CATHOLICISM = "Catholicism"
    EASTERN_ORTHODOXY = "Eastern Orthodoxy"
    HINDUISM = "Hinduism"
    ISLAM = "Islam"
    BUDDHISM = "Buddhism"
    TAOISM = "Taoism"
    NONE = "None"
    LUTHERANISM = "Lutheranism"
    OTHER = "Other"

class HSKKLevel(str, enum.Enum):
    BEGINNER = "Beginner"
    ELEMENTARY = "Elementary"
    INTERMEDIATE = "Intermediate"
    ADVANCED = "Advanced"

class NativeLanguage(str, enum.Enum):
    CHINESE = "Chinese"
    ENGLISH = "English"
    CROATIAN = "Croatian"
    BULGARIAN = "Bulgarian"
    GERMAN = "German"
    LATVIAN = "latvian"
    CZECH = "Czech"
    FRENCH = "French"
    SLOVAK = "Slovak"
    ITALIAN = "Italian"
    DUTCH = "Dutch"
    MACEDONIAN = "Macedonian"
    MALTESE = "Maltese"
    LATIN = "Latin"
    FINNISH = "Finnish"
    ALBANIAN = "Albanian"
    IRISH_LANGUAGE = "Irish language"
    CATALAN = "Catalan"
    BELARUSSIAN = "Belarussian"
    ICELANDIC = "Icelandic"
    SERBIA_CROATIAN = "Serbia - Croatian"
    ROMANIAN = "Romanian"
    PORTUGUESE = "Portuguese"
    SWEDISH = "Swedish"
    SLOVENIAN = "Slovenian"
    UKRAINIAN = "Ukrainian"
    SPANISH = "Spanish"
    GREEK = "Greek"
    CREOLE = "Creole"
    DANISH = "Danish"
    GREENLANDIC = "Greenlandic"
    UZBEK = "Uzbek"
    ARABIC = "Arabic"
    PERSIAN = "Persian"
    KYRGYZ = "Kyrgyz"
    BURMESE = "Burmese"
    LAO = "Lao"
    AZERBAIJANI = "Azerbaijani"
    PASHTO = "Pashto"
    FILIPINO = "Filipino"
    ARMENIAN = "Armenian"
    KAZAK = "Kazak"
    RUSSIAN = "Russian"
    KHMER = "Khmer"
    BHUTANESE = "Bhutanese"
    MALAY = "Malay"
    NEPALI = "Nepali"
    TURKISH = "Turkish"
    DAI_LANGUAGE = "Dai language"
    HEBREW = "Hebrew"
    JAPANESE = "Japanese"
    VIETNAMESE = "Vietnamese"
    SWAHILI = "swahili"
    BURUNDI = "Burundi"
    KOREAN = "korean"
    RWANDA = "Rwanda"
    AFRIKAANS = "Afrikaans"
    SANGO = "Sango"
    URDU = "Urdu"
    HINDI = "Hindi"
    MONGOLIAN = "Mongolian"
    INDONESIAN = "Indonesian"
    BENGALI = "Bengali"
    SINHALA = "Sinhala"
    TURKMAN = "Turkman"
    TMIL = "Tmil"
    OTHER = "Other"

class HighestEducationLevel(str, enum.Enum):
    JUNIOR_HIGH = "Junior high"
    SENIOR_HIGH = "Senior high"
    TECHNICAL_SECONDARY = "Technical secondary"
    VOCATIONAL_COLLEGE = "Vocational College"
    BACHELOR = "Bachelor"
    MASTER = "Master"
    DR_PHD = "Dr./Phd"

class Occupation(str, enum.Enum):
    EMPLOYEE = "Employee"
    STUDENT = "Student"
    TEACHER = "Teacher"
    DOCTOR = "Doctor"
    LABOURER = "Labourer"
    ARMY_SERVICE = "Army service"
    ENGINEERS = "Engineers"
    SCHOLARS = "Scholars"
    HOUSEWIFE = "Housewife"
    RETIRED = "Retired"
    MANAGER = "Manager"
    OFFICER = "Officer"
    FARMER = "Farmer"
    REPORTER = "Reporter"
    MONKS_AND_PRIESTS = "Monks and priests"
    RELIGIOUS = "Religious"
    OTHERS = "Others"

class LanguageProficiency(str, enum.Enum):
    NONE = "None"
    POOR = "Poor"
    FAIR = "Fair"
    GOOD = "Good"
    EXCELLENT = "Excellent"

class HSKLevel(str, enum.Enum):
    NONE = "none"
    HSK_LEVEL_1 = "HSK LEVEL 1"
    HSK_LEVEL_2 = "HSK LEVEL 2"
    HSK_LEVEL_3 = "HSK LEVEL 3"
    HSK_LEVEL_4 = "HSK LEVEL 4"
    HSK_LEVEL_5 = "HSK LEVEL 5"
    HSK_LEVEL_6 = "HSK LEVEL 6"
    HSK_LEVEL_7 = "HSK LEVEL 7"
    HSK_LEVEL_8 = "HSK LEVEL 8"
    HSK_LEVEL_9 = "HSK LEVEL 9"

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



# New filtered-retrieval RAG schema
class RagSource(Base):
    __tablename__ = "rag_sources"
    
    id = Column(BigInteger, primary_key=True, index=True)
    name = Column(Text, nullable=False)
    doc_type = Column(Text, nullable=False)  # csca, b2c_study, b2b_partner, people_contact, service_policy
    audience = Column(Text, nullable=False, default='student')  # student, partner
    version = Column(Text, nullable=True)
    status = Column(Text, nullable=False, default='active')  # active, archived, deprecated
    source_url = Column(Text, nullable=True)
    last_verified_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    chunks = relationship("RagChunk", back_populates="source", cascade="all, delete-orphan")

class RagChunk(Base):
    __tablename__ = "rag_chunks"
    
    id = Column(BigInteger, primary_key=True, index=True)
    source_id = Column(BigInteger, ForeignKey("rag_sources.id", ondelete="CASCADE"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    chunk_hash = Column(Text, nullable=False, unique=True)  # MD5 hash for deduplication
    content = Column(Text, nullable=False)
    embedding = Column(Vector(1536), nullable=False)  # text-embedding-3-small dimension
    priority = Column(SmallInteger, nullable=False, default=3)  # 1=high, 2=medium, 3=low
    meta_data = Column(JSON, name="metadata", nullable=False, default={})  # Python attr 'meta_data' maps to DB column 'metadata'
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    source = relationship("RagSource", back_populates="chunks")

# Universities table
class University(Base):
    __tablename__ = "universities"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    name_cn = Column(String, nullable=True)  # Chinese name
    city = Column(String)
    province = Column(String)
    country = Column(String, default="China")
    is_partner = Column(Boolean, default=True)
    is_active = Column(Boolean, default=True)  # Whether university is active
    university_ranking = Column(Integer, nullable=True)
    world_ranking_band = Column(String, nullable=True)  # e.g., "301-400"
    national_ranking = Column(Integer, nullable=True)  # National ranking in China
    aliases = Column(JSON, nullable=True)  # Array of aliases for matching (e.g., ["Beihang", "BUAA"])
    project_tags = Column(JSON, nullable=True)  # Array of project tags (e.g., ["211", "985", "C9"])
    default_currency = Column(String, default="CNY")  # Default currency for fees
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
    name_cn = Column(String, nullable=True)  # Chinese name
    degree_level = Column(String)  # Changed from SQLEnum to String for flexibility
    teaching_language = Column(String)  # Changed from SQLEnum to String for flexibility
    duration_years = Column(Float)
    description = Column(Text)
    discipline = Column(String)  # Engineering, Business, Medicine, etc.
    category = Column(String, nullable=True)  # "Non-degree/Language Program" vs "Degree Program"
    keywords = Column(JSON, nullable=True)  # Array of keywords for matching (e.g., ["physics", "applied physics"])
    is_featured = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)  # Whether major is active
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
    
    # ========== NEW FIELDS - Program Start & Deadline ==========
    program_start_date = Column(Date, nullable=True)  # Program start date
    deadline_type = Column(String, nullable=True)  # University deadline vs CSC deadline, etc.
    
    # ========== NEW FIELDS - Scholarship ==========
    scholarship_available = Column(Boolean, nullable=True)  # NULL=unknown, True/False confirmed
    
    # ========== NEW FIELDS - Age Requirements ==========
    age_min = Column(Integer, nullable=True)  # Minimum age requirement
    age_max = Column(Integer, nullable=True)  # Maximum age requirement
    
    # ========== NEW FIELDS - Academic Requirements ==========
    min_average_score = Column(Float, nullable=True)  # Minimum average score requirement
    
    # ========== NEW FIELDS - Test/Interview Requirements ==========
    interview_required = Column(Boolean, nullable=True)  # Interview required
    written_test_required = Column(Boolean, nullable=True)  # Written test required
    acceptance_letter_required = Column(Boolean, nullable=True)  # Acceptance letter required
    
    # ========== NEW FIELDS - Inside China Applicants ==========
    inside_china_applicants_allowed = Column(Boolean, nullable=True)  # Inside China applicants allowed
    inside_china_extra_requirements = Column(Text, nullable=True)  # Extra requirements for inside China applicants
    
    # ========== NEW FIELDS - Bank Statement Requirements ==========
    bank_statement_required = Column(Boolean, nullable=True)  # Bank statement required
    bank_statement_amount = Column(Float, nullable=True)  # Required bank statement amount
    bank_statement_currency = Column(String, nullable=True)  # USD/CNY
    bank_statement_note = Column(Text, nullable=True)  # e.g., "≥ $5000"
    
    # ========== NEW FIELDS - Language Requirements ==========
    hsk_required = Column(Boolean, nullable=True)  # HSK required
    hsk_level = Column(Integer, nullable=True)  # HSK level required (e.g., 5)
    hsk_min_score = Column(Integer, nullable=True)  # HSK minimum score (e.g., 180)
    english_test_required = Column(Boolean, nullable=True)  # English test required
    english_test_note = Column(Text, nullable=True)  # IELTS/TOEFL/PTE etc when you have it
    
    # ========== NEW FIELDS - Currency & Fee Periods ==========
    currency = Column(String, default="CNY")  # Currency for fees
    accommodation_fee_period = Column(String, nullable=True)  # month/year/semester; docs vary
    medical_insurance_fee_period = Column(String, nullable=True)  # often per year
    arrival_medical_checkup_is_one_time = Column(Boolean, default=True)  # One-time medical checkup
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    university = relationship("University", back_populates="program_intakes")
    major = relationship("Major", back_populates="program_intakes")
    students = relationship("Student", back_populates="target_intake")
    applications = relationship("Application", back_populates="program_intake")
    program_documents = relationship("ProgramDocument", back_populates="program_intake", cascade="all, delete-orphan")
    program_intake_scholarships = relationship("ProgramIntakeScholarship", back_populates="program_intake", cascade="all, delete-orphan")
    program_exam_requirements = relationship("ProgramExamRequirement", back_populates="program_intake", cascade="all, delete-orphan")

# Program Documents table (normalized documents_required)
class ProgramDocument(Base):
    __tablename__ = "program_documents"
    
    id = Column(Integer, primary_key=True, index=True)
    program_intake_id = Column(Integer, ForeignKey("program_intakes.id"), nullable=False)
    name = Column(String, nullable=False)  # e.g., Passport, Photo, Transcript, Police Clearance
    is_required = Column(Boolean, default=True)  # Whether this document is required
    rules = Column(Text, nullable=True)  # e.g., "Study plan 800+ words", "video 3–5 minutes"
    applies_to = Column(String, nullable=True)  # e.g., "inside_china_only"
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    program_intake = relationship("ProgramIntake", back_populates="program_documents")

# Scholarships table
class Scholarship(Base):
    __tablename__ = "scholarships"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)  # e.g., CSC, HuaShan, Freshman Scholarship
    provider = Column(String, nullable=True)  # University/CSC/etc
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    program_intake_scholarships = relationship("ProgramIntakeScholarship", back_populates="scholarship")

# Program Intake Scholarships table (many-to-many relationship)
class ProgramIntakeScholarship(Base):
    __tablename__ = "program_intake_scholarships"
    
    id = Column(Integer, primary_key=True, index=True)
    program_intake_id = Column(Integer, ForeignKey("program_intakes.id"), nullable=False)
    scholarship_id = Column(Integer, ForeignKey("scholarships.id"), nullable=False)
    covers_tuition = Column(Boolean, nullable=True)
    covers_accommodation = Column(Boolean, nullable=True)
    covers_insurance = Column(Boolean, nullable=True)
    tuition_waiver_percent = Column(Integer, nullable=True)  # e.g., 50% waived
    living_allowance_monthly = Column(Float, nullable=True)  # e.g., 3500/month
    living_allowance_yearly = Column(Float, nullable=True)  # e.g., 36000/year
    first_year_only = Column(Boolean, nullable=True)  # Huashan says first year only
    renewal_required = Column(Boolean, nullable=True)  # Huashan reapply every year
    deadline = Column(Date, nullable=True)  # Scholarship deadline can differ from program deadline
    eligibility_note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    program_intake = relationship("ProgramIntake", back_populates="program_intake_scholarships")
    scholarship = relationship("Scholarship", back_populates="program_intake_scholarships")

# Program Exam Requirements table
class ProgramExamRequirement(Base):
    __tablename__ = "program_exam_requirements"
    
    id = Column(Integer, primary_key=True, index=True)
    program_intake_id = Column(Integer, ForeignKey("program_intakes.id"), nullable=False)
    exam_name = Column(Text, nullable=False)  # e.g., HSK, CSCA, IELTS, TOEFL
    required = Column(Boolean, default=True)  # Whether this exam is required
    subjects = Column(Text, nullable=True)  # For CSCA: Math/Physics/Chemistry etc.
    min_level = Column(Integer, nullable=True)  # For HSK level
    min_score = Column(Integer, nullable=True)  # For HSK score, IELTS band, TOEFL score
    exam_language = Column(Text, nullable=True)  # e.g., "English version CSCA required"
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    program_intake = relationship("ProgramIntake", back_populates="program_exam_requirements")

# Partner table
class Partner(Base):
    __tablename__ = "partners"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(Text, nullable=False)  # Contact person name
    company_name = Column(Text, nullable=True)
    phone1 = Column(Text, nullable=True)
    phone2 = Column(Text, nullable=True)
    email = Column(String, unique=True, nullable=False, index=True)
    city = Column(Text, nullable=True)
    country = Column(Text, nullable=True)
    full_address = Column(Text, nullable=True)
    website = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    password = Column(String, nullable=False)  # Hashed password
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    students = relationship("Student", back_populates="partner")

# Students table (enhanced)
class Student(Base):
    __tablename__ = "students"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True)
    partner_id = Column(Integer, ForeignKey("partners.id"), nullable=True)  # Link to partner
    
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
    video_url = Column(String, nullable=True)  # 3-5 Minutes Video Url
    
    # Passport & Scores
    passport_number = Column(String)
    passport_expiry_date = Column(DateTime)
    passport_scanned_url = Column(String, nullable=True)
    passport_photo_url = Column(String, nullable=True)
    hsk_score = Column(Float, nullable=True)  # HSK score
    level_of_hsk = Column(SQLEnum(HSKLevel), nullable=True)  # HSK level dropdown
    hsk_test_score_report_no = Column(String, nullable=True)  # HSK Test Score Report No.
    hsk_certificate_date = Column(DateTime, nullable=True)  # HSK certificate date
    hsk_certificate_url = Column(String, nullable=True)
    hskk_level = Column(SQLEnum(HSKKLevel), nullable=True)  # HSKK level (dropdown)
    hskk_score = Column(Float, nullable=True)  # HSKK score
    csca_status = Column(SQLEnum(CSCAStatus), default=CSCAStatus.NOT_REGISTERED)
    csca_score_math = Column(Float, nullable=True)
    csca_score_specialized_chinese = Column(Float, nullable=True)
    csca_score_physics = Column(Float, nullable=True)
    csca_score_chemistry = Column(Float, nullable=True)
    csca_report_url = Column(String, nullable=True)
    english_test_type = Column(SQLEnum(EnglishTestType), default=EnglishTestType.NONE)
    english_test_score = Column(Float, nullable=True)
    english_certificate_url = Column(String, nullable=True)
    other_certificate_english_name = Column(String, nullable=True)  # Other Certificate for english name
    
    # Personal Information
    marital_status = Column(SQLEnum(MaritalStatus), nullable=True)
    religion = Column(SQLEnum(Religion), nullable=True)
    occupation = Column(SQLEnum(Occupation), nullable=True)
    native_language = Column(SQLEnum(NativeLanguage), nullable=True)
    employer_or_institution_affiliated = Column(String, nullable=True)
    health_status = Column(Text, nullable=True)
    hobby = Column(Text, nullable=True)  # e.g., sports, etc.
    is_ethnic_chinese = Column(Boolean, nullable=True, default=False)
    chinese_language_proficiency = Column(SQLEnum(LanguageProficiency), nullable=True)
    english_language_proficiency = Column(SQLEnum(LanguageProficiency), nullable=True)
    other_language_proficiency = Column(Text, nullable=True)
    
    # Academic Docs
    highest_degree_diploma_url = Column(String, nullable=True)
    highest_degree_name = Column(SQLEnum(HighestEducationLevel), nullable=True)  # Highest Level of Education Completed/to be Completed
    highest_degree_medium = Column(SQLEnum(DegreeMedium), nullable=True)  # English, Chinese, or Native
    highest_degree_institution = Column(String, nullable=True)
    highest_degree_country = Column(String, nullable=True)
    highest_degree_year = Column(Integer, nullable=True)  # Year of graduation
    highest_degree_cgpa = Column(Float, nullable=True)  # CGPA/GPA score
    academic_transcript_url = Column(String, nullable=True)
    number_of_published_papers = Column(Integer, nullable=True)  # Number of published papers
    
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
    acceptance_letter_url = Column(String, nullable=True)  # Acceptance letter from university
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
    
    # Additional fields from COVA form
    criminal_record = Column(Boolean, nullable=True, default=False)  # Have you ever had a criminal record?
    criminal_record_details = Column(Text, nullable=True)  # Details if yes
    financial_supporter = Column(JSON, nullable=True)  # Financial supporter information (name, tel, organization, address, relationship, email)
    guarantor_in_china = Column(JSON, nullable=True)  # Guarantor in China (name, phone_number, mobile, email, address, organization)
    social_media_accounts = Column(JSON, nullable=True)  # Social media accounts (Facebook, LinkedIn, QQ, Skype, WeChat, Twitter, DingTalk, Instagram)
    studied_in_china = Column(Boolean, nullable=True, default=False)  # Have you ever studied online or offline at any institution in China?
    studied_in_china_details = Column(Text, nullable=True)  # Details if yes
    work_experience = Column(Boolean, nullable=True, default=False)  # Do you have work experience?
    work_experience_details = Column(JSON, nullable=True)  # Work experience details
    worked_in_china = Column(Boolean, nullable=True, default=False)  # Have you ever worked in China?
    worked_in_china_details = Column(Text, nullable=True)  # Details if yes
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    user = relationship("User", back_populates="student")
    applications = relationship("Application", back_populates="student")
    documents = relationship("Document", back_populates="student")
    target_university = relationship("University", foreign_keys=[target_university_id])
    target_major = relationship("Major", foreign_keys=[target_major_id])
    target_intake = relationship("ProgramIntake", foreign_keys=[target_intake_id])
    partner = relationship("Partner", back_populates="students")

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

