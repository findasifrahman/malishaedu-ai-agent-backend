"""
Data Ingestion Service
Deterministic backend code that handles all SQL generation and execution.
NO LLM-generated SQL is ever executed.
"""
from typing import Dict, List, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from app.models import (
    University, Major, ProgramIntake, ProgramDocument, 
    Scholarship, ProgramIntakeScholarship, IntakeTerm
)
from app.schemas.document_import import ExtractedData, MajorInfo, ProgramIntakeInfo
from datetime import datetime
import json


class DataIngestionService:
    """Service to ingest extracted data into database deterministically"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def ingest_extracted_data(self, extracted_data: Dict) -> Dict:
        """
        Ingest extracted data into database.
        
        Identity Rules (defined in code):
        - University: Match by name (case-insensitive)
        - Major: (university_id, lower(name), degree_level, teaching_language)
        - ProgramIntake: (major_id, intake_term, intake_year)
        - ProgramDocument: (program_intake_id, name)
        - Scholarship: (name) - global, not per university
        - ProgramIntakeScholarship: (program_intake_id, scholarship_id)
        
        Returns:
            Dict with counts: {
                "majors_inserted": int,
                "majors_updated": int,
                "program_intakes_inserted": int,
                "program_intakes_updated": int,
                "documents_inserted": int,
                "documents_updated": int,
                "scholarships_inserted": int,
                "scholarships_updated": int,
                "links_inserted": int,
                "errors": List[str]
            }
        """
        errors = []
        counts = {
            "majors_inserted": 0,
            "majors_updated": 0,
            "program_intakes_inserted": 0,
            "program_intakes_updated": 0,
            "documents_inserted": 0,
            "documents_updated": 0,
            "scholarships_inserted": 0,
            "scholarships_updated": 0,
            "links_inserted": 0,
            "errors": []
        }
        
        try:
            # Step 1: Resolve university
            university_name = extracted_data.get("university_name", "").strip()
            if not university_name:
                errors.append("University name is required")
                counts["errors"] = errors
                return counts
            
            university = self._resolve_university(university_name)
            if not university:
                errors.append(f"University not found: {university_name}. Please ensure the university exists in the database.")
                counts["errors"] = errors
                return counts
            
            # Step 2: Process majors and their intakes
            # Expand major_groups into individual majors if present (optimization for many majors)
            majors_data = []
            majors_list = extracted_data.get("majors")
            major_groups = extracted_data.get("major_groups", [])
            
            # First, add individual majors if present
            if majors_list:
                majors_data.extend(majors_list)
            
            # Then, expand major_groups into individual majors
            if major_groups:
                for group in major_groups:
                    group_props = {
                        "degree_level": group.get("degree_level"),
                        "teaching_language": group.get("teaching_language"),
                        "duration_years": group.get("duration_years"),
                        "discipline": group.get("discipline"),
                        "category": group.get("category"),
                        "keywords": group.get("keywords"),
                        "is_featured": group.get("is_featured", False),
                        "is_active": group.get("is_active", True),
                        "intakes": group.get("intakes", [])
                    }
                    # Create individual major entry for each major name in the group
                    major_names = group.get("major_names", [])
                    for major_name in major_names:
                        major_entry = {
                            "name": major_name,
                            **group_props
                        }
                        majors_data.append(major_entry)
            
            if not majors_data:
                errors.append("No majors found in extracted data (neither 'majors' nor 'major_groups' provided)")
                counts["errors"] = errors
                return counts
            
            # Process each major
            for major_data in majors_data:
                try:
                    major_result = self._process_major(university.id, major_data)
                    counts["majors_inserted"] += major_result["inserted"]
                    counts["majors_updated"] += major_result["updated"]
                    
                    # Process intakes for this major
                    if major_result["major_id"]:
                        intakes_data = major_data.get("intakes", [])
                        major_degree_level = major_data.get("degree_level")
                        major_teaching_language = major_data.get("teaching_language")
                        major_duration_years = major_data.get("duration_years")
                        for intake_data in intakes_data:
                            intake_result = self._process_program_intake(
                                university.id, 
                                major_result["major_id"], 
                                intake_data,
                                major_degree_level,
                                major_teaching_language,
                                major_duration_years
                            )
                            counts["program_intakes_inserted"] += intake_result["inserted"]
                            counts["program_intakes_updated"] += intake_result["updated"]
                            
                            # Process documents for this intake
                            if intake_result["intake_id"]:
                                docs_result = self._process_documents(
                                    intake_result["intake_id"],
                                    intake_data.get("documents", [])
                                )
                                counts["documents_inserted"] += docs_result["inserted"]
                                counts["documents_updated"] += docs_result["updated"]
                            
                            # Process scholarships for this intake
                            if intake_result["intake_id"]:
                                scholarships_result = self._process_scholarships(
                                    intake_result["intake_id"],
                                    intake_data.get("scholarships", [])
                                )
                                counts["scholarships_inserted"] += scholarships_result["inserted"]
                                counts["scholarships_updated"] += scholarships_result["updated"]
                                counts["links_inserted"] += scholarships_result["links_inserted"]
                
                except Exception as e:
                    error_msg = f"Error processing major '{major_data.get('name', 'unknown')}': {str(e)}"
                    errors.append(error_msg)
                    print(f"❌ {error_msg}")
                    import traceback
                    print(traceback.format_exc())
            
            # Commit transaction
            self.db.commit()
            
            # Check if critical entities were inserted
            if counts["program_intakes_inserted"] == 0 and counts["program_intakes_updated"] == 0:
                errors.append("WARNING: No program intakes were inserted or updated. Please verify the extracted data.")
            
            counts["errors"] = errors
            return counts
            
        except Exception as e:
            self.db.rollback()
            error_msg = f"Data ingestion failed: {str(e)}"
            errors.append(error_msg)
            counts["errors"] = errors
            print(f"❌ {error_msg}")
            import traceback
            print(traceback.format_exc())
            return counts
    
    def _resolve_university(self, university_name: str) -> Optional[University]:
        """Resolve university by name (case-insensitive)"""
        university = self.db.query(University).filter(
            func.lower(University.name) == func.lower(university_name)
        ).first()
        return university
    
    def _process_major(self, university_id: int, major_data: Dict) -> Dict:
        """
        Process major: insert or update.
        
        Identity: (university_id, lower(name), degree_level, teaching_language)
        """
        name = major_data.get("name", "").strip()
        degree_level = major_data.get("degree_level")
        teaching_language = major_data.get("teaching_language")
        
        if not name or not degree_level or not teaching_language:
            return {"inserted": 0, "updated": 0, "major_id": None}
        
        # Find existing major by identity
        existing_major = self.db.query(Major).filter(
            and_(
                Major.university_id == university_id,
                func.lower(Major.name) == func.lower(name),
                Major.degree_level == degree_level,
                Major.teaching_language == teaching_language
            )
        ).first()
        
        # Prepare major data
        major_fields = {
            "university_id": university_id,
            "name": name,
            "degree_level": degree_level,
            "teaching_language": teaching_language,
            "duration_years": major_data.get("duration_years"),
            "discipline": major_data.get("discipline"),
            "category": major_data.get("category"),
            "keywords": json.dumps(major_data.get("keywords")) if major_data.get("keywords") else None,
            "is_featured": major_data.get("is_featured", False),
            "is_active": major_data.get("is_active", True)
        }
        
        if existing_major:
            # Update existing major
            for key, value in major_fields.items():
                setattr(existing_major, key, value)
            self.db.flush()
            return {"inserted": 0, "updated": 1, "major_id": existing_major.id}
        else:
            # Insert new major
            new_major = Major(**major_fields)
            self.db.add(new_major)
            self.db.flush()
            return {"inserted": 1, "updated": 0, "major_id": new_major.id}
    
    def _process_program_intake(self, university_id: int, major_id: int, intake_data: Dict, major_degree_level: Optional[str] = None, major_teaching_language: Optional[str] = None, major_duration_years: Optional[float] = None) -> Dict:
        """
        Process program intake: insert or update.
        
        Identity: (major_id, intake_term, intake_year)
        """
        intake_term_str = intake_data.get("intake_term")
        intake_year = intake_data.get("intake_year")
        
        if not intake_term_str or not intake_year:
            return {"inserted": 0, "updated": 0, "intake_id": None}
        
        # Normalize intake_term enum
        intake_term = self._normalize_intake_term(intake_term_str)
        if not intake_term:
            return {"inserted": 0, "updated": 0, "intake_id": None}
        
        # Find existing intake by identity
        existing_intake = self.db.query(ProgramIntake).filter(
            and_(
                ProgramIntake.major_id == major_id,
                ProgramIntake.intake_term == intake_term,
                ProgramIntake.intake_year == intake_year
            )
        ).first()
        
        # Parse dates
        fees_data = intake_data.get("fees", {})
        requirements_data = intake_data.get("requirements", {})
        
        application_deadline = None
        if intake_data.get("application_deadline"):
            try:
                application_deadline = datetime.fromisoformat(intake_data["application_deadline"].replace("Z", "+00:00"))
            except:
                try:
                    application_deadline = datetime.strptime(intake_data["application_deadline"], "%Y-%m-%d")
                except:
                    pass
        
        program_start_date = None
        if intake_data.get("program_start_date"):
            try:
                program_start_date = datetime.strptime(intake_data["program_start_date"], "%Y-%m-%d").date()
            except:
                pass
        
        # Prepare intake data
        intake_fields = {
            "university_id": university_id,
            "major_id": major_id,
            "intake_term": intake_term,
            "intake_year": intake_year,
            "application_deadline": application_deadline,
            "deadline_type": intake_data.get("deadline_type"),
            "program_start_date": program_start_date,
            "tuition_per_semester": fees_data.get("tuition_per_semester"),
            "tuition_per_year": fees_data.get("tuition_per_year"),
            "application_fee": fees_data.get("application_fee"),
            "accommodation_fee": fees_data.get("accommodation_fee"),
            "accommodation_fee_period": fees_data.get("accommodation_fee_period"),
            "accommodation_note": fees_data.get("accommodation_note"),
            "service_fee": fees_data.get("service_fee"),
            "medical_insurance_fee": fees_data.get("medical_insurance_fee"),
            "medical_insurance_fee_period": fees_data.get("medical_insurance_fee_period"),
            "arrival_medical_checkup_fee": fees_data.get("arrival_medical_checkup_fee"),
            "arrival_medical_checkup_is_one_time": fees_data.get("arrival_medical_checkup_is_one_time"),
            "visa_extension_fee": fees_data.get("visa_extension_fee"),
            "notes": fees_data.get("notes"),
            "scholarship_available": intake_data.get("scholarship_available"),
            "scholarship_info": intake_data.get("scholarship_info"),
            "age_min": requirements_data.get("age_min"),
            "age_max": requirements_data.get("age_max"),
            "min_average_score": requirements_data.get("min_average_score"),
            "interview_required": requirements_data.get("interview_required"),
            "written_test_required": requirements_data.get("written_test_required"),
            "acceptance_letter_required": requirements_data.get("acceptance_letter_required"),
            "inside_china_applicants_allowed": requirements_data.get("inside_china_applicants_allowed"),
            "inside_china_extra_requirements": requirements_data.get("inside_china_extra_requirements"),
            "bank_statement_required": requirements_data.get("bank_statement_required"),
            "bank_statement_amount": requirements_data.get("bank_statement_amount"),
            "bank_statement_currency": requirements_data.get("bank_statement_currency"),
            "bank_statement_note": requirements_data.get("bank_statement_note"),
            "hsk_required": requirements_data.get("hsk_required"),
            "hsk_level": requirements_data.get("hsk_level"),
            "hsk_min_score": requirements_data.get("hsk_min_score"),
            "english_test_required": requirements_data.get("english_test_required"),
            "english_test_note": requirements_data.get("english_test_note"),
            "currency": fees_data.get("currency", "CNY"),
            "teaching_language": intake_data.get("teaching_language") or major_teaching_language or "English",  # Use intake teaching_language, or major's, or default to "English"
            "duration_years": float(intake_data.get("duration_years")) if intake_data.get("duration_years") is not None else (float(major_duration_years) if major_duration_years is not None else None),  # Convert to float, use intake or major's duration_years
            "degree_type": intake_data.get("degree_type") or major_degree_level  # Use intake degree_type or fallback to major's degree_level
        }
        
        if existing_intake:
            # Update existing intake
            for key, value in intake_fields.items():
                setattr(existing_intake, key, value)
            self.db.flush()
            return {"inserted": 0, "updated": 1, "intake_id": existing_intake.id}
        else:
            # Insert new intake
            new_intake = ProgramIntake(**intake_fields)
            self.db.add(new_intake)
            self.db.flush()
            return {"inserted": 1, "updated": 0, "intake_id": new_intake.id}
    
    def _process_documents(self, program_intake_id: int, documents_data: List[Dict]) -> Dict:
        """
        Process program documents: insert or update.
        
        Identity: (program_intake_id, name)
        """
        inserted = 0
        updated = 0
        
        for doc_data in documents_data:
            name = doc_data.get("name", "").strip()
            if not name:
                continue
            
            # Find existing document by identity
            existing_doc = self.db.query(ProgramDocument).filter(
                and_(
                    ProgramDocument.program_intake_id == program_intake_id,
                    ProgramDocument.name == name
                )
            ).first()
            
            doc_fields = {
                "program_intake_id": program_intake_id,
                "name": name,
                "is_required": doc_data.get("is_required", True),
                "rules": doc_data.get("rules"),
                "applies_to": doc_data.get("applies_to")
            }
            
            if existing_doc:
                # Update existing document
                for key, value in doc_fields.items():
                    setattr(existing_doc, key, value)
                updated += 1
            else:
                # Insert new document
                new_doc = ProgramDocument(**doc_fields)
                self.db.add(new_doc)
                inserted += 1
        
        self.db.flush()
        return {"inserted": inserted, "updated": updated}
    
    def _process_scholarships(self, program_intake_id: int, scholarships_data: List[Dict]) -> Dict:
        """
        Process scholarships and links: insert or update.
        
        Scholarship Identity: (name) - global
        Link Identity: (program_intake_id, scholarship_id)
        """
        inserted = 0
        updated = 0
        links_inserted = 0
        
        for scholarship_data in scholarships_data:
            name = scholarship_data.get("name", "").strip()
            if not name:
                continue
            
            # Find or create scholarship (global, not per university)
            existing_scholarship = self.db.query(Scholarship).filter(
                func.lower(Scholarship.name) == func.lower(name)
            ).first()
            
            scholarship_fields = {
                "name": name,
                "provider": scholarship_data.get("provider"),
                "notes": scholarship_data.get("notes")
            }
            
            if existing_scholarship:
                # Update existing scholarship
                for key, value in scholarship_fields.items():
                    setattr(existing_scholarship, key, value)
                scholarship_id = existing_scholarship.id
                updated += 1
            else:
                # Insert new scholarship
                new_scholarship = Scholarship(**scholarship_fields)
                self.db.add(new_scholarship)
                self.db.flush()
                scholarship_id = new_scholarship.id
                inserted += 1
            
            # Process link
            existing_link = self.db.query(ProgramIntakeScholarship).filter(
                and_(
                    ProgramIntakeScholarship.program_intake_id == program_intake_id,
                    ProgramIntakeScholarship.scholarship_id == scholarship_id
                )
            ).first()
            
            link_fields = {
                "program_intake_id": program_intake_id,
                "scholarship_id": scholarship_id,
                "covers_tuition": scholarship_data.get("covers_tuition"),
                "covers_accommodation": scholarship_data.get("covers_accommodation"),
                "covers_insurance": scholarship_data.get("covers_insurance"),
                "tuition_waiver_percent": scholarship_data.get("tuition_waiver_percent"),
                "living_allowance_monthly": scholarship_data.get("living_allowance_monthly"),
                "living_allowance_yearly": scholarship_data.get("living_allowance_yearly"),
                "first_year_only": scholarship_data.get("first_year_only"),
                "renewal_required": scholarship_data.get("renewal_required"),
                "deadline": self._parse_date(scholarship_data.get("deadline")),
                "eligibility_note": scholarship_data.get("eligibility_note")
            }
            
            if existing_link:
                # Update existing link
                for key, value in link_fields.items():
                    setattr(existing_link, key, value)
            else:
                # Insert new link
                new_link = ProgramIntakeScholarship(**link_fields)
                self.db.add(new_link)
                links_inserted += 1
        
        self.db.flush()
        return {"inserted": inserted, "updated": updated, "links_inserted": links_inserted}
    
    def _normalize_intake_term(self, intake_term_str: str) -> Optional[IntakeTerm]:
        """Normalize intake term string to enum"""
        if not intake_term_str:
            return None
        
        term_lower = intake_term_str.strip().lower()
        if term_lower == "march":
            return IntakeTerm.MARCH
        elif term_lower == "september":
            return IntakeTerm.SEPTEMBER
        elif term_lower == "other":
            return IntakeTerm.OTHER
        else:
            # Try to match case-insensitively
            for term in IntakeTerm:
                if term.value.lower() == term_lower:
                    return term
            return None
    
    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """Parse date string to datetime"""
        if not date_str:
            return None
        try:
            return datetime.strptime(date_str, "%Y-%m-%d")
        except:
            return None

