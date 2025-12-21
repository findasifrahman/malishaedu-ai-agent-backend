"""
Database Query Service - Implements DB-first principle for agent queries
"""
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, timedelta, timezone
import json
from app.models import (
    University, Major, ProgramIntake, Student, 
    DegreeLevel, TeachingLanguage, IntakeTerm,
    ProgramDocument, ProgramExamRequirement,
    ProgramIntakeScholarship, Scholarship
)

class DBQueryService:
    """Service for querying university, major, and program intake data"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def search_universities(
        self,
        name: Optional[str] = None,
        city: Optional[str] = None,
        province: Optional[str] = None,
        is_partner: Optional[bool] = None,
        limit: int = 10
    ) -> List[University]:
        """Search universities by various criteria"""
        query = self.db.query(University)
        
        if name:
            query = query.filter(University.name.ilike(f"%{name}%"))
        if city:
            query = query.filter(University.city.ilike(f"%{city}%"))
        if province:
            query = query.filter(University.province.ilike(f"%{province}%"))
        if is_partner is not None:
            query = query.filter(University.is_partner == is_partner)
        
        # Prioritize partner universities
        query = query.order_by(University.is_partner.desc(), University.name)
        
        return query.limit(limit).all()
    
    def find_university_candidates(self, partner_id: Optional[int], query: str, limit: int = 8) -> List[Dict[str, Any]]:
        """
        Find university candidates using DB query with LIMIT (no full table load).
        Searches name, name_cn, and aliases JSON.
        Returns list of dicts: [{id, name, city, province, aliases}]
        """
        if not query:
            return []
        
        # Build query with ILIKE search
        db_query = self.db.query(University).filter(University.is_partner == True)
        
        # Search in name, name_cn, and aliases JSON
        search_term = f"%{query}%"
        db_query = db_query.filter(
            or_(
                University.name.ilike(search_term),
                University.name_cn.ilike(search_term),
                University.aliases.ilike(search_term) if hasattr(University, 'aliases') else False
            )
        )
        
        results = db_query.limit(limit).all()
        
        candidates = []
        for uni in results:
            candidates.append({
                "id": uni.id,
                "name": uni.name,
                "city": uni.city,
                "province": uni.province,
                "aliases": json.loads(uni.aliases) if hasattr(uni, 'aliases') and isinstance(uni.aliases, str) else (uni.aliases if isinstance(uni.aliases, list) else [])
            })
        
        return candidates
    
    def find_major_candidates(self, partner_id: Optional[int], query: str, 
                             degree_level: Optional[str] = None,
                             teaching_language: Optional[str] = None,
                             limit: int = 8) -> List[Dict[str, Any]]:
        """
        Find major candidates using DB query with LIMIT (no full table load).
        Searches name, name_cn, keywords JSON, and category.
        Returns list of dicts: [{id, name, degree_level, teaching_language, university_id}]
        """
        if not query:
            return []
        
        # Join with University to filter by partner
        db_query = self.db.query(Major).join(University).filter(University.is_partner == True)
        
        # Search in name, name_cn, and keywords JSON
        search_term = f"%{query}%"
        conditions = [
            Major.name.ilike(search_term),
            Major.name_cn.ilike(search_term),
        ]
        
        # Also search in keywords JSON (if stored as JSON string)
        if hasattr(Major, 'keywords'):
            conditions.append(Major.keywords.ilike(search_term))
        
        db_query = db_query.filter(or_(*conditions))
        
        # Apply filters
        if degree_level:
            db_query = db_query.filter(Major.degree_level == degree_level)
        if teaching_language:
            db_query = db_query.filter(Major.teaching_language == teaching_language)
        
        results = db_query.limit(limit).all()
        
        candidates = []
        for major in results:
            candidates.append({
                "id": major.id,
                "name": major.name,
                "degree_level": major.degree_level,
                "teaching_language": major.teaching_language,
                "university_id": major.university_id
            })
        
        return candidates
    
    def search_majors(
        self,
        university_id: Optional[int] = None,
        name: Optional[str] = None,
        degree_level: Optional[str] = None,  # Changed from DegreeLevel enum to str
        teaching_language: Optional[str] = None,  # Changed from TeachingLanguage enum to str
        discipline: Optional[str] = None,
        is_featured: Optional[bool] = None,
        limit: int = 10
    ) -> List[Major]:
        """Search majors by various criteria"""
        query = self.db.query(Major)
        
        if university_id:
            query = query.filter(Major.university_id == university_id)
        if name:
            # Use ILIKE for case-insensitive partial matching
            # This handles variations like "Computer Science & Technology" vs "Computer Science and Technology"
            # Split name into words and match each word
            name_words = name.split()
            if len(name_words) > 1:
                # Multiple words - match all words (AND condition)
                conditions = [Major.name.ilike(f"%{word}%") for word in name_words]
                query = query.filter(and_(*conditions))
            else:
                # Single word or abbreviation
                query = query.filter(Major.name.ilike(f"%{name}%"))
        if degree_level:
            # Handle both string and enum types for backward compatibility
            if isinstance(degree_level, str):
                query = query.filter(Major.degree_level.ilike(f"%{degree_level}%"))
            else:
                # If it's an enum, convert to string for comparison
                query = query.filter(Major.degree_level == str(degree_level.value) if hasattr(degree_level, 'value') else str(degree_level))
        if teaching_language:
            # Handle both string and enum types
            if isinstance(teaching_language, str):
                query = query.filter(Major.teaching_language.ilike(f"%{teaching_language}%"))
            else:
                query = query.filter(Major.teaching_language == str(teaching_language.value) if hasattr(teaching_language, 'value') else str(teaching_language))
        if discipline:
            query = query.filter(Major.discipline.ilike(f"%{discipline}%"))
        if is_featured is not None:
            query = query.filter(Major.is_featured == is_featured)
        
        query = query.order_by(Major.is_featured.desc(), Major.name)
        
        return query.limit(limit).all()
    
    def search_program_intakes(
        self,
        university_id: Optional[int] = None,
        university_ids: Optional[List[int]] = None,  # Support list of university IDs
        major_id: Optional[int] = None,
        major_ids: Optional[List[int]] = None,  # Support list of major IDs
        intake_term: Optional[IntakeTerm] = None,
        intake_year: Optional[int] = None,
        upcoming_only: bool = True,
        limit: int = 10,
        duration_years_target: Optional[float] = None,
        duration_constraint: Optional[str] = None,  # "exact", "min", "max", "approx"
        budget_max: Optional[float] = None,
        teaching_language: Optional[str] = None,
        degree_level: Optional[str] = None
    ) -> List[ProgramIntake]:
        """Search program intakes with optional filters including duration constraints and budget"""
        query = self.db.query(ProgramIntake)
        
        if university_ids:
            query = query.filter(ProgramIntake.university_id.in_(university_ids))
        elif university_id:
            query = query.filter(ProgramIntake.university_id == university_id)
        
        if major_ids:
            query = query.filter(ProgramIntake.major_id.in_(major_ids))
        elif major_id:
            query = query.filter(ProgramIntake.major_id == major_id)
        if major_id:
            query = query.filter(ProgramIntake.major_id == major_id)
        if intake_term:
            query = query.filter(ProgramIntake.intake_term == intake_term)
        if intake_year:
            query = query.filter(ProgramIntake.intake_year == intake_year)
        if upcoming_only:
            now = datetime.now(timezone.utc)
            query = query.filter(ProgramIntake.application_deadline >= now)
        
        # Duration constraint filtering
        if duration_years_target is not None:
            # Join with Major to get duration
            query = query.join(Major, ProgramIntake.major_id == Major.id)
            # Use intake's duration if available, otherwise major's duration
            duration_expr = func.coalesce(ProgramIntake.duration_years, Major.duration_years)
            
            if duration_constraint == "exact":
                query = query.filter(func.abs(duration_expr - duration_years_target) < 0.1)
            elif duration_constraint == "min":
                query = query.filter(duration_expr >= duration_years_target)
            elif duration_constraint == "max":
                query = query.filter(duration_expr <= duration_years_target)
            elif duration_constraint == "approx":
                # Within 20% tolerance
                query = query.filter(
                    duration_expr >= duration_years_target * 0.8,
                    duration_expr <= duration_years_target * 1.2
                )
            else:
                # Default to exact if constraint not specified
                query = query.filter(func.abs(duration_expr - duration_years_target) < 0.1)
        
        # Budget filtering (only if we have known fees)
        if budget_max is not None:
            # Filter by tuition_per_year or tuition_per_semester
            budget_conditions = []
            if ProgramIntake.tuition_per_year is not None:
                budget_conditions.append(ProgramIntake.tuition_per_year <= budget_max)
            if ProgramIntake.tuition_per_semester is not None:
                budget_conditions.append(ProgramIntake.tuition_per_semester <= budget_max)
            if budget_conditions:
                query = query.filter(or_(*budget_conditions))
        
        # Teaching language filter
        if teaching_language:
            # Check intake's teaching_language or major's teaching_language
            if duration_years_target is not None:
                # Already joined with Major
                pass
            else:
                query = query.join(Major, ProgramIntake.major_id == Major.id)
            
            teaching_lang_conditions = [
                ProgramIntake.teaching_language.ilike(f"%{teaching_language}%")
            ]
            teaching_lang_conditions.append(Major.teaching_language.ilike(f"%{teaching_language}%"))
            query = query.filter(or_(*teaching_lang_conditions))
        
        # Degree level filter
        if degree_level:
            if duration_years_target is None and teaching_language is None:
                query = query.join(Major, ProgramIntake.major_id == Major.id)
            query = query.filter(
                or_(
                    ProgramIntake.degree_type.ilike(f"%{degree_level}%"),
                    Major.degree_level.ilike(f"%{degree_level}%")
                )
            )
        
        # Order by nearest deadline first
        query = query.order_by(ProgramIntake.application_deadline)
        
        return query.limit(limit).all()
    
    def get_student_profile(self, user_id: int) -> Optional[Student]:
        """Get student profile by user_id"""
        return self.db.query(Student).filter(Student.user_id == user_id).first()
    
    def get_student_target_intake(self, student_id: int) -> Optional[ProgramIntake]:
        """Get the program intake that a student is targeting"""
        student = self.db.query(Student).filter(Student.id == student_id).first()
        if not student or not student.target_intake_id:
            return None
        
        return self.db.query(ProgramIntake).filter(
            ProgramIntake.id == student.target_intake_id
        ).first()
    
    def format_university_info(self, university: University) -> str:
        """Format university information as text for LLM"""
        info = f"University: {university.name}\n"
        if university.city:
            info += f"Location: {university.city}"
            if university.province:
                info += f", {university.province}"
            info += f", {university.country}\n"
        if university.is_partner:
            info += "Partner University: Yes\n"
        if university.university_ranking is not None:
            info += f"University Ranking: {university.university_ranking}\n"
        if university.description:
            info += f"Description: {university.description}\n"
        if university.website:
            info += f"Website: {university.website}\n"
        return info
    
    def format_major_info(self, major: Major) -> str:
        """Format major information as text for LLM"""
        info = f"Major: {major.name}\n"
        # Handle both string and enum types
        degree_level = major.degree_level if isinstance(major.degree_level, str) else (major.degree_level.value if hasattr(major.degree_level, 'value') else str(major.degree_level))
        teaching_lang = major.teaching_language if isinstance(major.teaching_language, str) else (major.teaching_language.value if hasattr(major.teaching_language, 'value') else str(major.teaching_language))
        info += f"Degree Level: {degree_level}\n"
        info += f"Teaching Language: {teaching_lang}\n"
        if major.duration_years:
            info += f"Duration: {major.duration_years} years\n"
        if major.discipline:
            info += f"Discipline: {major.discipline}\n"
        if major.description:
            info += f"Description: {major.description}\n"
        return info
    
    def format_program_intake_info(self, intake: ProgramIntake) -> str:
        """Format program intake information as text for LLM with explicit EXACT value markers"""
        info = f"=== EXACT DATABASE VALUES - USE THESE EXACT NUMBERS ===\n"
        
        # Include university information with ranking
        if intake.university:
            info += self.format_university_info(intake.university)
            info += "\n"
        
        # Handle intake_term (still enum) and convert to string
        intake_term = intake.intake_term.value if hasattr(intake.intake_term, 'value') else str(intake.intake_term)
        info += f"Program Intake: {intake_term} {intake.intake_year}\n"
        
        # Include major information (for duration calculation)
        if intake.major:
            info += f"Major: {intake.major.name}\n"
            # Use intake's degree_type if available, otherwise use major's degree_level
            # Handle both string and enum types
            degree_info = None
            if intake.degree_type:
                degree_info = intake.degree_type if isinstance(intake.degree_type, str) else (intake.degree_type.value if hasattr(intake.degree_type, 'value') else str(intake.degree_type))
            elif intake.major.degree_level:
                degree_info = intake.major.degree_level if isinstance(intake.major.degree_level, str) else (intake.major.degree_level.value if hasattr(intake.major.degree_level, 'value') else str(intake.major.degree_level))
            info += f"Degree Type: {degree_info or 'N/A'}\n"
            
            # Use intake's duration if available, otherwise use major's duration
            duration = intake.duration_years if intake.duration_years is not None else (intake.major.duration_years if intake.major.duration_years else None)
            if duration:
                info += f"Program Duration: {duration} years\n"
                # Calculate expected graduation year
                expected_end_year = intake.intake_year + int(duration)
                info += f"Expected Graduation Year: {expected_end_year}\n"
        
        # Teaching language (use intake's if available, otherwise major's)
        # Handle both string and enum types
        teaching_lang = None
        if intake.teaching_language:
            teaching_lang = intake.teaching_language if isinstance(intake.teaching_language, str) else (intake.teaching_language.value if hasattr(intake.teaching_language, 'value') else str(intake.teaching_language))
        elif intake.major and intake.major.teaching_language:
            teaching_lang = intake.major.teaching_language if isinstance(intake.major.teaching_language, str) else (intake.major.teaching_language.value if hasattr(intake.major.teaching_language, 'value') else str(intake.major.teaching_language))
        info += f"Teaching Language: {teaching_lang or 'N/A'}\n"
        
        if intake.application_deadline:
            deadline = intake.application_deadline
            # Ensure both datetimes are timezone-aware for comparison
            if deadline.tzinfo is None:
                # If deadline is naive, assume it's UTC
                deadline = deadline.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            days_left = (deadline - now).days
            info += f"Application Deadline (EXACT): {deadline.strftime('%Y-%m-%d')} ({days_left} days remaining)\n"
        
        # EXACT TUITION FEES - USE THESE EXACT VALUES
        if intake.tuition_per_year:
            info += f"TUITION PER YEAR (EXACT FROM DATABASE): {intake.tuition_per_year} RMB - USE THIS EXACT VALUE\n"
        if intake.tuition_per_semester:
            info += f"TUITION PER SEMESTER (EXACT FROM DATABASE): {intake.tuition_per_semester} RMB - USE THIS EXACT VALUE\n"
        
        # APPLICATION FEE - NON-REFUNDABLE
        if intake.application_fee:
            info += f"APPLICATION FEE (EXACT FROM DATABASE, NON-REFUNDABLE): {intake.application_fee} RMB - USE THIS EXACT VALUE\n"
        
        # ACCOMMODATION FEE - PER YEAR (LLM MUST KNOW THIS IS PER YEAR)
        if intake.accommodation_fee:
            info += f"ACCOMMODATION FEE PER YEAR (EXACT FROM DATABASE - THIS IS PER YEAR, NOT PER SEMESTER): {intake.accommodation_fee} RMB - USE THIS EXACT VALUE\n"
        
        # SERVICE FEE - MalishaEdu service fee (only for successful application)
        if intake.service_fee:
            info += f"SERVICE FEE (MalishaEdu, only charged for successful application): {intake.service_fee} RMB\n"
        
        # MEDICAL INSURANCE FEE - Taken by university after arrival in China
        if intake.medical_insurance_fee:
            info += f"MEDICAL INSURANCE FEE (taken by university after successful application and arriving in China): {intake.medical_insurance_fee} RMB\n"
        
        # ARRIVAL MEDICAL CHECKUP FEE - ONE-TIME (LLM MUST KNOW THIS IS ONE-TIME)
        if intake.arrival_medical_checkup_fee is not None:
            info += f"ARRIVAL MEDICAL CHECKUP FEE (ONE-TIME, paid upon arrival in China - LLM MUST KNOW THIS IS ONE-TIME, NOT ANNUAL): {intake.arrival_medical_checkup_fee} RMB\n"
        
        # VISA EXTENSION FEE - REQUIRED EACH YEAR (LLM MUST KNOW THIS IS ANNUAL)
        if intake.visa_extension_fee is not None:
            info += f"VISA EXTENSION FEE (REQUIRED EACH YEAR - LLM MUST KNOW THIS IS ANNUAL, NOT ONE-TIME): {intake.visa_extension_fee} RMB per year\n"
        
        # ACCOMMODATION NOTE
        if intake.accommodation_note:
            info += f"ACCOMMODATION NOTE: {intake.accommodation_note}\n"
        
        # ADMISSION PROCESS
        if intake.admission_process:
            info += f"ADMISSION PROCESS: {intake.admission_process}\n"
        
        # SCHOLARSHIP INFORMATION - LLM MUST PARSE AND CALCULATE ACTUAL COSTS
        if intake.scholarship_info:
            info += f"\nSCHOLARSHIP INFORMATION (EXACT FROM DATABASE - LLM MUST PARSE AND CALCULATE):\n{intake.scholarship_info}\n"
            info += "IMPORTANT: Parse the scholarship information above. If it contains a scholarship amount or percentage, calculate the actual cost after scholarship deduction. Show both the original cost and the cost after scholarship.\n"
        else:
            info += "\nSCHOLARSHIP INFORMATION: No scholarship available for this program/intake.\n"
        
        # EXACT DOCUMENTS REQUIRED - USE THIS EXACT LIST
        if intake.documents_required:
            info += f"\nREQUIRED DOCUMENTS (EXACT FROM DATABASE - USE THIS EXACT LIST):\n{intake.documents_required}\n"
        
        if intake.notes:
            info += f"\nAdditional Notes: {intake.notes}\n"
        
        info += "=== END EXACT DATABASE VALUES ===\n"
        return info
    
    def get_comprehensive_program_info(
        self,
        university_id: Optional[int] = None,
        major_id: Optional[int] = None,
        intake_id: Optional[int] = None
    ) -> str:
        """Get comprehensive information about a program including university, major, and intake"""
        info_parts = []
        
        if intake_id:
            intake = self.db.query(ProgramIntake).filter(ProgramIntake.id == intake_id).first()
            if intake:
                university = intake.university
                major = intake.major
                info_parts.append(self.format_university_info(university))
                info_parts.append(self.format_major_info(major))
                info_parts.append(self.format_program_intake_info(intake))
        elif major_id:
            major = self.db.query(Major).filter(Major.id == major_id).first()
            if major:
                university = major.university
                info_parts.append(self.format_university_info(university))
                info_parts.append(self.format_major_info(major))
                # Get upcoming intakes for this major
                intakes = self.search_program_intakes(major_id=major.id, limit=3)
                if intakes:
                    info_parts.append("Upcoming Intakes:")
                    for intake in intakes:
                        info_parts.append(self.format_program_intake_info(intake))
        elif university_id:
            university = self.db.query(University).filter(University.id == university_id).first()
            if university:
                info_parts.append(self.format_university_info(university))
                # Get featured majors
                majors = self.search_majors(university_id=university.id, is_featured=True, limit=5)
                if majors:
                    info_parts.append("Featured Majors:")
                    for major in majors:
                        info_parts.append(self.format_major_info(major))
        
        return "\n\n".join(info_parts) if info_parts else "No information found."
    
    def list_universities_by_filters(
        self,
        city: Optional[str] = None,
        province: Optional[str] = None,
        degree_level: Optional[str] = None,
        teaching_language: Optional[str] = None,
        intake_term: Optional[IntakeTerm] = None,
        intake_year: Optional[int] = None,
        duration_years_target: Optional[float] = None,
        duration_constraint: Optional[str] = None,
        upcoming_only: bool = True,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        List universities by filters, joining with program_intakes and majors.
        Returns list of dicts with university info and count of matching programs.
        """
        query = self.db.query(
            University,
            func.count(ProgramIntake.id).label('program_count')
        ).join(
            ProgramIntake, University.id == ProgramIntake.university_id
        ).join(
            Major, ProgramIntake.major_id == Major.id
        ).filter(
            University.is_partner == True
        )
        
        # Location filters
        if city:
            query = query.filter(University.city.ilike(f"%{city}%"))
        if province:
            query = query.filter(University.province.ilike(f"%{province}%"))
        
        # Degree level filter
        if degree_level:
            query = query.filter(
                or_(
                    ProgramIntake.degree_type.ilike(f"%{degree_level}%"),
                    Major.degree_level.ilike(f"%{degree_level}%")
                )
            )
        
        # Teaching language filter
        if teaching_language:
            query = query.filter(
                or_(
                    ProgramIntake.teaching_language.ilike(f"%{teaching_language}%"),
                    Major.teaching_language.ilike(f"%{teaching_language}%")
                )
            )
        
        # Intake filters
        if intake_term:
            query = query.filter(ProgramIntake.intake_term == intake_term)
        if intake_year:
            query = query.filter(ProgramIntake.intake_year == intake_year)
        
        # Duration constraint
        if duration_years_target is not None:
            duration_expr = func.coalesce(ProgramIntake.duration_years, Major.duration_years)
            if duration_constraint == "exact":
                query = query.filter(func.abs(duration_expr - duration_years_target) < 0.1)
            elif duration_constraint == "min":
                query = query.filter(duration_expr >= duration_years_target)
            elif duration_constraint == "max":
                query = query.filter(duration_expr <= duration_years_target)
            elif duration_constraint == "approx":
                query = query.filter(
                    duration_expr >= duration_years_target * 0.8,
                    duration_expr <= duration_years_target * 1.2
                )
        
        # Upcoming only
        if upcoming_only:
            now = datetime.now(timezone.utc)
            query = query.filter(ProgramIntake.application_deadline >= now)
        
        # Group by university and aggregate
        query = query.group_by(University.id).order_by(
            func.count(ProgramIntake.id).desc(),
            University.name
        )
        
        results = query.limit(limit).all()
        
        return [
            {
                "university": uni,
                "program_count": count
            }
            for uni, count in results
        ]
    
    def get_program_requirements(self, program_intake_id: int) -> Dict[str, Any]:
        """
        Get comprehensive program requirements by merging:
        - ProgramDocument
        - ProgramExamRequirement
        - ProgramIntake fields (bank, hsk, english, age, inside_china, interview, written_test, acceptance_letter, deadline)
        - notes, accommodation_note
        """
        intake = self.db.query(ProgramIntake).filter(ProgramIntake.id == program_intake_id).first()
        if not intake:
            return {}
        
        requirements = {
            "documents": [],
            "exams": [],
            "bank_statement": None,
            "age": None,
            "inside_china": None,
            "interview": None,
            "written_test": None,
            "acceptance_letter": None,
            "deadline": None,
            "notes": None,
            "accommodation_note": None,
            "min_average_score": None,
            "country_restrictions": None
        }
        
        # Documents from ProgramDocument table
        program_docs = self.db.query(ProgramDocument).filter(
            ProgramDocument.program_intake_id == program_intake_id
        ).all()
        
        if program_docs:
            requirements["documents"] = [
                {
                    "name": doc.name,
                    "is_required": doc.is_required,
                    "rules": doc.rules,
                    "applies_to": doc.applies_to
                }
                for doc in program_docs
            ]
        elif intake.documents_required:
            # Fallback to text field
            requirements["documents"] = [{"name": "See documents_required field", "text": intake.documents_required}]
        
        # Exam requirements from ProgramExamRequirement table
        exam_reqs = self.db.query(ProgramExamRequirement).filter(
            ProgramExamRequirement.program_intake_id == program_intake_id
        ).all()
        
        requirements["exams"] = [
            {
                "exam_name": req.exam_name,
                "required": req.required,
                "subjects": req.subjects,
                "min_level": req.min_level,
                "min_score": req.min_score,
                "exam_language": req.exam_language,
                "notes": req.notes
            }
            for req in exam_reqs
        ]
        
        # Bank statement requirements
        if intake.bank_statement_required is not None:
            requirements["bank_statement"] = {
                "required": intake.bank_statement_required,
                "amount": intake.bank_statement_amount,
                "currency": intake.bank_statement_currency,
                "note": intake.bank_statement_note
            }
        
        # Age requirements
        if intake.age_min is not None or intake.age_max is not None:
            requirements["age"] = {
                "min": intake.age_min,
                "max": intake.age_max
            }
        
        # Inside China applicants
        if intake.inside_china_applicants_allowed is not None:
            requirements["inside_china"] = {
                "allowed": intake.inside_china_applicants_allowed,
                "extra_requirements": intake.inside_china_extra_requirements
            }
        
        # Interview and written test
        if intake.interview_required is not None:
            requirements["interview"] = {"required": intake.interview_required}
        if intake.written_test_required is not None:
            requirements["written_test"] = {"required": intake.written_test_required}
        if intake.acceptance_letter_required is not None:
            requirements["acceptance_letter"] = {"required": intake.acceptance_letter_required}
        
        # Deadline
        if intake.application_deadline:
            requirements["deadline"] = {
                "date": intake.application_deadline,
                "deadline_type": intake.deadline_type
            }
        
        # Notes
        if intake.notes:
            requirements["notes"] = intake.notes
        if intake.accommodation_note:
            requirements["accommodation_note"] = intake.accommodation_note
        
        # Minimum average score
        if intake.min_average_score is not None:
            requirements["min_average_score"] = intake.min_average_score
        
        # Country restrictions (parse from notes if available, or return None)
        # For now, return None - can be extended with structured table later
        requirements["country_restrictions"] = None
        
        return requirements
    
    def get_program_scholarships(self, program_intake_id: int) -> List[Dict[str, Any]]:
        """
        Get scholarships for a program intake by joining program_intake_scholarships and scholarships.
        """
        scholarship_data = self.db.query(
            ProgramIntakeScholarship,
            Scholarship
        ).join(
            Scholarship, ProgramIntakeScholarship.scholarship_id == Scholarship.id
        ).filter(
            ProgramIntakeScholarship.program_intake_id == program_intake_id
        ).all()
        
        return [
            {
                "scholarship": {
                    "id": sch.id,
                    "name": sch.name,
                    "provider": sch.provider,
                    "notes": sch.notes
                },
                "covers_tuition": pis.covers_tuition,
                "covers_accommodation": pis.covers_accommodation,
                "covers_insurance": pis.covers_insurance,
                "tuition_waiver_percent": pis.tuition_waiver_percent,
                "living_allowance_monthly": pis.living_allowance_monthly,
                "living_allowance_yearly": pis.living_allowance_yearly,
                "first_year_only": pis.first_year_only,
                "renewal_required": pis.renewal_required,
                "deadline": pis.deadline,
                "eligibility_note": pis.eligibility_note
            }
            for pis, sch in scholarship_data
        ]
    
    def search_scholarships(
        self,
        program_intake_id: Optional[int] = None,
        university_id: Optional[int] = None,
        scholarship_focus: Optional[Dict[str, bool]] = None  # {"csc": bool, "university": bool, "any": bool}
    ) -> List[Dict[str, Any]]:
        """
        Search scholarships with filters.
        scholarship_focus: {"csc": True} for CSC only, {"university": True} for university only, {"any": True} for all
        """
        query = self.db.query(
            ProgramIntakeScholarship,
            Scholarship,
            ProgramIntake,
            University
        ).join(
            Scholarship, ProgramIntakeScholarship.scholarship_id == Scholarship.id
        ).join(
            ProgramIntake, ProgramIntakeScholarship.program_intake_id == ProgramIntake.id
        ).join(
            University, ProgramIntake.university_id == University.id
        )
        
        if program_intake_id:
            query = query.filter(ProgramIntakeScholarship.program_intake_id == program_intake_id)
        if university_id:
            query = query.filter(ProgramIntake.university_id == university_id)
        
        # Scholarship focus filter
        if scholarship_focus:
            if scholarship_focus.get("csc"):
                query = query.filter(Scholarship.name.ilike("%CSC%"))
            elif scholarship_focus.get("university"):
                query = query.filter(~Scholarship.name.ilike("%CSC%"))
        
        results = query.all()
        
        return [
            {
                "scholarship": {
                    "id": sch.id,
                    "name": sch.name,
                    "provider": sch.provider,
                    "notes": sch.notes
                },
                "program_intake": {
                    "id": intake.id,
                    "university": uni.name,
                    "major": intake.major.name if intake.major else None,
                    "intake_term": intake.intake_term.value if hasattr(intake.intake_term, 'value') else str(intake.intake_term),
                    "intake_year": intake.intake_year
                },
                "covers_tuition": pis.covers_tuition,
                "covers_accommodation": pis.covers_accommodation,
                "covers_insurance": pis.covers_insurance,
                "tuition_waiver_percent": pis.tuition_waiver_percent,
                "living_allowance_monthly": pis.living_allowance_monthly,
                "living_allowance_yearly": pis.living_allowance_yearly,
                "first_year_only": pis.first_year_only,
                "renewal_required": pis.renewal_required,
                "deadline": pis.deadline,
                "eligibility_note": pis.eligibility_note
            }
            for pis, sch, intake, uni in results
        ]
    
    def search_intakes_upcoming(
        self,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 10,
        order_by_deadline: bool = True
    ) -> List[ProgramIntake]:
        """
        Search upcoming intakes with filters, ordered by nearest deadline.
        Filters can include: university_id, major_id, degree_level, teaching_language,
        intake_term, intake_year, duration_years_target, duration_constraint, budget_max.
        """
        query = self.db.query(ProgramIntake)
        
        # Join with Major and University for filtering
        query = query.join(Major, ProgramIntake.major_id == Major.id)
        query = query.join(University, ProgramIntake.university_id == University.id)
        
        # Filter partner universities only
        query = query.filter(University.is_partner == True)
        
        # Filter upcoming only (deadline >= now)
        now = datetime.now(timezone.utc)
        query = query.filter(ProgramIntake.application_deadline >= now)
        
        if filters:
            if filters.get("university_id"):
                query = query.filter(ProgramIntake.university_id == filters["university_id"])
            if filters.get("major_id"):
                query = query.filter(ProgramIntake.major_id == filters["major_id"])
            if filters.get("degree_level"):
                query = query.filter(
                    or_(
                        ProgramIntake.degree_type.ilike(f"%{filters['degree_level']}%"),
                        Major.degree_level.ilike(f"%{filters['degree_level']}%")
                    )
                )
            if filters.get("teaching_language"):
                query = query.filter(
                    or_(
                        ProgramIntake.teaching_language.ilike(f"%{filters['teaching_language']}%"),
                        Major.teaching_language.ilike(f"%{filters['teaching_language']}%")
                    )
                )
            if filters.get("intake_term"):
                query = query.filter(ProgramIntake.intake_term == filters["intake_term"])
            if filters.get("intake_year"):
                query = query.filter(ProgramIntake.intake_year == filters["intake_year"])
            
            # Duration constraint
            if filters.get("duration_years_target") is not None:
                duration_expr = func.coalesce(ProgramIntake.duration_years, Major.duration_years)
                duration_constraint = filters.get("duration_constraint", "exact")
                
                if duration_constraint == "exact":
                    query = query.filter(func.abs(duration_expr - filters["duration_years_target"]) < 0.1)
                elif duration_constraint == "min":
                    query = query.filter(duration_expr >= filters["duration_years_target"])
                elif duration_constraint == "max":
                    query = query.filter(duration_expr <= filters["duration_years_target"])
                elif duration_constraint == "approx":
                    query = query.filter(
                        duration_expr >= filters["duration_years_target"] * 0.8,
                        duration_expr <= filters["duration_years_target"] * 1.2
                    )
            
            # Budget filter
            if filters.get("budget_max") is not None:
                budget_conditions = []
                if ProgramIntake.tuition_per_year is not None:
                    budget_conditions.append(ProgramIntake.tuition_per_year <= filters["budget_max"])
                if ProgramIntake.tuition_per_semester is not None:
                    budget_conditions.append(ProgramIntake.tuition_per_semester <= filters["budget_max"])
                if budget_conditions:
                    query = query.filter(or_(*budget_conditions))
        
        # Order by nearest deadline first
        if order_by_deadline:
            query = query.order_by(ProgramIntake.application_deadline.asc(), ProgramIntake.program_start_date.asc())
        else:
            query = query.order_by(ProgramIntake.program_start_date.asc())
        
        return query.limit(limit).all()
    
    def search_scholarship_intakes(
        self,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 10
    ) -> List[ProgramIntake]:
        """
        Search intakes that have scholarships available.
        Filters can include: university_id, major_id, degree_level, teaching_language,
        intake_term, intake_year, scholarship_type (CSC vs university).
        """
        query = self.db.query(ProgramIntake)
        
        # Join with Major, University, and ProgramIntakeScholarship
        query = query.join(Major, ProgramIntake.major_id == Major.id)
        query = query.join(University, ProgramIntake.university_id == University.id)
        query = query.join(ProgramIntakeScholarship, ProgramIntake.id == ProgramIntakeScholarship.program_intake_id)
        query = query.join(Scholarship, ProgramIntakeScholarship.scholarship_id == Scholarship.id)
        
        # Filter partner universities only
        query = query.filter(University.is_partner == True)
        
        # Filter upcoming only
        now = datetime.now(timezone.utc)
        query = query.filter(ProgramIntake.application_deadline >= now)
        
        if filters:
            if filters.get("university_id"):
                query = query.filter(ProgramIntake.university_id == filters["university_id"])
            if filters.get("major_id"):
                query = query.filter(ProgramIntake.major_id == filters["major_id"])
            if filters.get("degree_level"):
                query = query.filter(
                    or_(
                        ProgramIntake.degree_type.ilike(f"%{filters['degree_level']}%"),
                        Major.degree_level.ilike(f"%{filters['degree_level']}%")
                    )
                )
            if filters.get("teaching_language"):
                query = query.filter(
                    or_(
                        ProgramIntake.teaching_language.ilike(f"%{filters['teaching_language']}%"),
                        Major.teaching_language.ilike(f"%{filters['teaching_language']}%")
                    )
                )
            if filters.get("intake_term"):
                query = query.filter(ProgramIntake.intake_term == filters["intake_term"])
            if filters.get("intake_year"):
                query = query.filter(ProgramIntake.intake_year == filters["intake_year"])
            
            # Scholarship type filter (CSC vs university)
            if filters.get("scholarship_type"):
                if filters["scholarship_type"].lower() == "csc":
                    query = query.filter(Scholarship.scholarship_type.ilike("%CSC%"))
                elif filters["scholarship_type"].lower() == "university":
                    query = query.filter(~Scholarship.scholarship_type.ilike("%CSC%"))
        
        # Order by nearest deadline
        query = query.order_by(ProgramIntake.application_deadline.asc(), ProgramIntake.program_start_date.asc())
        
        # Distinct to avoid duplicates from multiple scholarships
        query = query.distinct()
        
        return query.limit(limit).all()
    
    def distinct_universities_for_intakes(self, intake_ids: List[int]) -> List[University]:
        """
        Get distinct universities for a list of intake IDs.
        Returns list of University objects.
        """
        if not intake_ids:
            return []
        
        query = self.db.query(University).join(
            ProgramIntake, University.id == ProgramIntake.university_id
        ).filter(
            ProgramIntake.id.in_(intake_ids),
            University.is_partner == True
        ).distinct()
        
        return query.all()
    
    def find_program_intakes(
        self,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 24,
        offset: int = 0,
        order_by: str = "deadline"  # "deadline", "start_date", "tuition"
    ) -> Tuple[List[ProgramIntake], int]:
        """
        Efficient query for program intakes with comprehensive filters.
        Returns (intakes, total_count).
        Filters include: degree_level, major_text, university_id, teaching_language,
        intake_term, intake_year, city, province, duration_years_target, duration_constraint,
        scholarship flags, requirements flags, country_allowed, inside_china_allowed,
        deadline_window, earliest flag.
        """
        query = self.db.query(ProgramIntake)
        
        # Join with Major and University
        query = query.join(Major, ProgramIntake.major_id == Major.id)
        query = query.join(University, ProgramIntake.university_id == University.id)
        
        # Filter partner universities only
        query = query.filter(University.is_partner == True)
        
        # Default: filter upcoming only (deadline >= today)
        now = datetime.now(timezone.utc)
        if filters is None or filters.get("deadline_window") != "all":
            query = query.filter(
                or_(
                    ProgramIntake.application_deadline >= now,
                    ProgramIntake.application_deadline.is_(None)
                )
            )
        
        if filters:
            if filters.get("university_id"):
                query = query.filter(ProgramIntake.university_id == filters["university_id"])
            if filters.get("major_ids"):
                # Filter by list of major IDs (IN clause)
                query = query.filter(ProgramIntake.major_id.in_(filters["major_ids"]))
            elif filters.get("major_id"):
                query = query.filter(ProgramIntake.major_id == filters["major_id"])
            if filters.get("major_text"):
                # Search in major name
                query = query.filter(Major.name.ilike(f"%{filters['major_text']}%"))
            if filters.get("degree_level"):
                query = query.filter(
                    or_(
                        ProgramIntake.degree_type.ilike(f"%{filters['degree_level']}%"),
                        Major.degree_level.ilike(f"%{filters['degree_level']}%")
                    )
                )
            if filters.get("teaching_language"):
                query = query.filter(
                    or_(
                        ProgramIntake.teaching_language.ilike(f"%{filters['teaching_language']}%"),
                        Major.teaching_language.ilike(f"%{filters['teaching_language']}%")
                    )
                )
            if filters.get("intake_term"):
                query = query.filter(ProgramIntake.intake_term == filters["intake_term"])
            if filters.get("intake_year"):
                query = query.filter(ProgramIntake.intake_year == filters["intake_year"])
            if filters.get("city"):
                query = query.filter(University.city.ilike(f"%{filters['city']}%"))
            if filters.get("province"):
                query = query.filter(University.province.ilike(f"%{filters['province']}%"))
            
            # Duration constraint
            if filters.get("duration_years_target") is not None:
                duration_expr = func.coalesce(ProgramIntake.duration_years, Major.duration_years)
                duration_constraint = filters.get("duration_constraint", "approx")
                
                if duration_constraint == "exact":
                    query = query.filter(func.abs(duration_expr - filters["duration_years_target"]) < 0.1)
                elif duration_constraint == "min":
                    query = query.filter(duration_expr >= filters["duration_years_target"])
                elif duration_constraint == "max":
                    query = query.filter(duration_expr <= filters["duration_years_target"])
                elif duration_constraint == "approx":
                    query = query.filter(
                        duration_expr >= filters["duration_years_target"] * 0.75,
                        duration_expr <= filters["duration_years_target"] * 1.25
                    )
            
            # Budget filter
            if filters.get("budget_max") is not None:
                budget_conditions = []
                if ProgramIntake.tuition_per_year is not None:
                    budget_conditions.append(ProgramIntake.tuition_per_year <= filters["budget_max"])
                if ProgramIntake.tuition_per_semester is not None:
                    budget_conditions.append(ProgramIntake.tuition_per_semester <= filters["budget_max"])
                if budget_conditions:
                    query = query.filter(or_(*budget_conditions))
            
            # Scholarship filter
            if filters.get("has_scholarship"):
                query = query.join(ProgramIntakeScholarship, ProgramIntake.id == ProgramIntakeScholarship.program_intake_id)
                query = query.distinct()
            if filters.get("scholarship_type"):
                if not filters.get("has_scholarship"):
                    query = query.join(ProgramIntakeScholarship, ProgramIntake.id == ProgramIntakeScholarship.program_intake_id)
                    query = query.join(Scholarship, ProgramIntakeScholarship.scholarship_id == Scholarship.id)
                if filters["scholarship_type"].lower() == "csc":
                    query = query.filter(Scholarship.scholarship_type.ilike("%CSC%"))
                elif filters["scholarship_type"].lower() == "university":
                    query = query.filter(~Scholarship.scholarship_type.ilike("%CSC%"))
                query = query.distinct()
            
            # Requirements filters
            if filters.get("bank_statement_amount") is not None:
                query = query.filter(
                    or_(
                        ProgramIntake.bank_statement_amount.is_(None),
                        ProgramIntake.bank_statement_amount <= filters["bank_statement_amount"]
                    )
                )
            if filters.get("hsk_required") is not None:
                query = query.filter(ProgramIntake.hsk_required == filters["hsk_required"])
            if filters.get("inside_china_allowed") is not None:
                query = query.filter(ProgramIntake.inside_china_allowed == filters["inside_china_allowed"])
        
        # Get total count before pagination
        total_count = query.count()
        
        # Ordering
        if order_by == "deadline":
            query = query.order_by(
                ProgramIntake.application_deadline.asc().nullslast(),
                ProgramIntake.program_start_date.asc().nullslast()
            )
        elif order_by == "start_date":
            query = query.order_by(ProgramIntake.program_start_date.asc().nullslast())
        elif order_by == "tuition":
            query = query.order_by(
                func.coalesce(ProgramIntake.tuition_per_year, ProgramIntake.tuition_per_semester).asc().nullslast()
            )
        else:
            query = query.order_by(ProgramIntake.application_deadline.asc().nullslast())
        
        # Pagination
        intakes = query.offset(offset).limit(limit).all()
        
        return intakes, total_count
    
    def get_scholarship_summary(self, partner_only: bool = True) -> Dict[str, Any]:
        """
        Get aggregated scholarship summary: counts of programs with scholarships.
        Returns dict with counts and example scholarship names.
        """
        query = self.db.query(
            ProgramIntake.id,
            Scholarship.name,
            Scholarship.scholarship_type
        ).join(
            ProgramIntakeScholarship, ProgramIntake.id == ProgramIntakeScholarship.program_intake_id
        ).join(
            Scholarship, ProgramIntakeScholarship.scholarship_id == Scholarship.id
        ).join(
            University, ProgramIntake.university_id == University.id
        )
        
        if partner_only:
            query = query.filter(University.is_partner == True)
        
        # Filter upcoming only
        now = datetime.now(timezone.utc)
        query = query.filter(
            or_(
                ProgramIntake.application_deadline >= now,
                ProgramIntake.application_deadline.is_(None)
            )
        )
        
        results = query.distinct().all()
        
        total_with_scholarship = len(set(r[0] for r in results))
        csc_count = len([r for r in results if r[2] and "CSC" in str(r[2]).upper()])
        university_count = total_with_scholarship - csc_count
        
        # Get example scholarship names
        example_scholarships = list(set([r[1] for r in results if r[1]]))[:10]
        
        return {
            "total_programs_with_scholarship": total_with_scholarship,
            "csc_scholarships": csc_count,
            "university_scholarships": university_count,
            "example_scholarship_names": example_scholarships
        }
    
    def get_distinct_teaching_languages(self, filters: Dict[str, Any]) -> set:
        """
        Get distinct teaching languages for programs matching the given filters.
        Uses ProgramIntake.teaching_language OR Major.teaching_language as fallback.
        Returns set of effective language strings (normalized to "English"/"Chinese").
        """
        query = self.db.query(ProgramIntake).join(Major, ProgramIntake.major_id == Major.id).join(
            University, ProgramIntake.university_id == University.id
        )
        
        # Apply same filters as find_program_intakes
        if filters.get("university_id"):
            query = query.filter(ProgramIntake.university_id == filters["university_id"])
        if filters.get("university_ids"):
            query = query.filter(ProgramIntake.university_id.in_(filters["university_ids"]))
        if filters.get("major_id"):
            query = query.filter(ProgramIntake.major_id == filters["major_id"])
        if filters.get("major_ids"):
            query = query.filter(ProgramIntake.major_id.in_(filters["major_ids"]))
        if filters.get("degree_level"):
            query = query.filter(Major.degree_level.ilike(f"%{filters['degree_level']}%"))
        if filters.get("intake_term"):
            query = query.filter(ProgramIntake.intake_term == filters["intake_term"])
        if filters.get("intake_year"):
            query = query.filter(ProgramIntake.intake_year == filters["intake_year"])
        if filters.get("city"):
            query = query.filter(University.city.ilike(f"%{filters['city']}%"))
        if filters.get("province"):
            query = query.filter(University.province.ilike(f"%{filters['province']}%"))
        if filters.get("upcoming_only", True):
            now = datetime.now(timezone.utc)
            query = query.filter(
                or_(
                    ProgramIntake.application_deadline >= now,
                    ProgramIntake.application_deadline.is_(None)
                )
            )
        
        # Get distinct teaching languages (use ProgramIntake.teaching_language first, fallback to Major.teaching_language)
        results = query.with_entities(
            func.coalesce(ProgramIntake.teaching_language, Major.teaching_language).label("teaching_language")
        ).distinct().all()
        
        languages = set()
        for (lang,) in results:
            if lang:
                lang_str = str(lang).strip()
                # Normalize to "English" or "Chinese"
                if lang_str.lower() in ["english", "en", "english-taught"]:
                    languages.add("English")
                elif lang_str.lower() in ["chinese", "cn", "chinese-taught", "mandarin", "中文"]:
                    languages.add("Chinese")
                else:
                    # Keep as-is if unknown
                    languages.add(lang_str)
        
        return languages


