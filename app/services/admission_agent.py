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

CRITICAL RULES:
1. **NEVER collect leads** - The student is already logged in and has a profile. Do NOT ask for contact information, nationality, or other lead data.
2. **Check applications first** - Always check if the student has applied to any program (from Application table linked to student_id).
3. **If no application** - Tell them to first apply to a program through the dashboard. Only provide program-specific information after they have applied.
4. **If application exists** - Check if the intake date and application deadline are in the future (from current date). If not, inform them the application period has passed.
5. **Data source priority**:
   - For scholarship chance questions: Use your domain knowledge, web search (Tavily), and general guidance
   - For cost calculation or documents: Use DATABASE (ProgramIntake, Application tables)
   - For CSCA or MalishaEdu questions: Use RAG knowledge base or web search (Tavily)

CRITICAL: Be CONCISE and TO THE POINT. Answer the specific question asked. Do NOT provide unnecessary information or long explanations. If asked about costs, provide exact numbers in a clear table format. Do NOT repeat information the student already knows.

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

9. COST EXPLANATION & FEE WARNINGS
   - **CRITICAL: When asked about costs, ALWAYS use the Application.scholarship_preference to calculate EXACT costs**
   - **DO NOT show costs without scholarship if the student has a scholarship preference set**
   - Calculate costs based on scholarship type:
     * Type-A: Tuition FREE, Accommodation FREE, Stipend up to 35000 CNY/year
     * Type-B: Tuition FREE, Accommodation FREE, No stipend
     * Type-C/Type-D: Only Tuition FREE, Accommodation PAID
     * Partial-Low (<5000 CNY/year): Partial scholarship, calculate remaining costs
     * Partial-Mid (5100-10000 CNY/year): Partial scholarship, calculate remaining costs
     * Partial-High (10000-15000 CNY/year): Partial scholarship, calculate remaining costs
     * Self-Paid: Full tuition and accommodation costs apply
   - For cost questions, provide:
     * Exact tuition per year (after scholarship deduction if applicable)
     * Exact accommodation per year (after scholarship deduction if applicable)
     * Medical insurance (typically 800 RMB/year, usually not covered by scholarship)
     * Visa extension fee (400 RMB/year)
     * One-time arrival medical checkup (460 RMB)
     * Application fee (one-time, non-refundable)
     * Total cost for entire program duration
   - **BE CONCISE**: Provide a clear table with exact numbers, not long explanations
   - **CRITICAL: ALWAYS WARN ABOUT FEES**
       - Application fees are NON-REFUNDABLE. Always remind: "Please note that application fees are non-refundable. Make sure you're committed to this program before applying."
       - Payment fees: Check Application.payment_fee_required, payment_fee_paid, payment_fee_due for each application.
       - If payment_fee_due > 0, warn: "You have an outstanding payment of [amount] RMB for [program]. Please complete the payment to proceed with your application."
       - If payment_fee_required > 0 and payment_fee_paid < payment_fee_required, remind: "For [program], you need to pay [amount] RMB. You have paid [paid] RMB so far. [due] RMB is still due."
       - Encourage timely payment: "To avoid delays in your application processing, please complete all required payments as soon as possible."

10. ENCOURAGING TIMELY APPLICATION & MISSING INFORMATION
   - Use ProgramIntake.application_deadline to compute days remaining.
   - If days_to_deadline is low, politely emphasize urgency:
       "There are only [X] days left before the application deadline. I recommend you upload [missing documents] in the next few days so we can submit your application on time."
   - **ALWAYS ENCOURAGE PROVIDING MISSING INFORMATION:**
       - If student profile is incomplete (missing DOB, passport, address, etc.), gently remind: "To complete your application, please provide [missing fields]. This information is required by universities."
       - If documents are missing, clearly list them: "For [program], you still need to upload: [list]. These documents are essential for your application."
       - If payment is due, remind: "Don't forget to complete the payment for [program]. The required amount is [amount] RMB."
       - Be encouraging: "Once you provide [missing info], we can move forward with your application. I'm here to help you through every step!"

11. APPLICATION STATE GUIDANCE
   - Check Application.application_state for each application:
       - "not_applied": Encourage to start application, explain requirements, guide through process.
       - "applied" / "submitted" / "under_review": Provide status updates, remind about missing documents or payments if any.
       - "succeeded" / "accepted": Congratulate, guide on next steps (visa, arrival, etc.).
       - "rejected": Be supportive, suggest alternatives, help understand reasons if available.
   - For each application, show clear status and next steps.

12. SCHOLARSHIP PREFERENCE GUIDANCE
   - Check Application.scholarship_preference for each application:
       - Type-A: "Tuition free, accommodation free, stipend up to 35000 CNY/year (depends on university and major)"
       - Type-B: "Tuition free, accommodation free, no stipend"
       - Type-C: "Only tuition fee free, accommodation paid"
       - Type-D: "Only tuition fee free (alternative), accommodation paid"
       - Partial-Low: "Partial scholarship (<5000 CNY/year reduction)"
       - Partial-Mid: "Partial scholarship (5100-10000 CNY/year reduction)"
       - Partial-High: "Partial scholarship (10000-15000 CNY/year reduction)"
       - Self-Paid: "No scholarship, full tuition and accommodation costs"
       - None: "No scholarship (for Language programs)"
   - **When calculating costs, ALWAYS apply the scholarship preference to get exact costs**
   - For Partial scholarships, subtract the scholarship amount from total tuition+accommodation costs
   - Explain what each type means concisely and help student understand their exact costs.
   - Remind: "Language programs typically have no scholarship options. For degree programs, scholarship availability depends on the university and your academic profile."

13. IF ASKED ABOUT LIVING COSTS-FOOD COSTS-TRANSPORTATION COSTS, USE THE FOLLOWING INFORMATION:
    - Use Database to find out the city of the university and use WEB SEARCH to find out the living costs in the city.

14. IF ASKED ANYTHING OUTSIDE CHINA AND CHINA EDUCATION SYSTEM, USE THE FOLLOWING INFORMATION:
    -  I am a MalishaEdu Admission Agent, not a general advisor. I can only answer questions about your application to Chinese universities.
Style:
- Personal, supportive, encouraging.
- Be concise and to the point.
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
            'days_to_deadline': Optional[int],
            'rag_context': Optional[str],
            'tavily_context': Optional[str]
        }
        """
        from datetime import datetime
        
        # Step 1: Check if student has any applications
        applications = self.db.query(Application).filter(
            Application.student_id == self.student.id
        ).all()
        
        # Step 2: Read student profile
        student_context = self._get_student_context()
        applications_context = self._get_applications_context()
        
        # Step 3: Determine data sources based on query type
        rag_context = None
        tavily_context = None
        program_context = None
        document_status = None
        missing_documents = []
        days_to_deadline = None
        
        user_message_lower = user_message.lower()
        
        # Check query type to determine data source
        is_scholarship_chance_question = any(keyword in user_message_lower for keyword in [
            'scholarship chance', 'chance of scholarship', 'get scholarship', 'scholarship possibility',
            'scholarship probability', 'likely to get', 'eligibility for scholarship'
        ])
        
        is_cost_or_document_question = any(keyword in user_message_lower for keyword in [
            'cost', 'fee', 'tuition', 'accommodation', 'payment', 'price', 'document', 'requirement',
            'deadline', 'application fee', 'what do i need', 'what documents', 'required documents'
        ])
        
        is_csca_or_malishaedu_question = any(keyword in user_message_lower for keyword in [
            'csca', 'malishaedu', 'service charge', 'service fee', 'malisha edu'
        ])
        
        # If no applications, tell them to apply first
        if not applications:
            # Still provide general guidance but remind to apply
            if is_csca_or_malishaedu_question:
                # Use RAG/WEB for CSCA/MalishaEdu questions even without application
                rag_results = self.rag_service.search_similar(self.db, user_message, top_k=3)
                if rag_results:
                    rag_context = self.rag_service.format_rag_context(rag_results)
                if not rag_context or 'csca' in user_message_lower or 'latest' in user_message_lower:
                    tavily_results = self.tavily_service.search(user_message, max_results=2)
                    if tavily_results:
                        tavily_context = self.tavily_service.format_search_results(tavily_results)
            else:
                # For other questions, remind to apply first
                pass
        else:
            # Student has applications - check validity and provide program-specific info
            valid_applications = []
            from datetime import timezone
            current_date = datetime.now(timezone.utc)
            
            for app in applications:
                if app.program_intake:
                    intake = app.program_intake
                    # Check if intake date and deadline are in the future
                    # IntakeTerm enum values: MARCH, SEPTEMBER
                    month = 3 if intake.intake_term.value.upper() == 'MARCH' else 9
                    intake_date = datetime(intake.intake_year, month, 1, tzinfo=timezone.utc)
                    
                    # Handle timezone-aware and timezone-naive datetimes
                    if intake.application_deadline:
                        if intake.application_deadline.tzinfo is None:
                            # Make it timezone-aware (UTC)
                            deadline = intake.application_deadline.replace(tzinfo=timezone.utc)
                        else:
                            deadline = intake.application_deadline
                        deadline_valid = deadline > current_date
                    else:
                        deadline_valid = False
                    
                    intake_valid = intake_date > current_date
                    
                    if deadline_valid and intake_valid:
                        valid_applications.append(app)
            
            if valid_applications:
                # Use the first valid application for context
                primary_app = valid_applications[0]
                if primary_app.program_intake:
                    program_context = self._get_program_context_for_application(primary_app)
                    document_status = self._get_document_status_for_application(primary_app)
                    missing_documents = self._get_missing_documents_for_application(primary_app)
                    days_to_deadline = self._get_days_to_deadline_for_application(primary_app)
            else:
                # All applications have passed deadlines - will be handled in context building
                pass
        
        # Step 4: Query appropriate data sources based on question type
        if is_scholarship_chance_question:
            # Use domain knowledge and web search for scholarship chances
            tavily_results = self.tavily_service.search(user_message, max_results=2)
            if tavily_results:
                tavily_context = self.tavily_service.format_search_results(tavily_results)
            # Also check RAG for general scholarship info
            rag_results = self.rag_service.search_similar(self.db, user_message, top_k=3)
            if rag_results:
                rag_context = self.rag_service.format_rag_context(rag_results)
        
        elif is_cost_or_document_question:
            # Use DATABASE for cost and document questions
            # program_context already loaded above if application exists
            # No need for RAG/WEB for these - use DB only
            pass
        
        elif is_csca_or_malishaedu_question:
            # Use RAG/WEB for CSCA and MalishaEdu questions
            rag_results = self.rag_service.search_similar(self.db, user_message, top_k=3)
            if rag_results:
                rag_context = self.rag_service.format_rag_context(rag_results)
            if not rag_context or 'latest' in user_message_lower:
                tavily_results = self.tavily_service.search(user_message, max_results=2)
                if tavily_results:
                    tavily_context = self.tavily_service.format_search_results(tavily_results)
        
        else:
            # General questions - use RAG if available
            rag_results = self.rag_service.search_similar(self.db, user_message, top_k=3)
            if rag_results:
                rag_context = self.rag_service.format_rag_context(rag_results)
        
        # Step 5: Build comprehensive context
        context_parts = []
        
        # Always include student context
        if student_context:
            context_parts.append(f"STUDENT PROFILE:\n{student_context}")
        
        # Include applications context
        if applications_context:
            context_parts.append(f"ALL APPLICATIONS:\n{applications_context}")
        
        # Add special instruction if no applications or all invalid
        if not applications:
            context_parts.append("IMPORTANT: Student has NOT applied to any program yet. Tell them to first apply to a program through the dashboard. Only provide program-specific information after they have applied.")
        elif applications:
            # Check if all applications are invalid (past deadlines)
            valid_applications_check = []
            from datetime import timezone
            current_date_check = datetime.now(timezone.utc)
            for app in applications:
                if app.program_intake:
                    intake = app.program_intake
                    # IntakeTerm enum values: MARCH, SEPTEMBER
                    month = 3 if intake.intake_term.value.upper() == 'MARCH' else 9
                    intake_date = datetime(intake.intake_year, month, 1, tzinfo=timezone.utc)
                    
                    # Handle timezone-aware and timezone-naive datetimes
                    if intake.application_deadline:
                        if intake.application_deadline.tzinfo is None:
                            deadline = intake.application_deadline.replace(tzinfo=timezone.utc)
                        else:
                            deadline = intake.application_deadline
                        deadline_valid = deadline > current_date_check
                    else:
                        deadline_valid = False
                    
                    intake_valid = intake_date > current_date_check
                    if deadline_valid and intake_valid:
                        valid_applications_check.append(app)
            if not valid_applications_check:
                context_parts.append("WARNING: All applications have passed their deadlines. The intake dates or application deadlines are in the past. Student needs to apply to a new program with future intake dates.")
        
        # Include program context only if valid application exists
        if program_context:
            context_parts.append(f"PROGRAM INFORMATION:\n{program_context}")
        
        if document_status:
            context_parts.append(f"DOCUMENT STATUS:\n{document_status}")
        
        if missing_documents:
            context_parts.append(f"MISSING DOCUMENTS:\n{', '.join(missing_documents)}")
        
        if days_to_deadline is not None:
            context_parts.append(f"DAYS TO APPLICATION DEADLINE: {days_to_deadline}")
        
        # Add data source context based on query type
        if rag_context:
            context_parts.append(f"KNOWLEDGE BASE (RAG):\n{rag_context}")
        
        if tavily_context:
            context_parts.append(f"LATEST INFORMATION (WEB):\n{tavily_context}")
        
        full_context = "\n\n".join(context_parts) if context_parts else None
        
        # Step 6: Generate response
        messages = [
            {"role": "system", "content": self.ADMISSION_SYSTEM_PROMPT}
        ]
        
        if full_context:
            context_message = f"Use the following information about the student and their application:\n\n{full_context}\n\nProvide personalized guidance based on this information."
            messages.append({"role": "system", "content": context_message})
        
        # Add conversation history
        messages.extend(conversation_history[-12:] if conversation_history else [])
        messages.append({"role": "user", "content": user_message})
        
        # Generate response
        response = self.openai_service.chat_completion(messages)
        answer = response.choices[0].message.content
        
        # Step 7: Reflection for important queries
        if any(keyword in user_message_lower for keyword in ['document', 'requirement', 'deadline', 'scholarship', 'csca', 'visa']):
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
            'days_to_deadline': days_to_deadline,
            'rag_context': rag_context,
            'tavily_context': tavily_context
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
                context += f"  Degree Level: {app.degree_level or intake.degree_type or 'N/A'}\n"
                context += f"  Intake: {intake.intake_term.value} {intake.intake_year}\n"
                context += f"  Application State: {app.application_state.value if app.application_state else 'not_applied'}\n"
                
                # Cost information with scholarship applied
                tuition_per_year = intake.tuition_per_year or 0
                accommodation_per_year = intake.accommodation_fee or 0
                duration_years = intake.duration_years or (intake.major.duration_years if intake.major else 2)
                
                # Calculate costs based on scholarship preference
                scholarship_pref = app.scholarship_preference.value if app.scholarship_preference else None
                context += f"  Scholarship Preference: {scholarship_pref or 'None'}\n"
                
                # Calculate costs after scholarship
                if scholarship_pref == "Type-A":
                    tuition_after_scholarship = 0
                    accommodation_after_scholarship = 0
                    context += f"  Cost Calculation (Type-A): Tuition FREE, Accommodation FREE, Stipend up to 35000 CNY/year\n"
                elif scholarship_pref == "Type-B":
                    tuition_after_scholarship = 0
                    accommodation_after_scholarship = 0
                    context += f"  Cost Calculation (Type-B): Tuition FREE, Accommodation FREE, No stipend\n"
                elif scholarship_pref in ["Type-C", "Type-D"]:
                    tuition_after_scholarship = 0
                    accommodation_after_scholarship = accommodation_per_year
                    context += f"  Cost Calculation ({scholarship_pref}): Tuition FREE, Accommodation PAID ({accommodation_per_year} RMB/year)\n"
                elif scholarship_pref == "Partial-Low":
                    scholarship_amount = 5000  # Maximum reduction
                    total_fees = tuition_per_year + accommodation_per_year
                    remaining = max(0, total_fees - scholarship_amount)
                    tuition_after_scholarship = tuition_per_year * (remaining / total_fees) if total_fees > 0 else tuition_per_year
                    accommodation_after_scholarship = accommodation_per_year * (remaining / total_fees) if total_fees > 0 else accommodation_per_year
                    context += f"  Cost Calculation (Partial-Low): Partial scholarship (<5000 CNY/year reduction)\n"
                    context += f"  Original: Tuition {tuition_per_year} + Accommodation {accommodation_per_year} = {total_fees} RMB/year\n"
                    context += f"  After Scholarship: {remaining} RMB/year (Tuition: {tuition_after_scholarship:.0f}, Accommodation: {accommodation_after_scholarship:.0f})\n"
                elif scholarship_pref == "Partial-Mid":
                    scholarship_amount = 7500  # Mid-range reduction
                    total_fees = tuition_per_year + accommodation_per_year
                    remaining = max(0, total_fees - scholarship_amount)
                    tuition_after_scholarship = tuition_per_year * (remaining / total_fees) if total_fees > 0 else tuition_per_year
                    accommodation_after_scholarship = accommodation_per_year * (remaining / total_fees) if total_fees > 0 else accommodation_per_year
                    context += f"  Cost Calculation (Partial-Mid): Partial scholarship (5100-10000 CNY/year reduction)\n"
                    context += f"  Original: Tuition {tuition_per_year} + Accommodation {accommodation_per_year} = {total_fees} RMB/year\n"
                    context += f"  After Scholarship: {remaining} RMB/year (Tuition: {tuition_after_scholarship:.0f}, Accommodation: {accommodation_after_scholarship:.0f})\n"
                elif scholarship_pref == "Partial-High":
                    scholarship_amount = 12500  # High-range reduction
                    total_fees = tuition_per_year + accommodation_per_year
                    remaining = max(0, total_fees - scholarship_amount)
                    tuition_after_scholarship = tuition_per_year * (remaining / total_fees) if total_fees > 0 else tuition_per_year
                    accommodation_after_scholarship = accommodation_per_year * (remaining / total_fees) if total_fees > 0 else accommodation_per_year
                    context += f"  Cost Calculation (Partial-High): Partial scholarship (10000-15000 CNY/year reduction)\n"
                    context += f"  Original: Tuition {tuition_per_year} + Accommodation {accommodation_per_year} = {total_fees} RMB/year\n"
                    context += f"  After Scholarship: {remaining} RMB/year (Tuition: {tuition_after_scholarship:.0f}, Accommodation: {accommodation_after_scholarship:.0f})\n"
                else:
                    # Self-Paid or None
                    tuition_after_scholarship = tuition_per_year
                    accommodation_after_scholarship = accommodation_per_year
                    context += f"  Cost Calculation (Self-Paid): Full tuition and accommodation costs apply\n"
                
                # Total costs for program duration
                total_tuition = tuition_after_scholarship * duration_years
                total_accommodation = accommodation_after_scholarship * duration_years
                insurance_total = 800 * duration_years  # 800 RMB/year
                visa_extension_total = 400 * duration_years  # 400 RMB/year
                medical_checkup = 460  # One-time
                application_fee = intake.application_fee or 0  # One-time
                
                total_program_cost = total_tuition + total_accommodation + insurance_total + visa_extension_total + medical_checkup + application_fee
                
                context += f"  Program Duration: {duration_years} years\n"
                context += f"  EXACT COSTS WITH SCHOLARSHIP APPLIED:\n"
                context += f"    Tuition ({duration_years} years): {total_tuition:.0f} RMB\n"
                context += f"    Accommodation ({duration_years} years): {total_accommodation:.0f} RMB\n"
                context += f"    Medical Insurance ({duration_years} years): {insurance_total} RMB\n"
                context += f"    Visa Extension Fee ({duration_years} years): {visa_extension_total} RMB\n"
                context += f"    Arrival Medical Checkup (one-time): {medical_checkup} RMB\n"
                context += f"    Application Fee (one-time, NON-REFUNDABLE): {application_fee} RMB\n"
                context += f"    TOTAL PROGRAM COST: {total_program_cost:.0f} RMB\n"
                
                context += f"  Application Fee: {application_fee} RMB (NON-REFUNDABLE)\n"
                context += f"  Application Fee Paid: {'Yes' if app.application_fee_paid else 'No'}\n"
                # Payment information
                payment_required = app.payment_fee_required or 0
                payment_paid = app.payment_fee_paid or 0
                payment_due = app.payment_fee_due or 0
                context += f"  Payment Required: {payment_required} RMB\n"
                context += f"  Payment Paid: {payment_paid} RMB\n"
                context += f"  Payment Due: {payment_due} RMB\n"
                if payment_due > 0:
                    context += f"  ⚠️ WARNING: Outstanding payment of {payment_due} RMB for this application!\n"
                context += f"  Status: {app.status.value if app.status else 'draft'}\n"
                if app.submitted_at:
                    context += f"  Submitted: {app.submitted_at.strftime('%Y-%m-%d')}\n"
                if app.result:
                    context += f"  Result: {app.result}\n"
        
        context += "\nIMPORTANT REMINDERS:\n"
        context += "- Each application has a NON-REFUNDABLE application fee. Remind student when they want to apply to additional programs.\n"
        context += "- If payment_fee_due > 0 for any application, ALWAYS warn the student about outstanding payments.\n"
        context += "- Encourage students to complete payments promptly to avoid delays in application processing.\n"
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
        
        from datetime import timezone
        now = datetime.now(timezone.utc)
        
        # Handle timezone-aware and timezone-naive datetimes
        if intake.application_deadline.tzinfo is None:
            deadline = intake.application_deadline.replace(tzinfo=timezone.utc)
        else:
            deadline = intake.application_deadline
        
        delta = deadline - now
        return max(0, delta.days)
    
    def _get_program_context_for_application(self, application: Application) -> str:
        """Get program information for a specific application"""
        if not application.program_intake:
            return "Program intake not found for this application."
        
        intake = application.program_intake
        return self.db_service.format_program_intake_info(intake)
    
    def _get_document_status_for_application(self, application: Application) -> str:
        """Get document status for a specific application"""
        if not application.program_intake or not application.program_intake.documents_required:
            return "Document requirements not specified for this program."
        
        intake = application.program_intake
        required_docs = [doc.strip() for doc in intake.documents_required.split(',')]
        
        # Check uploaded documents
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
        status += f"Required: {', '.join(required_docs)}\n"
        
        return status
    
    def _get_missing_documents_for_application(self, application: Application) -> List[str]:
        """Get missing documents for a specific application"""
        if not application.program_intake or not application.program_intake.documents_required:
            return []
        
        intake = application.program_intake
        required_docs = [doc.strip().lower() for doc in intake.documents_required.split(',')]
        missing = []
        
        # Simple matching
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
    
    def _get_days_to_deadline_for_application(self, application: Application) -> Optional[int]:
        """Calculate days remaining until application deadline for a specific application"""
        if not application.program_intake or not application.program_intake.application_deadline:
            return None
        
        intake = application.program_intake
        from datetime import timezone
        now = datetime.now(timezone.utc)
        
        # Handle timezone-aware and timezone-naive datetimes
        if intake.application_deadline.tzinfo is None:
            deadline = intake.application_deadline.replace(tzinfo=timezone.utc)
        else:
            deadline = intake.application_deadline
        
        delta = deadline - now
        return max(0, delta.days)

