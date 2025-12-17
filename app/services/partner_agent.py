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
from app.models import University, Major, ProgramIntake, ProgramDocument, ProgramIntakeScholarship, Scholarship, ProgramExamRequirement, IntakeTerm
from datetime import datetime, date
import time
import json
import re


@dataclass
class PartnerQueryState:
    """
    Structured state extracted from conversation history (last 16 messages).
    This state is NOT saved to database - computed fresh each time.
    """
    degree_level: Optional[str] = None  # "Bachelor", "Master", "PhD", "Language", "Non-degree", etc.
    major_query: Optional[str] = None   # Free text user intent for major/subject (e.g., "mechanical engineering", "painting")
    university: Optional[str] = None     # University name
    city: Optional[str] = None
    province: Optional[str] = None
    intake_term: Optional[str] = None   # March / September
    intake_year: Optional[int] = None
    teaching_language: Optional[str] = None  # English / Chinese (only set if user explicitly stated)
    scholarship_type: Optional[str] = None   # Type-A, Type-B, Partial, etc.
    has_ielts: Optional[bool] = None
    has_hsk: Optional[bool] = None
    has_csca: Optional[bool] = None
    ielts_score: Optional[float] = None
    hsk_score: Optional[int] = None
    csca_score: Optional[float] = None
    duration_preference: Optional[str] = None  # "one_semester" | "half_year" | "one_year" | "two_semester" | None
    duration_years_target: Optional[float] = None  # Derived: one_semester/half_year -> 0.5, one_year -> 1.0, two_semester -> 1.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for LLM context"""
        return {
            "degree_level": self.degree_level,
            "major_query": self.major_query,
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
            "csca_score": self.csca_score,
            "duration_preference": self.duration_preference,
            "duration_years_target": self.duration_years_target
        }


@dataclass
class PaginationState:
    """State for list query pagination"""
    results: List[Dict[str, Any]]
    offset: int
    total: int
    page_size: int
    intent: str
    timestamp: float
    last_displayed: Optional[List[Dict[str, Any]]] = None  # Last displayed intakes for follow-up questions


class PartnerAgent:
    """Partner agent for answering questions about MalishaEdu universities and programs"""
    
    # List query caps to prevent huge context
    MAX_LIST_UNIVERSITIES = 12
    MAX_LIST_INTAKES = 24
    
    PARTNER_SYSTEM_PROMPT = """You are the MalishaEdu Partner Agent, helping partners answer questions about Chinese universities and programs offered by MalishaEdu.

CRITICAL: Be CONCISE and CONVERSATIONAL. Build the conversation gradually. Don't overwhelm with too much information at once.

SYSTEM CORE (CRITICAL RULES):
- Use only DATABASE CONTEXT; never use general knowledge for programs/fees/deadlines
- **CRITICAL ANTI-HALLUCINATION: ONLY mention universities and programs that are EXPLICITLY listed in the DATABASE CONTEXT. DO NOT invent, assume, or suggest any university or program that is NOT in the DATABASE CONTEXT. If DATABASE CONTEXT shows no matches, you MUST say "I don't have any matching programs in our database" - DO NOT make up universities or programs.**
- Treat CURRENT DATE in DATABASE CONTEXT as the single source of truth for deadline checks and days remaining
- Never assume fee periods; always use *_fee_period. If missing, say "period not specified"
- If multiple deadlines exist (scholarship vs self-paid), show both clearly
- If info is missing in DB, say "not provided" - do NOT infer or hallucinate
- If uncertain match, offer top 2-3 options and ask user to pick (do not guess)
- Use the intent-specific template; be concise.
- When intent is fees_only, do NOT include unrelated sections.
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

RESPONSE TEMPLATES BY INTENT (use minimal necessary info):
- fees_only: Program line (university + major + degree + intake + language), Deadlines (application + scholarship if present), Fees (tuition, accommodation with period, insurance, application, medical, visa extension, bank if present). No eligibility/exams/documents/scholarship list. No Year 1 total unless user explicitly asks for total/overall/year 1.
- documents_only: Program line + all required documents with rules. Omit fees unless asked.
- scholarship_only: Program line + scholarships (what they cover) + scholarship deadlines. Omit fees unless asked.
- eligibility_only: Program line + eligibility only (age, min score, language tests, CSCA/exams, interview/written). Omit fees.
- general: Be concise, structured, and DB-first; include only relevant sections.

MISSING INFORMATION HANDLING:
- If a field is not present in DATABASE CONTEXT, say "not provided" - do NOT infer or guess
- Do NOT make up values for fees, deadlines, or requirements that are not in the database
- If uncertain about a match, present 2-3 options and ask the user to confirm

FUZZY MATCHING BEHAVIOR:
- If confidence is low OR there are multiple close matches, present 2-3 options and ask the user to pick
- Do NOT assume which university/major the user meant if there are similar options
- Example: "I found a few options that might match. Did you mean [Option 1], [Option 2], or [Option 3]? Please let me know which one."

Style Guidelines:
- CONCISE: Keep responses brief
- CONVERSATIONAL: Talk naturally, not like a textbook
- NO REPETITION: Never repeat the same information
- PROGRESSIVE: Start with basics, add details when asked
- FRIENDLY: Warm and helpful
- STRUCTURED: Use bullet points for lists (3+ items)
- PHRASING: Prefer "MalishaEdu offers" instead of "I found"; avoid saying "in our database"
- DEFAULTS: If teaching language is not specified, do NOT filter by language - show both English and Chinese programs. If degree level is missing, ask for it before giving program details (Language/Non-degree, Bachelor, Master, PhD).
- Use the intent-based template; when intent is fees_only, do not include unrelated sections.
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
        
        # Conversation memory for follow-up questions
        self.last_selected_university_id: Optional[int] = None
        self.last_selected_major_id: Optional[int] = None
        self.last_selected_program_intake_id: Optional[int] = None
        
        # Multi-track info for responses with multiple teaching languages
        self._multi_track_info: Optional[Dict[str, Any]] = None
        
        # List pagination cache: keyed by (partner_id, conversation_id) -> PaginationState
        self._pagination_cache: Dict[Tuple[Optional[int], str], PaginationState] = {}
        
        # Stable session fallback IDs per partner (for pagination when no conversation_id)
        self._session_fallback_id_by_partner: Dict[Optional[int], str] = {}
        
        # Last pagination key by partner (for stable fallback)
        self._last_pagination_key_by_partner: Dict[Optional[int], Tuple[Optional[int], str]] = {}
    
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
    
    def _normalize_keywords(self, raw_keywords: Any) -> List[str]:
        """Normalize keywords field from DB (list, JSON string, or comma-separated string)."""
        if isinstance(raw_keywords, list):
            return [str(k).strip() for k in raw_keywords if str(k).strip()]
        if isinstance(raw_keywords, str):
            raw = raw_keywords.strip()
            if not raw:
                return []
            # Try JSON first
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return [str(k).strip() for k in parsed if str(k).strip()]
            except Exception:
                pass
            # Fallback: comma-separated
            return [k.strip() for k in raw.split(",") if k.strip()]
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
                    "keywords": self._normalize_keywords(major.keywords),
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
        Extract and consolidate PartnerQueryState from conversation history (last 16 messages).
        Uses LLM to infer state.
        """
        if not conversation_history:
            return PartnerQueryState()
        
        # Build conversation text from last 16 messages
        conversation_text = ""
        for msg in conversation_history[-16:]:
            role = msg.get('role', '')
            content = msg.get('content', '')
            conversation_text += f"{role}: {content}\n"
        
        # Get list of universities for reference (majors no longer needed in prompt)
        uni_list = [uni["name"] for uni in self.all_universities[:50]]
        uni_list_str = ", ".join(uni_list)
        if len(self.all_universities) > 50:
            uni_list_str += f"\n... and {len(self.all_universities) - 50} more universities"
        
        # Use LLM to extract state
        extraction_prompt = f"""You are a state extractor for partner queries. Given the conversation, output a JSON object with these fields:
- degree_level: "Bachelor", "Master", "PhD", "Language", "Non-degree", "Associate", "Vocational College", "Junior high", "Senior high", or null
- major_query: Extract the user's requested subject/major exactly as written (e.g., "mechanical engineering", "painting", "materials science"). Use lowercase normalization, but preserve the original wording. Do NOT force it to match any database list. If user mentions a major/subject, extract it as-is. If not mentioned, use null.
- university: university name from the database list below, or the closest match, or null
- city: city name or null
- province: province name or null
- intake_term: "March", "September", or null
- intake_year: year number (e.g., 2026) or null
- teaching_language: "English", "Chinese", or null (ONLY set if user explicitly stated a preference; do NOT default to "English")
- scholarship_type: "Type-A", "Type-B", "Type-C", "Type-D", "Partial-Low", "Partial-Mid", "Partial-High", "Self-Paid", "None", or null
- has_ielts: true/false/null
- has_hsk: true/false/null
- has_csca: true/false/null
- ielts_score: score number or null
- hsk_score: score number or null
- csca_score: score number or null
- duration_preference: "one_semester" (for "1 semester", "one semester", "half year", "6 months"), "two_semester" (for "2 semesters"), "one_year" (for "1 year", "one year"), or null

AVAILABLE UNIVERSITIES IN DATABASE (for matching):
{uni_list_str}

CRITICAL RULES:
1. Use ONLY information explicitly stated by the user. Do NOT guess.
2. LATEST MESSAGE WINS: If user changes their mind, use the LATEST statement.
3. For major_query: Extract the subject/major the user wants to study exactly as they wrote it (e.g., "mechanical engineering", "materials", "finance", "painting"). Do NOT try to match it to a database list. Just extract what the user said.
4. **IMPORTANT: Do NOT extract fee-related words as major_query. If user says "what's the tuition", "application fee", "cost", "price", "tuition fee", etc., set major_query to null unless a real major/subject is explicitly mentioned (e.g., "tuition for Business Administration" → major_query="business administration", but "what's the tuition" → major_query=null).**
5. For university matching: Look through AVAILABLE UNIVERSITIES to find the closest match.
6. Use fuzzy matching for degree_level: "masters" = "Master", "phd" = "PhD", "doctoral" = "PhD", "bachelor" = "Bachelor"
7. For teaching_language: ONLY set if user explicitly said "English" or "Chinese". Do NOT default to "English".

Conversation:
{conversation_text}

Output ONLY valid JSON, no other text:"""

        try:
            print(f"DEBUG: PartnerAgent - Extracting state from conversation (last 16 messages)")
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
            
            # Normalize major_query to lowercase if present
            major_query = extracted.get('major_query') or extracted.get('major')  # Backward compatibility
            if major_query:
                major_query = major_query.lower().strip()
                # Remove fee-related keywords from major_query (they shouldn't be treated as major names)
                fee_keywords = ["tuition", "fee", "fees", "cost", "price", "application fee", "tuition fee", "application", "scholarship"]
                major_words = major_query.split()
                # If major_query only contains fee keywords or is just fee keywords, set to None
                if all(word in fee_keywords for word in major_words):
                    major_query = None
                    print(f"DEBUG: Cleaned major_query - removed fee keywords, set to None")
                # If major_query starts with fee keywords, remove them
                elif major_words[0] in fee_keywords:
                    # Check if there's a real major after fee keywords (e.g., "tuition for business" → "business")
                    if len(major_words) > 1:
                        # Look for words after "for", "of", "at" that might be the major
                        for i, word in enumerate(major_words):
                            if word in ["for", "of", "at"] and i + 1 < len(major_words):
                                major_query = " ".join(major_words[i+1:])
                                print(f"DEBUG: Cleaned major_query - extracted '{major_query}' after fee keywords")
                                break
                        else:
                            # No "for/of/at" found, set to None
                            major_query = None
                            print(f"DEBUG: Cleaned major_query - no major found after fee keywords, set to None")
                    else:
                        major_query = None
                        print(f"DEBUG: Cleaned major_query - only fee keyword, set to None")
            
            # Derive duration_years_target from duration_preference
            duration_pref = extracted.get('duration_preference')
            duration_years = None
            if duration_pref == "one_semester" or duration_pref == "half_year":
                duration_years = 0.5
            elif duration_pref == "one_year" or duration_pref == "two_semester":
                duration_years = 1.0
            
            state = PartnerQueryState(
                degree_level=extracted.get('degree_level'),
                major_query=major_query,
                university=extracted.get('university'),
                city=extracted.get('city'),
                province=extracted.get('province'),
                intake_term=extracted.get('intake_term'),
                intake_year=extracted.get('intake_year'),
                teaching_language=extracted.get('teaching_language'),  # Only set if explicitly stated
                scholarship_type=extracted.get('scholarship_type'),
                has_ielts=extracted.get('has_ielts'),
                has_hsk=extracted.get('has_hsk'),
                has_csca=extracted.get('has_csca'),
                ielts_score=extracted.get('ielts_score'),
                hsk_score=extracted.get('hsk_score'),
                csca_score=extracted.get('csca_score'),
                duration_preference=duration_pref,
                duration_years_target=duration_years
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
    
    def _find_university_ids_by_location(self, city: Optional[str], province: Optional[str]) -> List[int]:
        """
        Find university IDs by location (city and/or province).
        Searches self.all_universities and matches by city and/or province (case-insensitive).
        Returns list of matching university IDs.
        """
        if not city and not province:
            return []
        
        matching_ids = []
        city_normalized = city.lower().strip() if city else None
        province_normalized = province.lower().strip() if province else None
        
        for uni in self.all_universities:
            uni_city = (uni.get("city") or "").lower().strip() if uni.get("city") else None
            uni_province = (uni.get("province") or "").lower().strip() if uni.get("province") else None
            
            # Match city if provided
            city_match = False
            if city_normalized:
                if uni_city == city_normalized:
                    city_match = True
                # Allow common whitespace variations
                elif uni_city and city_normalized.replace(" ", "") == uni_city.replace(" ", ""):
                    city_match = True
            
            # Match province if provided
            province_match = False
            if province_normalized:
                if uni_province == province_normalized:
                    province_match = True
                # Allow common whitespace variations
                elif uni_province and province_normalized.replace(" ", "") == uni_province.replace(" ", ""):
                    province_match = True
            
            # Add if matches city (if city provided) and/or province (if province provided)
            if city_normalized and province_normalized:
                # Both provided - match both
                if city_match and province_match:
                    matching_ids.append(uni["id"])
            elif city_normalized:
                # Only city provided
                if city_match:
                    matching_ids.append(uni["id"])
            elif province_normalized:
                # Only province provided
                if province_match:
                    matching_ids.append(uni["id"])
        
        print(f"DEBUG: Location filter - city={city}, province={province}, matched_uni_ids={matching_ids}")
        return matching_ids
    
    def _infer_duration_from_major_name(self, major_name: str) -> Optional[float]:
        """
        Infer duration in years from major name using robust regex/keywords.
        Returns 0.5 for semester/half year/6 month, 1.0 for one year/academic year/year, or None if unclear.
        """
        if not major_name:
            return None
        
        major_lower = major_name.lower()
        
        # Check for semester/half year patterns
        if re.search(r'\b(1\s+semester|one\s+semester|half\s+year|6\s+month|six\s+month|0\.5|0\s*\.\s*5)\b', major_lower):
            return 0.5
        
        # Check for one year patterns
        if re.search(r'\b(1\s+year|one\s+year|academic\s+year|year\s+program)\b', major_lower):
            return 1.0
        
        return None
    
    def _fuzzy_match_major(self, user_input: str, university_id: Optional[int] = None, degree_level: Optional[str] = None, top_k: int = 20) -> Tuple[bool, Optional[Dict[str, Any]], List[Tuple[Dict[str, Any], float]]]:
        """
        Fuzzy match user input to major from pre-loaded array.
        Collects all matching majors by (1) exact name, (2) keyword exact/substring, (3) fuzzy score.
        Returns (matched: bool, best_match: Optional[Dict], all_matches: List[Tuple[Dict, score]])
        Never returns early - collects all candidates, dedupes, and returns top_k.
        """
        user_input_clean = re.sub(r'[^\w\s&]', '', user_input.lower())
        
        all_majors = self.all_majors
        if university_id:
            all_majors = [m for m in all_majors if m["university_id"] == university_id]
        if degree_level:
            all_majors = [m for m in all_majors if m.get("degree_level") and degree_level.lower() in str(m["degree_level"]).lower()]
        
        # Collect all matches with scores - no early returns
        matches = []  # List of (major, score, match_type)
        
        for major in all_majors:
            major_name_clean = re.sub(r'[^\w\s&]', '', major["name"].lower())
            best_score_for_major = 0.0
            match_type = None
            
            # 1. Exact name match (highest priority)
            if user_input_clean == major_name_clean:
                best_score_for_major = 1.0
                match_type = "exact_name"
            else:
                # 2. Substring match in name
                if user_input_clean in major_name_clean or major_name_clean in user_input_clean:
                    match_ratio = SequenceMatcher(None, user_input_clean, major_name_clean).ratio()
                    if match_ratio > best_score_for_major:
                        best_score_for_major = match_ratio
                        match_type = "name_substring"
                
                # 3. Check keywords (exact and substring)
                keywords = self._normalize_keywords(major.get("keywords", []))
                if keywords:
                    for keyword in keywords:
                        keyword_clean = re.sub(r'[^\w\s&]', '', str(keyword).lower())
                        if not keyword_clean:
                            continue
                        # Exact keyword match
                        if user_input_clean == keyword_clean:
                            if 0.95 > best_score_for_major:  # Slightly lower than exact name
                                best_score_for_major = 0.95
                                match_type = "keyword_exact"
                        # Keyword substring match
                        elif user_input_clean in keyword_clean or keyword_clean in user_input_clean:
                            match_ratio = SequenceMatcher(None, user_input_clean, keyword_clean).ratio()
                            if match_ratio > best_score_for_major:
                                best_score_for_major = match_ratio * 0.9  # Slightly penalize keyword matches
                                match_type = "keyword_substring"
                
                # 4. Word overlap (fuzzy)
                # Allow single-word matches for queries like "materials", "finance", "painting"
                # but only if keyword match didn't already score well
                user_words = set(user_input_clean.split())
                major_words = set(major_name_clean.split())
                common_words = user_words & major_words
                if common_words:
                    # For 2+ common words: use existing logic
                    if len(common_words) >= 2:
                        match_ratio = len(common_words) / max(len(user_words), len(major_words))
                        if match_ratio >= 0.4 and match_ratio > best_score_for_major:
                            best_score_for_major = match_ratio * 0.7  # Penalize word overlap matches
                            match_type = "word_overlap"
                    # For single-word matches: only use if keyword match didn't score well (score < 0.6)
                    elif len(common_words) == 1 and best_score_for_major < 0.6:
                        # Single word overlap gets lower score
                        match_ratio = 0.5  # Fixed lower score for single-word matches
                        if match_ratio > best_score_for_major:
                            best_score_for_major = match_ratio * 0.6  # Further penalize single-word matches
                            match_type = "word_overlap_single"
            
            # Add match if score is above threshold
            if best_score_for_major >= 0.4:
                matches.append((major, best_score_for_major, match_type))
        
        if not matches:
            return False, None, []
        
        # Remove duplicates (same major_id with different match scores - keep highest)
        seen_majors = {}
        for major, score, match_type in matches:
            major_id = major.get("id")
            if major_id not in seen_majors or seen_majors[major_id][1] < score:
                seen_majors[major_id] = (major, score, match_type)
        
        # Convert to list of (major, score) tuples and sort by score
        deduped_matches = [(m, s) for m, s, _ in seen_majors.values()]
        deduped_matches.sort(key=lambda x: x[1], reverse=True)
        
        # Return top_k candidates
        top_matches = deduped_matches[:top_k]
        best_match = top_matches[0] if top_matches else None
        
        # Determine if we have high confidence (best match >= 0.8)
        has_high_confidence = best_match and best_match[1] >= 0.8
        
        return has_high_confidence, best_match[0] if best_match else None, top_matches
    
    def _get_upcoming_intakes(
        self,
        current_date: date,
        degree_level: Optional[str] = None,
        university_id: Optional[int] = None,
        major_ids: Optional[List[int]] = None,
        intake_term: Optional[str] = None,
        intake_year: Optional[int] = None,
        teaching_language: Optional[str] = None,
        university_ids: Optional[List[int]] = None
    ) -> List[Dict[str, Any]]:
        """
        Get program intakes with upcoming deadlines (deadline > current_date) using filtered SQL to reduce load.
        Option A: Accept university_ids parameter for location-based filtering.
        """
        try:
            print(f"DEBUG: Loading filtered upcoming intakes (deadline > {current_date})...")
            print(f"DEBUG: Query parameters: university_id={university_id}, university_ids={university_ids}, major_ids={major_ids}, degree_level={degree_level}, intake_term={intake_term}, intake_year={intake_year}, teaching_language={teaching_language}")
            
            # First, check if there are any intakes for the matched major(s) without date filter (for debugging)
            if major_ids:
                base_query = self.db.query(ProgramIntake).join(Major).join(University).filter(
                    University.is_partner == True,
                    ProgramIntake.major_id.in_(major_ids)
                )
                total_for_major = base_query.count()
                print(f"DEBUG: Total intakes for matched major_ids {major_ids} (all dates): {total_for_major}")
                if total_for_major > 0:
                    # Check how many have upcoming deadlines
                    upcoming_for_major = base_query.filter(ProgramIntake.application_deadline > current_date).count()
                    print(f"DEBUG: Upcoming intakes for matched major_ids {major_ids} (deadline > {current_date}): {upcoming_for_major}")
                    # Check intake_term filter impact
                    if intake_term:
                        term_count = base_query.filter(
                            ProgramIntake.application_deadline > current_date,
                            ProgramIntake.intake_term == intake_term
                        ).count()
                        print(f"DEBUG: Upcoming intakes with intake_term={intake_term}: {term_count}")
            
            query = self.db.query(ProgramIntake).join(Major).join(University).filter(
                University.is_partner == True,
                ProgramIntake.application_deadline > current_date
            ).order_by(ProgramIntake.application_deadline.asc())
            
            filters_applied = ["deadline > current_date", "is_partner = True"]
            if university_id:
                query = query.filter(ProgramIntake.university_id == university_id)
                filters_applied.append(f"university_id = {university_id}")
            elif university_ids:
                # Option A: Filter by list of university IDs (for location-based filtering)
                query = query.filter(ProgramIntake.university_id.in_(university_ids))
                filters_applied.append(f"university_id IN {university_ids}")
            if major_ids:
                query = query.filter(ProgramIntake.major_id.in_(major_ids))
                filters_applied.append(f"major_id IN {major_ids}")
            if degree_level:
                query = query.filter(Major.degree_level.ilike(f"%{degree_level}%"))
                filters_applied.append(f"degree_level ILIKE '%{degree_level}%'")
            if intake_term:
                query = query.filter(ProgramIntake.intake_term == intake_term)
                filters_applied.append(f"intake_term = {intake_term}")
            if intake_year:
                query = query.filter(ProgramIntake.intake_year == intake_year)
                filters_applied.append(f"intake_year = {intake_year}")
            if teaching_language:
                # Respect ProgramIntake.teaching_language override: match if ProgramIntake.teaching_language ILIKE requested
                # OR (ProgramIntake.teaching_language is NULL AND Major.teaching_language ILIKE requested)
                from sqlalchemy import or_, and_
                query = query.filter(
                    or_(
                        ProgramIntake.teaching_language.ilike(f"%{teaching_language}%"),
                        and_(ProgramIntake.teaching_language.is_(None), Major.teaching_language.ilike(f"%{teaching_language}%"))
                    )
                )
                filters_applied.append(f"Teaching language filter (ProgramIntake override OR Major default)")
                print(f"DEBUG: Filtering by teaching_language with ProgramIntake override support")
            
            print(f"DEBUG: Applied filters: {', '.join(filters_applied)}")
            intakes = query.all()
            print(f"DEBUG: Found {len(intakes)} upcoming intakes after filtering")
            
            result = []
            for intake in intakes:
                result.append({
                    "id": intake.id,
                    "university_id": intake.university_id,
                    "university_name": intake.university.name,
                    "major_id": intake.major_id,
                    "major_name": intake.major.name,
                    "degree_level": intake.major.degree_level if intake.major and intake.major.degree_level else None,
                    "intake_term": intake.intake_term.value if intake.intake_term else None,
                    "intake_year": intake.intake_year,
                    "application_deadline": intake.application_deadline.isoformat() if intake.application_deadline else None,
                    "teaching_language": intake.teaching_language,  # ProgramIntake override
                    "major_teaching_language": intake.major.teaching_language if intake.major else None,  # Major default
                    "effective_teaching_language": intake.teaching_language or (intake.major.teaching_language if intake.major else None),  # Effective: ProgramIntake if present, else Major
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
                    "written_test_required": intake.written_test_required,
                    "notes": intake.notes,
                    "scholarship_info": intake.scholarship_info,
                    "documents_required": intake.documents_required
                })
            return result
        except Exception as e:
            import traceback
            print(f"ERROR: Error loading upcoming intakes: {e}")
            traceback.print_exc()
            return []
    
    def _get_latest_intakes_any_deadline(
        self,
        degree_level: Optional[str] = None,
        university_id: Optional[int] = None,
        major_ids: Optional[List[int]] = None,
        intake_term: Optional[str] = None,
        intake_year: Optional[int] = None,
        teaching_language: Optional[str] = None,
        limit_per_major: int = 3,
        university_ids: Optional[List[int]] = None
    ) -> List[Dict[str, Any]]:
        """
        Get latest program intakes WITHOUT deadline filter (for fee queries when no upcoming deadlines).
        Returns latest 1-3 intakes per major, sorted by application_deadline desc nullslast,
        or by intake_year desc, then intake_term.
        """
        try:
            print(f"DEBUG: Loading latest intakes (any deadline) - university_id={university_id}, university_ids={university_ids}, major_ids={major_ids}, degree_level={degree_level}, intake_term={intake_term}, teaching_language={teaching_language}")
            
            # Query without deadline filter
            query = self.db.query(ProgramIntake).join(Major).join(University).filter(
                University.is_partner == True
            )
            
            if university_id:
                query = query.filter(ProgramIntake.university_id == university_id)
            elif university_ids:
                # Option A: Filter by list of university IDs (for location-based filtering)
                query = query.filter(ProgramIntake.university_id.in_(university_ids))
            if major_ids:
                query = query.filter(ProgramIntake.major_id.in_(major_ids))
            if degree_level:
                query = query.filter(Major.degree_level.ilike(f"%{degree_level}%"))
            if intake_term:
                query = query.filter(ProgramIntake.intake_term == intake_term)
            if intake_year:
                query = query.filter(ProgramIntake.intake_year == intake_year)
            if teaching_language:
                # Respect ProgramIntake.teaching_language override
                from sqlalchemy import or_, and_
                query = query.filter(
                    or_(
                        ProgramIntake.teaching_language.ilike(f"%{teaching_language}%"),
                        and_(ProgramIntake.teaching_language.is_(None), Major.teaching_language.ilike(f"%{teaching_language}%"))
                    )
                )
                print(f"DEBUG: Filtering by teaching_language with ProgramIntake override support")
            
            # Sort by deadline desc (nulls last), then by intake_year desc, then by intake_term
            from sqlalchemy import desc, nullslast
            query = query.order_by(
                nullslast(desc(ProgramIntake.application_deadline)),
                desc(ProgramIntake.intake_year),
                desc(ProgramIntake.intake_term)
            )
            
            intakes = query.all()
            print(f"DEBUG: Found {len(intakes)} total intakes (any deadline) after filtering")
            
            # Group by major_id and take latest limit_per_major per major
            from collections import defaultdict
            by_major = defaultdict(list)
            for intake in intakes:
                by_major[intake.major_id].append(intake)
            
            result = []
            for major_id, major_intakes in by_major.items():
                # Take latest limit_per_major intakes for this major
                latest_for_major = major_intakes[:limit_per_major]
                for intake in latest_for_major:
                    result.append({
                        "id": intake.id,
                        "university_id": intake.university_id,
                        "university_name": intake.university.name,
                        "major_id": intake.major_id,
                        "major_name": intake.major.name,
                        "degree_level": intake.major.degree_level if intake.major and intake.major.degree_level else None,
                        "intake_term": intake.intake_term.value if intake.intake_term else None,
                        "intake_year": intake.intake_year,
                        "application_deadline": intake.application_deadline.isoformat() if intake.application_deadline else None,
                        "teaching_language": intake.teaching_language,  # ProgramIntake override
                        "major_teaching_language": intake.major.teaching_language if intake.major else None,  # Major default
                        "effective_teaching_language": intake.teaching_language or (intake.major.teaching_language if intake.major else None),  # Effective: ProgramIntake if present, else Major
                        "tuition_per_year": intake.tuition_per_year,
                        "tuition_per_semester": intake.tuition_per_semester,
                        "application_fee": intake.application_fee,
                        "accommodation_fee": intake.accommodation_fee,
                        "medical_insurance_fee": intake.medical_insurance_fee,
                        "arrival_medical_checkup_fee": intake.arrival_medical_checkup_fee,
                        "visa_extension_fee": intake.visa_extension_fee,
                        "scholarship_available": intake.scholarship_available,
                        "currency": intake.currency or "CNY",
                        "accommodation_fee_period": intake.accommodation_fee_period,
                        "medical_insurance_fee_period": intake.medical_insurance_fee_period,
                        "arrival_medical_checkup_is_one_time": intake.arrival_medical_checkup_is_one_time,
                        "hsk_required": intake.hsk_required,
                        "hsk_level": intake.hsk_level,
                        "hsk_min_score": intake.hsk_min_score,
                        "english_test_required": intake.english_test_required,
                        "english_test_note": intake.english_test_note,
                        "bank_statement_required": intake.bank_statement_required,
                        "bank_statement_amount": intake.bank_statement_amount,
                        "bank_statement_currency": intake.bank_statement_currency,
                        "bank_statement_note": intake.bank_statement_note,
                        "age_min": intake.age_min,
                        "age_max": intake.age_max,
                        "min_average_score": intake.min_average_score,
                        "interview_required": intake.interview_required,
                        "written_test_required": intake.written_test_required,
                        "notes": intake.notes,
                        "scholarship_info": intake.scholarship_info,
                        "documents_required": intake.documents_required
                    })
            
            print(f"DEBUG: Returning {len(result)} latest intakes (up to {limit_per_major} per major)")
            return result
        except Exception as e:
            import traceback
            print(f"ERROR: Error loading latest intakes: {e}")
            traceback.print_exc()
            return []
    
    def _get_majors_for_list_query(
        self,
        university_id: Optional[int] = None,
        degree_level: Optional[str] = None,
        teaching_language: Optional[str] = None,
        university_ids: Optional[List[int]] = None
    ) -> List[Dict[str, Any]]:
        """
        Query Majors table directly for list queries.
        Does NOT require application_deadline > current_date.
        Used to check if programs exist regardless of intake availability.
        """
        try:
            print(f"DEBUG: Querying majors for list query - university_id={university_id}, university_ids={university_ids}, degree_level={degree_level}, teaching_language={teaching_language}")
            
            # Query majors from partner universities
            query = self.db.query(Major).join(University).filter(
                University.is_partner == True
            )
            
            if university_id:
                query = query.filter(Major.university_id == university_id)
            elif university_ids:
                # Filter by list of university IDs (for location-based filtering)
                query = query.filter(Major.university_id.in_(university_ids))
            
            if degree_level:
                query = query.filter(Major.degree_level.ilike(f"%{degree_level}%"))
            
            # Note: teaching_language is stored in ProgramIntake, not Major
            # For now, we'll query majors and filter by teaching_language later if needed
            # Or we can join with ProgramIntake to check teaching_language
            
            majors = query.all()
            
            result = []
            for major in majors:
                # If teaching_language is specified, check if any intake for this major has that language
                if teaching_language:
                    # Check if this major has any intake with the specified teaching language
                    intake_check = self.db.query(ProgramIntake).filter(
                        ProgramIntake.major_id == major.id
                    ).filter(
                        ProgramIntake.teaching_language.ilike(f"%{teaching_language}%")
                    ).first()
                    if not intake_check:
                        continue  # Skip this major if it doesn't have intakes with the specified language
                
                result.append({
                    "id": major.id,
                    "name": major.name,
                    "name_cn": major.name_cn,
                    "university_id": major.university_id,
                    "university_name": major.university.name if major.university else None,
                    "degree_level": major.degree_level,
                    "discipline": major.discipline,
                    "category": major.category,
                    "keywords": major.keywords
                })
            
            print(f"DEBUG: Found {len(result)} majors matching criteria")
            return result
        except Exception as e:
            import traceback
            print(f"ERROR: Error loading majors for list query: {e}")
            traceback.print_exc()
            return []
    
    def _get_program_documents_batch(self, intake_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
        """Batch load required documents for multiple program intakes (avoid N+1)."""
        if not intake_ids:
            return {}
        try:
            documents = self.db.query(ProgramDocument).filter(
                ProgramDocument.program_intake_id.in_(intake_ids)
            ).all()
            
            result: Dict[int, List[Dict[str, Any]]] = {}
            for doc in documents:
                result.setdefault(doc.program_intake_id, []).append({
                    "name": doc.name,
                    "is_required": doc.is_required,
                    "rules": doc.rules,
                    "applies_to": doc.applies_to
                })
            return result
        except Exception as e:
            print(f"Error loading program documents: {e}")
            return {}
    
    def _get_program_scholarships_batch(self, intake_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
        """Batch load scholarships for multiple program intakes (avoid N+1)."""
        if not intake_ids:
            return {}
        try:
            intake_scholarships = self.db.query(ProgramIntakeScholarship).filter(
                ProgramIntakeScholarship.program_intake_id.in_(intake_ids)
            ).all()
            
            result: Dict[int, List[Dict[str, Any]]] = {}
            for intake_sch in intake_scholarships:
                scholarship = intake_sch.scholarship
                sch_dict = {
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
                }
                result.setdefault(intake_sch.program_intake_id, []).append(sch_dict)
            return result
        except Exception as e:
            print(f"Error loading program scholarships: {e}")
            return {}
    
    def _get_program_exam_requirements_batch(self, intake_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
        """Batch load exam requirements for program intakes to avoid N+1 queries."""
        if not intake_ids:
            return {}
        try:
            exam_reqs = self.db.query(ProgramExamRequirement).filter(
                ProgramExamRequirement.program_intake_id.in_(intake_ids)
            ).all()
            result: Dict[int, List[Dict[str, Any]]] = {}
            for req in exam_reqs:
                result.setdefault(req.program_intake_id, []).append({
                    "exam_name": req.exam_name,
                    "required": req.required,
                    "subjects": req.subjects,
                    "min_level": req.min_level,
                    "min_score": req.min_score,
                    "exam_language": req.exam_language,
                    "notes": req.notes
                })
            return result
        except Exception as e:
            print(f"Error loading exam requirements: {e}")
            return {}
    
    def _build_database_context(
        self,
        state: PartnerQueryState,
        current_date: date,
        intakes: List[Dict[str, Any]],
        show_catalog: bool = False,
        match_notes: Optional[List[str]] = None,
        include_docs: bool = True,
        include_exams: bool = True,
        include_scholarships: bool = True,
        include_deadlines: bool = True,
        include_eligibility: bool = True,
        include_cost: bool = True,
        is_list_query: bool = False,
        using_latest_intakes: bool = False,
        intent: str = "general"
    ) -> str:
        """
        Build database context based on query state and already-filtered intakes.
        """
        context_parts: List[str] = []
        current_date_str = current_date.isoformat()
        context_parts.append(f"CURRENT DATE: {current_date_str}")
        
        if using_latest_intakes:
            context_parts.append("IMPORTANT: These are the latest recorded intakes (deadlines may not be currently open).")
            if state.intake_term:
                norm_term = self._normalize_intake_term_enum(state.intake_term)
                if norm_term:
                    context_parts.append(f"NOTE: {norm_term.value.title()} intake deadlines are not currently open / not yet available, but latest recorded information (fees/scholarships/documents/eligibility) is shown below.")
            else:
                context_parts.append("NOTE: Intake deadlines are not currently open / not yet available, but latest recorded information is shown below.")
        else:
            context_parts.append("IMPORTANT: Only suggest programs with application_deadline > current_date")
        
        if show_catalog and self.all_universities:
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
        
        if show_catalog and self.all_majors:
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
        
        if match_notes:
            context_parts.extend(match_notes)
        
        # Fix 1: Force detailed mode for single university with <=10 intakes for specific intents
        force_detailed_single_university = False
        if is_list_query and intakes:
            unique_universities = len({i['university_id'] for i in intakes})
            if (unique_universities == 1 and 
                len(intakes) <= 10 and 
                intent in ["fees_only", "documents_only", "eligibility_only", "scholarship_only", "general"]):
                force_detailed_single_university = True
                print(f"DEBUG: Forcing detailed mode for single university ({unique_universities} uni, {len(intakes)} intakes) with intent={intent}")
        
        filtered_intakes = intakes if is_list_query else intakes[:20]
        # If forcing detailed mode, include intake_ids even for list query
        intake_ids = [i['id'] for i in filtered_intakes] if (not is_list_query or force_detailed_single_university) else []
        docs_map = self._get_program_documents_batch(intake_ids) if intake_ids else {}
        scholarships_map = self._get_program_scholarships_batch(intake_ids) if intake_ids else {}
        exams_map = self._get_program_exam_requirements_batch(intake_ids) if intake_ids else {}
        
        if is_list_query and filtered_intakes and not force_detailed_single_university:
            # Compact summary: unique universities with counts and earliest deadline / languages
            uni_map: Dict[int, Dict[str, Any]] = {}
            for intake in filtered_intakes:
                uid = intake['university_id']
                uni_entry = uni_map.setdefault(uid, {
                    "name": intake['university_name'],
                    "count": 0,
                    "earliest_deadline": None,
                    "languages": set()
                })
                uni_entry["count"] += 1
                if intake.get('application_deadline'):
                    dt = datetime.fromisoformat(intake['application_deadline']).date()
                    if uni_entry["earliest_deadline"] is None or dt < uni_entry["earliest_deadline"]:
                        uni_entry["earliest_deadline"] = dt
                if intake.get('teaching_language'):
                    uni_entry["languages"].add(intake['teaching_language'])
            # Sort by earliest deadline then name
            uni_items = list(uni_map.values())
            uni_items.sort(key=lambda x: (x["earliest_deadline"] or date.max, x["name"]))
            top = uni_items[:10]
            more = max(0, len(uni_items) - len(top))
            context_parts.append("=== MATCHED UNIVERSITIES (compact) ===")
            for uni in top:
                lang_str = ", ".join(sorted(uni["languages"])) if uni["languages"] else "not provided"
                deadline_str = uni["earliest_deadline"].isoformat() if uni["earliest_deadline"] else "not provided"
                context_parts.append(f"- {uni['name']} — {uni['count']} program(s), earliest deadline {deadline_str}, languages: {lang_str}")
            if more:
                context_parts.append(f"+ {more} more universities not shown in list context.")
            context_parts.append("=== END MATCHED UNIVERSITIES ===")
        elif filtered_intakes:
            # Add note if using latest intakes (not upcoming deadlines)
            if using_latest_intakes:
                context_parts.append(f"\n=== MATCHED PROGRAM INTAKES (Latest Recorded - Deadlines Not Currently Open) ===")
                context_parts.append("NOTE: Deadlines are not currently open / not available yet in DB. Showing latest saved intake information from database.")
            else:
                context_parts.append(f"\n=== MATCHED PROGRAM INTAKES (Upcoming Deadlines Only) ===")
            
            # Check if multiple tracks exist (English/Chinese) for same university+major+degree
            # Group by university+major+degree to detect multiple tracks
            from collections import defaultdict
            program_groups = defaultdict(list)
            for intake in filtered_intakes:
                key = (intake.get('university_id'), intake.get('major_id'), intake.get('degree_level'))
                program_groups[key].append(intake)
            
            # If multiple tracks exist and user didn't specify language, list them and ask
            multiple_tracks_detected = False
            for key, intakes in program_groups.items():
                if len(intakes) > 1:
                    # Check if they have different teaching languages
                    languages = set()
                    for i in intakes:
                        eff_lang = i.get('effective_teaching_language') or i.get('teaching_language') or i.get('major_teaching_language')
                        if eff_lang:
                            languages.add(eff_lang)
                    if len(languages) > 1 and not state.teaching_language:
                        multiple_tracks_detected = True
                        print(f"DEBUG: Multiple tracks detected: YES - university_id={key[0]}, major_id={key[1]}, languages={languages}")
                        break
            
            if not multiple_tracks_detected:
                print(f"DEBUG: Multiple tracks detected: NO (or user specified teaching_language)")
            
            # If multiple tracks and user didn't specify language, list them
            if multiple_tracks_detected and intent in ["documents_only", "eligibility_only", "fees_only", "scholarship_only"]:
                # Group by university+major and show tracks
                track_groups = defaultdict(lambda: defaultdict(list))
                for intake in filtered_intakes:
                    uni_id = intake.get('university_id')
                    major_id = intake.get('major_id')
                    eff_lang = intake.get('effective_teaching_language') or intake.get('teaching_language') or intake.get('major_teaching_language') or 'N/A'
                    track_groups[(uni_id, major_id)][eff_lang].append(intake)
                
                response_parts = []
                for (uni_id, major_id), lang_dict in track_groups.items():
                    if len(lang_dict) > 1:  # Multiple languages for this program
                        uni_name = next((u["name"] for u in self.all_universities if u["id"] == uni_id), "Unknown University")
                        major_name = next((m["name"] for m in self.all_majors if m["id"] == major_id), "Unknown Major")
                        response_parts.append(f"Multiple tracks available for {uni_name} - {major_name}:")
                        for lang, lang_intakes in lang_dict.items():
                            response_parts.append(f"  - {lang}-taught track ({len(lang_intakes)} intake(s))")
                        response_parts.append("Which teaching language would you like information about?")
                
                if response_parts:
                    return {
                        "response": "\n".join(response_parts),
                        "used_db": True,
                        "used_tavily": False,
                        "sources": []
                    }
            
            for intake in filtered_intakes:
                # Use effective_teaching_language (ProgramIntake override or Major default)
                effective_lang = intake.get('effective_teaching_language') or intake.get('teaching_language') or intake.get('major_teaching_language') or 'N/A'
                intake_info = f"\nProgram: {intake['university_name']} - {intake['major_name']} ({intake.get('degree_level', 'N/A')})"
                intake_info += f"\n  Teaching Language: {effective_lang}"  # Always show teaching language explicitly
                intake_info += f"\n  Intake: {intake.get('intake_term', 'N/A')} {intake.get('intake_year', 'N/A')}"
                if include_deadlines:
                    if intake.get('application_deadline'):
                        deadline_date = datetime.fromisoformat(intake['application_deadline']).date()
                        days_remaining = (deadline_date - current_date).days
                        intake_info += f"\n  Application Deadline: {intake['application_deadline']} ({days_remaining} days remaining from CURRENT DATE: {current_date.isoformat()})"
                    # Earliest scholarship deadline if any
                    scholarships = scholarships_map.get(intake['id'], [])
                    if scholarships:
                        deadlines = [sch.get('deadline') for sch in scholarships if sch.get('deadline')]
                        if deadlines:
                            earliest = sorted(deadlines)[0]
                            intake_info += f"\n  Scholarship Deadline: {earliest}"
                if include_eligibility:
                    # Always show teaching language for eligibility
                    effective_lang_elig = intake.get('effective_teaching_language') or intake.get('teaching_language') or intake.get('major_teaching_language') or 'N/A'
                    intake_info += f"\n  Teaching Language: {effective_lang_elig}"
                    # Always show age requirements for documents_only/eligibility_only
                    if intent in ["documents_only", "eligibility_only"]:
                        if intake.get('age_min') is not None or intake.get('age_max') is not None:
                            age_range = []
                            if intake.get('age_min') is not None:
                                age_range.append(f"Min: {intake['age_min']}")
                            if intake.get('age_max') is not None:
                                age_range.append(f"Max: {intake['age_max']}")
                            intake_info += f"\n  Age Requirements: {', '.join(age_range) if age_range else 'Not specified'}"
                        else:
                            intake_info += f"\n  Age Requirements: Not specified"
                    elif intake.get('age_min') or intake.get('age_max'):
                        age_range = []
                        if intake.get('age_min'):
                            age_range.append(f"Min: {intake['age_min']}")
                        if intake.get('age_max'):
                            age_range.append(f"Max: {intake['age_max']}")
                        intake_info += f"\n  Age Requirements: {', '.join(age_range)}"
                    if intake.get('min_average_score'):
                        intake_info += f"\n  Minimum Average Score: {intake['min_average_score']}"
                    if intake.get('interview_required'):
                        intake_info += f"\n  Interview Required: Yes"
                    if intake.get('written_test_required'):
                        intake_info += f"\n  Written Test Required: Yes"
                
                if include_cost:
                    currency = intake.get('currency', 'CNY')

                    tuition = None
                    if intake.get('tuition_per_year') is not None:
                        tuition = f"{intake['tuition_per_year']} {currency}/year"
                    elif intake.get('tuition_per_semester') is not None:
                        tuition = f"{intake['tuition_per_semester']} {currency}/semester"
                    if tuition:
                        intake_info += f"\n  Tuition: {tuition}"
                    else:
                        intake_info += f"\n  Tuition: not provided"

                    application_fee = intake.get('application_fee')
                    if application_fee is None:
                        application_fee = 0
                    intake_info += f"\n  Application Fee: {application_fee} {currency}"

                    accommodation_fee = intake.get('accommodation_fee')
                    if accommodation_fee is None:
                        accommodation_fee = 0
                    acc_period = intake.get('accommodation_fee_period') or 'year'
                    intake_info += f"\n  Accommodation: {accommodation_fee} {currency} per {acc_period}"
                    if acc_period and 'month' in str(acc_period).lower():
                        annual_estimate = float(accommodation_fee) * 12
                        intake_info += f" (estimated annual: {annual_estimate:.2f} {currency})"

                    medical_fee = intake.get('medical_insurance_fee')
                    if medical_fee is None:
                        medical_fee = 0
                    med_period = intake.get('medical_insurance_fee_period') or 'year'
                    intake_info += f"\n  Medical Insurance: {medical_fee} {currency} per {med_period}"
                    if med_period and 'month' in str(med_period).lower():
                        annual_estimate = float(medical_fee) * 12
                        intake_info += f" (estimated annual: {annual_estimate:.2f} {currency})"

                    arrival_fee = intake.get('arrival_medical_checkup_fee')
                    if arrival_fee is None:
                        arrival_fee = 0
                    one_time = " (one-time)" if intake.get('arrival_medical_checkup_is_one_time', True) else ""
                    intake_info += f"\n  Arrival Medical Checkup: {arrival_fee} {currency}{one_time}"

                    visa_fee = intake.get('visa_extension_fee')
                    if visa_fee not in [None, ""]:
                        intake_info += f"\n  Visa Extension: {visa_fee} {currency} per year"

                    bank_amount = intake.get('bank_statement_amount')
                    if bank_amount not in [None, ""]:
                        bank_currency = intake.get('bank_statement_currency', 'CNY')
                        intake_info += f"\n  Bank Statement Required: {bank_amount} {bank_currency}"
                        if intake.get('bank_statement_note'):
                            intake_info += f" ({intake['bank_statement_note']})"
                
                if include_eligibility or include_docs:
                    # Always show structured requirement fields for docs/eligibility intents
                    # Bank statement requirement (always show for docs/eligibility)
                    if intent in ["documents_only", "eligibility_only"]:
                        bank_req = intake.get('bank_statement_required')
                        if bank_req is True:
                            bank_amount = intake.get('bank_statement_amount')
                            bank_currency = intake.get('bank_statement_currency', 'CNY')
                            bank_note = intake.get('bank_statement_note', '')
                            bank_line = f"  Bank Statement Required: {bank_amount} {bank_currency}" if bank_amount else "  Bank Statement Required: Yes"
                            if bank_note:
                                bank_line += f" ({bank_note})"
                            intake_info += f"\n{bank_line}"
                        elif bank_req is False:
                            intake_info += f"\n  Bank Statement Required: No (not required)"
                        else:
                            intake_info += f"\n  Bank Statement Required: Not specified"
                    
                    # Age requirements
                    if intake.get('age_min') or intake.get('age_max'):
                        age_range = []
                        if intake.get('age_min'):
                            age_range.append(f"Min: {intake['age_min']}")
                        if intake.get('age_max'):
                            age_range.append(f"Max: {intake['age_max']}")
                        intake_info += f"\n  Age Requirements: {', '.join(age_range)}"
                    
                    # Minimum average score
                    if intake.get('min_average_score'):
                        intake_info += f"\n  Minimum Average Score: {intake['min_average_score']}"
                    
                    # HSK requirements (always show for docs/eligibility)
                    if intent in ["documents_only", "eligibility_only"]:
                        hsk_req = intake.get('hsk_required')
                        if hsk_req is True:
                            lvl = intake.get('hsk_level')
                            min_score = intake.get('hsk_min_score')
                            hsk_line = "  HSK Required: Yes"
                            if lvl is not None:
                                hsk_line += f" (Level {lvl})"
                            if min_score is not None:
                                hsk_line += f", Min Score: {min_score}"
                            intake_info += f"\n{hsk_line}"
                        elif hsk_req is False:
                            intake_info += f"\n  HSK Required: No (not required)"
                        else:
                            intake_info += f"\n  HSK Required: Not specified"
                    elif include_eligibility and intake.get('hsk_required'):
                        # For other intents, only show if required
                        lvl = intake.get('hsk_level')
                        min_score = intake.get('hsk_min_score')
                        hsk_line = "  HSK Required:"
                        if lvl is not None:
                            hsk_line += f" Level {lvl}"
                        if min_score is not None:
                            hsk_line += f", Min Score: {min_score}"
                        intake_info += f"\n{hsk_line}"

                    # English test requirements (always show for docs/eligibility)
                    if intent in ["documents_only", "eligibility_only"]:
                        eng_req = intake.get('english_test_required')
                        if eng_req is True:
                            note = intake.get('english_test_note')
                            if note:
                                intake_info += f"\n  English Test Required: Yes ({note})"
                            else:
                                intake_info += f"\n  English Test Required: Yes"
                        elif eng_req is False:
                            intake_info += f"\n  English Test Required: No (not required)"
                        else:
                            intake_info += f"\n  English Test Required: Not specified"
                    elif include_eligibility and intake.get('english_test_required'):
                        # For other intents, only show if required
                        note = intake.get('english_test_note')
                        if note:
                            intake_info += f"\n  English Test Required: {note}"
                        else:
                            intake_info += f"\n  English Test Required"
                    
                    # Interview and written test (always show for docs/eligibility)
                    if intent in ["documents_only", "eligibility_only"]:
                        interview_req = intake.get('interview_required')
                        if interview_req is True:
                            intake_info += f"\n  Interview Required: Yes"
                        elif interview_req is False:
                            intake_info += f"\n  Interview Required: No"
                        else:
                            intake_info += f"\n  Interview Required: Not specified"
                        
                        written_test_req = intake.get('written_test_required')
                        if written_test_req is True:
                            intake_info += f"\n  Written Test Required: Yes"
                        elif written_test_req is False:
                            intake_info += f"\n  Written Test Required: No"
                        else:
                            intake_info += f"\n  Written Test Required: Not specified"
                        
                        acceptance_letter_req = intake.get('acceptance_letter_required')
                        if acceptance_letter_req is True:
                            intake_info += f"\n  Acceptance Letter Required: Yes"
                        elif acceptance_letter_req is False:
                            intake_info += f"\n  Acceptance Letter Required: No"
                        else:
                            intake_info += f"\n  Acceptance Letter Required: Not specified"
                    else:
                        # For other intents, only show if required
                        if intake.get('interview_required'):
                            intake_info += f"\n  Interview Required: Yes"
                        if intake.get('written_test_required'):
                            intake_info += f"\n  Written Test Required: Yes"
                        if intake.get('acceptance_letter_required'):
                            intake_info += f"\n  Acceptance Letter Required: Yes"
                    
                    # Inside China applicants (always show for docs/eligibility)
                    if intent in ["documents_only", "eligibility_only"]:
                        inside_china = intake.get('inside_china_applicants_allowed')
                        if inside_china is True:
                            intake_info += f"\n  Inside China Applicants: Allowed"
                            if intake.get('inside_china_extra_requirements'):
                                intake_info += f" (Extra requirements: {intake['inside_china_extra_requirements']})"
                        elif inside_china is False:
                            intake_info += f"\n  Inside China Applicants: Not Allowed"
                        else:
                            intake_info += f"\n  Inside China Applicants: Not specified"
                    elif include_eligibility and intake.get('inside_china_applicants_allowed') is not None:
                        # For other intents, only show if specified
                        if intake.get('inside_china_applicants_allowed'):
                            intake_info += f"\n  Inside China Applicants: Allowed"
                            if intake.get('inside_china_extra_requirements'):
                                intake_info += f" (Extra requirements: {intake['inside_china_extra_requirements']})"
                        else:
                            intake_info += f"\n  Inside China Applicants: Not Allowed"
                
                if include_scholarships:
                    # Check scholarship_available flag first
                    scholarship_available = intake.get('scholarship_available')
                    if scholarship_available is False:
                        intake_info += f"\n  Scholarship Available: No (scholarship is not available for this intake per database)"
                    else:
                        scholarships = scholarships_map.get(intake['id'], [])
                        if not scholarships and intent == "scholarship_only":
                            # For scholarship_only intent, even if no structured scholarships, check scholarship_info
                            if intake.get('scholarship_info'):
                                intake_info += f"\n  Scholarship Info: {intake['scholarship_info']}"
                            else:
                                intake_info += f"\n  Scholarships: No structured scholarship records in database for this intake"
                        elif scholarships:
                            intake_info += f"\n  Scholarships Available:"
                        for sch in scholarships[:3]:
                            sch_lines = []
                            if sch.get('covers_tuition'):
                                sch_lines.append("Covers Tuition: Yes")
                            if sch.get('tuition_waiver_percent') is not None:
                                sch_lines.append(f"Tuition Waiver: {sch['tuition_waiver_percent']}%")
                            if sch.get('covers_accommodation'):
                                sch_lines.append("Covers Accommodation: Yes")
                            if sch.get('covers_insurance'):
                                sch_lines.append("Covers Insurance: Yes")
                            if sch.get('living_allowance_monthly') is not None:
                                sch_lines.append(f"Living Allowance: {sch['living_allowance_monthly']} CNY/month")
                            elif sch.get('living_allowance_yearly') is not None:
                                sch_lines.append(f"Living Allowance: {sch['living_allowance_yearly']} CNY/year")
                            if sch.get('first_year_only'):
                                sch_lines.append("First Year Only: Yes")
                            if sch.get('renewal_required'):
                                sch_lines.append("Renewal Required: Yes")
                            if sch.get('deadline'):
                                sch_lines.append(f"Scholarship Deadline: {sch['deadline']}")
                            if sch.get('eligibility_note'):
                                sch_lines.append(f"Eligibility Note: {sch['eligibility_note']}")
                            if sch_lines:
                                intake_info += "\n    - " + "; ".join(sch_lines)
                        
                        # Add competitiveness note based on university ranking (only for scholarship queries)
                        if intent == "scholarship_only":
                            uni_id = intake.get('university_id')
                            if uni_id:
                                uni_info = next((u for u in self.all_universities if u["id"] == uni_id), None)
                                if uni_info:
                                    competitiveness_notes = []
                                    # Use university_ranking (not world_ranking_band) for realism
                                    uni_ranking = uni_info.get('university_ranking')
                                    if uni_ranking:
                                        if uni_ranking <= 50:
                                            competitiveness_notes.append("Highly competitive (Top 50 university)")
                                        elif uni_ranking <= 100:
                                            competitiveness_notes.append("Very competitive (Top 100 university)")
                                        elif uni_ranking <= 200:
                                            competitiveness_notes.append("Competitive (Top 200 university)")
                                    elif uni_info.get('world_ranking_band'):
                                        wrb = uni_info['world_ranking_band']
                                        if wrb and wrb < 500:
                                            competitiveness_notes.append("Highly competitive (World ranking < 500)")
                                        elif wrb and wrb < 1000:
                                            competitiveness_notes.append("Competitive (World ranking < 1000)")
                                    
                                    # Check if any scholarship offers 100% waiver
                                    has_100_percent = any(sch.get('tuition_waiver_percent') == 100 for sch in scholarships) if scholarships else False
                                    if has_100_percent and competitiveness_notes:
                                        competitiveness_notes.append("100% waiver is highly competitive; usually requires excellent GPA + strong profile")
                                    
                                    if uni_info.get('national_ranking'):
                                        nr = uni_info['national_ranking']
                                        if nr:
                                            competitiveness_notes.append(f"National ranking: {nr}")
                                    
                                    if competitiveness_notes:
                                        intake_info += f"\n  Competitiveness Note: {', '.join(competitiveness_notes)}"
                    
                    # For scholarship_only intent, always check scholarship_info and notes for forms (even if no structured scholarships)
                    if intent == "scholarship_only":
                        scholarship_forms = []
                        # Check scholarship_info for forms
                        if intake.get('scholarship_info'):
                            scholarship_info_lower = intake.get('scholarship_info', '').lower()
                            form_keywords = ["form", "application form", "scholarship form", "talents", "outstanding"]
                            if any(kw in scholarship_info_lower for kw in form_keywords):
                                # Extract the form name - try to find a specific form name
                                scholarship_forms.append(intake.get('scholarship_info'))
                        
                        # Check notes for forms
                        if intake.get('notes'):
                            notes_lower = intake.get('notes', '').lower()
                            if any(kw in notes_lower for kw in ["scholarship form", "application form", "talents", "outstanding", "form"]):
                                # Extract form name from notes if it mentions a specific form
                                scholarship_forms.append(intake.get('notes'))
                        
                        # Check documents for scholarship-specific forms
                        documents_for_sch = docs_map.get(intake['id'], [])
                        for doc in documents_for_sch:
                            doc_name_lower = doc.get('name', '').lower()
                            if any(kw in doc_name_lower for kw in ["scholarship", "talents", "outstanding", "form"]):
                                if doc.get('name') not in [f if isinstance(f, str) else f.get('name', '') for f in scholarship_forms]:
                                    scholarship_forms.append(doc.get('name'))
                        
                        if scholarship_forms:
                            intake_info += f"\n  Scholarship-Specific Documents Required:"
                            for form in scholarship_forms[:5]:  # Limit to 5
                                if isinstance(form, str):
                                    # Try to extract just the form name if it's a long text
                                    if len(form) > 100:
                                        # Look for form name patterns
                                        import re
                                        form_match = re.search(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+(?:Form|Application))', form)
                                        if form_match:
                                            intake_info += f"\n    - {form_match.group(1)}"
                                        else:
                                            intake_info += f"\n    - {form[:80]}..."
                                    else:
                                        intake_info += f"\n    - {form}"
                                elif isinstance(form, dict):
                                    intake_info += f"\n    - {form.get('name', 'N/A')}"
                                    if form.get('rules'):
                                        intake_info += f": {form['rules']}"
                
                # Include ProgramIntake notes and scholarship_info
                # Fix 3: Always show notes for documents_only/eligibility_only/scholarship_only
                if intent in ["documents_only", "eligibility_only", "scholarship_only"]:
                    if intake.get('notes'):
                        intake_info += f"\n  Important Notes: {intake['notes']}"
                    else:
                        intake_info += f"\n  Important Notes: Not specified"
                    if intent == "scholarship_only" and intake.get('scholarship_info'):
                        intake_info += f"\n  Scholarship Info: {intake['scholarship_info']}"
                else:
                    # For other intents, include notes if present
                    if intake.get('notes'):
                        intake_info += f"\n  Important Notes: {intake['notes']}"
                    if intake.get('scholarship_info'):
                        intake_info += f"\n  Scholarship Info: {intake['scholarship_info']}"
                
                if include_docs:
                    # Merge documents_required (free text) with structured ProgramDocument requirements
                    documents = docs_map.get(intake['id'], [])
                    
                    # Parse documents_required (comma-separated) and merge with structured documents
                    documents_required_text = intake.get('documents_required', '')
                    if documents_required_text:
                        # Normalize document names for deduplication
                        doc_names_from_text = [d.strip().lower() for d in documents_required_text.split(',') if d.strip()]
                        existing_doc_names = {doc.get('name', '').lower() for doc in documents}
                        
                        # Add documents from free text that aren't already in structured documents
                        for doc_name in doc_names_from_text:
                            if doc_name not in existing_doc_names:
                                # Check if it matches structured fields (bank statement, HSK, English test)
                                is_structured = False
                                if 'bank' in doc_name and 'statement' in doc_name and intake.get('bank_statement_required'):
                                    is_structured = True  # Will be shown in structured section
                                elif 'hsk' in doc_name and intake.get('hsk_required'):
                                    is_structured = True  # Will be shown in exam requirements
                                elif any(term in doc_name for term in ['ielts', 'toefl', 'english', 'pte', 'duolingo']) and intake.get('english_test_required'):
                                    is_structured = True  # Will be shown in exam requirements
                                
                                if not is_structured:
                                    documents.append({
                                        'name': doc_name.title(),
                                        'is_required': True,
                                        'rules': None,
                                        'applies_to': None
                                    })
                    
                    if documents:
                        # Filter for scholarship-related documents if intent is scholarship_only
                        if intent == "scholarship_only":
                            scholarship_keywords = ["scholarship", "talents", "outstanding", "waiver", "stipend", "type-a", "type-b", "csc"]
                            filtered_docs = []
                            for doc in documents:
                                doc_name_lower = doc.get('name', '').lower()
                                applies_to_lower = (doc.get('applies_to') or '').lower()
                                # Check if document name or applies_to contains scholarship keywords
                                if any(kw in doc_name_lower for kw in scholarship_keywords) or any(kw in applies_to_lower for kw in scholarship_keywords):
                                    filtered_docs.append(doc)
                            documents = filtered_docs
                        
                        # Add structured document requirements (bank statement, HSK, English test, etc.)
                        # Bank Statement - always show with True/False/NULL handling
                        bank_req = intake.get('bank_statement_required')
                        bank_statement_already_in_docs = any('bank' in doc.get('name', '').lower() and 'statement' in doc.get('name', '').lower() for doc in documents)
                        
                        if not bank_statement_already_in_docs:
                            if bank_req is True:
                                bank_amount = intake.get('bank_statement_amount')
                                bank_currency = intake.get('bank_statement_currency', 'CNY')
                                bank_note = intake.get('bank_statement_note', '')
                                bank_rules = f"{bank_amount} {bank_currency}" if bank_amount else ""
                                if bank_note:
                                    bank_rules += f" ({bank_note})" if bank_rules else bank_note
                                documents.append({
                                    'name': 'Bank Statement',
                                    'is_required': True,
                                    'rules': bank_rules if bank_rules else None,
                                    'applies_to': None
                                })
                            elif bank_req is False:
                                # Explicitly say not required
                                documents.append({
                                    'name': 'Bank Statement',
                                    'is_required': False,
                                    'rules': 'Not required',
                                    'applies_to': None
                                })
                            else:
                                # NULL - not specified (only show for doc/eligibility intents)
                                if intent in ["documents_only", "eligibility_only"]:
                                    documents.append({
                                        'name': 'Bank Statement',
                                        'is_required': None,
                                        'rules': 'Not specified',
                                        'applies_to': None
                                    })
                        
                        # HSK Certificate (if required and not already in documents)
                        if intake.get('hsk_required') and not any('hsk' in doc.get('name', '').lower() for doc in documents):
                            hsk_level = intake.get('hsk_level')
                            hsk_min_score = intake.get('hsk_min_score')
                            hsk_rules = []
                            if hsk_level is not None:
                                hsk_rules.append(f"Level {hsk_level}")
                            if hsk_min_score is not None:
                                hsk_rules.append(f"Min Score: {hsk_min_score}")
                            documents.append({
                                'name': 'HSK Certificate',
                                'is_required': True,
                                'rules': ', '.join(hsk_rules) if hsk_rules else None,
                                'applies_to': None
                            })
                        
                        # English Test Certificate (if required and not already in documents)
                        if intake.get('english_test_required') and not any(term in doc.get('name', '').lower() for doc in documents for term in ['ielts', 'toefl', 'english', 'pte', 'duolingo']):
                            eng_note = intake.get('english_test_note', '')
                            documents.append({
                                'name': 'English Test Certificate',
                                'is_required': True,
                                'rules': eng_note if eng_note else None,
                                'applies_to': None
                            })
                        
                        # Acceptance Letter (if required)
                        if intake.get('acceptance_letter_required') and not any('acceptance' in doc.get('name', '').lower() for doc in documents):
                            documents.append({
                                'name': 'Acceptance Letter',
                                'is_required': True,
                                'rules': None,
                                'applies_to': None
                            })
                        
                        # Inside China extra requirements (if applicable)
                        if intake.get('inside_china_applicants_allowed') and intake.get('inside_china_extra_requirements'):
                            if not any('inside china' in doc.get('name', '').lower() or 'inside china' in (doc.get('applies_to') or '').lower() for doc in documents):
                                documents.append({
                                    'name': 'Additional Requirements for Inside China Applicants',
                                    'is_required': True,
                                    'rules': intake.get('inside_china_extra_requirements'),
                                    'applies_to': 'inside_china_only'
                                })
                        
                        if documents:
                            doc_label = "Scholarship-Related Documents" if intent == "scholarship_only" else "Required Documents"
                            intake_info += f"\n  {doc_label} ({len(documents)} total):"
                            for doc in documents:
                                doc_name = doc.get('name', 'N/A')
                                is_required = doc.get('is_required')
                                
                                # Special handling for bank statement with True/False/NULL
                                if doc_name.lower() == 'bank statement':
                                    if is_required is True:
                                        req_str = "Required"
                                    elif is_required is False:
                                        req_str = "Not required"
                                    else:
                                        req_str = "Not specified"
                                else:
                                    req_str = "Required" if is_required else "Optional"
                                
                                intake_info += f"\n    - {doc_name} ({req_str})"
                                if doc.get('rules') and doc.get('rules') not in ['Not required', 'Not specified']:
                                    intake_info += f": {doc['rules']}"
                                if doc.get('applies_to'):
                                    intake_info += f" [Applies to: {doc['applies_to']}]"
                
                if include_exams:
                    exam_reqs = exams_map.get(intake['id'], [])
                    if exam_reqs:
                        intake_info += f"\n  Exam Requirements:"
                        for req in exam_reqs[:3]:
                            req_str = "Required" if req.get('required') else "Optional"
                            intake_info += f"\n    - {req.get('exam_name', 'N/A')} ({req_str})"
                            if req.get('subjects'):
                                intake_info += f": {req['subjects']}"
                            if req.get('min_score'):
                                intake_info += f", Min Score: {req['min_score']}"
                            if req.get('notes'):
                                intake_info += f" - Note: {req['notes']}"
                
                context_parts.append(intake_info)
            context_parts.append("=== END MATCHED PROGRAMS ===")
        
        return "\n".join(context_parts)
    
    def _normalize_unicode_text(self, text: str) -> str:
        """
        Normalize Unicode punctuation to ASCII before intent/keyword checks.
        Converts curly apostrophes and quotes to ASCII: '→', "/"→".
        Then lowercases.
        """
        if not text:
            return ""
        # Replace curly quotes and apostrophes
        text = text.replace("'", "'").replace("'", "'")  # Curly apostrophes
        text = text.replace('"', '"').replace('"', '"')  # Curly quotes
        text = text.replace('"', '"').replace('"', '"')  # Double curly quotes
        return text.lower()
    
    def _strip_degree_from_major_query(self, text: str) -> str:
        """
        Remove degree phrases from major_query, keeping only the subject name.
        Example: "bachelor in chemistry" -> "chemistry"
        Also handles: "subject is Bachelor in chemistry" -> "chemistry"
        """
        if not text:
            return ""
        
        text_lower = text.lower().strip()
        
        # Degree phrases to strip (case-insensitive)
        # Order matters: longer phrases first to avoid partial matches
        degree_phrases = [
            "bachelor in",
            "bachelor of",
            "bsc in",
            "b.sc in",
            "bsc ",  # Handle "BSc Applied Chemistry" -> "applied chemistry"
            "b.sc ",  # Handle "B.Sc Applied Chemistry"
            "master in",
            "master of",
            "msc in",
            "m.sc in",
            "msc ",  # Handle "MSc Applied Chemistry"
            "m.sc ",  # Handle "M.Sc Applied Chemistry"
            "phd in",
            "doctorate in"
        ]
        
        # First try removing from start (most common case)
        for phrase in degree_phrases:
            if text_lower.startswith(phrase):
                # Remove the phrase and any following whitespace
                cleaned = text_lower[len(phrase):].strip()
                if cleaned:  # Only return if there's something left
                    return cleaned
        
        # Also check if phrase appears anywhere in the text (e.g., "subject is Bachelor in chemistry")
        for phrase in degree_phrases:
            phrase_with_space = f" {phrase}"  # Look for phrase with leading space
            if phrase_with_space in text_lower:
                # Find the position and extract what comes after
                idx = text_lower.find(phrase_with_space)
                if idx >= 0:
                    # Get text after the phrase
                    after_phrase = text_lower[idx + len(phrase_with_space):].strip()
                    # Also get text before the phrase (might be the subject if phrase is in middle)
                    before_phrase = text_lower[:idx].strip()
                    # If there's text after, prefer that; otherwise use before
                    if after_phrase:
                        return after_phrase
                    elif before_phrase and not any(p in before_phrase for p in ["subject", "is", "for", "program"]):
                        return before_phrase
        
        # If no degree phrase found, return original (already lowercased and stripped)
        return text_lower
    
    def _format_list_response_deterministic(self, intakes: List[Dict[str, Any]], offset: int, total: int, 
                                           duration_preference: Optional[str] = None, 
                                           user_message: Optional[str] = None) -> Dict[str, Any]:
        """
        Format a deterministic response for list/compare queries without LLM.
        Returns a compact table-like list of universities with key fee information.
        Handles single-university vs multi-university cases differently.
        If duration_preference is specified OR user asked for "programs/courses", show all programs (not grouped by university).
        """
        if not intakes:
            return {
                "response": "No matching programs found.",
                "used_db": True,
                "used_tavily": False,
                "sources": []
            }
        
        response_parts = []
        
        # Helper function to format a single intake line
        def format_intake_line(intake: Dict[str, Any], idx: int = None) -> str:
            uni_name = intake.get('university_name', 'N/A')
            major_name = intake.get('major_name', 'N/A')
            degree_level = intake.get('degree_level', 'N/A')
            teaching_lang = intake.get('effective_teaching_language') or intake.get('teaching_language', 'N/A')
            
            # Format tuition
            currency = intake.get('currency', 'CNY')
            tuition_str = "Not provided"
            if intake.get('tuition_per_year'):
                tuition_str = f"{intake['tuition_per_year']} {currency}/year"
            elif intake.get('tuition_per_semester'):
                tuition_str = f"{intake['tuition_per_semester']} {currency}/semester"
            
            # Format application fee
            app_fee = intake.get('application_fee', 0) or 0
            app_fee_str = f"{app_fee} {currency}" if app_fee else "Not provided"
            
            # Format deadline
            deadline = intake.get('application_deadline', 'N/A')
            
            prefix = f"{idx}. " if idx is not None else ""
            return (
                f"{prefix}{uni_name} - {major_name} ({degree_level}, {teaching_lang}-taught)\n"
                f"   Tuition: {tuition_str} | Application Fee: {app_fee_str} | Deadline: {deadline}"
            )
        
        # Detect single-university case OR duration_preference OR "programs/courses" keywords
        unique_university_ids = set(intake.get('university_id') for intake in intakes if intake.get('university_id'))
        user_asked_for_programs = user_message and any(kw in user_message.lower() for kw in ["programs", "courses", "list programs", "show programs", "all programs"])
        should_show_all_programs = (len(unique_university_ids) == 1) or duration_preference or user_asked_for_programs
        
        if should_show_all_programs:
            # SINGLE-UNIVERSITY CASE: Show ALL programs, not just one representative
            uni_id = list(unique_university_ids)[0]
            uni_name = intakes[0].get('university_name', 'N/A')
            
            # Sort all intakes for this university (by deadline, then tuition)
            dt_func = datetime
            today_date = date.today()
            def sort_key(i, dt=dt_func, today=today_date):
                deadline = i.get('application_deadline')
                tuition = i.get('tuition_per_year') or (i.get('tuition_per_semester', 0) * 2) or float('inf')
                is_upcoming = False
                if deadline:
                    try:
                        deadline_date = dt.fromisoformat(deadline).date()
                        is_upcoming = deadline_date > today
                    except:
                        is_upcoming = False
                return (not is_upcoming, tuition, deadline or '')
            
            sorted_intakes = sorted(intakes, key=sort_key)
            
            # Apply pagination to programs (not universities)
            page_size = 12  # Reasonable page size for programs
            start_idx = offset
            end_idx = min(offset + page_size, len(sorted_intakes))
            displayed_intakes = sorted_intakes[start_idx:end_idx]
            displayed_count = len(displayed_intakes)
            
            # Use actual program count as total (not the passed total which might be for universities)
            total_programs = len(sorted_intakes)
            
            # Header: "I found X language program(s) at <University Name>:"
            program_word = "program" if total_programs == 1 else "programs"
            response_parts.append(f"I found {total_programs} {program_word} at {uni_name}:\n")
            
            # List each program
            for idx, intake in enumerate(displayed_intakes, 1):
                response_parts.append(format_intake_line(intake, idx=idx))
            
            # Pagination: Only show if there's actually more to show
            # Don't show pagination if total programs ≤ 5 and we're showing all (offset == 0)
            if total_programs > offset + displayed_count:
                remaining = total_programs - (offset + displayed_count)
                start_num = offset + 1
                end_num = offset + displayed_count
                response_parts.append(f"\nShowing {start_num}–{end_num} of {total_programs} programs. Say 'show more' for the next page ({remaining} more available).")
            # If total programs ≤ 5 and we're showing all, don't show pagination (already handled by condition above)
        
        else:
            # MULTI-UNIVERSITY CASE: Select one representative intake per university
            from collections import defaultdict
            by_university = defaultdict(list)
            for intake in intakes:
                by_university[intake.get('university_id')].append(intake)
            
            # Select best intake per university (upcoming deadline, then lowest tuition, then earliest deadline)
            selected_intakes = []
            dt_func = datetime
            today_date = date.today()
            for uni_id, uni_intakes in by_university.items():
                # Sort by: upcoming deadline first, then tuition (lowest), then deadline
                def sort_key(i, dt=dt_func, today=today_date):
                    deadline = i.get('application_deadline')
                    tuition = i.get('tuition_per_year') or (i.get('tuition_per_semester', 0) * 2) or float('inf')
                    is_upcoming = False
                    if deadline:
                        try:
                            deadline_date = dt.fromisoformat(deadline).date()
                            is_upcoming = deadline_date > today
                        except:
                            is_upcoming = False
                    return (not is_upcoming, tuition, deadline or '')
                
                best = sorted(uni_intakes, key=sort_key)[0]
                selected_intakes.append(best)
            
            # Sort selected intakes by university name
            selected_intakes.sort(key=lambda x: x.get('university_name', ''))
            
            # Total unique universities (caller should pass this correctly)
            # Use max of passed total and computed from intakes (defensive)
            total_unique_universities = max(total, len(selected_intakes))
            
            # Apply pagination to universities
            page_size = 12
            start_idx = offset
            end_idx = min(offset + page_size, len(selected_intakes))
            displayed_intakes = selected_intakes[start_idx:end_idx]
            displayed_count = len(displayed_intakes)
            unique_universities_shown = len(displayed_intakes)
            
            # Format pagination header
            start_num = offset + 1
            end_num = offset + displayed_count
            if offset == 0:
                # Header: "Here are the top N universities with matching programs:"
                # N = number of unique universities shown (not total)
                response_parts.append(f"Here are the top {unique_universities_shown} universities with matching programs:\n")
            else:
                response_parts.append(f"Showing {start_num}–{end_num} of {total_unique_universities} universities:\n")
            
            # List each university (one representative program per university)
            for idx, intake in enumerate(displayed_intakes, 1):
                response_parts.append(format_intake_line(intake, idx=idx))
            
            # Pagination: Only show if there's actually more to show
            if total_unique_universities > offset + displayed_count:
                remaining = total_unique_universities - (offset + displayed_count)
                response_parts.append(f"\nShowing {start_num}–{end_num} of {total_unique_universities} universities. Say 'show more' for the next page ({remaining} more available).")
        
        return {
            "response": "\n".join(response_parts),
            "used_db": True,
            "used_tavily": False,
            "sources": []
        }
    
    def _is_duration_question(self, text: str) -> bool:
        """
        Detect if the user is asking about program duration.
        Returns True for questions like "how long", "is it 1 year", "one year", "semester", etc.
        """
        if not text:
            return False
        
        text_lower = text.lower()
        duration_keywords = [
            "how long", "duration", "length",
            "is it 1 year", "is it one year", "1 year", "one year",
            "is it 2 year", "is it two year", "2 year", "two year",
            "semester", "half year", "6 month", "six month",
            "months", "weeks", "years",
            "1 semester", "one semester", "two semester"
        ]
        
        return any(kw in text_lower for kw in duration_keywords)
    
    def _is_generic_program_query(self, text: str, major_query: Optional[str] = None) -> bool:
        """
        Check if the query is generic (not a specific major name).
        Returns True for queries like "language program", "fees", "documents required", etc.
        Does NOT return True for duration questions (handled separately).
        """
        if not text:
            return False
        
        # Do NOT treat duration questions as generic/list queries
        if self._is_duration_question(text):
            return False
        
        text_lower = text.lower()
        major_query_lower = (major_query or "").lower()
        
        # Explicit list patterns (required for list mode)
        list_patterns = [
            "program list", "programs list", "list programs", "list of programs",
            "show programs", "show all programs", "available programs",
            "what programs", "which programs", "all programs",
            "english taught programs", "chinese taught programs"
        ]
        
        # If it's an explicit list request, return True
        if any(pattern in text_lower for pattern in list_patterns):
            return True
        
        # Generic program keywords (but NOT just "program" alone)
        generic_keywords = [
            "language", "language program", "language course",
            "fees", "tuition", "application fee", "cost", "price",
            "documents required", "documents", "requirements",
            "scholarship", "scholarships"
        ]
        
        # Check if text contains generic keywords (but require explicit patterns, not just "program")
        if any(kw in text_lower for kw in generic_keywords):
            # If major_query is also generic or empty, it's a generic query
            if not major_query or major_query_lower in ["language", "language program", "language course"]:
                return True
            # If major_query contains generic keywords, it's generic
            if any(kw in major_query_lower for kw in ["language", "program", "fees", "tuition", "documents"]):
                return True
        
        return False
    
    def _is_pagination_command(self, text: str) -> bool:
        """
        Check if the user message is a pagination command.
        Returns True for: "show more", "more", "next", "next page", "page 2", "continue"
        """
        if not text:
            return False
        text_lower = text.lower().strip()
        pagination_commands = [
            "show more",
            "more",
            "next",
            "next page",
            "page 2",
            "continue"
        ]
        return any(cmd == text_lower or text_lower.startswith(cmd + " ") for cmd in pagination_commands)
    
    def _get_pagination_cache_key(self, partner_id: Optional[int], conversation_history: List[Dict[str, str]], 
                                 conversation_id: Optional[str] = None) -> Tuple[Optional[int], str]:
        """
        Generate stable cache key for pagination state.
        Uses conversation_id if provided, else extracts from history, else uses stable session fallback.
        """
        # If conversation_id param is provided, use it directly
        if conversation_id:
            cache_key = (partner_id, conversation_id)
            print(f"DEBUG: Pagination cache key (from param): {cache_key}")
            return cache_key
        
        # Try to extract conversation_id from history if available
        extracted_id = None
        for msg in reversed(conversation_history):
            if isinstance(msg, dict) and "conversation_id" in msg:
                extracted_id = str(msg.get("conversation_id"))
                break
        
        if extracted_id:
            cache_key = (partner_id, extracted_id)
            print(f"DEBUG: Pagination cache key (from history): {cache_key}")
            return cache_key
        
        # Stable fallback: use session fallback ID (does NOT depend on last messages)
        if partner_id not in self._session_fallback_id_by_partner:
            import uuid
            self._session_fallback_id_by_partner[partner_id] = str(uuid.uuid4())[:8]
            print(f"DEBUG: Generated stable session fallback ID for partner_id={partner_id}: {self._session_fallback_id_by_partner[partner_id]}")
        
        fallback_id = self._session_fallback_id_by_partner[partner_id]
        cache_key = (partner_id, fallback_id)
        print(f"DEBUG: Pagination cache key (stable fallback): {cache_key}")
        
        # Store this key as last pagination key for this partner (for stable fallback)
        self._last_pagination_key_by_partner[partner_id] = cache_key
        
        return cache_key
    
    def _get_pagination_state(self, partner_id: Optional[int], conversation_history: List[Dict[str, str]], 
                             conversation_id: Optional[str] = None) -> Optional[PaginationState]:
        """Get pagination state from cache"""
        cache_key = self._get_pagination_cache_key(partner_id, conversation_history, conversation_id)
        state = self._pagination_cache.get(cache_key)
        if state:
            print(f"DEBUG: Pagination cache HIT for key: {cache_key}")
        else:
            print(f"DEBUG: Pagination cache MISS for key: {cache_key}")
            # Try partner_id fallback key if available
            if partner_id in self._last_pagination_key_by_partner:
                fallback_key = self._last_pagination_key_by_partner[partner_id]
                state = self._pagination_cache.get(fallback_key)
                if state:
                    print(f"DEBUG: Pagination cache HIT for fallback key: {fallback_key}")
                    # Update cache key mapping
                    cache_key = fallback_key
        # Check if state is expired (older than 1 hour)
        if state and time.time() - state.timestamp > 3600:
            del self._pagination_cache[cache_key]
            print(f"DEBUG: Pagination state expired, removed from cache")
            return None
        return state
    
    def _set_pagination_state(self, partner_id: Optional[int], conversation_history: List[Dict[str, str]], 
                             results: List[Dict[str, Any]], offset: int, total: int, 
                             page_size: int, intent: str, last_displayed: Optional[List[Dict[str, Any]]] = None,
                             conversation_id: Optional[str] = None):
        """Store pagination state in cache"""
        cache_key = self._get_pagination_cache_key(partner_id, conversation_history, conversation_id)
        self._pagination_cache[cache_key] = PaginationState(
            results=results,
            offset=offset,
            total=total,
            page_size=page_size,
            intent=intent,
            timestamp=time.time(),
            last_displayed=last_displayed
        )
        print(f"DEBUG: Stored pagination state for key: {cache_key}, offset={offset}, total={total}, page_size={page_size}, last_displayed_count={len(last_displayed) if last_displayed else 0}")
    
    def generate_response(self, user_message: str, conversation_history: List[Dict[str, str]], 
                         partner_id: Optional[int] = None, conversation_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Generate response for partner query.
        Uses database as primary source, Tavily only when necessary.
        """
        print(f"\n{'='*80}")
        print(f"DEBUG: PartnerAgent.generate_response() called")
        print(f"DEBUG: User message: {user_message}")
        
        # Reset multi-track info for this query
        self._multi_track_info = None
        print(f"DEBUG: Conversation history length: {len(conversation_history)}")
        print(f"{'='*80}\n")
        
        # Normalize Unicode punctuation BEFORE any processing
        user_message_normalized = self._normalize_unicode_text(user_message)
        print(f"DEBUG: Normalized user message: {user_message_normalized}")
        
        # Fix 1: Handle pagination commands BEFORE LLM extraction
        if self._is_pagination_command(user_message):
            print(f"DEBUG: Pagination command detected")
            pagination_state = self._get_pagination_state(partner_id, conversation_history, conversation_id)
            
            if pagination_state:
                # Compute next slice
                next_offset = pagination_state.offset + pagination_state.page_size
                next_batch = pagination_state.results[next_offset:next_offset + pagination_state.page_size]
                
                if next_batch:
                    print(f"DEBUG: Returning list page offset={next_offset} size={len(next_batch)} total={pagination_state.total}")
                    # Update pagination state in cache (including last_displayed)
                    self._set_pagination_state(
                        partner_id=partner_id,
                        conversation_history=conversation_history,
                        results=pagination_state.results,  # Keep all results
                        offset=next_offset,
                        total=pagination_state.total,
                        page_size=pagination_state.page_size,
                        intent=pagination_state.intent,
                        last_displayed=next_batch,  # Update with what we're showing now
                        conversation_id=conversation_id
                    )
                    return self._format_list_response_deterministic(
                        next_batch, next_offset, pagination_state.total,
                        duration_preference=None, user_message=user_message
                    )
                else:
                    return {
                        "response": "No more results. You've reached the end.",
                        "used_db": True,
                        "used_tavily": False,
                        "sources": []
                    }
            else:
                return {
                    "response": "I can show more, but I need the list request again. Please repeat your last query (e.g., 'List language programs for March intake').",
                    "used_db": False,
                    "used_tavily": False,
                    "sources": []
                }
        
        # Fix B: Handle duration questions BEFORE generic/list detection
        if self._is_duration_question(user_message_normalized):
            print(f"DEBUG: Duration question detected: {user_message_normalized}")
            pagination_state = self._get_pagination_state(partner_id, conversation_history, conversation_id)
            
            if pagination_state and pagination_state.last_displayed:
                last_displayed = pagination_state.last_displayed
                print(f"DEBUG: Found last_displayed with {len(last_displayed)} program(s)")
                
                if len(last_displayed) == 1:
                    # Single program: answer directly
                    intake = last_displayed[0]
                    major_name = intake.get('major_name', 'N/A')
                    tuition_per_year = intake.get('tuition_per_year')
                    tuition_per_semester = intake.get('tuition_per_semester')
                    
                    # Try to infer duration from major name
                    major_lower = major_name.lower()
                    if any(term in major_lower for term in ["1 year", "one year", "1-year"]):
                        duration_answer = "Yes, this is a 1-year program."
                    elif any(term in major_lower for term in ["1 semester", "one semester", "half year", "6 month", "six month"]):
                        duration_answer = "No, this is a 1-semester (6-month) program."
                    elif tuition_per_year:
                        duration_answer = "The database doesn't explicitly store duration; tuition is annual. This is typically a 1-year foundation/language track unless the university states otherwise."
                    elif tuition_per_semester:
                        duration_answer = "The database shows semester-based tuition. This is typically a 1-semester program, but some universities offer both 1-semester and 1-year options. Please check with the university for exact duration."
                    else:
                        duration_answer = "The database doesn't explicitly store program duration. Please check with the university or refer to the program details for exact duration information."
                    
                    return {
                        "response": f"{duration_answer}",
                        "used_db": True,
                        "used_tavily": False,
                        "sources": []
                    }
                else:
                    # Multiple programs: ask which one, but try to infer
                    response_parts = ["Which program are you asking about?"]
                    for idx, intake in enumerate(last_displayed, 1):
                        major_name = intake.get('major_name', 'N/A')
                        major_lower = major_name.lower()
                        duration_hint = ""
                        
                        if any(term in major_lower for term in ["1 year", "one year", "1-year"]):
                            duration_hint = " (1-year program)"
                        elif any(term in major_lower for term in ["1 semester", "one semester", "half year", "6 month", "six month"]):
                            duration_hint = " (1-semester program)"
                        elif intake.get('tuition_per_year'):
                            duration_hint = " (typically 1-year, annual tuition)"
                        elif intake.get('tuition_per_semester'):
                            duration_hint = " (typically 1-semester, semester tuition)"
                        
                        response_parts.append(f"{idx}. {major_name}{duration_hint}")
                    
                    return {
                        "response": "\n".join(response_parts),
                        "used_db": True,
                        "used_tavily": False,
                        "sources": []
                    }
            else:
                print(f"DEBUG: Duration question but no last_displayed found, continuing to normal flow")
        
        current_date = date.today()
        print(f"DEBUG: Current date: {current_date}")
        
        t_state_start = time.perf_counter()
        quick = self._quick_extract_query(user_message)
        used_quick = False
        if quick.get("confident"):
            print(f"DEBUG: Using quick extraction path: {quick}")
            major_text = quick.get("major_text")
            state = PartnerQueryState(
                degree_level=self._normalize_degree_level_value(quick.get("degree_level")),
                major_query=major_text.lower().strip() if major_text else None,
                university=None,
                intake_term=quick.get("intake_term"),
                intake_year=quick.get("intake_year"),
                duration_preference=quick.get("duration_preference"),
                duration_years_target=quick.get("duration_years_target")
            )
            used_quick = True
        else:
            print(f"DEBUG: Extracting partner query state via LLM...")
            state = self.extract_partner_query_state(conversation_history)
            # If quick pass found a degree level, prefer it when LLM didn't set one
            if not state.degree_level and quick.get("degree_level"):
                state.degree_level = self._normalize_degree_level_value(quick.get("degree_level"))
            # If still missing degree level, do a tiny LLM degree-level-only extraction from the current message
            if not state.degree_level:
                dl = self._llm_quick_extract_degree_level(user_message)
                if dl:
                    state.degree_level = dl
            # If quick extraction found major_text but LLM didn't, use it (but not for list queries)
            # List query detection happens later, but we check here to avoid overwriting
            # We'll check for list patterns before setting major_query
            user_lower_check = user_message.lower()
            is_list_check = any(pattern in user_lower_check for pattern in [
                "program list", "list programs", "programs list", "list of programs",
                "show all programs", "show programs", "all programs",
                "what programs", "which programs", "available programs",
                "all majors", "list majors", "majors list",
                "english taught programs", "chinese taught programs", "taught programs"
            ]) or ("list" in user_lower_check.split() and "program" in user_lower_check)
            
            if not state.major_query and quick.get("major_text") and not is_list_check:
                state.major_query = quick.get("major_text").lower().strip()
        t_state_end = time.perf_counter()
        print(f"DEBUG: Extracted state: {state.to_dict()} (used_quick={used_quick}) in {(t_state_end - t_state_start):.3f}s")

        # Normalize major_query if present
        if state.major_query:
            original_major_query = state.major_query
            state.major_query = self._normalize_unicode_text(state.major_query)
            # Strip degree phrases from major_query (e.g., "bachelor in chemistry" -> "chemistry")
            # Also detect degree_level from the phrase if missing
            text_lower = state.major_query.lower()
            if not state.degree_level:
                # Check for degree phrases and set degree_level accordingly
                if any(phrase in text_lower for phrase in ["bachelor", "bsc", "b.sc"]):
                    state.degree_level = "Bachelor"
                elif any(phrase in text_lower for phrase in ["master", "msc", "m.sc"]):
                    state.degree_level = "Master"
                elif any(phrase in text_lower for phrase in ["phd", "doctorate"]):
                    state.degree_level = "PhD"
            
            state.major_query = self._strip_degree_from_major_query(state.major_query)
            # If cleaned value is empty, set to None
            if not state.major_query or not state.major_query.strip():
                state.major_query = None

        # Intent classifier (rule-based) - detect early for follow-up resolution
        # Use normalized text for intent detection
        # IMPORTANT: duration_question must be checked BEFORE other intents
        intent = "general"
        if self._is_duration_question(user_message_normalized):
            intent = "duration_question"
        elif any(k in user_message_normalized for k in ["fee", "fees", "tuition", "cost", "price", "how much", "budget", "per year", "per month"]):
            intent = "fees_only"
        elif any(k in user_message_normalized for k in ["document", "documents", "required documents", "doc list", "paper", "papers", "materials", "what doc", "what documents"]):
            intent = "documents_only"
        elif any(k in user_message_normalized for k in ["scholarship", "waiver", "type-a", "first class", "stipend", "how to get", "how can i get"]):
            intent = "scholarship_only"
        elif any(k in user_message_normalized for k in ["eligible", "requirements", "age", "ielts", "hsk", "csca"]):
            intent = "eligibility_only"
        # fees_compare for cheapest/lowest cost queries (must include "lowest fees", "lowest tuition")
        if any(k in user_message_normalized for k in ["cheapest", "lowest", "lowest fees", "lowest tuition", "less fee", "low fee", "lowest cost", "less cost"]):
            intent = "fees_compare"
        print(f"DEBUG: Early intent detection: {intent}")

        # Defensive cleanup: if major_query contains fee/scholarship keywords, set to None
        # Do not put fee/scholarship questions into major_query
        follow_up_intents = ["fees_only", "fees_compare", "scholarship_only", "documents_only", "eligibility_only", "duration_question"]
        if state.major_query and intent in follow_up_intents:
            major_query_normalized = state.major_query.lower()
            
            # Keywords that indicate fee/scholarship questions, not major names
            problematic_keywords = [
                "tuition", "fee", "fees", "application fee", "cost", "price", "expense", "charge", "budget",
                "scholarship", "waiver", "stipend", "type-a", "type-b", "partial",
                "what", "what's", "what is", "how much", "tell me", "show me", "give me", "how to get", "how can i get"
            ]
            
            # Real major keywords that indicate an actual major name
            real_major_keywords = [
                "artificial intelligence", "computer science", "business administration", "engineering", "science",
                "physics", "chemistry", "mathematics", "biology", "economics", "finance", "management",
                "mechanical", "electrical", "civil", "chemical", "materials", "environmental"
            ]
            
            # Check if major_query contains problematic keywords or looks like a question
            contains_problematic = any(kw in major_query_normalized for kw in problematic_keywords)
            looks_like_question = len(state.major_query.split()) > 5 or "?" in state.major_query
            
            # Check if it contains real major keywords (explicit major name)
            contains_real_major = any(kw in major_query_normalized for kw in real_major_keywords)
            
            # If it looks like a question and doesn't contain real major keywords, set to None
            if (contains_problematic or looks_like_question) and not contains_real_major:
                print(f"DEBUG: Cleaning major_query - looks like fee/scholarship question without real major name, setting to None")
                print(f"DEBUG:   major_query was: '{state.major_query}'")
                state.major_query = None
        
        # Follow-up resolver: if intent is fees/scholarship/docs/eligibility and major_query is None or problematic,
        # use conversation memory (last_selected_university_id, last_selected_major_id, last_selected_program_intake_id)
        if intent in follow_up_intents and not state.major_query:
            print(f"DEBUG: Follow-up query detected (intent={intent}) - checking conversation memory...")
            # Check if user message contains real major keywords (not just fee/scholarship words)
            major_keywords = ["major", "program", "subject", "course", "degree", "bachelor", "master", "phd", "engineering", "science", "business", "arts", "artificial intelligence", "computer science", "business administration"]
            has_real_major = any(kw in user_message_normalized for kw in major_keywords)
            
            if not has_real_major:
                # No real major mentioned - use conversation memory
                if self.last_selected_university_id and not state.university:
                    uni_info = next((u for u in self.all_universities if u["id"] == self.last_selected_university_id), None)
                    if uni_info:
                        state.university = uni_info["name"]
                        print(f"DEBUG: Using conversation memory - last_selected_university_id={self.last_selected_university_id} ({uni_info['name']})")
                
                if self.last_selected_major_id and not state.major_query:
                    major_info = next((m for m in self.all_majors if m["id"] == self.last_selected_major_id), None)
                    if major_info:
                        # Don't set major_query, but we'll use major_id directly later
                        print(f"DEBUG: Using conversation memory - last_selected_major_id={self.last_selected_major_id} ({major_info['name']})")
                        # We'll handle this in the matching section by checking last_selected_major_id

        # Robust list-query detection (early, before language intent processing)
        # Use normalized text for list query detection
        list_patterns = [
            "program list", "list programs", "programs list", "list of programs",
            "show all programs", "show programs", "all programs",
            "what programs", "which programs", "available programs",
            "all majors", "list majors", "majors list",
            "english taught programs", "chinese taught programs", "taught programs",
            "programs for", "programs in", "programs at"
        ]
        list_trigger = any(pattern in user_message_normalized for pattern in list_patterns)
        
        # Also check for individual words that indicate list intent (more flexible matching)
        if not list_trigger:
            words = user_message_normalized.split()
            # Check for "list" + "program" in proximity
            if "list" in words and "program" in user_message_normalized:
                list_trigger = True
            # Check for "taught programs" or "programs" with teaching language indicator
            elif ("taught" in words or "taught" in user_message_normalized) and ("program" in user_message_normalized or "programs" in user_message_normalized):
                list_trigger = True
            # Check for "show" + "program" or "what" + "program"
            elif ("show" in words or "what" in words or "which" in words) and ("program" in user_message_normalized or "programs" in user_message_normalized):
                list_trigger = True
        
        is_list_query = list_trigger
        show_catalog = is_list_query
        
        # Debug: Print list query detection result
        print(f"DEBUG: List query detection - user_message='{user_message[:100]}', list_trigger={list_trigger}, is_list_query={is_list_query}")
        
        # If it's a list query, force major_query to None (don't try to match a specific major)
        if is_list_query:
            state.major_query = None
            print(f"DEBUG: Detected list query (patterns matched) - setting major_query to None")

        # Detect language intent early and auto-fill degree/major_query (only if not list query)
        # For fees_compare, do NOT set major_query - we want to query all Language intakes
        language_kw = ["language program", "language", "non-degree", "non degree", "chinese language", "english language", "mandarin course"]
        is_language_intent = any(kw in user_message.lower() for kw in language_kw)
        if is_language_intent and not is_list_query and intent != "fees_compare":
            state.degree_level = state.degree_level or "Language"
            if "chinese language" in user_message.lower():
                state.major_query = state.major_query or "chinese language (one year)"
            elif "english language" in user_message.lower():
                state.major_query = state.major_query or "english language program"
            else:
                state.major_query = state.major_query or "language program"
        elif is_language_intent and intent == "fees_compare":
            # For fees_compare, only set degree_level, NOT major_query
            state.degree_level = state.degree_level or "Language"
            state.major_query = None  # Don't filter by major name - query all Language intakes

        # If degree level is missing and not language intent, ask for it before proceeding
        if not state.degree_level and not is_language_intent:
            return {
                "response": (
                    "To tailor fees and requirements, please confirm the degree level "
                    "(Language/Non-degree, Bachelor, Master, PhD). "
                    "I will assume English teaching language unless you prefer Chinese."
                ),
                "used_db": False,
                "used_tavily": False,
                "sources": []
            }

        # List guardrails: Ask for missing required fields before querying
        if is_list_query:
            missing_fields = []
            if not state.intake_term:
                missing_fields.append("intake term (March/September)")
            if not state.degree_level:
                missing_fields.append("degree level (Language/Bachelor/Master/PhD)")
            
            if missing_fields:
                print(f"DEBUG: List guardrail triggered: missing {', '.join(missing_fields)}")
                return {
                    "response": f"To list and compare programs, please specify: {', '.join(missing_fields)}?",
                    "used_db": False,
                    "used_tavily": False,
                    "sources": []
                }
            
            # Fix 4: "Too broad" constraint - if both intake_term and major_query are missing for Language
            if state.degree_level and "Language" in str(state.degree_level):
                if not state.intake_term and not state.major_query:
                    print(f"DEBUG: List guardrail triggered: too broad (missing both intake_term and major_query for Language)")
                    # Provide a small preview: top 5 by soonest deadline or cheapest tuition
                    # Query a small sample to show preview
                    preview_intakes = self._get_upcoming_intakes(
                        current_date=current_date,
                        degree_level=state.degree_level,
                        university_id=None,
                        major_ids=None,
                        intake_term=None,
                        intake_year=None,
                        teaching_language=None
                    )[:5]  # Just get 5 for preview
                    
                    if preview_intakes:
                        preview_parts = ["Here's a preview of available language programs:\n"]
                        for intake in preview_intakes[:5]:
                            uni_name = intake.get('university_name', 'N/A')
                            major_name = intake.get('major_name', 'N/A')
                            intake_term = intake.get('intake_term', 'N/A')
                            deadline = intake.get('application_deadline', 'N/A')
                            preview_parts.append(f"- {uni_name} - {major_name} ({intake_term} intake, deadline: {deadline})")
                        preview_parts.append("\nWhich intake term (March/September) and which program type (one semester/one year)?")
                        return {
                            "response": "\n".join(preview_parts),
                            "used_db": True,
                            "used_tavily": False,
                            "sources": []
                        }
                    else:
                        return {
                            "response": "Which intake term (March/September) and which program type (one semester/one year)?",
                            "used_db": False,
                            "used_tavily": False,
                            "sources": []
                        }
            
            # For non-Language degree levels, require major_query
            if state.degree_level and "Language" not in str(state.degree_level) and not state.major_query:
                print(f"DEBUG: List guardrail triggered: missing major_query for degree_level={state.degree_level}")
                return {
                    "response": "Which major/subject should I list and compare (e.g., Chemistry, Computer Science, MBBS)?",
                    "used_db": False,
                    "used_tavily": False,
                    "sources": []
                }
        
        # Try to detect university from the current user message if absent
        if not state.university:
            detected_uni = self._detect_university_in_text(user_message)
            if detected_uni:
                state.university = detected_uni.get("name")
        
        # Use conversation memory for follow-up questions (fees/scholarship without repeating major/university)
        # If user asks fees/scholarship/documents/eligibility without specifying major/university, use last selected
        follow_up_keywords = ["fee", "fees", "tuition", "scholarship", "waiver", "documents", "requirements", "eligibility", "how can i get", "how to get"]
        is_follow_up = any(kw in user_message.lower() for kw in follow_up_keywords)
        if is_follow_up and not state.major_query and not state.university:
            if self.last_selected_university_id:
                uni_info = next((u for u in self.all_universities if u["id"] == self.last_selected_university_id), None)
                if uni_info:
                    state.university = uni_info["name"]
                    print(f"DEBUG: Using conversation memory - last_selected_university_id={self.last_selected_university_id} ({uni_info['name']})")
            if self.last_selected_major_id:
                major_info = next((m for m in self.all_majors if m["id"] == self.last_selected_major_id), None)
                if major_info:
                    state.major_query = major_info["name"]
                    print(f"DEBUG: Using conversation memory - last_selected_major_id={self.last_selected_major_id} ({major_info['name']})")

        # Deterministic matching: university first, then major_query
        match_notes: List[str] = []
        university_id = None
        major_ids: List[int] = []
        major_match_scores: Dict[int, float] = {}  # Store major match scores for ranking
        
        print(f"DEBUG: Starting deterministic matching - state.university={state.university}, state.major_query={state.major_query}, state.degree_level={state.degree_level}, is_list_query={is_list_query}")
        
        # Step 1: Match university if specified
        candidate_university_ids: Optional[List[int]] = None
        if state.university:
            matched_uni, uni_dict, uni_matches = self._fuzzy_match_university(state.university)
            if matched_uni and uni_dict:
                university_id = uni_dict["id"]
                print(f"DEBUG: Matched university '{state.university}' → '{uni_dict['name']}' (id={university_id})")
            elif uni_matches and len(uni_matches) > 1:
                match_notes.append(f"\nNOTE: Multiple university matches for '{state.university}':")
                for uni_match, score in uni_matches[:3]:
                    match_notes.append(f"  - {uni_match['name']} (match score: {score:.2f})")
                match_notes.append("Please ask which university they prefer.")
        elif state.city or state.province:
            # Location-based filtering: find universities by city/province
            candidate_university_ids = self._find_university_ids_by_location(state.city, state.province)
            if not candidate_university_ids:
                # No universities found in requested location
                location_str = ""
                if state.city and state.province:
                    location_str = f"in {state.city}, {state.province}"
                elif state.city:
                    location_str = f"in {state.city}"
                elif state.province:
                    location_str = f"in {state.province}"
                
                intake_term_str = (state.intake_term or "").title() if state.intake_term else ""
                degree_str = state.degree_level or "programs"
                
                return {
                    "response": f"I don't currently have {intake_term_str} {degree_str} {location_str} in our partner database. Would you like to search nearby cities, province-wide, or any city?",
                    "used_db": True,
                    "used_tavily": False,
                    "sources": []
                }
            print(f"DEBUG: Location filter matched {len(candidate_university_ids)} universities: {candidate_university_ids}")
        
        # Check for generic program queries (treat as list queries when university is specified)
        # This must happen after university_id is defined
        if not is_list_query and university_id and self._is_generic_program_query(user_message_normalized, state.major_query):
            print(f"DEBUG: Detected generic program query - treating as list query for university_id={university_id}")
            is_list_query = True
            state.major_query = None
            show_catalog = True
        
        # Fix 2: Generic "language / language course / language program" must not match a single major
        generic_language_phrases = ["language", "language course", "language program", "foundation program"]
        if (university_id and 
            state.intake_term and 
            state.major_query and 
            any(phrase in state.major_query.lower() for phrase in generic_language_phrases)):
            print(f"DEBUG: Detected generic language query '{state.major_query}' - treating as list query, clearing major_query")
            state.major_query = None
            is_list_query = True
            show_catalog = True
            # Ensure degree_level is Language if not set
            if not state.degree_level:
                state.degree_level = "Language"
        
        # Normalize intake term early (needed for special handling blocks)
        norm_intake_term = self._normalize_intake_term_enum(state.intake_term)
        
        # Special handling for doc/eligibility questions with language programs
        # If user specifies university + intake_term + degree_level Language but didn't specify a specific major,
        # fetch ALL Language programs for that university instead of fuzzy-matching "language program"
        if (intent in ["documents_only", "eligibility_only"] and 
            university_id and 
            state.intake_term and 
            state.degree_level and 
            "Language" in str(state.degree_level) and
            (not state.major_query or state.major_query in ["language program", "language", "chinese language", "english language"])):
            print(f"DEBUG: Doc/eligibility query for Language program - fetching ALL Language intakes for university_id={university_id}")
            # For doc/eligibility with Language programs, ignore state.teaching_language unless user explicitly requested it
            # Check if user explicitly mentioned a language
            explicit_language_keywords = ["english", "chinese", "mandarin", "english-taught", "chinese-taught", "中文", "汉语"]
            user_explicitly_requested_language = any(kw in user_message_normalized for kw in explicit_language_keywords)
            
            teaching_language_filter = state.teaching_language if user_explicitly_requested_language else None
            print(f"DEBUG: user_explicitly_requested_language={user_explicitly_requested_language}, teaching_language_filter={teaching_language_filter}")
            
            # Query all Language intakes for this university + intake_term
            all_language_intakes = self._get_upcoming_intakes(
                current_date=current_date,
                degree_level=state.degree_level,
                university_id=university_id,
                major_ids=None,  # Don't filter by major - get all Language programs
                intake_term=norm_intake_term,
                intake_year=state.intake_year,
                teaching_language=teaching_language_filter,  # Only filter if user explicitly requested it
                university_ids=candidate_university_ids if not university_id else None
            )
            
            if len(all_language_intakes) > 1:
                # Multiple Language programs found - check for distinct teaching languages
                print(f"DEBUG: Found {len(all_language_intakes)} Language programs - checking for distinct teaching languages")
                
                # Get distinct teaching languages
                distinct_languages = set()
                for intake in all_language_intakes:
                    eff_lang = intake.get('effective_teaching_language') or intake.get('teaching_language')
                    if eff_lang:
                        distinct_languages.add(eff_lang)
                
                print(f"DEBUG: Distinct teaching languages found: {distinct_languages}")
                
                uni_name = next((u["name"] for u in self.all_universities if u["id"] == university_id), "this university")
                intake_term_str = norm_intake_term.value.title() if norm_intake_term else state.intake_term
                intake_year_str = f" {state.intake_year}" if state.intake_year else ""
                
                response_parts = [
                    f"I found {len(all_language_intakes)} {intake_term_str}{intake_year_str} language program(s) at {uni_name}:"
                ]
                
                for idx, intake in enumerate(all_language_intakes[:10], 1):  # Limit to 10 for readability
                    eff_lang = intake.get('effective_teaching_language') or intake.get('teaching_language') or 'N/A'
                    deadline_str = intake.get('application_deadline', 'N/A')
                    response_parts.append(
                        f"\n{idx}) {intake.get('major_name', 'N/A')} — Teaching language: {eff_lang} — deadline: {deadline_str}"
                    )
                
                if len(all_language_intakes) > 10:
                    response_parts.append(f"\n... and {len(all_language_intakes) - 10} more program(s)")
                
                # If user asked "documents required" and there are <=2 items, show both document lists
                # Also show if there are multiple distinct teaching languages (e.g., English + Chinese)
                should_show_both_docs = (intent == "documents_only" and len(all_language_intakes) <= 2) or (len(distinct_languages) > 1 and len(all_language_intakes) <= 2)
                
                if should_show_both_docs:
                    # Load documents for these intakes
                    intake_ids_for_docs = [i['id'] for i in all_language_intakes]
                    docs_map_temp = self._get_program_documents_batch(intake_ids_for_docs)
                    
                    response_parts.append("\n\nDocument requirements for each program:")
                    for intake in all_language_intakes:
                        response_parts.append(f"\n--- {intake.get('major_name', 'N/A')} ({intake.get('effective_teaching_language', 'N/A')}-taught) ---")
                        # Get documents for this intake
                        intake_docs = docs_map_temp.get(intake['id'], [])
                        # Also merge with documents_required text and structured fields
                        if intake.get('documents_required'):
                            doc_names_from_text = [d.strip() for d in intake.get('documents_required', '').split(',') if d.strip()]
                            existing_doc_names = {doc.get('name', '').lower() for doc in intake_docs}
                            for doc_name in doc_names_from_text:
                                if doc_name.lower() not in existing_doc_names:
                                    intake_docs.append({
                                        'name': doc_name.title(),
                                        'is_required': True,
                                        'rules': None,
                                        'applies_to': None
                                    })
                        
                        # Add structured fields - always show bank statement requirement
                        bank_req = intake.get('bank_statement_required')
                        bank_statement_already_in_docs = any('bank' in d.get('name', '').lower() and 'statement' in d.get('name', '').lower() for d in intake_docs)
                        
                        if not bank_statement_already_in_docs:
                            if bank_req is True:
                                bank_amount = intake.get('bank_statement_amount')
                                bank_currency = intake.get('bank_statement_currency', 'CNY')
                                bank_note = intake.get('bank_statement_note', '')
                                bank_rules = f"{bank_amount} {bank_currency}" if bank_amount else ""
                                if bank_note:
                                    bank_rules += f" ({bank_note})" if bank_rules else bank_note
                                intake_docs.append({
                                    'name': 'Bank Statement',
                                    'is_required': True,
                                    'rules': bank_rules if bank_rules else None,
                                    'applies_to': None
                                })
                            elif bank_req is False:
                                # Explicitly say not required
                                intake_docs.append({
                                    'name': 'Bank Statement',
                                    'is_required': False,
                                    'rules': 'Not required',
                                    'applies_to': None
                                })
                            else:
                                # NULL - not specified
                                intake_docs.append({
                                    'name': 'Bank Statement',
                                    'is_required': None,
                                    'rules': 'Not specified',
                                    'applies_to': None
                                })
                        
                        if intake_docs:
                            for doc in intake_docs:
                                doc_name = doc.get('name', 'N/A')
                                is_required = doc.get('is_required')
                                
                                # Special handling for bank statement with True/False/NULL
                                if doc_name.lower() == 'bank statement':
                                    if is_required is True:
                                        req_str = "Required"
                                    elif is_required is False:
                                        req_str = "Not required"
                                    else:
                                        req_str = "Not specified"
                                else:
                                    req_str = "Required" if is_required else "Optional"
                                
                                doc_line = f"  - {doc_name} ({req_str})"
                                if doc.get('rules') and doc.get('rules') not in ['Not required', 'Not specified']:
                                    doc_line += f": {doc['rules']}"
                                response_parts.append(doc_line)
                        else:
                            response_parts.append("  (No documents specified in database)")
                else:
                    response_parts.append("\nWhich one do you want documents/requirements for?")
                
                return {
                    "response": "\n".join(response_parts),
                    "used_db": True,
                    "used_tavily": False,
                    "sources": []
                }
            elif len(all_language_intakes) == 1:
                # Single Language program - use it
                major_ids = [all_language_intakes[0]["major_id"]]
                print(f"DEBUG: Single Language program found - using major_id={major_ids[0]}")
                state.major_query = None  # Clear major_query to avoid fuzzy matching
        
        # Step 2: Match major_query if specified (deterministic matching after state extraction)
        if state.major_query and not is_list_query:
            cleaned_major_query = state.major_query
            if university_id:
                uni_dict = next((u for u in self.all_universities if u["id"] == university_id), None)
                cleaned_major_query = self._strip_university_from_major(state.major_query, uni_dict) if uni_dict else state.major_query
            
            matched_major, major_dict, major_matches = self._fuzzy_match_major(
                cleaned_major_query,
                degree_level=state.degree_level,
                university_id=university_id,
                top_k=20  # Collect up to 20 candidates
            )
            
            # Store match scores for ranking later
            if major_matches:
                major_match_scores.update({m[0]["id"]: m[1] for m in major_matches})
            
            if major_matches:
                # Check if we have high confidence (best match score >= 0.8)
                best_score = major_matches[0][1] if major_matches else 0.0
                
                if matched_major and major_dict and best_score >= 0.8:
                    if not university_id:
                        # When university is NOT specified, use ALL candidate major_ids
                        major_ids = [m[0]["id"] for m in major_matches]
                        print(f"DEBUG: High confidence match for '{cleaned_major_query}' → {len(major_ids)} candidates (university not specified, using all matches)")
                        for major_match, score in major_matches[:5]:  # Show top 5 for debug
                            uni_name = next((u["name"] for u in self.all_universities if u["id"] == major_match.get("university_id")), "Unknown")
                            print(f"DEBUG:   - '{major_match['name']}' at {uni_name} (id={major_match['id']}, score={score:.3f})")
                    else:
                        # When university IS specified, use best match
                        major_ids = [major_dict["id"]]
                        print(f"DEBUG: High confidence match for '{cleaned_major_query}' → '{major_dict['name']}' (id={major_dict['id']}, university_id={major_dict.get('university_id')}, degree_level={major_dict.get('degree_level')})")
                else:
                    # Medium/low confidence: show top 3 options to user
                    top_3 = major_matches[:3]
                    major_ids = [m[0]["id"] for m in top_3]
                    match_notes.append(f"\nNOTE: Multiple major matches for '{state.major_query}':")
                    for major_match, score in top_3:
                        uni_name = next((u["name"] for u in self.all_universities if u["id"] == major_match.get("university_id")), "Unknown")
                        match_notes.append(f"  - {major_match['name']} at {uni_name} (match score: {score:.2f})")
                    match_notes.append("Please pick one from the list above.")
                    print(f"DEBUG: Medium confidence - showing top 3 matches for '{cleaned_major_query}': {[m[0]['name'] for m in top_3]}")
            else:
                print(f"DEBUG: NO fuzzy matches found for major_query '{cleaned_major_query}' (degree_level={state.degree_level}, university_id={university_id})")
        
        # Use conversation memory for follow-up queries if no major_ids found yet
        if not major_ids and intent in follow_up_intents and self.last_selected_major_id:
            # Verify the major still matches current filters
            major_info = next((m for m in self.all_majors if m["id"] == self.last_selected_major_id), None)
            if major_info:
                # Check if it matches current university and degree_level filters
                matches_filters = True
                if university_id and major_info.get("university_id") != university_id:
                    matches_filters = False
                if state.degree_level and major_info.get("degree_level") and state.degree_level.lower() not in str(major_info.get("degree_level")).lower():
                    matches_filters = False
                
                if matches_filters:
                    major_ids = [self.last_selected_major_id]
                    print(f"DEBUG: Using conversation memory major_id={self.last_selected_major_id} for follow-up query (intent={intent})")
                # If major_query is too generic (like "science", "engineering", "business") and we have university+degree,
                # suggest closest majors available at that university/degree
                if university_id and state.degree_level and cleaned_major_query:
                    # Check if the query is generic (short, common words)
                    generic_keywords = ["science", "engineering", "business", "arts", "medicine", "law", "education", "technology", "management"]
                    is_generic = cleaned_major_query.lower() in generic_keywords or len(cleaned_major_query.split()) == 1
                    
                    if is_generic:
                        print(f"DEBUG: Generic major_query '{cleaned_major_query}' detected - fetching available majors at university+degree")
                        # Fetch available majors for this university+degree
                        available_majors = self._get_majors_for_list_query(
                            university_id=university_id,
                            degree_level=state.degree_level,
                            teaching_language=state.teaching_language,
                            university_ids=candidate_university_ids if not university_id else None
                        )
                        
                        if available_majors:
                            # Filter majors that might be related to the generic query
                            # For "science", look for Physics, Chemistry, Math, Materials, etc.
                            related_majors = []
                            query_lower = cleaned_major_query.lower()
                            for major in available_majors:
                                major_name_lower = major["name"].lower()
                                # Check if major name contains words related to the generic query
                                if query_lower in major_name_lower or any(word in major_name_lower for word in ["physics", "chemistry", "math", "biology", "materials", "engineering"] if query_lower == "science"):
                                    related_majors.append(major)
                                elif query_lower == "engineering" and any(word in major_name_lower for word in ["engineering", "technology", "mechanical", "electrical", "computer"]):
                                    related_majors.append(major)
                                elif query_lower == "business" and any(word in major_name_lower for word in ["business", "management", "economics", "commerce", "finance", "marketing"]):
                                    related_majors.append(major)
                            
                            # If no related majors found, use all available majors
                            if not related_majors:
                                related_majors = available_majors
                            
                            # Limit to 5-10 closest majors
                            related_majors = related_majors[:10]
                            
                            if related_majors:
                                uni_name = next((u["name"] for u in self.all_universities if u["id"] == university_id), "this university")
                                degree_str = f"{state.degree_level} " if state.degree_level else ""
                                lang_str = f"{state.teaching_language}-taught " if state.teaching_language else ""
                                
                                response_parts = [
                                    f"'{cleaned_major_query}' is quite broad. Here are the closest {lang_str}{degree_str}programs available at {uni_name}:"
                                ]
                                
                                for major in related_majors:
                                    response_parts.append(f"  - {major['name']}")
                                if len(available_majors) > len(related_majors):
                                    response_parts.append(f"  ... and {len(available_majors) - len(related_majors)} more programs")
                                
                                response_parts.append("\nWhich one would you like information about?")
                                
                                return {
                                    "response": "\n".join(response_parts),
                                    "used_db": True,
                                    "used_tavily": False,
                                    "sources": []
                                }
        
        # Handle case when major_query is None but we have university+degree+language with multiple majors
        if not is_list_query and not state.major_query and university_id and state.degree_level:
            print(f"DEBUG: major_query is None but have university+degree - checking for multiple majors...")
            # Query majors for this university+degree+language
            majors_for_university = self._get_majors_for_list_query(
                university_id=university_id,
                degree_level=state.degree_level,
                teaching_language=state.teaching_language,
                university_ids=candidate_university_ids if not university_id else None
            )
            
            if len(majors_for_university) > 1:
                # Multiple majors exist - list them and ask which one
                uni_name = next((u["name"] for u in self.all_universities if u["id"] == university_id), "this university")
                degree_str = f"{state.degree_level} " if state.degree_level else ""
                lang_str = f"{state.teaching_language}-taught " if state.teaching_language else ""
                
                response_parts = [
                    f"Multiple {lang_str}{degree_str}programs are available at {uni_name}. Which major would you like information about?"
                ]
                
                # List unique majors (limit to 10)
                for major in majors_for_university[:10]:
                    response_parts.append(f"  - {major['name']}")
                if len(majors_for_university) > 10:
                    response_parts.append(f"  ... and {len(majors_for_university) - 10} more")
                
                return {
                    "response": "\n".join(response_parts),
                    "used_db": True,
                    "used_tavily": False,
                    "sources": []
                }
            elif len(majors_for_university) == 1:
                # Single major - use it
                major_ids = [majors_for_university[0]["id"]]
                print(f"DEBUG: Single major found for university+degree - using major_id={major_ids[0]}")
            else:
                print(f"DEBUG: No majors found for university+degree combination")

        # For list queries: if university is specified, query by university (no major required)
        # If university is NOT specified, gather broader major ids by topic
        if is_list_query:
            if university_id:
                # University list query: don't require major, query all programs for that university
                major_ids = None  # No major filter - show all programs
                print(f"DEBUG: University list query - querying all programs for university_id={university_id} (no major filter)")
            else:
                # General list query: try to find majors by topic if user mentioned a subject
                topic = quick.get("major_text")  # Only use quick extraction, not full user message
                if topic:
                    major_ids = self._find_major_ids_by_topic(topic, degree_level=state.degree_level)
                    if len(major_ids) > 300:
                        major_ids = major_ids[:300]
                    print(f"DEBUG: General list query major_ids matched: {len(major_ids)} topic='{topic}'")
                else:
                    # No topic mentioned - query all majors (will be filtered by degree_level if provided)
                    major_ids = None
                    print(f"DEBUG: General list query - no topic mentioned, will query all programs")

        # If still nothing to filter by (non-list), ask for missing info
        if not is_list_query and not university_id and not major_ids:
            # If user provided a major_query but no match was found, tell them explicitly
            if state.major_query:
                return {
                    "response": (
                        f"I don't have any {state.degree_level or 'programs'} matching '{state.major_query}' "
                        f"in our partner universities database. "
                        f"Could you specify a different major/subject, or would you like me to suggest similar programs?"
                    ),
                    "used_db": True,
                    "used_tavily": False,
                    "sources": []
                }
            # Smart fallback: only ask for missing pieces, not already-known fields
            missing_parts = []
            if not state.degree_level:
                missing_parts.append("degree level (Bachelor/Master/PhD/Language)")
            if not state.major_query:
                missing_parts.append("major/subject")
            if not state.intake_term and not state.intake_year:
                missing_parts.append("intake term/year")
            
            if missing_parts:
                return {
                    "response": (
                        f"I can share exact fees and documents once you confirm:\n"
                        f"- {', '.join(missing_parts)}\n"
                        f"- I will suggest partner universities based on your preferences.\n"
                        f"Please provide the missing information so I can narrow it down."
                    ),
                    "used_db": False,
                    "used_tavily": False,
                    "sources": []
                }
            else:
                # All key fields present but no matches - ask if they're flexible
                return {
                    "response": (
                        f"I have your preferences (degree: {state.degree_level}, intake: {state.intake_term or 'any'}). "
                        f"Are you flexible on the teaching language (English/Chinese) or would you like me to show both options?"
                    ),
                    "used_db": False,
                    "used_tavily": False,
                    "sources": []
                }

        # Intent already detected earlier - just update fees_compare if needed
        # fees_compare for cheapest/lowest cost queries
        if any(k in user_message_normalized for k in ["cheapest", "lowest", "less fee", "low fee", "lowest cost", "less cost"]) and is_language_intent:
            intent = "fees_compare"

        include_total = any(k in user_message_normalized for k in ["total", "overall", "year 1 total", "year one total"])
        year1_total_included = False  # We no longer compute totals unless explicitly added later
        print(f"DEBUG: Detected intent={intent}, include_total_requested={include_total}")

        # Teaching language: only set if user explicitly stated preference
        # Do NOT infer Chinese just because user wrote "language program"
        # Explicit language keywords: english, chinese, mandarin, 中文, 汉语, english-taught, chinese-taught
        explicit_language_keywords = {
            "english": "English",
            "english-taught": "English",
            "english taught": "English",
            "chinese": "Chinese",
            "chinese-taught": "Chinese",
            "chinese taught": "Chinese",
            "mandarin": "Chinese",
            "中文": "Chinese",
            "汉语": "Chinese"
        }
        
        # Check if user explicitly mentioned a teaching language
        user_has_explicit_language = False
        detected_language = None
        for keyword, lang in explicit_language_keywords.items():
            if keyword in user_message_normalized:
                detected_language = lang
                user_has_explicit_language = True
                print(f"DEBUG: Explicit language keyword detected: '{keyword}' → {lang}")
                break
        
        # Post-processing: clear teaching_language if it was inferred incorrectly
        # For doc/eligibility with Language programs, only keep teaching_language if explicitly mentioned
        if intent in ["documents_only", "eligibility_only"] and state.degree_level and "Language" in str(state.degree_level):
            if user_has_explicit_language:
                state.teaching_language = detected_language
                print(f"DEBUG: Keeping explicit teaching_language={detected_language} for Language program doc/eligibility query")
            else:
                # Clear teaching_language - user didn't explicitly request a language
                state.teaching_language = None
                print(f"DEBUG: Clearing teaching_language for Language program doc/eligibility query (not explicitly mentioned)")
        elif not state.teaching_language:
            # For other intents, set if explicitly mentioned
            if user_has_explicit_language:
                state.teaching_language = detected_language
            elif "both" in user_message_normalized:
                state.teaching_language = None  # no filter - show both
            # If user didn't specify, keep teaching_language = None (no filter)

        # For list queries: Check majors FIRST (separate program existence from intake availability)
        # Only enforce "upcoming deadline" rule when user asks about applying, fees, scholarships, or deadlines
        requires_upcoming_deadline = any(k in user_message_normalized for k in ["apply", "application", "fee", "fees", "tuition", "cost", "price", "scholarship", "deadline", "deadline"])
        
        # norm_intake_term already computed earlier (before special handling blocks)
        
        # For list queries, check majors first
        if is_list_query:
            print(f"DEBUG: List query detected - checking majors first (requires_upcoming_deadline={requires_upcoming_deadline})")
            majors_list = self._get_majors_for_list_query(
                university_id=university_id,
                degree_level=state.degree_level,
                teaching_language=state.teaching_language,
                university_ids=candidate_university_ids if not university_id else None
            )
            
            if majors_list:
                print(f"DEBUG: Found {len(majors_list)} majors for list query")
                # Extract major_ids from majors_list
                major_ids_from_list = [m["id"] for m in majors_list]
                # Now check for upcoming intakes
                t_db_start = time.perf_counter()
                filtered_intakes = self._get_upcoming_intakes(
                    current_date=current_date,
                    degree_level=state.degree_level,
                    university_id=university_id,
                    major_ids=major_ids_from_list,  # Use majors from the list
                    intake_term=norm_intake_term,
                    intake_year=state.intake_year,
                    teaching_language=state.teaching_language,
                    university_ids=candidate_university_ids if not university_id else None
                )
                t_db_end = time.perf_counter()
                print(f"DEBUG: DB intakes load time: {(t_db_end - t_db_start):.3f}s, count={len(filtered_intakes)}")
                
                # If majors exist but no upcoming intakes, always show majors (regardless of requires_upcoming_deadline)
                if not filtered_intakes:
                    # List the majors and explain deadlines aren't open
                    uni_name = next((u["name"] for u in self.all_universities if u["id"] == university_id), "this university") if university_id else "partner universities"
                    degree_str = f"{state.degree_level} " if state.degree_level else ""
                    lang_str = f"{state.teaching_language}-taught " if state.teaching_language else ""
                    intake_term_str = f" for {norm_intake_term.value.title()} intake" if norm_intake_term else ""
                    
                    response_parts = [
                        f"These {lang_str}{degree_str}programs exist at {uni_name}{intake_term_str}:"
                    ]
                    
                    # List majors (limit to 20 for readability)
                    for major in majors_list[:20]:
                        response_parts.append(f"  - {major['name']}")
                    if len(majors_list) > 20:
                        response_parts.append(f"  ... and {len(majors_list) - 20} more programs")
                    
                    # Explain about deadlines
                    if norm_intake_term:
                        response_parts.append(f"\n{norm_intake_term.value.title()} intake deadlines are not currently open / not yet available.")
                    else:
                        response_parts.append(f"\nIntake deadlines are not currently open / not yet available.")
                    
                    # If user asked about fees/deadlines, add note that they can't be provided
                    if requires_upcoming_deadline:
                        response_parts.append("Fees, deadlines, and application details cannot be provided until intake deadlines are open.")
                    else:
                        response_parts.append("Please check back later or ask about specific program details.")
                    
                    return {
                        "response": "\n".join(response_parts),
                        "used_db": True,
                        "used_tavily": False,
                        "sources": []
                    }
            else:
                # No majors found - continue to normal flow
                print(f"DEBUG: No majors found for list query - will continue to normal flow")
        
        # Filter intakes in SQL to reduce load (for non-list queries or list queries that need upcoming deadlines)
        t_db_start = time.perf_counter()
        
        # For fees_compare with language programs, do NOT filter by major_ids - query all Language intakes
        # Then filter by "one year" vs "one semester" AFTER retrieving intakes if user specified
        if intent == "fees_compare" and is_language_intent and state.degree_level and "Language" in str(state.degree_level):
            print(f"DEBUG: fees_compare for Language program - clearing major_ids to query all Language intakes")
            major_ids = None  # Don't filter by major name - query all Language intakes
        
        # Show matched major details before querying
        if major_ids:
            matched_major_details = []
            for mid in major_ids:
                major_info = next((m for m in self.all_majors if m["id"] == mid), None)
                if major_info:
                    uni_name = next((u["name"] for u in self.all_universities if u["id"] == major_info.get("university_id")), "Unknown")
                    matched_major_details.append(f"id={mid}, name='{major_info.get('name')}', degree_level={major_info.get('degree_level')}, university='{uni_name}' (id={major_info.get('university_id')})")
            print(f"DEBUG: Querying intakes for matched majors: {', '.join(matched_major_details)}")
        
        filtered_intakes = self._get_upcoming_intakes(
            current_date=current_date,
            degree_level=state.degree_level,
            university_id=university_id,
            major_ids=major_ids if major_ids else None,
            intake_term=norm_intake_term,
            intake_year=state.intake_year,
            teaching_language=state.teaching_language,
            university_ids=candidate_university_ids if not university_id else None
        )
        
        # Apply duration preference filtering if specified
        if state.duration_years_target is not None and filtered_intakes:
            print(f"DEBUG: Applying duration filter - target={state.duration_years_target} years")
            filtered_by_duration = []
            for intake in filtered_intakes:
                major_name = intake.get('major_name', '')
                major_info = next((m for m in self.all_majors if m["id"] == intake.get('major_id')), None)
                
                # Check duration_years from major if available
                duration_years = None
                if major_info and major_info.get('duration_years'):
                    duration_years = major_info.get('duration_years')
                else:
                    # Infer from major name
                    duration_years = self._infer_duration_from_major_name(major_name)
                
                # Match if duration matches target (with tolerance for 0.5 vs 1.0)
                if duration_years is not None:
                    if abs(duration_years - state.duration_years_target) < 0.1:  # Allow small tolerance
                        filtered_by_duration.append(intake)
                else:
                    # If duration cannot be inferred, include it (user can decide)
                    filtered_by_duration.append(intake)
            
            if filtered_by_duration:
                print(f"DEBUG: Duration filter: {len(filtered_intakes)} -> {len(filtered_by_duration)} intakes")
                filtered_intakes = filtered_by_duration
            else:
                print(f"DEBUG: Duration filter: No intakes match duration preference {state.duration_years_target} years")
        
        # For fees_compare, if user asked "one year" vs "one semester", filter by major name AFTER retrieving intakes (legacy support)
        if intent == "fees_compare" and filtered_intakes and state.duration_years_target is None:
            if "one year" in user_message_normalized or "1 year" in user_message_normalized:
                print(f"DEBUG: Filtering fees_compare results to 'One Year' programs")
                filtered_intakes = [i for i in filtered_intakes if "one year" in i.get('major_name', '').lower() or "1 year" in i.get('major_name', '').lower()]
            elif "one semester" in user_message_normalized or "1 semester" in user_message_normalized or "six month" in user_message_normalized or "6 month" in user_message_normalized:
                print(f"DEBUG: Filtering fees_compare results to 'One Semester' / 'Six Month' programs")
                filtered_intakes = [i for i in filtered_intakes if any(term in i.get('major_name', '').lower() for term in ["one semester", "1 semester", "six month", "6 month"])]
        t_db_end = time.perf_counter()
        print(f"DEBUG: Intent chosen: {intent}")
        print(f"DEBUG: Programs matched before filters: {len(major_ids) if major_ids else 'all'} major(s)")
        print(f"DEBUG: Programs matched after filters (upcoming intakes): {len(filtered_intakes)} intake(s)")
        print(f"DEBUG: DB intakes load time: {(t_db_end - t_db_start):.3f}s, count={len(filtered_intakes)} (matched majors: {len(major_ids) if major_ids else 0}, intake_term_enum={norm_intake_term})")

        # Fallback to latest intakes for fees/docs/scholarship intents if upcoming returns 0
        using_latest_intakes = False
        fallback_intents = ["fees_only", "fees_compare", "scholarship_only", "documents_only", "eligibility_only"]
        if intent in fallback_intents and not filtered_intakes:
            print(f"DEBUG: {intent} query returned 0 upcoming intakes - falling back to latest intakes (any deadline)...")
            print(f"REGRESSION_TEST: Fallback triggered for intent={intent}, university_id={university_id}, major_ids={major_ids}")
            filtered_intakes = self._get_latest_intakes_any_deadline(
                degree_level=state.degree_level,
                university_id=university_id,
                major_ids=major_ids if major_ids else None,
                intake_term=norm_intake_term,
                intake_year=state.intake_year,
                teaching_language=state.teaching_language,
                limit_per_major=3,
                university_ids=candidate_university_ids if not university_id else None
            )
            if filtered_intakes:
                using_latest_intakes = True
                print(f"DEBUG: Fallback to latest intakes returned {len(filtered_intakes)} intake(s)")
                print(f"REGRESSION_TEST: Fallback successful - using_latest_intakes=True, intake_count={len(filtered_intakes)}")
            else:
                print(f"REGRESSION_TEST: Fallback returned 0 intakes - no matching programs in DB")
        
        # REGRESSION_TEST: Verify location filtering correctness
        if state.city and filtered_intakes and candidate_university_ids:
            # Check that all results are actually in the requested city
            city_normalized = state.city.lower().strip()
            mismatched_cities = []
            for intake in filtered_intakes:
                uni_id = intake.get('university_id')
                uni_info = next((u for u in self.all_universities if u["id"] == uni_id), None)
                if uni_info:
                    uni_city = (uni_info.get("city") or "").lower().strip()
                    if uni_city != city_normalized:
                        mismatched_cities.append(f"university_id={uni_id} ({uni_info.get('name')}) in city='{uni_city}' (expected '{city_normalized}')")
            
            if mismatched_cities:
                print(f"REGRESSION_TEST WARNING: Location filter mismatch! Requested city='{state.city}', but found intakes in different cities:")
                for mismatch in mismatched_cities[:5]:  # Limit to first 5
                    print(f"  - {mismatch}")
                # Fall back to "no programs found in city" response
                intake_term_str = norm_intake_term.value.title() if norm_intake_term else ""
                degree_str = state.degree_level or "programs"
                return {
                    "response": f"I don't currently have {intake_term_str} {degree_str} in {state.city} in our partner database. Would you like to search nearby cities, province-wide, or any city?",
                    "used_db": True,
                    "used_tavily": False,
                    "sources": []
                }
            else:
                print(f"REGRESSION_TEST: Location filter verified - all {len(filtered_intakes)} intakes are in requested city '{state.city}'")

        # Handle fee queries when major_query is None but we have university+degree
        # For fee/scholarship queries, if no major_ids and we have university+degree, fetch majors and auto-select or list
        using_latest_intakes = False  # Initialize early for fee query handling
        if not is_list_query and not major_ids and university_id and state.degree_level and intent in ["fees_only", "fees_compare", "scholarship_only"]:
            print(f"DEBUG: Fee/scholarship query with no major_ids but have university+degree - fetching available majors...")
            available_majors = self._get_majors_for_list_query(
                university_id=university_id,
                degree_level=state.degree_level,
                teaching_language=state.teaching_language
            )
            
            if len(available_majors) == 1:
                # Single major - auto-select it and show fees from latest intake
                major_ids = [available_majors[0]["id"]]
                print(f"DEBUG: Single major found - auto-selecting major_id={major_ids[0]}")
                # Re-query with this major_id
                filtered_intakes = self._get_upcoming_intakes(
                    current_date=current_date,
                    degree_level=state.degree_level,
                    university_id=university_id,
                    major_ids=major_ids,
                    intake_term=norm_intake_term,
                    intake_year=state.intake_year,
                    teaching_language=state.teaching_language
                )
                # If no upcoming intakes, try latest intakes
                if not filtered_intakes:
                    filtered_intakes = self._get_latest_intakes_any_deadline(
                        degree_level=state.degree_level,
                        university_id=university_id,
                        major_ids=major_ids,
                        intake_term=norm_intake_term,
                        intake_year=state.intake_year,
                        teaching_language=state.teaching_language,
                        limit_per_major=3,
                        university_ids=candidate_university_ids if not university_id else None
                    )
                    if filtered_intakes:
                        using_latest_intakes = True
            elif len(available_majors) > 1:
                # Multiple majors - check if fees are identical, otherwise list them
                print(f"DEBUG: Multiple majors found ({len(available_majors)}) - checking if fees are identical...")
                # Get latest intakes for all majors to compare fees
                latest_intakes_all = self._get_latest_intakes_any_deadline(
                    degree_level=state.degree_level,
                    university_id=university_id,
                    major_ids=[m["id"] for m in available_majors],
                    intake_term=norm_intake_term,
                    intake_year=state.intake_year,
                    teaching_language=state.teaching_language,
                    limit_per_major=1,  # Just need one per major to compare
                    university_ids=candidate_university_ids if not university_id else None
                )
                
                if latest_intakes_all:
                    # Group by major and extract fee info
                    from collections import defaultdict
                    by_major = defaultdict(list)
                    for intake in latest_intakes_all:
                        by_major[intake["major_id"]].append(intake)
                    
                    # Get unique fee combinations
                    fee_combinations = {}
                    for major_id, major_intakes in by_major.items():
                        if major_intakes:
                            latest = major_intakes[0]
                            fee_key = (
                                latest.get("tuition_per_year"),
                                latest.get("tuition_per_semester"),
                                latest.get("application_fee")
                            )
                            if fee_key not in fee_combinations:
                                fee_combinations[fee_key] = []
                            major_name = next((m["name"] for m in available_majors if m["id"] == major_id), "Unknown")
                            fee_combinations[fee_key].append(major_name)
                    
                    # If all majors have same fees, use them all
                    if len(fee_combinations) == 1:
                        major_ids = [m["id"] for m in available_majors]
                        print(f"DEBUG: All {len(major_ids)} majors have identical fees - using all majors")
                        filtered_intakes = latest_intakes_all
                        using_latest_intakes = True
                    else:
                        # Fees differ - list majors and ask user to choose
                        uni_name = next((u["name"] for u in self.all_universities if u["id"] == university_id), "this university")
                        degree_str = f"{state.degree_level} " if state.degree_level else ""
                        lang_str = f"{state.teaching_language}-taught " if state.teaching_language else ""
                        intake_term_str = f" for {norm_intake_term.value.title()} intake" if norm_intake_term else ""
                        
                        response_parts = [
                            f"Multiple {lang_str}{degree_str}programs are available at {uni_name}{intake_term_str}. Fees differ by major. Which major would you like fee information for?"
                        ]
                        
                        for major in available_majors[:10]:
                            response_parts.append(f"  - {major['name']}")
                        if len(available_majors) > 10:
                            response_parts.append(f"  ... and {len(available_majors) - 10} more")
                        
                        return {
                            "response": "\n".join(response_parts),
                            "used_db": True,
                            "used_tavily": False,
                            "sources": []
                        }
                else:
                    # No intakes found - list majors anyway
                    uni_name = next((u["name"] for u in self.all_universities if u["id"] == university_id), "this university")
                    degree_str = f"{state.degree_level} " if state.degree_level else ""
                    lang_str = f"{state.teaching_language}-taught " if state.teaching_language else ""
                    
                    response_parts = [
                        f"Multiple {lang_str}{degree_str}programs are available at {uni_name}. Which major would you like information about?"
                    ]
                    
                    for major in available_majors[:10]:
                        response_parts.append(f"  - {major['name']}")
                    if len(available_majors) > 10:
                        response_parts.append(f"  ... and {len(available_majors) - 10} more")
                    
                    return {
                        "response": "\n".join(response_parts),
                        "used_db": True,
                        "used_tavily": False,
                        "sources": []
                    }

        # Fallback for fee/scholarship/documents/eligibility queries: if no upcoming intakes, use latest intakes
        # Note: using_latest_intakes is initialized earlier for fee query handling
        fallback_intents = ["fees_only", "fees_compare", "scholarship_only", "documents_only", "eligibility_only"]
        fallback_used = False
        if intent in fallback_intents and not filtered_intakes:
            print(f"DEBUG: {intent} query with no upcoming intakes - falling back to latest intakes (any deadline)...")
            fallback_used = True
            latest_intakes = self._get_latest_intakes_any_deadline(
                degree_level=state.degree_level,
                university_id=university_id,
                major_ids=major_ids if major_ids else None,
                intake_term=norm_intake_term,
                intake_year=state.intake_year,
                teaching_language=state.teaching_language,
                limit_per_major=3,
                university_ids=candidate_university_ids if not university_id else None
            )
            
            if latest_intakes:
                print(f"DEBUG: Found {len(latest_intakes)} latest intakes for {intent} query fallback")
                # Check if multiple majors exist
                from collections import defaultdict
                by_major = defaultdict(list)
                for intake in latest_intakes:
                    by_major[intake["major_id"]].append(intake)
                
                # For fee queries: check if fees differ by major
                if intent in ["fees_only", "fees_compare"]:
                    fee_combinations = {}
                    for major_id, major_intakes in by_major.items():
                        latest = major_intakes[0]
                        fee_key = (
                            latest.get("tuition_per_year"),
                            latest.get("tuition_per_semester"),
                            latest.get("application_fee")
                        )
                        if fee_key not in fee_combinations:
                            fee_combinations[fee_key] = []
                        fee_combinations[fee_key].append(latest["major_name"])
                    
                    # If multiple majors with different fees, ask follow-up
                    if len(fee_combinations) > 1 and len(by_major) > 1:
                        uni_name = next((u["name"] for u in self.all_universities if u["id"] == university_id), "this university") if university_id else "partner universities"
                        degree_str = f"{state.degree_level} " if state.degree_level else ""
                        lang_str = f"{state.teaching_language}-taught " if state.teaching_language else ""
                        intake_term_str = f" for {norm_intake_term.value.title()} intake" if norm_intake_term else ""
                        
                        response_parts = [
                            f"{norm_intake_term.value.title() if norm_intake_term else 'Intake'} deadlines are not currently open / not yet available for {lang_str}{degree_str}programs at {uni_name}{intake_term_str}.",
                            f"\nFees differ by major. Which major would you like fee information for?"
                        ]
                        
                        unique_majors = list(set([intake["major_name"] for intake in latest_intakes]))[:10]
                        for major_name in unique_majors:
                            response_parts.append(f"  - {major_name}")
                        if len(unique_majors) > 10:
                            response_parts.append(f"  ... and {len(set([intake['major_name'] for intake in latest_intakes])) - 10} more")
                        
                        return {
                            "response": "\n".join(response_parts),
                            "used_db": True,
                            "used_tavily": False,
                            "sources": []
                        }
                
                # For other intents or same fees: use latest intakes for response
                filtered_intakes = latest_intakes
                using_latest_intakes = True
                print(f"DEBUG: Fallback used: YES - Using {len(filtered_intakes)} latest intakes for {intent} query fallback")
            else:
                print(f"DEBUG: Fallback used: YES but no latest intakes found for {intent} query fallback")
        else:
            print(f"DEBUG: Fallback used: NO (intent={intent}, filtered_intakes={len(filtered_intakes)})")

        # Fallback behavior for list queries: if specific intake_term returns 0, check other terms
        if is_list_query and norm_intake_term and not filtered_intakes:
            print(f"DEBUG: List query with intake_term={norm_intake_term} returned 0 results, checking other terms...")
            # Query again without intake_term filter to see if other terms have results
            fallback_intakes = self._get_upcoming_intakes(
                current_date=current_date,
                degree_level=state.degree_level,
                university_id=university_id,
                major_ids=major_ids if major_ids else None,
                intake_term=None,  # Remove intake_term filter
                intake_year=state.intake_year,
                teaching_language=state.teaching_language
            )
            
            print(f"DEBUG: Fallback query returned {len(fallback_intakes)} intakes (without intake_term filter)")
            if fallback_intakes:
                # Group by intake_term to show what's available
                from collections import defaultdict
                by_term = defaultdict(list)
                for intake in fallback_intakes:
                    term = intake.get("intake_term", "Unknown")
                    by_term[term].append(intake)
                print(f"DEBUG: Fallback found intakes in terms: {list(by_term.keys())}")
                
                # Build response with available terms
                uni_name = next((u["name"] for u in self.all_universities if u["id"] == university_id), "this university") if university_id else "partner universities"
                degree_str = f"{state.degree_level} " if state.degree_level else ""
                lang_str = f"{state.teaching_language}-taught " if state.teaching_language else ""
                
                response_parts = [
                    f"No {norm_intake_term.value.title()} intake found for {lang_str}{degree_str}programs at {uni_name}."
                ]
                
                # List available intakes by term
                response_parts.append(f"\nHere are the upcoming {lang_str}{degree_str}intakes available:")
                for term, term_intakes in sorted(by_term.items()):
                    response_parts.append(f"\n{term.title()} intake ({len(term_intakes)} program{'s' if len(term_intakes) != 1 else ''}):")
                    for intake in term_intakes[:10]:  # Limit to 10 per term
                        major_name = intake.get("major_name", "Unknown")
                        deadline = intake.get("application_deadline", "N/A")
                        year = intake.get("intake_year", "")
                        response_parts.append(f"  - {major_name} ({term} {year}, deadline: {deadline})")
                    if len(term_intakes) > 10:
                        response_parts.append(f"  ... and {len(term_intakes) - 10} more")
                
                # Ask follow-up question
                if norm_intake_term == IntakeTerm.SEPTEMBER:
                    response_parts.append(f"\nDo you want {IntakeTerm.MARCH.value.title()} intake instead, or should I include Chinese-taught programs too?")
                elif norm_intake_term == IntakeTerm.MARCH:
                    response_parts.append(f"\nDo you want {IntakeTerm.SEPTEMBER.value.title()} intake instead, or should I include Chinese-taught programs too?")
                else:
                    response_parts.append(f"\nWould you like to see programs from other intake terms, or include different teaching languages?")
                
                return {
                    "response": "\n".join(response_parts),
                    "used_db": True,
                    "used_tavily": False,
                    "sources": []
                }
        
        # If language intent with specific intake term and no results, return early
        if is_language_intent and norm_intake_term and not filtered_intakes:
            fallback_msg = f"No {norm_intake_term.value.title()} language intakes available right now."
            if norm_intake_term == IntakeTerm.MARCH:
                fallback_msg += " Want me to check September language intakes instead?"
            return {
                "response": fallback_msg,
                "used_db": True,
                "used_tavily": False,
                "sources": []
            }
        
        # CRITICAL: If no intakes found after filtering, check for list query fallback
        # CRITICAL: Early return check - must NOT execute for fallback_intents
        # Fallback intents should have already tried _get_latest_intakes_any_deadline()
        fallback_intents_check = ["fees_only", "fees_compare", "scholarship_only", "documents_only", "eligibility_only"]
        should_skip_early_return = intent in fallback_intents_check
        
        if not filtered_intakes:
            # For fallback_intents, skip the generic early return - fallback should have already been tried
            if should_skip_early_return:
                print(f"DEBUG: {intent} query with no intakes after fallback - checking if majors exist...")
                # Fallback should have already been tried, so if we still have no intakes, check majors
                if (university_id or candidate_university_ids) and state.degree_level:
                    majors_list = self._get_majors_for_list_query(
                        university_id=university_id,
                        degree_level=state.degree_level,
                        teaching_language=state.teaching_language,
                        university_ids=candidate_university_ids if not university_id else None
                    )
                    if majors_list:
                        uni_name = next((u["name"] for u in self.all_universities if u["id"] == university_id), "this university")
                        degree_str = f"{state.degree_level} " if state.degree_level else ""
                        lang_str = f"{state.teaching_language}-taught " if state.teaching_language else ""
                        return {
                            "response": (
                                f"Deadlines are not open / not available yet in DB for {lang_str}{degree_str}programs at {uni_name}. "
                                f"We have the program(s) but no intake records with open deadlines. Please check back later or contact MalishaEdu for the latest information."
                            ),
                            "used_db": True,
                            "used_tavily": False,
                            "sources": []
                        }
                
                # If no majors either, return message saying deadlines not open
                major_desc = f" for {state.major_query}" if state.major_query else ""
                degree_desc = f" ({state.degree_level})" if state.degree_level else ""
                intake_desc = f" with {state.intake_term} {state.intake_year or 'intake'}" if state.intake_term else ""
                return {
                    "response": (
                        f"Deadlines are not open / not available yet in DB{major_desc}{degree_desc}{intake_desc}. "
                        f"Please check back later or contact MalishaEdu for the latest information."
                    ),
                    "used_db": True,
                    "used_tavily": False,
                    "sources": []
                }
            
            # For non-fallback intents, proceed with normal early return logic
            if not should_skip_early_return:
                # For list queries, try fallback without intake_term if one was specified
                if is_list_query and norm_intake_term:
                    print(f"DEBUG: List query fallback - intake_term={norm_intake_term} returned 0 results, checking other terms...")
                    fallback_intakes = self._get_upcoming_intakes(
                        current_date=current_date,
                        degree_level=state.degree_level,
                        university_id=university_id,
                        major_ids=major_ids if major_ids else None,
                        intake_term=None,  # Remove intake_term filter
                        intake_year=state.intake_year,
                        teaching_language=state.teaching_language
                    )
                    
                    print(f"DEBUG: List query fallback returned {len(fallback_intakes)} intakes (without intake_term filter)")
                    if fallback_intakes:
                        # Group by intake_term to show what's available
                        from collections import defaultdict
                        by_term = defaultdict(list)
                        for intake in fallback_intakes:
                            term = intake.get("intake_term", "Unknown")
                            by_term[term].append(intake)
                        print(f"DEBUG: List query fallback found intakes in terms: {list(by_term.keys())}")
                        
                        # Build response with available terms
                        uni_name = next((u["name"] for u in self.all_universities if u["id"] == university_id), "this university") if university_id else "partner universities"
                        degree_str = f"{state.degree_level} " if state.degree_level else ""
                        lang_str = f"{state.teaching_language}-taught " if state.teaching_language else ""
                        
                        response_parts = [
                            f"No {norm_intake_term.value.title()} intake found for {lang_str}{degree_str}programs at {uni_name}."
                        ]
                        
                        # List available intakes by term
                        response_parts.append(f"\nAvailable intakes:")
                        for term, term_intakes in sorted(by_term.items()):
                            response_parts.append(f"\n{term.title()} intake ({len(term_intakes)} program{'s' if len(term_intakes) != 1 else ''}):")
                            for intake in term_intakes[:10]:  # Limit to 10 per term
                                major_name = intake.get("major_name", "Unknown")
                                deadline = intake.get("application_deadline", "N/A")
                                year = intake.get("intake_year", "")
                                response_parts.append(f"  - {major_name} ({term} {year}, deadline: {deadline})")
                            if len(term_intakes) > 10:
                                response_parts.append(f"  ... and {len(term_intakes) - 10} more")
                        
                        # Ask follow-up question
                        if norm_intake_term == IntakeTerm.SEPTEMBER:
                            response_parts.append(f"\nDo you want {IntakeTerm.MARCH.value.title()} intake instead, or should I include Chinese-taught programs too?")
                        elif norm_intake_term == IntakeTerm.MARCH:
                            response_parts.append(f"\nDo you want {IntakeTerm.SEPTEMBER.value.title()} intake instead, or should I include Chinese-taught programs too?")
                        else:
                            response_parts.append(f"\nWould you like to see programs from other intake terms, or include different teaching languages?")
                        
                        return {
                            "response": "\n".join(response_parts),
                            "used_db": True,
                            "used_tavily": False,
                            "sources": []
                        }
                
                # For list queries: check majors before returning generic message
                if is_list_query and not requires_upcoming_deadline:
                    print(f"DEBUG: List query with no intakes - checking majors as fallback...")
                    majors_list = self._get_majors_for_list_query(
                        university_id=university_id,
                        degree_level=state.degree_level,
                        teaching_language=state.teaching_language,
                        university_ids=candidate_university_ids if not university_id else None
                    )
                    
                    if majors_list:
                        # Majors exist but no upcoming intakes - list them
                        uni_name = next((u["name"] for u in self.all_universities if u["id"] == university_id), "this university") if university_id else "partner universities"
                        degree_str = f"{state.degree_level} " if state.degree_level else ""
                        lang_str = f"{state.teaching_language}-taught " if state.teaching_language else ""
                        intake_term_str = f" for {norm_intake_term.value.title()} intake" if norm_intake_term else ""
                        
                        response_parts = [
                            f"These {lang_str}{degree_str}programs exist at {uni_name}{intake_term_str}:"
                        ]
                        
                        # List majors (limit to 20 for readability)
                        for major in majors_list[:20]:
                            response_parts.append(f"  - {major['name']}")
                        if len(majors_list) > 20:
                            response_parts.append(f"  ... and {len(majors_list) - 20} more programs")
                        
                        # Explain about deadlines
                        if norm_intake_term:
                            response_parts.append(f"\n{norm_intake_term.value.title()} intake deadlines are not currently open / not yet available.")
                        else:
                            response_parts.append(f"\nIntake deadlines are not currently open / not yet available.")
                        response_parts.append("Please check back later or ask about specific program details.")
                        
                        return {
                            "response": "\n".join(response_parts),
                            "used_db": True,
                            "used_tavily": False,
                            "sources": []
                        }
                
                # Generic "no matching programs" message (only for non-fallback intents)
                # Fallback intents are handled earlier in the if should_skip_early_return block
                major_desc = f" for {state.major_query}" if state.major_query else ""
                degree_desc = f" ({state.degree_level})" if state.degree_level else ""
                intake_desc = f" with {state.intake_term} {state.intake_year or 'intake'}" if state.intake_term else ""
                return {
                    "response": (
                        f"I don't have any matching programs{major_desc}{degree_desc}{intake_desc} "
                        f"in our partner universities database with upcoming deadlines. "
                        f"Could you try a different major, degree level, or intake term?"
                    ),
                    "used_db": True,
                    "used_tavily": False,
                    "sources": []
                }

        # Rank intakes by combined score (major match + deadline closeness + intake_term match)
        # Only rank if we have multiple major candidates (university not specified)
        if not is_list_query and not university_id and major_match_scores and len(filtered_intakes) > 1:
            from datetime import datetime, timedelta
            
            # Calculate combined scores for each intake
            ranked_intakes = []
            for intake in filtered_intakes:
                combined_score = 0.0
                
                # 1. Major match score (0.0 to 1.0, weighted 50%)
                major_id = intake.get("major_id")
                major_score = major_match_scores.get(major_id, 0.5)  # Default 0.5 if not found
                combined_score += major_score * 0.5
                
                # 2. Deadline closeness (closer = higher score, weighted 30%)
                deadline_str = intake.get("application_deadline")
                if deadline_str:
                    try:
                        deadline = datetime.fromisoformat(deadline_str).date()
                        days_until = (deadline - current_date).days
                        # Normalize: 0-365 days -> 1.0 to 0.0 score (closer = better)
                        # Use exponential decay for better discrimination
                        if days_until >= 0:
                            deadline_score = max(0.0, 1.0 - (days_until / 365.0) ** 0.5)
                        else:
                            deadline_score = 0.0
                        combined_score += deadline_score * 0.3
                    except:
                        deadline_score = 0.5  # Default if parsing fails
                        combined_score += deadline_score * 0.3
                
                # 3. Intake term match (exact match = 1.0, weighted 20%)
                intake_term_str = intake.get("intake_term")
                if norm_intake_term and intake_term_str:
                    if intake_term_str == norm_intake_term.value:
                        term_score = 1.0
                    else:
                        term_score = 0.3  # Partial credit for having an intake term
                elif norm_intake_term:
                    term_score = 0.0  # User specified term but intake doesn't match
                else:
                    term_score = 0.5  # No term specified, neutral
                combined_score += term_score * 0.2
                
                ranked_intakes.append((intake, combined_score))
            
            # Sort by combined score (highest first)
            ranked_intakes.sort(key=lambda x: x[1], reverse=True)
            filtered_intakes = [intake for intake, score in ranked_intakes]
            
            print(f"DEBUG: Ranked {len(ranked_intakes)} intakes by combined score. Top 3 scores: {[f'{s:.3f}' for _, s in ranked_intakes[:3]]}")
            if ranked_intakes:
                top_intake = ranked_intakes[0][0]
                print(f"DEBUG: Best intake: {top_intake.get('university_name')} - {top_intake.get('major_name')} (score={ranked_intakes[0][1]:.3f}, deadline={top_intake.get('application_deadline')})")

        # For fees_compare intent: rank by fees, not deadline
        if intent == "fees_compare" and filtered_intakes:
            print(f"DEBUG: fees_compare intent - ranking by tuition fees instead of deadline")
            # Calculate effective tuition (per year preferred, fallback to semester * 2)
            def get_effective_tuition(intake):
                if intake.get('tuition_per_year') is not None:
                    return float(intake['tuition_per_year'])
                elif intake.get('tuition_per_semester') is not None:
                    return float(intake['tuition_per_semester']) * 2  # Convert to annual
                else:
                    return float('inf')  # No tuition info - put at end
            
            # Sort by effective tuition (lowest first)
            filtered_intakes.sort(key=get_effective_tuition)
            print(f"DEBUG: Ranked {len(filtered_intakes)} intakes by fees. Cheapest: {get_effective_tuition(filtered_intakes[0]) if filtered_intakes else 'N/A'}")
        
        # Hard cap to avoid sending huge lists to LLM
        original_count = len(filtered_intakes)
        if is_list_query:
            # Cap at MAX_LIST_INTAKES for list queries
            filtered_intakes = filtered_intakes[:self.MAX_LIST_INTAKES]
            # Store full results for pagination
            self.last_list_results = filtered_intakes.copy()
            self.last_list_offset = 0
            uniq_unis = len({i['university_id'] for i in filtered_intakes})
            print(f"DEBUG: List mode capped: universities_shown={uniq_unis} total={original_count} offset=0")
        elif intent == "fees_compare":
            # For fees_compare, load more intakes (up to 50) to compare
            filtered_intakes = filtered_intakes[:50]
        else:
            filtered_intakes = filtered_intakes[:40]
        
        if is_list_query:
            uniq_unis = len({i['university_id'] for i in filtered_intakes})
            print(f"DEBUG: List query unique universities: {uniq_unis}, intake_count={len(filtered_intakes)}")
            
            # Fix 4: For list queries with Language degree_level OR fees_only/fees_compare, ALWAYS use deterministic formatter
            # Never build DB context or call OpenAI for these
            is_language_list = state.degree_level and "Language" in str(state.degree_level)
            should_use_deterministic = (intent in ["fees_compare", "fees_only"]) or is_language_list
            
            if should_use_deterministic:
                print(f"DEBUG: Using deterministic formatter for list query with intent={intent}, is_language={is_language_list}")
                # Select best intake per university (up to MAX_LIST_UNIVERSITIES)
                from collections import defaultdict
                by_university = defaultdict(list)
                for intake in filtered_intakes:
                    by_university[intake.get('university_id')].append(intake)
                
                # Select best intake per university
                selected_intakes = []
                # Import datetime locally to avoid closure issues
                from datetime import datetime as dt_func
                current_dt = current_date
                for uni_id, uni_intakes in list(by_university.items())[:self.MAX_LIST_UNIVERSITIES]:
                    # Sort by: upcoming deadline first, then tuition (lowest), then deadline
                    def sort_key(i, dt=dt_func, current=current_dt):
                        deadline = i.get('application_deadline')
                        tuition = i.get('tuition_per_year') or (i.get('tuition_per_semester', 0) * 2) or float('inf')
                        is_upcoming = False
                        if deadline:
                            try:
                                deadline_date = dt.fromisoformat(deadline).date()
                                is_upcoming = deadline_date > current
                            except:
                                is_upcoming = False
                        return (not is_upcoming, tuition, deadline or '')
                    
                    best = sorted(uni_intakes, key=sort_key)[0]
                    selected_intakes.append(best)
                
                # Compute total unique universities for pagination
                # Check if single-university case
                unique_uni_ids = set(i.get('university_id') for i in filtered_intakes if i.get('university_id'))
                if len(unique_uni_ids) == 1:
                    # Single-university: total = total programs (intakes)
                    total_for_formatter = len(filtered_intakes)
                else:
                    # Multi-university: total = unique universities (not intakes)
                    total_for_formatter = len(unique_uni_ids)
                
                # Store pagination state in cache (including last_displayed for follow-up questions)
                self._set_pagination_state(
                    partner_id=partner_id,
                    conversation_history=conversation_history,
                    results=filtered_intakes,  # Store all results, not just selected
                    offset=0,
                    total=total_for_formatter,  # Use correct total (programs or universities)
                    page_size=self.MAX_LIST_UNIVERSITIES,
                    intent=intent,
                    last_displayed=selected_intakes,  # Store what we're actually showing to the user
                    conversation_id=conversation_id
                )
                
                # Store last_selected_major_id for duration questions (use first intake's major_id if available)
                if selected_intakes and selected_intakes[0].get('major_id'):
                    self.last_selected_major_id = selected_intakes[0].get('major_id')
                    print(f"DEBUG: Stored last_selected_major_id={self.last_selected_major_id} for duration questions")
                
                response = self._format_list_response_deterministic(
                    selected_intakes, 0, total_for_formatter,
                    duration_preference=state.duration_preference,
                    user_message=user_message
                )
                print(f"DEBUG: Returning deterministic list response (early return, no LLM call)")
                return response
        
        # Multi-track handling: Check for multiple teaching languages
        # Group intakes by teaching language when same university+degree+term+year but different languages
        if filtered_intakes and not is_list_query and not state.teaching_language:
            from collections import defaultdict
            by_track = defaultdict(list)
            for intake in filtered_intakes:
                eff_lang = intake.get('effective_teaching_language') or intake.get('teaching_language') or 'Unknown'
                # Group by university_id + degree_level + intake_term + intake_year + teaching_language
                track_key = (
                    intake.get('university_id'),
                    str(intake.get('degree_level', '')),
                    intake.get('intake_term', ''),
                    intake.get('intake_year'),
                    eff_lang
                )
                by_track[track_key].append(intake)
            
            # Check if we have multiple distinct teaching languages for the same university+degree+term+year
            if len(by_track) > 1:
                # Check if tracks differ only by teaching language
                track_keys_by_base = defaultdict(list)
                for track_key, intakes in by_track.items():
                    base_key = track_key[:4]  # university_id, degree_level, intake_term, intake_year
                    track_keys_by_base[base_key].append((track_key[4], intakes))  # teaching_language, intakes
                
                # If we have multiple tracks for the same base (different languages), handle multi-track
                for base_key, tracks in track_keys_by_base.items():
                    if len(tracks) > 1:
                        track_languages = [lang for lang, _ in tracks]
                        print(f"DEBUG: Multi-track detected - {len(tracks)} teaching languages for same university+degree+term+year")
                        print(f"REGRESSION_TEST: Multi-track detected - languages={track_languages}, track_count={len(tracks)}")
                        # Store multi-track info for later use in response building
                        self._multi_track_info = {
                            'tracks': tracks,
                            'base_key': base_key
                        }
                        break
        
        # Update conversation memory when a program is recommended (use first/best match)
        if filtered_intakes and not is_list_query:
            best_intake = filtered_intakes[0]
            self.last_selected_university_id = best_intake.get('university_id')
            self.last_selected_major_id = best_intake.get('major_id')
            self.last_selected_program_intake_id = best_intake.get('id')
            print(f"DEBUG: Updated conversation memory - university_id={self.last_selected_university_id}, major_id={self.last_selected_major_id}, intake_id={self.last_selected_program_intake_id}")
        
        # Build database context (already filtered) with batch-loaded related data
        print(f"DEBUG: Building database context...")
        t_ctx_start = time.perf_counter()

        # Flags based on intent
        include_docs = include_exams = include_scholarships = include_eligibility = include_deadlines = include_cost = True
        if intent == "fees_only":
            include_docs = False
            include_exams = False
            include_scholarships = False
            include_eligibility = False
            include_deadlines = True
            include_cost = True
        elif intent == "fees_compare":
            include_docs = False
            include_exams = False
            include_scholarships = False
            include_eligibility = False
            include_deadlines = True
            include_cost = True
        elif intent == "documents_only":
            include_docs = True
            include_exams = False
            include_scholarships = False
            include_eligibility = False
            include_cost = False
        elif intent == "scholarship_only":
            include_docs = True  # Include scholarship-related documents
            include_exams = False
            include_scholarships = True
            include_eligibility = False
            include_cost = False
        elif intent == "eligibility_only":
            include_docs = False
            include_exams = True
            include_scholarships = False
            include_eligibility = True
            include_cost = False

        db_context = self._build_database_context(
            state=state,
            current_date=current_date,
            intakes=filtered_intakes,
            show_catalog=show_catalog,
            match_notes=match_notes,
            include_docs=include_docs,
            include_exams=include_exams,
            include_scholarships=include_scholarships,
            include_deadlines=include_deadlines,
            include_eligibility=include_eligibility,
            include_cost=include_cost,
            is_list_query=is_list_query,
            using_latest_intakes=using_latest_intakes,
            intent=intent
        )
        t_ctx_end = time.perf_counter()
        print(f"DEBUG: Database context length: {len(db_context)} characters (built in {(t_ctx_end - t_ctx_start):.3f}s)")
        
        # Build system prompt with state summary
        state_summary = self._state_to_summary_string(state)
        system_prompt = f"""{self.PARTNER_SYSTEM_PROMPT}

CURRENT CONVERSATION STATE:
{state_summary}

CURRENT INTENT: {intent}
INCLUDE_TOTAL_REQUESTED: {include_total}

DATABASE CONTEXT:
{db_context}

IMPORTANT INSTRUCTIONS:
- Use the DATABASE CONTEXT above to answer questions
- Use CURRENT DATE ({current_date.isoformat()}) from DATABASE CONTEXT as the single source of truth for deadline checks
- Only suggest programs with upcoming deadlines (application_deadline > {current_date.isoformat()})
- **If DATABASE CONTEXT mentions "latest recorded intakes" or "deadlines are not currently open", clearly state in your response: "Deadlines are not currently open / not available, showing the latest saved intake info from database." Then provide the latest recorded information (fees/scholarships/documents/eligibility) as shown in DATABASE CONTEXT.**
- **For scholarship questions: If scholarship records exist for the latest intake, show them. If none exist, say "Scholarship info not provided in DB for this program yet" and ask if they want March/September or self-funded options.**
- **CRITICAL ANTI-HALLUCINATION RULE: ONLY mention universities and programs that are EXPLICITLY listed in the DATABASE CONTEXT above. DO NOT invent, assume, or suggest any university or program that is NOT in the DATABASE CONTEXT. If DATABASE CONTEXT is empty or shows no matches, you MUST say "I don't have any matching programs in our database" - DO NOT make up universities or programs.**
- **CRITICAL: If DATABASE CONTEXT shows no programs matching the user's query, you MUST tell them there are no matches. DO NOT suggest universities that aren't in the context.**
- CRITICAL: If user asks for "March language program", ONLY show programs with intake_term = "March"
- CRITICAL: If user asks for "September language program", ONLY show programs with intake_term = "September"
- DO NOT mix March and September intakes - they are different semesters
- For language programs: DO NOT ask about teaching medium (English/Chinese) - language programs are always taught in English
- For language programs: Focus on duration (one semester vs one year) and intake term
- If multiple deadlines exist (scholarship vs self-paid), show both explicitly
- Use accommodation_fee_period and medical_insurance_fee_period exactly - do NOT assume "per year"
- If a field is missing in DATABASE CONTEXT, say "not provided" - do NOT infer
- If uncertain about a match, present 2-3 options and ask user to confirm
- Always follow the RESPONSE FORMAT structure (Best match → Deadlines → Eligibility → Cost → Scholarships → Next question)
- Be concise and build conversation gradually
- If list is long (>10), show top 10 and mention there are more
- Calculate costs accurately from database
- Provide exact document requirements from database
- If intent=fees_only, omit eligibility/documents/scholarship lists unless explicitly requested.
- Do NOT include Year 1 total unless INCLUDE_TOTAL_REQUESTED=True.
- Only use Tavily for questions like "Is China student dorm friendly for Muslim?" or latest policy updates
- Use phrasing like "MalishaEdu offers..." instead of "I found"; avoid saying "in our database"
- Present results with clear bullet points for readability
- **CRITICAL: Always mention teaching language explicitly in your answer ("English-taught" / "Chinese-taught"). If both English and Chinese programs exist for the same major, ask the user to pick the language before quoting fees/scholarships.**
- **CRITICAL: Never say "notify you", "I will notify you", "notify me later", "I'll notify you", or any variation of notification promises - you cannot send notifications. Instead, say "I can't send notifications; please check back or ask again later" or "deadlines not open yet / not updated—check back later" or "please check back later or contact MalishaEdu for the latest information".**
- **For scholarship_only intent: Do NOT require upcoming deadlines to explain scholarship options. If no upcoming intakes exist, use latest intakes (any deadline) and show scholarship info if present in the database. Clearly state that deadlines are not currently open but provide the scholarship information available.**
- **CRITICAL: When answering fees/scholarship/documents/eligibility questions, ALWAYS read and mention ProgramIntake.notes (shown as "Important Notes") and ProgramIntake.scholarship_info if present in DATABASE CONTEXT. If notes or scholarship_info mention extra forms (e.g., "Outstanding Talents Scholarship Application Form"), list them under "Scholarship-Specific Documents Required" when user asks about scholarship/how to get it.**
- **CRITICAL: Separate "program exists" vs "deadline open". For fees_only/scholarship_only/documents_only/eligibility_only queries, if you have a matched university + major but no upcoming deadlines, use latest intakes (any deadline) and clearly say "Deadlines are not open / not available yet in DB" instead of "no matching program". Only say "no matching program" if the program/major doesn't exist in the database at all.**
- **CRITICAL: Teaching language must ALWAYS be explicit in every answer. Every program mentioned must show "Teaching Language: English" or "Teaching Language: Chinese". Use effective_teaching_language from DATABASE CONTEXT (ProgramIntake.teaching_language if present, else Major.teaching_language).**
- **CRITICAL: For scholarship questions, be realistic. If university ranking is very strong (top 50/100), mention that "100% waiver is highly competitive; usually requires excellent GPA + strong profile". Never claim scholarships are "easy" to get. Use university_ranking field from DATABASE CONTEXT.**
- **CRITICAL: If multiple tracks exist (English/Chinese) for the same university+major+degree and user didn't specify language, list both tracks with teaching language and ask which one they mean. Do NOT silently pick one track.**"""

        # Build messages for LLM
        messages = [
            {"role": "system", "content": system_prompt}
        ]
        
        # Add conversation history (last 16 messages = 4 exchanges)
        for msg in conversation_history[-16:]:
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
            
            t_llm_start = time.perf_counter()
            response = self.openai_service.chat_completion(
                messages=messages,
                temperature=0.7
            )
            t_llm_end = time.perf_counter()
            
            assistant_message = response.choices[0].message.content.strip()
            print(f"DEBUG: Generated response length: {len(assistant_message)} characters in {(t_llm_end - t_llm_start):.3f}s")
            print(f"DEBUG: Response preview: {assistant_message[:200]}...")
            
            print(f"DEBUG: intent={intent}, include_total_requested={include_total}, year1_total_included={year1_total_included}")
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
        if state.major_query:
            summary_lines.append(f"Major Query: {state.major_query}")
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

    def _quick_extract_query(self, user_message: str) -> Dict[str, Any]:
        """
        Lightweight regex-based extraction for intake term/year and fee-focused queries.
        Skips LLM state extraction when confident (has major text + intake term + year).
        """
        # Normalize Unicode punctuation before processing
        text = self._normalize_unicode_text(user_message.strip())
        lower = text  # Already lowercased by _normalize_unicode_text
        
        term = None
        if re.search(r'\bmar(ch)?\b', lower):
            term = "March"
        elif re.search(r'\bsep(t|tember)?\b', lower):
            term = "September"
        
        year_match = re.search(r'\b(20[2-9]\d)\b', lower)
        year = int(year_match.group(1)) if year_match else None
        
        fee_focus = bool(re.search(r'\b(fee|fees|tuition|cost|expense|charge|budget)\b', lower))

        # Duration preference detection
        duration_preference = None
        duration_years_target = None
        if re.search(r'\b(1\s+semester|one\s+semester|half\s+year|6\s+month|six\s+month)\b', lower):
            duration_preference = "one_semester"
            duration_years_target = 0.5
        elif re.search(r'\b(2\s+semester|two\s+semester)\b', lower):
            duration_preference = "two_semester"
            duration_years_target = 1.0
        elif re.search(r'\b(1\s+year|one\s+year)\b', lower):
            duration_preference = "one_year"
            duration_years_target = 1.0

        # Degree level quick mapping
        degree_level = None
        degree_map = {
            "bachelor": ["bachelor", "undergrad", "undergraduate", "b.sc", "bsc", "b.s", "bs", "b.a", "ba", "beng", "b.eng"],
            "master": ["master", "postgrad", "post-graduate", "graduate", "msc", "m.sc", "ms", "m.s", "ma", "m.a", "mba"],
            "Phd": ["phd", "doctorate", "doctoral", "dphil"],
            "Language": ["language program", "language", "non-degree", "non degree"],
            "Diploma": ["diploma", "associate", "assoc"]
        }
        for lvl, keywords in degree_map.items():
            if any(re.search(rf"\b{re.escape(k)}\b", lower) for k in keywords):
                degree_level = lvl
                break
        
        cleaned = re.sub(r'\b(20[2-9]\d)\b', '', text, flags=re.IGNORECASE)
        cleaned = re.sub(r'\b(mar(ch)?|sep(t|tember)?)\b', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\b(fee|fees|tuition|cost|expense|charge|budget|application fee|application)\b', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        
        # If cleaned text is empty or only contains common words, don't set major_text
        common_words = {"for", "the", "what", "whats", "what's", "is", "are", "at", "in", "and", "or", "of", "to", "a", "an"}
        cleaned_words = set(cleaned.lower().split())
        if not cleaned or cleaned_words.issubset(common_words):
            cleaned = None
        
        confident = bool(term and year and cleaned)
        return {
            "intake_term": term,
            "intake_year": year,
            "major_text": cleaned,
            "fee_focus": fee_focus,
            "degree_level": degree_level,
            "duration_preference": duration_preference,
            "duration_years_target": duration_years_target,
            "confident": confident
        }

    def _llm_quick_extract_degree_level(self, user_message: str) -> Optional[str]:
        """
        Use a very small LLM prompt to recover degree level from noisy input
        (e.g., typos like 'undergradute').
        """
        prompt = f"""Extract ONLY the degree level from this query. Respond with one of:
- Bachelor
- Master
- PhD
- Language
- Non-degree
- Diploma
- None

Query: "{user_message}"

Return exactly one of the above terms (case-sensitive)."""
        try:
            resp = self.openai_service.chat_completion(
                messages=[{"role": "system", "content": "Return exactly one token from the allowed list."},
                          {"role": "user", "content": prompt}],
                temperature=0.0
            )
            val = resp.choices[0].message.content.strip()
            allowed = {"Bachelor", "Master", "PhD", "Language", "Non-degree", "Diploma", "None"}
            return val if val in allowed and val != "None" else None
        except Exception:
            return None

    def _detect_university_in_text(self, text: str) -> Optional[Dict[str, Any]]:
        """
        Detect university by direct alias/name presence in text or fuzzy match.
        """
        lower = text.lower()
        for uni in self.all_universities:
            aliases = [uni.get("name"), uni.get("name_cn")] + (uni.get("aliases") or [])
            for alias in aliases:
                if alias and str(alias).lower() in lower:
                    return uni
        matched, uni_dict, _ = self._fuzzy_match_university(text)
        if matched and uni_dict:
            return uni_dict
        return None

    def _strip_university_from_major(self, major_text: str, university: Dict[str, Any]) -> str:
        """Remove university aliases from major text to improve matching."""
        if not major_text or not university:
            return major_text
        lower = major_text.lower()
        aliases = [university.get("name"), university.get("name_cn")] + (university.get("aliases") or [])
        for alias in aliases:
            if alias:
                alias_lower = str(alias).lower()
                lower = lower.replace(alias_lower, "")
        return re.sub(r'\s+', ' ', lower).strip()

    def _normalize_degree_level_value(self, value: Optional[str]) -> Optional[str]:
        """Normalize degree level text to canonical casing."""
        if not value:
            return None
        mapping = {
            "bachelor": "Bachelor",
            "master": "Master",
            "phd": "PhD",
            "doctorate": "PhD",
            "doctoral": "PhD",
            "language": "Language",
            "non-degree": "Non-degree",
            "non degree": "Non-degree",
            "diploma": "Diploma",
            "associate": "Diploma",
            "vocational college": "Vocational College",
            "junior high": "Junior high",
            "senior high": "Senior high",
            "language program": "Language",
        }
        key = value.strip().lower()
        return mapping.get(key, value.strip().capitalize())

    def _normalize_intake_term_enum(self, term: Optional[str]) -> Optional[IntakeTerm]:
        """Normalize intake term string to IntakeTerm enum with typo tolerance."""
        if not term:
            return None
        t = term.strip().lower()
        
        # March variations
        if t in ["march", "mar", "spring"]:
            return IntakeTerm.MARCH
        
        # September variations with typo tolerance
        # Common misspellings: septembar, septembr, septmber, etc.
        if t in ["september", "sep", "sept", "fall", "autumn"]:
            return IntakeTerm.SEPTEMBER
        
        # Typo tolerance: check if it starts with "sept" and has similar length
        if t.startswith("sept") and len(t) >= 6:
            # Common misspellings: septembar, septembr, septmber, septmeber
            if any(misspelling in t for misspelling in ["septembar", "septembr", "septmber", "septmeber", "septemb"]):
                return IntakeTerm.SEPTEMBER
            # If it starts with "sept" and is close to "september" length, assume September
            if 6 <= len(t) <= 10:
                return IntakeTerm.SEPTEMBER
        
        return None

    def _find_major_ids_by_topic(self, topic_text: str, degree_level: Optional[str] = None, limit: int = 300) -> List[int]:
        """Find many major ids by topic/keywords for list queries. Uses strict matching to avoid false positives."""
        if not topic_text:
            return []
        topic = topic_text.lower().strip()
        # Remove common stop words
        stop_words = {"for", "my", "the", "a", "an", "in", "at", "to", "of", "and", "or", "with", "program", "programs", "intake", "intakes"}
        tokens = {t for t in re.split(r'\W+', topic) if t and t not in stop_words and len(t) > 2}
        if not tokens:
            tokens = {t for t in re.split(r'\W+', topic) if t and len(t) > 2}
        
        synonyms = {
            "computer science": ["cs", "cse", "software", "programming", "it", "information technology", "cyber", "cybersecurity", "ai", "artificial intelligence", "data", "data science"],
            "business": ["management", "mba", "commerce", "marketing"],
            "electrical": ["ece", "eee", "electronic"],
        }
        
        def topic_hits(name: str, keywords: List[str]) -> bool:
            """Strict matching: require meaningful overlap, not just any token."""
            name_l = name.lower()
            name_tokens = {t for t in re.split(r'\W+', name_l) if t and len(t) > 2}
            
            # Exact substring match (strong)
            if len(topic) >= 4 and (topic in name_l or name_l in topic):
                return True
            
            # Meaningful token overlap: require at least 2 tokens or one token with length >= 5
            if tokens and name_tokens:
                overlap = tokens & name_tokens
                if len(overlap) >= 2 or (len(overlap) == 1 and any(len(t) >= 5 for t in overlap)):
                    return True
            
            # Keyword matching (strict)
            for kw in keywords:
                kw_l = str(kw).lower().strip()
                if not kw_l or len(kw_l) < 3:
                    continue
                # Exact match in keyword
                if len(topic) >= 4 and (topic in kw_l or kw_l in topic):
                    return True
                # Token overlap in keyword
                kw_tokens = {t for t in re.split(r'\W+', kw_l) if t and len(t) > 2}
                if kw_tokens and tokens:
                    overlap = kw_tokens & tokens
                    if len(overlap) >= 2 or (len(overlap) == 1 and any(len(t) >= 5 for t in overlap)):
                        return True
            
            # Synonym matching (only for known synonyms)
            for base, syns in synonyms.items():
                if base in topic or any(s in topic for s in syns):
                    if any(s in name_l for s in ([base] + syns)):
                        return True
                    kw_text = " ".join(str(k) for k in keywords).lower()
                    if any(s in kw_text for s in ([base] + syns)):
                        return True
            return False

        matched = []
        matched_names = []  # For debug logging
        for m in self.all_majors:
            if degree_level and m.get("degree_level") and degree_level.lower() not in str(m["degree_level"]).lower():
                continue
            kws = self._normalize_keywords(m.get("keywords"))
            if topic_hits(m.get("name", ""), kws):
                matched.append(m["id"])
                matched_names.append(m.get("name", "N/A"))
                if len(matched) >= limit:
                    break
        
        if matched:
            print(f"DEBUG: _find_major_ids_by_topic matched {len(matched)} majors for topic '{topic_text}': {matched_names[:10]}")
        else:
            print(f"DEBUG: _find_major_ids_by_topic found NO matches for topic '{topic_text}' (tokens: {tokens})")
        return matched

