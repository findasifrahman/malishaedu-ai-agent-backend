"""
Two-Stage Router for Partner Agent
Stage 1: Rule-based parsing
Stage 2: LLM extraction (only if confidence < 0.75)
"""
import re
import json
from typing import Optional, Dict, Any, Tuple, List
from difflib import SequenceMatcher
from app.services.slot_schema import PartnerQueryState, RequirementFocus, ScholarshipFocus, PaginationConfig
from app.services.openai_service import OpenAIService


class PartnerRouter:
    """Two-stage router for partner queries"""
    
    # Intent enum values
    INTENT_PAGINATION = "PAGINATION"
    INTENT_LIST_UNIVERSITIES = "LIST_UNIVERSITIES"
    INTENT_LIST_PROGRAMS = "LIST_PROGRAMS"
    INTENT_ADMISSION_REQUIREMENTS = "ADMISSION_REQUIREMENTS"
    INTENT_SCHOLARSHIP = "SCHOLARSHIP"
    INTENT_FEES = "FEES"
    INTENT_COMPARISON = "COMPARISON"
    INTENT_PROGRAM_DETAILS = "PROGRAM_DETAILS"
    INTENT_GENERAL = "GENERAL"
    
    def __init__(self, openai_service: OpenAIService):
        self.openai_service = openai_service
    
    def normalize_query(self, text: str) -> str:
        """Normalize query: lowercase, collapse repeated letters, replace synonyms"""
        if not text:
            return ""
        
        # Collapse repeated letters (e.g., "Bachelorvvvv" -> "bachelor")
        text = re.sub(r'(.)\1{2,}', r'\1', text, flags=re.IGNORECASE)
        
        # Lowercase
        text = text.lower()
        
        # Replace synonyms
        synonyms = {
            r'\buni\b': 'university',
            r'\bsept\b': 'september',
            r'\bfall\b': 'september',
            r'\bautumn\b': 'september',
            r'\bspring\b': 'march',
            r'\basap\b': 'earliest',
            r'\bsoonest\b': 'earliest',
        }
        for pattern, replacement in synonyms.items():
            text = re.sub(pattern, replacement, text)
        
        return text.strip()
    
    def parse_duration(self, text: str) -> Tuple[Optional[float], Optional[str]]:
        """
        Parse duration from text. Returns (duration_years, constraint).
        Supports: "4 months", "16 weeks", "1.3 years", "about 2 years", "at least 1 year", "max 2 years"
        """
        text_lower = text.lower()
        
        # Extract constraint
        constraint = None
        if re.search(r'\b(exactly|exact|precisely)\b', text_lower):
            constraint = "exact"
        elif re.search(r'\b(about|approx|approximately|around|roughly)\b', text_lower):
            constraint = "approx"
        elif re.search(r'\b(at least|minimum|min|more than|over|above)\b', text_lower):
            constraint = "min"
        elif re.search(r'\b(max|maximum|under|below|less than|up to)\b', text_lower):
            constraint = "max"
        
        # Extract numeric value and unit
        # Pattern: number + (months|weeks|years|semesters)
        patterns = [
            (r'(\d+\.?\d*)\s*(?:months?|month)', lambda m: float(m.group(1)) / 12),
            (r'(\d+\.?\d*)\s*(?:weeks?|week)', lambda m: float(m.group(1)) / 52),
            (r'(\d+\.?\d*)\s*(?:years?|year)', lambda m: float(m.group(1))),
            (r'(\d+\.?\d*)\s*(?:semesters?|semester)', lambda m: float(m.group(1)) * 0.5),
        ]
        
        for pattern, converter in patterns:
            match = re.search(pattern, text_lower)
            if match:
                years = converter(match)
                return years, constraint or "exact"
        
        # Try to extract just a number (assume years)
        number_match = re.search(r'(\d+\.?\d*)', text_lower)
        if number_match and constraint:
            return float(number_match.group(1)), constraint
        
        return None, None
    
    def route_stage1_rules(self, query: str, prev_state: Optional[PartnerQueryState] = None) -> PartnerQueryState:
        """
        Stage 1: Rule-based parsing
        Returns state with confidence score
        """
        normalized = self.normalize_query(query)
        state = PartnerQueryState()
        
        # Detect intent with priority order
        if re.search(r'\b(next|more|show more|next page|page \d+|continue|prev|previous|back|page 1|first page)\b', normalized):
            state.intent = self.INTENT_PAGINATION
            if re.search(r'\b(next|more|show more|next page|continue)\b', normalized):
                state.page_action = "next"
            elif re.search(r'\b(prev|previous|back)\b', normalized):
                state.page_action = "prev"
            elif re.search(r'\b(page 1|first page)\b', normalized):
                state.page_action = "first"
            state.confidence += 0.4
        
        elif re.search(r'\b(university|universities|partner universities?|uni list|show all universities?|list universities?)\b', normalized):
            state.intent = self.INTENT_LIST_UNIVERSITIES
            state.wants_list = True
            state.confidence += 0.4
        
        elif re.search(r'\b(list|show|available|what programs?|which programs?|programs? available|majors? available|courses? available)\b', normalized):
            state.intent = self.INTENT_LIST_PROGRAMS
            state.wants_list = True
            state.confidence += 0.4
        
        elif re.search(r'\b(requirements?|eligibility|admission requirements?|apply|documents? needed|docs?|bank|hsk|ielts|csca|age|inside china|country|accommodation|deadline)\b', normalized):
            state.intent = self.INTENT_ADMISSION_REQUIREMENTS
            state.wants_requirements = True
            state.confidence += 0.4
            
            # If user says "admission requirement(s)" or "requirements", set all req_focus flags to True
            if re.search(r'\b(admission requirements?|requirements?|eligibility)\b', normalized) and not re.search(r'\b(bank|hsk|ielts|csca|age|deadline|accommodation|country|document|doc)\b', normalized):
                state.req_focus.docs = True
                state.req_focus.bank = True
                state.req_focus.exams = True
                state.req_focus.age = True
                state.req_focus.deadline = True
            else:
                # Set req_focus based on specific keywords
                if re.search(r'\b(doc|document|paper|material)\b', normalized):
                    state.req_focus.docs = True
                if re.search(r'\b(hsk|ielts|toefl|csca|exam|test|english test)\b', normalized):
                    state.req_focus.exams = True
                if re.search(r'\b(bank|statement|guarantee)\b', normalized):
                    state.req_focus.bank = True
                if re.search(r'\b(age|old|young)\b', normalized):
                    state.req_focus.age = True
                if re.search(r'\b(inside china|in china|china applicant)\b', normalized):
                    state.req_focus.inside_china = True
                if re.search(r'\b(deadline|when|due|last date)\b', normalized):
                    state.req_focus.deadline = True
                if re.search(r'\b(accommodation|dorm|apartment|housing)\b', normalized):
                    state.req_focus.accommodation = True
                if re.search(r'\b(country|nationality|allowed|eligible)\b', normalized):
                    state.req_focus.country = True
        
        elif re.search(r'\b(scholarship|waiver|type-?a|type-?b|type-?c|type-?d|partial|stipend|csc|university scholarship)\b', normalized):
            state.intent = self.INTENT_SCHOLARSHIP
            state.wants_scholarship = True
            state.confidence += 0.4
            
            if re.search(r'\bcsc\b', normalized):
                state.scholarship_focus.csc = True
                state.scholarship_focus.any = False
            if re.search(r'\buniversity scholarship\b', normalized):
                state.scholarship_focus.university = True
                state.scholarship_focus.any = False
        
        elif re.search(r'\b(cheapest|lowest|compare|comparison|compare \d+)\b', normalized):
            state.intent = self.INTENT_COMPARISON
            state.wants_fees = True
            state.confidence += 0.4
        
        elif re.search(r'\b(fee|fees|tuition|cost|price|how much|budget|per year|per month|application fee|calculate fees?)\b', normalized):
            state.intent = self.INTENT_FEES
            state.wants_fees = True
            state.confidence += 0.4
        
        else:
            state.intent = self.INTENT_GENERAL
        
        # Extract slots
        # Degree level
        if re.search(r'\b(bachelor|undergrad|undergraduate|b\.?sc|bsc|b\.?s|bs|b\.?a|ba|beng|b\.?eng)\b', normalized):
            state.degree_level = "Bachelor"
            state.confidence += 0.2
        elif re.search(r'\b(master|masters|postgrad|post-graduate|graduate|msc|m\.?sc|ms|m\.?s|ma|m\.?a|mba)\b', normalized):
            state.degree_level = "Master"
            state.confidence += 0.2
        elif re.search(r'\b(phd|ph\.?d|doctorate|doctoral|dphil)\b', normalized):
            state.degree_level = "PhD"
            state.confidence += 0.2
        elif re.search(r'\b(language\s+program|language\s+course|non-?degree|foundation|foundation\s+program)\b', normalized):
            state.degree_level = "Language"
            state.confidence += 0.2
        elif re.search(r'\b(diploma|associate|assoc)\b', normalized):
            state.degree_level = "Diploma"
            state.confidence += 0.2
        
        # Teaching language
        if re.search(r'\b(english|english-?taught|english\s+program)\b', normalized):
            state.teaching_language = "English"
        elif re.search(r'\b(chinese|chinese-?taught|chinese\s+program|mandarin)\b', normalized):
            state.teaching_language = "Chinese"
        
        # Intake term
        if re.search(r'\b(mar(ch)?|spring)\b', normalized):
            state.intake_term = "March"
            state.confidence += 0.2
        elif re.search(r'\b(sep(t|tember)?|fall|autumn)\b', normalized):
            state.intake_term = "September"
            state.confidence += 0.2
        
        # Intake year
        year_match = re.search(r'\b(20[2-9]\d)\b', normalized)
        if year_match:
            state.intake_year = int(year_match.group(1))
            state.confidence += 0.2
        
        # Duration
        duration_years, constraint = self.parse_duration(normalized)
        if duration_years:
            state.duration_years_target = duration_years
            state.duration_constraint = constraint
        
        # Location
        city_match = re.search(r'\bin\s+([a-z]+(?:[\s-][a-z]+)*)\b', normalized)
        if city_match:
            city_candidate = city_match.group(1)
            # Common Chinese cities
            if city_candidate in ["guangzhou", "beijing", "shanghai", "shenzhen", "hangzhou", "nanjing", "chengdu", "xian", "wuhan"]:
                state.city = city_candidate.title()
        
        province_match = re.search(r'\b(guangdong|jiangsu|zhejiang|sichuan|shaanxi|hubei|shandong|hunan)\b', normalized)
        if province_match:
            state.province = province_match.group(1).title()
        
        # Budget
        budget_match = re.search(r'\b(budget|max|maximum|under|below|less than|up to)\s*\$?(\d+(?:\.\d+)?)\s*(?:usd|rmb|yuan|cn)?\b', normalized)
        if budget_match:
            state.budget_max = float(budget_match.group(2))
        
        # Earliest/ASAP
        if re.search(r'\b(earliest|asap|as soon as possible|soonest)\b', normalized):
            state.wants_earliest = True
        
        # Major query extraction (avoid "university/universities/database/list/program/course/major/majors")
        # CRITICAL: Never treat "master"/"bachelor"/"degree" as majors
        degree_words = {"master", "masters", "bachelor", "bachelors", "degree", "phd", "doctorate", "language", "diploma", "undergrad", "graduate", "postgrad", "postgraduate"}
        stop_words = {"university", "universities", "database", "list", "program", "programs", "course", "courses", "major", "majors", "show", "all", "available", "what", "which"}
        words = normalized.split()
        major_words = [w for w in words if w not in stop_words and w not in degree_words and len(w) > 2]
        if major_words and state.intent not in [self.INTENT_LIST_UNIVERSITIES, self.INTENT_LIST_PROGRAMS]:
            # Try to extract subject/major
            cleaned = " ".join(major_words)
            # Remove degree level, intake terms, etc.
            cleaned = re.sub(r'\b(bachelor|bachelors|master|masters|phd|language|diploma|march|september|english|chinese|fee|fees|tuition|cost|price|degree)\b', '', cleaned)
            cleaned = cleaned.strip()
            if cleaned and len(cleaned) > 2:
                state.major_query = cleaned
                state.confidence += 0.2
        
        # University query extraction
        uni_patterns = [
            r'\b(at|in|from)\s+([A-Z][a-zA-Z\s&]+(?:University|College|Institute|Medical|Normal))',
            r'\b([A-Z][a-zA-Z\s&]+(?:University|College|Institute|Medical|Normal))\b'
        ]
        for pattern in uni_patterns:
            match = re.search(pattern, query)  # Use original query for capitalization
            if match:
                uni_candidate = match.group(2) if len(match.groups()) > 1 else match.group(1)
                if uni_candidate and len(uni_candidate.strip()) > 3:
                    state.university_query = uni_candidate.strip()
                    state.confidence += 0.2
                    break
        
        # Cap confidence at 1.0
        state.confidence = min(1.0, state.confidence)
        
        # Handle mid-conversation intent changes
        if prev_state:
            change_indicators = re.search(r'\b(instead|actually|change|now|switch|no,|no\s+)\b', normalized)
            if change_indicators:
                # Override intent if new one detected
                if state.intent != self.INTENT_GENERAL:
                    # Clear conflicting slots unless re-stated
                    if state.degree_level and state.degree_level != prev_state.degree_level:
                        # Clear major_query, duration, intake filters unless re-stated
                        if not state.major_query:
                            state.major_query = None
                        if not state.duration_years_target:
                            state.duration_years_target = None
                        if not state.intake_term:
                            state.intake_term = None
            else:
                # If no change indicators and prev_state has intent, preserve it
                if prev_state.intent != self.INTENT_GENERAL and state.intent == self.INTENT_GENERAL:
                    state.intent = prev_state.intent
                    state.wants_scholarship = prev_state.wants_scholarship
                    state.wants_fees = prev_state.wants_fees
                    state.wants_requirements = prev_state.wants_requirements
        
        return state
    
    def route_stage2_llm(self, query: str, conversation_history: List[Dict[str, str]], prev_state: Optional[PartnerQueryState] = None) -> PartnerQueryState:
        """
        Stage 2: LLM extraction (only called if confidence < 0.75)
        Returns JSON with slot values
        """
        # Build conversation text
        conversation_text = ""
        for msg in conversation_history[-16:]:
            role = msg.get('role', '')
            content = msg.get('content', '')
            conversation_text += f"{role}: {content}\n"
        
        # LLM prompt for JSON extraction
        extraction_prompt = f"""Extract information from this conversation. Output ONLY valid JSON with these exact fields:
{{
  "intent": "PAGINATION" | "LIST_UNIVERSITIES" | "LIST_PROGRAMS" | "ADMISSION_REQUIREMENTS" | "SCHOLARSHIP" | "FEES" | "COMPARISON" | "PROGRAM_DETAILS" | "GENERAL",
  "degree_level": "Language" | "Non-degree" | "Bachelor" | "Master" | "PhD" | "Diploma" | null,
  "major_query": string or null,
  "university_query": string or null,
  "teaching_language": "English" | "Chinese" | "Any" | null,
  "intake_term": "March" | "September" | "Any" | null,
  "intake_year": number or null,
  "duration_years_target": number or null,
  "duration_constraint": "exact" | "min" | "max" | "approx" | null,
  "wants_requirements": boolean,
  "wants_fees": boolean,
  "wants_scholarship": boolean,
  "wants_list": boolean,
  "page_action": "none" | "next" | "prev" | "first",
  "city": string or null,
  "province": string or null,
  "country": string or null,
  "budget_max": number or null,
  "wants_earliest": boolean
}}

RULES:
- Extract ONLY what the user explicitly stated. Do NOT guess.
- For major_query: Extract subject/major (e.g., "computer science", "pharmacy"). NEVER extract "university/universities/database/list/program/course/major/majors".
- For university_query: Extract university name as written.
- For teaching_language: ONLY set if user explicitly said "English" or "Chinese".

Conversation:
{conversation_text}

Output ONLY valid JSON, no other text:"""
        
        try:
            response = self.openai_service.chat_completion(
                messages=[
                    {"role": "system", "content": "You are a JSON extractor. Output only valid JSON matching the exact schema."},
                    {"role": "user", "content": extraction_prompt}
                ],
                temperature=0.0
            )
            
            content = response.choices[0].message.content.strip()
            # Remove markdown code blocks
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
            
            extracted = json.loads(content)
            
            # Build state from LLM output
            llm_state = PartnerQueryState()
            llm_state.intent = extracted.get("intent", "GENERAL")
            llm_state.degree_level = extracted.get("degree_level")
            llm_state.major_query = extracted.get("major_query")
            llm_state.university_query = extracted.get("university_query")
            llm_state.teaching_language = extracted.get("teaching_language")
            llm_state.intake_term = extracted.get("intake_term")
            llm_state.intake_year = extracted.get("intake_year")
            llm_state.duration_years_target = extracted.get("duration_years_target")
            llm_state.duration_constraint = extracted.get("duration_constraint")
            llm_state.wants_requirements = extracted.get("wants_requirements", False)
            llm_state.wants_fees = extracted.get("wants_fees", False)
            llm_state.wants_scholarship = extracted.get("wants_scholarship", False)
            llm_state.wants_list = extracted.get("wants_list", False)
            llm_state.page_action = extracted.get("page_action", "none")
            llm_state.city = extracted.get("city")
            llm_state.province = extracted.get("province")
            llm_state.country = extracted.get("country")
            llm_state.budget_max = extracted.get("budget_max")
            llm_state.wants_earliest = extracted.get("wants_earliest", False)
            
            return llm_state
            
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            print(f"ERROR: LLM extraction failed: {e}")
            return PartnerQueryState()
    
    def _fuzzy_match_degree_level(self, query: str) -> Optional[str]:
        """
        Fuzzy match degree level using string similarity.
        Returns degree_level if similarity ≥ 0.75, None otherwise.
        """
        normalized = self.normalize_query(query).strip()
        # Remove punctuation
        normalized = re.sub(r'[^\w\s]', '', normalized)
        words = normalized.split()
        
        # Must be single word or very short (e.g., "Bachelor", "Masters", "PhD")
        if len(words) > 2:
            return None
        
        # Target degree levels
        degree_targets = {
            "Bachelor": ["bachelor", "bsc", "bs", "ba", "beng", "b.eng", "undergrad", "undergraduate"],
            "Master": ["master", "masters", "msc", "ms", "ma", "mba", "m.sc", "m.s", "m.a", "postgrad", "postgraduate", "graduate"],
            "PhD": ["phd", "ph.d", "doctorate", "doctoral", "dphil"],
            "Language": ["language", "nondegree", "non-degree", "foundation"],
            "Diploma": ["diploma", "associate", "assoc"]
        }
        
        # First try exact/regex match
        if re.match(r'^(bachelor|bacheler|bsc|bs|ba|undergrad|undergraduate)$', normalized):
            return "Bachelor"
        elif re.match(r'^(master|masters|msc|ms|ma|mba|postgrad|postgraduate|graduate)$', normalized):
            return "Master"
        elif re.match(r'^(phd|ph\.?d|doctorate|doctoral|dphil)$', normalized):
            return "PhD"
        elif re.match(r'^(language|non.?degree|foundation)$', normalized):
            return "Language"
        elif re.match(r'^(diploma|associate|assoc)$', normalized):
            return "Diploma"
        
        # Fuzzy matching with similarity threshold
        best_match = None
        best_score = 0.0
        threshold = 0.75
        
        for degree_level, variants in degree_targets.items():
            for variant in variants:
                score = SequenceMatcher(None, normalized, variant).ratio()
                if score > best_score:
                    best_score = score
                    best_match = degree_level
        
        if best_score >= threshold:
            return best_match
        
        return None
    
    def _is_single_word_degree(self, query: str) -> Optional[str]:
        """Check if query is ONLY a degree word. Uses fuzzy matching."""
        return self._fuzzy_match_degree_level(query)
    
    def _detect_clarification_mode(self, conversation_history: List[Dict[str, str]]) -> Tuple[bool, Optional[str]]:
        """
        Detect if we're in clarification mode by checking if last assistant message was a question.
        Returns (is_clarifying, pending_slot)
        """
        if len(conversation_history) < 2:
            return False, None
        
        # Check last assistant message
        last_assistant_msg = None
        for msg in reversed(conversation_history[-4:]):  # Check last 4 messages
            if msg.get('role') == 'assistant':
                last_assistant_msg = msg.get('content', '')
                break
        
        if not last_assistant_msg:
            return False, None
        
        # Check if it's a clarification question
        question_patterns = [
            (r'which level|which degree|degree level', 'degree_level'),
            (r'which intake|which term|intake', 'intake_term'),
            (r'which university|which program|university|program', 'target'),
            (r'which subject|which major|subject|major', 'major_query'),
        ]
        
        for pattern, slot in question_patterns:
            if re.search(pattern, last_assistant_msg.lower()):
                return True, slot
        
        return False, None
    
    def route(self, query: str, conversation_history: List[Dict[str, str]], prev_state: Optional[PartnerQueryState] = None) -> PartnerQueryState:
        """
        Two-stage routing: rules first, then LLM if confidence < 0.75
        Handles clarification mode and intent locking.
        """
        # CLARIFICATION SHORT-CIRCUIT: If prev_state.pending_slot is set, handle directly
        # DO NOT call router.route() logic, DO NOT call LLM, DO NOT re-detect intent
        if prev_state and prev_state.pending_slot:
            state = PartnerQueryState()
            state.intent = prev_state.intent
            state.wants_scholarship = prev_state.wants_scholarship
            state.wants_fees = prev_state.wants_fees
            state.wants_requirements = prev_state.wants_requirements
            state.wants_list = prev_state.wants_list
            state.req_focus = prev_state.req_focus
            state.scholarship_focus = prev_state.scholarship_focus
            state.intake_term = prev_state.intake_term
            state.intake_year = prev_state.intake_year
            state.university_query = prev_state.university_query
            state.major_query = prev_state.major_query
            state.teaching_language = prev_state.teaching_language
            state.degree_level = prev_state.degree_level
            
            if prev_state.pending_slot == "degree_level":
                matched_degree = self._fuzzy_match_degree_level(query)
                if matched_degree:
                    state.degree_level = matched_degree
                    state.pending_slot = None
                    state.is_clarifying = False
                    state.confidence = 1.0
                    state.major_query = None  # Ensure degree words never become majors
                else:
                    state.pending_slot = prev_state.pending_slot
                    state.is_clarifying = True
            return state
        
        # Check if we're in clarification mode
        is_clarifying, pending_slot = self._detect_clarification_mode(conversation_history)
        
        # Check if this is a single-word degree reply
        single_degree = self._fuzzy_match_degree_level(query)
        
        # If in clarification mode and single-word degree, handle specially
        if is_clarifying and single_degree and pending_slot in ['degree_level', 'target']:
            # Inherit intent from prev_state, only set degree_level
            state = PartnerQueryState()
            if prev_state:
                state.intent = prev_state.intent  # LOCK INTENT
                state.wants_scholarship = prev_state.wants_scholarship
                state.wants_fees = prev_state.wants_fees
                state.wants_requirements = prev_state.wants_requirements
                state.wants_list = prev_state.wants_list
                state.req_focus = prev_state.req_focus
                state.scholarship_focus = prev_state.scholarship_focus
                # Inherit other slots
                state.intake_term = prev_state.intake_term
                state.intake_year = prev_state.intake_year
                state.university_query = prev_state.university_query
                state.major_query = prev_state.major_query
            state.degree_level = single_degree
            state.confidence = 1.0  # High confidence for clarification replies
            state.is_clarifying = False  # Clear clarification mode after answer
            return state
        
        # If in clarification mode, inherit intent but allow slot updates
        if is_clarifying and prev_state:
            # Start with prev_state, update only relevant slots
            state = PartnerQueryState()
            state.intent = prev_state.intent  # LOCK INTENT
            state.wants_scholarship = prev_state.wants_scholarship
            state.wants_fees = prev_state.wants_fees
            state.wants_requirements = prev_state.wants_requirements
            state.wants_list = prev_state.wants_list
            state.req_focus = prev_state.req_focus
            state.scholarship_focus = prev_state.scholarship_focus
            # Run rules to extract new slots
            rules_state = self.route_stage1_rules(query, prev_state)
            # Merge: keep intent locked, update slots
            state.degree_level = rules_state.degree_level or prev_state.degree_level
            state.major_query = rules_state.major_query or prev_state.major_query
            state.university_query = rules_state.university_query or prev_state.university_query
            state.intake_term = rules_state.intake_term or prev_state.intake_term
            state.intake_year = rules_state.intake_year or prev_state.intake_year
            state.teaching_language = rules_state.teaching_language or prev_state.teaching_language
            state.confidence = 0.9  # High confidence in clarification mode
            state.is_clarifying = False  # Clear after processing
            # DO NOT call LLM in clarification mode
            return state
        
        # Normal routing
        # Stage 1: Rules
        rules_state = self.route_stage1_rules(query, prev_state)
        
        # CRITICAL: If fuzzy match found a degree, ALWAYS use it and ALWAYS clear major_query
        if single_degree:
            rules_state.degree_level = single_degree
            rules_state.major_query = None  # ALWAYS clear - degree words NEVER become majors
            rules_state.confidence = max(rules_state.confidence, 0.8)  # High confidence for degree match
        
        # Prevent "master"/"bachelor" from being treated as majors (fuzzy check)
        if rules_state.major_query:
            major_lower = rules_state.major_query.lower()
            degree_words = {"master", "masters", "bachelor", "bachelors", "degree", "phd", "doctorate", "language", "diploma", "undergrad", "graduate", "postgrad"}
            # Check exact match
            if major_lower in degree_words or any(word in degree_words for word in major_lower.split()):
                rules_state.major_query = None  # Clear invalid major_query
            else:
                # Check fuzzy similarity
                for degree_word in degree_words:
                    if SequenceMatcher(None, major_lower, degree_word).ratio() >= 0.75:
                        rules_state.major_query = None  # Clear invalid major_query
                        break
        
        # Stage 2: LLM only if confidence < 0.75 AND NOT in clarification mode AND NOT single-word degree
        # LLM BAN: Forbidden when pending_slot, short messages (≤2 words), or clarification mode
        query_words = len(query.split())
        is_short_message = query_words <= 2
        
        needs_llm = (
            not is_clarifying and
            not single_degree and
            not is_short_message and  # BAN LLM for short messages
            (rules_state.confidence < 0.75 or
            (rules_state.intent == self.INTENT_ADMISSION_REQUIREMENTS and not rules_state.university_query and not (rules_state.degree_level and rules_state.major_query)) or
            (rules_state.intent == self.INTENT_COMPARISON and not rules_state.university_query and not rules_state.major_query))
        )
        
        if needs_llm:
            try:
                print(f"DEBUG: Router calling LLM for extraction (confidence={rules_state.confidence})...")
                llm_state = self.route_stage2_llm(query, conversation_history, prev_state)
                print(f"DEBUG: LLM extraction returned: intent={llm_state.intent}")
            except Exception as e:
                import traceback
                print(f"ERROR: LLM extraction failed: {e}")
                traceback.print_exc()
                # Fallback to rules state if LLM fails
                llm_state = rules_state
            # Merge: rules win unless rules slot is invalid
            merged = PartnerQueryState()
            # Lock intent from prev_state if exists and no change indicators
            if prev_state and prev_state.intent != self.INTENT_GENERAL:
                change_indicators = re.search(r'\b(instead|actually|change|now|switch|no,|no\s+)\b', self.normalize_query(query))
                if not change_indicators:
                    merged.intent = prev_state.intent  # LOCK INTENT
                    merged.wants_scholarship = prev_state.wants_scholarship
                    merged.wants_fees = prev_state.wants_fees
                    merged.wants_requirements = prev_state.wants_requirements
                else:
                    merged.intent = rules_state.intent if rules_state.intent != self.INTENT_GENERAL else llm_state.intent
            else:
                merged.intent = rules_state.intent if rules_state.intent != self.INTENT_GENERAL else llm_state.intent
            merged.confidence = max(rules_state.confidence, 0.5)  # LLM gives at least 0.5
            
            # Merge slots: rules win unless invalid
            merged.degree_level = rules_state.degree_level or llm_state.degree_level
            # Prevent degree words from being treated as majors
            degree_words = {"master", "masters", "bachelor", "bachelors", "degree", "phd", "doctorate", "language", "diploma"}
            major_candidate = rules_state.major_query if rules_state.major_query and rules_state.major_query not in ["university", "universities", "database", "list", "program", "course", "major", "majors"] else (llm_state.major_query or rules_state.major_query)
            if major_candidate and major_candidate.lower() not in degree_words and not any(word in degree_words for word in major_candidate.lower().split()):
                merged.major_query = major_candidate
            else:
                merged.major_query = None
            merged.university_query = rules_state.university_query or llm_state.university_query
            merged.teaching_language = rules_state.teaching_language or llm_state.teaching_language
            merged.intake_term = rules_state.intake_term or llm_state.intake_term
            merged.intake_year = rules_state.intake_year or llm_state.intake_year
            merged.duration_years_target = rules_state.duration_years_target or llm_state.duration_years_target
            merged.duration_constraint = rules_state.duration_constraint or llm_state.duration_constraint
            merged.wants_requirements = rules_state.wants_requirements or llm_state.wants_requirements
            merged.wants_fees = rules_state.wants_fees or llm_state.wants_fees
            merged.wants_scholarship = rules_state.wants_scholarship or llm_state.wants_scholarship
            merged.wants_list = rules_state.wants_list or llm_state.wants_list
            merged.page_action = rules_state.page_action if rules_state.page_action != "none" else llm_state.page_action
            merged.city = rules_state.city or llm_state.city
            merged.province = rules_state.province or llm_state.province
            merged.country = rules_state.country or llm_state.country
            merged.budget_max = rules_state.budget_max or llm_state.budget_max
            merged.wants_earliest = rules_state.wants_earliest or llm_state.wants_earliest
            
            # Merge req_focus and scholarship_focus
            if llm_state.wants_requirements:
                merged.req_focus = llm_state.req_focus
            else:
                merged.req_focus = rules_state.req_focus
            
            if llm_state.wants_scholarship:
                merged.scholarship_focus = llm_state.scholarship_focus
            else:
                merged.scholarship_focus = rules_state.scholarship_focus
            
            return merged
        
        # If returning rules_state directly, lock intent from prev_state if exists
        if prev_state and prev_state.intent != self.INTENT_GENERAL:
            change_indicators = re.search(r'\b(instead|actually|change|now|switch|no,|no\s+)\b', self.normalize_query(query))
            if not change_indicators and rules_state.intent == self.INTENT_GENERAL:
                rules_state.intent = prev_state.intent  # LOCK INTENT
                rules_state.wants_scholarship = prev_state.wants_scholarship
                rules_state.wants_fees = prev_state.wants_fees
                rules_state.wants_requirements = prev_state.wants_requirements
        
        return rules_state
    
    def needs_clarification(self, intent: str, state: PartnerQueryState) -> Tuple[bool, Optional[str]]:
        """
        Determine if clarification is needed and return the best question.
        Returns (needs_clarification: bool, question: Optional[str])
        """
        if intent == self.INTENT_LIST_UNIVERSITIES:
            # If no filters at all, ask for degree level and intake
            if not state.degree_level and not state.intake_term and not state.city and not state.province:
                state.pending_slot = "degree_level"  # Set pending_slot
                state.is_clarifying = True
                return True, "Which level (Language/Bachelor/Master/PhD) and which intake (March/September)?"
            # If user asked "in Guangzhou" only, no question needed
            return False, None
        
        elif intent == self.INTENT_LIST_PROGRAMS:
            # If missing both degree_level and major_query
            if not state.degree_level and not state.major_query:
                state.pending_slot = "degree_level"  # Set pending_slot
                state.is_clarifying = True
                return True, "Which degree level and which subject/major?"
            return False, None
        
        elif intent == self.INTENT_ADMISSION_REQUIREMENTS:
            # If no target (no university_query and no (degree_level+major_query))
            if not state.university_query and not (state.degree_level and state.major_query):
                state.pending_slot = "target"  # Set pending_slot
                state.is_clarifying = True
                return True, "Which university or which program (degree + major)?"
            return False, None
        
        elif intent == self.INTENT_SCHOLARSHIP:
            # SCHOLARSHIP intent requires degree_level
            if not state.degree_level:
                state.pending_slot = "degree_level"  # Set pending_slot
                state.is_clarifying = True
                return True, "Which degree level (Language/Bachelor/Master/PhD)?"
            return False, None
        
        elif intent in [self.INTENT_FEES, self.INTENT_COMPARISON]:
            # If user says "calculate fees" without a target
            if not state.university_query and not state.major_query and not (state.degree_level and state.major_query):
                state.pending_slot = "target"  # Set pending_slot
                state.is_clarifying = True
                return True, "Which university/program/intake should I calculate for?"
            return False, None
        
        # If wants_earliest=True and intake_term/year missing, do NOT ask (DB will order by deadline)
        if state.wants_earliest and not state.intake_term and not state.intake_year:
            return False, None
        
        # Never ask about intake_year unless user requests a specific year
        return False, None

