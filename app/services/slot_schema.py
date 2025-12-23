"""
Slot Schema - Extended PartnerQueryState with all required fields
"""
from dataclasses import dataclass, field
from typing import Optional, Dict, Any


@dataclass
class RequirementFocus:
    """Focus flags for admission requirements"""
    docs: bool = True
    exams: bool = True
    bank: bool = True
    age: bool = True
    inside_china: bool = True
    deadline: bool = True
    accommodation: bool = True
    country: bool = True


@dataclass
class ScholarshipFocus:
    """Focus flags for scholarship queries"""
    any: bool = True
    csc: bool = False
    university: bool = False


@dataclass
class PaginationConfig:
    """Pagination configuration"""
    limit: int = 10
    offset: int = 0


@dataclass
class PartnerQueryState:
    """
    Extended structured state for partner queries.
    Includes intent, confidence, and all query parameters.
    """
    # Intent and confidence
    intent: str = "general"  # enum: PAGINATION, LIST_UNIVERSITIES, LIST_PROGRAMS, ADMISSION_REQUIREMENTS, SCHOLARSHIP, FEES, COMPARISON, PROGRAM_DETAILS, GENERAL
    confidence: float = 0.0  # 0.0 to 1.0
    
    # Core query parameters
    degree_level: Optional[str] = None  # "Language", "Non-degree", "Bachelor", "Master", "PhD", "Diploma", null
    major_query: Optional[str] = None  # Free text user intent for major/subject
    university_query: Optional[str] = None  # University name
    teaching_language: Optional[str] = None  # "English", "Chinese", "Any", null
    intake_term: Optional[str] = None  # "March", "September", "Any", null
    intake_year: Optional[int] = None
    
    # Duration
    duration_years_target: Optional[float] = None  # Supports 0.33, 0.67, 1.3, etc.
    duration_constraint: Optional[str] = None  # "exact", "min", "max", "approx", null
    
    # Requirements flags
    wants_requirements: bool = False
    req_focus: RequirementFocus = field(default_factory=RequirementFocus)
    
    # Fees flags
    wants_fees: bool = False
    wants_free_tuition: bool = False  # Filter for programs with zero tuition (tuition_per_year = 0 or NULL AND tuition_per_semester = 0 or NULL)
    budget_max: Optional[float] = None
    
    # Scholarship flags
    wants_scholarship: bool = False
    scholarship_focus: ScholarshipFocus = field(default_factory=ScholarshipFocus)
    
    # List flags
    wants_list: bool = False
    page_action: str = "none"  # "none", "next", "prev", "first"
    pagination: PaginationConfig = field(default_factory=PaginationConfig)
    
    # Location
    city: Optional[str] = None
    province: Optional[str] = None
    country: Optional[str] = None
    
    # Other
    wants_earliest: bool = False
    
    # Clarification state
    is_clarifying: bool = False  # Whether the agent is currently asking for clarification
    pending_slot: Optional[str] = None  # The slot name that is pending clarification (e.g., "degree_level", "major_or_university")
    
    # Legacy fields (for backward compatibility)
    university: Optional[str] = None  # Alias for university_query
    duration_preference: Optional[str] = None  # Legacy duration format
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for LLM context"""
        return {
            "intent": self.intent,
            "confidence": self.confidence,
            "degree_level": self.degree_level,
            "major_query": self.major_query,
            "university_query": self.university_query or self.university,
            "teaching_language": self.teaching_language,
            "intake_term": self.intake_term,
            "intake_year": self.intake_year,
            "duration_years_target": self.duration_years_target,
            "duration_constraint": self.duration_constraint,
            "wants_requirements": self.wants_requirements,
            "req_focus": {
                "docs": self.req_focus.docs,
                "exams": self.req_focus.exams,
                "bank": self.req_focus.bank,
                "age": self.req_focus.age,
                "inside_china": self.req_focus.inside_china,
                "deadline": self.req_focus.deadline,
                "accommodation": self.req_focus.accommodation,
                "country": self.req_focus.country,
            },
            "wants_fees": self.wants_fees,
            "wants_free_tuition": self.wants_free_tuition,
            "budget_max": self.budget_max,
            "wants_scholarship": self.wants_scholarship,
            "scholarship_focus": {
                "any": self.scholarship_focus.any,
                "csc": self.scholarship_focus.csc,
                "university": self.scholarship_focus.university,
            },
            "wants_list": self.wants_list,
            "page_action": self.page_action,
            "city": self.city,
            "province": self.province,
            "country": self.country,
            "wants_earliest": self.wants_earliest,
        }

