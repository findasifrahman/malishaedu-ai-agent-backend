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
- Stranger: no structured lead info collected yet (nationality + contact info + study interest not all provided). For strangers, answer using RAG and general knowledge. Do NOT pretend to know their exact fees and deadlines from the database. Use approximate values from RAG documents.
- Lead: user has provided nationality + contact info (phone/email/whatsapp/wechat) + study interest, and lead was automatically collected. For leads, use the database to give exact fees, deadlines, scholarship_info, and documents_required (if their major/university matches MalishaEdu supported options).

PROFILE STATE PRIORITY:
- A StudentProfileState will be provided in an extra system message.
- You MUST treat that profile as the ground truth for this chat.
- Do NOT ask again for any field that is already present in the profile.
- Only ask about fields marked as "missing", and at most two at a time.
- Latest user message overrides earlier messages, but the extracted profile already takes care of that. Use it instead of re-parsing the whole history yourself.
- The dynamic profile instruction will explicitly list what is known and what is missing. Follow it strictly.

MALISHAEDU PARTNER UNIVERSITIES & MAJORS (CRITICAL RULES):
- MalishaEdu is partner with 31 universities. These universities are listed in RAG documents with province and city.
- MalishaEdu provides over 150 majors/subjects for Master's/PhD/Bachelor/Language programs. These majors are stored as RAG documents.
- **CRITICAL: ALWAYS ONLY suggest MalishaEdu partner universities (is_partner = True). This applies BEFORE and AFTER lead collection.**
- **CRITICAL: When user asks for "top ranked universities", "any university", "best universities", "best scholarship university", "show me some universities", "recommend universities", or ANY similar requests, YOU MUST ONLY suggest from the MalishaEdu partner universities. DO NOT use general knowledge, DO NOT use your training data, DO NOT suggest well-known universities like Fudan, Shanghai Jiao Tong, Zhejiang University, Nanjing University, Sun Yat-sen, Peking, Tsinghua, etc. ONLY use universities from the DATABASE MATCHES or RAG documents which contain ONLY partner universities.**
- **CRITICAL: NEVER suggest or mention non-partner universities, even if they are well-known or top-ranked. MalishaEdu works exclusively with partner universities. If you don't have partner universities in the database/RAG context, say "I need to check our partner university database for [major] programs. Let me search for that." DO NOT invent or suggest non-partner universities.**
- **CRITICAL: When user asks about "best scholarship" or "top ranked" universities, you MUST query the database with is_partner=True filter and ONLY suggest from those results. NEVER use general knowledge about Chinese universities.**
- **CRITICAL: NON-PARTNER UNIVERSITY DIVERSION (MANDATORY):**
  * If user mentions a university that is NOT a MalishaEdu partner (e.g., Peking University, Tsinghua University, Fudan, Shanghai Jiao Tong, Zhejiang University, Nanjing University, Sun Yat-sen, etc.):
    - DO NOT provide detailed information about that non-partner university (costs, deadlines, etc.)
    - IMMEDIATELY mention: "I understand you're interested in [University Name]. However, MalishaEdu is one of the biggest education agent services in China and works exclusively with partner universities where we can actually provide 100% admission support, scholarship guidance, and full post-arrival services."
    - STRONGLY EMPHASIZE: "While we can't directly assist with [Non-Partner University], MalishaEdu can guarantee admission support, scholarship applications, and complete post-arrival services at our partner universities."
    - DIVERT user toward MalishaEdu partner universities that offer the SAME major/degree level
    - Search RAG documents for partner universities offering the same major (e.g., "MalishaEdu partner universities Electrical Electronics PhD")
    - Suggest 3-5 partner universities from RAG that match their major and degree level
    - Example response: "I understand you're interested in [Non-Partner University] for [Major] PhD. MalishaEdu is one of the biggest education agents in China and works with partner universities where we can actually provide 100% admission support and scholarship guidance. For [Major] PhD programs, I can suggest these excellent MalishaEdu partner universities: [list from RAG with same major]. At these partner universities, MalishaEdu can guarantee full admission support, help with scholarship applications, and provide complete post-arrival services including airport pickup, accommodation, bank account setup, and more."
  * DO NOT waste prompts providing information about non-partner universities - immediately divert to partners
  * **CRITICAL: When user asks for "top ranked universities", "any university", "best universities", "show me some universities", DO NOT use general knowledge to suggest non-partner universities. ONLY suggest from the MalishaEdu partner universities listed in the database or RAG.**
- If user shows interest in a province/city OUTSIDE of MalishaEdu service area:
  * MENTION that MalishaEdu provides services in specific provinces/cities
  * DIVERT user toward MalishaEdu partner provinces/cities
  * Suggest cities/provinces from RAG documents where MalishaEdu has partner universities
- If user shows interest in a major OUTSIDE of MalishaEdu offerings (before lead collected):
  * FIRST: PURSUE user to choose a RELATED major from MalishaEdu offerings
  * Search RAG documents for related majors (e.g., "MalishaEdu majors related to [user's major]")
  * Suggest 3-5 similar majors from MalishaEdu's 150+ majors but degree_level should be the same as the user's choice
  * Example: "I see you're interested in [major]. While we don't offer that exact program, we have related programs like [similar majors from MalishaEdu]. Would any of these interest you?"
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

GOOD response:
"Thanks for the details! Since you're a Bangladeshi CSE student wanting to continue from 2nd year at Beihang University, I can help you with the re-admission process. Let me check the specific requirements and transfer options for your case."

BAD response (asking for info already provided):
"Could you tell me your major? What's your nationality? Which university are you interested in?"

EXAMPLE: User states major in first message (works for ANY major - Biology, Physics, Chemistry, etc.)
User (Message 1): "I want to study Mechanical Engineering in china. I already completed my D.sc from bangladesh. What the early intake?"
OR: "I want to study Biology in china..."
OR: "I want to study Physics in china..."
Agent: [Responds about that specific major's Master's programs]

User (Message 2): "I want to study masters"

GOOD response (for ANY major):
"Perfect! For Master's in [the major they mentioned - Mechanical Engineering/Biology/Physics/etc.], let me check the earliest available intakes for you. [Provide specific intake dates and deadlines from database]"

BAD response (re-asking major):
"Thanks for letting me know! You're interested in studying a Master's program in China. To help you find the best options, could you please tell me: Your preferred major field of study for your Master's?"

This applies to ALL majors: Biology, Physics, Chemistry, Computer Science, Mechanical Engineering, Business, Medicine, Material Science, etc.

MalishaEdu Services (mention when relevant, not all at once):
- 100% admission support for Bachelor, Master, PhD & Diploma programs
- Scholarship guidance (partial scholarships are more likely and still valuable)
- Document preparation assistance
- Airport pickup, accommodation, bank account, SIM card after arrival
- Dedicated country-specific counsellors

COST INFORMATION HANDLING (CRITICAL):
- RAG DOCUMENTS CONTAIN STRUCTURED COST PROFILES:
  * Ranking-based cost profiles: "reputed" (top tier, world ranking up to ~400), "average" (mid tier, ranking 401-1000), "below_average" (lower tier, ranking above 1000 or not ranked)
  * Each ranking bucket has cost ranges for: chinese_language_or_other_non_degree, bachelor, masters, phd, accommodation, insurance_fee, medical_fee
  * University-specific generic fees: Each university has structured data with generic_fees for non_degree_tuition_yearly, bachelor_tuition_yearly, masters_tuition_yearly, phd_tuition_yearly, accommodation_fees, insurance_fee, visa_extension_fee, medical_in_china_fee
  * **MALISHAEDU SERVICE CHARGES** (from RAG documents - see detailed table below):
    - Service charges vary by degree level, teaching language, and scholarship type
    - Application deposit: 80 USD (required when sending documents, refundable if no admission due to MalishaEdu)
    - Full service charges are paid after receiving admission notice and JW202 copy
    - For detailed service charge table, search RAG for "MalishaEdu service charges" or "MalishaEdu service fee"

- WHEN USER ASKS ABOUT COST/TUITION:
  * ALWAYS search RAG documents first for structured cost profiles AND MalishaEdu service charges
  * Identify ranking bucket from user query: "top/reputed/best" → reputed, "normal/average/regular" → average, "below average/cheap/affordable" → below_average
  * Identify program type: bachelor, masters, phd, or chinese_language_or_other_non_degree
  * Identify scholarship type: full scholarship (tuition+accommodation free, with/without stipend), partial scholarship, or self-paid
  * Provide COMPREHENSIVE cost breakdown including:
    1. Application fee/Registration fee (non-refundable) - from database if available, otherwise mention typical range
    2. **MalishaEdu Application Deposit: 80 USD** (required when sending documents, refundable if no admission due to MalishaEdu) - ALWAYS mention this when discussing costs
    3. Tuition (per year) - from RAG cost profiles based on ranking bucket and program type
    4. Accommodation fee (per year) - from RAG cost profiles
    5. Insurance fee (per year) - from RAG cost profiles (typically 800 CNY)
    6. One-time China arrival medical fee - from RAG cost profiles (typically 400-600 CNY)
    7. Living cost range (per month) - mention typical range: 1,500-3,000 RMB/month depending on city
    8. Visa renewal/extension fee - from RAG or mention typical: 400-800 RMB/year
    9. **MalishaEdu Service Charge** - from RAG documents based on degree level, teaching language, and scholarship type (see service charge table in RAG)
  * Calculate and show TOTAL ESTIMATED COST for first year (including MalishaEdu service charge)
  * IMPORTANT: Always mention that MalishaEdu service charges are for successful applications and include comprehensive admission support, scholarship guidance, and post-arrival services

- BEFORE LEAD COLLECTION:
  * Use RAG cost profiles (ranking-based or university-specific)
  * After providing generic cost breakdown, STRONGLY ENCOURAGE: "For precise cost information tailored to your specific profile and chosen programs, please sign up at /signup (or log in at /login). Once you sign up, I can give you exact fees from our database for programs that match your interests."
  * Mention: "Creating a free MalishaEdu account will allow me to track your application and provide personalized cost estimates based on your selected programs."

- AFTER LEAD COLLECTION:
  * Use RAG cost profiles for general estimates
  * ALSO check database for specific program costs (from program_intakes table)
  * If database has specific fees, use EXACT values: tuition_per_year, tuition_per_semester, application_fee, accommodation_fee, service_fee, medical_insurance_fee, arrival_medical_checkup_fee, visa_extension_fee
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

Information Sources (in priority order):
1. DATABASE (ALWAYS FIRST)
   - Universities: use DBQueryService.search_universities() and format_university_info().
   - Majors/programs: use DBQueryService.search_majors() and format_major_info().
   - Intakes & requirements: use DBQueryService.search_program_intakes() and format_program_intake_info().
   Always prefer partner universities (is_partner = True) when suggesting options.

2. RAG (knowledge base)
   - Use RAGService.search_similar() to fetch information about:
     - MalishaEdu services
     - China education system
     - Program and scholarship requirements
     - Application & visa process
     - Accommodation, living expenses, medical insurance, CSCA, HSK

3. Tavily (web search)
   - Use TavilyService only if DATABASE + RAG are weak.
   - Mainly for the latest CSCA policies, scholarship or visa updates, or if the user asks about very specific current news.

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
"""


    def __init__(self, db: Session):
        self.db = db
        self.db_service = DBQueryService(db)
        self.rag_service = RAGService()
        self.tavily_service = TavilyService()
        self.openai_service = OpenAIService()
    
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
            
            # If user mentioned "master" but degree_level is None, fix it
            master_keywords = ['master degree', 'master', 'masters', 'master\'s', 'looking for master', 'want master', 'i am looking for master', 'for my masters', 'get admitted', 'admission']
            if any(keyword in conversation_lower for keyword in master_keywords) and state.degree_level is None:
                print(f"WARNING: User mentioned 'master' but degree_level was not extracted. Fixing: setting degree_level='Master'")
                state.degree_level = "Master"
                state.program_type = "Degree"  # Master's is a degree program
            
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
        """
        user_input_lower = user_input.lower().strip()
        
        # Get all universities from database
        all_universities = self.db_service.search_universities(limit=100)
        
        matches = []
        for uni in all_universities:
            uni_name_lower = uni.name.lower()
            
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
                matches.append((uni.name, similarity, uni))
        
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
    
    def _fuzzy_match_major(self, user_input: str, university_id: Optional[int] = None, threshold: float = 0.4) -> Tuple[Optional[str], List[str]]:
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
        
        # Get majors (optionally filtered by university) - increase limit to cover all majors
        if university_id:
            all_majors = self.db_service.search_majors(university_id=university_id, limit=200)
        else:
            all_majors = self.db_service.search_majors(limit=200)
        
        # Calculate similarity scores with multiple strategies
        matches = []
        for major in all_majors:
            major_name_clean = re.sub(r'[^\w\s&]', '', major.name.lower())
            major_name_words = set(major_name_clean.split())
            
            # Strategy 1: Exact match (highest priority)
            if user_input_clean == major_name_clean:
                matches.append((major.name, 1.0))
                continue
            
            # Strategy 2: Substring match (high priority)
            if user_input_clean in major_name_clean or major_name_clean in user_input_clean:
                matches.append((major.name, 0.95))
                continue
            
            # Strategy 3: Word overlap (for handling variations like "Computer Science & Technology" vs "Computer Science and Technology")
            # Also handles semantic similarity like "Industrial automation" → "Automation", "Control Engineering"
            common_words = user_input_words.intersection(major_name_words)
            if common_words:
                word_overlap_ratio = len(common_words) / max(len(user_input_words), len(major_name_words))
                if word_overlap_ratio >= 0.3:  # Lowered threshold to 30% for better matching (e.g., "Industrial automation" matches "Automation")
                    matches.append((major.name, 0.6 + word_overlap_ratio * 0.3))
                    continue
            
            # Strategy 3.5: Semantic keyword matching (e.g., "Industrial automation" matches "Automation", "Control", "Engineering")
            # Check if key words from user input appear in major name
            automation_keywords = ['automation', 'automatic', 'control', 'industrial', 'engineering', 'mechanical', 'electrical']
            if any(kw in user_input_clean for kw in automation_keywords):
                if any(kw in major_name_clean for kw in automation_keywords):
                    # Boost similarity if both have automation-related keywords
                    matches.append((major.name, 0.65))
                    continue
            
            # Strategy 4: Abbreviation matching (e.g., "AI" matches "Artificial Intelligence", "MBA" matches "Business Administration (MBA)")
            # Check if user input is an abbreviation that might match
            user_input_no_spaces = user_input_clean.replace(' ', '')
            if len(user_input_no_spaces) <= 5 and user_input_no_spaces.isupper():
                # User input looks like an abbreviation
                major_abbrev = ''.join([word[0].upper() for word in major_name_clean.split() if word and word[0].isalpha()])
                if user_input_no_spaces.upper() == major_abbrev:
                    matches.append((major.name, 0.85))
                    continue
            
            # Strategy 5: Fuzzy string similarity (for typos)
            similarity = SequenceMatcher(None, user_input_clean, major_name_clean).ratio()
            if similarity >= threshold:
                matches.append((major.name, similarity))
        
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
        
        # Step 0.25: Compute known and missing fields from StudentProfileState
        profile_info = self._compute_known_and_missing_fields(student_state)
        known_fields = profile_info["known_fields"]
        missing_fields = profile_info["missing_fields"]
        
        # Step 0.5: Determine if we can use DB
        # use_db parameter takes precedence, but also check legacy device_fingerprint if use_db not explicitly set
        if not use_db:
            # Check if lead exists for chat_session_id (new way) or device_fingerprint (legacy)
            if chat_session_id:
                lead = self.db.query(Lead).filter(Lead.chat_session_id == chat_session_id).first()
                use_db = lead is not None
            elif device_fingerprint:
                lead = self.db.query(Lead).filter(Lead.device_fingerprint == device_fingerprint).first()
                use_db = lead is not None
        
        lead_collected = use_db  # Alias for backward compatibility
        
        # Step 0.6: Detect if this is a simple informational query (listing majors, basic info) vs personalized recommendation
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
        
        # Step 1: Query Database
        # Allow database queries if:
        # 1. use_db=True (lead collected), OR
        # 2. User has all required info (degree_level, major, nationality, intake_term, intake_year) for comparison
        has_all_info_for_comparison = (
            student_state.degree_level and
            student_state.major and
            student_state.nationality and
            student_state.intake_term and
            student_state.intake_year
        )
        
        # Check if user is asking for specific info that requires database
        asks_for_specific_info = any(term in user_message.lower() for term in [
            'cost', 'fee', 'tuition', 'price', 'scholarship', 'deadline', 'application process',
            'documents', 'university', 'compare', 'comparison', 'show me', 'tell me about',
            'which university', 'what university', 'best university', 'lowest cost', 'good university'
        ])
        
        if not use_db and not (has_all_info_for_comparison and asks_for_specific_info):
            # Stranger (no lead) and doesn't have all info: Don't query database, use RAG/Web only
            # For general questions (scholarships, CSCA, costs), use RAG/Tavily
            # Only avoid program_intakes queries for exact user-specific data
            db_context = "ANONYMOUS USER (NO LEAD): User has not provided structured lead information. DO NOT use database program/intake information for personalized recommendations. Answer only using RAG knowledge base and, if needed, web search context. Do NOT invent exact tuition or deadlines: use approximate values from RAG documents."
            matched_programs = ""
        else:
            # use_db=True OR has all info for comparison: Query database for exact fees, deadlines, documents
            db_context, matched_programs = self._query_database_with_state(user_message, student_state, conversation_slice, lead_collected, is_simple_info_query)
        
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
                    rag_results = self.rag_service.search_similar(self.db, csca_search_query, top_k=5)
                else:
                    rag_results = self.rag_service.search_similar(self.db, user_message, top_k=5)
                
                if rag_results:
                    rag_context = self.rag_service.format_rag_context(rag_results)
                    # Add instruction for RAG-based answers
                    rag_context += "\n\nIMPORTANT: Use ONLY the RAG content above as your factual source. DO NOT fabricate university-specific CSCA rules or scholarship amounts. If RAG doesn't contain specific details, say so clearly. Make distinction between general policy (from RAG) and specific university rules."
            except Exception as e:
                print(f"RAG search for CSCA/scholarship failed: {e}")
                rag_context = None
        
        # Step 2.2: Check if user's major is not offered - search for related majors
        related_majors_context = None
        if student_state.major and not use_db:
            # For anonymous users, check if major is in RAG
            try:
                major_check_query = f"MalishaEdu majors {student_state.major} {student_state.degree_level or ''}"
                major_check_results = self.rag_service.search_similar(
                    self.db,
                    major_check_query,
                    top_k=3
                )
                # If no results or weak results, search for related majors
                if not major_check_results or len(major_check_results) < 2:
                    related_majors_query = f"MalishaEdu majors related to {student_state.major} {student_state.degree_level or ''} similar alternative"
                    related_majors_results = self.rag_service.search_similar(
                        self.db,
                        related_majors_query,
                        top_k=5
                    )
                    if related_majors_results:
                        related_majors_context = self.rag_service.format_rag_context(related_majors_results)
            except Exception as e:
                print(f"RAG search for related majors failed: {e}")
        
        # Step 2.3: If not CSCA/scholarship question, use standard RAG search
        # For strangers (use_db=False): Always prioritize RAG first
        # For lead collected (use_db=True): Use RAG if DB context is weak
        if not rag_context:
            if not use_db:
                # Stranger: Always try RAG first
                try:
                    rag_results = self.rag_service.search_similar(self.db, user_message, top_k=5)
                    if rag_results:
                        rag_context = self.rag_service.format_rag_context(rag_results)
                        # Add related majors context if available
                        if related_majors_context:
                            rag_context = f"{rag_context}\n\nRELATED MAJORS FROM MALISHAEDU (if user's major is not offered):\n{related_majors_context}"
                except Exception as e:
                    print(f"RAG search failed: {e}")
                    rag_context = None
            elif not db_context or len(db_context) < 100:  # DB context is weak
                # Lead collected but DB context weak: try RAG
                try:
                    rag_results = self.rag_service.search_similar(self.db, user_message, top_k=3)
                    if rag_results:
                        rag_context = self.rag_service.format_rag_context(rag_results)
                except Exception as e:
                    print(f"RAG search failed: {e}")
                    rag_context = None
        
        # Step 2.4: If non-partner university mentioned, search RAG for partner alternatives
        if non_partner_university_mentioned and student_state.major and student_state.degree_level:
            try:
                partner_alt_query = f"MalishaEdu partner universities {student_state.major} {student_state.degree_level}"
                partner_rag_results = self.rag_service.search_similar(self.db, partner_alt_query, top_k=5)
                if partner_rag_results:
                    partner_alt_context = self.rag_service.format_rag_context(partner_rag_results)
                    if rag_context:
                        rag_context = f"{rag_context}\n\nMALISHAEDU PARTNER UNIVERSITIES OFFERING {student_state.major} {student_state.degree_level}:\n{partner_alt_context}"
                    else:
                        rag_context = f"MALISHAEDU PARTNER UNIVERSITIES OFFERING {student_state.major} {student_state.degree_level}:\n{partner_alt_context}"
            except Exception as e:
                print(f"RAG search for partner alternatives failed: {e}")
        
        # Step 2.5: If user asks about cost, search RAG for structured cost profiles
        user_msg_lower = user_message.lower()
        asks_about_cost = any(term in user_msg_lower for term in [
            'cost', 'fee', 'tuition', 'price', 'how much', 'expense', 'costing',
            'typical cost', 'average cost', 'normal cost', 'top university cost',
            'reputed university cost', 'below average cost', 'bachelor cost',
            'masters cost', 'phd cost', 'engineering cost', 'medical cost',
            'accommodation cost', 'living cost', 'total cost', 'all cost'
        ])
        
        if asks_about_cost:
            try:
                # Search for ranking-based cost profiles (reputed/average/below_average)
                ranking_cost_terms = []
                if any(term in user_msg_lower for term in ['top', 'reputed', 'reputation', 'best', 'high ranking', 'world ranking']):
                    ranking_cost_terms.append('reputed')
                if any(term in user_msg_lower for term in ['normal', 'average', 'mid', 'regular', 'standard']):
                    ranking_cost_terms.append('average')
                if any(term in user_msg_lower for term in ['below average', 'lower', 'cheap', 'affordable']):
                    ranking_cost_terms.append('below_average')
                
                # Search for program type
                program_type = None
                if any(term in user_msg_lower for term in ['bachelor', 'bsc', 'undergraduate', 'bachelor\'s']):
                    program_type = 'bachelor'
                elif any(term in user_msg_lower for term in ['master', 'masters', 'msc', 'graduate', 'master\'s']):
                    program_type = 'masters'
                elif any(term in user_msg_lower for term in ['phd', 'doctorate', 'doctoral', 'ph.d']):
                    program_type = 'phd'
                elif any(term in user_msg_lower for term in ['language', 'foundation', 'non-degree', 'chinese language']):
                    program_type = 'chinese_language_or_other_non_degree'
                
                # Build search query
                cost_search_query = "MalishaEdu typical cost profiles ranking buckets reputed average below_average"
                if ranking_cost_terms:
                    cost_search_query += f" {' '.join(ranking_cost_terms)}"
                if program_type:
                    cost_search_query += f" {program_type}"
                cost_search_query += " tuition accommodation insurance medical fee living cost"
                
                cost_rag_results = self.rag_service.search_similar(
                    self.db,
                    cost_search_query,
                    top_k=5
                )
                
                # Also search for university-specific generic fees
                university_specific_query = None
                if student_state.preferred_universities:
                    university_specific_query = f"MalishaEdu {student_state.preferred_universities[0]} generic fees tuition accommodation"
                elif any(uni_term in user_msg_lower for uni_term in ['university', 'college', 'institute']):
                    # Extract potential university name from message
                    university_specific_query = f"MalishaEdu university generic fees tuition accommodation"
                
                if university_specific_query:
                    uni_cost_results = self.rag_service.search_similar(
                        self.db,
                        university_specific_query,
                        top_k=3
                    )
                    if uni_cost_results:
                        cost_rag_results = (cost_rag_results or []) + uni_cost_results
                
                if cost_rag_results:
                    cost_rag_context = self.rag_service.format_rag_context(cost_rag_results)
                    if rag_context:
                        rag_context = f"{rag_context}\n\nCOST INFORMATION FROM RAG (Structured Cost Profiles):\n{cost_rag_context}"
                    else:
                        rag_context = f"COST INFORMATION FROM RAG (Structured Cost Profiles):\n{cost_rag_context}"
                    
                    # Add instruction for comprehensive cost breakdown
                    if not lead_collected:
                        rag_context += "\n\nIMPORTANT: Provide comprehensive cost breakdown including: application fee (non-refundable), MalishaEdu application deposit (80 USD, refundable if no admission due to MalishaEdu), tuition, accommodation fee, insurance fee, one-time China arrival medical fee, living cost range, visa renewal fee, and MalishaEdu service charge (based on degree level, teaching language, and scholarship type - search RAG for 'MalishaEdu service charges' table). Calculate total first-year cost. After providing generic cost info, ENCOURAGE user to sign up at /signup (or /login) for more precise information based on their specific profile."
                    else:
                        rag_context += "\n\nIMPORTANT: Provide comprehensive cost breakdown including: application fee (non-refundable), MalishaEdu application deposit (80 USD, refundable if no admission due to MalishaEdu), tuition, accommodation fee, insurance fee, one-time China arrival medical fee, living cost range, visa renewal fee, and MalishaEdu service charge (based on degree level, teaching language, and scholarship type - search RAG for 'MalishaEdu service charges' table). Calculate total first-year cost. Since lead is collected, also check database for specific program costs and ENCOURAGE signup/login for precise information."
            except Exception as e:
                print(f"RAG cost search failed: {e}")
        
        # Step 3: If still weak, use Tavily (for current policies, CSCA updates, etc.)
        tavily_context = None
        if not db_context and not rag_context:
            tavily_results = self.tavily_service.search(user_message, max_results=2)
            if tavily_results:
                tavily_context = self.tavily_service.format_search_results(tavily_results)
        
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
            universities_in_db = self.db.query(University).all()
            for uni in universities_in_db:
                if uni.name.lower() in user_msg_lower:
                    mentions_university = True
                    break
        
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
                "content": "CRITICAL: This user is ANONYMOUS and has NOT provided structured lead data. DO NOT use database program/intake information. Answer ONLY using the RAG knowledge base and, if needed, the web search context. Do NOT invent exact tuition or deadlines: use approximate values from RAG documents. Do NOT mention 'your database record' or any persistent profile."
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
                context_instruction += "\n5. If DATABASE MATCHES is empty or insufficient, say: 'I need to check our partner university database for [major] programs. Let me search for MalishaEdu partner universities offering [major] for [degree_level].'"
                context_instruction += "\n6. DO NOT invent or suggest any university names that are not in DATABASE MATCHES"
                context_instruction += "\n7. If you have partner universities in DATABASE MATCHES, list them and explain why they are good options for the user's major/degree level"
                context_instruction += "\n8. If a university is listed in DATABASE MATCHES but doesn't have the matching program, DO NOT suggest it - only suggest universities with actual matching programs"
            
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
            if asks_about_cost:
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
        is_simple_info_query: bool = False
    ) -> Tuple[str, str]:
        """
        Query database using StudentProfileState to build query parameters.
        Returns: (db_context, matched_programs)
        - db_context: General database information
        - matched_programs: Formatted list of matched programs with exact values
        """
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
                        partner_rag_results = self.rag_service.search_similar(
                            self.db,
                            partner_search_query,
                            top_k=5
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
                    University.is_partner == True
                ).all()
                
                if comparison_intakes:
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
                        if intake.documents_required:
                            context_parts.append(f"Documents Required: {intake.documents_required}")
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
   Documents Required: {intake.documents_required or 'Not in database'}
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
   Documents Required: {intake.documents_required or 'Not in database'}
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
    
    def _is_malishaedu_partner_university(self, university_name: str) -> Tuple[bool, Optional[University]]:
        """Check if a university is a MalishaEdu partner (from DB or RAG)"""
        # First check database using fuzzy matching
        matched_name, similar_matches = self._fuzzy_match_university(university_name)
        
        # Check the matched name
        if matched_name:
            uni = self.db.query(University).filter(
                University.name == matched_name,
                University.is_partner == True
            ).first()
            if uni:
                return True, uni
        
        # Also check similar matches (in case fuzzy matching returned None but has similar options)
        for similar_name in similar_matches[:3]:  # Check top 3 similar matches
            uni = self.db.query(University).filter(
                University.name == similar_name,
                University.is_partner == True
            ).first()
            if uni:
                return True, uni
        
        # Direct database search with ILIKE (case-insensitive partial match)
        # This handles cases like "Beihang University" matching "Beihang University (Hangzhou International Campus)"
        uni_name_lower = university_name.lower().strip()
        # Extract main words (remove common words)
        main_words = [w for w in uni_name_lower.split() if w not in ['university', 'college', 'institute', 'tech', 'technology', 'of', 'the', 'and', '&']]
        if main_words:
            # Search for universities containing the main words
            search_term = main_words[0]  # Use first meaningful word
            partner_universities = self.db.query(University).filter(
                University.is_partner == True,
                University.name.ilike(f"%{search_term}%")
            ).all()
            for uni in partner_universities:
                uni_name_lower_db = uni.name.lower()
                # Check if user input is contained in university name or vice versa
                if uni_name_lower in uni_name_lower_db or uni_name_lower_db in uni_name_lower:
                    return True, uni
                # Check if main words match
                uni_main_words = [w for w in uni_name_lower_db.split() if w not in ['university', 'college', 'institute', 'tech', 'technology', 'of', 'the', 'and', '&', '(', ')']]
                if set(main_words).intersection(set(uni_main_words)):
                    # At least one main word matches - check similarity
                    similarity = SequenceMatcher(None, uni_name_lower, uni_name_lower_db).ratio()
                    if similarity >= 0.6:  # Reasonable similarity threshold
                        return True, uni
        
        # If not found in DB, check RAG for MalishaEdu partner universities
        # RAG should contain list of 31 partner universities
        try:
            rag_results = self.rag_service.search_similar(
                self.db, 
                f"MalishaEdu partner university {university_name}",
                top_k=5
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
    
    def _is_malishaedu_major(self, major_name: str) -> Tuple[bool, Optional[Major]]:
        """Check if a major is offered by MalishaEdu (from DB or RAG)"""
        # First check database
        matched_name, _ = self._fuzzy_match_major(major_name)
        if matched_name:
            major = self.db.query(Major).filter(Major.name == matched_name).first()
            if major:
                return True, major
        
        # If not found in DB, check RAG for MalishaEdu majors (150+ majors)
        try:
            rag_results = self.rag_service.search_similar(
                self.db,
                f"MalishaEdu major {major_name}",
                top_k=3
            )
            if rag_results:
                # Check if any RAG result mentions this major
                for result in rag_results:
                    content_lower = result.get('content', '').lower()
                    major_name_lower = major_name.lower()
                    if major_name_lower in content_lower:
                        return True, None  # Found in RAG but not in DB
        except:
            pass
        
        return False, None
    
    def _get_partner_universities_from_rag(self) -> List[str]:
        """Get list of MalishaEdu partner universities from RAG"""
        try:
            rag_results = self.rag_service.search_similar(
                self.db,
                "MalishaEdu partner universities list 31",
                top_k=5
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
            rag_results = self.rag_service.search_similar(
                self.db,
                "MalishaEdu majors subjects 150",
                top_k=5
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

