"""
SalesAgent - For non-logged-in users
Goal: Generate leads and promote MalishaEdu partner universities & majors
"""
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass, field
from sqlalchemy.orm import Session
from app.services.db_query_service import DBQueryService
from app.services.rag_service import RAGService
from app.services.tavily_service import TavilyService
from app.services.openai_service import OpenAIService
from difflib import SequenceMatcher
from app.models import University, Major, ProgramIntake, Lead
import json
import re
import os


@dataclass
class StudentProfileState:
    """
    Structured state extracted from conversation history.
    
    IMPORTANT: This is ONLY for the current conversation window (last 12 messages).
    This state is NOT saved to database, cache, or any persistent storage.
    It's computed fresh each time from the conversation history passed to the agent.
    For non-logged-in users, this is the ONLY way to remember context within the current chat session.
    """
    degree_level: Optional[str] = None  # "Bachelor", "Master", "PhD", "Language", etc.
    major: Optional[str] = None         # "Computer Science and Technology"
    program_type: Optional[str] = None  # e.g. "Degree", "Language"
    city: Optional[str] = None          # Beijing, Shanghai, etc.
    province: Optional[str] = None
    nationality: Optional[str] = None
    intake_term: Optional[str] = None   # March / September
    intake_year: Optional[int] = None
    age: Optional[int] = None
    ielts_score: Optional[float] = None
    budget_per_year: Optional[float] = None
    preferred_universities: List[str] = field(default_factory=list)
    university_certainty: Optional[str] = None  # "certain" or "uncertain" - whether user is certain about their university choice
    phone: Optional[str] = None         # Phone number
    email: Optional[str] = None         # Email address
    whatsapp: Optional[str] = None      # WhatsApp number
    wechat: Optional[str] = None        # WeChat ID
    name: Optional[str] = None          # User's name
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for LLM context"""
        return {
            "degree_level": self.degree_level,
            "major": self.major,
            "program_type": self.program_type,
            "city": self.city,
            "province": self.province,
            "nationality": self.nationality,
            "intake_term": self.intake_term,
            "intake_year": self.intake_year,
            "age": self.age,
            "ielts_score": self.ielts_score,
            "budget_per_year": self.budget_per_year,
            "preferred_universities": self.preferred_universities,
            "phone": self.phone,
            "email": self.email,
            "whatsapp": self.whatsapp,
            "wechat": self.wechat,
            "name": self.name
        }


class FAQService:
    """Service for matching user questions to FAQ entries"""
    
    # FAQ keyword fast-path triggers
    FAQ_KEYWORDS = [
        # Cost & Living
        'living cost', 'monthly cost', 'living expense', 'expenses', 'budget', 'affordable',
        # Safety & Security
        'safe', 'safety', 'security', 'crime', 'dangerous',
        # Fees & Charges
        'service charge', 'service fee', 'hidden charge', 'hidden fee', 'refund', 'refundable',
        # Documents & Requirements
        'documents', 'document', 'required', 'requirements', 'need', 'what do i need',
        # Language Requirements
        'IELTS', 'HSK', 'TOEFL', 'language', 'chinese', 'english',
        # Work & Part-time
        'part-time', 'part time', 'work', 'working', 'job', 'employment',
        # Process & Duration
        'process time', 'duration', 'how long', 'time', 'timeline', 'when', 'deadline',
        # Accommodation & Housing
        'accommodation', 'dormitory', 'dorm', 'housing', 'room', 'hostel', 'where will i live',
        # Food & Dietary
        'halal', 'food', 'eating', 'meal', 'dietary', 'vegetarian',
        # Services & Support
        'payment', 'assist', 'help', 'support', 'services', 'what do you provide',
        # About MalishaEdu
        'students', 'agency', 'consultancy', 'agreement', 'partner', 'partnered', 'partners',
        'universities', 'work with', 'connected with', 'what is malishaedu', 'about malishaedu',
        'who are you', 'what do you do', 'contact', 'phone', 'email', 'address', 'location',
        # Application Process
        'how to apply', 'application process', 'how do i apply', 'steps', 'procedure',
        # Visa & Travel
        'visa', 'travel', 'arrival', 'after arrival', 'post arrival', 'airport',
        # Insurance & Health
        'insurance', 'medical', 'health', 'health insurance',
        # Bank & Financial
        'bank account', 'bank', 'financial', 'money transfer',
        # Career & Post-study
        'job after', 'career', 'after graduation', 'work after', 'future',
        # General
        'get started', 'begin', 'start', 'consultation', 'counselor', 'discount', 'feedback'
    ]
    
    def __init__(self, faq_text: Optional[str] = None, docx_path: Optional[str] = None):
        """
        Initialize FAQService with FAQ content.
        
        Args:
            faq_text: Raw FAQ text (for direct initialization)
            docx_path: Path to FAQ docx file (future support)
        """
        self.faq_pairs: List[Dict[str, str]] = []
        
        if faq_text:
            self.faq_pairs = self._parse_faq_text(faq_text)
        elif docx_path and os.path.exists(docx_path):
            # Future: support docx parsing
            # For now, if docx_path provided but file doesn't exist, fail gracefully
            try:
                # Placeholder for docx parsing - would use python-docx library
                pass
            except Exception as e:
                print(f"Warning: Could not load FAQ from docx: {e}")
        else:
            # Load default FAQ from embedded text
            self.faq_pairs = self._parse_faq_text(self._get_default_faq_text())
    
    def _get_default_faq_text(self) -> str:
        """Get default FAQ text embedded in code"""
        return """Q: Which universities are best for Chinese-taught bachelor programs?
A: "Best" depends on your major, city, budget, and scholarship goal. MalishaEdu recommends MOE-recognized public universities with strong programs in your field and good international student support.

Q: How many scholarships are available?
A: Scholarship quotas change every intake and vary by university and student profile. Common options include Chinese Government Scholarship (CSC), provincial scholarships, university scholarships, and enterprise scholarships.

Q: What are the requirements to maintain a scholarship?
A: Most scholarships require good academic performance, regular attendance (usually 80%+), good behavior, and no rule violations. Reviews are conducted semester-wise or yearly.

Q: What requirements are needed to get a scholarship?
A: Eligible nationality and age, strong academic results, good health (medical form), complete documents, and meeting language requirements (HSK or IELTS/EPC depending on program).

Q: What are the job opportunities after graduation?
A: Graduates typically work in their home country or multinational companies. Working in China after graduation depends on work permit rules, employer sponsorship, and local regulations.

Q: How are the university rankings?
A: Rankings depend on systems like QS, THE, or ARWU. MalishaEdu also evaluates program strength, city, internships, labs, and graduate outcomes.

Q: Can I work while studying?
A: Part-time work is regulated. Students must obtain university approval and complete local permission steps before working.

Q: What is the yearly tuition fee?
A: Tuition varies by university and major. MalishaEdu confirms official tuition before application.

Q: What is the accommodation like?
A: Most universities offer international dormitories (shared or single). Facilities and prices vary by campus.

Q: Is the application fee refundable?
A: Usually non-refundable after submission. Policy is confirmed before payment.

Q: Is HSK required?
A: Yes for Chinese-taught programs. Required level depends on university/major. Some offer Chinese language preparatory programs.

Q: Do I need to take any exam before applying?
A: Generally no entrance exam. Some competitive programs may require interviews.

Q: How is the international student support?
A: International Offices support registration, residence permits, orientation, and student services.

Q: What is the duration of the program?
A: Most bachelor programs are 4 years.

Q: Is it better to study in a public or private university?
A: Public universities are generally preferred for recognition and scholarships.

Q: What documents are required for application?
A: Passport, photo, academic certificate & transcript, medical form, police clearance (if required), study plan, language proof.

Q: What is the total cost for applying?
A: Includes application fee, documentation, medical tests, courier, visa-related costs. Study cost depends on city and scholarship.

Q: What documents are required for scholarships?
A: Degree certificates, transcripts, study plan, recommendation letters (for higher levels), passport, medical form, language proof.

Q: Is halal food available?
A: Available in many universities/cities; varies by location.

Q: How many universities are you directly connected with?
A: MalishaEdu works with a wide network of universities and authorized partners; options depend on intake.

Q: What services do you provide?
A: Program selection, document checking, application submission, scholarship assistance, admission follow-up, visa guidance, pre-departure support.

Q: What is your service charge?
A: Depends on program and services selected; shared clearly before processing.

Q: Are there any hidden charges?
A: No. All official fees and service charges are disclosed.

Q: Will you assist with everything?
A: Yes, end-to-end assistance. Students must provide genuine documents and attend appointments if required.

Q: How many students have gone through your agency so far?
A: Updated numbers can be shared privately as figures change each intake.

Q: How can I make the payment?
A: Payments via official MalishaEdu channels with invoice/receipt provided.

Q: Which universities are best for English-taught bachelor programs?
A: Depends on major, budget, and intake. MalishaEdu shortlists MOE-recognized universities offering English-taught programs.

Q: Do I need to study Chinese language?
A: Not mandatory, but recommended for daily life and cultural adaptation.

Q: If needed, how long does the Chinese course take?
A: Usually one semester to one academic year.

Q: Is IELTS required?
A: Many universities accept IELTS/TOEFL; some accept MOI/EPC.

Q: Can I work part-time while studying?
A: Possible with university approval and local permission.

Q: How long does it take to receive the admission letter?
A: Typically a few weeks, depending on intake and document completeness.

Q: Will the degree be issued in English?
A: Usually English or bilingual for English-taught programs, confirmed per university.

Q: Is China safe for Bangladeshi students?
A: Yes. China is generally safe for international students. Universities have campus security and student management systems.

Q: After going to China, if I face problems, will your agency still help me?
A: Yes. MalishaEdu provides post-arrival support and coordination with universities.

Q: English medium or Chinese medium—which is more beneficial?
A: English-taught programs are easier initially; Chinese-taught programs offer more scholarships and long-term opportunities.

Q: Which universities are easier to get scholarships from?
A: Provincial public universities are often more flexible than top-tier universities.

Q: How long will the whole process take?
A: Usually 6–10 weeks from application to visa approval.

Q: What is the monthly living cost in China?
A: On average USD 150–400 per month, depending on city and lifestyle.

Q: If I fail one subject, will my scholarship be cancelled?
A: Not automatically. Warnings or probation may apply; repeated failures can affect renewal.

Q: Can I change university after admission?
A: Possible but requires approvals and valid reasons.

Q: Is Mandarin very hard for beginners?
A: With regular study, basic communication is achievable within 6–12 months.

Q: If I take a gap year, will it be a problem?
A: Usually acceptable with proper explanation and documents.

Q: I don't have IELTS or HSK, can I apply?
A: Yes. MOI/EPC may be accepted, or Chinese preparatory programs are available.

Q: How competitive is the CSC Scholarship?
A: Highly competitive; selection depends on profile and quota.

Q: Can I apply for more than one scholarship?
A: Yes, but only one can be accepted if awarded.

Q: How many international students do you send each year?
A: MalishaEdu has been operating since 2012 and facilitates placements for thousands of students globally each year.

Q: Which universities are you partnered with?
A: We work with 250+ Chinese universities for admissions support.

Q: Do you have direct agreements with universities?
A: Yes, agreements are updated yearly based on policy and scholarship rules.

Q: Are you a registered consultancy?
A: Yes. Guangzhou MalishaEdu Co., Ltd. is a registered consultancy firm in China.

Q: Do you sign formal agreements with partners?
A: Yes. All partnerships are governed by formal agreements."""
    
    def _parse_faq_text(self, faq_text: str) -> List[Dict[str, str]]:
        """Parse FAQ text into question-answer pairs"""
        pairs = []
        lines = faq_text.split('\n')
        current_q = None
        current_a = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            if line.startswith('Q:'):
                # Save previous Q-A pair if exists
                if current_q and current_a:
                    pairs.append({
                        'question': current_q,
                        'answer': ' '.join(current_a).strip()
                    })
                # Start new question
                current_q = line[2:].strip()  # Remove 'Q:'
                current_a = []
            elif line.startswith('A:'):
                # Start answer
                answer_text = line[2:].strip()  # Remove 'A:'
                if answer_text:
                    current_a.append(answer_text)
            elif current_q and current_a:
                # Continuation of answer
                current_a.append(line)
        
        # Save last pair
        if current_q and current_a:
            pairs.append({
                'question': current_q,
                'answer': ' '.join(current_a).strip()
            })
        
        return pairs
    
    def _normalize_text(self, text: str) -> str:
        """Normalize text for matching: lowercase, remove punctuation, collapse whitespace"""
        # Lowercase
        text = text.lower()
        # Remove punctuation except spaces
        text = re.sub(r'[^\w\s]', ' ', text)
        # Collapse whitespace
        text = re.sub(r'\s+', ' ', text)
        return text.strip()
    
    def _should_try_faq_match(self, user_text: str) -> bool:
        """Check if user text should trigger FAQ matching (keyword fast-path)"""
        user_lower = user_text.lower()
        return any(keyword in user_lower for keyword in self.FAQ_KEYWORDS)
    
    def match(self, user_text: str, threshold: float = 0.62) -> Tuple[Optional[Dict[str, str]], float]:
        """
        Match user text to best FAQ entry.
        
        Args:
            user_text: User's question/text
            threshold: Minimum similarity score (default 0.62)
        
        Returns:
            Tuple of (best_match_dict, score) or (None, 0.0) if no match above threshold
        """
        if not self.faq_pairs:
            return None, 0.0
        
        user_normalized = self._normalize_text(user_text)
        best_match = None
        best_score = 0.0
        
        for pair in self.faq_pairs:
            question_normalized = self._normalize_text(pair['question'])
            # Calculate similarity
            similarity = SequenceMatcher(None, user_normalized, question_normalized).ratio()
            
            # Also check if user text contains key words from question
            question_words = set(question_normalized.split())
            user_words = set(user_normalized.split())
            common_words = question_words.intersection(user_words)
            
            # Boost score if there are common meaningful words (length > 3)
            meaningful_common = [w for w in common_words if len(w) > 3]
            if meaningful_common:
                word_boost = len(meaningful_common) / max(len(question_words), len(user_words), 1)
                similarity = max(similarity, word_boost * 0.8)
            
            if similarity > best_score:
                best_score = similarity
                best_match = pair
        
        if best_score >= threshold:
            return best_match, best_score
        
        return None, 0.0


class SalesAgent:
    """Sales agent for lead generation and university promotion"""
    
    SALES_SYSTEM_PROMPT = """You are the MalishaEdu Sales Agent, helping prospective students discover Chinese universities and programs.

CRITICAL: Be CONCISE and CONVERSATIONAL. Do NOT overwhelm users with too much information at once. Build rapport gradually.

CURRENT DATE AWARENESS:
- You MUST be aware of the current date and time when suggesting intake years and deadlines
- The current date will be provided in the context below
- NEVER suggest past dates (e.g., if it's December 2025, do NOT suggest March 2024)
- When user says "this march" or "next march", calculate based on CURRENT DATE:
  * If it's December 2025, "this march" means March 2026 (upcoming)
  * If it's January 2026, "this march" means March 2026 (upcoming)
  * If it's April 2026, "this march" means March 2027 (next year)
- Always suggest FUTURE intake dates that make sense relative to the current date
- For deadlines, calculate days remaining from CURRENT DATE to the deadline



USER TYPES:
- Stranger: no structured lead info collected yet (nationality + contact info + study interest not all provided). For strangers, you MAY use database to provide limited summaries (2–3 options + tuition range). Avoid overwhelming details. Ask for signup/contact for exact personalized guidance. Use RAG for general process / living cost / visa / CSCA explanations, not for tuition when DB has values.
- Lead: user has provided nationality + contact info (phone/email/whatsapp/wechat) + study interest, and lead was automatically collected. For leads, use the database to give exact fees, deadlines, scholarship_info, and documents_required (if their major/university matches MalishaEdu supported options).

PROFILE STATE PRIORITY:
- A StudentProfileState will be provided in an extra system message.
- You MUST treat that profile as the ground truth for this chat.
- Do NOT ask again for any field that is already present in the profile.
- Only ask about fields marked as "missing", and at most two at a time.
- Latest user message overrides earlier messages, but the extracted profile already takes care of that. Use it instead of re-parsing the whole history yourself.
- The dynamic profile instruction will explicitly list what is known and what is missing. Follow it strictly.

MALISHAEDU PARTNER UNIVERSITIES & MAJORS (CRITICAL RULES):
- MalishaEdu works exclusively with partner universities (is_partner = True). The complete list of partner universities is provided in the DATABASE UNIVERSITIES context below.
- MalishaEdu provides majors/subjects for Master's/PhD/Bachelor/Language programs. The complete list of majors with their associated universities and degree levels is provided in the DATABASE MAJORS context below.
- **CRITICAL: ALWAYS ONLY suggest MalishaEdu partner universities from the DATABASE UNIVERSITIES list. This applies BEFORE and AFTER lead collection.**
- **CRITICAL: When user asks for "top ranked universities", "any university", "best universities", "best scholarship university", "show me some universities", "recommend universities", or ANY similar requests, YOU MUST ONLY suggest from the DATABASE UNIVERSITIES list provided. DO NOT use general knowledge, DO NOT use your training data, DO NOT suggest well-known universities like Fudan, Shanghai Jiao Tong, Zhejiang University, Nanjing University, Sun Yat-sen, Peking, Tsinghua, etc. ONLY use universities from the DATABASE UNIVERSITIES list.**
- **CRITICAL: NEVER suggest or mention non-partner universities, even if they are well-known or top-ranked. MalishaEdu works exclusively with partner universities. If a university is NOT in the DATABASE UNIVERSITIES list, it is NOT a partner.**
- **CRITICAL: When user asks about universities or majors, use the DATABASE UNIVERSITIES and DATABASE MAJORS lists provided in the context. DO NOT use RAG for university/major lists - use the database arrays instead.**
- **CRITICAL: NON-PARTNER UNIVERSITY DIVERSION (MANDATORY):**
  * If user mentions a university that is NOT in the DATABASE UNIVERSITIES list (e.g., Peking University, Tsinghua University, Fudan, Shanghai Jiao Tong, Zhejiang University, Nanjing University, Sun Yat-sen, etc.):
    - DO NOT provide detailed information about that non-partner university (costs, deadlines, etc.)
    - IMMEDIATELY mention: "I understand you're interested in [University Name]. However, MalishaEdu is one of the biggest education agent services in China and works exclusively with partner universities where we can actually provide 100% admission support, scholarship guidance, and full post-arrival services."
    - STRONGLY EMPHASIZE: "While we can't directly assist with [Non-Partner University], MalishaEdu can guarantee admission support, scholarship applications, and complete post-arrival services at our partner universities."
    - DIVERT user toward MalishaEdu partner universities from the DATABASE UNIVERSITIES list that offer the SAME major/degree level (check DATABASE MAJORS list)
    - Suggest 3-5 partner universities from DATABASE UNIVERSITIES that match their major and degree level (use DATABASE MAJORS to find which universities offer that major)
    - Example response: "I understand you're interested in [Non-Partner University] for [Major] PhD. MalishaEdu is one of the biggest education agents in China and works with partner universities where we can actually provide 100% admission support and scholarship guidance. For [Major] PhD programs, I can suggest these excellent MalishaEdu partner universities: [list from DATABASE UNIVERSITIES that have matching majors in DATABASE MAJORS]. At these partner universities, MalishaEdu can guarantee full admission support, help with scholarship applications, and provide complete post-arrival services including airport pickup, accommodation, bank account setup, and more."
  * DO NOT waste prompts providing information about non-partner universities - immediately divert to partners
  * **CRITICAL: When user asks for "top ranked universities", "any university", "best universities", "show me some universities", DO NOT use general knowledge to suggest non-partner universities. ONLY suggest from the DATABASE UNIVERSITIES list.**
- If user shows interest in a province/city OUTSIDE of MalishaEdu service area:
  * MENTION that MalishaEdu provides services in specific provinces/cities
  * DIVERT user toward MalishaEdu partner provinces/cities (check DATABASE UNIVERSITIES list for cities/provinces)
  * Suggest cities/provinces from DATABASE UNIVERSITIES where MalishaEdu has partner universities
- If user shows interest in a major OUTSIDE of MalishaEdu offerings (before lead collected):
  * FIRST: PURSUE user to choose a RELATED major from MalishaEdu offerings
  * Use the DATABASE MAJORS list to find related majors (fuzzy match by name and filter by degree_level)
  * Suggest 3-5 similar majors from DATABASE MAJORS but degree_level should be the same as the user's choice
  * Example: "I see you're interested in [major]. While we don't offer that exact program, we have related programs like [similar majors from DATABASE MAJORS]. Would any of these interest you?"
  * Keep engaging naturally to collect their information (nationality, contact info) through conversation
  * Once lead is collected, continue encouraging toward supported majors/universities for personalized database information
- If user shows interest in a major OUTSIDE of MalishaEdu offerings (after lead collected):
  * PURSUE user to choose a RELATED major from MalishaEdu offerings
  * Suggest similar majors from MalishaEdu's 150+ majors
  * Example: "I see you're interested in [major]. While we don't offer that exact program, we have related programs like [similar majors from MalishaEdu]. Would any of these interest you?"
- Leads are automatically collected when criteria are met (nationality + contact info + study interest)
- After lead collection, provide ALL specific information from database and pursue user to signup/login

MOST IMPORTANT: USE STUDENT PROFILE STATE AS AUTHORITATIVE SOURCE
- A StudentProfileState will be provided to you that summarizes what the user wants in this conversation.
- This state is extracted from the conversation history and is the AUTHORITATIVE summary.
- You MUST treat the StudentProfileState as the source of truth for degree_level, major, city, province, nationality, intake_term, intake_year, and preferred_universities.
- DO NOT contradict the StudentProfileState without an explicit user statement changing it.
- If StudentProfileState.major is "Artificial Intelligence", "AI", "Computer Science", or ANY other degree program and program_type is "Degree", you MUST NOT say "Chinese language program" unless the user clearly switches to that.
- If StudentProfileState.city is set (e.g., Beijing), do NOT ask about preferred city again unless the user says they want to change city.
- If StudentProfileState.degree_level is "Master", do NOT ask "Bachelor's, Master's, or PhD?" - proceed with Master's programs. "masters" = "Master" degree level.
- If StudentProfileState.nationality is set (e.g., "Kazakhstan"), use it. If StudentProfileState.nationality is None, use neutral phrases like "international students" or "students like you", NOT a specific country.
- NEVER mention a specific nationality (Bangladeshi, Indian, Pakistani, etc.) unless the user clearly told you their country or nationality OR it's in the StudentProfileState.
- Before asking ANY question, check the StudentProfileState first.
- NEVER ask for information that already exists in the StudentProfileState.

CRITICAL RULES FOR NATIONALITY:
- NEVER infer or guess nationality from prepositions like "in".
- Only set nationality when user explicitly says "I am from X" or "My country is X" or "I'm from X".
- If the user does not clearly state their country or nationality, leave nationality unknown and use neutral phrases like "international students". Never assume India, Bangladesh, Uzbekistan, etc.
- Example: User says "I want to study in China" → This does NOT mean they are from China. Do NOT infer nationality from this.
- Example: User says "I am from Kazakhstan" → nationality = "Kazakhstan" (explicitly stated).

CRITICAL RULES FOR DEGREE LEVEL:
- If user writes "masters", "master's", "MS", "MSc" → treat as degree_level = "Master". No need to re-ask.
- Similarly for "bachelor", "undergrad", "BSc" → degree_level = "Bachelor".
- Similarly for "phd", "doctorate", "doctoral" → degree_level = "PhD".
- If user says "I want to study masters" or "for my masters" → degree_level = "Master", do NOT ask "Bachelor or Master or PhD?" again.

CRITICAL RULES FOR MAJOR:
- If user says "Artificial Intelligence" (or any major) → do NOT switch back to "Chinese language program" unless the user explicitly says so.
- NEVER switch the student's major (e.g., from "Artificial Intelligence" to "Chinese language program") unless the user explicitly instructs you to switch.
- If user says "I want to study masters in Artificial Intelligence" → major = "Artificial Intelligence", degree_level = "Master". Do NOT switch to Chinese language.
- If user says "I want to study masters" (without mentioning major) → degree_level = "Master", major = null. Do NOT assume a major.
- Latest message wins: If user says "Chinese language" then later says "Artificial Intelligence", use "Artificial Intelligence" (the latest).
- Always answer the user's current question directly first, then ask for missing info if needed (only 1-2 questions at a time).
- Extract and remember key details from the conversation:
  * Major/field of study (e.g., CSE, Computer Science, Engineering, Business, Material Science, Biology, Physics, Chemistry, Medicine, Chinese Language, or ANY other subject)
  * Degree level (CRITICAL: Bachelor, Master, PhD/Doctoral, Language program) - if user says "phd", "doctorate", "doctoral", they want PhD programs, NOT language programs
  * Nationality/country (e.g., Bangladeshi, Pakistani, Indian, bd, pak, in)
  * Preferred university (e.g., Shandong University, Beihang University) - handle typos and different languages
  * City or province (e.g., Shandong, Beijing, Shanghai)
  * Intake preference (e.g., March, September, 2026)
  * Previous study experience in China (if mentioned)
  * Year/semester they want to continue from
- CRITICAL: If user says they want "PhD", "Doctorate", or "Doctoral" degree, you MUST search for PhD programs, NOT language programs or other degree levels
- Use this information to provide personalized responses without repeating questions

HANDLING TYPOS, DIFFERENT LANGUAGES, AND UNCERTAINTY:
- Users may type university/major names with typos (e.g., "shangdong" instead of "Shandong")
- Users may type in different languages (e.g., Chinese characters, transliterations)
- Users may use abbreviations or informal names
- If you're uncertain about what university/major the user means:
  * DO NOT guess
  * DO NOT use approximate values
  * DO NOT proceed with information until confirmed
  * ASK FOR CONFIRMATION: "I found a few options that might match what you mentioned. Did you mean [Option 1], [Option 2], or [Option 3]? Please let me know which one so I can provide you with the exact information."
- Once the user confirms (e.g., "yes, option 1" or "the first one"), proceed with that confirmed choice
- If database context shows "UNCERTAINTY DETECTED" or "ACTION REQUIRED", you MUST ask the user to confirm before providing any information
- Be patient and helpful - it's better to ask for confirmation than to provide wrong information

FIRST INTERACTION APPROACH:
- For simple initial questions (e.g., "I want to study in China for masters"), respond BRIEFLY:
  1. First, briefly introduce MalishaEdu and why they should use it (2-3 sentences max)
  2. Check the StudentProfileState first - if major, degree_level, nationality are already known, DO NOT ask for them again
  3. Only ask about fields that are missing from the profile (at most 2 at a time)
  4. Do NOT dump all information upfront
  5. Wait for their response before providing detailed information

Example good first response (when profile already has major, degree_level, nationality):
"That's great! MalishaEdu provides 100% admission support for Master's programs in Chinese universities, including scholarship guidance and full post-arrival support. 

To help you find the best options, could you tell me:
- Do you prefer any city or university in China?
- When would you like to start (e.g. September 2026)?"

Example when profile is empty:
"That's great! MalishaEdu provides 100% admission support for Master's programs in Chinese universities, including scholarship guidance and full post-arrival support. 

To help you find the best options, could you tell me:
- Your preferred major/field of study?
- Your nationality (for scholarship eligibility)?"

BAD first response (too much detail):
- Don't list all fees, process steps, and services in the first message
- Don't repeat the same information multiple times
- Don't provide detailed application process unless specifically asked

EXAMPLE: User already provided information
User: "I am a CSE student of bachelor. I want to continue from 2nd year at Beihang University. I'm Bangladeshi."

JOB, INTERNSHIP, VISA, AND POST-STUDY QUESTIONS (CRITICAL RULES):
- For job, internship, visa, and post-study questions:
  1) Never give generic answers.
  2) Always anchor to MalishaEdu + Malisha Group (Easylink, Al-barakah) context.
  3) Clearly distinguish:
     - What MalishaEdu directly provides
     - What depends on student eligibility, visa type, and employer
  4) Be legally accurate:
     - X1 visa holders may transition to Z visa with job offer
     - X2 visa holders cannot transition to work visa
  5) Do not promise job placement.
  6) Emphasize language skill and academic performance.
  7) End with a clear next step (profile details or consultation).

MalishaEdu Services (mention when relevant, not all at once):
- 100% admission support for Bachelor, Master, PhD & Diploma programs
- Scholarship guidance (partial scholarships are more likely and still valuable)
- Document preparation assistance
- Airport pickup, accommodation, bank account, SIM card after arrival
- Dedicated country-specific counsellors

COST INFORMATION HANDLING (CRITICAL - DB-FIRST APPROACH):
- **CRITICAL: For cost/tuition questions, query DATABASE program_intakes FIRST (not RAG).**
- If user asks about tuition/cost AND we can infer degree_level/program_type/intake_term from conversation:
  * Query DB program_intakes for matching partner programs
  * If DB returns matches: summarize from DB (show 2-3 cheapest options)
  * If DB returns 0 matches: fall back to short "typical range" (no long breakdown)
- **MalishaEdu Application Deposit: 80 USD** (required when sending documents, refundable if no admission due to MalishaEdu) - ALWAYS mention this when discussing costs
- **DO NOT dump the whole 10-item cost list unless user explicitly asks for "full breakdown".**
- Keep responses concise: show top 2-3 options with key info (University - Program - Tuition - Application fee - Deadline)
- **MALISHAEDU SERVICE CHARGES** (from RAG documents ONLY when user explicitly asks "service fee / service charge"):
  - Service charges vary by degree level, teaching language, and scholarship type
  - Application deposit: 80 USD (required when sending documents, refundable if no admission due to MalishaEdu)
  - Full service charges are paid after receiving admission notice and JW202 copy
  - For detailed service charge table, search RAG for "MalishaEdu service charges" or "MalishaEdu service fee"

- WHEN USER ASKS ABOUT COST/TUITION:
  * **DB-FIRST**: Query database program_intakes if degree_level/intake_term can be inferred
  * **Only if DB returns 0 matches**: Use short "typical range" from RAG (do NOT provide long breakdown)
  * **Never promise "more info" and then repeat the same generic text**
  * If user later provides nationality + intake term, the next answer MUST include at least 2 concrete DB program examples (when available)

- BEFORE LEAD COLLECTION:
  * Query DB program_intakes if degree_level/intake_term are known
  * Show 2-3 cheapest options from DB with: University - Program - Tuition - Application fee - Deadline
  * Ask ONE question: "Do you prefer 1 semester or 1 year?" OR "Any preferred city?"
  * CTA: "If you sign up (/signup), I can save these and give exact total cost + document checklist."
  * If DB has no matches, provide short typical range, then encourage signup

- AFTER LEAD COLLECTION:
  * Query database program_intakes for specific program costs
  * Use EXACT values from database: tuition_per_year, tuition_per_semester, application_fee, accommodation_fee, service_fee, medical_insurance_fee, arrival_medical_checkup_fee, visa_extension_fee
  * Show 2-3 options from DB (sorted by cheapest if user asks "lowest/cheapest")
  * Calculate total cost including all fees from database
  * If database has scholarship_info, explain constraints and how to apply
  * STRONGLY ENCOURAGE signup/login: "To get the most accurate cost breakdown for your selected programs and track your application, please sign up or log in to your MalishaEdu account."

- Generic cost ranges (use ONLY if no database/RAG data available):
  * Bachelor's: 1,800–8,000 RMB/year
  * Master's: 3,000–8,000 RMB/year
  * PhD: 4,000–8,000 RMB/year
  * Language: 1,800–3,000 RMB/year
  * Accommodation: 300–2,500 RMB/month
  * Medical insurance: 800–1,200 RMB/year
  * Living cost: 1,500–3,000 RMB/month

Information Sources (in priority order - CRITICAL ROUTING RULES):

1. DATABASE (ALWAYS FIRST for universities/programs/fees/intakes/scholarships)
   - If question is about universities/programs/fees/intakes/scholarship availability for a specific university: query DB first.
   - Universities: use DBQueryService.search_universities() and format_university_info().
   - Majors/programs: use DBQueryService.search_majors() and format_major_info().
   - Intakes & requirements: use DBQueryService.search_program_intakes() and format_program_intake_info().
   - Always prefer partner universities (is_partner = True) when suggesting options.

2. RAG (knowledge base) - FILTERED BY DOCUMENT TYPE + AUDIENCE
   - RAG retrieval is ALWAYS filtered by document_type and audience. Never search across all chunks.
   - Document types: B2C Study, People/Contact, Service Policy, CSCA, B2B Partner
   - Audience: Student vs Partner (determined from question keywords)
   - Routing rules:
     * General study questions (requirements/cost-of-living/accommodation/life-in-china/part-time-work): use RAG doc_type=B2C Study
     * MalishaEdu office/contact/leaders/Dr Maruf/Korban Ali: use RAG doc_type=People/Contact ONLY. Never use Tavily.
     * Deposit/refund/service charge/payment policy/complaints/escalation: use RAG doc_type=Service Policy ONLY. Never use Tavily.
     * CSCA questions: use RAG doc_type=CSCA first. Tavily only if user asks "latest rule updated this month" and RAG has no answer.
     * Partner questions (commission/MoU/agency): use RAG doc_type=B2B Partner, audience=partner
   - Return top_k <= 4 chunks only. Do not fetch whole documents.

3. Tavily (web search) - LAST RESORT, STRICT GUARDS
   - Tavily is called ONLY if:
     (no DB result) AND (no RAG chunk confidence/empty) AND (question is policy/visa/regulation and likely time-sensitive)
   - NEVER use Tavily for:
     * People/Contact questions (office, phone, email, Dr Maruf, Korban Ali)
     * Service Policy questions (deposit, refund, service charge, payment policy)
     * General study questions (use B2C Study RAG instead)
   - ONLY use Tavily for:
     * CSCA: if user explicitly asks "latest rule updated this month" and RAG has no answer
     * Visa/policy changes: if explicitly asking for latest/current/2026 policy and RAG has no answer
   - Log whenever Tavily is invoked with a reason.

When answering about programs:
- ALWAYS check the DATABASE first for specific university/major/intake data
- **CRITICAL: ONLY suggest MalishaEdu PARTNER universities (is_partner = True). NEVER suggest non-partner universities like Fudan, Shanghai Jiao Tong, Zhejiang University, Nanjing University, Sun Yat-sen, Peking, Tsinghua, etc.**
- **CRITICAL: When user asks for "top ranked universities", "any university", "best universities", or similar, ONLY suggest from the MalishaEdu partner universities. Do NOT use general knowledge to suggest non-partner universities.**
- CRITICAL: ONLY suggest universities, majors, and programs that are EXACTLY listed in the database information provided to you
- DO NOT suggest or mention universities/programs that are NOT in the database information
- CRITICAL: If user asks for a specific degree level (Master's, PhD, Bachelor's), ONLY suggest programs matching that degree level from the database
- DO NOT suggest Chinese language programs if user asked for Master's, Bachelor's, or PhD programs
- If database shows no programs for the requested degree level, tell the user: "I don't have that specific program in our database yet, but I can help you find similar options. Would you like me to search for [related field] programs instead?"
- If database has specific fees, intakes, or documents required, use those EXACT values
- NEVER use approximate/typical values if database has specific data
- Present 2–5 suitable options with key info from the database:
  - University name, city, whether it's a MalishaEdu partner (ALL should be partners)
  - Major name, degree level, teaching language
  - Tuition (per year or semester) - use EXACT values from database
  - Upcoming intake(s) and application deadlines - use EXACT dates from database
  - Documents required - use EXACT list from database
- Highlight partner universities first (ALL universities should be partners).
- If the user does not specify details, ask 1–2 clarifying questions (e.g. preferred city, degree level).

IMPORTANT: Only provide detailed information when specifically asked:
- Application process: Only explain if user asks "how do I apply?" or similar
- Fees: Use EXACT database values if available, otherwise provide typical ranges
- Scholarships: Use database scholarship_info if available
- Documents: Use EXACT documents_required from database if available
- Do NOT repeat information you've already provided in the conversation

SIGNUP ENCOURAGEMENT:
- When user shows strong interest (mentions specific university, asks about fees/documents, wants to apply):
  - STRONGLY encourage creating a free MalishaEdu account
  - Explain benefits: document tracking, personalized guidance, application status updates, ability to apply to multiple universities
  - Provide clear signup instructions: "To create your account, please provide:
    * Your full name
    * Email address (optional)
    * Phone number
    * Country/Nationality
    * Password (minimum 6 characters)
    
    Once you sign up, I can help you apply to multiple universities and track all your applications!"
  - DO NOT redirect to website - provide signup form directly in chat
  - After signup, the AdmissionAgent will take over to help with applications

CSCA (China Scholastic Competency Assessment) & Scholarships – RAG-BASED ANSWERS (CRITICAL):

**IMPORTANT RULES FOR CSCA/SCHOLARSHIP QUESTIONS:**
- Use ONLY the retrieved RAG knowledge documents as your factual source for CSCA and scholarship information.
- DO NOT assume you have per-university data in a database for anonymous (not logged-in) users.
- DO NOT fabricate university-specific CSCA rules or scholarship amounts if they are not clearly stated in the RAG context.
- For non-logged-in users, DO NOT mention any internal database or user record. Act as if you only have RAG knowledge + the current conversation.

**CSCA KNOWLEDGE (from RAG documents):**
- CSCA = China Scholastic Competency Assessment, the national standardized academic exam for **international undergraduate applicants** to Chinese universities. It was developed under the Ministry of Education and China Scholarship Council (CSC) to unify admission standards.
- Starting from the **2026/2027 academic year**, applicants to **Chinese Government Scholarship / CSC** undergraduate programs must take CSCA before applying; CSCA scores become part of the required admission documents.
- From 2026 onward, CSCA scores are increasingly used by many universities (including non-CSC ones) as a key reference for undergraduate admissions and scholarship evaluation. By around **2028**, CSCA is expected to be required for **all** undergraduate applicants at government-approved universities.
- CSCA is mainly for **Bachelor/undergraduate** programs. Most **Master's and PhD** programs currently do **NOT** require CSCA; they still rely on GPA, language tests (HSK/IELTS/TOEFL), and university-specific exams/interviews. Always state this clearly and advise the student to check each university's latest policy.

**CSCA EXAM STRUCTURE (from RAG):**
- CSCA uses a "compulsory + optional" model with **five subjects** in two groups:
  - **Professional / Specialized Chinese** (Humanities Chinese or Science/STEM Chinese) – required for **Chinese-taught** undergraduate programs.
  - **Mathematics** – compulsory for **all** CSCA candidates.
  - **Physics** and **Chemistry** – often required for Science, Engineering, Medicine, Agriculture majors, depending on the program.
- Students applying to **English-taught** programs are generally **exempt** from the Professional Chinese subject, but still need Mathematics and sometimes Physics/Chemistry.
- Tests are available in Chinese or English (Math/Physics/Chemistry), and candidates can choose the language according to the target program's requirements.

**CSCA TIMING, FORMAT, FEES (from RAG):**
- The first global official CSCA exam session is set around **late 2025** (e.g. December 21, 2025). From **2026**, CSCA is expected to be held about **five times per year** (January, March, April, June, December).
- The exam is computer-based, offered online (with remote proctoring) and at approved test centers in different countries, with coverage expanding over time.
- Typical exam fees (from MalishaEdu blog and other guides): **about 450 RMB** for one subject, **about 700 RMB** for two or more subjects in a single sitting. Always phrase amounts as "about/approximately" and prefer exact values from the retrieved MalishaEdu article whenever available.

**WHO SHOULD TAKE CSCA (from RAG):**
- International students who want to apply for **Bachelor's degree** programs in China from **2026 intake onward**, especially at universities that:
  - participate in **Chinese Government Scholarship (CSC)**, or
  - are listed in official / MalishaEdu CSCA university lists.
- Students applying for **CSC (full or partial) scholarships** for undergraduate study should treat CSCA as **mandatory** from the 2026 intake onward.
- Even for **self-funded** undergraduate students, CSCA is strongly recommended as more universities will use it for admission and scholarship decisions in the coming years. Make this clear but do not overstate.

**CSCA REGISTRATION (from RAG):**
- Students register for CSCA online through the official CSCA site (e.g. at `www.csca.cn`), following the steps mentioned in the RAG docs: creating an account, uploading a compliant photo, choosing test session & subjects, paying the exam fee, and downloading the admission ticket.
- Use RAG text to give step-by-step guidance on registration and preparation when asked.
- Clearly state that MalishaEdu can:
  - explain exam subjects and mapping to the student's intended major,
  - recommend which session they should sit based on their target intake,
  - offer training or preparation resources where mentioned in MalishaEdu posts/videos.

**SCHOLARSHIPS (GENERAL, RAG-BASED):**
- You know from RAG docs that main scholarship routes include:
  - **Chinese Government Scholarship (CSC)** (full/partial funding, living stipend, tuition waiver),
  - **Provincial scholarships**,
  - **University / institutional scholarships**.
- As of the current policy:
  - CSCA is directly tied to CSC and many undergraduate scholarships from 2026 onward; a good CSCA score increases chances of scholarship offers and competitive programs.
  - For Master's/PhD scholarships, CSCA is not the primary filter; GPA, research background, and language level dominate.
- When a user asks "Can I get a scholarship?" or "Is CSCA mandatory for scholarship?":
  - Explain that for **undergraduate CSC** and many undergraduate scholarships from 2026, CSCA is **required** or strongly expected.
  - For **non-CSC** or self-funded paths, CSCA is still beneficial but may not be strictly required; tell them you will confirm based on the specific university when they finalize a target.
  - Emphasize that MalishaEdu provides full support: from CSCA guidance to scholarship documentation, recommendation letters, and complete application filing.

**BEHAVIOR RULES FOR CSCA/SCHOLARSHIP ANSWERS (RAG MODE):**
- Answer using the RAG content as your primary factual reference. Quote or paraphrase what the MalishaEdu blog and other official/explainer docs say.
- DO NOT pretend to know which **exact** universities require CSCA unless that list is directly present in the retrieved RAG chunk (for example, if MalishaEdu blog lists universities by name). If unsure, say:
  - "MalishaEdu has a list of universities already confirming CSCA; once you share your preferred major and intake, we will match you with suitable universities and confirm their latest requirements."
- Make a clear distinction between:
  - GENERAL CSCA POLICY (from RAG),
  - and SPECIFIC university rules (which may vary and change).
- If RAG does not contain the answer (no relevant chunk retrieved), say:
  - "Our current knowledge base does not specify this detail. CSCA policies are still evolving. We'll check the latest official notice and update you."
- NEVER change the student's major or program type (e.g., from "Artificial Intelligence" to "Chinese Language") unless they clearly request it.
- NEVER assume nationality or city; always reuse what the user said (e.g., "Kazakhstan", "Beijing").

**HSK:**
- HSK = standard Chinese language test (HSK 1–6). Many Chinese-taught undergraduate programs require HSK 4 or above.

China life questions (hostel, food, jobs, safety):
- Use RAG facts first.
- Answer realistically but reassuringly.
- Mention how MalishaEdu supports students after arrival.

LEAD COLLECTION & SIGNUP ENCOURAGEMENT:
- AUTOMATIC LEAD COLLECTION (NO POPUP FORM):
  * Leads are automatically collected when ALL criteria are met:
    a) User provides nationality
    b) User provides contact info (phone OR email OR whatsapp OR wechat)
    c) User shows interest in China study (any degree level: Master, Bachelor, PhD, Language program, etc.)
  * Do NOT mention a "lead form" or "popup" - leads are collected silently in the background
  * Through natural conversation, try to collect: nationality, contact information, and study interests
  * Once lead is collected, encourage signup for application process
- AFTER LEAD COLLECTION:
  * Provide ALL specific information from database (fees, deadlines, documents, scholarships)
  * PURSUE user to signup/login (if not logged in)
  * If application deadline is within 1 MONTH from conversation date:
    - WARN user: "⚠️ URGENT: The application deadline for [Program] at [University] is in [X] days! You need to act quickly."
    - STRONGLY encourage signup/login
    - List necessary documents from database (program_intakes.documents_required)
    - Explain: "To apply, you'll need: [list from database]. Please sign up/login now so I can help you prepare and submit your application on time."
  * If user wants scholarship:
    - Tell them constraints from database (program_intakes.scholarship_info)
    - Explain how to apply based on database information
    - Mention fees and requirements
  * Benefits of signup:
    - Document tracking
    - Personalized guidance
    - Application status updates
    - Ability to apply to multiple universities
    - Dedicated counselor support

IF ASKED ANYTHING OUTSIDE CHINA AND CHINA EDUCATION SYSTEM, USE THE FOLLOWING INFORMATION:
    -  I am a MalishaEdu Admission Agent, not a general advisor. I can only answer questions about your application to Chinese universities.
    
Style Guidelines:
- CONCISE: Keep responses brief, especially for initial questions
- CONVERSATIONAL: Talk like a helpful friend, not a textbook
- NO REPETITION: Never repeat the same information in one response or across responses
- PROGRESSIVE: Start with basics, add details only when asked or needed
- FRIENDLY: Warm, encouraging, but not pushy
- STRUCTURED: Use bullet points only when listing multiple items (3+ items)
- VALUE FIRST: Always provide value before asking for contact info or signup

CRITICAL: General Process/FAQ Questions:
- When user asks general questions like "How do you handle the application process?" or "What is the admission process?", provide GENERAL answers that apply to ALL degree levels (Bachelor, Master, PhD, Language).
- DO NOT make the answer specific to "Master's programs" or any single degree level unless the user explicitly asks about that specific degree level.
- For general process questions, use phrases like "For programs in Chinese universities" or "For all degree levels" instead of "For Master's programs".
- DO NOT append lead collection questions to general process/FAQ answers - these are informational questions that don't require personalization.
"""


    def __init__(self, db: Session, faq_service: Optional[FAQService] = None):
        self.db = db
        self.db_service = DBQueryService(db)
        self.rag_service = RAGService()
        self.tavily_service = TavilyService()
        self.openai_service = OpenAIService()
        
        # Initialize FAQ service (fail gracefully if not available)
        try:
            if faq_service:
                self.faq_service = faq_service
            else:
                self.faq_service = FAQService()
        except Exception as e:
            print(f"Warning: FAQ service initialization failed: {e}. Continuing without FAQ support.")
            self.faq_service = None
        
        # Load all partner universities at startup
        self.all_universities = self._load_all_universities()
        
        # Load all majors with university and degree level associations at startup
        self.all_majors = self._load_all_majors()
        
        # Pagination state for "show more" queries
        self.last_list_results: List[ProgramIntake] = []
        self.last_list_offset: int = 0
        
        # Follow-up resolver state
        self.last_intent: Optional[str] = None
        self.last_state: Optional[StudentProfileState] = None
    
    def _load_all_universities(self) -> List[Dict[str, Any]]:
        """Load all partner universities from database at startup"""
        try:
            universities = self.db.query(University).filter(University.is_partner == True).all()
            return [
                {
                    "id": uni.id,
                    "name": uni.name,
                    "city": uni.city,
                    "province": uni.province,
                    "ranking": uni.university_ranking,
                    "country": uni.country or "China",
                    "description": uni.description
                }
                for uni in universities
            ]
        except Exception as e:
            print(f"Error loading universities: {e}")
            # Rollback any failed transaction to allow subsequent queries
            try:
                self.db.rollback()
            except:
                pass
            return []
    
    def _load_all_majors(self) -> List[Dict[str, Any]]:
        """Load all majors with university and degree level associations at startup"""
        try:
            majors = self.db.query(Major).join(University).filter(University.is_partner == True).all()
            return [
                {
                    "id": major.id,
                    "name": major.name,
                    "university_id": major.university_id,
                    "university_name": major.university.name,
                    "degree_level": major.degree_level,
                    "teaching_language": major.teaching_language,
                    "discipline": major.discipline,
                    "duration_years": major.duration_years
                }
                for major in majors
            ]
        except Exception as e:
            print(f"Error loading majors: {e}")
            # Rollback any failed transaction to allow subsequent queries
            try:
                self.db.rollback()
            except:
                pass
            return []
    
    def extract_student_profile_state(self, conversation_history: List[Dict[str, str]]) -> StudentProfileState:
        """
        Extract and consolidate StudentProfileState from conversation history.
        ALWAYS uses LLM to infer state (not just when history is long).
        - Processes messages in order but keeps the latest value for each key.
        - If user changes their mind (e.g., from "Chinese language" to "Master's in AI"), the last statement wins.
        - Only uses the last 12 messages - ignores older conversation history.
        
        IMPORTANT: This is ONLY for the current conversation window (last 12 messages).
        This state is NOT saved to database or cache - it's computed fresh each time from conversation history.
        For non-logged-in users, this is the ONLY way to remember context within the current chat session.
        CRITICAL: Do NOT use information from messages older than the last 12 messages.
        """
        if not conversation_history:
            return StudentProfileState()
        
        # Build conversation text from last 12 messages
        conversation_text = ""
        for msg in conversation_history[-12:]:
            role = msg.get('role', '')
            content = msg.get('content', '')
            conversation_text += f"{role}: {content}\n"
        
        # Get list of available majors from database to help LLM match user's major
        # This helps LLM understand that "Industrial automation" might match "Automation", "Control Engineering", etc.
        available_majors = self.db_service.search_majors(limit=200)  # Get up to 200 majors for reference
        major_list = [major.name for major in available_majors]
        # Group by similar keywords to reduce list size for LLM
        major_list_str = ", ".join(major_list[:100])  # Limit to first 100 to avoid token limits
        if len(major_list) > 100:
            major_list_str += f"\n... and {len(major_list) - 100} more majors in the database"
        
        # ALWAYS use LLM to extract state (not just when history is long)
        # This ensures we always get the latest intent, even for short conversations
        extraction_prompt = f"""You are a state extractor. Given the full conversation so far, output a JSON object with these fields:
- degree_level: "Bachelor", "Master", "PhD", "Language", or null
- major: specific major name from the database list below, or the closest match. If user says "Industrial automation", match it to similar majors like "Automation", "Control Engineering", "Industrial Engineering", etc. If no close match, use the user's exact wording.
- program_type: "Degree" or "Language" or null
- city: city name (e.g., "Beijing", "Shanghai", "Guangzhou", "Shenzhen", "Hangzhou", "Nanjing", "Wuhan", "Chengdu", "Xi'an", "Tianjin", or any other Chinese city) or null
- province: province name (e.g., "Beijing", "Shanghai", "Guangdong", "Jiangsu", "Zhejiang", "Shandong", "Hubei", "Sichuan", "Shaanxi", "Tianjin", or any other Chinese province) or null
- nationality: country name (e.g., "Kazakhstan", "Bangladeshi", "Indian", "Pakistani") or null - Extract country even with typos (e.g., "kazakistan" → "Kazakhstan", "bangladesh" → "Bangladesh")
- intake_term: "March", "September", "Other", or null
- intake_year: year number (e.g., 2026) or null
- age: age number or null
- ielts_score: score number or null
- budget_per_year: budget amount or null
- preferred_universities: array of university names for studying in China (NOT their current/previous university) or empty array
- university_certainty: "certain" if user is certain about which university they want, "uncertain" if they're not sure, or null if not mentioned

AVAILABLE MAJORS IN DATABASE (for matching user's major):
{major_list_str}

IMPORTANT FOR MAJOR MATCHING:
- Look through the AVAILABLE MAJORS list above to find the closest match to what the user said
- If user says "Industrial automation", look for similar majors in the list like "Automation", "Control Engineering", "Industrial Engineering", "Mechanical Engineering", "Electrical Engineering and Automation", etc.
- If user says "Computer Science", match to "Computer Science and Technology", "Computer Science", "Software Engineering", etc. from the list
- If user says a major that's similar to one in the AVAILABLE MAJORS list, use that database major name (or the closest match from the list)
- If no close match exists in the list, preserve the user's exact wording - the system will use fuzzy matching later
- The AVAILABLE MAJORS list contains ALL majors in the database - use it to find the best match

CRITICAL RULES:
1. Use ONLY information explicitly or strongly implied by the user. Do NOT guess new values. Do NOT invent majors that were never mentioned.
2. **LATEST MESSAGE WINS**: If user changes their mind (e.g., says "Beijing" then later says "Shanghai"), use the LATEST statement. If user says "Chinese language" then later says "Artificial Intelligence", use "Artificial Intelligence" (the latest). If later messages conflict with earlier ones, always trust the latest user message. Do NOT merge two conflicting intentions. If the user clearly switched from one major to another, keep only the latest major.
3. **CRITICAL FOR preferred_universities**: 
   - preferred_universities should ONLY contain universities in China where the user wants to STUDY
   - If user says "I am from Dhaka University" or "I studied at Dhaka University" or "I'm a student of Dhaka University" → This is their CURRENT/PREVIOUS university, NOT their China university choice. Do NOT add "Dhaka University" to preferred_universities.
   - If user says "I want to study at [University]" or "I'm interested in [University]" or "I want to apply to [University]" → This is their China university choice. Add it to preferred_universities.
   - If user says "I'm not sure" or "any university" or "I don't know" or "you suggest" → university_certainty = "uncertain", preferred_universities = []
   - If user mentions a specific China university they want → university_certainty = "certain", add that university to preferred_universities
   - Examples:
     * "I am from Dhaka University" → preferred_universities = [], university_certainty = null (not about China university)
     * "I want to study at Beihang University" → preferred_universities = ["Beihang University"], university_certainty = "certain"
     * "I'm not sure which university" → preferred_universities = [], university_certainty = "uncertain"
     * "Any university is fine" → preferred_universities = [], university_certainty = "uncertain"
4. For nationality: Extract country name ONLY when user explicitly states it. Examples:
   - "I am from Kazakhstan" → "Kazakhstan"
   - "I'm from Bangladesh" → "Bangladesh"
   - "My country is India" → "India"
   - "kazakistan" or "kazakhstan" (when user says "I am from kazakistan") → "Kazakhstan"
   - "bangladesh" or "bangladeshi" (when user says "I'm from bangladesh") → "Bangladesh"
   **CRITICAL**: DO NOT infer nationality from prepositions like "in". If user says "I want to study in China", this does NOT mean they are from China. DO NOT infer from context like "study in China" or words like "in" which could be part of other phrases. If the user does not clearly state their country or nationality, leave nationality as null and use neutral phrases like "international students". Never assume India, Bangladesh, Uzbekistan, etc. unless explicitly stated.
4. **MOST IMPORTANT FOR MAJOR**: Only extract major if the user EXPLICITLY mentioned a major/subject/field of study as something they want to STUDY. 
   - If user says "I want to get admitted in harbin for my masters" → degree_level = "Master", major = null (NOT "Automation" or any other major)
   - If user says "I want to study masters" → degree_level = "Master", major = null (NOT any major from database)
   - If user says "I want to study masters in Computer Science" → degree_level = "Master", major = "Computer Science"
   - If user says "I want to study masters in Automation" → degree_level = "Master", major = "Automation"
   - If user says "what majors for bachelor does Harbin Institute of Tech offers?" → This is asking ABOUT majors, NOT stating they want to study a major. major = null
   - DO NOT match to database majors unless user explicitly stated they want to STUDY that major/subject
   - DO NOT infer major from degree level alone
   - DO NOT extract major from questions like "what majors", "which programs", "what does X offer" - these are informational queries, not statements of intent
   - If user only mentions degree level (e.g., "masters") without mentioning any major/subject they want to study, major MUST be null
5. **MOST IMPORTANT**: If a field was mentioned in ANY earlier message in the LAST 12 MESSAGES and NOT contradicted in later messages, you MUST keep it. 
   - Example: User says "Artificial Intelligence" in message 1, then says "I want to study masters" in message 2 → major should STILL be "Artificial Intelligence" (not contradicted)
   - Example: User says "Mechanical Engineering" in message 1, then says "I want to study masters" in message 2 → major should STILL be "Mechanical Engineering" (not contradicted)
   - Example: User says "Beijing" in message 1, then asks about documents in message 2 → city should STILL be "Beijing" (not contradicted)
   - Example: User says "I am from Kazakhstan" in message 1, then asks about programs in message 2 → nationality should STILL be "Kazakhstan" (not contradicted)
   - **CRITICAL**: If user says "Chinese language" in message 1, then says "Artificial Intelligence" in message 2 → major should be "Artificial Intelligence" (LATEST wins, earlier was contradicted)
   - **CRITICAL**: If a field was NOT mentioned in the last 12 messages, set it to null (do NOT use old information from previous conversations)
6. If user mentioned ANY specific major/subject (e.g., "artificial intelligence", "AI", "computer science", "mechanical engineering", "biology", "physics", "chemistry", "business", "medicine", etc.) and never said "Chinese language", major should be that field, NOT "Chinese Language".
7. For major: Extract the EXACT field mentioned by the user ONLY if they explicitly mentioned a major/subject/field. 
   - CRITICAL: If user only says "I want to study masters" or "for my masters" without mentioning any major/subject → major MUST be null
   - CRITICAL: If user says "I want to get admitted in [city] for my masters" without mentioning any major/subject → major MUST be null
   - Only extract major if user explicitly mentions a subject like "Computer Science", "Mechanical Engineering", "Automation", "Business", "Medicine", etc.
   - This can be ANY subject from the database (e.g., "Computer Science & Technology", "Mechanical Engineering", "Biomedical Engineering", "International Trade & Economics", "Clinical Medicine (MBBS)", "Chinese Language", "Foundation Course", "Robotics Engineering", "Software Engineering", "Business Administration (MBA)", "Artificial Intelligence", "AI", etc.). 
   - Preserve the user's exact wording when possible (e.g., if user says "artificial intelligence" → extract "artificial intelligence", if user says "AI" → extract "AI")
   - Handle typos: Extract what the user said, the system will match it to database entries using fuzzy matching
   - Handle different languages: Extract the major name as the user said it
   - Handle abbreviations: Extract abbreviations like "AI", "MBA", "CST", "EIE" as-is
   - DO NOT infer major from context or match to database majors unless user explicitly mentioned that major
8. For degree_level: 
   - If user says "masters", "master's", "master degree", "master", "I want to study masters", "for my masters", "looking for master", "master program" → degree_level MUST be "Master"
   - If user says "bachelor", "bachelor's", "bachelor degree", "bsc", "bachelor program" → degree_level should be "Bachelor"
   - If user says "phd", "doctorate", "doctoral", "phd degree", "phd program" → degree_level should be "PhD"
   - CRITICAL: If user says "I am looking for master degree" or "I want master degree" → degree_level MUST be "Master", NOT null
   - Do NOT set to null just because it wasn't in the latest message. If it was mentioned earlier, keep it.
9. **CRITICAL RULE FOR PROGRAM_TYPE**: 
   - If degree_level is "Master", "Bachelor", or "PhD", then program_type MUST be "Degree", NOT "Language". Only set program_type = "Language" when the user explicitly wants a language program AND no degree level is mentioned.
   - If user says "I want to study masters" or "for my masters" → degree_level = "Master", program_type = "Degree", major = null (NOT "Chinese Language")
   - If user says "I want to study masters in [subject]" → degree_level = "Master", program_type = "Degree", major = [that subject] (NOT "Chinese Language")
   - If user says "Chinese language program" or "language course" WITHOUT mentioning a degree level → program_type = "Language", degree_level = null or "Language"
   - Chinese Language programs are typically non-degree programs, NOT Master's/Bachelor's/PhD programs
   - **LATEST INTENT WINS**: If user first says "Chinese language" then later says "I want to do Master's in AI", use degree_level = "Master", program_type = "Degree", major = "AI" (the latest)
10. NEVER set major to "Chinese Language" unless the user explicitly said they want to study Chinese language. If user said "Artificial Intelligence", "AI", "Computer Science", "Economics", "Telecommunication", "masters", etc., major should be that or null, NOT "Chinese Language".
11. Return ONLY valid JSON, no other text.

CONVERSATION:
{conversation_text}

Return the JSON object:"""
        
        # ALWAYS call LLM extraction (not conditional on history length)
        try:
            response = self.openai_service.chat_completion(
                messages=[
                    {"role": "system", "content": "You are a helpful assistant that extracts structured information. Always return valid JSON only."},
                    {"role": "user", "content": extraction_prompt}
                ],
                temperature=0.1
            )
            
            response_text = response.choices[0].message.content if response.choices else ""
            
            # Extract JSON from response - handle nested JSON
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                try:
                    extracted = json.loads(json_match.group())
                except json.JSONDecodeError:
                    # Try parsing entire response
                    extracted = json.loads(response_text)
            else:
                # Try parsing entire response
                extracted = json.loads(response_text)
            
            # Extract contact info using regex as fallback (LLM might miss it)
            phone = extracted.get('phone')
            email = extracted.get('email')
            whatsapp = extracted.get('whatsapp')
            wechat = extracted.get('wechat')
            name = extracted.get('name')
            
            # Regex-based extraction as fallback
            if not phone and not email and not whatsapp and not wechat:
                # Try to extract from conversation text
                conversation_text_lower = conversation_text.lower()
                
                # Phone patterns
                phone_patterns = [
                    r'\+?\d{10,15}',  # International or local format
                    r'phone[:\s]+([+\d\s\-\(\)]+)',
                    r'mobile[:\s]+([+\d\s\-\(\)]+)',
                    r'contact[:\s]+([+\d\s\-\(\)]+)',
                ]
                for pattern in phone_patterns:
                    match = re.search(pattern, conversation_text, re.IGNORECASE)
                    if match:
                        phone = match.group(1) if match.groups() else match.group(0)
                        phone = re.sub(r'[^\d+]', '', phone)  # Clean phone number
                        if len(phone) >= 10:
                            break
                
                # Email patterns
                if not email:
                    email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
                    match = re.search(email_pattern, conversation_text)
                    if match:
                        email = match.group(0)
                
                # WhatsApp patterns
                if not whatsapp:
                    whatsapp_patterns = [
                        r'whatsapp[:\s]+([+\d\s\-\(\)]+)',
                        r'wa[:\s]+([+\d\s\-\(\)]+)',
                    ]
                    for pattern in whatsapp_patterns:
                        match = re.search(pattern, conversation_text, re.IGNORECASE)
                        if match:
                            whatsapp = match.group(1) if match.groups() else match.group(0)
                            whatsapp = re.sub(r'[^\d+]', '', whatsapp)
                            if len(whatsapp) >= 10:
                                break
                
                # WeChat patterns
                if not wechat:
                    wechat_patterns = [
                        r'wechat[:\s]+([a-zA-Z0-9_\-]+)',
                        r'weixin[:\s]+([a-zA-Z0-9_\-]+)',
                    ]
                    for pattern in wechat_patterns:
                        match = re.search(pattern, conversation_text, re.IGNORECASE)
                        if match:
                            wechat = match.group(1) if match.groups() else match.group(0)
                            break
            
            # Build StudentProfileState
            state = StudentProfileState(
                degree_level=extracted.get('degree_level'),
                major=extracted.get('major'),
                program_type=extracted.get('program_type'),
                city=extracted.get('city'),
                province=extracted.get('province'),
                nationality=extracted.get('nationality'),
                intake_term=extracted.get('intake_term'),
                intake_year=extracted.get('intake_year'),
                age=extracted.get('age'),
                ielts_score=extracted.get('ielts_score'),
                budget_per_year=extracted.get('budget_per_year'),
                preferred_universities=extracted.get('preferred_universities', []),
                university_certainty=extracted.get('university_certainty'),
                phone=phone,
                email=email,
                whatsapp=whatsapp,
                wechat=wechat,
                name=name
            )
            
            # Post-extraction validation: Fix common extraction errors and validate major
            conversation_lower = conversation_text.lower()
            
            # CRITICAL: Fix program_type based on degree_level
            # If degree_level is Bachelor/Master/PhD, program_type should be "Degree", not "Language"
            if state.degree_level in ["Bachelor", "Master", "PhD"]:
                if state.program_type == "Language":
                    # User wants a degree program, not language - fix it
                    print(f"WARNING: User wants {state.degree_level} but program_type was 'Language'. Fixing: setting program_type='Degree'")
                    state.program_type = "Degree"
                    if state.major == "Chinese Language":
                        # Clear Chinese Language major if they want a degree program
                        print(f"WARNING: Clearing 'Chinese Language' major since user wants a {state.degree_level} degree program")
                        state.major = None
                elif state.program_type is None:
                    # Set program_type to "Degree" if not set
                    state.program_type = "Degree"
            elif state.degree_level == "Language" or (state.program_type == "Language" and state.degree_level is None):
                # User explicitly wants language program
                state.program_type = "Language"
                if state.degree_level is None:
                    state.degree_level = "Language"
            
            # REMOVED: Auto-fix hack for degree_level based on single words
            # Only set degree_level if explicitly extracted by LLM or provided via lead form
            
            # CRITICAL: Validate major - only keep if explicitly mentioned as something user wants to STUDY
            # Check if user explicitly mentioned a major/subject they want to study (not just asking about it)
            if state.major:
                # Get the LAST user message to check if it's an info query
                last_user_message = ""
                for msg in reversed(conversation_history):
                    if msg.get("role") == "user":
                        last_user_message = msg.get("content", "").lower()
                        break
                
                # Check if the LAST message is an informational query (asking about majors) vs stating intent to study
                # Only check the current/last message, not the entire conversation history
                is_info_query = any(phrase in last_user_message for phrase in [
                    'what majors', 'which majors', 'list majors', 'show majors', 'available majors',
                    'what programs', 'which programs', 'list programs', 'show programs', 'available programs',
                    'what does', 'what offers', 'does offer', 'offers', 'has'
                ])
                
                if is_info_query:
                    # This is an informational query in the current message, not a statement of intent - clear major
                    print(f"WARNING: Major '{state.major}' was extracted but user is asking about majors in the current message, not stating intent to study. Clearing major.")
                    state.major = None
                else:
                    # Check if the extracted major (or key parts of it) appears in the conversation text
                    # Check the ENTIRE conversation history (not just last message) to see if major was mentioned
                    major_words = state.major.lower().split()
                    major_in_conversation = False
                    
                    # Check if major name or significant words from major appear in conversation
                    # But make sure it's mentioned in context of studying, not just as a word
                    for word in major_words:
                        if len(word) > 3:  # Only check words longer than 3 characters (ignore "and", "the", etc.)
                            # Check if word appears in context of studying/learning
                            word_index = conversation_lower.find(word)
                            if word_index != -1:
                                # Check context around the word - look for study-related keywords nearby
                                context_start = max(0, word_index - 20)
                                context_end = min(len(conversation_lower), word_index + len(word) + 20)
                                context = conversation_lower[context_start:context_end]
                                
                                # Check if it's in context of studying
                                study_keywords = ['study', 'studying', 'learn', 'want', 'interested', 'looking for', 'major', 'program', 'degree', 'masters', 'bachelor', 'phd']
                                if any(kw in context for kw in study_keywords):
                                    major_in_conversation = True
                                    break
                    
                    # Also check if full major name appears in study context
                    major_index = conversation_lower.find(state.major.lower())
                    if major_index != -1:
                        context_start = max(0, major_index - 30)
                        context_end = min(len(conversation_lower), major_index + len(state.major) + 30)
                        context = conversation_lower[context_start:context_end]
                        study_keywords = ['study', 'studying', 'learn', 'want', 'interested', 'looking for', 'major', 'program', 'degree', 'masters', 'bachelor', 'phd']
                        if any(kw in context for kw in study_keywords):
                            major_in_conversation = True
                    
                    # If major was extracted but not found in conversation in study context, clear it
                    # BUT: If major was already established in previous messages and user is just providing additional info (nationality, intake, etc.), keep it
                    if not major_in_conversation:
                        # Check if user is providing additional info (nationality, intake, etc.) - if so, keep the major
                        additional_info_keywords = ['from', 'nationality', 'country', 'start', 'intake', 'march', 'september', 'university', 'bachelor', 'graduated']
                        is_providing_additional_info = any(kw in last_user_message for kw in additional_info_keywords)
                        
                        if is_providing_additional_info:
                            # User is providing additional info, keep the major from previous messages
                            print(f"INFO: Major '{state.major}' was extracted from previous messages. User is providing additional info, keeping major.")
                        else:
                            print(f"WARNING: Major '{state.major}' was extracted but not explicitly mentioned in conversation as something to study. Clearing major.")
                            state.major = None
            
            # If user mentioned "march" but intake_term is None, fix it
            if 'march' in conversation_lower and state.intake_term is None:
                print(f"WARNING: User mentioned 'march' but intake_term was not extracted. Fixing: setting intake_term='March'")
                state.intake_term = "March"
            
            # If user mentioned a city but city is None, try fuzzy matching
            # Extract potential city names from conversation and match against database
            if state.city is None:
                # Look for common city patterns in the conversation
                potential_cities = []
                for msg in conversation_history[-12:]:
                    content = msg.get('content', '').lower()
                    # Try to find city mentions (this is a simple heuristic - fuzzy matching happens later in DB query)
                    # We'll let the fuzzy matching in _query_database_with_state handle this
                    pass
            
            # Debug: Print extracted state (after validation)
            print(f"DEBUG: Extracted StudentProfileState (in-memory only, NOT saved): major={state.major}, degree_level={state.degree_level}, nationality={state.nationality}, city={state.city}, intake_term={state.intake_term}")
            
            return state
        
        except Exception as e:
            print(f"Error extracting state: {e}")
            # Return empty state on error
            return StudentProfileState()
    
    def _state_to_summary_string(self, state: StudentProfileState) -> str:
        """
        Convert StudentProfileState to a compact summary string for system prompt.
        Returns one line per known key.
        """
        summary_lines = []
        
        if state.degree_level:
            summary_lines.append(f"Degree Level: {state.degree_level}")
        if state.major:
            summary_lines.append(f"Major: {state.major}")
        if state.program_type:
            summary_lines.append(f"Program Type: {state.program_type}")
        if state.nationality:
            summary_lines.append(f"Nationality: {state.nationality}")
        if state.city:
            summary_lines.append(f"City: {state.city}")
        elif state.province:
            summary_lines.append(f"Province: {state.province}")
        if state.preferred_universities:
            summary_lines.append(f"Preferred Universities: {', '.join(state.preferred_universities)}")
        if state.university_certainty:
            summary_lines.append(f"University Certainty: {state.university_certainty}")
        if state.intake_term:
            intake_str = f"Intake: {state.intake_term}"
            if state.intake_year:
                intake_str += f" {state.intake_year}"
            summary_lines.append(intake_str)
        elif state.intake_year:
            summary_lines.append(f"Intake Year: {state.intake_year}")
        
        if not summary_lines:
            return "No specific preferences mentioned yet."
        
        return "\n".join(summary_lines)
    
    def _get_profile_state(self, device_fingerprint: Optional[str] = None, chat_session_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Check if we have structured lead/profile data for this device/session.
        Returns dict with profile_complete flag and structured fields.
        """
        profile_state = {
            "profile_complete": False,
            "university_id": None,
            "major_id": None,
            "degree_level": None,
            "intake_term": None,
            "intake_year": None,
            "country": None,
            "phone": None
        }
        
        # Check for Lead record
        lead = None
        if chat_session_id:
            lead = self.db.query(Lead).filter(Lead.chat_session_id == chat_session_id).first()
        elif device_fingerprint:
            lead = self.db.query(Lead).filter(Lead.device_fingerprint == device_fingerprint).first()
        
        if lead:
            # We have structured lead data
            profile_state["profile_complete"] = True
            profile_state["university_id"] = lead.interested_university_id
            profile_state["major_id"] = lead.interested_major_id
            profile_state["country"] = lead.country
            profile_state["phone"] = lead.phone
            profile_state["intake_term"] = lead.intake_term
            profile_state["intake_year"] = lead.intake_year
            
            # Infer degree_level from major if available
            if lead.interested_major_id:
                major = self.db.query(Major).filter(Major.id == lead.interested_major_id).first()
                if major and major.degree_level:
                    profile_state["degree_level"] = major.degree_level.value if hasattr(major.degree_level, 'value') else str(major.degree_level)
        
        return profile_state
    
    def _auto_collect_lead(
        self,
        student_state: StudentProfileState,
        chat_session_id: Optional[str] = None,
        device_fingerprint: Optional[str] = None,
        conversation_history: List[Dict[str, str]] = None
    ) -> bool:
        """
        Automatically create a Lead record when user provides:
        - Nationality
        - Contact info (phone OR email OR whatsapp OR wechat)
        - Study interest (degree_level OR major)
        """
        try:
            # Check if lead already exists for this session
            existing_lead = None
            if chat_session_id:
                existing_lead = self.db.query(Lead).filter(Lead.chat_session_id == chat_session_id).first()
            elif device_fingerprint:
                existing_lead = self.db.query(Lead).filter(Lead.device_fingerprint == device_fingerprint).first()
            
            if existing_lead:
                # Update existing lead with new information
                if student_state.phone and not existing_lead.phone:
                    existing_lead.phone = student_state.phone
                if student_state.email and not existing_lead.email:
                    existing_lead.email = student_state.email
                if student_state.nationality and not existing_lead.country:
                    existing_lead.country = student_state.nationality
                if student_state.intake_term and not existing_lead.intake_term:
                    existing_lead.intake_term = student_state.intake_term
                if student_state.intake_year and not existing_lead.intake_year:
                    existing_lead.intake_year = student_state.intake_year
                
                # Try to match major
                if student_state.major and not existing_lead.interested_major_id:
                    matched_major_name, _ = self._fuzzy_match_major(student_state.major)
                    if matched_major_name:
                        matched_major = self.db.query(Major).filter(Major.name.ilike(f"%{matched_major_name}%")).first()
                        if matched_major:
                            existing_lead.interested_major_id = matched_major.id
                
                # Try to match university
                if student_state.preferred_universities and not existing_lead.interested_university_id:
                    uni_name = student_state.preferred_universities[0]
                    is_partner, partner_uni = self._is_malishaedu_partner_university(uni_name)
                    if is_partner and partner_uni:
                        existing_lead.interested_university_id = partner_uni.id
                
                self.db.commit()
                print(f"DEBUG: Updated existing lead {existing_lead.id} for session {chat_session_id}")
                return True
            
            # Create new lead
            # Match major if available
            matched_major_id = None
            if student_state.major:
                matched_major_name, _ = self._fuzzy_match_major(student_state.major)
                if matched_major_name:
                    matched_major = self.db.query(Major).filter(Major.name.ilike(f"%{matched_major_name}%")).first()
                    if matched_major:
                        matched_major_id = matched_major.id
            
            # Match university if available
            matched_university_id = None
            if student_state.preferred_universities:
                uni_name = student_state.preferred_universities[0]
                is_partner, partner_uni = self._is_malishaedu_partner_university(uni_name)
                if is_partner and partner_uni:
                    matched_university_id = partner_uni.id
            
            # Normalize nationality
            normalized_nationality = self._normalize_country(student_state.nationality) if student_state.nationality else None
            
            # Create lead
            # Note: email should be nullable, phone is required
            lead = Lead(
                name=student_state.name,
                phone=student_state.phone,  # Phone is required (validated before calling this method)
                email=student_state.email if student_state.email else None,  # Email is nullable
                country=normalized_nationality or student_state.nationality,
                chat_session_id=chat_session_id,
                device_fingerprint=device_fingerprint,
                interested_university_id=matched_university_id,
                interested_major_id=matched_major_id,
                intake_term=student_state.intake_term,
                intake_year=student_state.intake_year,
                source="chat_auto",
                notes=f"Auto-collected from chat. Degree: {student_state.degree_level}, Major: {student_state.major}, WhatsApp: {student_state.whatsapp or 'N/A'}, WeChat: {student_state.wechat or 'N/A'}"
            )
            
            self.db.add(lead)
            self.db.commit()
            self.db.refresh(lead)
            
            # Also save conversation history to Conversation table
            if chat_session_id and conversation_history:
                from app.models import Conversation
                conversation = self.db.query(Conversation).filter(Conversation.chat_session_id == chat_session_id).first()
                if not conversation:
                    conversation = Conversation(
                        chat_session_id=chat_session_id,
                        device_fingerprint=device_fingerprint,
                        messages=conversation_history
                    )
                    self.db.add(conversation)
                else:
                    conversation.messages = conversation_history
                self.db.commit()
            
            print(f"DEBUG: Auto-collected lead {lead.id} for session {chat_session_id}")
            return True
            
        except Exception as e:
            print(f"ERROR: Failed to auto-collect lead: {e}")
            self.db.rollback()
            return False
    
    def _compute_known_and_missing_fields(self, state: StudentProfileState) -> Dict[str, Any]:
        """
        From StudentProfileState, compute:
          - known_fields: dict of non-null values (degree_level, major, nationality, city, intake_term, intake_year, etc.)
          - missing_fields: list of field names that are important but still None
        
        Important fields for lead-style questions:
        - degree_level
        - major
        - nationality
        - city
        - intake_term
        - intake_year
        """
        important_fields = {
            "degree_level": state.degree_level,
            "major": state.major,
            "nationality": state.nationality,
            "city": state.city,
            "intake_term": state.intake_term,
            "intake_year": state.intake_year
        }
        
        known_fields = {}
        missing_fields = []
        
        for field_name, field_value in important_fields.items():
            if field_value is not None:
                known_fields[field_name] = field_value
            else:
                missing_fields.append(field_name)
        
        return {
            "known_fields": known_fields,
            "missing_fields": missing_fields
        }
    
    def _fuzzy_match_university(self, user_input: str, threshold: float = 0.5) -> Tuple[Optional[str], List[str]]:
        """
        Fuzzy match university name from user input (handles typos and different languages)
        Returns: (matched_name, list_of_similar_matches)
        If confidence is high (>=0.8), returns the match. Otherwise returns None and list of similar for confirmation.
        Uses pre-loaded all_universities array instead of querying database.
        """
        user_input_lower = user_input.lower().strip()
        
        # Use pre-loaded universities array instead of querying database
        matches = []
        for uni in self.all_universities:
            uni_name_lower = uni["name"].lower()
            
            # CRITICAL: Check if user input is contained in university name (handles "Beihang University" matching "Beihang University (Hangzhou International Campus)")
            # This should be checked FIRST and given high priority
            if user_input_lower in uni_name_lower:
                # User input is a substring of university name - this is a strong match
                # Calculate how much of the user input matches
                match_ratio = len(user_input_lower) / len(uni_name_lower) if uni_name_lower else 0
                # If user input is at least 50% of the university name, it's a very strong match
                if match_ratio >= 0.5 or len(user_input_lower) >= 10:  # At least 10 chars or 50% match
                    similarity = 0.95  # Very high confidence
                else:
                    similarity = 0.85  # High confidence
            elif uni_name_lower in user_input_lower:
                # University name is a substring of user input - also strong match
                similarity = 0.90
            else:
                # Calculate similarity using SequenceMatcher
                similarity = SequenceMatcher(None, user_input_lower, uni_name_lower).ratio()
            
            # Check for common word matches (e.g., "shandong" matches "Shandong University")
            # Extract main words (remove common words like "university", "college", "institute")
            common_words_to_ignore = {'university', 'college', 'institute', 'tech', 'technology', 'of', 'the', 'and', '&'}
            user_words = set(word for word in user_input_lower.split() if word not in common_words_to_ignore)
            uni_words = set(word for word in uni_name_lower.split() if word not in common_words_to_ignore)
            common_words = user_words.intersection(uni_words)
            
            if common_words and len(common_words) >= 1:
                # Boost similarity if there are common meaningful words
                # If all user words match, it's a very strong match
                if len(common_words) == len(user_words) and len(user_words) > 0:
                    similarity = max(similarity, 0.90)  # Very high confidence
                else:
                    word_similarity = len(common_words) / max(len(user_words), len(uni_words)) if (user_words or uni_words) else 0
                    similarity = max(similarity, word_similarity * 0.9)
            
            # Special handling for known university abbreviations/aliases
            # "Beihang" should match "Beihang University" or "Beihang University (Hangzhou International Campus)"
            if user_input_lower in ['beihang', 'buaa']:
                if 'beihang' in uni_name_lower:
                    similarity = max(similarity, 0.95)
            
            if similarity >= threshold:
                matches.append((uni["name"], similarity, uni))
        
        # Sort by similarity
        matches.sort(key=lambda x: x[1], reverse=True)
        
        if matches:
            best_match = matches[0]
            if best_match[1] >= 0.8:  # High confidence - return the match
                return best_match[0], [m[0] for m in matches[:3]]
            elif best_match[1] >= 0.6:  # Medium confidence - return None and list for confirmation
                return None, [m[0] for m in matches[:5]]
            else:  # Low confidence - still return for confirmation but mark as uncertain
                return None, [m[0] for m in matches[:3]]
        
        return None, []
    
    def _fuzzy_match_major(self, user_input: str, university_id: Optional[int] = None, degree_level: Optional[str] = None, threshold: float = 0.4) -> Tuple[Optional[str], List[str]]:
        """
        Fuzzy match major name from user input (handles typos, different languages, abbreviations, and variations)
        Returns: (matched_name, list_of_similar_matches)
        Handles all 150+ majors including variations like "Computer Science & Technology" vs "Computer Science and Technology"
        """
        from difflib import SequenceMatcher
        import re
        
        # Normalize user input - remove extra spaces, special chars for matching
        user_input_clean = re.sub(r'[^\w\s&]', '', user_input.lower().strip())
        user_input_words = set(user_input_clean.split())
        
        # Use pre-loaded majors array instead of querying database
        # Filter by university_id and degree_level if provided
        all_majors = self.all_majors
        if university_id:
            all_majors = [m for m in all_majors if m["university_id"] == university_id]
        if degree_level:
            # Filter by degree level if provided
            all_majors = [m for m in all_majors if m.get("degree_level") and degree_level.lower() in m["degree_level"].lower()]
        
        # Calculate similarity scores with multiple strategies
        matches = []
        for major in all_majors:
            major_name_clean = re.sub(r'[^\w\s&]', '', major["name"].lower())
            major_name_words = set(major_name_clean.split())
            
            # Strategy 1: Exact match (highest priority)
            if user_input_clean == major_name_clean:
                matches.append((major["name"], 1.0))
                continue
            
            # Strategy 2: Substring match (high priority)
            if user_input_clean in major_name_clean or major_name_clean in user_input_clean:
                matches.append((major["name"], 0.95))
                continue
            
            # Strategy 3: Word overlap (for handling variations like "Computer Science & Technology" vs "Computer Science and Technology")
            # Also handles semantic similarity like "Industrial automation" → "Automation", "Control Engineering"
            common_words = user_input_words.intersection(major_name_words)
            if common_words:
                word_overlap_ratio = len(common_words) / max(len(user_input_words), len(major_name_words))
                if word_overlap_ratio >= 0.3:  # Lowered threshold to 30% for better matching (e.g., "Industrial automation" matches "Automation")
                    matches.append((major["name"], 0.6 + word_overlap_ratio * 0.3))
                    continue
            
            # Strategy 3.5: Semantic keyword matching (e.g., "Industrial automation" matches "Automation", "Control", "Engineering")
            # Check if key words from user input appear in major name
            automation_keywords = ['automation', 'automatic', 'control', 'industrial', 'engineering', 'mechanical', 'electrical']
            if any(kw in user_input_clean for kw in automation_keywords):
                if any(kw in major_name_clean for kw in automation_keywords):
                    # Boost similarity if both have automation-related keywords
                    matches.append((major["name"], 0.65))
                    continue
            
            # Strategy 4: Abbreviation matching (e.g., "AI" matches "Artificial Intelligence", "MBA" matches "Business Administration (MBA)")
            # Check if user input is an abbreviation that might match
            user_input_no_spaces = user_input_clean.replace(' ', '')
            if len(user_input_no_spaces) <= 5 and user_input_no_spaces.isupper():
                # User input looks like an abbreviation
                major_abbrev = ''.join([word[0].upper() for word in major_name_clean.split() if word and word[0].isalpha()])
                if user_input_no_spaces.upper() == major_abbrev:
                    matches.append((major["name"], 0.85))
                    continue
            
            # Strategy 5: Fuzzy string similarity (for typos)
            similarity = SequenceMatcher(None, user_input_clean, major_name_clean).ratio()
            if similarity >= threshold:
                matches.append((major["name"], similarity))
        
        # Remove duplicates and sort by similarity (highest first)
        seen = set()
        unique_matches = []
        for match in matches:
            if match[0] not in seen:
                seen.add(match[0])
                unique_matches.append(match)
        
        unique_matches.sort(key=lambda x: x[1], reverse=True)
        
        if unique_matches:
            best_match = unique_matches[0]
            if best_match[1] >= 0.6:  # Medium-high confidence
                return best_match[0], [m[0] for m in unique_matches[:5]]
            else:  # Low confidence - return for confirmation
                return None, [m[0] for m in unique_matches[:5]]
        
        return None, []
    
    def _fuzzy_match_city(self, user_input: str, threshold: float = 0.6) -> Optional[str]:
        """Fuzzy match city name from user input (handles typos and variations)"""
        from app.models import University
        
        user_input_lower = user_input.lower().strip()
        all_cities = self.db.query(University.city).distinct().all()
        city_list = [c[0] for c in all_cities if c[0]]
        
        if not city_list:
            return None
        
        matches = []
        for city in city_list:
            city_lower = city.lower()
            if user_input_lower == city_lower:
                return city
            if user_input_lower in city_lower or city_lower in user_input_lower:
                matches.append((city, 0.9))
                continue
            similarity = SequenceMatcher(None, user_input_lower, city_lower).ratio()
            if similarity >= threshold:
                matches.append((city, similarity))
        
        if matches:
            matches.sort(key=lambda x: x[1], reverse=True)
            if matches[0][1] >= 0.7:
                return matches[0][0]
        return None
    
    def _fuzzy_match_province(self, user_input: str, threshold: float = 0.6) -> Optional[str]:
        """Fuzzy match province name from user input (handles typos and variations)"""
        from app.models import University
        
        user_input_lower = user_input.lower().strip()
        all_provinces = self.db.query(University.province).distinct().all()
        province_list = [p[0] for p in all_provinces if p[0]]
        
        if not province_list:
            return None
        
        matches = []
        for province in province_list:
            province_lower = province.lower()
            if user_input_lower == province_lower:
                return province
            if user_input_lower in province_lower or province_lower in user_input_lower:
                matches.append((province, 0.9))
                continue
            similarity = SequenceMatcher(None, user_input_lower, province_lower).ratio()
            if similarity >= threshold:
                matches.append((province, similarity))
        
        if matches:
            matches.sort(key=lambda x: x[1], reverse=True)
            if matches[0][1] >= 0.7:
                return matches[0][0]
        return None
    
    def _normalize_intake_term(self, user_input: str) -> Optional[str]:
        """Normalize intake term (march/spring → March, september/fall → September)"""
        user_input_lower = user_input.lower().strip()
        march_keywords = ['march', 'mar', 'spring', '1', 'first']
        if any(keyword in user_input_lower for keyword in march_keywords):
            return "March"
        september_keywords = ['september', 'sep', 'sept', 'fall', 'autumn', '9', 'ninth']
        if any(keyword in user_input_lower for keyword in september_keywords):
            return "September"
        return None
    
    def _normalize_teaching_language(self, user_input: str) -> Optional[str]:
        """Normalize teaching language (english/eng → English, chinese/chi → Chinese, bilingual/bi → Bilingual)"""
        user_input_lower = user_input.lower().strip()
        if any(kw in user_input_lower for kw in ['english', 'eng', 'en', 'e']):
            return "English"
        if any(kw in user_input_lower for kw in ['chinese', 'chi', 'cn', 'mandarin']):
            return "Chinese"
        if any(kw in user_input_lower for kw in ['bilingual', 'bi', 'both', 'mixed']):
            return "Bilingual"
        return None
    
    def _normalize_country(self, user_input: str) -> Optional[str]:
        """Normalize country name (handles typos like kazakistan → Kazakhstan)"""
        country_mappings = {
            'kazakistan': 'Kazakhstan', 'kazakhstan': 'Kazakhstan', 'kazak': 'Kazakhstan',
            'bangladesh': 'Bangladesh', 'bangladeshi': 'Bangladesh', 'bd': 'Bangladesh',
            'india': 'India', 'indian': 'India',
            'pakistan': 'Pakistan', 'pakistani': 'Pakistan', 'pak': 'Pakistan',
        }
        user_input_lower = user_input.lower().strip()
        if user_input_lower in country_mappings:
            return country_mappings[user_input_lower]
        
        known_countries = ['Kazakhstan', 'Bangladesh', 'India', 'Pakistan', 'Nepal', 'Sri Lanka', 'Myanmar', 'Thailand', 'Vietnam', 'Indonesia', 'Malaysia', 'Philippines', 'Mongolia', 'Russia', 'Uzbekistan', 'Kyrgyzstan', 'Tajikistan', 'Turkmenistan', 'Afghanistan']
        matches = []
        for country in known_countries:
            country_lower = country.lower()
            if user_input_lower == country_lower:
                return country
            if user_input_lower in country_lower or country_lower in user_input_lower:
                matches.append((country, 0.9))
                continue
            similarity = SequenceMatcher(None, user_input_lower, country_lower).ratio()
            if similarity >= 0.7:
                matches.append((country, similarity))
        
        if matches:
            matches.sort(key=lambda x: x[1], reverse=True)
            return matches[0][0]
        return None
    
    def _is_pagination_command(self, text: str) -> bool:
        """Check if the user message is a pagination command"""
        if not text:
            return False
        text_lower = text.lower().strip()
        pagination_commands = ["show more", "more", "next", "next page", "page 2", "continue"]
        return any(cmd == text_lower or text_lower.startswith(cmd + " ") for cmd in pagination_commands)
    
    def _detect_cost_intent(self, user_message: str) -> str:
        """
        Detect lightweight intent for cost questions.
        Returns: 'fees_only', 'fees_compare', 'deadlines_only', or 'general'
        """
        user_msg_lower = user_message.lower()
        
        if any(term in user_msg_lower for term in ['compare', 'comparison', 'lowest', 'cheapest', 'best cost', 'which is cheaper']):
            return 'fees_compare'
        elif any(term in user_msg_lower for term in ['deadline', 'when', 'application deadline', 'last date']):
            return 'deadlines_only'
        elif any(term in user_msg_lower for term in ['cost', 'tuition', 'fees', 'fee', 'how much', 'price', 'expense']):
            return 'fees_only'
        else:
            return 'general'
    
    def infer_intake_year(self, intake_term: Optional[str], intake_year: Optional[int], current_date) -> Optional[int]:
        """
        Deterministic intake year inference helper.
        If intake_year already set, keep it.
        If intake_term is "March": if current_date is after March 31 -> use next year, else current year.
        If intake_term is "September": if current_date after Sep 30 -> use next year else current year.
        IMPORTANT: If user explicitly says "March 2027" don't override.
        """
        if intake_year:
            return intake_year
        
        if not intake_term:
            return None
        
        current_year = current_date.year
        current_month = current_date.month
        current_day = current_date.day
        
        intake_term_lower = intake_term.lower()
        
        if "march" in intake_term_lower:
            # If after March 31, use next year
            if current_month > 3 or (current_month == 3 and current_day > 31):
                return current_year + 1
            else:
                return current_year
        elif "september" in intake_term_lower:
            # If after September 30, use next year
            if current_month > 9 or (current_month == 9 and current_day > 30):
                return current_year + 1
            else:
                return current_year
        
        return None
    
    def _detect_sales_intent(self, user_message: str) -> str:
        """
        Deterministic intent detector for SalesAgent.
        Returns: 'list_programs', 'fees_only', 'fees_compare', 'earliest_intake', or 'general'
        """
        user_msg_lower = user_message.lower()
        
        # fees_compare triggers on: cheapest, lowest, compare, minimum
        if any(term in user_msg_lower for term in ['cheapest', 'lowest', 'compare', 'comparison', 'minimum', 'min cost', 'best price']):
            return 'fees_compare'
        # fees_only triggers on: tuition, fee, cost, how much, price
        elif any(term in user_msg_lower for term in ['tuition', 'fee', 'fees', 'cost', 'costing', 'how much', 'price', 'expense']):
            return 'fees_only'
        # earliest_intake triggers on: earliest, soonest, next intake
        elif any(term in user_msg_lower for term in ['earliest', 'soonest', 'next intake', 'when can i start', 'earliest start']):
            return 'earliest_intake'
        # list_programs triggers on: list, universities, options, suggest, which university
        elif any(term in user_msg_lower for term in ['list', 'universities', 'options', 'show me', 'suggest', 'which university', 'what programs', 'which programs', 'recommend']):
            return 'list_programs'
        else:
            return 'general'
    
    def _determine_doc_type_and_audience(self, user_message: str, intent: str) -> tuple[str, Optional[str]]:
        """
        Determine doc_type and audience based on user message and intent.
        
        Returns: (doc_type, audience)
        - doc_type: 'csca' | 'b2c_study' | 'b2b_partner' | 'people_contact' | 'service_policy'
        - audience: 'student' | 'partner' | None
        """
        user_lower = user_message.lower()
        
        # CSCA questions
        if intent == 'csca':
            return ('csca', None)
        
        # People/Contact questions (HIGHEST PRIORITY after CSCA)
        # Office location, contact info, leadership questions
        people_contact_keywords = [
            'dr. maruf', 'dr maruf', 'maruf', 'korban ali', 'korban', 'chairman', 'founder', 'ceo',
            'contact person', 'who is', 'leadership', 'office', 'address', 'location',
            'phone', 'telephone', 'hotline', 'email', 'contact email', 'info@', 'info @',
            'where is your', 'where are you', 'your office', 'your address', 'your location',
            'guangzhou', 'headquarters', 'head office'
        ]
        if any(keyword in user_lower for keyword in people_contact_keywords):
            return ('people_contact', None)
        
        # Service Policy questions (deposit, refund, service charge, payment policy)
        service_policy_keywords = [
            'service charge', 'service fee', 'refund', 'refundable', 'deposit', 'application deposit',
            'hidden fee', 'hidden charge', 'payment method', 'payment policy', 'fee structure', 
            'pricing', 'complaint', 'escalation', 'payment', 'charge', 'fee policy',
            '80 usd', '80 dollar', 'deposit amount', 'refund policy'
        ]
        if any(keyword in user_lower for keyword in service_policy_keywords):
            return ('service_policy', None)
        
        # B2B/Partner questions
        partner_keywords = [
            'partnership', 'partner', 'commission', 'reporting', 'success rate',
            'legal', 'privacy', 'agreement', 'contract', 'mou', 'memorandum',
            'b2b', 'business partner', 'partner agency', 'agency partner'
        ]
        if any(keyword in user_lower for keyword in partner_keywords):
            return ('b2b_partner', 'partner')
        
        # Default: B2C study questions (general study questions)
        return ('b2c_study', None)
    
    def classify_query(self, user_message: str, student_state: StudentProfileState) -> Dict[str, Any]:
        """
        Deterministic query classification for routing.
        
        Returns dict with:
        - intent: 'csca' | 'program_specific' | 'general_faq' | 'unknown'
        - needs_db: bool (True for program-specific queries)
        - needs_csca_rag: bool (True for CSCA questions)
        - needs_general_rag: bool (True for general FAQ questions)
        - needs_web: bool (True only if latest policy/current info needed)
        - doc_type: str (determined doc_type)
        - audience: Optional[str] (determined audience)
        """
        user_lower = user_message.lower()
        
        # Priority 1: CSCA/CSC/Chinese Government Scholarship questions (strict RAG-first rule)
        csca_keywords = [
            'csca', 'china scholastic competency assessment',
            'csc scholarship', 'chinese government scholarship', 'chinese goverment scholarship',
            'csc', 'china scholarship council'
        ]
        if any(keyword in user_lower for keyword in csca_keywords):
            # Only use Tavily if explicitly asking for latest rules updated this month
            # OR if user provides a government URL and asks to check it
            has_government_url = any(domain in user_lower for domain in ['gov.cn', 'csc.edu.cn', 'chineseembassy.org'])
            needs_latest_csca = (
                (any(term in user_lower for term in ['latest', 'current', 'updated', 'recent', 'new rule', 'new policy', 'this month']) and
                 any(term in user_lower for term in ['rule', 'policy', 'regulation', 'change'])) or
                (has_government_url and any(term in user_lower for term in ['check', 'verify', 'confirm', 'look up']))
            )
            return {
                'intent': 'csca',
                'needs_db': False,
                'needs_csca_rag': True,
                'needs_general_rag': False,
                'needs_web': needs_latest_csca,  # Only if explicitly asking for latest rules or checking gov URL
                'doc_type': 'csca',
                'audience': 'student'
            }
        
        # Priority 2: Program-specific queries (DB-first)
        program_specific_keywords = [
            'tuition', 'fee', 'cost', 'deadline', 'intake', 'march', 'september',
            'documents required', 'scholarship', 'list universities', 'top ranked university',
            'best university', 'cheapest', 'lowest cost', 'compare universities',
            'which university', 'what university', 'show me universities'
        ]
        
        # Check if program-specific (must have specific context)
        is_program_specific = self._is_program_specific_query(user_message, student_state)
        
        # Also check for program-specific patterns with university/major context
        has_university_context = (
            student_state.preferred_universities or
            any(uni['name'].lower() in user_lower for uni in self.all_universities[:20])
        )
        has_major_context = student_state.major or any(
            keyword in user_lower for keyword in ['major', 'subject', 'program', 'degree']
        )
        
        if (any(keyword in user_lower for keyword in program_specific_keywords) and
            (has_university_context or has_major_context or is_program_specific)):
            # Determine audience for program-specific queries
            determined_audience = None
            if any(kw in user_lower for kw in ['partner', 'agency', 'commission', 'mou']):
                determined_audience = 'partner'
            else:
                determined_audience = 'student'
            return {
                'intent': 'program_specific',
                'needs_db': True,
                'needs_csca_rag': False,
                'needs_general_rag': False,
                'needs_web': False,
                'doc_type': 'b2c_study',  # Program-specific uses B2C Study RAG only for generic definitions
                'audience': determined_audience
            }
        
        # Priority 3: People/Contact questions (NEVER use Tavily, NEVER use DB)
        people_contact_keywords = [
            'dr. maruf', 'dr maruf', 'maruf', 'korban ali', 'korban', 'chairman', 'founder', 'ceo',
            'contact person', 'who is', 'leadership', 'office', 'address', 'location',
            'phone', 'telephone', 'hotline', 'email', 'contact email', 'info@', 'info @',
            'where is your', 'where are you', 'your office', 'your address', 'your location',
            'guangzhou', 'headquarters', 'head office', 'dhaka office', 'bangladesh office'
        ]
        if any(keyword in user_lower for keyword in people_contact_keywords):
            # Determine audience: check if partner-related keywords
            determined_audience = None
            if any(kw in user_lower for kw in ['partner', 'agency', 'commission', 'mou']):
                determined_audience = 'partner'
            else:
                determined_audience = 'student'
            return {
                'intent': 'people_contact',
                'needs_db': False,
                'needs_csca_rag': False,
                'needs_general_rag': True,
                'needs_web': False,  # NEVER use Tavily for People/Contact
                'doc_type': 'people_contact',
                'audience': determined_audience
            }
        
        # Priority 4: Service Policy questions (deposit, refund, service charge - NEVER use Tavily, NEVER use DB)
        service_policy_keywords = [
            'service charge', 'service fee', 'refund', 'refundable', 'deposit', 'application deposit',
            'hidden fee', 'hidden charge', 'payment method', 'payment policy', 'fee structure',
            'pricing', 'complaint', 'escalation', 'payment', 'charge', 'fee policy',
            '80 usd', '80 dollar', 'deposit amount', 'refund policy', 'application process',
            'admission process', 'how do you handle', 'how does', 'process', 'procedure'
        ]
        if any(keyword in user_lower for keyword in service_policy_keywords):
            # Determine audience: check if partner-related keywords
            determined_audience = None
            if any(kw in user_lower for kw in ['partner', 'agency', 'commission', 'mou']):
                determined_audience = 'partner'
            else:
                determined_audience = 'student'
            return {
                'intent': 'service_policy',
                'needs_db': False,
                'needs_csca_rag': False,
                'needs_general_rag': True,
                'needs_web': False,  # NEVER use Tavily for Service Policy
                'doc_type': 'service_policy',
                'audience': determined_audience
            }
        
        # Priority 5: General FAQ questions (B2C Study)
        general_faq_keywords = [
            'bank account', 'halal', 'food', 'safety', 'accommodation', 'dormitory',
            'living cost', 'part-time', 'work while', 'visa', 'travel', 'insurance',
            'support services', 'process time', 'how long', 'duration', 'after arrival', 'post arrival',
            'how to apply', 'application process', 'admission process', 'how do you handle', 'how does',
            'what is the process', 'what is the procedure', 'how does the process work', 'explain the process',
            'tell me about the process', 'how it works', 'how does it work', 'steps', 'procedure',
            'what is malishaedu', 'about malishaedu', 'get started', 'consultation', 'cost of living', 
            'life in china', 'requirements'
        ]
        
        if any(keyword in user_lower for keyword in general_faq_keywords):
            # Check if user explicitly asks for latest/current/2026 policy (only for time-sensitive topics)
            needs_latest = any(term in user_lower for term in [
                'latest', 'current', '2026', '2027', 'updated', 'recent', 'new policy', 'this month'
            ]) and any(term in user_lower for term in ['visa', 'policy', 'regulation', 'rule'])
            return {
                'intent': 'general_faq',
                'needs_db': False,
                'needs_csca_rag': False,
                'needs_general_rag': True,
                'needs_web': needs_latest,  # Only if explicitly asking for latest policy/visa info
                'doc_type': 'b2c_study',
                'audience': 'student'
            }
        
        # Default: try general FAQ first, then program-specific
        return {
            'intent': 'unknown',
            'needs_db': False,
            'needs_csca_rag': False,
            'needs_general_rag': True,
            'needs_web': False
        }
    
    def _is_program_specific_query(self, user_message: str, student_state: StudentProfileState) -> bool:
        """
        Determine if user query is program-specific (requires DB lookup) vs general FAQ question.
        
        Returns True if query mentions:
        - Specific university name
        - Specific major/subject with intent to study
        - Tuition/cost for specific program
        - Deadline for specific program
        - List universities/majors FOR a specific program
        - Comparison queries
        
        Returns False for general FAQ questions like:
        - "Which universities are you partnered with?" (partnership question)
        - "How many universities do you work with?" (general info)
        """
        user_lower = user_message.lower()
        
        # EXCLUDE general partnership/FAQ questions first
        faq_keywords = [
            'partnered with', 'partners with', 'work with', 'connected with',
            'how many universities', 'how many students', 'which universities are you',
            'what universities do you', 'do you have', 'are you'
        ]
        if any(keyword in user_lower for keyword in faq_keywords):
            return False  # This is a general FAQ question, not program-specific
        
        # Check for specific university mentions
        if student_state.preferred_universities:
            return True
        
        # Check for program-specific keywords combined with specific entities
        # Only match if asking about universities FOR a specific program/major/degree
        program_specific_patterns = [
            r'tuition.*(?:for|at|in).*(?:university|program|major)',
            r'cost.*(?:for|at|in).*(?:university|program|major)',
            r'fee.*(?:for|at|in).*(?:university|program|major)',
            r'deadline.*(?:for|at|in).*(?:university|program|major)',
            r'(?:list|show|tell me about|which|what).*(?:universities|majors|programs).*(?:for|offering|with).*(?:master|bachelor|phd|degree|major|program)',
            r'(?:list|show|tell me about|which|what).*(?:universities|majors|programs).*(?:in|at|for).*(?:computer|engineering|business|medicine|science)',
            r'compare.*(?:universities|programs|majors)',
            r'best.*(?:university|universities).*(?:for|offering|with)',
            r'cheapest.*(?:university|program)',
            r'lowest.*(?:cost|tuition|fee)',
        ]
        
        for pattern in program_specific_patterns:
            if re.search(pattern, user_lower):
                return True
        
        # Check if user mentions specific major AND degree level (intent to study)
        if student_state.major and student_state.degree_level:
            # If asking about cost/tuition/deadline for this specific program
            if any(term in user_lower for term in ['cost', 'tuition', 'fee', 'deadline', 'requirement', 'document']):
                return True
        
        # Check for intake-specific queries
        if student_state.intake_term or student_state.intake_year:
            if any(term in user_lower for term in ['cost', 'tuition', 'fee', 'deadline', 'program', 'intake']):
                return True
        
        # If asking "which universities" or "list universities" without program context, it's FAQ
        if re.search(r'(?:which|what|list|show).*universities', user_lower):
            # Check if it's asking about partnerships or general info (FAQ)
            if any(term in user_lower for term in ['partner', 'work', 'connected', 'have', 'you']):
                return False  # FAQ question
            # Otherwise, if no program context, still treat as FAQ (general question)
            if not (student_state.major or student_state.degree_level):
                return False  # General question, not program-specific
        
        return False
    
    def _build_single_lead_question(self, student_state: StudentProfileState, audience: Optional[str] = None) -> Optional[str]:
        """
        Build a single lead collection question with priority.
        
        For partners (audience='partner'):
        - Ask for contact (WhatsApp/WeChat/email) if missing
        
        For students (default):
        1. Nationality (if missing)
        2. Contact info (if missing)
        3. Degree level or major (if missing)
        
        Returns None if all important fields are collected.
        """
        # Partner lead collection
        if audience == 'partner':
            missing_contact = not (student_state.phone or student_state.email or 
                                   student_state.whatsapp or student_state.wechat)
            if missing_contact:
                return "What's your WhatsApp/WeChat (or email) so we can connect you with our partnership team?"
            return None
        
        # Student lead collection - ask for 3-5 fields only
        missing_nationality = not student_state.nationality
        missing_degree = not student_state.degree_level
        missing_major = not student_state.major
        missing_intake = not student_state.intake_term
        # Note: We don't ask for contact info here - only after user expresses intent to apply
        
        # Build question asking for 3-5 missing fields
        missing_fields = []
        if missing_nationality:
            missing_fields.append("nationality")
        if missing_degree:
            missing_fields.append("degree level")
        if missing_major:
            missing_fields.append("major")
        if missing_intake:
            missing_fields.append("intake term")
        
        if len(missing_fields) > 0:
            if len(missing_fields) == 1:
                field_name = missing_fields[0]
                if field_name == "nationality":
                    return "Which country are you from?"
                elif field_name == "degree level":
                    return "Which degree level are you interested in (Bachelor/Master/PhD/Language)?"
                elif field_name == "major":
                    return "Which major or subject are you interested in?"
                elif field_name == "intake term":
                    return "When would you like to start (March or September)?"
            elif len(missing_fields) <= 3:
                fields_str = ", ".join(missing_fields[:-1]) + f", and {missing_fields[-1]}"
                return f"To help you better, could you share your {fields_str}?"
            else:
                # Too many missing - ask for top 3
                top_3 = missing_fields[:3]
                fields_str = ", ".join(top_3[:-1]) + f", and {top_3[-1]}"
                return f"To help you better, could you share your {fields_str}?"
        
        # All important fields collected
        return None
    
    def _provide_csca_fallback_answer(self, user_message: str) -> str:
        """Provide short practical CSCA fallback answer when RAG context is empty or low confidence"""
        user_lower = user_message.lower()
        
        # Post-study / After graduation
        if any(term in user_lower for term in ['after', 'post', 'completing', 'graduation', 'graduate', 'finish', 'complete studies', 'what happens']):
            return """After completing studies under the CSCA scholarship, you'll graduate with your degree. The scholarship typically ends upon graduation. Your next steps depend on your goals: you can return to your home country, pursue further studies, or explore legal work opportunities in China (work permit requirements vary)."""
        
        # General CSCA information
        else:
            return """CSCA (China Scholastic Competency Assessment) is used by some Chinese universities as part of their international student admission and scholarship evaluation process. Requirements vary by university, major, and degree level."""
    
    def _build_csca_lead_question(self, student_state: StudentProfileState) -> str:
        """Build CSCA-specific lead question asking for degree level, scholarship category, and university"""
        missing_fields = []
        if not student_state.degree_level:
            missing_fields.append("degree level")
        if not student_state.preferred_universities:
            missing_fields.append("target university")
        
        if len(missing_fields) == 1:
            if missing_fields[0] == "degree level":
                return "Which degree level are you interested in (Bachelor/Master/PhD)?"
            else:
                return "Which university are you considering?"
        elif len(missing_fields) >= 2:
            fields_str = ", ".join(missing_fields[:-1]) + f", and {missing_fields[-1]}"
            return f"To provide more specific guidance, could you share your {fields_str}?"
        else:
            # All fields collected, ask about scholarship category
            return "Are you specifically interested in CSCA scholarship programs, or other Chinese Government Scholarship options?"
    
    def _provide_csca_domain_knowledge(self, user_message: str) -> str:
        """Provide helpful CSCA domain knowledge answer when RAG/Tavily have no results"""
        user_lower = user_message.lower()
        
        # Post-study / After graduation
        if any(term in user_lower for term in ['after', 'post', 'completing', 'graduation', 'graduate', 'finish', 'complete studies', 'what happens']):
            return """After completing studies under the CSCA scholarship:

• **Degree Recognition**: Your degree from a Chinese university is recognized internationally. You'll receive your degree certificate and graduation certificate upon successful completion.

• **Career Options**:
  - Return to your home country: Many graduates return and work in their home country, where their Chinese degree and language skills are valuable.
  - Work in China: Graduates can apply for work permits in China, though this requires employer sponsorship and meeting specific requirements.
  - Further Studies: Some graduates pursue Master's or PhD programs in China or other countries.
  - International Companies: Your bilingual skills and international experience are valuable for multinational companies.

• **Scholarship Obligations**: CSCA scholarship recipients typically need to maintain good academic performance and follow university regulations throughout their studies.

• **Alumni Network**: Many universities have active international student alumni networks that can help with career connections.

• **Documentation**: Keep all your academic documents, transcripts, and certificates safe - you'll need them for future applications and job opportunities.

Would you like to know more about work opportunities in China after graduation, or about further studies?"""
        
        # CSCA exam / registration
        elif any(term in user_lower for term in ['csca exam', 'take csca', 'csca registration', 'register', 'how to apply csca']):
            return """CSCA (China Scholastic Competency Assessment) Registration:

• **Purpose**: CSCA is an assessment exam that some universities use as part of their admission process for international students.

• **Registration**: Students typically register online through the official CSCA website (www.csca.cn) or as directed by their target universities.

• **Requirements**: You'll need to create an account, upload required documents (photo, ID), choose your test session and subjects, and pay the exam fee.

• **Timing**: Registration deadlines and exam dates vary by university and intake. Check with your target universities or MalishaEdu for specific dates.

• **Support**: MalishaEdu can guide you through the CSCA registration process and help ensure you meet all requirements.

Would you like to know which universities require CSCA, or do you have a specific university in mind?"""
        
        # General CSCA information
        else:
            return """CSCA (China Scholastic Competency Assessment) Information:

• **What is CSCA**: CSCA is an assessment exam used by some Chinese universities as part of their international student admission process.

• **Who needs it**: Not all universities require CSCA. Requirements vary by university, major, and degree level. Some universities use it for scholarship eligibility assessment.

• **Exam Structure**: The exam typically assesses academic competency and may include subject-specific tests depending on your chosen major.

• **Scholarship Connection**: Some universities use CSCA results as part of their scholarship evaluation process.

• **Support**: MalishaEdu can help you determine if your target universities require CSCA and guide you through the registration and preparation process.

Would you like to know which universities require CSCA, or do you have specific questions about the exam format or registration?"""
    
    def _provide_general_knowledge_answer(self, user_message: str) -> str:
        """Provide helpful general knowledge answer when RAG/Tavily have no results"""
        user_lower = user_message.lower()
        
        # Travel/Transportation
        if any(term in user_lower for term in ['travel', 'transportation', 'transport', 'getting around']):
            return """Travel options in China for international students:

• **High-speed trains (高铁)**: Fast, convenient, and affordable for intercity travel. Popular for trips between major cities like Beijing, Shanghai, Guangzhou.

• **Metro/Subway**: Available in major cities (Beijing, Shanghai, Guangzhou, Shenzhen, etc.). Very affordable and efficient for daily commuting.

• **Buses**: Extensive network in all cities. Very cheap (usually 1-2 CNY per ride).

• **Taxis/Ride-hailing**: Didi (similar to Uber) is widely used. Affordable for short distances.

• **Air travel**: For long distances, domestic flights are reasonably priced.

• **Bicycles/E-bikes**: Popular for short distances, especially with shared bike services (Mobike, Ofo, etc.).

Most students use a combination of metro, buses, and ride-hailing apps for daily travel. High-speed trains are great for exploring other cities during holidays.

Is there a specific city or travel scenario you'd like to know more about?"""
        
        # Food/Halal
        elif any(term in user_lower for term in ['food', 'halal', 'eating', 'meal', 'dietary']):
            return """Food options in China for international students:

• **University cafeterias**: Most universities have cafeterias with diverse options, including halal sections in many institutions.

• **Halal food**: Available in major cities, especially in areas with Muslim communities. Many universities in cities like Beijing, Xi'an, and Guangzhou have halal dining options.

• **Restaurants**: Wide variety from local Chinese cuisine to international options (Western, Middle Eastern, etc.).

• **Cooking**: Most dormitories allow cooking, and students often prepare meals in shared kitchens.

• **Food delivery**: Apps like Meituan and Ele.me offer convenient food delivery with many halal and international options.

• **Cost**: Eating at university cafeterias is very affordable (typically 10-30 CNY per meal). Restaurants vary widely in price.

Would you like to know about halal food availability in a specific city or university?"""
        
        # Safety
        elif any(term in user_lower for term in ['safety', 'safe', 'security', 'crime']):
            return """Safety in China for international students:

• **General safety**: China is generally very safe for international students. Major cities have low crime rates compared to many countries.

• **Campus security**: Universities have 24/7 security, controlled access to dormitories, and security personnel on campus.

• **Public safety**: Public transportation, streets, and public spaces are generally safe, even at night in most areas.

• **Emergency services**: Police (110), medical (120), and fire (119) services are available nationwide.

• **Precautions**: As in any country, students should take normal precautions: be aware of surroundings, keep valuables secure, and follow local laws and regulations.

• **Support**: Universities provide orientation and safety briefings for new international students.

Is there a specific safety concern you'd like to know more about?"""
        
        # Accommodation
        elif any(term in user_lower for term in ['accommodation', 'dormitory', 'dorm', 'housing', 'where will i live']):
            return """Accommodation options in China:

• **University dormitories**: Most universities provide international student dormitories with options for single or shared rooms. Facilities typically include basic furniture, internet, and shared bathrooms/kitchens.

• **Room types**: Single rooms, double rooms, or shared rooms (2-4 students). Prices vary by city and university.

• **Facilities**: Common facilities include laundry rooms, shared kitchens, study areas, and sometimes gyms.

• **Cost**: Dormitory fees range from 3,000-12,000 CNY/year depending on city and room type.

• **Off-campus**: Some students choose to rent apartments off-campus, though this is usually more expensive and requires more paperwork.

• **MalishaEdu support**: We can help you with accommodation arrangements and provide guidance on the best options for your budget and preferences.

Would you like to know about accommodation at a specific university or city?"""
        
        # Default helpful response
        else:
            return f"I'd be happy to help you with information about {user_message}. While I don't have specific details in our knowledge base right now, I can provide general guidance. Could you tell me more about what specifically you'd like to know?"
    
    def _build_faq_cta(self, student_state: StudentProfileState) -> str:
        """Build CTA after FAQ answer to collect missing lead fields (backward compatibility - uses single question)"""
        lead_question = self._build_single_lead_question(student_state)
        if lead_question:
            return lead_question
        return "If you'd like personalized guidance for your specific situation, please share your study preferences."
    
    def _get_major_ids_by_fuzzy_match(self, major_name: str, degree_level: Optional[str] = None) -> List[int]:
        """
        Helper to get major IDs by fuzzy matching major name.
        Returns list of major IDs that match the query.
        """
        if not major_name:
            return []
        
        # Use fuzzy matching
        matched_name, similar_matches = self._fuzzy_match_major(major_name, degree_level=degree_level, threshold=0.4)
        
        # Collect all matching major IDs from all_majors array
        major_ids = []
        major_name_lower = major_name.lower()
        
        for major in self.all_majors:
            if degree_level and major.get("degree_level"):
                if degree_level.lower() not in major["degree_level"].lower():
                    continue
            
            major_db_name = major["name"].lower()
            # Check if it's in the matched list
            if matched_name and major["name"] == matched_name:
                major_ids.append(major["id"])
            elif major["name"] in similar_matches:
                major_ids.append(major["id"])
            # Also check for substring match
            elif major_name_lower in major_db_name or major_db_name in major_name_lower:
                major_ids.append(major["id"])
        
        # Remove duplicates
        return list(set(major_ids))
    
    def get_matching_intakes(self, student_state, limit=30):
        """
        DB-lite querying: get matching intakes using DBQueryService.
        Filters: degree_level, major (fuzzy), intake_term + inferred intake_year, teaching_language, upcoming deadlines.
        Fix 5: Improved major fuzzy matching for queries like "International trade bachelor earliest intake"
        """
        from datetime import datetime, timezone
        from app.models import IntakeTerm, ProgramIntake, Major, University
        
        current_date = datetime.now(timezone.utc).date()
        
        # Infer intake_year if not set
        inferred_year = self.infer_intake_year(
            student_state.intake_term,
            student_state.intake_year,
            current_date
        )
        
        # Convert intake_term to enum if available
        intake_term_enum = None
        if student_state.intake_term:
            try:
                intake_term_enum = IntakeTerm(student_state.intake_term)
            except:
                pass
        
        # Fix 5: Better major fuzzy matching - find matching majors first
        major_ids = None
        if student_state.major:
            major_ids = self._get_major_ids_by_fuzzy_match(student_state.major, student_state.degree_level)
            if major_ids:
                print(f"DEBUG: Major fuzzy match - query='{student_state.major}', matched_ids={major_ids[:10]}")
        
        # Build query
        query = self.db.query(ProgramIntake).join(Major).join(University).filter(
            University.is_partner == True,
            ProgramIntake.application_deadline > current_date
        )
        
        if student_state.degree_level:
            query = query.filter(Major.degree_level == student_state.degree_level)
        if major_ids:
            query = query.filter(Major.id.in_(major_ids))
        if intake_term_enum:
            query = query.filter(ProgramIntake.intake_term == intake_term_enum)
        if inferred_year or student_state.intake_year:
            query = query.filter(ProgramIntake.intake_year == (inferred_year or student_state.intake_year))
        
        intakes = query.limit(limit).all()
        print(f"DEBUG: DB query filters - degree_level={student_state.degree_level}, intake_term={intake_term_enum}, intake_year={inferred_year or student_state.intake_year}, major_ids={major_ids[:5] if major_ids else None}, count={len(intakes)}")
        
        return intakes
    
    def summarize_tuition(self, intakes) -> str:
        """
        Summarize tuition from intakes into a range string.
        Normalizes tuition_per_semester vs tuition_per_year into comparable display.
        Returns: "6,000–12,000 CNY/semester" or mixed year/semester if needed.
        """
        if not intakes:
            return "Tuition: Not available"
        
        tuitions = []
        currencies = set()
        periods = []
        
        for intake in intakes:
            currency = getattr(intake, 'currency', 'CNY') or 'CNY'
            currencies.add(currency)
            
            if intake.tuition_per_year:
                tuitions.append(float(intake.tuition_per_year))
                periods.append('year')
            elif intake.tuition_per_semester:
                tuitions.append(float(intake.tuition_per_semester))
                periods.append('semester')
        
        if not tuitions:
            return "Tuition: Not available"
        
        # Normalize to same period if mixed
        if 'year' in periods and 'semester' in periods:
            # Convert all to per-year for comparison
            normalized = []
            for i, intake in enumerate(intakes):
                if intake.tuition_per_year:
                    normalized.append(float(intake.tuition_per_year))
                elif intake.tuition_per_semester:
                    normalized.append(float(intake.tuition_per_semester) * 2)
            tuitions = normalized
            period = 'year'
        else:
            period = periods[0] if periods else 'year'
        
        min_tuition = min(tuitions)
        max_tuition = max(tuitions)
        currency = list(currencies)[0] if currencies else 'CNY'
        
        if min_tuition == max_tuition:
            return f"Tuition: {int(min_tuition):,.0f} {currency}/{period}"
        else:
            return f"Tuition: {int(min_tuition):,.0f}–{int(max_tuition):,.0f} {currency}/{period}"
    
    def pick_top_options(self, intakes, k=3, sort_by_cheapest=False):
        """
        Pick top k options from intakes.
        Sort by earliest deadlines (default) or cheapest tuition (if sort_by_cheapest=True).
        Returns list of formatted strings: "University – Program – teaching language – tuition – deadline"
        """
        if not intakes:
            return []
        
        from datetime import datetime, timezone
        
        current_date = datetime.now(timezone.utc).date()
        
        # Sort
        if sort_by_cheapest:
            def get_tuition(intake):
                if intake.tuition_per_year:
                    return float(intake.tuition_per_year)
                elif intake.tuition_per_semester:
                    return float(intake.tuition_per_semester) * 2
                return float('inf')
            sorted_intakes = sorted(intakes, key=get_tuition)
        else:
            def get_deadline_score(intake):
                if intake.application_deadline:
                    days_until = (intake.application_deadline.date() - current_date).days
                    return days_until if days_until > 0 else float('inf')
                return float('inf')
            sorted_intakes = sorted(intakes, key=get_deadline_score)
        
        # Format top k
        options = []
        for intake in sorted_intakes[:k]:
            uni = intake.university
            major = intake.major
            teaching_lang = intake.teaching_language or (major.teaching_language if major else 'N/A')
            
            # Format tuition
            currency = getattr(intake, 'currency', 'CNY') or 'CNY'
            tuition_str = "Not provided"
            if intake.tuition_per_year:
                tuition_str = f"{intake.tuition_per_year:,.0f} {currency}/year"
            elif intake.tuition_per_semester:
                tuition_str = f"{intake.tuition_per_semester:,.0f} {currency}/semester"
            
            # Format deadline
            deadline = intake.application_deadline.strftime('%Y-%m-%d') if intake.application_deadline else 'N/A'
            
            options.append(
                f"{uni.name} – {major.name if major else 'N/A'} ({teaching_lang}-taught) – "
                f"{tuition_str} – Deadline: {deadline}"
            )
        
        return options
    
    def _query_language_program_costs(self, student_state, intake_term_enum=None, intent='fees_only', limit=3):
        """
        Query DB for Language program costs.
        Returns list of ProgramIntake objects sorted by intent (cheapest for fees_compare, nearest deadline for fees_only).
        """
        from datetime import datetime, timezone
        from app.models import IntakeTerm
        
        current_date = datetime.now(timezone.utc).date()
        
        # Build query
        query = self.db.query(ProgramIntake).join(Major).join(University).filter(
            University.is_partner == True,
            ProgramIntake.application_deadline > current_date
        )
        
        # Filter by degree level (Language)
        query = query.filter(
            (Major.degree_level == 'Language') | (Major.degree_level == 'Language Program')
        )
        
        # Filter by intake_term if provided
        if intake_term_enum:
            query = query.filter(ProgramIntake.intake_term == intake_term_enum)
        
        # Filter by intake_year if available
        if student_state.intake_year:
            query = query.filter(ProgramIntake.intake_year == student_state.intake_year)
        
        intakes = query.all()
        
        if not intakes:
            return []
        
        # Sort based on intent
        if intent == 'fees_compare':
            # Sort by cheapest tuition
            def get_tuition(intake):
                if intake.tuition_per_year:
                    return float(intake.tuition_per_year)
                elif intake.tuition_per_semester:
                    return float(intake.tuition_per_semester) * 2  # Normalize to per-year
                return float('inf')
            intakes = sorted(intakes, key=get_tuition)
        else:
            # Sort by nearest deadline
            def get_deadline_score(intake):
                if intake.application_deadline:
                    days_until = (intake.application_deadline.date() - current_date).days
                    return days_until if days_until > 0 else float('inf')
                return float('inf')
            intakes = sorted(intakes, key=get_deadline_score)
        
        return intakes[:limit]
    
    def _extract_last_list_ctx(self, conversation_history: List[Dict[str, str]]) -> Optional[Dict[str, Any]]:
        """
        Extract the most recent LIST_CTX marker from conversation history.
        Returns dict with intent, filters, offset, limit, or None if not found.
        """
        import json
        import re
        
        # Search backwards through conversation history
        for msg in reversed(conversation_history):
            content = msg.get('content', '')
            # Look for LIST_CTX marker
            pattern = r'<!--LIST_CTX:(\{.*?\})-->'
            match = re.search(pattern, content)
            if match:
                try:
                    ctx_data = json.loads(match.group(1))
                    print(f"DEBUG: Extracted LIST_CTX: {ctx_data}")
                    return ctx_data
                except json.JSONDecodeError:
                    continue
        return None
    
    def _format_documents_and_requirements(self, program_intake) -> str:
        """
        Format documents and requirements deterministically from ProgramIntake.
        Returns formatted string with both documents_required and structured fields.
        """
        parts = []
        
        # (A) Documents from documents_required field
        if program_intake.documents_required:
            parts.append(f"Documents Required: {program_intake.documents_required}")
        
        # (B) Requirements block from structured fields
        req_parts = []
        
        # Bank statement
        if hasattr(program_intake, 'bank_statement_required') and program_intake.bank_statement_required:
            bank_amount = getattr(program_intake, 'bank_statement_amount', None)
            bank_currency = getattr(program_intake, 'bank_statement_currency', 'CNY')
            if bank_amount:
                req_parts.append(f"Bank Statement: {bank_amount} {bank_currency}")
            else:
                req_parts.append("Bank Statement: Required")
        
        # HSK requirements
        if hasattr(program_intake, 'hsk_required') and program_intake.hsk_required:
            hsk_level = getattr(program_intake, 'hsk_level', None)
            hsk_min_score = getattr(program_intake, 'hsk_min_score', None)
            hsk_str = "HSK Required"
            if hsk_level:
                hsk_str += f" (Level {hsk_level})"
            if hsk_min_score:
                hsk_str += f", Min Score: {hsk_min_score}"
            req_parts.append(hsk_str)
        
        # English test requirements
        if hasattr(program_intake, 'english_test_required') and program_intake.english_test_required:
            eng_note = getattr(program_intake, 'english_test_note', None)
            if eng_note:
                req_parts.append(f"English Test Required: {eng_note}")
            else:
                req_parts.append("English Test Required: Yes")
        
        if req_parts:
            parts.append("Requirements: " + ", ".join(req_parts))
        
        return "\n".join(parts) if parts else "Documents/Requirements: Not specified in database"
    
    def _format_list_page(self, intakes: List[ProgramIntake], offset: int, limit: int, total: int, 
                         sort_by_fees: bool = False) -> str:
        """
        Format a deterministic list page response for fee comparison queries.
        Returns formatted string with LIST_CTX marker embedded.
        """
        import json
        from datetime import datetime
        
        if not intakes:
            return "No matching programs found."
        
        # Sort if needed
        if sort_by_fees:
            def get_tuition(intake):
                if intake.tuition_per_year:
                    return float(intake.tuition_per_year)
                elif intake.tuition_per_semester:
                    return float(intake.tuition_per_semester) * 2
                return float('inf')
            intakes = sorted(intakes, key=get_tuition)
        
        # Limit to page size
        page_intakes = intakes[offset:offset + limit]
        
        response_parts = []
        start_num = offset + 1
        end_num = offset + len(page_intakes)
        
        if offset == 0:
            response_parts.append(f"Here are the top {len(page_intakes)} universities with matching programs:\n")
        else:
            response_parts.append(f"Showing {start_num}–{end_num} of {total} universities:\n")
        
        for idx, intake in enumerate(page_intakes, 1):
            uni = intake.university
            major = intake.major
            teaching_lang = intake.teaching_language or (major.teaching_language if major else 'N/A')
            
            # Format tuition
            currency = getattr(intake, 'currency', 'CNY') or 'CNY'
            tuition_str = "Not provided"
            if intake.tuition_per_year:
                tuition_str = f"{intake.tuition_per_year} {currency}/year"
            elif intake.tuition_per_semester:
                tuition_str = f"{intake.tuition_per_semester} {currency}/semester"
            
            # Format application fee
            app_fee = intake.application_fee or 0
            app_fee_str = f"{app_fee} {currency}" if app_fee else "Not provided"
            
            # Format deadline
            deadline = intake.application_deadline.strftime('%Y-%m-%d') if intake.application_deadline else 'N/A'
            
            response_parts.append(
                f"{idx}. {uni.name} – {major.name if major else 'N/A'} – {teaching_lang}-taught – "
                f"Tuition: {tuition_str} – App Fee: {app_fee_str} – Deadline: {deadline}"
            )
        
        if total > offset + len(page_intakes):
            remaining = total - (offset + len(page_intakes))
            response_parts.append(f"\nShowing {start_num}–{end_num} of {total}. Say 'show more' for next page ({remaining} more available).")
        
        # Embed LIST_CTX marker
        list_ctx = {
            "intent": "fees_compare",
            "filters": {
                "degree_level": page_intakes[0].major.degree_level if page_intakes and page_intakes[0].major else None,
                "intake_term": page_intakes[0].intake_term.value if page_intakes and page_intakes[0].intake_term else None,
                "intake_year": page_intakes[0].intake_year if page_intakes else None
            },
            "offset": offset,
            "limit": limit
        }
        list_ctx_marker = f"<!--LIST_CTX:{json.dumps(list_ctx)}-->"
        response_parts.append(f"\n{list_ctx_marker}")
        
        return "\n".join(response_parts)
    
    def generate_response(
        self,
        user_message: str,
        conversation_history: List[Dict[str, str]],
        device_fingerprint: Optional[str] = None,
        chat_session_id: Optional[str] = None,
        use_db: bool = False
    ) -> Dict[str, Any]:
        """
        Generate response using DB-first approach with StudentProfileState
        
        Args:
            user_message: Current user message
            conversation_history: List of message dicts with 'role' and 'content'
            device_fingerprint: Device fingerprint (backward compatibility)
            chat_session_id: Per-chat session identifier
            use_db: If False (stranger), skip DB queries and use only RAG/Web. If True (lead collected), allow DB queries.
        
        Returns: {
            'response': str,
            'db_context': str,
            'rag_context': Optional[str],
            'tavily_context': Optional[str],
            'lead_collected': bool
        }
        """
        # Get current date for date awareness
        from datetime import datetime, timezone
        current_date = datetime.now(timezone.utc)
        current_year = current_date.year
        current_month = current_date.month
        current_date_str = current_date.strftime("%Y-%m-%d")
        
        # Reset pagination state for new queries (not "show more")
        user_msg_normalized = user_message.lower().strip()
        is_pagination = self._is_pagination_command(user_message)
        if not is_pagination:
            # Only reset if this is clearly a new query (not a follow-up)
            # Follow-up resolver will handle short follow-ups
            is_short_followup = len(user_message.split()) <= 5
            contains_only_fields = (
                any(term in user_msg_normalized for term in ['march', 'september', '2026', '2027', '2028']) or
                any(term in user_msg_normalized for term in ['bangladesh', 'pakistan', 'india', 'kazakhstan', 'uzbekistan']) or
                any(term in user_msg_normalized for term in ['guangzhou', 'beijing', 'shanghai', 'harbin', 'wuhan'])
            )
            if not (is_short_followup and contains_only_fields):
                # New query - reset state
                self.last_list_results = []
                self.last_list_offset = 0
                self.last_intent = None
                self.last_state = None
        
        # Fix 6: Handle "show more" pagination using instance variables (before LIST_CTX)
        if is_pagination:
            print(f"DEBUG: Pagination command detected")
            
            # First try instance variables (simpler, for cost queries)
            if self.last_list_results:
                self.last_list_offset += 5  # Next batch of 5
                next_batch = self.last_list_results[self.last_list_offset:self.last_list_offset + 5]
                
                if next_batch:
                    print(f"DEBUG: Returning next batch from instance variables: offset={self.last_list_offset}, size={len(next_batch)}")
                    # Format response
                    response_parts = []
                    for idx, intake in enumerate(next_batch, 1):
                        uni = intake.university
                        major = intake.major
                        teaching_lang = intake.teaching_language or (major.teaching_language if major else 'N/A')
                        currency = getattr(intake, 'currency', 'CNY') or 'CNY'
                        tuition_str = "Not provided"
                        if intake.tuition_per_year:
                            tuition_str = f"{intake.tuition_per_year} {currency}/year"
                        elif intake.tuition_per_semester:
                            tuition_str = f"{intake.tuition_per_semester} {currency}/semester"
                        app_fee = intake.application_fee or 0
                        app_fee_str = f"{app_fee} {currency}" if app_fee else "Not provided"
                        deadline = intake.application_deadline.strftime('%Y-%m-%d') if intake.application_deadline else 'N/A'
                        response_parts.append(
                            f"{idx}. {uni.name} – {major.name if major else 'N/A'} – {teaching_lang}-taught – "
                            f"Tuition: {tuition_str} – App Fee: {app_fee_str} – Deadline: {deadline}"
                        )
                    
                    if self.last_list_offset + 5 < len(self.last_list_results):
                        response_parts.append(f"\nSay 'show more' for next page ({len(self.last_list_results) - self.last_list_offset - 5} more available).")
                    response_parts.append("\nIf you sign up (/signup), I can save these and give exact total cost + document checklist.")
                    
                    return {
                        'response': "\n".join(response_parts),
                        'db_context': '',
                        'rag_context': None,
                        'tavily_context': None,
                        'lead_collected': use_db,
                        'show_lead_form': False,
                        'lead_form_prefill': {}
                    }
                else:
                    return {
                        'response': "No more results. You've reached the end. If you sign up (/signup), I can save these and give exact total cost + document checklist.",
                        'db_context': '',
                        'rag_context': None,
                        'tavily_context': None,
                        'lead_collected': use_db,
                        'show_lead_form': False,
                        'lead_form_prefill': {}
                    }
            
            # Fallback to LIST_CTX handler
            list_ctx = self._extract_last_list_ctx(conversation_history)
            
            if list_ctx:
                # Rerun the same DB query with offset += limit
                filters = list_ctx.get('filters', {})
                old_offset = list_ctx.get('offset', 0)
                limit = list_ctx.get('limit', 12)
                offset = old_offset + limit
                print(f"DEBUG: Pagination offset change: {old_offset} -> {offset} (limit={limit})")
                
                # Query program intakes with filters
                query = self.db.query(ProgramIntake).join(Major).join(University).filter(
                    University.is_partner == True
                )
                
                if filters.get('degree_level'):
                    query = query.filter(Major.degree_level == filters['degree_level'])
                if filters.get('intake_term'):
                    from app.models import IntakeTerm
                    intake_term_enum = IntakeTerm(filters['intake_term'])
                    query = query.filter(ProgramIntake.intake_term == intake_term_enum)
                if filters.get('intake_year'):
                    query = query.filter(ProgramIntake.intake_year == filters['intake_year'])
                
                # Filter by upcoming deadlines
                query = query.filter(ProgramIntake.application_deadline > current_date.date())
                
                total = query.count()
                intakes = query.offset(offset).limit(limit).all()
                
                if intakes:
                    print(f"DEBUG: Returning list page offset={offset} size={len(intakes)} total={total}")
                    response_text = self._format_list_page(intakes, offset, limit, total, sort_by_fees=True)
                    return {
                        'response': response_text,
                        'db_context': '',
                        'rag_context': None,
                        'tavily_context': None,
                        'lead_collected': use_db,
                        'show_lead_form': False,
                        'lead_form_prefill': {}
                    }
                else:
                    return {
                        'response': "No more results. You've reached the end.",
                        'db_context': '',
                        'rag_context': None,
                        'tavily_context': None,
                        'lead_collected': use_db,
                        'show_lead_form': False,
                        'lead_form_prefill': {}
                    }
            else:
                return {
                    'response': "Sure — which intake and degree level should I list?",
                    'db_context': '',
                    'rag_context': None,
                    'tavily_context': None,
                    'lead_collected': use_db,
                    'show_lead_form': False,
                    'lead_form_prefill': {}
                }
        
        # TEMPORARY LOGGING: Print conversation history to verify it's being passed correctly
        print(f"\n{'='*80}")
        print(f"SalesAgent.generate_response called")
        print(f"chat_session_id: {chat_session_id}")
        print(f"device_fingerprint: {device_fingerprint}")
        print(f"conversation_history length: {len(conversation_history)}")
        print(f"conversation_history content:")
        for i, msg in enumerate(conversation_history):
            role = msg.get('role', 'unknown')
            content = msg.get('content', '')[:100]  # First 100 chars
            print(f"  [{i}] {role}: {content}...")
        print(f"current user_message: {user_message[:100]}...")
        print(f"{'='*80}\n")
        
        # Step 0: Extract StudentProfileState from conversation history (ONLY last 12 messages - ignore older history)
        conversation_slice = conversation_history[-12:] if conversation_history else []
        student_state = self.extract_student_profile_state(conversation_slice)
        
        # Step 0.1: Apply deterministic intake year inference (A)
        if not student_state.intake_year and student_state.intake_term:
            inferred_year = self.infer_intake_year(
                student_state.intake_term,
                student_state.intake_year,
                current_date
            )
            if inferred_year:
                student_state.intake_year = inferred_year
                print(f"DEBUG: Inferred intake_year={inferred_year} from intake_term={student_state.intake_term} (current_date={current_date_str})")
        
        # Step 0.25: Compute known and missing fields from StudentProfileState
        profile_info = self._compute_known_and_missing_fields(student_state)
        known_fields = profile_info["known_fields"]
        missing_fields = profile_info["missing_fields"]
        
        # Step 0.5: Split DB access - always allow reads, only check Lead for writes
        # Fix 1: allow_db_read = True (always), allow_lead_write = (lead exists or user provided contact info)
        allow_db_read = True  # Always allow DB reads for partner universities/majors/intakes
        
        # Check if lead exists for write operations (creating/updating Lead rows)
        allow_lead_write = False
        if use_db:
            allow_lead_write = True
        else:
            # Check if lead exists for chat_session_id (new way) or device_fingerprint (legacy)
            if chat_session_id:
                lead = self.db.query(Lead).filter(Lead.chat_session_id == chat_session_id).first()
                allow_lead_write = lead is not None
            elif device_fingerprint:
                lead = self.db.query(Lead).filter(Lead.device_fingerprint == device_fingerprint).first()
                allow_lead_write = lead is not None
        
        # Also check if user provided contact info (for lead creation)
        has_contact_info = (
            student_state.phone or 
            student_state.email or 
            student_state.whatsapp or 
            student_state.wechat
        )
        if has_contact_info and student_state.nationality and (student_state.degree_level or student_state.major):
            allow_lead_write = True
        
        lead_collected = allow_lead_write  # Alias for backward compatibility
        print(f"DEBUG: DB access - allow_db_read={allow_db_read}, allow_lead_write={allow_lead_write}")
        
        # Step 0.6: Follow-up resolver (Fix 3)
        # Check if user_message is short and only provides missing fields
        user_msg_lower = user_message.lower().strip()
        is_short_followup = len(user_message.split()) <= 5  # Short message
        
        # Check if message only contains: intake term, year, nationality, city
        contains_only_fields = (
            any(term in user_msg_lower for term in ['march', 'september', '2026', '2027', '2028']) or
            any(term in user_msg_lower for term in ['bangladesh', 'pakistan', 'india', 'kazakhstan', 'uzbekistan']) or
            any(term in user_msg_lower for term in ['guangzhou', 'beijing', 'shanghai', 'harbin', 'wuhan'])
        )
        
        # Check if prior assistant turn asked for missing field
        prior_asked_for_missing = False
        if conversation_slice:
            for msg in reversed(conversation_slice[-3:]):  # Check last 3 messages
                if msg.get('role') == 'assistant':
                    content = msg.get('content', '').lower()
                    # Check if assistant asked for intake/nationality/city
                    if any(phrase in content for phrase in ['intake', 'nationality', 'city', 'which city', 'where', 'when']):
                        prior_asked_for_missing = True
                        break
        
        # Reuse last intent and state if conditions met
        if is_short_followup and contains_only_fields and prior_asked_for_missing and self.last_intent:
            print(f"DEBUG: Follow-up resolver triggered - reusing last_intent={self.last_intent}")
            sales_intent = self.last_intent
            # Merge new info into last_state
            if self.last_state:
                # Update last_state with new info from current student_state
                if student_state.intake_term and not self.last_state.intake_term:
                    self.last_state.intake_term = student_state.intake_term
                if student_state.intake_year and not self.last_state.intake_year:
                    self.last_state.intake_year = student_state.intake_year
                if student_state.nationality and not self.last_state.nationality:
                    self.last_state.nationality = student_state.nationality
                if student_state.city and not self.last_state.city:
                    self.last_state.city = student_state.city
                # Use merged state
                student_state = self.last_state
        else:
            # Detect intent normally
            sales_intent = self._detect_sales_intent(user_message)
            print(f"DEBUG: Sales intent detected: {sales_intent}")
        
        # Step 0.6.1: Detect if this is a simple informational query (listing majors, basic info) vs personalized recommendation
        user_msg_lower = user_message.lower()
        is_simple_info_query = any(phrase in user_msg_lower for phrase in [
            'what majors', 'which majors', 'list majors', 'show majors', 'available majors',
            'what programs', 'which programs', 'list programs', 'show programs', 'available programs',
            'what does', 'what offers', 'does offer', 'offers', 'has'
        ]) and any(term in user_msg_lower for term in ['university', 'college', 'institute'])
        
        # Step 0.7: Early detection of non-partner universities mentioned in user message
        # Extract potential university names from user message
        non_partner_university_mentioned = None
        import re
        # Common non-partner universities that users might mention
        known_non_partners = ['peking university', 'pku', 'tsinghua', 'tsinghua university', 'beijing university', 
                             'fudan', 'fudan university', 'shanghai jiao tong', 'sjtu', 'zhejiang university', 'zju']
        
        for non_partner in known_non_partners:
            if non_partner in user_msg_lower:
                # Verify it's actually not a partner
                is_partner, _ = self._is_malishaedu_partner_university(non_partner)
                if not is_partner:
                    non_partner_university_mentioned = non_partner
                    break
        
        # Also try to extract university name from message using patterns
        if not non_partner_university_mentioned:
            # Improved patterns to catch university names in various positions
            uni_patterns = [
                r'(?:want to study|interested in|apply to|apply for|study at|study in|want to go to|looking for)\s+([A-Z][a-zA-Z\s&]+(?:University|College|Institute|Tech|Technology))',  # Explicit study intent
                r'(?:at|in|to|for)\s+([A-Z][a-zA-Z\s&]+(?:University|College|Institute|Tech|Technology))',  # After preposition (but not "from")
                r'([A-Z][a-zA-Z\s&]+(?:University|College|Institute|Tech|Technology)\s+(?:for|at|in))',  # Before preposition
            ]
            for pattern in uni_patterns:
                matches = re.finditer(pattern, user_message, re.IGNORECASE)
                for match in matches:
                    # Check if this is "I am from [University]" - skip if so
                    match_start = match.start()
                    context_before = user_message[max(0, match_start-50):match_start].lower()
                    # Skip if preceded by "I am from", "I'm from", "studied at", "studying at", "student of", "graduate of"
                    if any(phrase in context_before for phrase in ['i am from', "i'm from", 'studied at', 'studying at', 'student of', 'graduate of', 'graduated from']):
                        continue
                    
                    potential_uni = match.group(1).strip() if match.groups() else match.group(0).strip()
                    # Clean up the extracted name (remove trailing prepositions)
                    potential_uni = re.sub(r'\s+(for|at|in|from|to)$', '', potential_uni, flags=re.IGNORECASE).strip()
                    if potential_uni and len(potential_uni) > 5:  # Minimum length check
                        is_partner, _ = self._is_malishaedu_partner_university(potential_uni)
                        if not is_partner and not lead_collected:
                            non_partner_university_mentioned = potential_uni
                            break
                if non_partner_university_mentioned:
                    break
            
            # Also check for known university names/abbreviations directly in the message
            # This handles cases like "beihang university" in lowercase or mid-sentence
            if not non_partner_university_mentioned:
                known_universities = ['beihang', 'buaa', 'peking', 'pku', 'tsinghua', 'shandong', 'harbin', 'zhejiang']
                for uni_keyword in known_universities:
                    if uni_keyword in user_msg_lower:
                        # Try to extract full university name around this keyword
                        # Look for patterns like "beihang university", "beihang", etc.
                        uni_pattern = rf'\b{re.escape(uni_keyword)}\s*(?:university|college|institute|tech|technology)?\b'
                        match = re.search(uni_pattern, user_msg_lower)
                        if match:
                            potential_uni = match.group(0).strip()
                            is_partner, _ = self._is_malishaedu_partner_university(potential_uni)
                            if not is_partner and not lead_collected:
                                non_partner_university_mentioned = potential_uni
                                break
                            elif is_partner:
                                # It's a partner - don't mark as non-partner
                                break
        
        # Step 0.8: Deterministic Query Classification and Routing
        query_classification = self.classify_query(user_message, student_state)
        intent = query_classification['intent']
        needs_db = query_classification['needs_db']
        needs_csca_rag = query_classification['needs_csca_rag']
        needs_general_rag = query_classification['needs_general_rag']
        needs_web = query_classification['needs_web']
        doc_type = query_classification.get('doc_type', 'b2c_study')
        audience = query_classification.get('audience')
        
        print(f"DEBUG: Query classification - intent={intent}, doc_type={doc_type}, audience={audience}, needs_db={needs_db}, needs_csca_rag={needs_csca_rag}, needs_general_rag={needs_general_rag}, needs_web={needs_web}")
        
        # Priority 1: CSCA/CSC/Chinese Government Scholarship questions - RAG only (strict rule)
        if intent == 'csca' and needs_csca_rag:
            try:
                # Build comprehensive search query for CSCA/CSC/Chinese Government Scholarship
                user_lower = user_message.lower()
                csca_search_query = "CSCA China Scholastic Competency Assessment Chinese Government Scholarship CSC undergraduate bachelor 2026 2027"
                
                # Add specific terms based on user question
                if any(term in user_lower for term in ['master', 'masters', 'phd', 'doctorate']):
                    csca_search_query += " masters phd not required"
                if any(term in user_lower for term in ['scholarship', 'csc', 'chinese government']):
                    csca_search_query += " scholarship application process requirements"
                if any(term in user_lower for term in ['exam', 'test', 'registration', 'fee']):
                    csca_search_query += " exam schedule registration fee"
                
                # Use filtered retrieval with doc_type='csca'
                rag_results = self.rag_service.retrieve(self.db, csca_search_query, doc_type='csca', audience=audience, top_k=4)
                
                if rag_results:
                    rag_context = self.rag_service.format_rag_context(rag_results)
                    
                    # Check if user is asking for details that might not be in chunks (success rate, testimonials, specific numbers)
                    asks_for_statistics = any(term in user_lower for term in [
                        'success rate', 'successful', 'testimonial', 'review', 'experience',
                        'how many', 'percentage', 'chance', 'probability', 'rate'
                    ])
                    
                    # Generate answer using RAG context only with strict instructions
                    # CRITICAL: NEVER mention tools, search results, embeddings, browsing, or retrieval
                    system_prompt = """You are a helpful assistant answering CSCA/CSC/Chinese Government Scholarship questions.

CRITICAL RULES:
1. Answer naturally and conversationally. NEVER mention tools, search results, embeddings, browsing, retrieval, or how you found information.
2. NEVER say "I couldn't find", "search results do not", "the provided context does not contain", or similar meta-talk about retrieval.
3. Use ONLY the provided information as your factual source.
4. If the information doesn't fully answer the question, provide a helpful, practical answer based on what you know, then ask for specific details to provide more personalized guidance.
5. Keep answers concise (2-5 sentences for most questions).
6. Always end with a relevant follow-up question to collect lead information (degree level, scholarship category, university preference)."""
                    
                    user_prompt = f"""Information about CSCA/CSC/Chinese Government Scholarship:
{rag_context}

User question: {user_message}

Answer the question naturally and conversationally. Use the information provided above. If the information doesn't fully cover the question, provide a helpful practical answer and ask for specific details to give more personalized guidance. NEVER mention tools, search, or how information was found."""
                    
                    response = self.openai_service.chat_completion([
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ])
                    answer = response.choices[0].message.content
                    
                    # Check if answer contains tool meta-talk or indicates low confidence
                    tool_meta_phrases = [
                        "the provided context does not contain",
                        "does not contain information",
                        "does not contain specific information",
                        "search results do not",
                        "search results do not explicitly",
                        "i couldn't find",
                        "i was unable to find",
                        "the information provided does not",
                        "based on the search results",
                        "according to the search results",
                        "the retrieved information",
                        "the embedded context"
                    ]
                    
                    has_tool_meta = any(phrase in answer.lower() for phrase in tool_meta_phrases)
                    
                    # Check if RAG context is empty or very short (low confidence)
                    rag_context_empty_or_low = not rag_context or len(rag_context.strip()) < 50
                    
                    # If tool meta-talk detected or low confidence, use fallback
                    if has_tool_meta or rag_context_empty_or_low:
                        print(f"DEBUG: RAG context didn't answer the question - trying Tavily/domain knowledge")
                        # Try Tavily or domain knowledge instead of asking for lead info
                        # Check if this is a general CSCA question that could benefit from Tavily
                        asks_for_latest = any(term in user_lower for term in [
                            'latest', 'current', 'updated', 'recent', 'new policy', 'this month', '2026', '2027'
                        ]) and any(term in user_lower for term in ['rule', 'policy', 'regulation', 'change'])
                        
                        is_general_csca_question = any(term in user_lower for term in [
                            'after', 'post', 'completing', 'graduation', 'graduate', 'finish', 'complete studies',
                            'career', 'job', 'work', 'future', 'what happens', 'next steps'
                        ])
                        
                        should_use_tavily = asks_for_latest or is_general_csca_question
                        
                        if should_use_tavily:
                            # Try Tavily
                            print(f"DEBUG: Attempting Tavily fallback for CSCA question")
                            tavily_context = None
                            try:
                                allowed_domains = ["malishaedu.com", "gov.cn", "csc.edu.cn", "chineseembassy.org"]
                                all_tavily_results = []
                                
                                for domain in allowed_domains:
                                    try:
                                        tavily_query = f"{user_message} site:{domain}"
                                        domain_results = self.tavily_service.search(tavily_query, max_results=2)
                                        filtered_results = [
                                            r for r in domain_results 
                                            if domain in r.get('url', '').lower()
                                        ]
                                        all_tavily_results.extend(filtered_results)
                                        if len(all_tavily_results) >= 3:
                                            break
                                    except Exception as e:
                                        print(f"DEBUG: Tavily search for {domain} failed: {e}")
                                        continue
                                
                                if all_tavily_results and len(all_tavily_results) > 0:
                                    all_tavily_results = all_tavily_results[:3]
                                    tavily_context = "\n\nLatest Information from Official Sources:\n"
                                    for i, result in enumerate(all_tavily_results, 1):
                                        tavily_context += f"\n{i}. {result.get('title', 'Source')}\n"
                                        tavily_context += f"   {result.get('content', '')}\n"
                                        tavily_context += f"   URL: {result.get('url', '')}\n"
                                    
                                    system_prompt = """You are a helpful assistant answering CSCA/CSC/Chinese Government Scholarship questions.

CRITICAL RULES:
1. Answer naturally and conversationally. NEVER mention tools, search results, browsing, or how you found information.
2. NEVER say "search results show", "according to the search", or similar meta-talk.
3. Use the provided information to answer the question directly and concisely (2-5 sentences).
4. Always end with a relevant follow-up question to collect lead information."""
                                    user_prompt = f"""Web Search Results:
{tavily_context}

User question: {user_message}

Please answer the question using the information from the web search results above."""
                                    
                                    response = self.openai_service.chat_completion([
                                        {"role": "system", "content": system_prompt},
                                        {"role": "user", "content": user_prompt}
                                    ])
                                    answer = response.choices[0].message.content
                                    print(f"DEBUG: Tavily provided answer for CSCA question")
                                    return {
                                        'response': answer,
                                        'db_context': '',
                                        'rag_context': rag_context,
                                        'tavily_context': tavily_context,
                                        'lead_collected': lead_collected,
                                        'show_lead_form': False,
                                        'lead_form_prefill': {}
                                    }
                            except Exception as e:
                                print(f"DEBUG: Tavily fallback failed: {e}")
                        
                        # Tavily failed or not applicable - use CSCA fallback template
                        print(f"DEBUG: Using CSCA fallback template")
                        answer = self._provide_csca_fallback_answer(user_message)
                        # Add lead question
                        lead_question = self._build_csca_lead_question(student_state)
                        if lead_question:
                            answer = answer + f"\n\n{lead_question}"
                        return {
                            'response': answer,
                            'db_context': '',
                            'rag_context': rag_context if not rag_context_empty_or_low else None,
                            'tavily_context': None,
                            'lead_collected': lead_collected,
                            'show_lead_form': False,
                            'lead_form_prefill': {}
                        }
                    
                    # Answer is good - add CSCA-specific lead question if needed
                    # Check if answer already asks for lead info
                    already_asks_for_info = any(term in answer.lower() for term in [
                        'nationality', 'major', 'degree level', 'scholarship category', 'university'
                    ])
                    if not already_asks_for_info:
                        lead_question = self._build_csca_lead_question(student_state)
                        if lead_question:
                            answer = answer + f"\n\n{lead_question}"
                    
                    return {
                        'response': answer,
                        'db_context': '',
                        'rag_context': rag_context,
                        'tavily_context': None,
                        'lead_collected': lead_collected,
                        'show_lead_form': False,
                        'lead_form_prefill': {}
                    }
                else:
                    # No RAG results found - try Tavily as fallback for CSCA questions
                    # Use Tavily for: 1) latest rules explicitly asked, OR 2) general CSCA questions not in RAG
                    asks_for_latest = any(term in user_lower for term in [
                        'latest', 'current', 'updated', 'recent', 'new policy', 'this month', '2026', '2027'
                    ]) and any(term in user_lower for term in ['rule', 'policy', 'regulation', 'change'])
                    
                    # For general CSCA questions (post-study, career, etc.), also try Tavily
                    is_general_csca_question = any(term in user_lower for term in [
                        'after', 'post', 'completing', 'graduation', 'graduate', 'finish', 'complete studies',
                        'career', 'job', 'work', 'future', 'what happens', 'next steps'
                    ])
                    
                    should_use_tavily = asks_for_latest or is_general_csca_question
                    
                    if not should_use_tavily:
                        print(f"DEBUG: No RAG results for CSCA/CSC question, but not a general/latest question - providing domain knowledge")
                        # Provide helpful domain knowledge answer
                        csca_answer = self._provide_csca_domain_knowledge(user_message)
                        return {
                            'response': csca_answer,
                            'db_context': '',
                            'rag_context': None,
                            'tavily_context': None,
                            'lead_collected': lead_collected,
                            'show_lead_form': False,
                            'lead_form_prefill': {}
                        }
                    
                    print(f"DEBUG: No RAG results for CSCA/CSC question: {user_message}")
                    print(f"DEBUG: Attempting Tavily fallback (MalishaEdu + govt sites)")
                    
                    tavily_context = None
                    try:
                        # Restrict Tavily search to MalishaEdu and government sites using site: operator
                        # Try multiple searches for different domains
                        allowed_domains = ["malishaedu.com", "gov.cn", "csc.edu.cn", "chineseembassy.org"]
                        all_tavily_results = []
                        
                        for domain in allowed_domains:
                            try:
                                tavily_query = f"{user_message} site:{domain}"
                                domain_results = self.tavily_service.search(tavily_query, max_results=2)
                                # Filter to ensure results are from the correct domain
                                filtered_results = [
                                    r for r in domain_results 
                                    if domain in r.get('url', '').lower()
                                ]
                                all_tavily_results.extend(filtered_results)
                                if len(all_tavily_results) >= 3:
                                    break
                            except Exception as e:
                                print(f"DEBUG: Tavily search for {domain} failed: {e}")
                                continue
                        
                        if all_tavily_results and len(all_tavily_results) > 0:
                            # Limit to top 3 results
                            all_tavily_results = all_tavily_results[:3]
                            tavily_context = "\n\nLatest Information from Official Sources:\n"
                            for i, result in enumerate(all_tavily_results, 1):
                                tavily_context += f"\n{i}. {result.get('title', 'Source')}\n"
                                tavily_context += f"   {result.get('content', '')}\n"
                                tavily_context += f"   URL: {result.get('url', '')}\n"
                            print(f"DEBUG: Tavily found {len(all_tavily_results)} results for CSCA question")
                        else:
                            print("DEBUG: Tavily also returned no results for CSCA question")
                    except Exception as e:
                        print(f"DEBUG: Tavily fallback failed: {e}")
                    
                    # If Tavily found results, use them; otherwise use safe template
                    if tavily_context:
                        system_prompt = """You are a helpful assistant answering CSCA/CSC/Chinese Government Scholarship questions.

Use the provided web search results to answer the user's question. Focus on official information from government websites and MalishaEdu sources. Be accurate and cite sources when possible."""
                        
                        user_prompt = f"""Web Search Results:
{tavily_context}

User question: {user_message}

Please answer the question using the information from the web search results above. If the information is not sufficient, ask for the user's nationality, major, and intake preferences."""
                        
                        try:
                            response = self.openai_service.chat_completion([
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_prompt}
                            ])
                            answer = response.choices[0].message.content
                            
                            # Add lead question if not already present
                            if "nationality" not in answer.lower() and "major" not in answer.lower():
                                lead_question = self._build_single_lead_question(student_state, audience=audience)
                                if lead_question:
                                    answer = answer + f"\n\n{lead_question}"
                            
                            return {
                                'response': answer,
                                'db_context': '',
                                'rag_context': None,
                                'tavily_context': tavily_context,
                                'lead_collected': lead_collected,
                                'show_lead_form': False,
                                'lead_form_prefill': {}
                            }
                        except Exception as e:
                            print(f"DEBUG: Failed to generate response from Tavily results: {e}")
                            # Fall through to safe template
                    
                    # Fallback to domain knowledge if Tavily didn't work or returned nothing
                    print(f"DEBUG: Tavily also returned no results - providing CSCA domain knowledge answer")
                    csca_answer = self._provide_csca_domain_knowledge(user_message)
                    return {
                        'response': csca_answer,
                        'db_context': '',
                        'rag_context': None,
                        'tavily_context': tavily_context,
                        'lead_collected': lead_collected,
                        'show_lead_form': False,
                        'lead_form_prefill': {}
                    }
            except Exception as e:
                print(f"CSCA RAG search failed: {e}")
                # Error occurred - provide helpful domain knowledge answer
                print(f"DEBUG: CSCA RAG search failed - providing CSCA domain knowledge answer")
                csca_answer = self._provide_csca_domain_knowledge(user_message)
                return {
                    'response': csca_answer,
                    'db_context': '',
                    'rag_context': None,
                    'tavily_context': None,
                    'lead_collected': lead_collected,
                    'show_lead_form': False,
                    'lead_form_prefill': {}
                }
        
        # Priority 2: General FAQ questions - FAQService first, then RAG
        if intent == 'general_faq' and not needs_db:
            # Try FAQService match first
            faq_match = None
            faq_score = 0.0
            if self.faq_service:
                should_try_faq = self.faq_service._should_try_faq_match(user_message)
                if should_try_faq:
                    faq_match, faq_score = self.faq_service.match(user_message, threshold=0.62)
                if not faq_match:
                    faq_match, faq_score = self.faq_service.match(user_message, threshold=0.55)
            
            # Validate FAQ match is semantically relevant
            if faq_match:
                # Extract key semantic words from user question and matched FAQ
                user_lower = user_message.lower()
                matched_question_lower = faq_match['question'].lower()
                matched_answer_lower = faq_match['answer'].lower()
                
                # Extract meaningful semantic keywords (exclude common stopwords)
                stopwords = {'what', 'are', 'the', 'your', 'that', 'this', 'with', 'from', 'china', 'chinese', 
                           'how', 'do', 'you', 'is', 'in', 'for', 'and', 'or', 'to', 'a', 'an', 'of', 'on', 'at',
                           'monthly', 'average', 'per', 'month', 'depending', 'city', 'lifestyle'}
                user_keywords = set([w for w in user_lower.split() if len(w) > 3 and w not in stopwords])
                matched_keywords = set([w for w in matched_question_lower.split() if len(w) > 3 and w not in stopwords])
                matched_answer_keywords = set([w for w in matched_answer_lower.split() if len(w) > 3 and w not in stopwords])
                
                # Check semantic overlap
                semantic_overlap = user_keywords.intersection(matched_keywords)
                answer_overlap = user_keywords.intersection(matched_answer_keywords)
                total_overlap = semantic_overlap.union(answer_overlap)
                
                # For low scores, require meaningful semantic overlap
                # "travel options" should NOT match "living cost" - they share no semantic keywords
                if faq_score < 0.60:
                    if not total_overlap:  # Require at least 1 matching keyword in question OR answer
                        print(f"DEBUG: FAQ match rejected - low score ({faq_score:.2f}) and no semantic overlap. User: '{user_message}' -> Matched: '{faq_match['question']}'. User keywords: {user_keywords}, Matched keywords: {matched_keywords}")
                        faq_match = None
                        faq_score = 0.0
                    elif faq_score < 0.58 and len(total_overlap) < 2:
                        # For very low scores, require at least 2 matching keywords
                        print(f"DEBUG: FAQ match rejected - very low score ({faq_score:.2f}) and insufficient semantic overlap ({len(total_overlap)} matches). User: '{user_message}' -> Matched: '{faq_match['question']}'")
                        faq_match = None
                        faq_score = 0.0
            
            # If FAQ match found and validated, use it
            if faq_match:
                print(f"DEBUG: FAQ match found (score={faq_score:.2f}): {faq_match['question'][:50]}...")
                faq_answer = faq_match['answer']
                # Skip lead question for general process/FAQ questions
                is_general_process_question = any(phrase in user_message.lower() for phrase in [
                    'how do you handle', 'how does', 'what is the process', 'what is the procedure',
                    'how does the process work', 'explain the process', 'tell me about the process',
                    'application process', 'admission process', 'how it works', 'how does it work'
                ]) and not any(phrase in user_message.lower() for phrase in [
                    'my', 'for me', 'my application', 'my admission', 'i want', 'i need'
                ])
                lead_question = None if is_general_process_question else self._build_single_lead_question(student_state, audience=audience)
                response_text = faq_answer + (f"\n\n{lead_question}" if lead_question else "")
                return {
                    'response': response_text,
                    'db_context': '',
                    'rag_context': None,
                    'tavily_context': None,
                    'lead_collected': lead_collected,
                    'show_lead_form': False,
                    'lead_form_prefill': {}
                }
            
            # If no FAQ match but needs_general_rag, try RAG from answer bank
            if needs_general_rag:
                try:
                    rag_results = self.rag_service.retrieve(self.db, user_message, doc_type=doc_type, audience=audience, top_k=4)
                    if rag_results:
                        rag_context = self.rag_service.format_rag_context(rag_results)
                        # Generate concise answer using RAG
                        response = self.openai_service.chat_completion([
                            {"role": "system", "content": "You are a helpful assistant. Answer the question using ONLY the provided context. Be concise and engaging. Do NOT invent facts not in the context. For general process questions, provide general answers that apply to ALL degree levels, not just one specific degree level."},
                            {"role": "user", "content": f"Context:\n{rag_context}\n\nUser question: {user_message}\n\nAnswer concisely using only the context above."}
                        ])
                        answer = response.choices[0].message.content
                        # Skip lead question for general process/FAQ questions
                        is_general_process_question = any(phrase in user_message.lower() for phrase in [
                            'how do you handle', 'how does', 'what is the process', 'what is the procedure',
                            'how does the process work', 'explain the process', 'tell me about the process',
                            'application process', 'admission process', 'how it works', 'how does it work'
                        ]) and not any(phrase in user_message.lower() for phrase in [
                            'my', 'for me', 'my application', 'my admission', 'i want', 'i need'
                        ])
                        lead_question = None if is_general_process_question else self._build_single_lead_question(student_state, audience=audience)
                        response_text = answer + (f"\n\n{lead_question}" if lead_question else "")
                        return {
                            'response': response_text,
                            'db_context': '',
                            'rag_context': rag_context,
                            'tavily_context': None,
                            'lead_collected': lead_collected,
                            'show_lead_form': False,
                            'lead_form_prefill': {}
                        }
                    else:
                        # RAG returned no results
                        # For people/contact/service_policy: use fallback
                        if intent in ['people_contact', 'service_policy']:
                            fallback = "For the most accurate information, please contact us:\n- Hotline: [check RAG for phone]\n- Email: info@malishaedu.com\n- Which country/city are you from? Our local team can assist you better."
                            return {
                                'response': fallback,
                                'db_context': '',
                                'rag_context': None,
                                'tavily_context': None,
                                'lead_collected': lead_collected,
                                'show_lead_form': False,
                                'lead_form_prefill': {}
                            }
                        # For general knowledge questions (travel, food, safety, etc.): use Tavily
                        elif intent == 'general_faq':
                            # Check if this is a general knowledge question (not policy/process)
                            is_general_knowledge = any(term in user_message.lower() for term in [
                                'travel', 'transportation', 'transport', 'food', 'halal', 'safety', 'security',
                                'accommodation', 'dormitory', 'housing', 'life in china', 'cost of living',
                                'bank account', 'part-time', 'work while', 'insurance', 'medical'
                            ])
                            if is_general_knowledge:
                                print(f"DEBUG: RAG returned no results for general knowledge question - using Tavily")
                                try:
                                    tavily_results = self.tavily_service.search(
                                        f"site:malishaedu.com {user_message}",
                                        max_results=2
                                    )
                                    if tavily_results:
                                        tavily_context = self.tavily_service.format_search_results(tavily_results)
                                        response = self.openai_service.chat_completion([
                                            {"role": "system", "content": "You are a helpful assistant. Answer the question using the provided web search results. Be concise and informative. If the results don't fully answer the question, provide a helpful general answer based on common knowledge about studying in China."},
                                            {"role": "user", "content": f"Web search results:\n{tavily_context}\n\nUser question: {user_message}\n\nAnswer concisely."}
                                        ])
                                        answer = response.choices[0].message.content
                                        # Don't ask for lead info for general knowledge questions
                                        return {
                                            'response': answer,
                                            'db_context': '',
                                            'rag_context': None,
                                            'tavily_context': tavily_context,
                                            'lead_collected': lead_collected,
                                            'show_lead_form': False,
                                            'lead_form_prefill': {}
                                        }
                                    else:
                                        # No Tavily results - provide helpful general answer
                                        print(f"DEBUG: Tavily also returned no results - providing general domain knowledge answer")
                                        general_answer = self._provide_general_knowledge_answer(user_message)
                                        return {
                                            'response': general_answer,
                                            'db_context': '',
                                            'rag_context': None,
                                            'tavily_context': None,
                                            'lead_collected': lead_collected,
                                            'show_lead_form': False,
                                            'lead_form_prefill': {}
                                        }
                                except Exception as e:
                                    print(f"Tavily search failed: {e}")
                                    # Fallback to general knowledge answer
                                    general_answer = self._provide_general_knowledge_answer(user_message)
                                    return {
                                        'response': general_answer,
                                        'db_context': '',
                                        'rag_context': None,
                                        'tavily_context': None,
                                        'lead_collected': lead_collected,
                                        'show_lead_form': False,
                                        'lead_form_prefill': {}
                                    }
                        # For other questions, provide helpful response without asking for lead info unnecessarily
                        helpful_response = f"I'd be happy to help you with that! While I don't have specific information about '{user_message}' in our knowledge base right now, I can provide general guidance. Could you tell me more about what specifically you'd like to know?"
                        return {
                            'response': helpful_response,
                            'db_context': '',
                            'rag_context': None,
                            'tavily_context': None,
                            'lead_collected': lead_collected,
                            'show_lead_form': False,
                            'lead_form_prefill': {}
                        }
                except Exception as e:
                    print(f"General RAG search failed: {e}")
                    # Fallback for people/contact/service_policy
                    if intent in ['people_contact', 'service_policy']:
                        fallback = "For the most accurate information, please contact us:\n- Hotline: [check RAG for phone]\n- Email: info@malishaedu.com\n- Which country/city are you from? Our local team can assist you better."
                        return {
                            'response': fallback,
                            'db_context': '',
                            'rag_context': None,
                            'tavily_context': None,
                            'lead_collected': lead_collected,
                            'show_lead_form': False,
                            'lead_form_prefill': {}
                        }
                    # For other errors, provide helpful response
                    helpful_response = f"I'd be happy to help you with that! Could you tell me more about what specifically you'd like to know?"
                    return {
                        'response': helpful_response,
                        'db_context': '',
                        'rag_context': None,
                        'tavily_context': None,
                        'lead_collected': lead_collected,
                        'show_lead_form': False,
                        'lead_form_prefill': {}
                    }
            
            # Only use Tavily if explicitly needed (latest policy/current info) AND not People/Contact or Service Policy
            # Hard guard: Never use Tavily for People/Contact or Service Policy
            never_use_tavily = (
                intent == 'people_contact' or 
                intent == 'service_policy' or
                doc_type == 'people_contact' or
                doc_type == 'service_policy'
            )
            
            if needs_web and not never_use_tavily:
                print(f"DEBUG: Tavily guard passed - calling Tavily for latest policy question")
                print(f"DEBUG: Reason: needs_web=True, intent={intent}, doc_type={doc_type}")
                try:
                    # Restrict to malishaedu.com domain
                    tavily_results = self.tavily_service.search(
                        f"site:malishaedu.com {user_message}", 
                        max_results=2
                    )
                    if tavily_results:
                        tavily_context = self.tavily_service.format_search_results(tavily_results)
                        print(f"DEBUG: Tavily returned {len(tavily_results)} results")
                        # Generate answer using Tavily context
                        response = self.openai_service.chat_completion([
                            {"role": "system", "content": "Answer using ONLY the provided web search results. Be concise."},
                            {"role": "user", "content": f"Web search results:\n{tavily_context}\n\nUser question: {user_message}\n\nAnswer concisely."}
                        ])
                        answer = response.choices[0].message.content
                        lead_question = self._build_single_lead_question(student_state, audience=audience)
                        response_text = answer + (f"\n\n{lead_question}" if lead_question else "")
                        return {
                            'response': response_text,
                            'db_context': '',
                            'rag_context': None,
                            'tavily_context': tavily_context,
                            'lead_collected': lead_collected,
                            'show_lead_form': False,
                            'lead_form_prefill': {}
                        }
                    else:
                        print(f"DEBUG: Tavily returned no results")
                except Exception as e:
                    print(f"Tavily search failed: {e}")
            elif never_use_tavily:
                print(f"DEBUG: Hard guard - skipping Tavily for {intent}/{doc_type} question")
        
        # Step 1: Query Database - ONLY if needs_db=True
        # CRITICAL: Never run DB queries for general questions (needs_db=False)
        db_context = ""
        matched_programs = ""
        if needs_db:
            # Query database for partner universities/majors/intakes
            db_context, matched_programs = self._query_database_with_state(
                user_message, 
                student_state, 
                conversation_slice, 
                lead_collected, 
                is_simple_info_query,
                sales_intent=sales_intent,
                current_date=current_date
            )
            print(f"DEBUG: DB query executed - context_length={len(db_context)}, matched_programs_count={len(matched_programs.split('MATCHED_PROGRAMS')) if matched_programs else 0}")
        else:
            print(f"DEBUG: Skipping DB query - needs_db=False (intent={intent})")
        
        # Step 2: Detect CSCA/scholarship questions - prioritize RAG for these topics
        user_msg_lower = user_message.lower()
        asks_about_csca = any(term in user_msg_lower for term in [
            'csca', 'china scholastic competency assessment', 'csca exam', 'csca test',
            'csca registration', 'csca requirement', 'csca mandatory', 'csca score',
            'csca subject', 'csca fee', 'csca cost', 'csca preparation', 'csca training'
        ])
        asks_about_scholarship = any(term in user_msg_lower for term in [
            'scholarship', 'scholarships', 'funding', 'financial aid', 'csc scholarship',
            'chinese government scholarship', 'provincial scholarship', 'university scholarship',
            'full scholarship', 'partial scholarship', 'tuition waiver', 'living stipend',
            'can i get scholarship', 'how to get scholarship', 'scholarship requirement',
            'scholarship application', 'scholarship deadline'
        ])
        
        # Step 2.1: If CSCA or scholarship question, prioritize RAG (don't query DB for anonymous users)
        rag_context = None
        if asks_about_csca or asks_about_scholarship:
            # For CSCA/scholarship questions, use RAG as primary source (not database for anonymous users)
            try:
                # Build targeted search query
                if asks_about_csca:
                    csca_search_query = "CSCA China Scholastic Competency Assessment exam structure subjects registration fee timing 2026 undergraduate bachelor"
                    if any(term in user_msg_lower for term in ['master', 'masters', 'phd', 'doctorate']):
                        csca_search_query += " masters phd not required"
                    rag_results = self.rag_service.retrieve(self.db, csca_search_query, doc_type='csca', audience=None, top_k=4)
                else:
                    # Determine doc_type for scholarship questions
                    scholarship_doc_type = 'b2c_study'  # Default for general scholarship questions
                    rag_results = self.rag_service.retrieve(self.db, user_message, doc_type=scholarship_doc_type, audience=None, top_k=4)
                
                if rag_results:
                    rag_context = self.rag_service.format_rag_context(rag_results)
                    # Add instruction for RAG-based answers
                    rag_context += "\n\nIMPORTANT: Use ONLY the RAG content above as your factual source. DO NOT fabricate university-specific CSCA rules or scholarship amounts. If RAG doesn't contain specific details, say so clearly. Make distinction between general policy (from RAG) and specific university rules."
            except Exception as e:
                print(f"RAG search for CSCA/scholarship failed: {e}")
                rag_context = None
        
        # Step 2.2: Check if user's major is not offered - use database arrays instead of RAG
        related_majors_context = None
        if student_state.major and not use_db:
            # For anonymous users, use database arrays to find related majors
            try:
                # Use fuzzy matching on database majors array
                matched_majors = self._fuzzy_match_major(student_state.major, student_state.degree_level, threshold=0.4)
                
                if matched_majors:
                    # Format matched majors for context
                    major_list = []
                    for major in matched_majors[:10]:  # Top 10 matches
                        major_info = f"- {major['name']} at {major['university_name']}"
                        if major['degree_level']:
                            major_info += f" ({major['degree_level']})"
                        major_list.append(major_info)
                    related_majors_context = "RELATED MAJORS FROM DATABASE:\n" + "\n".join(major_list)
            except Exception as e:
                print(f"Database major matching failed: {e}")
        
        # Step 2.3: RAG search (skip for program/fee listing - use DB only)
        # Fix: Do NOT use RAG for program/fee listing - only for marketing copy or generic questions
        skip_rag_for_programs = sales_intent in ['fees_only', 'fees_compare', 'list_programs', 'earliest_intake']
        
        if not rag_context and not skip_rag_for_programs:
            # Only use RAG for non-program queries (CSCA, scholarships, general questions)
            if not allow_lead_write:  # Stranger: Use RAG for general questions
                try:
                    # Use filtered retrieval with determined doc_type and audience
                    rag_results = self.rag_service.retrieve(self.db, user_message, doc_type=doc_type, audience=audience, top_k=4)
                    if rag_results:
                        rag_context = self.rag_service.format_rag_context(rag_results)
                        # Add related majors context if available
                        if related_majors_context:
                            rag_context = f"{rag_context}\n\nRELATED MAJORS FROM MALISHAEDU (if user's major is not offered):\n{related_majors_context}"
                except Exception as e:
                    print(f"RAG search failed: {e}")
                    rag_context = None
            elif not db_context or len(db_context) < 100:  # DB context is weak
                # Lead collected but DB context weak: try RAG (only for non-program queries)
                try:
                    rag_results = self.rag_service.retrieve(self.db, user_message, doc_type=doc_type, audience=audience, top_k=4)
                    if rag_results:
                        rag_context = self.rag_service.format_rag_context(rag_results)
                except Exception as e:
                    print(f"RAG search failed: {e}")
                    rag_context = None
        elif skip_rag_for_programs:
            print(f"DEBUG: Skipping RAG for program/fee listing (intent={sales_intent}) - using DB only")
        
        # Step 2.4: If non-partner university mentioned, use database arrays to find partner alternatives
        if non_partner_university_mentioned and student_state.major and student_state.degree_level:
            try:
                # Use database arrays to find partner universities offering the same major/degree
                matched_majors = self._fuzzy_match_major(student_state.major, student_state.degree_level, threshold=0.4)
                
                if matched_majors:
                    # Get unique universities from matched majors
                    partner_unis = {}
                    for major in matched_majors[:10]:  # Top 10 matches
                        uni_id = major['university_id']
                        if uni_id not in partner_unis:
                            # Find university in all_universities array
                            uni = next((u for u in self.all_universities if u['id'] == uni_id), None)
                            if uni:
                                partner_unis[uni_id] = uni
                    
                    if partner_unis:
                        uni_list = []
                        for uni in partner_unis.values():
                            uni_info = f"- {uni['name']}"
                            if uni['city']:
                                uni_info += f" ({uni['city']}"
                                if uni['province']:
                                    uni_info += f", {uni['province']}"
                                uni_info += ")"
                            if uni['ranking']:
                                uni_info += f" [Ranking: {uni['ranking']}]"
                            uni_list.append(uni_info)
                        
                        partner_alt_context = f"MALISHAEDU PARTNER UNIVERSITIES OFFERING {student_state.major} {student_state.degree_level}:\n" + "\n".join(uni_list)
                        if rag_context:
                            rag_context = f"{rag_context}\n\n{partner_alt_context}"
                        else:
                            rag_context = partner_alt_context
            except Exception as e:
                print(f"Database search for partner alternatives failed: {e}")
        
        # Step 2.4.5: DB-lite querying (Fix 5) - improved query behavior
        # Query DB for fees_only, fees_compare, list_programs, earliest_intake intents
        db_lite_context = None
        can_query_db_lite = (
            student_state.degree_level and 
            (student_state.intake_term or sales_intent == 'earliest_intake') and
            (student_state.intake_year or (student_state.intake_term and self.infer_intake_year(student_state.intake_term, None, current_date)))
        )
        
        if can_query_db_lite and (sales_intent in ['fees_only', 'fees_compare', 'list_programs', 'earliest_intake']):
            try:
                # Infer intake year if missing
                inferred_year = self.infer_intake_year(
                    student_state.intake_term,
                    student_state.intake_year,
                    current_date
                )
                if inferred_year and not student_state.intake_year:
                    student_state.intake_year = inferred_year
                    print(f"DEBUG: Inferred intake_year={inferred_year} for DB-lite query")
                
                print(f"DEBUG: DB-lite querying for intent={sales_intent}, degree_level={student_state.degree_level}, intake_term={student_state.intake_term}, intake_year={student_state.intake_year}, city={student_state.city}")
                
                # Query matching intakes
                matching_intakes = self.get_matching_intakes(student_state, limit=30)
                
                # Filter by city if provided (fuzzy match)
                if student_state.city and matching_intakes:
                    from difflib import SequenceMatcher
                    city_lower = student_state.city.lower()
                    filtered_by_city = []
                    for intake in matching_intakes:
                        uni_city = (intake.university.city or '').lower()
                        if uni_city:
                            similarity = SequenceMatcher(None, city_lower, uni_city).ratio()
                            if similarity >= 0.6 or city_lower in uni_city or uni_city in city_lower:
                                filtered_by_city.append(intake)
                    if filtered_by_city:
                        matching_intakes = filtered_by_city
                        print(f"DEBUG: Filtered by city '{student_state.city}': {len(matching_intakes)} intakes")
                
                if matching_intakes:
                    print(f"DEBUG: DB-lite returned {len(matching_intakes)} intakes")
                    
                    # For earliest_intake, find the earliest deadline
                    if sales_intent == 'earliest_intake':
                        current_dt = current_date.date()
                        earliest = min(matching_intakes, key=lambda i: i.application_deadline.date() if i.application_deadline else current_dt)
                        matching_intakes = [earliest]
                    
                    # Sort based on intent
                    sort_by_cheapest = (sales_intent == 'fees_compare')
                    
                    # Store for pagination and follow-up resolver (Fix 3)
                    self.last_list_results = matching_intakes
                    self.last_list_offset = 0
                    self.last_intent = sales_intent
                    # Create a snapshot of student_state
                    import copy
                    self.last_state = copy.deepcopy(student_state)
                    print(f"DEBUG: Stored pagination state - intent={sales_intent}, results_count={len(matching_intakes)}")
                    
                    # Build DB-lite context
                    db_lite_parts = []
                    db_lite_parts.append("=== DATABASE PROGRAM INFORMATION (DB-LITE) ===")
                    
                    # Summarize tuition range
                    tuition_summary = self.summarize_tuition(matching_intakes)
                    db_lite_parts.append(tuition_summary)
                    
                    # Pick top 2-3 options
                    top_options = self.pick_top_options(matching_intakes, k=3, sort_by_cheapest=sort_by_cheapest)
                    
                    if top_options:
                        db_lite_parts.append("\nTop options:")
                        for opt in top_options:
                            db_lite_parts.append(f"- {opt}")
                    
                    db_lite_parts.append("\n=== END DATABASE PROGRAM INFORMATION ===")
                    
                    # Add instruction based on intent
                    if sales_intent in ['fees_only', 'fees_compare']:
                        db_lite_parts.append("\nINSTRUCTION: Show the tuition range and top 2-3 options. Keep response concise (max 8-12 lines). Ask ONE follow-up: preferred city OR WhatsApp number for fast processing. CTA: 'If you sign up (/signup), I can save these and give exact total cost + document checklist.' Mention MalishaEdu Application Deposit: 80 USD.")
                    elif sales_intent == 'list_programs':
                        db_lite_parts.append("\nINSTRUCTION: List the top 2-3 options with key info. Keep response concise. Ask ONE follow-up: preferred city OR WhatsApp number. CTA: 'Sign up (/signup) for exact personalized guidance.'")
                    elif sales_intent == 'earliest_intake':
                        db_lite_parts.append("\nINSTRUCTION: Show the earliest intake option. Keep response concise. Ask ONE follow-up: preferred city OR WhatsApp number. CTA: 'Sign up (/signup) for exact personalized guidance.'")
                    
                    db_lite_context = "\n".join(db_lite_parts)
                    
                    # Add to db_context if it exists, otherwise create it
                    if db_context and "ANONYMOUS USER" not in db_context:
                        db_context = f"{db_context}\n\n{db_lite_context}"
                    else:
                        db_context = db_lite_context
                else:
                    print(f"DEBUG: DB-lite returned 0 matches")
                    # Check if September exists when March doesn't (E)
                    if student_state.intake_term and "march" in student_state.intake_term.lower():
                        # Try September of same year
                        from app.models import IntakeTerm
                        try:
                            # Create a temporary StudentProfileState for September query
                            sept_state = StudentProfileState(
                                degree_level=student_state.degree_level,
                                major=student_state.major,
                                intake_term='September',
                                intake_year=student_state.intake_year or self.infer_intake_year('September', None, current_date),
                                nationality=student_state.nationality
                            )
                            sept_intakes = self.get_matching_intakes(sept_state, limit=30)
                            if sept_intakes:
                                inferred_year = student_state.intake_year or self.infer_intake_year('March', None, current_date)
                                sept_year = sept_state.intake_year or self.infer_intake_year('September', None, current_date)
                                db_context = f"{db_context}\n\n=== ALTERNATIVE INTAKE AVAILABLE ===\nWe don't have March {inferred_year} {student_state.degree_level or ''} intakes for that major in partner DB, but September {sept_year} options are available. Would you like to see September options?\n=== END ALTERNATIVE ==="
                        except Exception as e:
                            print(f"DEBUG: September alternative check failed: {e}")
                            pass
            except Exception as e:
                print(f"DEBUG: DB-lite querying failed: {e}")
                import traceback
                traceback.print_exc()
        
        # Step 2.5: Service charge RAG search (only when explicitly asked)
        # Fix 6: Remove embeddings-based cost intent detection - DB-lite handles cost queries
        # Only use RAG for service charges when explicitly requested
        user_msg_lower = user_message.lower()
        if any(term in user_msg_lower for term in ['service fee', 'service charge']):
            try:
                cost_rag_results = self.rag_service.retrieve(
                    self.db,
                    "MalishaEdu service charges service fee table",
                    doc_type='service_policy',
                    audience=None,
                    top_k=4
                )
                if cost_rag_results:
                    cost_rag_context = self.rag_service.format_rag_context(cost_rag_results)
                    if rag_context:
                        rag_context = f"{rag_context}\n\nMALISHAEDU SERVICE CHARGES:\n{cost_rag_context}"
                    else:
                        rag_context = f"MALISHAEDU SERVICE CHARGES:\n{cost_rag_context}"
            except Exception as e:
                print(f"RAG service charge search failed: {e}")
        
        # Step 3: Tavily (LAST RESORT with hard guards)
        # Hard guards: NEVER use Tavily for People/Contact, Service Policy, or general study questions
        tavily_context = None
        skip_tavily_for_programs = sales_intent in ['fees_only', 'fees_compare', 'list_programs', 'earliest_intake'] or intent == 'program_specific'
        
        # Hard guard: Never use Tavily for People/Contact or Service Policy questions
        never_use_tavily = (
            intent == 'people_contact' or 
            intent == 'service_policy' or
            doc_type == 'people_contact' or
            doc_type == 'service_policy'
        )
        
        if never_use_tavily:
            print(f"DEBUG: Hard guard - skipping Tavily for {intent}/{doc_type} question (People/Contact and Service Policy never use Tavily)")
        elif not skip_tavily_for_programs and needs_web and not db_context and not rag_context:
            # Only use Tavily if:
            # 1. No DB result
            # 2. No RAG chunk confidence/empty
            # 3. Question is policy/visa/regulation and likely time-sensitive
            is_time_sensitive = any(term in user_message.lower() for term in [
                'visa', 'policy', 'regulation', 'rule', 'latest', 'current', 'updated', 'recent', 'new policy', 'this month'
            ])
            
            if is_time_sensitive:
                print(f"DEBUG: Tavily guard passed - calling Tavily for time-sensitive policy/visa question")
                print(f"DEBUG: Reason: no DB context, no RAG context, time-sensitive question")
                try:
                    tavily_results = self.tavily_service.search(f"site:malishaedu.com {user_message}", max_results=2)
                    if tavily_results:
                        tavily_context = self.tavily_service.format_search_results(tavily_results)
                        print(f"DEBUG: Tavily returned {len(tavily_results)} results")
                    else:
                        print(f"DEBUG: Tavily returned no results")
                except Exception as e:
                    print(f"Tavily search failed: {e}")
            else:
                print(f"DEBUG: Tavily guard failed - question is not time-sensitive policy/visa/regulation")
        elif skip_tavily_for_programs:
            print(f"DEBUG: Skipping Tavily for program-specific query (intent={intent}) - using DB only")
        
        # Step 3.5: Detect if user shows interest (should show lead form)
        # Show lead form for: specific university/major questions, scholarship questions, application questions
        # Don't show for: generic China questions, general living questions, general scholarship info
        user_msg_lower = user_message.lower()
        
        # Check if user is agreeing to fill lead form (after being asked)
        # Look at the last assistant message to see if it mentioned lead form
        agreement_keywords = ['ok', 'okay', 'yes', 'sure', 'alright', 'fine', 'yeah', 'yep', 'of course', 'definitely']
        user_agreed = any(keyword in user_msg_lower for keyword in agreement_keywords)
        
        # Check if previous assistant message mentioned lead form
        previous_mentioned_lead_form = False
        if conversation_slice:
            # Look at the last assistant message
            for msg in reversed(conversation_slice):
                if msg.get('role') == 'assistant':
                    assistant_content = msg.get('content', '').lower()
                    if any(phrase in assistant_content for phrase in ['lead form', 'fill out', 'popup', 'form']):
                        previous_mentioned_lead_form = True
                    break
        
        # Keywords that indicate interest in specific programs/universities
        interest_keywords = [
            'want to study', 'interested in', 'apply', 'application', 'fee', 'cost', 'tuition',
            'document', 'requirement', 'scholarship', 'when can i', 'how much', 'which university',
            'what university', 'what major', 'what program', 'intake', 'deadline', 'admission',
            'enroll', 'enrollment', 'program', 'course', 'degree', 'bachelor', 'master', 'phd',
            'doctorate', 'language program', 'csca', 'hsk'
        ]
        
        # Check if user mentions specific university (from database)
        mentions_university = False
        if db_context:
            # Check if db_context contains university names
            try:
                universities_in_db = self.db.query(University).all()
                for uni in universities_in_db:
                    if uni.name.lower() in user_msg_lower:
                        mentions_university = True
                        break
            except Exception as e:
                print(f"Error querying universities: {e}")
                # Rollback any failed transaction
                try:
                    self.db.rollback()
                except:
                    pass
        
        # Check if user mentions specific major (including in Bengali/other languages)
        mentions_major = any(term in user_msg_lower for term in [
            'computer science', 'engineering', 'business', 'medicine', 'mbbs', 'chinese language',
            'mechanical', 'electrical', 'civil', 'chemical', 'biology', 'physics', 'chemistry',
            'mathematics', 'economics', 'management', 'artificial intelligence', 'ai', 'automation',
            'cs', 'cse', 'it', 'software', 'data science'
        ])
        
        # Also check StudentProfileState for major (might be extracted from previous messages)
        if student_state.major:
            mentions_major = True
        
        # Generic questions that should NOT show lead form
        generic_questions = [
            'what is china', 'living in china', 'life in china', 'food in china', 'weather in china',
            'culture in china', 'general scholarship', 'scholarship in general', 'how is china',
            'tell me about china', 'about china', 'china general', 'general information'
        ]
        is_generic = any(term in user_msg_lower for term in generic_questions)
        
        # Determine if should show lead form
        # CRITICAL RULE: Do NOT show lead form in the first answer
        # Show lead form ONLY after:
        # 1. Agent has engaged with user (not first interaction)
        # 2. Agent has confirmed user is viable (major is offered by MalishaEdu OR user agreed to related major)
        # 3. User has provided enough info OR explicitly agreed to fill form
        
        # Check if we have enough information to show form (major + degree_level + nationality)
        has_enough_info = (
            student_state.major is not None and 
            student_state.degree_level is not None and 
            student_state.nationality is not None
        )
        
        # Check if this is the first interaction (no assistant messages yet)
        num_assistant_messages = sum(1 for m in conversation_history if m.get("role") == "assistant")
        is_first_interaction = (num_assistant_messages == 0)
        
        # Check if major is offered by MalishaEdu (from RAG or DB)
        # This will be checked in the context building phase
        major_is_offered = False
        if student_state.major and use_db:
            # If we have DB access, check if major exists in database
            matched_majors = self.db_service.search_majors(
                name=student_state.major,
                degree_level=student_state.degree_level,
                limit=1
            )
            major_is_offered = len(matched_majors) > 0
        elif student_state.major:
            # For anonymous users, check RAG for major mentions
            # If related_majors_context exists, it means the major is likely not offered
            if related_majors_context:
                major_is_offered = False  # Major not offered, but we have related majors
            else:
                major_is_offered = None  # Unknown, will be determined from RAG context
        
        # Determine if user is viable (agent is satisfied they can be served)
        # User is viable if:
        # - Major is offered by MalishaEdu, OR
        # - User has agreed to consider related majors, OR
        # - Major is unknown but user shows strong interest
        user_is_viable = False
        if major_is_offered is True:
            user_is_viable = True
        elif major_is_offered is None:
            # Unknown - check if user has shown strong interest and we have enough info
            # We'll let the agent persuade first, then show form
            user_is_viable = has_enough_info and not is_first_interaction
        else:
            # Major is not offered - user needs to be persuaded first
            # Check if user has agreed to consider related majors (this will be in conversation)
            user_agreed_to_related = any(term in user_msg_lower for term in [
                'yes', 'ok', 'sure', 'interested', 'that works', 'sounds good', 'i agree'
            ]) and any(term in user_msg_lower for term in [
                'related', 'similar', 'alternative', 'instead', 'that major', 'those majors'
            ])
            user_is_viable = user_agreed_to_related
        
        # AUTOMATIC LEAD COLLECTION (NO POPUP FORM)
        # Criteria for automatic lead collection:
        # a) User provides nationality
        # b) User provides phone number OR email OR whatsapp/wechat number
        # c) User provides interest in China study for any degree level (Master, Bachelor, PhD, Language program, etc.)
        
        # Check if we have contact info (phone OR email OR whatsapp OR wechat)
        has_contact_info = (
            (student_state.phone and len(student_state.phone) >= 10) or
            (student_state.email and '@' in student_state.email) or
            (student_state.whatsapp and len(student_state.whatsapp) >= 10) or
            (student_state.wechat and len(student_state.wechat) >= 3)
        )
        
        # Check if user has interest in China study (any degree level)
        has_study_interest = (
            student_state.degree_level is not None or
            student_state.major is not None or
            any(term in user_msg_lower for term in ['study', 'studying', 'want to study', 'interested in', 'apply', 'application', 'admission', 'enroll'])
        )
        
        # Check if all criteria are met for automatic lead collection
        should_collect_lead = (
            not lead_collected and  # Don't create duplicate leads
            student_state.nationality is not None and  # Nationality provided
            has_contact_info and  # Contact info provided
            has_study_interest  # Interest in studying in China
        )
        
        # Automatically collect lead if criteria met
        lead_created = False
        if should_collect_lead:
            lead_created = self._auto_collect_lead(
                student_state=student_state,
                chat_session_id=chat_session_id,
                device_fingerprint=device_fingerprint,
                conversation_history=conversation_history
            )
            if lead_created:
                lead_collected = True
                use_db = True  # Enable DB access after lead collection
        
        # NEVER show lead form popup (always False)
        shows_interest = False
        
        # Step 4: Build context for LLM
        context_parts = []
        
        # Add DATABASE UNIVERSITIES and DATABASE MAJORS arrays at the beginning
        # Format universities list
        if self.all_universities:
            uni_list = []
            for uni in self.all_universities:
                uni_info = f"- {uni['name']}"
                if uni['city']:
                    uni_info += f" ({uni['city']}"
                    if uni['province']:
                        uni_info += f", {uni['province']}"
                    uni_info += ")"
                if uni['ranking']:
                    uni_info += f" [Ranking: {uni['ranking']}]"
                uni_list.append(uni_info)
            context_parts.append(f"DATABASE UNIVERSITIES (MalishaEdu Partner Universities - Use this list for all university questions):\n" + "\n".join(uni_list))
        
        # Format majors list with university and degree level
        if self.all_majors:
            major_list = []
            for major in self.all_majors[:200]:  # Limit to 200 to avoid token limits
                major_info = f"- {major['name']}"
                major_info += f" at {major['university_name']}"
                if major['degree_level']:
                    major_info += f" ({major['degree_level']})"
                if major['teaching_language']:
                    major_info += f" [{major['teaching_language']}]"
                major_list.append(major_info)
            context_parts.append(f"DATABASE MAJORS (MalishaEdu Majors with University and Degree Level - Use this list for all major questions):\n" + "\n".join(major_list))
            if len(self.all_majors) > 200:
                context_parts.append(f"... and {len(self.all_majors) - 200} more majors in the database")
        
        if matched_programs:
            context_parts.append(f"DATABASE MATCHES:\n{matched_programs}")
        if db_context:
            context_parts.append(f"DATABASE INFORMATION:\n{db_context}")
        if rag_context:
            context_parts.append(f"KNOWLEDGE BASE:\n{rag_context}")
        if tavily_context:
            context_parts.append(f"WEB SEARCH RESULTS:\n{tavily_context}")
        
        full_context = "\n\n".join(context_parts) if context_parts else None
        
        # Step 5: Generate response with OpenAI
        # Add current date awareness to system prompt
        current_date_instruction = f"\n\nCURRENT DATE: Today is {current_date_str} (Year: {current_year}, Month: {current_month}). When suggesting intake years or deadlines, ALWAYS use FUTURE dates relative to this date. If user says 'this march' or 'next march', calculate based on the current date. NEVER suggest past dates (e.g., if it's December 2025, do NOT suggest March 2024)."
        system_prompt_with_date = self.SALES_SYSTEM_PROMPT + current_date_instruction
        messages = [
            {"role": "system", "content": system_prompt_with_date}
        ]
        
        # Add anonymous user instruction if use_db=False
        if not use_db:
            messages.append({
                "role": "system",
                "content": "CRITICAL: This user is ANONYMOUS and has NOT provided structured lead data. DO NOT use database program/intake information. Answer ONLY using the RAG knowledge base and, if needed, the web search context. Even when answering using RAG or your own knowledge, you MUST NEVER mention or suggest any non-partner universities. If a university is not clearly identified as a MalishaEdu partner in the DATABASE context, you must speak in generic terms (e.g. ‘our partner universities’) and avoid naming it.If RAG or your pretraining shows examples of Chinese universities that are not in the partner list, IGNORE those names and DO NOT use them in your answer. Do NOT invent exact tuition or deadlines: use approximate values from RAG documents. Do NOT mention 'your database record' or any persistent profile."
            })
        
        # CRITICAL: Add dynamic profile instruction based on known vs missing fields
        # This is the single source of truth for what the agent knows and can ask
        profile_summary_lines = []
        for k, v in known_fields.items():
            profile_summary_lines.append(f"{k}: {v}")
        profile_summary = "\n".join(profile_summary_lines) if profile_summary_lines else "None"
        
        missing_summary = ", ".join(missing_fields) if missing_fields else "None"
        
        dynamic_profile_instruction = f"""The following student profile state has been extracted from the conversation (this is authoritative):

{profile_summary}

Missing or unknown fields: {missing_summary}

**Hard rules:**
- NEVER ask again for any field that appears in the profile above.
- You may only ask about fields listed under "Missing or unknown fields".
- If degree_level, major, or nationality are known, you MUST naturally reuse them in your first 1–2 sentences.Example: ‘Since you’re interested in a Master’s in Computer Science and you’re from Bangladesh, here’s what I’d suggest…’Do NOT re-ask for them, just reuse them to make the reply feel personalized.”
- Ask about at most TWO missing fields in a single reply.
- If all important fields are known, do not ask follow-up questions just to repeat them; instead, directly answer the user's question or give next steps.
- If the user says "yes let's start" or similar agreement without adding new info, do NOT re-ask for fields that are already in the profile above."""
        
        messages.append({
            "role": "system",
            "content": dynamic_profile_instruction
        })
        
        # Detect if this is a first interaction - only the very first assistant reply
        # Count assistant messages to determine if this is truly the first interaction
        # Note: This is already computed above, but we'll use it here too for consistency
        
        # CRITICAL: Add conversation history FIRST so LLM can see everything
        if conversation_slice:
            # Add the FULL conversation history FIRST
            messages.extend(conversation_slice)
            
            # Convert StudentProfileState to summary string
            state_summary_text = self._state_to_summary_string(student_state)
            
            # Add state summary as system message - CRITICAL: This prevents re-asking known info
            if state_summary_text and state_summary_text != "No specific preferences mentioned yet.":
                # Build explicit forbidden questions list
                forbidden_questions = []
                if student_state.major:
                    forbidden_questions.append(f"❌ DO NOT ask 'What is your preferred major?' or 'Which major are you interested in?' or 'What subject or major are you interested in?' - Major is ALREADY: {student_state.major} (this works for ANY major: Biology, Physics, Chemistry, Computer Science, Mechanical Engineering, Business, Medicine, Economics, Telecommunication, etc.)")
                if student_state.degree_level:
                    forbidden_questions.append(f"❌ DO NOT ask 'Bachelor's, Master's, or PhD?' - Degree level is ALREADY: {student_state.degree_level}")
                if student_state.city:
                    forbidden_questions.append(f"❌ DO NOT ask 'Which city?' or 'Do you have a preferred city?' - City is ALREADY: {student_state.city}")
                if student_state.nationality:
                    forbidden_questions.append(f"❌ DO NOT ask 'What is your nationality?' or 'Which country are you from?' - Nationality is ALREADY: {student_state.nationality}")
                
                forbidden_text = "\n".join(forbidden_questions) if forbidden_questions else ""
                
                messages.append({"role": "system", "content": f"""The conversation so far tells you this about the user:

{state_summary_text}

{forbidden_text}

CRITICAL RULES:
- Do NOT ask again for any field that is already known (degree level, major, nationality, city, university, intake term, intake year).
- If the user said they want "masters" or "master's" → they want Master's programs. DO NOT ask "Bachelor, Master or PhD?" again.
- If the user already mentioned "telecommunication", "Artificial Intelligence", "Economics", "Computer Science", or any other major, DO NOT ask "which subject/major" again unless they explicitly say they want to change it.
- If the user already gave their nationality, DO NOT ask for nationality again.
- If earlier they wanted Chinese language but later clearly say they want a Master's degree in a different field, FOLLOW THE LATEST STATEMENT. The latest message overrides earlier ones.
- If both a language program and a degree program are mentioned in the conversation, you MUST follow the latest user intent. Do not mix them.
- Do NOT talk about Chinese language programs when the user clearly wants a Master's, Bachelor's, or PhD program in a specific major.
- If degree_level is "Master", "Bachelor", or "PhD", then program_type should be "Degree", NOT "Language", unless the user explicitly wants a language program.

YOU MUST FOLLOW THESE RULES STRICTLY:
1. Treat this StudentProfileState as the AUTHORITATIVE summary of what the user wants. DO NOT IGNORE IT.
2. NEVER ask for information that already exists in the state above - this includes major, degree_level, city, nationality, etc.
3. **CRITICAL**: If state.major is set (ANY major: "Artificial Intelligence", "AI", "Mechanical Engineering", "Computer Science", "Biology", "Physics", "Chemistry", "Business", "Medicine", "Economics", "Telecommunication", etc.), DO NOT ask "What is your preferred major?" or "Which major are you interested in?" or "What subject or major are you interested in?" - the major is already known. USE IT in your response immediately.
4. **CRITICAL**: If state.degree_level is set (e.g., "Bachelor", "Master", "PhD"), DO NOT ask "Which degree level?" or "Bachelor's, Master's, or PhD?" - proceed with that degree level immediately.
5. NEVER contradict the state without explicit user statement changing it.
6. **CRITICAL**: If state.major is set to ANY degree program (e.g., "Artificial Intelligence", "Computer Science", "Mechanical Engineering", "Biology", "Physics", "Economics", "Telecommunication", etc.) and program_type is "Degree", you MUST NOT say "Chinese language program" unless the user clearly switches to that.
7. If state.city is set (e.g., Beijing), do NOT ask about preferred city again unless the user says they want to change city.
8. If state.degree_level is "Master", do NOT ask "Bachelor's, Master's, or PhD?" - proceed with Master's programs. The user already said "masters" which means Master's degree.
9. If state.nationality is set (e.g., "Kazakhstan"), use it in your response. If state.nationality is None, use neutral phrases like "international students" or "students like you", NOT a specific country.
10. Provide SPECIFIC answers based on the state above.
11. Use the database matches provided to give EXACT values (fees, deadlines, documents).
12. Answer the user's current question directly using the state information, then only ask for missing information if absolutely necessary.
13. **DO NOT SWITCH TOPICS**: If the user said "Economics" and "masters", you MUST respond about Master's in Economics. Do NOT switch to Chinese language programs.

CRITICAL EXAMPLES:
- If state.major = "Economics" and state.degree_level = "Master" → Respond about "Master's in Economics", DO NOT ask "What subject or major are you interested in?" or "Which degree level?" again.
- If state.major = "Telecommunication" and state.degree_level = "Master" → Respond about "Master's in Telecommunication", DO NOT ask again.
- If user said "Economics masters" in first message → You MUST use "Master's in Economics" in your response, DO NOT ask again.
- If user said "I want to do masters in telecommunication" → You MUST use "Master's in Telecommunication", DO NOT ask again.

DO NOT BE GENERIC. Be specific and helpful based on the state above."""})
            else:
                messages.append({"role": "system", "content": "CRITICAL: Read the conversation history above carefully. Use ALL information the user has already provided. Do NOT ask for information that was already mentioned. If nationality is unknown, use neutral phrases like 'international students', NOT a specific country."})
        
        # Add related majors context if major is not offered
        if related_majors_context and not major_is_offered:
            if full_context:
                full_context = f"{full_context}\n\nRELATED MAJORS FROM MALISHAEDU:\n{related_majors_context}"
            else:
                full_context = f"RELATED MAJORS FROM MALISHAEDU:\n{related_majors_context}"
        
        # Add context if available
        if full_context:
            # Check if there's uncertainty that needs confirmation
            needs_confirmation = "UNCERTAINTY DETECTED" in full_context or "ACTION REQUIRED" in full_context
            
            if needs_confirmation:
                context_instruction = f"""CRITICAL: UNCERTAINTY DETECTED - ASK FOR CONFIRMATION

The database query found multiple possible matches for what the user mentioned. You MUST ask the user to confirm which one they mean before providing any information.

DATABASE INFORMATION:
{full_context}

ACTION REQUIRED:
1. Present the options to the user clearly
2. Ask them to confirm which university/major they mean
3. DO NOT guess or assume
4. DO NOT provide information until they confirm
5. Be friendly and helpful: "I found a few options that might match. Could you confirm which one you mean?" """
            else:
                context_instruction = f"""CRITICAL INSTRUCTIONS FOR USING DATABASE INFORMATION:

1. The DATABASE MATCHES section below contains EXACT values from the database. USE ONLY THIS INFORMATION.
2. **CRITICAL: ONLY suggest universities that are listed in the DATABASE MATCHES below. ALL universities in DATABASE MATCHES are MalishaEdu partner universities (is_partner = True).**
3. **CRITICAL: DO NOT use general knowledge to suggest non-partner universities like Fudan, Shanghai Jiao Tong, Zhejiang University, Nanjing University, Sun Yat-sen, Peking, Tsinghua, etc. If user asks for "top ranked" or "any university", ONLY suggest from DATABASE MATCHES.**
4. DO NOT suggest universities or programs that are NOT listed in the DATABASE MATCHES below.
5. If DATABASE MATCHES shows specific fees (per semester or per year) → USE THOSE EXACT VALUES, do NOT use approximate ranges.
6. If DATABASE MATCHES shows specific documents_required → USE THAT EXACT LIST, do NOT provide generic document lists.
7. If DATABASE MATCHES shows specific intake dates and deadlines → USE THOSE EXACT DATES.
8. If DATABASE MATCHES shows scholarship_info → USE THAT EXACT INFORMATION.
9. NEVER say "typically" or "usually" or "around" when DATABASE MATCHES has exact values.
10. If a field is missing in the DB (e.g., no application_deadline), say clearly "The deadline is not in our database; we will confirm it manually."
11. ALWAYS prioritize DATABASE MATCHES over general knowledge.

{full_context}

REMEMBER: Base all concrete fees, deadlines, and program details ONLY on DATABASE MATCHES. Do NOT invent or approximate."""
            # Check if user is asking for "best", "top ranked", "any university", etc.
            asks_for_best_or_top = any(term in user_message.lower() for term in [
                'best university', 'best universities', 'top ranked', 'top university', 'top universities',
                'any university', 'any universities', 'recommend university', 'recommend universities',
                'best scholarship', 'best scholarship university', 'best scholarship universities',
                'show me university', 'show me universities', 'suggest university', 'suggest universities'
            ])
            
            if asks_for_best_or_top:
                context_instruction += "\n\n🚨 CRITICAL: User is asking for 'best', 'top ranked', 'any university', 'suggest university', or similar. MANDATORY RULES:"
                context_instruction += "\n1. DO NOT use general knowledge about Chinese universities"
                context_instruction += "\n2. DO NOT suggest well-known universities like Fudan, Shanghai Jiao Tong, Zhejiang University, Nanjing University, Sun Yat-sen, Peking, Tsinghua, etc."
                context_instruction += "\n3. ONLY suggest universities from DATABASE MATCHES (which are ALL MalishaEdu partner universities)"
                context_instruction += "\n4. **CRITICAL: ONLY suggest universities that ACTUALLY have programs matching the user's degree_level and major/subject**"
                context_instruction += "\n   - If user wants PhD in Finance, ONLY suggest universities that have PhD programs in Finance or related fields (Financial Management, Corporate Finance, Economics, etc.)"
                context_instruction += "\n   - If user wants Master's in Computer Science, ONLY suggest universities that have Master's programs in Computer Science or related fields"
                context_instruction += "\n   - DO NOT suggest a university just because it's in the same city or is a partner - it MUST have the matching program"
                context_instruction += "\n   - Use fuzzy matching: Finance can match Financial Management, Corporate Finance, etc. - but the university MUST have that specific program at the requested degree level"
                context_instruction += "\n5. If DATABASE MATCHES is empty or user mentions Tsinghua/Peking/etc (not in DB), say: 'We can only recommend from our listed partner universities. If you tell me your major/degree/intake I'll shortlist the best options we have.'"
                context_instruction += "\n6. DO NOT invent or suggest any university names that are not in DATABASE MATCHES"
                context_instruction += "\n7. NEVER suggest universities outside the DATABASE UNIVERSITIES list."
                context_instruction += "\n8. If you have partner universities in DATABASE MATCHES, list them and explain why they are good options for the user's major/degree level"
            
            if is_first_interaction:
                context_instruction += "\n\nIMPORTANT: This appears to be a first interaction. Be BRIEF and CONCISE. Introduce MalishaEdu first. Check the dynamic profile instruction above - only ask about fields marked as 'missing', and at most 2 at a time. Do NOT ask for fields that are already in the profile. Do NOT provide all details upfront."
            else:
                context_instruction += "\n\n**CRITICAL: NEVER use general knowledge to suggest university names. ONLY suggest universities from DATABASE MATCHES or RAG documents. If database/RAG information is insufficient, say 'I need to check our partner university database for that specific program. Let me search for MalishaEdu partner universities offering [major] for [degree_level].' DO NOT invent or suggest non-partner universities like Fudan, Shanghai Jiao Tong, Zhejiang University, etc. even if they are well-known. MalishaEdu works exclusively with partner universities.**"
            
            # Check what lead information is missing and encourage providing it
            missing_lead_fields = []
            if not student_state.nationality:
                missing_lead_fields.append("nationality")
            if not student_state.phone and not student_state.email and not student_state.whatsapp and not student_state.wechat:
                missing_lead_fields.append("contact information (phone/email/whatsapp/wechat)")
            if not student_state.degree_level:
                missing_lead_fields.append("degree level")
            if not student_state.intake_term:
                missing_lead_fields.append("intake term")
            if not student_state.intake_year:
                missing_lead_fields.append("intake year")
            if not student_state.major:
                missing_lead_fields.append("major/subject")
            # Only add university to missing fields if user is certain about wanting a specific university
            # If user is uncertain, don't push for university name
            if not student_state.preferred_universities and student_state.university_certainty != "uncertain":
                missing_lead_fields.append("preferred university (optional)")
            
            if missing_lead_fields and not lead_collected:
                context_instruction += f"\n\nMISSING LEAD INFORMATION: User has not provided: {', '.join(missing_lead_fields)}. "
                context_instruction += "Encourage user to provide this information naturally through conversation. "
                context_instruction += "DO NOT encourage signup until user provides nationality + contact info + study interest (degree level or major). "
                context_instruction += "After user provides degree_type, nationality, intake, intake_year, major - encourage providing rest of lead info (contact information)."
            
            # Handle university uncertainty
            if student_state.university_certainty == "uncertain":
                context_instruction += "\n\nUNIVERSITY UNCERTAINTY: User is uncertain about which university they want. DO NOT push for university name. Instead, focus on:"
                context_instruction += "\n1. Finding the best matching major/subject for their interest"
                context_instruction += "\n2. Providing information about programs and options"
                context_instruction += "\n3. Encouraging lead collection (nationality, contact info, major, degree level, intake)"
                context_instruction += "\n4. Once lead is collected, you can suggest partner universities that match their major/degree level"
                context_instruction += "\n5. DO NOT repeatedly ask 'which university do you want?' - focus on other information gathering"
            
            # After providing degree_type, nationality, intake, intake_year - encourage providing contact info
            has_basic_info = (
                student_state.degree_level and
                student_state.nationality and
                student_state.intake_term and
                student_state.intake_year
            )
            missing_contact = (
                not student_state.phone and
                not student_state.email and
                not student_state.whatsapp and
                not student_state.wechat
            )
            
            if has_basic_info and missing_contact and not lead_collected:
                context_instruction += "\n\nENCOURAGE CONTACT INFO: User has provided degree_type, nationality, intake_term, and intake_year. "
                context_instruction += "Encourage them to provide contact information (phone number, email, WhatsApp, or WeChat) to complete their profile. "
                context_instruction += "Say: 'To help you better and provide personalized recommendations, could you please share your contact information (phone number, email, WhatsApp, or WeChat)?'"
            
            # Add lead collection status and urgency info
            if lead_collected:
                context_instruction += "\n\nLEAD COLLECTED: User has already provided lead information (nationality + contact info + study interest). Provide ALL specific information from database. Strongly pursue signup/login if not logged in."
            else:
                # Check if this is a simple informational query
                is_simple_info = any(phrase in user_message.lower() for phrase in [
                    'what majors', 'which majors', 'list majors', 'show majors', 'available majors',
                    'what programs', 'which programs', 'list programs', 'show programs', 'available programs',
                    'what does', 'what offers', 'does offer', 'offers', 'has'
                ])
                if is_simple_info:
                    context_instruction += "\n\nSIMPLE INFORMATIONAL QUERY - NO LEAD: User is asking for basic information (listing majors/programs). Answer directly from database. After providing the information, gently encourage: 'For personalized program recommendations and detailed application guidance, please sign up at /signup (or /login).'"
                else:
                    context_instruction += "\n\nLEAD NOT COLLECTED: For personalized recommendations, encourage user to sign up at /signup (or /login) to get personalized recommendations."
            
            # Add cost-specific instructions if user asks about cost
            if sales_intent in ['fees_only', 'fees_compare']:
                if lead_collected:
                    context_instruction += "\n\nCOST QUESTION - LEAD COLLECTED: Use RAG cost profiles for general estimates AND check database for specific program costs. Provide comprehensive breakdown: application fee (non-refundable), MalishaEdu application deposit (80 USD, refundable if no admission due to MalishaEdu), tuition, accommodation, insurance, medical checkup, living cost, visa renewal, and MalishaEdu service charge (based on degree level, teaching language, and scholarship type - search RAG for 'MalishaEdu service charges' table). Calculate total first-year cost including service charge. STRONGLY encourage signup/login for precise information."
                else:
                    context_instruction += "\n\nCOST QUESTION - NO LEAD: Use RAG cost profiles (ranking-based: reputed/average/below_average or university-specific). Provide comprehensive breakdown: application fee (non-refundable), MalishaEdu application deposit (80 USD, refundable if no admission due to MalishaEdu), tuition, accommodation, insurance, medical checkup, living cost, visa renewal, and MalishaEdu service charge (based on degree level, teaching language, and scholarship type - search RAG for 'MalishaEdu service charges' table). Calculate total first-year cost including service charge. After providing generic cost, STRONGLY ENCOURAGE user to sign up at /signup (or /login) for precise information based on their specific profile."
            
            # Add CSCA/scholarship handling instructions
            if asks_about_csca or asks_about_scholarship:
                context_instruction += "\n\nCSCA/SCHOLARSHIP QUESTION - RAG-BASED ANSWER REQUIRED:"
                context_instruction += "\n- Use ONLY the RAG knowledge documents provided as your factual source."
                context_instruction += "\n- DO NOT assume you have per-university data in a database for anonymous users."
                context_instruction += "\n- DO NOT fabricate university-specific CSCA rules or scholarship amounts if they are not clearly stated in the RAG context."
                context_instruction += "\n- Quote or paraphrase what the MalishaEdu blog and other official/explainer docs say."
                context_instruction += "\n- If RAG doesn't contain specific details, say: 'Our current knowledge base does not specify this detail. CSCA policies are still evolving. We'll check the latest official notice and update you.'"
                context_instruction += "\n- Make clear distinction between GENERAL CSCA POLICY (from RAG) and SPECIFIC university rules (which may vary and change)."
                context_instruction += "\n- NEVER change the student's major or program type unless they clearly request it."
                context_instruction += "\n- NEVER assume nationality or city; always reuse what the user said."
                if asks_about_csca:
                    context_instruction += "\n- For CSCA: Emphasize it's mainly for Bachelor/undergraduate programs. Most Master's and PhD programs do NOT require CSCA."
                if asks_about_scholarship:
                    context_instruction += "\n- For scholarships: Explain that CSCA is directly tied to CSC and many undergraduate scholarships from 2026 onward. For Master's/PhD scholarships, CSCA is not the primary filter."
            
            # Add urgency warning if deadline within 1 month
            if matched_programs and 'URGENT APPLICATION DEADLINES' in db_context:
                context_instruction += "\n\n⚠️ URGENT: Application deadline(s) are within 1 month! WARN user and STRONGLY encourage signup/login immediately. List necessary documents from database."
            
            # Check if user mentioned a non-partner university (need to divert)
            mentions_non_partner_uni = False
            if (db_context and "NOT a MalishaEdu partner university" in db_context) or non_partner_university_mentioned:
                mentions_non_partner_uni = True
                uni_name = non_partner_university_mentioned if non_partner_university_mentioned else "the mentioned university"
                context_instruction += f"\n\n🚨 CRITICAL: User mentioned '{uni_name}' which is NOT a MalishaEdu partner university. MANDATORY ACTIONS:"
                context_instruction += "\n1. DO NOT provide detailed information about this non-partner university (costs, deadlines, application process, duration, etc.)"
                context_instruction += f"\n2. IMMEDIATELY mention: 'I understand you're interested in {uni_name}. However, MalishaEdu is one of the biggest education agent services in China and works exclusively with partner universities where we can actually provide 100% admission support, scholarship guidance, and full post-arrival services.'"
                context_instruction += "\n3. STRONGLY EMPHASIZE MalishaEdu's capabilities: 'At our partner universities, MalishaEdu can guarantee admission support, help with scholarship applications, and provide complete post-arrival services including airport pickup, accommodation, bank account setup, SIM card, and dedicated counselor support. This is something we cannot guarantee for non-partner universities.'"
                context_instruction += "\n4. DIVERT to partner universities offering the SAME major/degree level (use the partner university list from RAG/DB context above)"
                context_instruction += "\n5. DO NOT waste prompts providing information about the non-partner university - immediately divert to partner alternatives"
                context_instruction += "\n6. If partner universities are listed in RAG/DB context above, mention them by name and emphasize MalishaEdu's services at those universities"
            
            # After automatic lead collection - encourage signup and provide personalized info
            if lead_collected or ('lead_created' in locals() and lead_created):
                # Check if major/university matches MalishaEdu supported ones
                major_matches = False
                university_matches = False
                
                if student_state.major:
                    is_malishaedu_major, matched_major = self._is_malishaedu_major(student_state.major)
                    major_matches = is_malishaedu_major
                
                if student_state.preferred_universities:
                    is_partner, partner_uni = self._is_malishaedu_partner_university(student_state.preferred_universities[0])
                    university_matches = is_partner
                
                if major_matches and university_matches:
                    # Both match - provide personalized data from database
                    context_instruction += "\n\nLEAD COLLECTED - PERSONALIZED DATA AVAILABLE: User's lead has been automatically collected. Their major and university match MalishaEdu's supported options. Use the database to provide EXACT fees, deadlines, scholarship information, and documents_required. Provide comprehensive personalized information based on their specific profile."
                elif major_matches or university_matches:
                    # One matches - provide partial personalized data
                    context_instruction += "\n\nLEAD COLLECTED - PARTIAL MATCH: User's lead has been automatically collected. Some of their preferences match MalishaEdu's supported options. Use the database where possible, and encourage them toward fully supported options for complete personalized information."
                else:
                    # Neither matches - encourage toward supported options
                    context_instruction += "\n\nLEAD COLLECTED - ENCOURAGE SUPPORTED OPTIONS: User's lead has been automatically collected, but their major/university preferences don't fully match MalishaEdu's supported options. Use RAG data to suggest related majors and partner universities. Keep encouraging them toward MalishaEdu's supported majors and partner universities for personalized database information."
                
                # Always encourage signup after lead collection
                context_instruction += "\n\nSIGNUP ENCOURAGEMENT: User's lead information has been collected (nationality + contact info + study interest). STRONGLY encourage them to sign up for a free MalishaEdu account to apply. Say: 'Thank you for providing your information! MalishaEdu provides the most comprehensive solution for studying in China. To apply with us, please sign up for a free account at /signup (or log in at /login if you already have one). Once you sign up, you can upload your required documents, track your application progress, and our admission agent will guide you through the entire process.'"
                context_instruction += "\n\nIMPORTANT: After lead collection, when user asks specific questions about scholarship, cost, accommodation, or admission chances, you MUST answer them based on the lead data collected. Use the database to provide precise information based on their university, major, degree level, and intake preferences (if they match MalishaEdu supported options)."
            elif is_first_interaction:
                if related_majors_context and not major_is_offered:
                    context_instruction += "\n\nCRITICAL: This is the first interaction. The user's major may not be offered by MalishaEdu. RELATED MAJORS are provided in the context above. FIRST: Persuade the user to consider related majors from MalishaEdu. Search the RELATED MAJORS context and suggest 3-5 similar majors. Keep engaging with the user to collect their nationality and contact information naturally through conversation."
                else:
                    context_instruction += "\n\nCRITICAL: This is the first interaction. First engage with the user, understand their needs. If their major is not offered by MalishaEdu, persuade them to consider related majors. Keep engaging naturally to collect their nationality and contact information (phone, email, whatsapp, or wechat) through conversation."
            
            messages.append({"role": "system", "content": context_instruction})
        elif is_first_interaction:
            # CRITICAL: First interaction - engage naturally, collect info through conversation
            messages.append({"role": "system", "content": "CRITICAL: This is the first interaction. Engage naturally with the user. If their major is not offered by MalishaEdu, search RAG for related majors and persuade them to consider alternatives. Through natural conversation, try to collect: nationality, contact information (phone/email/whatsapp/wechat), and their study interests. Be BRIEF and CONCISE. Introduce MalishaEdu first, then ask 1-2 key questions. Do NOT provide all details upfront."})
        
        # Add current user message
        messages.append({"role": "user", "content": user_message})
        
        # Generate response
        response = self.openai_service.chat_completion(messages)
        answer = response.choices[0].message.content
        
        # Step 6: Reflection - improve answer if important
        if any(keyword in user_message.lower() for keyword in ['scholarship', 'fee', 'tuition', 'requirement', 'deadline', 'csca']):
            improved_answer = self.openai_service.reflect_and_improve(
                answer,
                db_context or "",
                tavily_context
            )
            answer = improved_answer
        
        # Step 6.5: Add single lead collection question for program-specific queries
        # (FAQ/CSCA queries already have lead question added in their early returns)
        # Skip lead question for general process/FAQ questions that don't require personalization
        is_general_process_question = any(phrase in user_message.lower() for phrase in [
            'how do you handle', 'how does', 'what is the process', 'what is the procedure',
            'how does the process work', 'explain the process', 'tell me about the process',
            'application process', 'admission process', 'how it works', 'how does it work'
        ]) and not any(phrase in user_message.lower() for phrase in [
            'my', 'for me', 'my application', 'my admission', 'i want', 'i need'
        ])
        
        if (intent == 'program_specific' or needs_db) and not is_general_process_question:
            lead_question = self._build_single_lead_question(student_state, audience=audience)
            if lead_question:
                answer = answer + f"\n\n{lead_question}"
        
        # Return response with all context
        # lead_collected is already computed from use_db/profile_complete earlier
        
        # DEBUG: Log automatic lead collection
        print(f"\n{'='*80}")
        print(f"DEBUG: SalesAgent.generate_response - Automatic Lead Collection")
        print(f"DEBUG: lead_collected (before) = {lead_collected}")
        print(f"DEBUG: lead_created = {lead_created if 'lead_created' in locals() else 'N/A'}")
        print(f"DEBUG: lead_collected (after) = {lead_collected}")
        print(f"DEBUG: student_state.nationality = {student_state.nationality}")
        print(f"DEBUG: student_state.phone = {student_state.phone}")
        print(f"DEBUG: student_state.email = {student_state.email}")
        print(f"DEBUG: student_state.whatsapp = {student_state.whatsapp}")
        print(f"DEBUG: student_state.wechat = {student_state.wechat}")
        print(f"DEBUG: student_state.degree_level = {student_state.degree_level}")
        print(f"DEBUG: student_state.major = {student_state.major}")
        print(f"DEBUG: has_contact_info = {has_contact_info if 'has_contact_info' in locals() else 'N/A'}")
        print(f"DEBUG: has_study_interest = {has_study_interest if 'has_study_interest' in locals() else 'N/A'}")
        print(f"DEBUG: should_collect_lead = {should_collect_lead if 'should_collect_lead' in locals() else 'N/A'}")
        print(f"{'='*80}\n")
        
        # Prepare pre-filled form data based on StudentProfileState (for reference, not for popup)
        prefill_data = {}
        # Always prepare prefill data (even though we don't show popup, it's useful for reference)
        if student_state.degree_level:
            prefill_data['degree_type'] = student_state.degree_level
        if student_state.major:
            prefill_data['subject_major'] = student_state.major
        if student_state.nationality:
            prefill_data['nationality'] = student_state.nationality
        if student_state.city:
            prefill_data['preferred_city'] = student_state.city
        if student_state.intake_term:
            prefill_data['intake'] = student_state.intake_term
        if student_state.intake_year:
            prefill_data['intake_year'] = student_state.intake_year
        if student_state.preferred_universities:
            prefill_data['university'] = student_state.preferred_universities[0] if student_state.preferred_universities else None
        
        return {
            'response': answer,
            'db_context': db_context,
            'rag_context': rag_context,
            'tavily_context': tavily_context,
            'lead_collected': lead_collected,  # Already computed from use_db/profile_complete
            'show_lead_form': False,  # NEVER show lead form popup - leads are collected automatically
            'lead_form_prefill': prefill_data  # Pre-fill data (for reference, not for popup)
        }
    
    def _extract_conversation_context(self, conversation_history: List[Dict[str, str]]) -> set:
        """Extract key information from conversation history using both keyword matching and LLM"""
        extracted_info = set()
        full_conversation_text = ""
        
        # Build conversation text
        for msg in conversation_history:
            role = msg.get('role', '')
            content = msg.get('content', '')
            full_conversation_text += f"{role}: {content}\n"
            
            if role == 'user':
                content_lower = content.lower()
                
                # Extract major (expanded list)
                if any(term in content_lower for term in ['cse', 'computer science', 'cs', 'computer engineering', 'software engineering', 'computing']):
                    extracted_info.add("Major: Computer Science/Engineering")
                elif any(term in content_lower for term in ['physics', 'physical science']):
                    extracted_info.add("Major: Physics")
                elif any(term in content_lower for term in ['chemistry', 'chemical']):
                    extracted_info.add("Major: Chemistry")
                elif any(term in content_lower for term in ['biology', 'biological']):
                    extracted_info.add("Major: Biology")
                elif any(term in content_lower for term in ['science', 'sciences']) and 'physics' not in content_lower and 'chemistry' not in content_lower and 'biology' not in content_lower:
                    extracted_info.add("Major: Science (general)")
                elif 'engineering' in content_lower:
                    extracted_info.add("Major: Engineering")
                elif any(term in content_lower for term in ['business', 'mba', 'management', 'commerce']):
                    extracted_info.add("Major: Business/Management")
                elif any(term in content_lower for term in ['medicine', 'medical', 'mbbs']):
                    extracted_info.add("Major: Medicine")
                elif any(term in content_lower for term in ['material science', 'materials']):
                    extracted_info.add("Major: Material Science")
                
                # Extract degree level (expanded - check for masters FIRST before bachelor to avoid false matches)
                # Check for "complete my masters" or similar phrases first
                if any(term in content_lower for term in ['complete my masters', 'complete masters', 'finish my masters', 'finish masters']):
                    extracted_info.add("Degree Level: Master")
                elif any(term in content_lower for term in ['master', 'masters', 'master\'s', 'ms', 'm.sc', 'm.sc.', 'mba', 'm.tech', 'm.eng', 'postgraduate', 'pg']):
                    extracted_info.add("Degree Level: Master")
                elif any(term in content_lower for term in ['phd', 'ph.d', 'ph.d.', 'doctorate', 'doctoral', 'doctor of philosophy', 'd.phil']):
                    extracted_info.add("Degree Level: PhD")
                elif any(term in content_lower for term in ['bachelor', 'bachelors', 'bs', 'b.sc', 'b.sc.', 'b.tech', 'b.eng', 'undergraduate', 'ug', 'hsc']):
                    extracted_info.add("Degree Level: Bachelor")
                elif any(term in content_lower for term in ['language', 'chinese language', 'language program', 'language course']):
                    extracted_info.add("Program Type: Language Program")
                
                # Extract nationality (FIXED: removed 'in' from India detection to avoid false matches)
                # Only match if user explicitly mentions country/nationality
                if any(term in content_lower for term in ['bangladesh', 'bangladeshi', 'bd']):
                    extracted_info.add("Nationality: Bangladeshi")
                elif any(term in content_lower for term in ['pakistan', 'pakistani', 'pak']):
                    extracted_info.add("Nationality: Pakistani")
                elif any(term in content_lower for term in ['india', 'indian']):  # REMOVED 'in' to fix bug
                    extracted_info.add("Nationality: Indian")
                # HSC is Bangladeshi qualification, but only if context suggests nationality
                elif 'hsc' in content_lower and ('bangladesh' in content_lower or 'bangladeshi' in content_lower or 'from bangladesh' in content_lower):
                    extracted_info.add("Nationality: Bangladeshi")
                elif any(term in content_lower for term in ['nepal', 'nepali', 'nepalese']):
                    extracted_info.add("Nationality: Nepali")
                elif any(term in content_lower for term in ['sri lanka', 'sri lankan', 'srilankan']):
                    extracted_info.add("Nationality: Sri Lankan")
                
                # Extract university (handle typos)
                if 'beihang' in content_lower or 'buaa' in content_lower:
                    extracted_info.add("University: Beihang University")
                elif 'shandong' in content_lower or 'shangdong' in content_lower:
                    extracted_info.add("University: Shandong University")
                elif 'tsinghua' in content_lower:
                    extracted_info.add("University: Tsinghua University")
                elif any(term in content_lower for term in ['peking', 'pku', 'beijing university']):
                    extracted_info.add("University: Peking University")
                
                # Extract language program interest
                if any(term in content_lower for term in ['chinese language', 'language program', 'language course', 'study chinese', 'learn chinese']):
                    extracted_info.add("Program: Chinese Language Program")
                
                # Extract year/semester
                if any(term in content_lower for term in ['2nd year', 'second year', 'year 2', '2 year']):
                    extracted_info.add("Want to continue from: 2nd year")
                elif any(term in content_lower for term in ['3rd year', 'third year', 'year 3', '3 year']):
                    extracted_info.add("Want to continue from: 3rd year")
                elif 'continue' in content_lower or 'resume' in content_lower or 'finish' in content_lower:
                    extracted_info.add("Situation: Returning to complete studies")
                
                # Extract intake preference
                if 'march' in content_lower:
                    extracted_info.add("Intake Preference: March")
                elif 'september' in content_lower or 'sep' in content_lower:
                    extracted_info.add("Intake Preference: September")
                elif any(term in content_lower for term in ['2025', '2026', '2027']):
                    year_match = [term for term in ['2025', '2026', '2027'] if term in content_lower]
                    if year_match:
                        extracted_info.add(f"Intake Year: {year_match[0]}")
        
        # Use LLM to extract additional context if conversation is complex
        if len(conversation_history) > 2:
            try:
                llm_extraction_prompt = f"""Extract key information from this conversation. Return ONLY a JSON object with these fields (use null if not mentioned):
{{
    "degree_level": "Bachelor/Master/PhD/Language or null",
    "major": "specific major name or null",
    "nationality": "country name or null",
    "university": "university name or null",
    "city": "city name or null",
    "intake_term": "March/September/Other or null",
    "intake_year": "year number or null"
}}

CONVERSATION:
{full_conversation_text}

Return ONLY valid JSON, no other text."""
                
                response = self.openai_service.chat_completion(
                    messages=[
                        {"role": "system", "content": "You are a helpful assistant that extracts structured information. Always return valid JSON only."},
                        {"role": "user", "content": llm_extraction_prompt}
                    ],
                    temperature=0.1
                )
                
                import json
                import re
                response_text = response.choices[0].message.content if response.choices else ""
                json_match = re.search(r'\{[^}]+\}', response_text, re.DOTALL)
                if json_match:
                    llm_extracted = json.loads(json_match.group())
                    
                    if llm_extracted.get('degree_level'):
                        extracted_info.add(f"Degree Level: {llm_extracted['degree_level']}")
                    if llm_extracted.get('major'):
                        extracted_info.add(f"Major: {llm_extracted['major']}")
                    if llm_extracted.get('nationality'):
                        extracted_info.add(f"Nationality: {llm_extracted['nationality']}")
                    if llm_extracted.get('university'):
                        extracted_info.add(f"University: {llm_extracted['university']}")
                    if llm_extracted.get('city'):
                        extracted_info.add(f"City: {llm_extracted['city']}")
                    if llm_extracted.get('intake_term'):
                        extracted_info.add(f"Intake Preference: {llm_extracted['intake_term']}")
                    if llm_extracted.get('intake_year'):
                        extracted_info.add(f"Intake Year: {llm_extracted['intake_year']}")
            except Exception as e:
                print(f"Error in LLM extraction: {e}")
                # Fall back to keyword matching only
        
        return extracted_info
    
    # NOTE: This method is deprecated and replaced by extract_student_profile_state
    # Keeping it for backward compatibility but it's not used in generate_response
    # TODO: Remove this method in a future cleanup
    
    def _generate_query_parameters(self, user_message: str, conversation_history: List[Dict[str, str]] = None) -> Dict[str, Any]:
        """Use LLM to understand user intent and generate database query parameters"""
        # Build conversation context
        conversation_text = user_message
        if conversation_history:
            for msg in conversation_history[-12:]:  # Last 12 messages
                role = msg.get('role', '')
                content = msg.get('content', '')
                conversation_text += f"\n{role}: {content}"
        
        # Database schema information for LLM
        schema_info = """
DATABASE SCHEMA INFORMATION:

Available Degree Levels in database:
- Non-degree
- Associate Bachelor
- Bachelor
- Master
- Doctoral (PhD)
- Language
- Short Program
- Study Tour Program
- Upgrade from Junior College Student to University Student

Available Teaching Languages:
- Chinese
- English
- Bilingual

Database Tables:
1. universities: id, name, city, province, country, is_partner, description, website, contact_email, contact_wechat
2. majors: id, university_id, name, degree_level, teaching_language, duration_years, description, discipline, is_featured
3. program_intakes: id, university_id, major_id, intake_term (March/September/Other), intake_year, application_deadline, documents_required, tuition_per_semester, tuition_per_year, application_fee, accommodation_fee, service_fee, medical_insurance_fee, teaching_language, duration_years, degree_type, arrival_medical_checkup_fee, admission_process, accommodation_note, visa_extension_fee, notes, scholarship_info

Query Methods Available:
- search_universities(name, city, province, is_partner, limit)
- search_majors(university_id, name, degree_level, teaching_language, discipline, is_featured, limit)
- search_program_intakes(university_id, major_id, intake_term, intake_year, upcoming_only, limit)
"""
        
        query_prompt = f"""You are a database query assistant. Analyze the user's conversation and suggest what database queries to make.

{schema_info}

CONVERSATION:
{conversation_text}

Based on the conversation above, analyze the user's intent and suggest database query parameters. Consider:
- What degree level are they interested in? (extract from context: master, phd, bachelor, language, etc.)
- What major/field of study? (computer science, engineering, business, material science, etc.)
- Any specific university mentioned?
- Any city or province preference?
- Intake preference (March, September, year)?
- Teaching language preference (English, Chinese, Bilingual)?

Respond in JSON format with these fields:
{{
    "university_name": "exact name or null",
    "major_name": "major name or null",
    "degree_level": "exact degree level string matching database values or null",
    "teaching_language": "Chinese/English/Bilingual or null",
    "discipline": "discipline name or null",
    "intake_term": "March/September/Other or null",
    "intake_year": year number or null,
    "city": "city name or null",
    "province": "province name or null",
    "reasoning": "brief explanation of why these parameters were chosen"
}}

IMPORTANT:
- Use EXACT degree level strings from the schema (e.g., "Master", "Doctoral (PhD)", "Language", "Bachelor")
- If user says "masters" or "ms", use "Master"
- If user says "phd" or "doctorate", use "Doctoral (PhD)"
- If user says "bachelor" or "bsc", use "Bachelor"
- If user mentions "chinese language" or "language program", use "Language"
- If user mentions "bilingual", use "Bilingual" for teaching_language
- Extract all relevant information from the ENTIRE conversation history, not just the last message
- Remember what the user said in previous messages (major, degree level, nationality, etc.)
"""
        
        try:
            response = self.openai_service.chat_completion(
                messages=[{"role": "system", "content": "You are a helpful database query assistant. Always respond with valid JSON only."},
                         {"role": "user", "content": query_prompt}],
                temperature=0.3
            )
            
            # Extract content from response
            response_text = response.choices[0].message.content if response.choices else ""
            
            # Parse JSON response
            import json
            import re
            # Extract JSON from response (handle markdown code blocks)
            json_match = re.search(r'\{[^}]+\}', response_text, re.DOTALL)
            if json_match:
                query_params = json.loads(json_match.group())
                return query_params
            else:
                # Fallback: try to parse entire response
                query_params = json.loads(response_text)
                return query_params
        except Exception as e:
            print(f"Error generating query parameters: {e}")
            return {}
    
    def _query_database(self, user_message: str, conversation_history: List[Dict[str, str]] = None) -> str:
        """Query database for relevant university/major/intake information using LLM-generated query parameters"""
        context_parts = []
        
        # Step 1: Use LLM to understand intent and generate query parameters
        query_params = self._generate_query_parameters(user_message, conversation_history)
        
        # Step 2: Execute database queries based on LLM suggestions
        university_name = query_params.get('university_name')
        major_name = query_params.get('major_name')
        degree_level = query_params.get('degree_level')
        teaching_language = query_params.get('teaching_language')
        discipline = query_params.get('discipline')
        intake_term = query_params.get('intake_term')
        intake_year = query_params.get('intake_year')
        city = query_params.get('city')
        province = query_params.get('province')
        
        # Normalize intake_term and teaching_language
        if intake_term:
            normalized_intake = self._normalize_intake_term(intake_term)
            if normalized_intake:
                intake_term = normalized_intake
        if teaching_language:
            normalized_lang = self._normalize_teaching_language(teaching_language)
            if normalized_lang:
                teaching_language = normalized_lang
        
        # If LLM provided reasoning, log it for debugging
        if query_params.get('reasoning'):
            print(f"LLM Query Reasoning: {query_params.get('reasoning')}")
        
        # Step 3: Search for universities
        universities = []
        if university_name:
            # Try fuzzy matching for university name
            matched_name, similar = self._fuzzy_match_university(university_name)
            if matched_name:
                university_name = matched_name
            elif similar:
                context_parts.append(f"UNCERTAINTY DETECTED: University name '{university_name}' could match multiple options:")
                for uni_name in similar:
                    context_parts.append(f"  - {uni_name}")
                context_parts.append("ACTION REQUIRED: Ask user to confirm which university they mean.")
                # Use first similar match for now, but mark as uncertain
                if similar:
                    university_name = similar[0]
            
            # Normalize city and province using fuzzy matching
            if city:
                matched_city = self._fuzzy_match_city(city)
                if matched_city:
                    city = matched_city
            if province:
                matched_province = self._fuzzy_match_province(province)
                if matched_province:
                    province = matched_province
            
            universities = self.db_service.search_universities(
                name=university_name,
                city=city,
                province=province,
                is_partner=True,  # ALWAYS filter by partner - MalishaEdu only works with partners
                limit=5
            )
        elif city or province:
            # Normalize city and province using fuzzy matching
            if city:
                matched_city = self._fuzzy_match_city(city)
                if matched_city:
                    city = matched_city
            if province:
                matched_province = self._fuzzy_match_province(province)
                if matched_province:
                    province = matched_province
            
            universities = self.db_service.search_universities(
                city=city,
                province=province,
                is_partner=True,  # Prefer partner universities
                limit=10
            )
        else:
            # No specific university - search for majors first, then get universities from those majors
            # This ensures we only suggest universities that actually have matching programs
            if degree_level or major_name:
                # First, search for majors matching the criteria
                matching_majors = self.db_service.search_majors(
                    name=major_name,
                    degree_level=degree_level,
                    limit=50  # Get more to find all matching universities
                )
                
                # If no exact match, try fuzzy matching for major name
                if not matching_majors and major_name:
                    matched_major_name, similar_majors = self._fuzzy_match_major(major_name)
                    if matched_major_name:
                        matching_majors = self.db_service.search_majors(
                            name=matched_major_name,
                            degree_level=degree_level,
                            limit=50
                        )
                
                # Get unique partner universities from matching majors
                if matching_majors:
                    partner_uni_ids = set()
                    for major in matching_majors:
                        if major.university and major.university.is_partner:
                            partner_uni_ids.add(major.university_id)
                    
                    if partner_uni_ids:
                        # Get universities
                        universities = self.db.query(University).filter(
                            University.id.in_(list(partner_uni_ids))
                        ).limit(10).all()
                        
                        # Update majors list to only include those from partner universities
                        majors = [m for m in matching_majors if m.university_id in partner_uni_ids]
        
        # Step 4: Search for majors/programs
        majors = []
        university_id = universities[0].id if universities else None
        
        if university_id:
            # Search majors at specific university
            # Normalize teaching_language if provided
            normalized_teaching_lang = None
            if teaching_language:
                normalized_teaching_lang = self._normalize_teaching_language(teaching_language)
            
            majors = self.db_service.search_majors(
                university_id=university_id,
                name=major_name,
                degree_level=degree_level,
                teaching_language=normalized_teaching_lang or teaching_language,
                discipline=discipline,
                limit=10
            )
            
            # If no exact match, try fuzzy matching for major name
            if not majors and major_name:
                matched_major_name, similar_majors = self._fuzzy_match_major(major_name, university_id=university_id)
                if matched_major_name:
                    majors = self.db_service.search_majors(
                        university_id=university_id,
                        name=matched_major_name,
                        degree_level=degree_level,
                        teaching_language=teaching_language,
                        limit=10
                    )
                elif similar_majors:
                    context_parts.append(f"UNCERTAINTY: Major '{major_name}' could match multiple options:")
                    for maj_name in similar_majors:
                        context_parts.append(f"  - {maj_name}")
                    context_parts.append("ACTION REQUIRED: Ask user to confirm which major/program they mean.")
        else:
            # Search majors across all universities
            # Normalize teaching_language if provided
            normalized_teaching_lang = None
            if teaching_language:
                normalized_teaching_lang = self._normalize_teaching_language(teaching_language)
            
            majors = self.db_service.search_majors(
                name=major_name,
                degree_level=degree_level,
                teaching_language=normalized_teaching_lang or teaching_language,
                discipline=discipline,
                limit=20
            )
            
            # If no exact match and we have a major name, try fuzzy matching across all universities
            if not majors and major_name:
                matched_major_name, similar_majors = self._fuzzy_match_major(major_name)
                if matched_major_name:
                    majors = self.db_service.search_majors(
                        name=matched_major_name,
                        degree_level=degree_level,
                        teaching_language=teaching_language,
                        limit=20
                    )
                elif similar_majors:
                    context_parts.append(f"UNCERTAINTY: Major '{major_name}' could match multiple options:")
                    for maj_name in similar_majors[:5]:
                        context_parts.append(f"  - {maj_name}")
                    context_parts.append("ACTION REQUIRED: Ask user to confirm which major/program they mean.")
        
        # Step 5: Format results
        # CRITICAL: Only show universities that actually have matching programs
        if universities:
            # Filter universities to only those with matching majors
            universities_with_programs = []
            for uni in universities[:10]:  # Check more universities to find matches
                # Get majors for this university
                uni_majors = [m for m in majors if m.university_id == uni.id] if majors else []
                
                # If no majors from the search, check if this university has any majors matching the criteria
                if not uni_majors and (degree_level or major_name):
                    uni_majors = self.db_service.search_majors(
                        university_id=uni.id,
                        name=major_name,
                        degree_level=degree_level,
                        limit=5
                    )
                
                # Only include university if it has matching programs
                if uni_majors:
                    universities_with_programs.append((uni, uni_majors))
            
            # Now format only universities with matching programs
            for uni, uni_majors in universities_with_programs[:5]:  # Limit to 5 universities
                context_parts.append(f"=== {uni.name.upper()} ({uni.city or 'China'}) ===")
                context_parts.append(self.db_service.format_university_info(uni))
                
                context_parts.append(f"\nAVAILABLE PROGRAMS AT {uni.name.upper()}:")
                for major in uni_majors[:5]:  # Limit to 5 majors per university
                    context_parts.append(self.db_service.format_major_info(major))
                    
                    # Get program intakes for this major
                    from app.models import IntakeTerm
                    intake_term_enum = None
                    if intake_term:
                        try:
                            intake_term_enum = IntakeTerm[intake_term.upper()]
                        except:
                            pass
                    
                    intakes = self.db_service.search_program_intakes(
                        university_id=uni.id,
                        major_id=major.id,
                        intake_term=intake_term_enum,
                        intake_year=intake_year,
                        upcoming_only=True,
                        limit=3
                    )
                    
                    if intakes:
                        context_parts.append(f"\n=== INTAKE DETAILS FOR {major.name} AT {uni.name.upper()} ===")
                        for intake in intakes:
                            context_parts.append(self.db_service.format_program_intake_info(intake))
        
        elif majors:
            # No specific university, but majors found - group by university
            universities_with_majors = {}
            for major in majors:
                uni_id = major.university_id
                if uni_id not in universities_with_majors:
                    uni = self.db.query(University).filter(University.id == uni_id).first()
                    if uni:
                        universities_with_majors[uni_id] = {
                            'university': uni,
                            'majors': []
                        }
                if uni_id in universities_with_majors:
                    universities_with_majors[uni_id]['majors'].append(major)
            
            # Format results grouped by university
            for uni_id, data in list(universities_with_majors.items())[:5]:
                uni = data['university']
                context_parts.append(f"=== {uni.name.upper()} ({uni.city or 'China'}) ===")
                context_parts.append(self.db_service.format_university_info(uni))
                context_parts.append(f"\nAVAILABLE PROGRAMS:")
                for major in data['majors']:
                    context_parts.append(self.db_service.format_major_info(major))
                    # Get intakes
                    intakes = self.db_service.search_program_intakes(
                        major_id=major.id,
                        upcoming_only=True,
                        limit=2
                    )
                    if intakes:
                        for intake in intakes:
                            context_parts.append(self.db_service.format_program_intake_info(intake))
        
        return "\n\n".join(context_parts) if context_parts else ""
    
    def _query_database_with_state(
        self, 
        user_message: str, 
        student_state: StudentProfileState,
        conversation_history: List[Dict[str, str]] = None,
        lead_collected: bool = False,
        is_simple_info_query: bool = False,
        sales_intent: str = 'general',
        current_date = None
    ) -> Tuple[str, str]:
        """
        Query database using StudentProfileState to build query parameters.
        Returns: (db_context, matched_programs)
        - db_context: General database information
        - matched_programs: Formatted list of matched programs with exact values
        """
        from datetime import datetime, timezone
        if current_date is None:
            current_date = datetime.now(timezone.utc)
        
        context_parts = []
        matched_programs_parts = []
        
        # Build query parameters - for simple info queries, extract from current message, not old state
        if is_simple_info_query:
            # Extract university and degree level directly from current message
            import re
            # Extract university name from message - look for patterns like "Harbin Institute of Technology", "Harbin Institute", etc.
            uni_patterns = [
                r'([A-Z][a-zA-Z\s&]+(?:University|College|Institute|Tech|Technology))',
                r'([A-Z][a-zA-Z\s]+(?:University|College|Institute))',
            ]
            university_name = None
            for pattern in uni_patterns:
                match = re.search(pattern, user_message, re.IGNORECASE)
                if match:
                    university_name = match.group(1).strip()
                    break
            
            # Extract degree level from current message
            user_lower = user_message.lower()
            if 'bachelor' in user_lower or 'bsc' in user_lower or 'undergraduate' in user_lower:
                degree_level = 'Bachelor'
            elif 'master' in user_lower or 'msc' in user_lower or 'graduate' in user_lower:
                degree_level = 'Master'
            elif 'phd' in user_lower or 'doctorate' in user_lower or 'doctoral' in user_lower:
                degree_level = 'PhD'
            else:
                degree_level = None
            
            major_name = None  # Simple queries don't need major
            city = None
            province = None
        else:
            # Use StudentProfileState for personalized queries
            university_name = student_state.preferred_universities[0] if student_state.preferred_universities else None
            major_name = student_state.major
            degree_level = student_state.degree_level
            city = student_state.city
            province = student_state.province
        
        # Normalize city and province using fuzzy matching
        if city:
            matched_city = self._fuzzy_match_city(city)
            if matched_city:
                city = matched_city
        if province:
            matched_province = self._fuzzy_match_province(province)
            if matched_province:
                province = matched_province
        
        # Map degree_level to database format
        degree_level_map = {
            "Bachelor": "Bachelor",
            "Master": "Master",
            "PhD": "Doctoral (PhD)",
            "Doctoral (PhD)": "Doctoral (PhD)",
            "Language": "Language"
        }
        db_degree_level = degree_level_map.get(degree_level, degree_level) if degree_level else None
        
        # Handle intake term - normalize first
        from app.models import IntakeTerm
        intake_term_enum = None
        if student_state.intake_term:
            normalized_intake = self._normalize_intake_term(student_state.intake_term)
            if normalized_intake:
                try:
                    intake_term_enum = IntakeTerm[normalized_intake.upper()]
                except:
                    pass
        
        # Step 1: Search for universities
        universities = []
        if university_name:
            matched_name, similar = self._fuzzy_match_university(university_name)
            if matched_name:
                university_name = matched_name
            elif similar:
                context_parts.append(f"UNCERTAINTY DETECTED: University name '{university_name}' could match multiple options:")
                for uni_name in similar:
                    context_parts.append(f"  - {uni_name}")
                context_parts.append("ACTION REQUIRED: Ask user to confirm which university they mean.")
                if similar:
                    university_name = similar[0]
        
        # Normalize city and province using fuzzy matching (works for ALL cities, not just Beijing)
        if city:
            matched_city = self._fuzzy_match_city(city)
            if matched_city:
                city = matched_city
        if province:
            matched_province = self._fuzzy_match_province(province)
            if matched_province:
                province = matched_province
        
        # Check if university is a MalishaEdu partner
        non_partner_university = None
        if university_name:
            is_partner, partner_uni = self._is_malishaedu_partner_university(university_name)
            if not is_partner and not lead_collected:
                # User interested in non-partner university - STRONGLY divert to partners
                non_partner_university = university_name
                context_parts.append(f"🚨 CRITICAL: '{university_name}' is NOT a MalishaEdu partner university.")
                context_parts.append("MANDATORY ACTION REQUIRED:")
                context_parts.append("1. DO NOT provide detailed information about this non-partner university (costs, deadlines, application process)")
                context_parts.append("2. IMMEDIATELY mention that MalishaEdu is one of the biggest education agent services in China")
                context_parts.append("3. EMPHASIZE that MalishaEdu works exclusively with partner universities where we can provide:")
                context_parts.append("   - 100% admission support and guarantee")
                context_parts.append("   - Scholarship application guidance and support")
                context_parts.append("   - Complete post-arrival services (airport pickup, accommodation, bank account, SIM card, etc.)")
                context_parts.append("4. DIVERT user to MalishaEdu partner universities that offer the SAME major/degree level")
                context_parts.append("5. Search RAG for partner universities with the same major (e.g., 'MalishaEdu partner universities [major] [degree_level]')")
                context_parts.append("6. Suggest 3-5 partner universities from RAG that match their major and degree level")
                context_parts.append("7. DO NOT waste prompts - immediately divert, don't provide info about the non-partner university")
                
                # Search RAG for partner universities with same major
                if major_name and db_degree_level:
                    partner_search_query = f"MalishaEdu partner universities {major_name} {db_degree_level}"
                    try:
                        partner_rag_results = self.rag_service.retrieve(
                            self.db,
                            partner_search_query,
                            doc_type='b2b_partner',
                            audience='partner',
                            top_k=4
                        )
                        if partner_rag_results:
                            partner_rag_context = self.rag_service.format_rag_context(partner_rag_results)
                            context_parts.append(f"\nMALISHAEDU PARTNER UNIVERSITIES FOR {major_name} {db_degree_level}:\n{partner_rag_context}")
                    except Exception as e:
                        print(f"RAG search for partner universities failed: {e}")
                
                # Also search DB for partner universities with same major
                partner_universities = []
                if major_name:
                    # Find majors matching the user's interest
                    matched_majors = self.db_service.search_majors(
                        name=major_name,
                        degree_level=db_degree_level,
                        limit=10
                    )
                    # Get unique partner universities from these majors
                    partner_uni_ids = set()
                    for major in matched_majors:
                        if major.university and major.university.is_partner:
                            partner_uni_ids.add(major.university_id)
                    
                    if partner_uni_ids:
                        partner_universities = self.db.query(University).filter(
                            University.id.in_(list(partner_uni_ids)),
                            University.is_partner == True
                        ).limit(5).all()
                        
                        if partner_universities:
                            partner_list = "\n".join([f"- {uni.name} ({uni.city}, {uni.province})" for uni in partner_universities])
                            context_parts.append(f"\nMALISHAEDU PARTNER UNIVERSITIES FROM DATABASE OFFERING {major_name} {db_degree_level}:\n{partner_list}")
                
                # Don't search for the non-partner university - we're diverting
                universities = []
            else:
                # It's a partner university - query with is_partner=True to ensure we get it
                universities = self.db_service.search_universities(
                    name=university_name,
                    city=city,
                    province=province,
                    is_partner=True,  # ALWAYS filter by partner - MalishaEdu only works with partners
                    limit=5
                )
        elif city or province:
            # ALWAYS show only partner universities - MalishaEdu only works with 31 partners
            universities = self.db_service.search_universities(
                city=city,
                province=province,
                is_partner=True,  # ALWAYS filter by partner
                limit=10
            )
        else:
            # No specific university - ALWAYS show only partner universities
            # When user asks for "top ranked" or "any university", only show partners
            universities = self.db_service.search_universities(
                is_partner=True,  # ALWAYS filter by partner - MalishaEdu only works with 31 partners
                limit=10
            )
        
        # Step 2: Search for majors
        majors = []
        university_id = universities[0].id if universities else None
        
        # FIRST: Check if major is offered by MalishaEdu
        if major_name:
            is_malishaedu_major, matched_major = self._is_malishaedu_major(major_name)
            if not is_malishaedu_major:
                if not lead_collected:
                    # Before lead collection - encourage signup
                    context_parts.append(f"⚠️ NOTE: '{major_name}' is not currently offered by MalishaEdu.")
                    context_parts.append("ACTION REQUIRED: Encourage user to sign up at /signup (or /login) to get personalized recommendations for similar programs.")
                else:
                    # After lead collection - pursue related major
                    context_parts.append(f"⚠️ NOTE: '{major_name}' is not currently offered by MalishaEdu.")
                    context_parts.append("ACTION REQUIRED: Pursue user to choose a RELATED major from MalishaEdu's 150+ offerings. Suggest similar majors.")
                # Still try to find similar majors for suggestions
                matched_major_name, similar_majors = self._fuzzy_match_major(major_name, university_id=university_id)
                if similar_majors:
                    context_parts.append(f"SIMILAR MALISHAEDU MAJORS: {', '.join(similar_majors[:5])}")
            else:
                # It's a MalishaEdu major - proceed with fuzzy matching
                matched_major_name, similar_majors = self._fuzzy_match_major(major_name, university_id=university_id)
                if matched_major_name:
                    print(f"DEBUG: Fuzzy matched '{major_name}' to database major '{matched_major_name}'")
                    major_name = matched_major_name
                elif similar_majors:
                    context_parts.append(f"UNCERTAINTY: Major '{major_name}' could match multiple options:")
                    for maj_name in similar_majors[:5]:
                        context_parts.append(f"  - {maj_name}")
                    context_parts.append("ACTION REQUIRED: Ask user to confirm which major/program they mean.")
                    if similar_majors:
                        major_name = similar_majors[0]
        
        if university_id:
            majors = self.db_service.search_majors(
                university_id=university_id,
                name=major_name,
                degree_level=db_degree_level,
                limit=10
            )
            
            if not majors and major_name:
                matched_major_name, similar_majors = self._fuzzy_match_major(major_name, university_id=university_id)
                if matched_major_name:
                    majors = self.db_service.search_majors(
                        university_id=university_id,
                        name=matched_major_name,
                        degree_level=db_degree_level,
                        limit=10
                    )
        else:
            majors = self.db_service.search_majors(
                name=major_name,
                degree_level=db_degree_level,
                limit=20
            )
        
        # Check if user is asking for comparison (best scholarship, lowest tuition, etc.)
        # and has degree_type, nationality, intake, intake_year, major but no university
        user_msg_lower = user_message.lower()
        asks_for_comparison = any(term in user_msg_lower for term in [
            'best scholarship', 'best scholarship university', 'best scholarship universities',
            'lowest tuition', 'lowest cost', 'cheapest', 'best university', 'best universities',
            'top ranked', 'top university', 'top universities', 'any university', 'any universities',
            'compare', 'comparison', 'comparative', 'which university', 'show me universities',
            'lowest application fee', 'lowest accommodation', 'total cost', 'all universities',
            'all options', 'options for', 'universities offering', 'show me some university',
            'show me some universities', 'university and their cost', 'cost to compare',
            'recommend university', 'recommend universities', 'suggest university', 'suggest universities'
        ])
        
        has_comparison_info = (
            student_state.degree_level and
            student_state.nationality and
            student_state.intake_term and
            student_state.intake_year and
            major_name and
            not university_name  # No specific university selected
        )
        
        # If user asks for comparison and has enough info, generate comparative chart
        if asks_for_comparison and has_comparison_info and not is_simple_info_query:
            # Find all partner universities offering this major and degree level
            all_majors_for_comparison = self.db_service.search_majors(
                name=major_name,
                degree_level=db_degree_level,
                limit=50  # Get all majors matching this
            )
            
            # Get unique partner universities
            partner_uni_ids = set()
            for major in all_majors_for_comparison:
                if major.university and major.university.is_partner:
                    partner_uni_ids.add(major.university_id)
            
            if partner_uni_ids:
                # Get all program intakes for these universities, majors, and intake
                comparison_intakes = self.db.query(ProgramIntake).join(Major).join(University).filter(
                    ProgramIntake.university_id.in_(list(partner_uni_ids)),
                    Major.name.ilike(f"%{major_name}%"),
                    Major.degree_level == db_degree_level,
                    ProgramIntake.intake_term == intake_term_enum,
                    ProgramIntake.intake_year == student_state.intake_year,
                    University.is_partner == True,
                    ProgramIntake.application_deadline > current_date.date()  # Only upcoming
                ).all()
                
                if comparison_intakes:
                    # Fix 3: Use deterministic formatter for list queries (>3 results)
                    # Note: This is handled in generate_response, not here
                    # _query_database_with_state only returns (db_context, matched_programs)
                    if len(comparison_intakes) > 3:
                        print(f"DEBUG: Large comparison query ({len(comparison_intakes)} intakes) - will be formatted in generate_response")
                    
                    # For <=3 results, use LLM with compact context
                    # Format as comparative chart
                    context_parts.append("=== COMPARATIVE CHART: ALL MALISHAEDU PARTNER UNIVERSITIES ===")
                    context_parts.append(f"Major: {major_name}")
                    context_parts.append(f"Degree Level: {db_degree_level}")
                    context_parts.append(f"Intake: {student_state.intake_term} {student_state.intake_year}")
                    context_parts.append(f"Nationality: {student_state.nationality}")
                    context_parts.append("\nCOMPARISON TABLE (Use this to create a comparative chart):\n")
                    
                    for intake in comparison_intakes:
                        uni = intake.university
                        major = intake.major
                        context_parts.append(f"\n--- {uni.name} ({uni.city or 'China'}) ---")
                        context_parts.append(f"Major: {major.name}")
                        context_parts.append(f"Application Fee: {intake.application_fee or 'N/A'} RMB")
                        context_parts.append(f"Tuition (per year): {intake.tuition_per_year or (intake.tuition_per_semester * 2 if intake.tuition_per_semester else None) or 'N/A'} RMB")
                        context_parts.append(f"Accommodation Fee (per year): {intake.accommodation_fee or 'N/A'} RMB")
                        if intake.accommodation_note:
                            context_parts.append(f"Accommodation Note: {intake.accommodation_note}")
                        context_parts.append(f"Medical Insurance Fee: {intake.medical_insurance_fee or 'N/A'} RMB")
                        context_parts.append(f"Arrival Medical Checkup Fee (one-time): {intake.arrival_medical_checkup_fee or 0} RMB")
                        context_parts.append(f"Visa Extension Fee (per year): {intake.visa_extension_fee or 0} RMB")
                        if intake.scholarship_info:
                            context_parts.append(f"Scholarship Info: {intake.scholarship_info}")
                        if intake.notes:
                            context_parts.append(f"Notes: {intake.notes}")
                        # Fix 2: Use deterministic documents/requirements formatting
                        docs_reqs = self._format_documents_and_requirements(intake)
                        context_parts.append(docs_reqs)
                        context_parts.append(f"Application Deadline: {intake.application_deadline.strftime('%Y-%m-%d') if intake.application_deadline else 'Not in database'}")
                    
                    context_parts.append("\n=== END COMPARATIVE CHART ===")
                    context_parts.append("\nINSTRUCTION: Create a clear comparative table/chart showing all universities side-by-side with their fees, scholarship info, and other details. Help user identify the best option based on their criteria (lowest cost, best scholarship, etc.).")
                    context_parts.append("\nIMPORTANT: After showing comparison, encourage user to provide contact information (phone/email/whatsapp/wechat) to complete their lead profile, then encourage signup.")
        
        # Check if user is asking for all subjects/majors at a specific university
        asks_for_all_subjects = any(term in user_msg_lower for term in [
            'all subjects', 'all majors', 'all programs', 'show me subjects', 'show me majors',
            'what subjects', 'what majors', 'list subjects', 'list majors', 'available subjects',
            'available majors', 'subjects offered', 'majors offered'
        ])
        
        if asks_for_all_subjects and university_name and db_degree_level:
            # Get all majors for this university and degree level
            all_majors_at_uni = self.db_service.search_majors(
                university_id=universities[0].id if universities else None,
                degree_level=db_degree_level,
                limit=100  # Get all majors
            )
            
            if all_majors_at_uni:
                context_parts.append(f"\n=== ALL MAJORS/SUBJECTS AT {university_name} ({db_degree_level}) ===")
                for idx, major in enumerate(all_majors_at_uni, 1):
                    context_parts.append(f"{idx}. {major.name}")
                    if major.description:
                        context_parts.append(f"   Description: {major.description}")
                context_parts.append("=== END MAJORS LIST ===")
                context_parts.append("\nINSTRUCTION: Present this list clearly to the user. If university is not a partner, gently motivate them to consider a partner university and encourage providing missing information (major/subjects).")
        
        # Step 3: Search for program intakes and format as MATCHED_PROGRAMS
        program_count = 0
        all_intakes = []  # Initialize for urgency check
        if universities:
            for uni in universities[:5]:
                uni_majors = [m for m in majors if m.university_id == uni.id] if majors else []
                
                if not uni_majors and db_degree_level:
                    # Try to find any majors at this university with the degree level
                    uni_majors = self.db_service.search_majors(
                        university_id=uni.id,
                        degree_level=db_degree_level,
                        limit=5
                    )
                
                for major in uni_majors[:5]:
                    intakes = self.db_service.search_program_intakes(
                        university_id=uni.id,
                        major_id=major.id,
                        intake_term=intake_term_enum,
                        intake_year=student_state.intake_year,
                        upcoming_only=True,
                        limit=3
                    )
                    
                    if intakes:
                        all_intakes.extend(intakes)  # Collect for urgency check
                        program_count += 1
                        for intake in intakes:
                            # Calculate days to deadline
                            days_to_deadline = None
                            if intake.application_deadline:
                                from datetime import datetime, timezone
                                now = datetime.now(timezone.utc)
                                if intake.application_deadline > now:
                                    days_to_deadline = (intake.application_deadline - now).days
                            
                            # Fix 2: Format documents/requirements deterministically
                            docs_reqs = self._format_documents_and_requirements(intake)
                            
                            matched_programs_parts.append(f"""
{program_count}) University: {uni.name} ({uni.city or 'China'}) {'[MALISHAEDU PARTNER]' if uni.is_partner else ''}
   Major: {major.name}
   Degree Level: {db_degree_level or 'N/A'}
   Teaching Language: {intake.teaching_language or major.teaching_language or 'N/A'}
   Intake: {intake.intake_term.value if hasattr(intake.intake_term, 'value') else intake.intake_term} {intake.intake_year}
   Application Deadline: {intake.application_deadline.strftime('%Y-%m-%d') if intake.application_deadline else 'Not in database'}{f' ({days_to_deadline} days left)' if days_to_deadline is not None else ''}
   Tuition (per year): {intake.tuition_per_year or (intake.tuition_per_semester * 2 if intake.tuition_per_semester else None) or 'Not in database'} RMB
   Application Fee: {intake.application_fee or 0} RMB (non-refundable)
   Accommodation Fee (per year): {intake.accommodation_fee or 'Not in database'} RMB
   Service Fee: {intake.service_fee or 'Not in database'} RMB (only for successful application)
   Medical Insurance Fee: {intake.medical_insurance_fee or 'Not in database'} RMB (per year)
   {docs_reqs}
   Scholarship Info: {intake.scholarship_info or 'No scholarship information'}
""")
        elif majors:
            # Group by university
            universities_with_majors = {}
            for major in majors:
                uni_id = major.university_id
                if uni_id not in universities_with_majors:
                    uni = self.db.query(University).filter(University.id == uni_id).first()
                    if uni:
                        universities_with_majors[uni_id] = {'university': uni, 'majors': []}
                if uni_id in universities_with_majors:
                    universities_with_majors[uni_id]['majors'].append(major)
            
            for uni_id, data in list(universities_with_majors.items())[:5]:
                uni = data['university']
                for major in data['majors']:
                    intakes = self.db_service.search_program_intakes(
                        major_id=major.id,
                        intake_term=intake_term_enum,
                        intake_year=student_state.intake_year,
                        upcoming_only=True,
                        limit=2
                    )
                    
                    if intakes:
                        all_intakes.extend(intakes)  # Collect for urgency check
                        program_count += 1
                        for intake in intakes:
                            # Calculate days to deadline
                            days_to_deadline = None
                            if intake.application_deadline:
                                from datetime import datetime, timezone
                                now = datetime.now(timezone.utc)
                                if intake.application_deadline > now:
                                    days_to_deadline = (intake.application_deadline - now).days
                            
                            # Fix 2: Format documents/requirements deterministically
                            docs_reqs = self._format_documents_and_requirements(intake)
                            
                            matched_programs_parts.append(f"""
{program_count}) University: {uni.name} ({uni.city or 'China'}) {'[MALISHAEDU PARTNER]' if uni.is_partner else ''}
   Major: {major.name}
   Degree Level: {db_degree_level or 'N/A'}
   Teaching Language: {intake.teaching_language or major.teaching_language or 'N/A'}
   Intake: {intake.intake_term.value if hasattr(intake.intake_term, 'value') else intake.intake_term} {intake.intake_year}
   Application Deadline: {intake.application_deadline.strftime('%Y-%m-%d') if intake.application_deadline else 'Not in database'}{f' ({days_to_deadline} days left)' if days_to_deadline is not None else ''}
   Tuition (per year): {intake.tuition_per_year or (intake.tuition_per_semester * 2 if intake.tuition_per_semester else None) or 'Not in database'} RMB
   Application Fee: {intake.application_fee or 0} RMB (non-refundable)
   Accommodation Fee (per year): {intake.accommodation_fee or 'Not in database'} RMB
   Service Fee: {intake.service_fee or 'Not in database'} RMB (only for successful application)
   Medical Insurance Fee: {intake.medical_insurance_fee or 'Not in database'} RMB (per year)
   {docs_reqs}
   Scholarship Info: {intake.scholarship_info or 'No scholarship information'}
""")
        
        # Format matched programs
        matched_programs = "MATCHED_PROGRAMS:\n" + "\n".join(matched_programs_parts) if matched_programs_parts else ""
        
        # Check application deadline urgency (within 1 month)
        urgency_info = self._check_application_deadline_urgency(all_intakes)
        if urgency_info and urgency_info['is_urgent']:
            urgency_parts = ["⚠️ URGENT APPLICATION DEADLINES (within 1 month):"]
            for urgent in urgency_info['intakes']:
                intake = urgent['intake']
                days = urgent['days_left']
                uni_name = intake.university.name if intake.university else 'University'
                major_name = intake.major.name if intake.major else 'Program'
                urgency_parts.append(f"- {major_name} at {uni_name}: {days} days left (Deadline: {intake.application_deadline.strftime('%Y-%m-%d')})")
            context_parts.append("\n".join(urgency_parts))
            context_parts.append("ACTION REQUIRED: Warn user about urgency and strongly encourage signup/login to apply immediately.")
        
        # General database context (for additional info)
        if universities:
            for uni in universities[:3]:
                context_parts.append(self.db_service.format_university_info(uni))
        
        db_context = "\n\n".join(context_parts) if context_parts else ""
        
        return db_context, matched_programs
    
    def _check_lead_collected(self, device_fingerprint: Optional[str]) -> bool:
        """Check if a lead has been collected for this device fingerprint"""
        if not device_fingerprint:
            return False
        lead = self.db.query(Lead).filter(
            Lead.device_fingerprint == device_fingerprint
        ).first()
        return lead is not None
    
    def _is_malishaedu_partner_university(self, university_name: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Check if a university is a MalishaEdu partner using pre-loaded database array"""
        # Use fuzzy matching on pre-loaded universities array
        matched_name, similar = self._fuzzy_match_university(university_name)
        if matched_name:
            # Find the university dict from all_universities
            for uni in self.all_universities:
                if uni["name"] == matched_name:
                    return True, uni
        
        # If fuzzy match didn't find it, try direct string matching
        uni_name_lower = university_name.lower().strip()
        for uni in self.all_universities:
            uni_name_lower_db = uni['name'].lower()
            # Exact match
            if uni_name_lower == uni_name_lower_db:
                return True, uni
            # Partial match (contains)
            if uni_name_lower in uni_name_lower_db or uni_name_lower_db in uni_name_lower:
                return True, uni
            # Check main words match
            main_words = [w for w in uni_name_lower.split() if w not in ['university', 'college', 'institute', 'tech', 'technology', 'of', 'the', 'and', '&']]
            uni_main_words = [w for w in uni_name_lower_db.split() if w not in ['university', 'college', 'institute', 'tech', 'technology', 'of', 'the', 'and', '&', '(', ')']]
            if main_words and set(main_words).intersection(set(uni_main_words)):
                similarity = SequenceMatcher(None, uni_name_lower, uni_name_lower_db).ratio()
                if similarity >= 0.6:
                    return True, uni
        
        # Not found in partner universities
        try:
            rag_results = self.rag_service.retrieve(
                self.db, 
                f"MalishaEdu partner university {university_name}",
                doc_type='b2c_study',
                audience=None,
                top_k=4
            )
            if rag_results:
                # Check if any RAG result mentions this university as a partner
                for result in rag_results:
                    content_lower = result.get('content', '').lower()
                    uni_name_lower = university_name.lower()
                    # Check if university name appears in RAG content and it's mentioned as a partner
                    if uni_name_lower in content_lower and ('partner' in content_lower or 'malishaedu' in content_lower):
                        return True, None  # Found in RAG but not in DB
                    # Also check for partial matches (e.g., "beihang" in "Beihang University")
                    main_word = uni_name_lower.split()[0] if uni_name_lower.split() else uni_name_lower
                    if main_word in content_lower and len(main_word) >= 5 and ('partner' in content_lower or 'malishaedu' in content_lower):
                        return True, None
        except Exception as e:
            print(f"RAG search for partner university check failed: {e}")
            pass
        
        return False, None
    
    def _is_malishaedu_major(self, major_name: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Check if a major is offered by MalishaEdu using pre-loaded database array"""
        # Use fuzzy matching on pre-loaded majors array
        matched_name, similar = self._fuzzy_match_major(major_name, threshold=0.5)
        if matched_name:
            # Find the major dict from all_majors
            for major in self.all_majors:
                if major["name"] == matched_name:
                    return True, major
        
        # If fuzzy match didn't find it, try direct string matching
        major_name_lower = major_name.lower().strip()
        for major in self.all_majors:
            major_name_lower_db = major['name'].lower()
            # Exact match
            if major_name_lower == major_name_lower_db:
                return True, major
            # Partial match (contains)
            if major_name_lower in major_name_lower_db or major_name_lower_db in major_name_lower:
                return True, major
        
        # Not found in MalishaEdu majors
        return False, None
    
    def _get_partner_universities_from_rag(self) -> List[str]:
        """Get list of MalishaEdu partner universities from RAG"""
        try:
            rag_results = self.rag_service.retrieve(
                self.db,
                "MalishaEdu partner universities list 31",
                doc_type='b2c_study',
                audience=None,
                top_k=4
            )
            universities = []
            if rag_results:
                for result in rag_results:
                    content = result.get('content', '')
                    # Extract university names from RAG content
                    # This is a simple extraction - can be improved
                    import re
                    # Look for patterns like "University Name (City, Province)"
                    uni_pattern = r'([A-Z][a-zA-Z\s&]+(?:University|College|Institute))'
                    found = re.findall(uni_pattern, content)
                    universities.extend(found)
            return list(set(universities))  # Remove duplicates
        except:
            return []
    
    def _get_malishaedu_majors_from_rag(self) -> List[str]:
        """Get list of MalishaEdu majors from RAG"""
        try:
            rag_results = self.rag_service.retrieve(
                self.db,
                "MalishaEdu majors subjects 150",
                doc_type='b2c_study',
                audience=None,
                top_k=4
            )
            majors = []
            if rag_results:
                for result in rag_results:
                    content = result.get('content', '')
                    # Extract major names from RAG content
                    # This is a simple extraction - can be improved
                    import re
                    # Look for major patterns
                    major_pattern = r'([A-Z][a-zA-Z\s&]+(?:Engineering|Science|Studies|Management|Medicine|Language))'
                    found = re.findall(major_pattern, content)
                    majors.extend(found)
            return list(set(majors))  # Remove duplicates
        except:
            return []
    
    def _check_application_deadline_urgency(self, program_intakes: List[ProgramIntake]) -> Optional[Dict[str, Any]]:
        """Check if any program intake has deadline within 1 month"""
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        one_month_later = now + timedelta(days=30)
        
        urgent_intakes = []
        for intake in program_intakes:
            if intake.application_deadline:
                if now <= intake.application_deadline <= one_month_later:
                    days_left = (intake.application_deadline - now).days
                    urgent_intakes.append({
                        'intake': intake,
                        'days_left': days_left
                    })
        
        if urgent_intakes:
            return {
                'is_urgent': True,
                'intakes': urgent_intakes
            }
        return None
    
    # NOTE: Lead collection is automatic (no popup). Leads are created when nationality + contact + study interest are present.                                 
    # Use _check_lead_collected() to check if lead exists for device fingerprint or chat_session_id


# ============================================================================
# REGRESSION TEST EXAMPLES
# ============================================================================
"""
Regression test examples for DB-lite program suggestions and time-aware intake inference:

1) Bangladesh + next March + language program costing
   Input: "I'm from Bangladesh and plan to start next March. What's the cost for language program?"
   Expected:
   - infer_intake_year("March", None, Dec 2025) -> 2026
   - Query DB for Language programs, March 2026, partner universities
   - Show tuition range (e.g., "6,000–12,000 CNY/semester")
   - List 2-3 options: University – Program – teaching language – tuition – deadline
   - Ask ONE follow-up: "Do you prefer 1 semester or 1 year?" OR "Any preferred city?"
   - CTA: "If you sign up (/signup), I can save these and give exact total cost + document checklist."
   - Do NOT ask intake year (already inferred)

2) Bachelor International Trade + March + Bangladesh
   Input: "I want to study Bachelor in International Trade, March intake. I am from Bangladesh."
   Expected:
   - infer_intake_year("March", None, Dec 2025) -> 2026
   - Query DB for Bachelor + (International Economics and Trade / International Trade) + March 2026
   - If matches exist: show 2-3 options + deadline
   - If none exist: "We don't have March 2026 Bachelor intakes for that major in partner DB, but September 2026 options are available. Would you like to see September options?"
   - Do NOT ask intake year (already inferred)

3) Show behavior when no DB matches for March but September exists
   Input: "Bachelor in International Trade, March intake"
   Expected:
   - Query DB for March intake -> 0 matches
   - Automatically check September of same year
   - If September exists: "We don't have March 2026 Bachelor intakes for that major in partner DB, but September 2026 options are available. Would you like to see September options?"
   - If September also doesn't exist: "We don't have March or September 2026 Bachelor intakes for that major in partner DB. Would you like to see other degree levels or majors?"
"""

