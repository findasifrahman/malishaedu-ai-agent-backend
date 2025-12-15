"""
PartnerAgent - For logged-in partners
Goal: Help partners answer questions about MalishaEdu universities, majors, and programs
"""
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass, field
from sqlalchemy.orm import Session
from app.services.db_query_service import DBQueryService
from app.services.tavily_service import TavilyService
from app.services.openai_service import OpenAIService
from difflib import SequenceMatcher
from app.models import University, Major, ProgramIntake, ProgramDocument, ProgramIntakeScholarship, Scholarship, ProgramExamRequirement
from datetime import datetime, date
import json
import re


@dataclass
class PartnerQueryState:
    """
    Structured state extracted from conversation history (last 8 messages).
    This state is NOT saved to database - computed fresh each time.
    """
    degree_level: Optional[str] = None  # "Bachelor", "Master", "PhD", "Language", "Non-degree", etc.
    major: Optional[str] = None         # Major name or subject
    university: Optional[str] = None     # University name
    city: Optional[str] = None
    province: Optional[str] = None
    intake_term: Optional[str] = None   # March / September
    intake_year: Optional[int] = None
    teaching_language: Optional[str] = None  # English / Chinese
    scholarship_type: Optional[str] = None   # Type-A, Type-B, Partial, etc.
    has_ielts: Optional[bool] = None
    has_hsk: Optional[bool] = None
    has_csca: Optional[bool] = None
    ielts_score: Optional[float] = None
    hsk_score: Optional[int] = None
    csca_score: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for LLM context"""
        return {
            "degree_level": self.degree_level,
            "major": self.major,
            "university": self.university,
            "city": self.city,
            "province": self.province,
            "intake_term": self.intake_term,
            "intake_year": self.intake_year,
            "teaching_language": self.teaching_language,
            "scholarship_type": self.scholarship_type,
            "has_ielts": self.has_ielts,
            "has_hsk": self.has_hsk,
            "has_csca": self.has_csca,
            "ielts_score": self.ielts_score,
            "hsk_score": self.hsk_score,
            "csca_score": self.csca_score
        }


class PartnerAgent:
    """Partner agent for answering questions about MalishaEdu universities and programs"""
    
    PARTNER_SYSTEM_PROMPT = """You are the MalishaEdu Partner Agent, helping partners answer questions about Chinese universities and programs offered by MalishaEdu.

CRITICAL: Be CONCISE and CONVERSATIONAL. Build the conversation gradually. Don't overwhelm with too much information at once.

SYSTEM CORE (CRITICAL RULES):
- Use only DATABASE CONTEXT; never use general knowledge for programs/fees/deadlines
- Treat CURRENT DATE in DATABASE CONTEXT as the single source of truth for deadline checks and days remaining
- Never assume fee periods; always use *_fee_period. If missing, say "period not specified"
- If multiple deadlines exist (scholarship vs self-paid), show both clearly
- If info is missing in DB, say "not provided in our database" - do NOT infer or hallucinate
- If uncertain match, offer top 2-3 options and ask user to pick (do not guess)
- Always respond using the STRICT RESPONSE FORMAT below
- For ambiguous queries, ask only 1-2 key disambiguators (degree level + intake/year)

CURRENT DATE AWARENESS:
- Use CURRENT DATE from DATABASE CONTEXT as the single source of truth for deadline checks and days remaining
- Do NOT assume system time - the CURRENT DATE will be explicitly provided in DATABASE CONTEXT
- NEVER suggest past deadlines or intakes
- Only suggest universities/programs with UPCOMING application deadlines (deadline > current date)
- Calculate days remaining from CURRENT DATE (from DATABASE CONTEXT) to the deadline

MALISHAEDU PARTNER UNIVERSITIES & MAJORS (CRITICAL RULES):
- MalishaEdu works exclusively with partner universities (is_partner = True)
- The complete list of partner universities is provided in the DATABASE UNIVERSITIES context below
- The complete list of majors with their associated universities and degree levels is provided in the DATABASE MAJORS context below
- **CRITICAL: ALWAYS ONLY suggest MalishaEdu partner universities from the DATABASE UNIVERSITIES list**
- **CRITICAL: NEVER suggest or mention non-partner universities, even if they are well-known**
- **CRITICAL: Use the DATABASE UNIVERSITIES and DATABASE MAJORS lists provided - DO NOT use general knowledge**

UNIVERSITY QUALITY (Based on Ranking):
- World ranking below 500: Exceptional university
- World ranking below 1000: Good university
- World ranking below 2000: Average university
- No rating (null or -1): Not rated

FUZZY MATCHING & CLARIFICATION:
- Use university aliases and major keywords to understand user queries
- Users may type with typos, in different languages, or use abbreviations
- If multiple close matches (university or major), return top 2-3 options and ask user to pick
- Example: "I found a few options that might match. Did you mean [Option 1], [Option 2], or [Option 3]? Please let me know which one."
- For ambiguous queries (e.g., "fees for NEFU"), do NOT ask many questions - ask only 1-2 key disambiguators (degree level + intake/year)
- Do NOT guess which university/major the user meant if there are similar options

DEGREE LEVEL MATCHING (CRITICAL):
- If user wants "Master" or "Masters", ONLY suggest Master programs - do NOT suggest Bachelor or PhD
- If user wants "PhD" or "Doctoral", ONLY suggest PhD programs - do NOT suggest Master or Bachelor
- If user wants "Bachelor", ONLY suggest Bachelor programs
- If user wants "Language" program, ONLY suggest Language/Non-degree programs
- Use fuzzy logic: "masters" = "Master", "phd" = "PhD", "doctoral" = "PhD", "bachelor" = "Bachelor"
- If someone wants to study a language program, suggest language programs. If there are no upcoming intakes, tell them to wait for further info

ONLY UPCOMING DEADLINES:
- Many universities exist in the database, but NOT all have upcoming intakes (March/September 2026+)
- ONLY suggest universities/programs where application_deadline > current_date
- If a program's deadline has passed, do NOT suggest it
- Check intake_term and intake_year against current date

INTAKE TERM MATCHING (CRITICAL):
- In China, there are ONLY two main intake terms: March and September
- When user asks for "March language program" or "march intake", show ONLY programs with intake_term = "March"
- When user asks for "September language program" or "september intake", show ONLY programs with intake_term = "September"
- DO NOT mix March and September intakes - they are completely different semesters
- If user asks for "march" and you find programs with "september" intake_term, DO NOT include them
- The database context will already filter by exact intake_term match, so trust the filtered results

SCHOLARSHIP INFORMATION:
- Provide accurate scholarship info from database (program_intake_scholarships table)
- Explain what each scholarship covers (tuition, accommodation, insurance, living allowance)
- Calculate total costs including fees and service charges
- Compare costs between different programs if user asks

DOCUMENT REQUIREMENTS:
- Provide EXACT documents required from database (program_documents table)
- Include rules and notes for each document
- Specify if documents apply to inside_china_only applicants

LANGUAGE REQUIREMENTS:
- HSK requirements: Check hsk_required, hsk_level, hsk_min_score from program_intake
- English test requirements: Check english_test_required, english_test_note from program_intake
- CSCA requirements: Check program_exam_requirements table for CSCA subjects and scores
- If user has no IELTS/English degree, ask about degree level and suggest programs accordingly

LANGUAGE PROGRAMS (CRITICAL):
- Language programs in China are ALWAYS taught in English (or Chinese for Chinese language programs)
- DO NOT ask about teaching medium (English/Chinese) for language programs - it's always English
- When user asks for "language program", they mean learning English or Chinese language
- Language programs are typically "Non-degree" or "Language" category programs
- If the major.category indicates "Language/Non-degree" or "Non-degree/Language Program", do NOT ask for teaching language; only ask intake term/year
- For language programs, focus on duration (one semester vs one year) and intake term (March vs September)
- DO NOT ask "Do you want it taught in English or Chinese?" for language programs

BANK GUARANTEE:
- Provide exact bank_statement_amount and bank_statement_currency from database
- Include bank_statement_note if available

COST CALCULATION:
- Calculate total cost including:
  * Application fee
  * Tuition (per year or semester)
  * Accommodation fee - Use accommodation_fee_period exactly (month/year/semester). If period is missing, say "period not specified in database"
  * Medical insurance fee - Use medical_insurance_fee_period exactly (month/year/semester). If period is missing, say "period not specified in database"
  * Arrival medical checkup fee (one-time, if arrival_medical_checkup_is_one_time is True)
  * Visa extension fee (annual, required each year)
  * MalishaEdu service charge (based on degree level, teaching language, scholarship type)
- Present costs in short sentences, build conversation gradually
- NEVER assume accommodation is "per year" - check accommodation_fee_period field

DATABASE-FIRST POLICY (CRITICAL):
- Answer from DB tables whenever possible: majors, program_intakes, program_documents, program_exam_requirements, program_intake_scholarships
- Only use web search (Tavily) when:
  * User asks about visa rules (X1/X2, work rules, part-time work)
  * User asks about rankings (if not in DB)
  * User asks about city climate/safety/campus lifestyle that isn't in DB notes
  * User asks about halal food, Muslim-friendly dorms (if not in DB)
  * User asks about latest CSCA policy updates or current visa requirements
  * NO program match exists in DB (then suggest similar programs from DB first)
- When web is used: Cite sources if available, summarize briefly, and note that policies may vary by country
- DO NOT use web search for: universities, majors, fees, deadlines, documents, scholarships, exam requirements (these MUST come from DB)

CONVERSATION BUILDING:
- Build conversation gradually - don't answer everything in one message
- Ask follow-up questions to narrow down options
- If suggested university/major list is long (>10), show top 10 and mention there are more
- Continue giving info and asking about majors to help partner find the best match

PERSUASION:
- Always try to persuade partners to choose MalishaEdu listed universities and majors
- If someone asks about "cyber security" but it's not in database, suggest "Computer Science" which covers it
- If a major is not available, suggest similar majors from the database

SCOPE:
- ONLY answer about MalishaEdu universities and China education
- Do NOT answer questions about other countries or education systems
- If asked about non-China topics, politely redirect: "I can only help with questions about Chinese universities and MalishaEdu programs."

STRICT RESPONSE FORMAT (MANDATORY - follow this exact structure):
1. Best Match:
   - University Name + Program Name + Intake (term and year)
   - Teaching Language
   
2. Deadlines:
   - Application Deadline: [date] ([X] days remaining from CURRENT DATE: [date from DB context])
   - Scholarship Deadline: [date if different] OR "Same as application deadline"
   - If both scholarship and self-funded deadlines exist, show both explicitly
   - Recommend the earlier deadline if student wants scholarship
   
3. Eligibility:
   - Age: [age_min] to [age_max] years OR "not provided in our database"
   - Minimum Score: [min_average_score] OR "not provided in our database"
   - Language Tests: [HSK/IELTS/TOEFL requirements] OR "not provided in our database"
   - Exams: [CSCA subjects/requirements] OR "not provided in our database"
   - Interview: [Required/Not required] OR "not provided in our database"
   - Written Test: [Required/Not required] OR "not provided in our database"
   
4. Cost Summary:
   - Tuition: [amount] [currency]/[year or semester]
   - Accommodation: [amount] [currency] per [period from accommodation_fee_period]
     * If monthly: Also show estimated annual (×12, marked "estimate")
   - Medical Insurance: [amount] [currency] per [period from medical_insurance_fee_period]
     * If monthly: Also show estimated annual (×12, marked "estimate")
   - Application Fee: [amount] [currency] (one-time)
   - Arrival Medical Checkup: [amount] [currency] (one-time)
   - Visa Extension: [amount] [currency] (annual, required each year)
   - Year 1 Total Estimate: [sum of all above for first year]
   
5. Scholarships:
   - For each scholarship:
     * Name: [scholarship_name]
     * Coverage: Tuition [Yes/No/XX% waiver], Accommodation [Yes/No], Insurance [Yes/No]
     * Living Allowance: [monthly/yearly amount] OR "None"
     * First Year Only: [Yes/No] OR "not provided in our database"
     * Renewal Required: [Yes/No] OR "not provided in our database"
     * Deadline: [date] OR "Same as application deadline"
   
6. Required Documents:
   - List ALL documents (do not truncate)
   - For each: [Document Name] ([Required/Optional]) - [rules if available]
   
7. Next Step Question:
   - Ask exactly 1 question to move the lead forward
   - Examples: "Do they want scholarship or self-funded?", "What's their preferred intake term?", "Do they have IELTS/HSK scores?"

MISSING INFORMATION HANDLING:
- If a field is not present in DATABASE CONTEXT, say "not provided in our database" - do NOT infer or guess
- Do NOT make up values for fees, deadlines, or requirements that are not in the database
- If uncertain about a match, present 2-3 options and ask the user to confirm

FUZZY MATCHING BEHAVIOR:
- If confidence is low OR there are multiple close matches, present 2-3 options and ask the user to pick
- Do NOT assume which university/major the user meant if there are similar options
- Example: "I found a few options that might match. Did you mean [Option 1], [Option 2], or [Option 3]? Please let me know which one."

AGENT MODE DETECTION:
- If user message includes: "student profile", "eligible", "can I apply", "eligibility" → Focus on Eligibility-first (age, scores, language tests, requirements)
- If user message includes: "commission", "process", "documents checklist", "what to collect", "pitch" → Focus on Operations-first (documents, process, fees, deadlines)

MULTI-DEADLINE CLARITY:
- If both scholarship deadline and self-paid deadline exist, show both explicitly
- Recommend the earlier deadline if the student wants scholarship
- Clearly distinguish between application deadline and scholarship deadline

Style Guidelines:
- CONCISE: Keep responses brief
- CONVERSATIONAL: Talk naturally, not like a textbook
- NO REPETITION: Never repeat the same information
- PROGRESSIVE: Start with basics, add details when asked
- FRIENDLY: Warm and helpful
- STRUCTURED: Use bullet points for lists (3+ items)
- ALWAYS follow the RESPONSE FORMAT above
"""

    def __init__(self, db: Session):
        self.db = db
        self.db_service = DBQueryService(db)
        self.tavily_service = TavilyService()
        self.openai_service = OpenAIService()
        
        # Load all partner universities at startup
        self.all_universities = self._load_all_universities()
        
        # Load all majors with university and degree level associations at startup
        self.all_majors = self._load_all_majors()
    
    def _load_all_universities(self) -> List[Dict[str, Any]]:
        """Load all partner universities from database at startup"""
        try:
            print(f"DEBUG: Loading all partner universities from database...")
            universities = self.db.query(University).filter(University.is_partner == True).all()
            print(f"DEBUG: Found {len(universities)} partner universities")
            return [
                {
                    "id": uni.id,
                    "name": uni.name,
                    "name_cn": uni.name_cn,
                    "aliases": uni.aliases if isinstance(uni.aliases, list) else (json.loads(uni.aliases) if isinstance(uni.aliases, str) else []),
                    "city": uni.city,
                    "province": uni.province,
                    "university_ranking": uni.university_ranking,
                    "world_ranking_band": uni.world_ranking_band,
                    "national_ranking": uni.national_ranking,
                    "country": uni.country or "China",
                    "description": uni.description
                }
                for uni in universities
            ]
        except Exception as e:
            import traceback
            print(f"ERROR: Error loading universities: {e}")
            traceback.print_exc()
            return []
    
    def _load_all_majors(self) -> List[Dict[str, Any]]:
        """Load all majors with university and degree level associations at startup"""
        try:
            print(f"DEBUG: Loading all majors from database...")
            majors = self.db.query(Major).join(University).filter(University.is_partner == True).all()
            print(f"DEBUG: Found {len(majors)} majors")
            return [
                {
                    "id": major.id,
                    "name": major.name,
                    "name_cn": major.name_cn,
                    "keywords": major.keywords if isinstance(major.keywords, list) else (json.loads(major.keywords) if isinstance(major.keywords, str) else []),
                    "university_id": major.university_id,
                    "university_name": major.university.name,
                    "degree_level": major.degree_level if major.degree_level else None,  # Already a string, no .value needed
                    "teaching_language": major.teaching_language,
                    "discipline": major.discipline,
                    "duration_years": major.duration_years,
                    "category": major.category
                }
                for major in majors
            ]
        except Exception as e:
            import traceback
            print(f"ERROR: Error loading majors: {e}")
            traceback.print_exc()
            return []
    
    def extract_partner_query_state(self, conversation_history: List[Dict[str, str]]) -> PartnerQueryState:
        """
        Extract and consolidate PartnerQueryState from conversation history (last 8 messages).
        Uses LLM to infer state.
        """
        if not conversation_history:
            return PartnerQueryState()
        
        # Build conversation text from last 8 messages
        conversation_text = ""
        for msg in conversation_history[-8:]:
            role = msg.get('role', '')
            content = msg.get('content', '')
            conversation_text += f"{role}: {content}\n"
        
        # Get list of available majors and universities for reference
        major_list = [major["name"] for major in self.all_majors[:100]]
        major_list_str = ", ".join(major_list)
        if len(self.all_majors) > 100:
            major_list_str += f"\n... and {len(self.all_majors) - 100} more majors"
        
        uni_list = [uni["name"] for uni in self.all_universities[:50]]
        uni_list_str = ", ".join(uni_list)
        if len(self.all_universities) > 50:
            uni_list_str += f"\n... and {len(self.all_universities) - 50} more universities"
        
        # Use LLM to extract state
        extraction_prompt = f"""You are a state extractor for partner queries. Given the conversation, output a JSON object with these fields:
- degree_level: "Bachelor", "Master", "PhD", "Language", "Non-degree", "Associate", "Vocational College", "Junior high", "Senior high", or null
- major: specific major name from the database list below, or the closest match, or null
- university: university name from the database list below, or the closest match, or null
- city: city name or null
- province: province name or null
- intake_term: "March", "September", or null
- intake_year: year number (e.g., 2026) or null
- teaching_language: "English", "Chinese", or null
- scholarship_type: "Type-A", "Type-B", "Type-C", "Type-D", "Partial-Low", "Partial-Mid", "Partial-High", "Self-Paid", "None", or null
- has_ielts: true/false/null
- has_hsk: true/false/null
- has_csca: true/false/null
- ielts_score: score number or null
- hsk_score: score number or null
- csca_score: score number or null

AVAILABLE MAJORS IN DATABASE (for matching):
{major_list_str}

AVAILABLE UNIVERSITIES IN DATABASE (for matching):
{uni_list_str}

CRITICAL RULES:
1. Use ONLY information explicitly stated by the user. Do NOT guess.
2. LATEST MESSAGE WINS: If user changes their mind, use the LATEST statement.
3. For major matching: Look through AVAILABLE MAJORS to find the closest match.
4. For university matching: Look through AVAILABLE UNIVERSITIES to find the closest match.
5. Use fuzzy matching: "masters" = "Master", "phd" = "PhD", "doctoral" = "PhD", "bachelor" = "Bachelor"

Conversation:
{conversation_text}

Output ONLY valid JSON, no other text:"""

        try:
            print(f"DEBUG: PartnerAgent - Extracting state from conversation (last 8 messages)")
            response = self.openai_service.chat_completion(
                messages=[
                    {"role": "system", "content": "You are a JSON extractor. Output only valid JSON."},
                    {"role": "user", "content": extraction_prompt}
                ],
                temperature=0.1
            )
            
            content = response.choices[0].message.content.strip()
            # Remove markdown code blocks if present
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
            
            extracted = json.loads(content)
            
            state = PartnerQueryState(
                degree_level=extracted.get('degree_level'),
                major=extracted.get('major'),
                university=extracted.get('university'),
                city=extracted.get('city'),
                province=extracted.get('province'),
                intake_term=extracted.get('intake_term'),
                intake_year=extracted.get('intake_year'),
                teaching_language=extracted.get('teaching_language'),
                scholarship_type=extracted.get('scholarship_type'),
                has_ielts=extracted.get('has_ielts'),
                has_hsk=extracted.get('has_hsk'),
                has_csca=extracted.get('has_csca'),
                ielts_score=extracted.get('ielts_score'),
                hsk_score=extracted.get('hsk_score'),
                csca_score=extracted.get('csca_score')
            )
            
            print(f"DEBUG: Successfully extracted state: {state.to_dict()}")
            return state
        except Exception as e:
            import traceback
            print(f"ERROR: Error extracting state: {e}")
            traceback.print_exc()
            return PartnerQueryState()
    
    def _fuzzy_match_university(self, user_input: str) -> Tuple[bool, Optional[Dict[str, Any]], List[Tuple[Dict[str, Any], float]]]:
        """
        Fuzzy match user input to university from pre-loaded array.
        Returns (matched: bool, best_match: Optional[Dict], all_matches: List[Tuple[Dict, score]])
        If multiple close matches, return top 2-3 for user to pick.
        """
        user_input_lower = user_input.lower().strip()
        
        matches = []
        for uni in self.all_universities:
            uni_name_lower = uni["name"].lower()
            
            # Exact match
            if user_input_lower == uni_name_lower:
                return True, uni, [(uni, 1.0)]
            
            # Check main name
            if user_input_lower in uni_name_lower or uni_name_lower in user_input_lower:
                match_ratio = SequenceMatcher(None, user_input_lower, uni_name_lower).ratio()
                matches.append((uni, match_ratio))
            
            # Check aliases
            aliases = uni.get("aliases", [])
            if aliases:
                for alias in aliases:
                    alias_lower = str(alias).lower()
                    if user_input_lower == alias_lower:
                        return True, uni, [(uni, 1.0)]
                    if user_input_lower in alias_lower or alias_lower in user_input_lower:
                        match_ratio = SequenceMatcher(None, user_input_lower, alias_lower).ratio()
                        matches.append((uni, match_ratio))
            
            # Check Chinese name
            if uni.get("name_cn"):
                name_cn_lower = str(uni["name_cn"]).lower()
                if user_input_lower == name_cn_lower:
                    return True, uni, [(uni, 1.0)]
                if user_input_lower in name_cn_lower or name_cn_lower in user_input_lower:
                    match_ratio = SequenceMatcher(None, user_input_lower, name_cn_lower).ratio()
                    matches.append((uni, match_ratio))
        
        if matches:
            # Remove duplicates (same university with different match scores)
            seen_unis = {}
            for uni, score in matches:
                uni_id = uni.get("id")
                if uni_id not in seen_unis or seen_unis[uni_id][1] < score:
                    seen_unis[uni_id] = (uni, score)
            matches = list(seen_unis.values())
            
            # Sort by match ratio (highest first)
            matches.sort(key=lambda x: x[1], reverse=True)
            best_match = matches[0]
            if best_match[1] >= 0.8:  # High confidence threshold (80%)
                return True, best_match[0], matches[:3]  # Return top 3 for context
            elif best_match[1] >= 0.6:  # Medium confidence (60-80%)
                # Return top 2-3 options for user to pick
                return False, None, matches[:3]
        
        return False, None, []
    
    def _fuzzy_match_major(self, user_input: str, university_id: Optional[int] = None, degree_level: Optional[str] = None) -> List[Tuple[Dict[str, Any], float]]:
        """
        Fuzzy match user input to major from pre-loaded array.
        Checks name, keywords, and filters by university/degree level if provided.
        """
        user_input_clean = re.sub(r'[^\w\s&]', '', user_input.lower())
        
        all_majors = self.all_majors
        if university_id:
            all_majors = [m for m in all_majors if m["university_id"] == university_id]
        if degree_level:
            all_majors = [m for m in all_majors if m.get("degree_level") and degree_level.lower() in str(m["degree_level"]).lower()]
        
        matches = []
        for major in all_majors:
            major_name_clean = re.sub(r'[^\w\s&]', '', major["name"].lower())
            
            # Exact match
            if user_input_clean == major_name_clean:
                matches.append((major, 1.0))
                continue
            
            # Substring match
            if user_input_clean in major_name_clean or major_name_clean in user_input_clean:
                match_ratio = SequenceMatcher(None, user_input_clean, major_name_clean).ratio()
                matches.append((major, match_ratio))
                continue
            
            # Check keywords
            keywords = major.get("keywords", [])
            if keywords:
                for keyword in keywords:
                    keyword_clean = re.sub(r'[^\w\s&]', '', str(keyword).lower())
                    if user_input_clean in keyword_clean or keyword_clean in user_input_clean:
                        match_ratio = SequenceMatcher(None, user_input_clean, keyword_clean).ratio()
                        matches.append((major, match_ratio))
                        break
            
            # Word overlap
            user_words = set(user_input_clean.split())
            major_words = set(major_name_clean.split())
            common_words = user_words & major_words
            if common_words and len(common_words) >= 2:
                match_ratio = len(common_words) / max(len(user_words), len(major_words))
                if match_ratio >= 0.4:
                    matches.append((major, match_ratio))
        
        # Sort by match ratio
        matches.sort(key=lambda x: x[1], reverse=True)
        return matches[:10]  # Return top 10 matches
    
    def _get_upcoming_intakes(self, current_date: date) -> List[Dict[str, Any]]:
        """
        Get all program intakes with upcoming deadlines (deadline > current_date).
        Returns list of intakes with university, major, and deadline info.
        """
        try:
            print(f"DEBUG: Loading upcoming intakes (deadline > {current_date})...")
            intakes = self.db.query(ProgramIntake).join(Major).join(University).filter(
                University.is_partner == True,
                ProgramIntake.application_deadline > current_date
            ).all()
            print(f"DEBUG: Found {len(intakes)} upcoming intakes")
            
            result = []
            for intake in intakes:
                result.append({
                    "id": intake.id,
                    "university_id": intake.university_id,
                    "university_name": intake.university.name,
                    "major_id": intake.major_id,
                    "major_name": intake.major.name,
                    "degree_level": intake.major.degree_level if intake.major and intake.major.degree_level else None,  # Get from Major, not ProgramIntake
                    "intake_term": intake.intake_term.value if intake.intake_term else None,
                    "intake_year": intake.intake_year,
                    "application_deadline": intake.application_deadline.isoformat() if intake.application_deadline else None,
                    "teaching_language": intake.teaching_language,
                    "tuition_per_year": intake.tuition_per_year,
                    "tuition_per_semester": intake.tuition_per_semester,
                    "application_fee": intake.application_fee,
                    "accommodation_fee": intake.accommodation_fee,
                    "medical_insurance_fee": intake.medical_insurance_fee,
                    "arrival_medical_checkup_fee": intake.arrival_medical_checkup_fee,
                    "visa_extension_fee": intake.visa_extension_fee,
                    "scholarship_available": intake.scholarship_available,
                    "hsk_required": intake.hsk_required,
                    "hsk_level": intake.hsk_level,
                    "hsk_min_score": intake.hsk_min_score,
                    "english_test_required": intake.english_test_required,
                    "english_test_note": intake.english_test_note,
                    "bank_statement_required": intake.bank_statement_required,
                    "bank_statement_amount": intake.bank_statement_amount,
                    "bank_statement_currency": intake.bank_statement_currency,
                    "bank_statement_note": intake.bank_statement_note,
                    "currency": intake.currency or "CNY",
                    "accommodation_fee_period": intake.accommodation_fee_period,
                    "medical_insurance_fee_period": intake.medical_insurance_fee_period,
                    "arrival_medical_checkup_is_one_time": intake.arrival_medical_checkup_is_one_time,
                    "age_min": intake.age_min,
                    "age_max": intake.age_max,
                    "min_average_score": intake.min_average_score,
                    "interview_required": intake.interview_required,
                    "written_test_required": intake.written_test_required
                })
            return result
        except Exception as e:
            import traceback
            print(f"ERROR: Error loading upcoming intakes: {e}")
            traceback.print_exc()
            return []
    
    def _get_program_documents(self, program_intake_id: int) -> List[Dict[str, Any]]:
        """Get required documents for a program intake"""
        try:
            documents = self.db.query(ProgramDocument).filter(
                ProgramDocument.program_intake_id == program_intake_id
            ).all()
            
            return [
                {
                    "name": doc.name,
                    "is_required": doc.is_required,
                    "rules": doc.rules,
                    "applies_to": doc.applies_to
                }
                for doc in documents
            ]
        except Exception as e:
            print(f"Error loading program documents: {e}")
            return []
    
    def _get_program_scholarships(self, program_intake_id: int) -> List[Dict[str, Any]]:
        """Get scholarships available for a program intake"""
        try:
            intake_scholarships = self.db.query(ProgramIntakeScholarship).filter(
                ProgramIntakeScholarship.program_intake_id == program_intake_id
            ).all()
            
            result = []
            for intake_sch in intake_scholarships:
                scholarship = intake_sch.scholarship
                result.append({
                    "scholarship_name": scholarship.name if scholarship else None,
                    "provider": scholarship.provider if scholarship else None,
                    "covers_tuition": intake_sch.covers_tuition,
                    "covers_accommodation": intake_sch.covers_accommodation,
                    "covers_insurance": intake_sch.covers_insurance,
                    "tuition_waiver_percent": intake_sch.tuition_waiver_percent,
                    "living_allowance_monthly": intake_sch.living_allowance_monthly,
                    "living_allowance_yearly": intake_sch.living_allowance_yearly,
                    "first_year_only": intake_sch.first_year_only,
                    "renewal_required": intake_sch.renewal_required,
                    "deadline": intake_sch.deadline.isoformat() if intake_sch.deadline else None,
                    "eligibility_note": intake_sch.eligibility_note
                })
            return result
        except Exception as e:
            print(f"Error loading program scholarships: {e}")
            return []
    
    def _get_program_exam_requirements(self, program_intake_id: int) -> List[Dict[str, Any]]:
        """Get exam requirements for a program intake"""
        try:
            exam_reqs = self.db.query(ProgramExamRequirement).filter(
                ProgramExamRequirement.program_intake_id == program_intake_id
            ).all()
            
            return [
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
        except Exception as e:
            print(f"Error loading exam requirements: {e}")
            return []
    
    def _build_database_context(self, state: PartnerQueryState, current_date: date) -> str:
        """
        Build database context based on query state.
        Only includes universities/programs with upcoming deadlines.
        """
        context_parts = []
        
        # Get current date string
        current_date_str = current_date.isoformat()
        context_parts.append(f"CURRENT DATE: {current_date_str}")
        context_parts.append("IMPORTANT: Only suggest universities/programs with application_deadline > current_date")
        
        # Add all partner universities
        if self.all_universities:
            uni_list = []
            for uni in self.all_universities:
                uni_info = f"- {uni['name']}"
                if uni.get('name_cn'):
                    uni_info += f" ({uni['name_cn']})"
                if uni.get('city'):
                    uni_info += f" - {uni['city']}"
                if uni.get('province'):
                    uni_info += f", {uni['province']}"
                if uni.get('university_ranking'):
                    uni_info += f" [University Ranking: {uni['university_ranking']}]"
                if uni.get('world_ranking_band'):
                    uni_info += f" [World Ranking Band: {uni['world_ranking_band']}]"
                if uni.get('national_ranking'):
                    uni_info += f" [National Ranking: {uni['national_ranking']}]"
                if uni.get('aliases'):
                    aliases_str = ", ".join(str(a) for a in uni['aliases'][:3])
                    uni_info += f" (Also known as: {aliases_str})"
                uni_list.append(uni_info)
            context_parts.append(f"\nDATABASE UNIVERSITIES (MalishaEdu Partner Universities):\n" + "\n".join(uni_list))
        
        # Add all majors (limited to avoid token limits)
        if self.all_majors:
            major_list = []
            for major in self.all_majors[:200]:
                major_info = f"- {major['name']}"
                if major.get('name_cn'):
                    major_info += f" ({major['name_cn']})"
                major_info += f" at {major['university_name']}"
                if major.get('degree_level'):
                    major_info += f" ({major['degree_level']})"
                if major.get('teaching_language'):
                    major_info += f" [{major['teaching_language']}]"
                if major.get('keywords'):
                    keywords_str = ", ".join(str(k) for k in major['keywords'][:3])
                    major_info += f" (Keywords: {keywords_str})"
                major_list.append(major_info)
            context_parts.append(f"\nDATABASE MAJORS (MalishaEdu Majors):\n" + "\n".join(major_list))
            if len(self.all_majors) > 200:
                context_parts.append(f"... and {len(self.all_majors) - 200} more majors")
        
        # Get upcoming intakes
        upcoming_intakes = self._get_upcoming_intakes(current_date)
        
        # Filter intakes based on state
        filtered_intakes = upcoming_intakes
        if state.degree_level:
            filtered_intakes = [i for i in filtered_intakes if i.get('degree_level') and state.degree_level.lower() in str(i['degree_level']).lower()]
        if state.university:
            matched_uni, uni_dict, uni_matches = self._fuzzy_match_university(state.university)
            if matched_uni and uni_dict:
                filtered_intakes = [i for i in filtered_intakes if i['university_id'] == uni_dict['id']]
            elif uni_matches and len(uni_matches) > 1:
                # Multiple close matches - add to context for LLM to handle
                context_parts.append(f"\nNOTE: Multiple university matches found for '{state.university}':")
                for uni_match, score in uni_matches[:3]:
                    context_parts.append(f"  - {uni_match['name']} (match score: {score:.2f})")
                context_parts.append("Please ask user to clarify which university they meant.")
        if state.major:
            matched_major, major_dict, major_matches = self._fuzzy_match_major(state.major, degree_level=state.degree_level)
            if matched_major and major_dict:
                filtered_intakes = [i for i in filtered_intakes if i['major_id'] == major_dict['id']]
            elif major_matches:
                # Multiple close matches - use top matches
                matched_major_ids = [m[0]['id'] for m in major_matches[:3]]
                filtered_intakes = [i for i in filtered_intakes if i['major_id'] in matched_major_ids]
                # Add to context for LLM to handle
                if len(major_matches) > 1:
                    context_parts.append(f"\nNOTE: Multiple major matches found for '{state.major}':")
                    for major_match, score in major_matches[:3]:
                        context_parts.append(f"  - {major_match['name']} at {major_match.get('university_name', 'N/A')} (match score: {score:.2f})")
                    context_parts.append("Showing programs for all matches. Please ask user to clarify if needed.")
        if state.intake_term:
            # CRITICAL: Exact match for intake_term (March vs September are different)
            # In China, there are only two main intakes: March and September
            filtered_intakes = [i for i in filtered_intakes if i.get('intake_term') and str(i['intake_term']).lower() == state.intake_term.lower()]
            print(f"DEBUG: Filtered by intake_term '{state.intake_term}': {len(filtered_intakes)} intakes remaining")
        if state.intake_year:
            filtered_intakes = [i for i in filtered_intakes if i.get('intake_year') == state.intake_year]
        if state.teaching_language:
            filtered_intakes = [i for i in filtered_intakes if i.get('teaching_language') and state.teaching_language.lower() in str(i['teaching_language']).lower()]
        
        # Limit to top 20 intakes
        filtered_intakes = filtered_intakes[:20]
        
        if filtered_intakes:
            context_parts.append(f"\n=== MATCHED PROGRAM INTAKES (Upcoming Deadlines Only) ===")
            for intake in filtered_intakes:
                intake_info = f"\nProgram: {intake['university_name']} - {intake['major_name']} ({intake.get('degree_level', 'N/A')})"
                intake_info += f"\n  Intake: {intake.get('intake_term', 'N/A')} {intake.get('intake_year', 'N/A')}"
                if intake.get('application_deadline'):
                    deadline_date = datetime.fromisoformat(intake['application_deadline']).date()
                    days_remaining = (deadline_date - current_date).days
                    intake_info += f"\n  Application Deadline: {intake['application_deadline']} ({days_remaining} days remaining)"
                intake_info += f"\n  Teaching Language: {intake.get('teaching_language', 'N/A')}"
                
                # Age requirements
                if intake.get('age_min') or intake.get('age_max'):
                    age_range = []
                    if intake.get('age_min'):
                        age_range.append(f"Min: {intake['age_min']}")
                    if intake.get('age_max'):
                        age_range.append(f"Max: {intake['age_max']}")
                    intake_info += f"\n  Age Requirements: {', '.join(age_range)}"
                
                # Minimum score
                if intake.get('min_average_score'):
                    intake_info += f"\n  Minimum Average Score: {intake['min_average_score']}"
                
                # Interview/written test
                if intake.get('interview_required'):
                    intake_info += f"\n  Interview Required: Yes"
                if intake.get('written_test_required'):
                    intake_info += f"\n  Written Test Required: Yes"
                
                # Fees
                if intake.get('tuition_per_year'):
                    intake_info += f"\n  Tuition: {intake['tuition_per_year']} {intake.get('currency', 'CNY')}/year"
                elif intake.get('tuition_per_semester'):
                    intake_info += f"\n  Tuition: {intake['tuition_per_semester']} {intake.get('currency', 'CNY')}/semester"
                if intake.get('application_fee'):
                    intake_info += f"\n  Application Fee: {intake['application_fee']} {intake.get('currency', 'CNY')}"
                if intake.get('accommodation_fee'):
                    period = intake.get('accommodation_fee_period', 'period not specified')
                    currency = intake.get('currency', 'CNY')
                    fee = intake['accommodation_fee']
                    intake_info += f"\n  Accommodation Fee: {fee} {currency} per {period}"
                    # If monthly, also show estimated annual
                    if period and 'month' in str(period).lower():
                        annual_estimate = float(fee) * 12
                        intake_info += f" (estimated annual: {annual_estimate:.2f} {currency})"
                if intake.get('medical_insurance_fee'):
                    period = intake.get('medical_insurance_fee_period', 'period not specified')
                    currency = intake.get('currency', 'CNY')
                    fee = intake['medical_insurance_fee']
                    intake_info += f"\n  Medical Insurance: {fee} {currency} per {period}"
                    # If monthly, also show estimated annual
                    if period and 'month' in str(period).lower():
                        annual_estimate = float(fee) * 12
                        intake_info += f" (estimated annual: {annual_estimate:.2f} {currency})"
                if intake.get('arrival_medical_checkup_fee'):
                    one_time = " (one-time)" if intake.get('arrival_medical_checkup_is_one_time', True) else ""
                    intake_info += f"\n  Arrival Medical Checkup: {intake['arrival_medical_checkup_fee']} {intake.get('currency', 'CNY')}{one_time}"
                if intake.get('bank_statement_amount'):
                    intake_info += f"\n  Bank Statement Required: {intake['bank_statement_amount']} {intake.get('bank_statement_currency', 'CNY')}"
                    if intake.get('bank_statement_note'):
                        intake_info += f" ({intake['bank_statement_note']})"
                
                # Language requirements
                if intake.get('hsk_required'):
                    intake_info += f"\n  HSK Required: Level {intake.get('hsk_level', 'N/A')}, Min Score: {intake.get('hsk_min_score', 'N/A')}"
                if intake.get('english_test_required'):
                    intake_info += f"\n  English Test Required: {intake.get('english_test_note', 'Yes')}"
                
                # Scholarships
                if intake.get('scholarship_available'):
                    scholarships = self._get_program_scholarships(intake['id'])
                    if scholarships:
                        intake_info += f"\n  Scholarships Available:"
                        for sch in scholarships[:3]:
                            sch_info = f"    - {sch.get('scholarship_name', 'N/A')}"
                            if sch.get('covers_tuition'):
                                intake_info += f"\n      Covers Tuition: Yes"
                            if sch.get('tuition_waiver_percent'):
                                intake_info += f"\n      Tuition Waiver: {sch['tuition_waiver_percent']}%"
                            if sch.get('covers_accommodation'):
                                intake_info += f"\n      Covers Accommodation: Yes"
                            if sch.get('covers_insurance'):
                                intake_info += f"\n      Covers Insurance: Yes"
                            if sch.get('living_allowance_monthly'):
                                intake_info += f"\n      Living Allowance: {sch['living_allowance_monthly']} CNY/month"
                            elif sch.get('living_allowance_yearly'):
                                intake_info += f"\n      Living Allowance: {sch['living_allowance_yearly']} CNY/year"
                            if sch.get('first_year_only'):
                                intake_info += f"\n      First Year Only: Yes"
                            if sch.get('renewal_required'):
                                intake_info += f"\n      Renewal Required: Yes"
                            if sch.get('deadline'):
                                intake_info += f"\n      Scholarship Deadline: {sch['deadline']}"
                            if sch.get('eligibility_note'):
                                intake_info += f"\n      Eligibility Note: {sch['eligibility_note']}"
                
                # Documents - Show ALL documents (do not truncate)
                documents = self._get_program_documents(intake['id'])
                if documents:
                    intake_info += f"\n  Required Documents ({len(documents)} total):"
                    for doc in documents:  # Show all, not truncated
                        req_str = "Required" if doc.get('is_required') else "Optional"
                        intake_info += f"\n    - {doc.get('name', 'N/A')} ({req_str})"
                        if doc.get('rules'):
                            intake_info += f": {doc['rules']}"
                        if doc.get('applies_to'):
                            intake_info += f" [Applies to: {doc['applies_to']}]"
                
                # Exam requirements
                exam_reqs = self._get_program_exam_requirements(intake['id'])
                if exam_reqs:
                    intake_info += f"\n  Exam Requirements:"
                    for req in exam_reqs[:3]:
                        req_str = "Required" if req.get('required') else "Optional"
                        intake_info += f"\n    - {req.get('exam_name', 'N/A')} ({req_str})"
                        if req.get('subjects'):
                            intake_info += f": {req['subjects']}"
                        if req.get('min_score'):
                            intake_info += f", Min Score: {req['min_score']}"
                
                context_parts.append(intake_info)
            context_parts.append("=== END MATCHED PROGRAMS ===")
        
        return "\n".join(context_parts)
    
    def generate_response(self, user_message: str, conversation_history: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        Generate response for partner query.
        Uses database as primary source, Tavily only when necessary.
        """
        print(f"\n{'='*80}")
        print(f"DEBUG: PartnerAgent.generate_response() called")
        print(f"DEBUG: User message: {user_message}")
        print(f"DEBUG: Conversation history length: {len(conversation_history)}")
        print(f"{'='*80}\n")
        
        current_date = date.today()
        print(f"DEBUG: Current date: {current_date}")
        
        # Extract query state from conversation
        print(f"DEBUG: Extracting partner query state...")
        state = self.extract_partner_query_state(conversation_history)
        print(f"DEBUG: Extracted state: {state.to_dict()}")
        
        # Build database context
        print(f"DEBUG: Building database context...")
        db_context = self._build_database_context(state, current_date)
        print(f"DEBUG: Database context length: {len(db_context)} characters")
        
        # Build system prompt with state summary
        state_summary = self._state_to_summary_string(state)
        system_prompt = f"""{self.PARTNER_SYSTEM_PROMPT}

CURRENT CONVERSATION STATE:
{state_summary}

DATABASE CONTEXT:
{db_context}

IMPORTANT INSTRUCTIONS:
- Use the DATABASE CONTEXT above to answer questions
- Use CURRENT DATE ({current_date.isoformat()}) from DATABASE CONTEXT as the single source of truth for deadline checks
- Only suggest programs with upcoming deadlines (application_deadline > {current_date.isoformat()})
- CRITICAL: If user asks for "March language program", ONLY show programs with intake_term = "March"
- CRITICAL: If user asks for "September language program", ONLY show programs with intake_term = "September"
- DO NOT mix March and September intakes - they are different semesters
- For language programs: DO NOT ask about teaching medium (English/Chinese) - language programs are always taught in English
- For language programs: Focus on duration (one semester vs one year) and intake term
- If multiple deadlines exist (scholarship vs self-paid), show both explicitly
- Use accommodation_fee_period and medical_insurance_fee_period exactly - do NOT assume "per year"
- If a field is missing in DATABASE CONTEXT, say "not provided in our database" - do NOT infer
- If uncertain about a match, present 2-3 options and ask user to confirm
- Always follow the RESPONSE FORMAT structure (Best match → Deadlines → Eligibility → Cost → Scholarships → Next question)
- Be concise and build conversation gradually
- If list is long (>10), show top 10 and mention there are more
- Calculate costs accurately from database
- Provide exact document requirements from database
- Only use Tavily for questions like "Is China student dorm friendly for Muslim?" or latest policy updates"""

        # Build messages for LLM
        messages = [
            {"role": "system", "content": system_prompt}
        ]
        
        # Add conversation history (last 8 messages = 4 exchanges)
        for msg in conversation_history[-8:]:
            messages.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", "")
            })
        
        # Add current user message
        messages.append({"role": "user", "content": user_message})
        
        # Check if Tavily is needed (DB-first policy: only for specific cases)
        use_tavily = False
        tavily_context = ""
        tavily_sources = []
        
        # Only use web search for specific topics not in DB
        user_message_lower = user_message.lower()
        web_search_triggers = [
            "visa", "x1", "x2", "work", "part-time", "employment",
            "halal", "muslim", "dorm friendly", "campus lifestyle", "climate", "safety",
            "csca policy", "latest policy", "policy update", "current visa requirement",
            "ranking"  # Only if not in DB
        ]
        
        # Check if question is about DB content first
        db_question_keywords = [
            "university", "major", "program", "fee", "tuition", "accommodation", "deadline",
            "scholarship", "document", "required", "exam", "hsk", "ielts", "csca", "eligibility"
        ]
        is_db_question = any(keyword in user_message_lower for keyword in db_question_keywords)
        
        # Only use web if: (1) not a DB question, OR (2) specific web-only topics
        should_use_web = any(trigger in user_message_lower for trigger in web_search_triggers) and not is_db_question
        
        if should_use_web:
            try:
                print(f"DEBUG: Using Tavily web search for: {user_message}")
                tavily_results = self.tavily_service.search(user_message, max_results=3)
                if tavily_results:
                    tavily_context = "\n".join([r.get("content", "") for r in tavily_results[:3]])
                    tavily_sources = [r.get("url", "") for r in tavily_results[:3] if r.get("url")]
                    use_tavily = True
                    messages.append({
                        "role": "system",
                        "content": f"WEB SEARCH RESULTS (use only if database doesn't have answer; cite sources):\n{tavily_context}\n\nSources: {', '.join(tavily_sources) if tavily_sources else 'No sources available'}"
                    })
            except Exception as e:
                print(f"Error in Tavily search: {e}")
        
        # Generate response
        try:
            print(f"DEBUG: Calling OpenAI chat_completion...")
            print(f"DEBUG: Messages count: {len(messages)}")
            print(f"DEBUG: System prompt length: {len(messages[0]['content'])} characters")
            print(f"DEBUG: Use Tavily: {use_tavily}")
            
            response = self.openai_service.chat_completion(
                messages=messages,
                temperature=0.7
            )
            
            assistant_message = response.choices[0].message.content.strip()
            print(f"DEBUG: Generated response length: {len(assistant_message)} characters")
            print(f"DEBUG: Response preview: {assistant_message[:200]}...")
            
            return {
                "response": assistant_message,
                "used_db": True,
                "used_tavily": use_tavily,
                "sources": tavily_sources if use_tavily else []
            }
        except Exception as e:
            import traceback
            print(f"ERROR: Error generating response: {e}")
            print(f"ERROR: Traceback:")
            traceback.print_exc()
            return {
                "response": f"I apologize, but I encountered an error processing your request: {str(e)}. Please try again.",
                "used_db": False,
                "used_tavily": False,
                "sources": []
            }
    
    def _state_to_summary_string(self, state: PartnerQueryState) -> str:
        """Convert PartnerQueryState to summary string"""
        summary_lines = []
        
        if state.degree_level:
            summary_lines.append(f"Degree Level: {state.degree_level}")
        if state.major:
            summary_lines.append(f"Major: {state.major}")
        if state.university:
            summary_lines.append(f"University: {state.university}")
        if state.city:
            summary_lines.append(f"City: {state.city}")
        if state.intake_term:
            intake_str = f"Intake: {state.intake_term}"
            if state.intake_year:
                intake_str += f" {state.intake_year}"
            summary_lines.append(intake_str)
        if state.teaching_language:
            summary_lines.append(f"Teaching Language: {state.teaching_language}")
        if state.scholarship_type:
            summary_lines.append(f"Scholarship Type: {state.scholarship_type}")
        if state.has_ielts is not None:
            summary_lines.append(f"Has IELTS: {state.has_ielts}")
        if state.has_hsk is not None:
            summary_lines.append(f"Has HSK: {state.has_hsk}")
        if state.has_csca is not None:
            summary_lines.append(f"Has CSCA: {state.has_csca}")
        
        if not summary_lines:
            return "No specific preferences mentioned yet."
        
        return "\n".join(summary_lines)

