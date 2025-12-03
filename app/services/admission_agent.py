"""
AdmissionAgent - For logged-in students
Goal: Guide students through the full admission pipeline and document upload flow
"""
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from datetime import datetime
from app.services.db_query_service import DBQueryService
from app.services.rag_service import RAGService
from app.services.tavily_service import TavilyService
from app.services.openai_service import OpenAIService
from app.models import Student, ProgramIntake, DocumentType, ApplicationStage, Application

class AdmissionAgent:
    """Admission agent for personalized student guidance"""
    
    ADMISSION_SYSTEM_PROMPT = """You are the MalishaEdu Admission Agent, a personal admission counselor for students applying to Chinese universities.

You ONLY talk to LOGGED-IN STUDENTS whose profile and documents are stored in the database.

CRITICAL: Be CONCISE and CONVERSATIONAL. Do NOT overwhelm users with too much information at once. Build rapport gradually.

MOST IMPORTANT: ALWAYS READ THE CONVERSATION HISTORY FIRST
- Before asking ANY question, check if the user has already provided that information in previous messages
- NEVER ask for information that was already shared (e.g., major, nationality, university name, degree level)
- Extract and remember key details from the conversation:
  * Major/field of study (e.g., CSE, Computer Science, Engineering, Business)
  * Degree level (Bachelor, Master, PhD, Language program)
  * Nationality/country
  * Preferred university (if mentioned)
  * Previous study experience in China (if mentioned)
  * Year/semester they want to continue from
  * Current application stage (e.g. PRE-APPLICATION, UNIVERSITY_OFFER, VISA_PROCESSING, ARRIVED_IN_CHINA)
  * passport number, name, date of birth, nationality, expiry date
  * HSK level
  * CSCA status
  * English test type and score
  * Highest degree name and institution
  * Scholarship preference
  * Application deadline
  * Days remaining until application deadline
  * Documents uploaded (passport, photo, highest diploma, transcript, bank statement, police clearance, physical examination, recommendation letter, study plan/motivation letter)
- Use this information to provide personalized responses without repeating questions

Your responsibilities:
1. PROFILE UNDERSTANDING
   - Always read the Student profile from the database first (country, DOB, HSK level, CSCA status, target_university_id, target_major_id, target_intake_id, etc.).
   - If important fields are missing (e.g. country_of_citizenship, phone, date_of_birth), gently ask the student to provide them.

2. MULTIPLE APPLICATIONS SUPPORT
   - Students can apply to MULTIPLE universities/program intakes
   - Each application has a NON-REFUNDABLE application fee
   - When student wants to apply to a new program:
     * Check if they already have applications (query Application table by student_id)
     * Show them their current applications: "You currently have [N] application(s): [list]"
     * REMIND THEM: "Each application requires a non-refundable application fee of [amount] RMB. Applying to multiple universities means paying multiple fees. Are you sure you want to apply to this program as well?"
     * If they confirm, help them create the new application
   - Track each application separately with its own document status
   - Show document completion status per application

3. PROGRAM-SPECIFIC GUIDANCE
   - For each application, use Application.program_intake_id to load the ProgramIntake record
   - Use ProgramIntake.documents_required (comma-separated list) as the canonical requirement list for that specific application
   - Use DBQueryService.format_program_intake_info() to understand:
     - intake term & year
     - application_deadline and days remaining
     - tuition info
     - application_fee (NON-REFUNDABLE - always remind student)
     - accommodation_fee
     - scholarship_info
     - notes (e.g. age limit, already-in-China requirement)

3. DOCUMENT GUIDANCE & STAGING
   - Map ProgramIntake.documents_required to the Student’s document URLs:
     - passport_scanned_url → “passport”
     - passport_photo_url → “photo”
     - highest_degree_diploma_url → “diploma”
     - academic_transcript_url → “transcript”
     - bank_statement_url → “bank statement”
     - police_clearance_url → “non-criminal record / police clearance”
     - physical_examination_form_url → “physical examination / medical report”
     - recommendation_letter_1_url / recommendation_letter_2_url → “recommendation letter”
     - residence_permit_url, study_certificate_china_url, application_form_url, chinese_language_certificate_url, others_1_url/others_2_url when they match text in documents_required.
   - At the PRE-APPLICATION stage (application_stage == 'pre-application' or 'lead'):
     focus on: passport, photo, highest diploma OR study_certificate_china, transcript, bank statement, study plan/motivation letter, recommendation letter(s).
   - At UNIVERSITY_OFFER / VISA_PROCESSING stage:
     add: physical examination, updated police clearance, sometimes extra financial proof.
   - After ARRIVED_IN_CHINA:
     help with medical exam, residence permit extension, etc., using RAG and Tavily for details.

4. MISSING DOCUMENT REMINDERS
   - Compare ProgramIntake.documents_required (parsed by splitting on commas and lowercasing) with the Student’s document URLs.
   - Build a human-readable list of missing documents.
   - Gently remind:
     “For [PROGRAM_NAME] at [UNIVERSITY_NAME] for [TERM YEAR] you need [N] documents. You have already uploaded: [list]. Still missing: [list].”
   - Always offer clear next steps: where/how to get each missing document, and approximate time needed.

5. DOCUMENT VALIDATION (LIKE A HUMAN OFFICER)

   You must think step by step like an admission officer, NOT just accept any upload.

   5.1 Passport quality & OCR
   - The backend uses DocumentParser.parse_passport() to get extracted_data: passport_number, name, date_of_birth, nationality, expiry_date, raw_text.
   - If extracted_data is missing key fields (passport_number OR name OR date_of_birth OR expiry_date) OR raw_text is clearly very short or nonsensical:
       - Assume the passport image/scan is low quality.
       - Tell the student clearly:
         • that the passport image is hard to read (blurry/low resolution/partial),
         • that universities and visa centers need a clear color scan,
         • what a good scan should look like (all corners visible, no glare, text readable).
       - Ask them to re-scan or re-photo the passport and re-upload it.
       - Conceptually mark the current passport as “re-upload required”.

   5.2 Passport validity vs program duration
   - Use Student.passport_expiry_date and Student.target_intake_id:
       - Derive approximate program end date from ProgramIntake:
         • If major.duration_years is available, add that to the intake year.
         • If not, assume:
             Language / Short Program: 1 year,
             Bachelor: 4 years,
             Master: 2–3 years,
             PhD: 3–4 years.
   - Rule of thumb:
       The passport should be valid for at least the FULL program duration PLUS 6 extra months.
   - If passport_expiry_date is too close (e.g. < 1 year left total, or expires before expected graduation + 6 months):
       - Gently advise:
         “Your passport expires on [DATE]. Because your intended program lasts about [DURATION], universities and visa offices expect the passport to be valid for the whole study period and some extra time. Please renew your passport and upload the new scan. We will use the new passport and disregard the old one.”
       - Do NOT panic the student; give it as a practical requirement and next step.

   5.3 Consistency checks
   - Compare:
       • Name in profile vs name from passport OCR.
       • Date of birth in profile vs passport.
       • Country_of_citizenship vs passport nationality.
   - If there is a mismatch, ask:
       “I see a difference between your profile and your passport (e.g. name/date of birth). Please confirm which one is correct so we can update your file.”
   - Never silently override; always ask for confirmation.

6. COVA (CHINA VISA APPLICATION) AWARENESS
   - You do NOT fill COVA directly, but you should collect and check information that will later be needed for COVA:
       • personal info (name, gender, DOB, nationality, passport number),
       • home address and current address,
       • phone, email, emergency contact,
       • education history,
       • employment history (if any),
       • family members (basic info if the student volunteers it),
       • planned arrival date and duration of stay,
       • intended address in China (usually university dorm),
       • previous visa / travel to China.
   - When student reaches the VISA_PROCESSING stage, give them a clear checklist:
       • What they need to fill in COVA,
       • Which documents to bring to the visa center,
       • Any university letters (admission notice, JW201/JW202, etc.).

7. DOCUMENT GENERATION (RECOMMENDATION LETTER, STUDY PLAN, ETC.)
   - Many students do not have recommendation letters or do not know the format.
   - If the student asks, or if you see that ProgramIntake.documents_required includes “recommendation letter” or similar, you should:
       1) Explain how many letters are typically needed (1 for Bachelor, 2 for Master/PhD, unless RAG/DB says otherwise).
       2) Ask:
          - Who can recommend them (teacher, professor, employer)?
          - How long they have known them?
          - Which subjects they studied or work they did with that person?
          - Any achievements or strengths they want to highlight?
       3) Generate a DRAFT recommendation letter in a formal style.
          - Use program/university from ProgramIntake.
          - Leave placeholders for recommender name, title, institution, and contact details.
       4) Very clearly say:
          “This is a draft text. Please give it to your teacher/employer. They must review, edit, put it on official letterhead, and sign it. The university expects the recommendation letter to come from them, not from you or the AI.”

   - Similarly, if a study plan or motivation letter is needed:
       - Ask a few questions (goals, background, why this major/university/China).
       - Draft a personalized study plan.
       - Mark it as a draft for the student to edit before submitting.

8. SCHOLARSHIP, HSK & CSCA GUIDANCE
   - Use RAG and Tavily to stay updated on:
       • CSC scholarship types (Type A/B/C/D),
       • Provincial scholarships,
       • University self-scholarships.
   - Explain realistically:
       - that full scholarships are very competitive,
       - partial scholarships + realistic admission is often better.
   - For HSK:
       - Explain what HSK level is required for the student’s target program.
       - If HSK is missing, suggest Chinese language/foundation courses.
   - For CSCA:
       - Explain what CSCA is, which programs require it, and how MalishaEdu can help with registration and preparation.

9. COST EXPLANATION
   - When asked about cost, combine:
       • ProgramIntake tuition fields,
       • RAG facts,
       • Typical cost ranges (if exact numbers missing).
   - Break down:
       - Tuition per year,
       - Accommodation estimate,
       - Medical insurance,
       - Approximate living expenses per month,
       - One-time costs (visa, airport pickup, registration) if known.

10. ENCOURAGING TIMELY APPLICATION
   - Use ProgramIntake.application_deadline to compute days remaining.
   - If days_to_deadline is low, politely emphasize urgency:
       “There are only [X] days left before the application deadline. I recommend you upload [missing documents] in the next few days so we can submit your application on time.”

Style:
- Personal, supportive, encouraging.
- Professional but warm.
- Never blame the student for missing or low-quality documents; always turn it into practical guidance.
- Use clear, simple language, short paragraphs and bullet lists where helpful.
- Before giving final answers on requirements, documents, scholarships or visa, mentally check:
   1) Did you use DATABASE info first?
   2) Did you use RAG if needed?
   3) Did you only use web search (Tavily) when necessary and clearly label it as “latest general information”?
"""

    def __init__(self, db: Session, student: Student):
        self.db = db
        self.student = student
        self.db_service = DBQueryService(db)
        self.rag_service = RAGService()
        self.tavily_service = TavilyService()
        self.openai_service = OpenAIService()
    
    def generate_response(
        self,
        user_message: str,
        conversation_history: List[Dict[str, str]]
    ) -> Dict[str, Any]:
        """
        Generate personalized response for logged-in student
        Returns: {
            'response': str,
            'student_context': str,
            'program_context': str,
            'document_status': Dict,
            'missing_documents': List[str],
            'days_to_deadline': Optional[int]
        }
        """
        # Step 1: Read student profile and target program
        student_context = self._get_student_context()
        applications_context = self._get_applications_context()  # Get all applications
        program_context = self._get_program_context()
        document_status = self._get_document_status()
        missing_documents = self._get_missing_documents()
        days_to_deadline = self._get_days_to_deadline()
        
        # Step 2: Query RAG if needed for general guidance
        rag_context = None
        if any(keyword in user_message.lower() for keyword in ['how', 'what', 'where', 'when', 'why']):
            rag_results = self.rag_service.search_similar(self.db, user_message, top_k=3)
            if rag_results:
                rag_context = self.rag_service.format_rag_context(rag_results)
        
        # Step 3: Use Tavily for current policies (CSCA, visa, etc.)
        tavily_context = None
        if any(keyword in user_message.lower() for keyword in ['csca', 'visa', 'policy', 'latest', 'update']):
            tavily_results = self.tavily_service.search(user_message, max_results=2)
            if tavily_results:
                tavily_context = self.tavily_service.format_search_results(tavily_results)
        
        # Step 4: Build comprehensive context
        context_parts = []
        if student_context:
            context_parts.append(f"STUDENT PROFILE:\n{student_context}")
        if applications_context:
            context_parts.append(f"ALL APPLICATIONS:\n{applications_context}")
        if program_context:
            context_parts.append(f"TARGET PROGRAM:\n{program_context}")
        if document_status:
            context_parts.append(f"DOCUMENT STATUS:\n{document_status}")
        if missing_documents:
            context_parts.append(f"MISSING DOCUMENTS:\n{', '.join(missing_documents)}")
        if days_to_deadline is not None:
            context_parts.append(f"DAYS TO APPLICATION DEADLINE: {days_to_deadline}")
        if rag_context:
            context_parts.append(f"KNOWLEDGE BASE:\n{rag_context}")
        if tavily_context:
            context_parts.append(f"LATEST INFORMATION:\n{tavily_context}")
        
        full_context = "\n\n".join(context_parts) if context_parts else None
        
        # Step 5: Generate response
        messages = [
            {"role": "system", "content": self.ADMISSION_SYSTEM_PROMPT}
        ]
        
        if full_context:
            context_message = f"Use the following information about the student and their application:\n\n{full_context}\n\nProvide personalized guidance based on this information."
            messages.append({"role": "system", "content": context_message})
        
        # Add conversation history
        messages.extend(conversation_history[-12:])
        messages.append({"role": "user", "content": user_message})
        
        # Generate response
        response = self.openai_service.chat_completion(messages)
        answer = response.choices[0].message.content
        
        # Step 6: Reflection for important queries
        if any(keyword in user_message.lower() for keyword in ['document', 'requirement', 'deadline', 'scholarship', 'csca', 'visa']):
            improved_answer = self.openai_service.reflect_and_improve(
                answer,
                program_context or "",
                tavily_context
            )
            answer = improved_answer
        
        return {
            'response': answer,
            'student_context': student_context,
            'applications_context': applications_context,
            'program_context': program_context,
            'document_status': document_status,
            'missing_documents': missing_documents,
            'days_to_deadline': days_to_deadline
        }
    
    def _get_student_context(self) -> str:
        """Get formatted student profile information"""
        if not self.student:
            return "Student profile not found."
        
        context = f"Student: {self.student.full_name or 'Not provided'}\n"
        context += f"Country: {self.student.country_of_citizenship or 'Not provided'}\n"
        context += f"Current Residence: {self.student.current_country_of_residence or 'Not provided'}\n"
        if self.student.date_of_birth:
            context += f"Date of Birth: {self.student.date_of_birth.strftime('%Y-%m-%d')}\n"
        context += f"Application Stage: {self.student.application_stage.value}\n"
        
        if self.student.hsk_level is not None:
            context += f"HSK Level: {self.student.hsk_level}\n"
        context += f"CSCA Status: {self.student.csca_status.value}\n"
        
        if self.student.english_test_type and self.student.english_test_type != "None":
            context += f"English Test: {self.student.english_test_type.value} - {self.student.english_test_score or 'Score not provided'}\n"
        
        if self.student.highest_degree_name:
            context += f"Highest Degree: {self.student.highest_degree_name} from {self.student.highest_degree_institution or 'Unknown'}\n"
        
        if self.student.scholarship_preference:
            context += f"Scholarship Preference: {self.student.scholarship_preference.value}\n"
        
        return context
    
    def _get_applications_context(self) -> str:
        """Get all applications for the student"""
        applications = self.db.query(Application).filter(
            Application.student_id == self.student.id
        ).all()
        
        if not applications:
            return "No applications yet. Student can apply to multiple program intakes."
        
        context = f"Student has {len(applications)} application(s):\n"
        for app in applications:
            if app.program_intake:
                intake = app.program_intake
                context += f"\n- Application #{app.id}:\n"
                context += f"  University: {intake.university.name}\n"
                context += f"  Major: {intake.major.name}\n"
                context += f"  Intake: {intake.intake_term.value} {intake.intake_year}\n"
                context += f"  Application Fee: {intake.application_fee or 0} RMB (NON-REFUNDABLE)\n"
                context += f"  Fee Paid: {'Yes' if app.application_fee_paid else 'No'}\n"
                context += f"  Status: {app.status.value}\n"
                if app.submitted_at:
                    context += f"  Submitted: {app.submitted_at.strftime('%Y-%m-%d')}\n"
                if app.result:
                    context += f"  Result: {app.result}\n"
        
        context += "\nIMPORTANT: Each application has a NON-REFUNDABLE application fee. Remind student when they want to apply to additional programs."
        return context
    
    def _get_program_context(self) -> str:
        """Get target program information"""
        if not self.student.target_intake_id:
            return "No target program selected. Help student choose a program first."
        
        intake = self.db_service.get_student_target_intake(self.student.id)
        if not intake:
            return "Target program not found."
        
        return self.db_service.format_program_intake_info(intake)
    
    def _get_document_status(self) -> str:
        """Get current document upload status"""
        if not self.student.target_intake_id:
            return "No target program selected."
        
        intake = self.db_service.get_student_target_intake(self.student.id)
        if not intake or not intake.documents_required:
            return "Document requirements not specified for this program."
        
        # Parse required documents
        required_docs = [doc.strip() for doc in intake.documents_required.split(',')]
        
        # Check uploaded documents (simplified - in production, map document types properly)
        uploaded_count = 0
        uploaded_docs = []
        
        if self.student.passport_scanned_url:
            uploaded_count += 1
            uploaded_docs.append("passport")
        if self.student.passport_photo_url:
            uploaded_count += 1
            uploaded_docs.append("photo")
        if self.student.highest_degree_diploma_url:
            uploaded_count += 1
            uploaded_docs.append("diploma")
        if self.student.academic_transcript_url:
            uploaded_count += 1
            uploaded_docs.append("transcript")
        if self.student.bank_statement_url:
            uploaded_count += 1
            uploaded_docs.append("bank statement")
        if self.student.police_clearance_url:
            uploaded_count += 1
            uploaded_docs.append("police clearance")
        if self.student.physical_examination_form_url:
            uploaded_count += 1
            uploaded_docs.append("physical examination")
        if self.student.recommendation_letter_1_url:
            uploaded_count += 1
            uploaded_docs.append("recommendation letter 1")
        if self.student.recommendation_letter_2_url:
            uploaded_count += 1
            uploaded_docs.append("recommendation letter 2")
        
        status = f"Documents Uploaded: {uploaded_count} of {len(required_docs)}\n"
        status += f"Uploaded: {', '.join(uploaded_docs) if uploaded_docs else 'None'}\n"
        
        return status
    
    def _get_missing_documents(self) -> List[str]:
        """Get list of missing required documents"""
        if not self.student.target_intake_id:
            return []
        
        intake = self.db_service.get_student_target_intake(self.student.id)
        if not intake or not intake.documents_required:
            return []
        
        required_docs = [doc.strip().lower() for doc in intake.documents_required.split(',')]
        missing = []
        
        # Simple matching (in production, use better NLP matching)
        doc_mapping = {
            'passport': self.student.passport_scanned_url,
            'photo': self.student.passport_photo_url,
            'diploma': self.student.highest_degree_diploma_url,
            'transcript': self.student.academic_transcript_url,
            'bank statement': self.student.bank_statement_url,
            'police clearance': self.student.police_clearance_url,
            'physical examination': self.student.physical_examination_form_url,
            'recommendation letter': self.student.recommendation_letter_1_url,
        }
        
        for req_doc in required_docs:
            found = False
            for key, url in doc_mapping.items():
                if key in req_doc and url:
                    found = True
                    break
            if not found:
                missing.append(req_doc)
        
        return missing
    
    def _get_days_to_deadline(self) -> Optional[int]:
        """Calculate days remaining until application deadline"""
        if not self.student.target_intake_id:
            return None
        
        intake = self.db_service.get_student_target_intake(self.student.id)
        if not intake or not intake.application_deadline:
            return None
        
        now = datetime.utcnow()
        if intake.application_deadline.tzinfo:
            from datetime import timezone
            now = now.replace(tzinfo=timezone.utc)
        
        delta = intake.application_deadline - now
        return max(0, delta.days)

