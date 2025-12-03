"""
Database Query Service - Implements DB-first principle for agent queries
"""
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, timezone
from app.models import (
    University, Major, ProgramIntake, Student, 
    DegreeLevel, TeachingLanguage, IntakeTerm
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
    
    def search_majors(
        self,
        university_id: Optional[int] = None,
        name: Optional[str] = None,
        degree_level: Optional[DegreeLevel] = None,
        teaching_language: Optional[TeachingLanguage] = None,
        discipline: Optional[str] = None,
        is_featured: Optional[bool] = None,
        limit: int = 10
    ) -> List[Major]:
        """Search majors by various criteria"""
        query = self.db.query(Major)
        
        if university_id:
            query = query.filter(Major.university_id == university_id)
        if name:
            query = query.filter(Major.name.ilike(f"%{name}%"))
        if degree_level:
            query = query.filter(Major.degree_level == degree_level)
        if teaching_language:
            query = query.filter(Major.teaching_language == teaching_language)
        if discipline:
            query = query.filter(Major.discipline.ilike(f"%{discipline}%"))
        if is_featured is not None:
            query = query.filter(Major.is_featured == is_featured)
        
        query = query.order_by(Major.is_featured.desc(), Major.name)
        
        return query.limit(limit).all()
    
    def search_program_intakes(
        self,
        university_id: Optional[int] = None,
        major_id: Optional[int] = None,
        intake_term: Optional[IntakeTerm] = None,
        intake_year: Optional[int] = None,
        upcoming_only: bool = True,
        limit: int = 10
    ) -> List[ProgramIntake]:
        """Search program intakes with optional filters"""
        query = self.db.query(ProgramIntake)
        
        if university_id:
            query = query.filter(ProgramIntake.university_id == university_id)
        if major_id:
            query = query.filter(ProgramIntake.major_id == major_id)
        if intake_term:
            query = query.filter(ProgramIntake.intake_term == intake_term)
        if intake_year:
            query = query.filter(ProgramIntake.intake_year == intake_year)
        if upcoming_only:
            now = datetime.now(timezone.utc)
            query = query.filter(ProgramIntake.application_deadline >= now)
        
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
        if university.description:
            info += f"Description: {university.description}\n"
        if university.website:
            info += f"Website: {university.website}\n"
        return info
    
    def format_major_info(self, major: Major) -> str:
        """Format major information as text for LLM"""
        info = f"Major: {major.name}\n"
        info += f"Degree Level: {major.degree_level.value}\n"
        info += f"Teaching Language: {major.teaching_language.value}\n"
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
        info += f"Program Intake: {intake.intake_term.value} {intake.intake_year}\n"
        
        # Include major information (for duration calculation)
        if intake.major:
            info += f"Major: {intake.major.name}\n"
            # Use intake's degree_type if available, otherwise use major's degree_level
            degree_info = intake.degree_type.value if intake.degree_type else (intake.major.degree_level.value if intake.major.degree_level else "N/A")
            info += f"Degree Type: {degree_info}\n"
            
            # Use intake's duration if available, otherwise use major's duration
            duration = intake.duration_years if intake.duration_years is not None else (intake.major.duration_years if intake.major.duration_years else None)
            if duration:
                info += f"Program Duration: {duration} years\n"
                # Calculate expected graduation year
                expected_end_year = intake.intake_year + int(duration)
                info += f"Expected Graduation Year: {expected_end_year}\n"
        
        # Teaching language (use intake's if available, otherwise major's)
        teaching_lang = intake.teaching_language.value if intake.teaching_language else (intake.major.teaching_language.value if intake.major and intake.major.teaching_language else "N/A")
        info += f"Teaching Language: {teaching_lang}\n"
        
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


