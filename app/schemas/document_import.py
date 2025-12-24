"""
Pydantic schemas for document import data extraction
These schemas define the strict JSON structure that the LLM must output
"""
from typing import List, Optional, Literal
from pydantic import BaseModel, Field, field_validator, model_validator
from datetime import date, datetime


# Enum values matching database models
DEGREE_LEVELS = ["Bachelor", "Master", "Phd", "Language Program", "Associate", "Vocational College", "Non Degree", "Junior high", "Senior high"]
TEACHING_LANGUAGES = ["Chinese", "English", "Bilingual"]
INTAKE_TERMS = ["March", "September", "Other"]


class FeeInfo(BaseModel):
    """Fee information extracted from document"""
    tuition_per_semester: Optional[float] = Field(None, description="Tuition per semester in CNY")
    tuition_per_year: Optional[float] = Field(None, description="Tuition per year in CNY")
    application_fee: Optional[float] = Field(None, description="Application fee in CNY")
    accommodation_fee: Optional[float] = Field(None, description="Accommodation fee (lower bound if range)")
    accommodation_fee_period: Optional[Literal["month", "year", "semester"]] = Field(None, description="Accommodation fee period")
    accommodation_note: Optional[str] = Field(None, description="Full accommodation fee text from document")
    service_fee: Optional[float] = Field(None, description="MalishaEdu service fee")
    medical_insurance_fee: Optional[float] = Field(None, description="Medical insurance fee")
    medical_insurance_fee_period: Optional[Literal["year", "semester"]] = Field(None, description="Medical insurance period")
    arrival_medical_checkup_fee: Optional[float] = Field(None, description="Arrival medical checkup fee")
    arrival_medical_checkup_is_one_time: Optional[bool] = Field(None, description="Is arrival medical checkup one-time?")
    visa_extension_fee: Optional[float] = Field(None, description="Visa extension fee")
    currency: Literal["CNY", "USD"] = Field("CNY", description="Currency for all fees")
    notes: Optional[str] = Field(None, description="Additional fee notes (registration fees, CSC deadlines, etc.)")


class RequirementsInfo(BaseModel):
    """Requirements extracted from document"""
    age_min: Optional[int] = Field(None, description="Minimum age requirement")
    age_max: Optional[int] = Field(None, description="Maximum age requirement")
    min_average_score: Optional[float] = Field(None, description="Minimum average score requirement")
    interview_required: Optional[bool] = Field(None, description="Interview required")
    written_test_required: Optional[bool] = Field(None, description="Written test required")
    acceptance_letter_required: Optional[bool] = Field(None, description="Acceptance letter required")
    inside_china_applicants_allowed: Optional[bool] = Field(None, description="Inside China applicants allowed")
    inside_china_extra_requirements: Optional[str] = Field(None, description="Extra requirements for inside China applicants")
    bank_statement_required: Optional[bool] = Field(None, description="Bank statement required")
    bank_statement_amount: Optional[float] = Field(None, description="Required bank statement amount")
    bank_statement_currency: Optional[Literal["CNY", "USD"]] = Field(None, description="Bank statement currency")
    bank_statement_note: Optional[str] = Field(None, description="Bank statement requirements note")
    hsk_required: Optional[bool] = Field(None, description="HSK required")
    hsk_level: Optional[int] = Field(None, description="HSK level required")
    hsk_min_score: Optional[int] = Field(None, description="HSK minimum score")
    english_test_required: Optional[bool] = Field(None, description="English test required")
    english_test_note: Optional[str] = Field(None, description="English test requirements (IELTS/TOEFL/etc)")


class DocumentInfo(BaseModel):
    """Document requirement extracted from document"""
    name: str = Field(..., description="Normalized document name (e.g., 'Passport', 'Transcript', 'Highest Degree Certificate')")
    is_required: bool = Field(True, description="Whether document is required")
    rules: Optional[str] = Field(None, description="Document rules/requirements (e.g., 'Notarized', 'IELTS 6.0')")
    applies_to: Optional[str] = Field(None, description="Applies to (e.g., 'if_applicable', 'under_18_only', 'inside_china_only')")


class ScholarshipInfo(BaseModel):
    """Scholarship information extracted from document"""
    name: str = Field(..., description="Scholarship name (e.g., 'CSC type B', 'Type A')")
    provider: Optional[str] = Field(None, description="Scholarship provider")
    notes: Optional[str] = Field(None, description="Scholarship notes/description")
    covers_tuition: Optional[bool] = Field(None, description="Covers tuition")
    covers_accommodation: Optional[bool] = Field(None, description="Covers accommodation")
    covers_insurance: Optional[bool] = Field(None, description="Covers insurance")
    tuition_waiver_percent: Optional[int] = Field(None, description="Tuition waiver percentage (0-100)")
    living_allowance_monthly: Optional[float] = Field(None, description="Monthly living allowance in CNY")
    living_allowance_yearly: Optional[float] = Field(None, description="Yearly living allowance in CNY")
    first_year_only: Optional[bool] = Field(None, description="Scholarship is first year only")
    renewal_required: Optional[bool] = Field(None, description="Renewal required")
    deadline: Optional[str] = Field(None, description="Scholarship deadline (YYYY-MM-DD)")
    eligibility_note: Optional[str] = Field(None, description="Eligibility notes (e.g., accommodation fee details)")


class ProgramIntakeInfo(BaseModel):
    """Program intake information extracted from document"""
    intake_term: Literal["March", "September", "Other"] = Field(..., description="Intake term")
    intake_year: int = Field(..., description="Intake year (e.g., 2026)")
    application_deadline: Optional[str] = Field(None, description="Application deadline (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)")
    deadline_type: Optional[str] = Field(None, description="Deadline type (e.g., 'University', 'CSC')")
    program_start_date: Optional[str] = Field(None, description="Program start date (YYYY-MM-DD)")
    fees: FeeInfo = Field(..., description="Fee information")
    requirements: RequirementsInfo = Field(..., description="Requirements information")
    documents: List[DocumentInfo] = Field(default_factory=list, description="Required documents")
    scholarships: List[ScholarshipInfo] = Field(default_factory=list, description="Available scholarships")
    scholarship_available: Optional[bool] = Field(None, description="Scholarship available (true/false/null)")
    scholarship_info: Optional[str] = Field(None, description="Scholarship summary text")


class MajorInfo(BaseModel):
    """Major/program information extracted from document"""
    name: str = Field(..., description="Major name exactly as in document")
    degree_level: Literal["Bachelor", "Master", "Phd", "Language Program", "Associate", "Vocational College", "Non Degree", "Junior high", "Senior high"] = Field(..., description="Degree level")
    teaching_language: Literal["Chinese", "English", "Bilingual"] = Field(..., description="Teaching language")
    duration_years: Optional[float] = Field(None, description="Duration in years (e.g., 2.0, 0.5 for one semester)")
    discipline: Optional[str] = Field(None, description="Discipline (e.g., 'Engineering', 'Business', 'Medicine')")
    category: Optional[Literal["Degree Program", "Non-degree/Language Program"]] = Field(None, description="Program category")
    keywords: Optional[List[str]] = Field(None, description="Keywords array (1-5 items, subject-only, no location/campus names)")
    is_featured: bool = Field(False, description="Is featured program")
    is_active: bool = Field(True, description="Is active program")
    intakes: List[ProgramIntakeInfo] = Field(default_factory=list, description="Program intakes for this major")


class MajorGroupInfo(BaseModel):
    """Group of majors that share the same properties and intakes (optimization for many majors)"""
    major_names: List[str] = Field(..., description="List of major names that share the same properties")
    degree_level: Literal["Bachelor", "Master", "Phd", "Language Program", "Associate", "Vocational College", "Non Degree", "Junior high", "Senior high"] = Field(..., description="Degree level (shared by all majors in group)")
    teaching_language: Literal["Chinese", "English", "Bilingual"] = Field(..., description="Teaching language (shared by all majors)")
    duration_years: Optional[float] = Field(None, description="Duration in years (shared by all majors)")
    discipline: Optional[str] = Field(None, description="Discipline (shared by all majors)")
    category: Optional[Literal["Degree Program", "Non-degree/Language Program"]] = Field(None, description="Program category (shared by all majors)")
    keywords: Optional[List[str]] = Field(None, description="Keywords array (shared by all majors)")
    is_featured: bool = Field(False, description="Is featured program (shared by all majors)")
    is_active: bool = Field(True, description="Is active program (shared by all majors)")
    intakes: List[ProgramIntakeInfo] = Field(default_factory=list, description="Program intakes (shared by all majors in group)")


class ExtractedData(BaseModel):
    """Complete extracted data from document"""
    university_name: str = Field(..., description="University name exactly as in document")
    majors: Optional[List[MajorInfo]] = Field(None, description="List of individual majors/programs (use when majors have different properties)")
    major_groups: Optional[List[MajorGroupInfo]] = Field(None, description="Groups of majors that share the same properties (optimization for many majors with same intake/fees/requirements)")
    errors: List[str] = Field(default_factory=list, description="List of errors/warnings encountered during extraction")
    
    @model_validator(mode='after')
    def validate_majors(self):
        """Custom validation to ensure at least one major source is provided"""
        if (not self.majors or len(self.majors) == 0) and (not self.major_groups or len(self.major_groups) == 0):
            raise ValueError("At least one major (in majors or major_groups) must be extracted")
        return self

