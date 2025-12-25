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
from app.services.router import PartnerRouter
from app.services.slot_schema import PartnerQueryState
from difflib import SequenceMatcher
from app.models import University, Major, ProgramIntake, ProgramDocument, ProgramIntakeScholarship, Scholarship, ProgramExamRequirement, IntakeTerm
from datetime import datetime, date
import time
import json
import re
import hashlib


@dataclass
class PaginationState:
    """State for list query pagination"""
    results: List[int]  # Store IDs only (program_intake IDs OR university IDs OR major IDs)
    result_type: str  # "intake_ids", "university_ids", or "major_ids"
    offset: int
    total: int
    page_size: int
    intent: str
    timestamp: float
    last_displayed: Optional[List[Dict[str, Any]]] = None  # Last displayed intakes for follow-up questions (full objects for duration questions)


@dataclass
class PendingState:
    """State for pending clarification questions"""
    intent: str
    missing_slots: List[str]
    created_at: float
    partial_state: Dict[str, Any]  # Store partial state (degree_level, intake_term, etc.) that was already extracted


class PartnerAgent:
    """Partner agent for answering questions about MalishaEdu universities and programs"""
    
    # CLASS-LEVEL cache to persist across requests (since agent is instantiated per request)
    _class_state_cache: Dict[Tuple[Optional[int], str], Dict[str, Any]] = {}
    _class_state_cache_ttl: float = 1800.0  # 30 minutes TTL
    
    # List query caps to prevent huge context
    MAX_LIST_UNIVERSITIES = 12
    MAX_LIST_INTAKES = 24
    
    PARTNER_SYSTEM_PROMPT = """You are the MalishaEdu Partner Agent. Use ONLY DATABASE CONTEXT provided. Never invent or assume facts.

CRITICAL RULES:
- ONLY mention universities/programs EXPLICITLY in DATABASE CONTEXT. If no matches, say "Not provided in our partner database."
- Use CURRENT DATE from DATABASE CONTEXT for deadline checks. Only suggest upcoming deadlines.
- If DB fields are null/unknown, output "Not provided in our partner database."
- Never treat "university/universities/database/list/program/course/major/majors" as a major_query.
- Be CONCISE. Use short templates. Label periods (per-year/per-semester/one-time/annual).
- For fees: Show tuition, accommodation (with period), insurance, application, medical, visa extension. Do NOT guess missing fees.
- For requirements: Show docs, exams, bank, age, inside-china, country, accommodation, deadlines. Only sections with req_focus=true.
- For scholarships: Show coverage, allowances, deadlines. Specify CSC vs university scholarship.
- For comparison: Side-by-side for up to 3 programs. Require targets or ask one question.
- Ask at most ONE clarifying question per turn, only when required to run a DB query.
"""

    def __init__(self, db: Session):
        self.db = db
        self.db_service = DBQueryService(db)
        self.tavily_service = TavilyService()
        self.openai_service = OpenAIService()
        self.router = PartnerRouter(self.openai_service)
        
        # Unified lazy caches with TTL (loaded only when needed for fuzzy matching)
        self._universities_cache: Optional[List[Dict[str, Any]]] = None
        self._majors_cache: Optional[List[Dict[str, Any]]] = None
        self._cache_ts: Optional[float] = None
        self._cache_ttl_seconds: float = 900.0  # 15 minutes TTL
        
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
    
        # Note: State cache is now class-level (PartnerAgent._class_state_cache) to persist across requests
        
        # Legacy pending slot cache (for backward compatibility during migration)
        self._pending_slot_cache: Dict[Tuple[Optional[int], str], Dict[str, Any]] = {}
        self._pending_slot_ttl: float = 600.0  # 10 minutes TTL
        
        # Legacy pending clarification state (for backward compatibility)
        self._pending: Dict[str, PendingState] = {}
        self._pending_ttl: float = 600.0  # 10 minutes TTL
    
    def _get_universities_cached(self, force_reload: bool = False) -> List[Dict[str, Any]]:
        """Lazy load university cache with TTL (only loads when needed for fuzzy matching)"""
        import time
        now = time.time()
        
        if force_reload or self._universities_cache is None or (self._cache_ts and (now - self._cache_ts) > self._cache_ttl_seconds):
            # Load only name, id, aliases for fuzzy matching (lightweight)
            universities = self.db_service.search_universities(is_partner=True, limit=1000)  # Reasonable limit
            self._universities_cache = [
                {
                    "id": uni.id,
                    "name": uni.name,
                    "name_cn": uni.name_cn,
                    "aliases": uni.aliases if isinstance(uni.aliases, list) else (json.loads(uni.aliases) if isinstance(uni.aliases, str) else []),
                }
                for uni in universities
            ]
            self._cache_ts = now
        
        return self._universities_cache
    
    def _get_uni_name_cache(self, force_reload: bool = False) -> List[Dict[str, Any]]:
        """Alias for backward compatibility"""
        return self._get_universities_cached(force_reload)
    
    def _infer_earliest_intake_term(self, current_date: Optional[date] = None) -> Optional[str]:
        """
        Infer earliest intake term (March/September) based on current month.
        - Dec/Jan/Feb => March is earliest
        - Mar/Apr/May/Jun/Jul/Aug => September is earliest  
        - Sep/Oct/Nov => next March is earliest
        """
        if current_date is None:
            current_date = date.today()
        
        month = current_date.month
        if month in [12, 1, 2]:
            return "March"
        elif month in [3, 4, 5, 6, 7, 8]:
            return "September"
        else:  # 9, 10, 11
            return "March"  # Next March
    
    def _get_majors_cached(self, force_reload: bool = False) -> List[Dict[str, Any]]:
        """
        Lazy load major cache with TTL (ONLY loads when fuzzy matching is actually needed).
        This is called ONLY when _fuzzy_match_major needs it, not on every request.
        """
        import time
        now = time.time()
        
        if force_reload or self._majors_cache is None or (self._cache_ts and (now - self._cache_ts) > self._cache_ttl_seconds):
            # Load minimal fields for fuzzy matching (bounded to 2000 entries)
            majors = self.db_service.search_majors(limit=2000)  # Max 2000 entries
            self._majors_cache = []
            for major in majors:
                # Normalize keywords (same as _load_all_majors did)
                keywords = getattr(major, 'keywords', None)
                if isinstance(keywords, str):
                    try:
                        keywords = json.loads(keywords)
                    except (json.JSONDecodeError, ValueError):
                        keywords = [k.strip() for k in keywords.split(',') if k.strip()] if keywords else []
                elif not isinstance(keywords, list):
                    keywords = []
                
                # Normalize aliases
                aliases = getattr(major, 'aliases', None)
                if isinstance(aliases, str):
                    try:
                        aliases = json.loads(aliases)
                    except (json.JSONDecodeError, ValueError):
                        aliases = [a.strip() for a in aliases.split(',') if a.strip()] if aliases else []
                elif not isinstance(aliases, list):
                    aliases = []
                
                self._majors_cache.append({
                    "id": major.id,
                    "name": major.name,
                    "name_cn": getattr(major, 'name_cn', None),
                    "keywords": keywords,
                    "aliases": aliases,
                    "degree_level": str(major.degree_level) if major.degree_level else None,
                    "teaching_language": str(major.teaching_language) if major.teaching_language else None,
                    "university_id": major.university_id,
                })
            self._cache_ts = now
        
        return self._majors_cache
    
    def _get_major_cache(self, force_reload: bool = False) -> List[Dict[str, Any]]:
        """Alias for backward compatibility"""
        return self._get_majors_cached(force_reload)
    
    def _get_pending_slot(self, partner_id: Optional[int], conversation_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """Get pending slot for conversation, checking TTL"""
        if not conversation_id:
            return None
        
        key = (partner_id, conversation_id)
        pending = self._pending_slot_cache.get(key)
        
        if pending:
            now = time.time()
            if (now - pending.get("timestamp", 0)) > self._pending_slot_ttl:
                # Expired, remove it
                del self._pending_slot_cache[key]
                return None
            return pending
        
        return None
    
    def _set_pending_slot(self, partner_id: Optional[int], conversation_id: Optional[str], slot: str, intent: str):
        """Set pending slot for conversation"""
        if not conversation_id:
            return
        
        key = (partner_id, conversation_id)
        self._pending_slot_cache[key] = {
            "slot": slot,
            "timestamp": time.time(),
            "intent": intent
        }
    
    def _clear_pending_slot(self, partner_id: Optional[int], conversation_id: Optional[str]):
        """Clear pending slot for conversation"""
        if not conversation_id:
            return
        
        key = (partner_id, conversation_id)
        if key in self._pending_slot_cache:
            del self._pending_slot_cache[key]
    
    def _get_conv_key(self, partner_id: Optional[int], conversation_id: Optional[str], 
                      conversation_history: List[Dict[str, str]], user_message: str = None) -> str:
        """
        Generate STABLE conversation key for pending state.
        CRITICAL: Must NOT include latest_user_message or any changing content.
        Use partner_id + conversation_id when both exist.
        If conversation_id is None, derive stable fallback from all user messages (not just latest).
        """
        # Primary: Use partner_id + conversation_id (most stable)
        if conversation_id:
            return f"partner:{partner_id}|conv:{conversation_id}"
        
        # Fallback: Hash all user messages concatenated (stable across clarification turns)
        all_user_messages = []
        for msg in conversation_history:
            if msg.get('role') == 'user':
                all_user_messages.append(msg.get('content', ""))
        
        if all_user_messages:
            # Use first user message as stable identifier (doesn't change on clarification replies)
            first_user_msg = all_user_messages[0][:200]  # First 200 chars
            key_str = f"partner:{partner_id}|first_msg:{first_user_msg}"
        else:
            # Last resort: use partner_id only
            key_str = f"partner:{partner_id}"
        
        return hashlib.sha1(key_str.encode()).hexdigest()[:16]
    
    def _get_pending_state(self, conv_key: str) -> Optional[PendingState]:
        """Get pending state for conversation, checking TTL"""
        pending = self._pending.get(conv_key)
        if not pending:
            return None
        
        now = time.time()
        if (now - pending.created_at) > self._pending_ttl:
            del self._pending[conv_key]
            return None
        
        return pending
    
    def _set_pending_state(self, conv_key: str, intent: str, missing_slots: List[str], 
                           full_state: PartnerQueryState):
        """Set pending state for conversation - stores FULL state snapshot"""
        # Convert full state to dict for storage (preserve all fields)
        partial_state_dict = {
            "degree_level": full_state.degree_level,
            "intake_term": full_state.intake_term,
            "university_query": full_state.university_query,
            "major_query": full_state.major_query,
            "teaching_language": full_state.teaching_language,
            "wants_scholarship": full_state.wants_scholarship,
            "wants_fees": full_state.wants_fees,
            "wants_requirements": full_state.wants_requirements,
            "wants_list": full_state.wants_list,
            "wants_earliest": full_state.wants_earliest,
            "city": full_state.city,
            "province": full_state.province,
            "intake_year": full_state.intake_year,
            "duration_years_target": full_state.duration_years_target,
            "duration_constraint": full_state.duration_constraint,
            "budget_max": full_state.budget_max,
            "req_focus": full_state.req_focus,
            "scholarship_focus": full_state.scholarship_focus,
        }
        self._pending[conv_key] = PendingState(
            intent=intent,
            missing_slots=missing_slots,
            created_at=time.time(),
            partial_state=partial_state_dict
        )
    
    def _get_cached_state(self, partner_id: Optional[int], conversation_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """Get unified cached state for conversation (state + pending info)"""
        if not conversation_id:
            print(f"DEBUG: _get_cached_state - conversation_id is None, returning None")
            return None
        key = (partner_id, conversation_id)
        print(f"DEBUG: _get_cached_state - key={key}, cache_keys={list(self._class_state_cache.keys())}")
        if key in self._class_state_cache:
            cached = self._class_state_cache[key]
            age = time.time() - cached.get("ts", 0)
            if age < self._class_state_cache_ttl:
                print(f"DEBUG: _get_cached_state - found cached state (age={age:.1f}s, pending={cached.get('pending') is not None})")
                return cached
            else:
                print(f"DEBUG: _get_cached_state - cached state expired (age={age:.1f}s > {self._class_state_cache_ttl}s)")
                del self._class_state_cache[key]
        else:
            print(f"DEBUG: _get_cached_state - key not found in cache")
        return None
    
    def _set_cached_state(self, partner_id: Optional[int], conversation_id: Optional[str], 
                         state: PartnerQueryState, pending: Optional[Dict[str, Any]] = None):
        """Cache unified state for conversation (state + pending info)"""
        if not conversation_id:
            return
        key = (partner_id, conversation_id)
        self._class_state_cache[key] = {
            "state": state,
            "pending": pending,
            "ts": time.time()
        }
    
    def _get_pending(self, partner_id: Optional[int], conversation_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """Get pending slot info from unified cache"""
        cached = self._get_cached_state(partner_id, conversation_id)
        return cached.get("pending") if cached else None
    
    def _set_pending(self, partner_id: Optional[int], conversation_id: Optional[str], 
                    slot: str, snapshot: PartnerQueryState):
        """Set pending slot with full state snapshot"""
        if not conversation_id:
            return
        cached = self._get_cached_state(partner_id, conversation_id)
        state = cached.get("state") if cached else snapshot
        
        pending_info = {
            "slot": slot,
            "snapshot": {
                "intent": snapshot.intent,
                "degree_level": snapshot.degree_level,
                "major_query": snapshot.major_query,
                "university_query": snapshot.university_query,
                "intake_term": snapshot.intake_term,
                "intake_year": snapshot.intake_year,
                "wants_earliest": snapshot.wants_earliest,
                "teaching_language": snapshot.teaching_language,
                "wants_scholarship": snapshot.wants_scholarship,
                "wants_fees": snapshot.wants_fees,
                "wants_free_tuition": getattr(snapshot, 'wants_free_tuition', False),
                "wants_requirements": snapshot.wants_requirements,
                "wants_deadline": getattr(snapshot, 'wants_deadline', False),
                "wants_list": getattr(snapshot, 'wants_list', False),
                "city": snapshot.city,
                "province": snapshot.province,
                "country": snapshot.country,
                "budget_max": snapshot.budget_max,
                "duration_years_target": snapshot.duration_years_target,
                "duration_constraint": snapshot.duration_constraint,
                "_duration_fallback_intake_ids": getattr(snapshot, '_duration_fallback_intake_ids', None),
                "_duration_fallback_available": getattr(snapshot, '_duration_fallback_available', None),
                "_university_candidates": getattr(snapshot, '_university_candidates', None),
                "_university_candidate_ids": getattr(snapshot, '_university_candidate_ids', None),
                "_suggested_major": getattr(snapshot, '_suggested_major', None),
                "req_focus": snapshot.req_focus.__dict__ if hasattr(snapshot.req_focus, '__dict__') else snapshot.req_focus,
                "scholarship_focus": snapshot.scholarship_focus.__dict__ if hasattr(snapshot.scholarship_focus, '__dict__') else snapshot.scholarship_focus,
            }
        }
        self._set_cached_state(partner_id, conversation_id, state, pending_info)
    
    def _consume_pending(self, partner_id: Optional[int], conversation_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """Consume and clear pending slot, return snapshot"""
        cached = self._get_cached_state(partner_id, conversation_id)
        if not cached or not cached.get("pending"):
            return None
        pending = cached["pending"]
        cached["pending"] = None
        self._set_cached_state(partner_id, conversation_id, cached.get("state"), None)
        return pending.get("snapshot")
    
    # Legacy methods for backward compatibility
    def _get_state_cache(self, partner_id: Optional[int], conversation_id: Optional[str]) -> Optional[PartnerQueryState]:
        """Get cached state for conversation (legacy)"""
        cached = self._get_cached_state(partner_id, conversation_id)
        return cached.get("state") if cached else None
    
    def _set_state_cache(self, partner_id: Optional[int], conversation_id: Optional[str], state: PartnerQueryState):
        """Cache state for conversation (legacy)"""
        self._set_cached_state(partner_id, conversation_id, state, None)
    
    def llm_extract_state(self, conversation_history: List[Dict[str, str]], today_date: date, prev_state: Optional[PartnerQueryState] = None) -> Dict[str, Any]:
        """
        ALWAYS-LLM extraction for intent/slots.
        Returns JSON dict with intent, slots, and confidence.
        DO NOT inject university/major lists - keep prompt small.
        """
        # Build conversation text (6 most recent turns ONLY - not whole history)
        conversation_text = ""
        for msg in conversation_history[-6:]:
            role = msg.get('role', '')
            content = msg.get('content', '')
            conversation_text += f"{role}: {content}\n"
        
        # Add prev_state summary if exists
        prev_state_summary = ""
        if prev_state:
            prev_state_summary = f"\nPrevious context: intent={prev_state.intent}, degree={prev_state.degree_level}, major={prev_state.major_query}, intake={prev_state.intake_term}"
        
        # Small LLM prompt for JSON extraction
        extraction_prompt = f"""Extract information from this conversation. Today's date: {today_date.strftime('%Y-%m-%d')}.

Output ONLY valid JSON with these exact fields:
{{
  "intent": "SCHOLARSHIP" | "FEES" | "REQUIREMENTS" | "LIST_PROGRAMS" | "LIST_UNIVERSITIES" | "GENERAL" | "PAGINATION",
  "degree_level": "Language" | "Bachelor" | "Master" | "PhD" | null,
  "major_raw": string or null,
  "university_raw": string or null,  # Can be full name, alias (e.g., "HIT" for "Harbin Institute of Technology"), or abbreviation
  "intake_term": "March" | "September" | null,
  "intake_year": number or null,
  "teaching_language": "English" | "Chinese" | null,
  "city": string or null,  # City name (e.g., "Guangzhou", "Beijing", "Shanghai", "Shenzhen", "Hangzhou", "Nanjing", "Chengdu", "Xian", "Wuhan", "Tianjin", "Dalian", "Qingdao")
  "province": string or null,  # Province name (e.g., "Guangdong", "Jiangsu", "Zhejiang", "Sichuan", "Shaanxi", "Hubei", "Shandong", "Hunan", "Beijing", "Shanghai", "Tianjin")
  "duration_years": number or null,
  "wants_earliest": boolean,
  "wants_scholarship": boolean,
  "wants_requirements": boolean,
  "wants_fees": boolean,
  "page_action": "next" | "prev" | "none",
  "confidence": number
}}

RULES:
- Use today's date ({today_date.strftime('%Y-%m-%d')}) to interpret "next intake", "earliest intake", "asap".
- Do NOT output major_raw = "scholarship info" / "fees" / "information". If unsure, null.
- Do NOT output university_raw = "list" / "all" / "database". If unsure, null.
- For university_raw: Extract university names, abbreviations, or aliases (e.g., "HIT" for "Harbin Institute of Technology", "BUAA" for "Beihang University"). Include common abbreviations and aliases.
- For degree_level: "Chinese Language", "English Language", "Mandarin Language", "Language Program", "Language Course", "Foundation Program","Chinese Language Program", "English Language Program", "Mandarin Language Program", "Non-degree Program" should all be set to degree_level="Language". If user says "Chinese Language" or "English Language", it means they want a Language program (degree_level="Language"), NOT a Bachelor/Master/PhD program taught in Chinese/English.
- For teaching_language: Extract "English" from phrases like "English", "English taught", "English-taught", "taught in English", "English program". Extract "Chinese" from  "Chinese taught", "Mandarin", "中文". CRITICAL: If user says "Chinese Language" or "English Language" as a degree level (major name), set degree_level="Language" but DO NOT set teaching_language. "Chinese Language" is a major/program name, NOT a teaching language requirement. Only set teaching_language if user explicitly mentions teaching language (e.g., "taught in Chinese", "English-taught program").
- For deadline questions (e.g., "application deadline", "when is the deadline"), set wants_deadline=true and intent can be GENERAL if no other intent is clear.
- CRITICAL: If user asks "which universities" or "list universities" or "how many universities" or "any university" (even with fee comparison like "lowest fees"), set intent="LIST_UNIVERSITIES" (NOT FEES). Set wants_fees=true if fees are mentioned.
- For fee-related questions (e.g., "tuition fee", "tuition", "fee", "cost", "price", "bank statement amount"), set wants_fees=true and intent=FEES (unless it's a "which universities" query).
- For scholarship questions (e.g., "scholarship", "scholarship info", "scholarship opportunity", "requiring CSC/CSCA", "requiring CSC", "requiring CSCA"), set wants_scholarship=true and intent=SCHOLARSHIP. Do NOT set wants_requirements=true for CSC/CSCA scholarship queries - "requiring CSC/CSCA" means scholarship requirement, not document requirements.
- For accommodation questions (e.g., "accommodation", "accommodation facility", "housing", "dormitory"), set wants_fees=true and intent=FEES (accommodation info is part of fees).
- For "offered majors", "offered subjects", "available majors", "list of majors", set intent=LIST_PROGRAMS.
- For city/province: Extract city names (e.g., "Guangzhou", "Beijing", "Shanghai", "Shenzhen", "Hangzhou", "Nanjing", "Chengdu", "Xian", "Wuhan", "Tianjin", "Dalian", "Qingdao") and province names (e.g., "Guangdong", "Jiangsu", "Zhejiang", "Sichuan", "Shaanxi", "Hubei", "Shandong", "Hunan", "Beijing", "Shanghai", "Tianjin"). Handle common typos (e.g., "guangzou" -> "Guangzhou", "guangjou" -> "Guangzhou"). City/province is NOT a university or major - it's a location filter.
- Output confidence in [0,1] based on how clear the user's intent is.
- For "next March intake", set intake_term="March" and wants_earliest=true, NOT page_action="next".

Conversation:
{conversation_text}{prev_state_summary}

Output ONLY valid JSON, no other text:"""
        
        try:
            response = self.openai_service.chat_completion(
                messages=[
                    {"role": "system", "content": "You are a JSON extractor. Output only valid JSON matching the exact schema."},
                    {"role": "user", "content": extraction_prompt}
                ],
                temperature=0.0
            )
            
            # Parse JSON response - OpenAI response is an object, not a dict
            if hasattr(response, 'choices') and len(response.choices) > 0:
                content = response.choices[0].message.content
            else:
                # Fallback to dict access if response is a dict
                content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")
            
            # Remove markdown code blocks if present
            content = re.sub(r'```json\s*', '', content)
            content = re.sub(r'```\s*', '', content)
            content = content.strip()
            
            extracted = json.loads(content)
            
            # POST-PROCESSING RULES (deterministic)
            # 1. If intent == PAGINATION, map to LIST_PROGRAMS or GENERAL
            if extracted.get("intent") == "PAGINATION":
                if extracted.get("page_action") in ["next", "prev"]:
                    extracted["intent"] = "LIST_PROGRAMS"
                else:
                    extracted["intent"] = "GENERAL"
            
            # 2. Remove stopwords from major_raw
            if extracted.get("major_raw"):
                major_clean = extracted["major_raw"].lower().strip()
                stopwords = {"scholarship", "info", "information", "fees", "tuition", "cost", 
                           "requirement", "requirements", "admission", "program", "course", "list", "all"}
                if major_clean in stopwords:
                    extracted["major_raw"] = None
                else:
                    # Clean stopwords from multi-word majors
                    words = major_clean.split()
                    filtered_words = [w for w in words if w not in stopwords]
                    if filtered_words:
                        extracted["major_raw"] = " ".join(filtered_words)
                    else:
                        extracted["major_raw"] = None
            
            # 3. If university_raw is "china", set country="China" instead
            if extracted.get("university_raw") and extracted["university_raw"].lower() in ["china", "chinese"]:
                extracted["country"] = "China"
                extracted["university_raw"] = None
            
            # 4. If message is ONLY language ("english/chinese/英文/中文/englsih/ingreji"), treat as slot fill
            # (This is handled in clarification logic, but ensure it doesn't break extraction)
            
            return extracted
        except (json.JSONDecodeError, KeyError, ValueError, AttributeError) as e:
            print(f"ERROR: llm_extract_state failed: {e}")
            # Return default dict
            return {
                "intent": "GENERAL",
                "degree_level": None,
                "major_raw": None,
                "university_raw": None,
                "intake_term": None,
                "intake_year": None,
                "teaching_language": None,
                "duration_years": None,
                "wants_earliest": False,
                "wants_scholarship": False,
                "wants_requirements": False,
                "wants_fees": False,
                "page_action": "none",
                "confidence": 0.0
            }
    
    def _clear_pending_state(self, conv_key: str):
        """Clear pending state for conversation"""
        if conv_key in self._pending:
            del self._pending[conv_key]
    
    def _get_university_name_by_id(self, university_id: int) -> str:
        """Get university name by ID using DBQueryService (lazy, no eager load)"""
        uni = self.db.query(University).filter(University.id == university_id).first()
        return uni.name if uni else "Unknown University"
    
    def _get_major_name_by_id(self, major_id: int) -> str:
        """Get major name by ID using DBQueryService (lazy, no eager load)"""
        major = self.db.query(Major).filter(Major.id == major_id).first()
        return major.name if major else "Unknown Major"
    
    def _expand_major_acronym(self, text: str) -> str:
        """
        Expand common major acronyms before fuzzy matching.
        Also strips trailing stopwords like "and", "&", ",".
        Handles dots in acronyms (e.g., "B.B.A." -> "BBA" -> "bachelor of business administration").
        """
        if not text:
            return text
        
        # Normalize dots in acronyms first (e.g., "B.B.A." -> "BBA")
        text = text.replace('.', '').strip()
        
        # Strip trailing stopwords
        text = re.sub(r'\s+(and|&|,)\s*$', '', text, flags=re.IGNORECASE).strip()
        
        text_lower = text.lower()
        
        # Acronym expansion map (includes ECE, BBA, LLB, MBBS, MD, MPhil, etc.)
        acronym_map = {
            # Computer Science & Engineering
            "cse": "computer science",
            "cs": "computer science",
            "ce": "computer engineering",
            "se": "software engineering",
            "ece": "electronics and communication engineering",
            "eee": "electrical engineering",
            "it": "information technology",
            "ai": "artificial intelligence",
            "ds": "data science",
            "ml": "machine learning",
            "cv": "computer vision",
            "nlp": "natural language processing",
            "bme": "biomedical engineering",
            "mse": "materials science engineering",
            # Sciences
            "chem": "chemistry",
            "bio": "biology",
            "phy": "physics",
            "math": "mathematics",
            "econ": "economics",
            "fin": "finance",
            # Business & Management
            "bba": "bachelor of business administration",
            "mba": "master of business administration",
            "bcom": "bachelor of commerce",
            "mcom": "master of commerce",
            # Law
            "llb": "bachelor of laws",
            "llm": "master of laws",
            "jd": "juris doctor",
            # Medicine & Health Sciences
            "mbbs": "bachelor of medicine and bachelor of surgery",
            "md": "doctor of medicine",
            "ms": "master of surgery",
            "dm": "doctor of medicine",
            "mch": "master of surgery",
            "bds": "bachelor of dental surgery",
            "mds": "master of dental surgery",
            "bpharm": "bachelor of pharmacy",
            "mpharm": "master of pharmacy",
            "bpt": "bachelor of physiotherapy",
            "mpt": "master of physiotherapy",
            "bsc nursing": "bachelor of science in nursing",
            "msc nursing": "master of science in nursing",
            "bvsc": "bachelor of veterinary science",
            "mvsc": "master of veterinary science",
            # Arts & Humanities
            "ba": "bachelor of arts",
            "ma": "master of arts",
            "bsc": "bachelor of science",
            "msc": "master of science",
            "btech": "bachelor of technology",
            "mtech": "master of technology",
            "bed": "bachelor of education",
            "med": "master of education",
            # Research Degrees
            "mphil": "master of philosophy",
            "phd": "doctor of philosophy",
            "dphil": "doctor of philosophy",
            "dsc": "doctor of science",
            "dlitt": "doctor of letters",
            # Architecture & Design
            "barch": "bachelor of architecture",
            "march": "master of architecture",
            # Other Professional Degrees
            "ca": "chartered accountant",
            "cpa": "certified public accountant",
            "cfa": "chartered financial analyst",
        }
        
        # Check if text is short (<=6 chars) or all-caps-like (mostly uppercase)
        # Also check for longer medical/law acronyms (MBBS, MPhil, etc.)
        is_short = len(text.replace(' ', '')) <= 6
        is_caps_like = len([c for c in text if c.isupper()]) > len([c for c in text if c.islower()]) if text else False
        is_long_acronym = len(text.replace(' ', '')) <= 8 and text.replace(' ', '').isupper()  # For MBBS, MPhil, etc.
        
        if is_short or is_caps_like or is_long_acronym:
            for abbrev, expansion in acronym_map.items():
                # Match whole word only (case-insensitive)
                pattern = r'\b' + re.escape(abbrev) + r'\b'
                if re.search(pattern, text_lower):
                    # Replace acronym with expansion
                    text = re.sub(pattern, expansion, text, flags=re.IGNORECASE)
                    break  # Only expand first match
        
        return text
    
    # ========== DETERMINISTIC PARSING HELPERS ==========
    
    def normalize_text(self, s: str) -> str:
        """Normalize text: lowercase, remove punctuation, collapse spaces"""
        if not s:
            return ""
        s = s.lower().strip()
        s = re.sub(r'[^\w\s]', '', s)  # Remove punctuation
        s = re.sub(r'\s+', ' ', s)  # Collapse spaces
        return s
    
    def fuzzy_pick(self, token: str, choices: List[str], cutoff: float = 0.75) -> Optional[str]:
        """
        Pick best matching choice using fuzzy similarity.
        Returns None if no match above cutoff.
        """
        if not token or not choices:
            return None
        
        token_norm = self.normalize_text(token)
        best_match = None
        best_score = 0.0
        
        for choice in choices:
            choice_norm = self.normalize_text(choice)
            ratio = SequenceMatcher(None, token_norm, choice_norm).ratio()
            if ratio > best_score:
                best_score = ratio
                best_match = choice
        
        if best_score >= cutoff:
            return best_match
        return None
    
    def parse_degree_level(self, text: str) -> Optional[str]:
        """
        Parse degree level with fuzzy mapping for synonyms/typos.
        Returns: "Bachelor", "Master", "PhD", "Language", or None
        """
        if not text:
            return None
        
        text_norm = self.normalize_text(text)
        
        # Exact/fuzzy matches for Bachelor
        bachelor_patterns = ["bachelor", "bsc", "b.sc", "undergraduate", "bachelov", "bacheller", "bachelar"]
        if any(pat in text_norm for pat in bachelor_patterns):
            return "Bachelor"
        
        # Fuzzy match for Bachelor (typos)
        bachelor_choices = ["bachelor", "bsc", "undergraduate"]
        if self.fuzzy_pick(text_norm, bachelor_choices, cutoff=0.75):
            return "Bachelor"
        
        # Exact/fuzzy matches for Master
        master_patterns = ["master", "msc", "m.sc", "ms", "masters"]
        if any(pat in text_norm for pat in master_patterns):
            return "Master"
        
        # Fuzzy match for Master
        master_choices = ["master", "msc", "masters"]
        if self.fuzzy_pick(text_norm, master_choices, cutoff=0.75):
            return "Master"
        
        # Exact/fuzzy matches for PhD
        phd_patterns = ["phd", "ph.d", "doctorate", "doctoral", "d.phil"]
        if any(pat in text_norm for pat in phd_patterns):
            return "PhD"
        
        # Exact/fuzzy matches for Language
        # Include "chinese language", "english language", etc. as Language programs
        language_patterns = ["language", "non-degree", "foundation", "preparatory", "chinese language", "english language"]
        if any(pat in text_norm for pat in language_patterns):
            return "Language"
        
        # Also check if text contains "language" as a word (e.g., "chinese language", "mandarin language")
        if "language" in text_norm:
            return "Language"
        
        return None
    
    def parse_intake_term(self, text: str) -> Optional[str]:
        """
        Parse intake term mapping: march/spring, sept/september/fall.
        Returns: "March" or "September" or None
        """
        if not text:
            return None
        
        text_norm = self.normalize_text(text)
        
        # March/Spring patterns
        if re.search(r'\b(mar(ch)?|spring|mar)\b', text_norm):
            return "March"
        
        # September/Fall patterns
        if re.search(r'\b(sep(t|tember)?|fall|autumn|sept)\b', text_norm):
            return "September"
        
        return None
    
    def parse_teaching_language(self, text: str) -> Optional[str]:
        """
        Parse teaching language mapping: english/english-taught, chinese/mandarin/中文.
        Handles typos (englsih, englsh) and Banglish (ingreji, english e).
        Returns: "English" or "Chinese" or None
        """
        if not text:
            return None
        
        text_norm = self.normalize_text(text)
        
        # English patterns (including typos and Banglish)
        # Match: "english", "english taught", "english-taught", "english program", etc.
        english_patterns = [
            r'\b(english|englsih|englsh|inglish|ingreji|english\s+e)\b',
            r'\b(english\s+taught|english-?taught|english\s+program)\b',  # "english taught" or "english-taught"
            r'\b(taught\s+in\s+english|program\s+in\s+english)\b',  # "taught in english"
            r'\b(英文)\b',  # Chinese word for English
        ]
        for pattern in english_patterns:
            if re.search(pattern, text_norm):
                return "English"
        
        # Chinese patterns (including Chinese characters)
        chinese_patterns = [
            r'\b(chinese|chinay|chinese-?taught|chinese\s+program|mandarin|cn)\b',
            r'\b(中文|汉语)\b',  # Chinese words for Chinese
        ]
        for pattern in chinese_patterns:
            if re.search(pattern, text_norm):
                return "Chinese"
        
        return None
    
    def parse_major_query(self, text: str) -> Optional[str]:
        """
        Parse major query, keeping short acronyms (cs,cse,ce,eee,mbbs,bba,mba) 
        and don't drop them as stopwords.
        Returns cleaned major query string or None.
        """
        if not text:
            return None
        
        # Semantic stopwords to remove
        stopwords = {"scholarship", "info", "information", "fees", "tuition", "cost", 
                     "requirement", "requirements", "admission", "program", "course"}
        
        text_norm = self.normalize_text(text)
        
        # Check if it's a stopword
        if text_norm in stopwords:
            return None
        
        # Check if it's a degree word (should not be a major)
        if self.parse_degree_level(text):
            return None
        
        # Keep acronyms and short terms (they might be valid majors)
        # Remove common query words but keep acronyms
        words = text_norm.split()
        filtered_words = [w for w in words if w not in stopwords]
        
        if not filtered_words:
            return None
        
        return ' '.join(filtered_words).strip()
    
    def parse_university_query(self, text: str) -> Optional[str]:
        """
        Parse university query: match exact name, then aliases JSON, then fuzzy to name.
        Must handle "HIT" => Harbin Institute of Technology via aliases.
        Returns university name or None.
        """
        if not text:
            return None
        
        text_norm = self.normalize_text(text)
        
        # Load universities cache for matching (lazy - only when needed)
        uni_cache = self._get_universities_cached()
        
        for uni in uni_cache:
            # Exact name match
            if text_norm == self.normalize_text(uni.get("name", "")):
                return uni.get("name")
            
            # Check aliases
            aliases = uni.get("aliases", [])
            if aliases:
                for alias in aliases:
                    if text_norm == self.normalize_text(str(alias)):
                        return uni.get("name")
            
            # Check Chinese name
            name_cn = uni.get("name_cn")
            if name_cn and text_norm == self.normalize_text(str(name_cn)):
                return uni.get("name")
        
        # Fuzzy match
        best_match = None
        best_score = 0.0
        
        for uni in uni_cache:
            # Match against name
            name_score = SequenceMatcher(None, text_norm, self.normalize_text(uni.get("name", ""))).ratio()
            if name_score > best_score and name_score >= 0.8:
                best_score = name_score
                best_match = uni.get("name")
            
            # Match against aliases
            aliases = uni.get("aliases", [])
            if aliases:
                for alias in aliases:
                    alias_score = SequenceMatcher(None, text_norm, self.normalize_text(str(alias))).ratio()
                    if alias_score > best_score and alias_score >= 0.8:
                        best_score = alias_score
                        best_match = uni.get("name")
        
        return best_match if best_score >= 0.8 else None
    
    def parse_query_rules(self, text: str) -> Dict[str, Any]:
        """
        Rule-based parsing of user query without LLM.
        Returns dict with: intent, university_raw, major_raw, degree_level, teaching_language,
        intake_term, intake_year, duration_text, wants_scholarship, wants_documents, wants_fees,
        wants_deadline, wants_list, page_action.
        """
        if not text:
            return {}
        
        normalized = self._normalize_unicode_text(text)
        lower = normalized.lower()  # Ensure lowercase for regex matching
        
        result = {
            "intent": "general",
            "university_raw": None,
            "major_raw": None,
            "degree_level": None,
            "teaching_language": None,
            "intake_term": None,
            "intake_year": None,
            "duration_text": None,
            "city": None,
            "province": None,
            "wants_scholarship": False,
            "wants_documents": False,
            "wants_fees": False,
            "wants_deadline": False,
            "wants_list": False,
            "page_action": "none"
        }
        
        # Page actions (pagination)
        if re.search(r'\b(next|more|show more|next page|page \d+|continue)\b', lower):
            result["page_action"] = "next"
        elif re.search(r'\b(prev|previous|back|page 1|first page)\b', lower):
            result["page_action"] = "prev"
        
        # Intake term detection
        if re.search(r'\b(mar(ch)?|spring)\b', lower):
            result["intake_term"] = "March"
        elif re.search(r'\b(sep(t|tember)?|fall|autumn)\b', lower):
            result["intake_term"] = "September"
        
        # Intake year detection
        year_match = re.search(r'\b(20[2-9]\d)\b', lower)
        if year_match:
            result["intake_year"] = int(year_match.group(1))
        
        # Degree level detection
        if re.search(r'\b(bachelor|undergrad|undergraduate|b\.?sc|bsc|b\.?s|bs|b\.?a|ba|beng|b\.?eng)\b', lower):
            result["degree_level"] = "Bachelor"
        elif re.search(r'\b(master|masters|postgrad|post-graduate|graduate|msc|m\.?sc|ms|m\.?s|ma|m\.?a|mba)\b', lower):
            result["degree_level"] = "Master"
        elif re.search(r'\b(phd|ph\.?d|doctorate|doctoral|dphil)\b', lower):
            result["degree_level"] = "PhD"
        elif re.search(r'\b(language\s+program|language\s+course|non-?degree|foundation|foundation\s+program|chinese\s+language|english\s+language|mandarin\s+language)\b', lower):
            result["degree_level"] = "Language"
        # Also check if "language" appears as a standalone word (e.g., "Chinese Language", "Mandarin Language")
        elif re.search(r'\b(chinese|english|mandarin|japanese|korean)\s+language\b', lower):
            result["degree_level"] = "Language"
        elif re.search(r'\b(diploma|associate|assoc)\b', lower):
            result["degree_level"] = "Diploma"
        
        # Teaching language detection
        # CRITICAL: Do NOT set teaching_language if "Chinese Language" or "English Language" is part of a major name
        # Only set if explicitly mentioned as a teaching requirement (e.g., "taught in Chinese", "English-taught program")
        # Check for explicit teaching language indicators first
        if re.search(r'\b(english-?taught|taught\s+in\s+english|english\s+taught|program\s+taught\s+in\s+english)\b', lower):
            result["teaching_language"] = "English"
        elif re.search(r'\b(chinese-?taught|taught\s+in\s+chinese|chinese\s+taught|program\s+taught\s+in\s+chinese|mandarin-?taught|taught\s+in\s+mandarin)\b', lower):
            result["teaching_language"] = "Chinese"
        # Only set if "English" or "Chinese" appears as standalone words (not part of "English Language" or "Chinese Language" major)
        elif re.search(r'\b(english)\b', lower) and not re.search(r'\b(chinese|english)\s+language\b', lower):
            # Only set if it's clearly about teaching language, not a major name
            if not re.search(r'\b(chinese\s+language|english\s+language|language\s+program|language\s+course)\b', lower):
                result["teaching_language"] = "English"
        elif re.search(r'\b(chinese|mandarin)\b', lower) and not re.search(r'\b(chinese\s+language|english\s+language|language\s+program|language\s+course)\b', lower):
            # Only set if it's clearly about teaching language, not a major name
            if not re.search(r'\b(chinese\s+language|english\s+language|language\s+program|language\s+course)\b', lower):
                result["teaching_language"] = "Chinese"
        
        # Duration detection - support arbitrary durations
        # Parse "4 months" => 0.333 years, "1.3 year" => 1.3, "at least 2 years" => 2.0 (min), "max 1 year" => 1.0 (max)
        duration_years = None
        duration_constraint = "approx"
        # Parse months (e.g., "4 months", "6 month")
        month_match = re.search(r'\b(\d+)\s*(?:month|months)\b', lower)
        if month_match:
            months = int(month_match.group(1))
            duration_years = months / 12.0
        # Parse years with decimals (e.g., "1.3 year", "1.5 years")
        elif re.search(r'\b(\d+\.?\d*)\s*(?:year|years)\b', lower):
            year_match = re.search(r'\b(\d+\.?\d*)\s*(?:year|years)\b', lower)
            if year_match:
                duration_years = float(year_match.group(1))
        # Parse "at least" or "minimum" for min constraint
        if re.search(r'\b(at\s+least|minimum|min)\s+(\d+\.?\d*)', lower):
            duration_constraint = "min"
        # Parse "max" or "maximum" for max constraint
        elif re.search(r'\b(max|maximum)\s+(\d+\.?\d*)', lower):
            duration_constraint = "max"
        
        if duration_years is not None:
            result["duration_years_target"] = duration_years
            result["duration_constraint"] = duration_constraint
        # Legacy duration_text for backward compatibility
        if re.search(r'\b(1\s+semester|one\s+semester|half\s+year|6\s+month|six\s+month|0\.5|0\s*\.\s*5)\b', lower):
            result["duration_text"] = "one_semester"
        elif re.search(r'\b(2\s+semester|two\s+semester)\b', lower):
            result["duration_text"] = "two_semester"
        elif re.search(r'\b(1\s+year|one\s+year)\b', lower):
            result["duration_text"] = "one_year"
        elif re.search(r'\b(2\s+years?|two\s+years?)\b', lower):
            result["duration_text"] = "two_year"
        elif re.search(r'\b(3\s+years?|three\s+years?)\b', lower):
            result["duration_text"] = "three_year"
        elif re.search(r'\b(4\s+years?|four\s+years?)\b', lower):
            result["duration_text"] = "four_year"
        
        # Intent detection
        # CRITICAL: Detect scholarship queries including "required/available/offers/offer" patterns
        # Examples: "Does LNPU offer type A scholarship?", "Is CSC scholarship available?", "Does X require CSC?"
        # Also handle: "Does CSC/CSCA/Chinese government scholarship/type a, type b, type c scholarship required/available for march physics bachelor in LNPU"
        if re.search(r'\b(scholarship|waiver|type-?a|type-?b|type-?c|type-?d|partial|stipend|how\s+to\s+get|how\s+can\s+i\s+get|csc|csca|china\s+scholarship|chinese\s+government)\b', lower) or \
           (re.search(r'\b(required|available|offers?)\b', lower) and re.search(r'\b(scholarship|type-?a|type-?b|type-?c|csc|csca|china\s+scholarship|chinese\s+government)\b', lower)):
            result["wants_scholarship"] = True
            result["intent"] = "scholarship_only"
            
            # Check for specific scholarship types
            # CRITICAL: Handle patterns like "B or C type of scholarship", "type B", "type-B", "type B scholarship", etc.
            scholarship_types = []
            # Pattern 1: "type a", "type-a", "type_a", "type A"
            if re.search(r'\b(type-?a|type\s+a|type_a|a\s+type|a-?type)\b', lower, re.IGNORECASE):
                scholarship_types.append("Type A")
            # Pattern 2: "type b", "type-b", "type_b", "type B", OR "B type", "B-type", "B type of scholarship"
            if re.search(r'\b(type-?b|type\s+b|type_b|b\s+type|b-?type)\b', lower, re.IGNORECASE):
                scholarship_types.append("Type B")
            # Pattern 3: "type c", "type-c", "type_c", "type C", OR "C type", "C-type", "C type of scholarship"
            if re.search(r'\b(type-?c|type\s+c|type_c|c\s+type|c-?type)\b', lower, re.IGNORECASE):
                scholarship_types.append("Type C")
            # CRITICAL: CSC/CSCA/Chinese Government Scholarship/Chinese Gov/Chinese Gov. are all the same
            if re.search(r'\b(csca|csc|china\s+scholarship\s+council|chinese\s+government\s+scholarship|chinese\s+gov\.?\s+scholarship|chinese\s+govt\.?\s+scholarship)\b', lower):
                scholarship_types.append("CSC")
                result["wants_csca_scholarship"] = True
                # Preserve scholarship focus for CSC/CSCA
                result["scholarship_focus"] = {"any": True, "csc": True, "university": False}
                # Avoid mis-parsing "CSCA/CSC" as a university name
                result["university_raw"] = None
            
            # Store scholarship types for filtering
            if scholarship_types:
                result["scholarship_types"] = scholarship_types
        if re.search(r'\b(document|documents|required\s+documents?|doc\s+list|paper|papers|materials|what\s+doc|what\s+documents?)\b', lower):
            result["wants_documents"] = True
            result["intent"] = "documents_only"
        if re.search(r'\b(fee|fees|tuition|cost|price|how\s+much|budget|per\s+year|per\s+month|application\s+fee)\b', lower):
            result["wants_fees"] = True
            if result["intent"] == "general":
                result["intent"] = "fees_only"
        if re.search(r'\b(deadline|when|application\s+deadline|last\s+date|due\s+date)\b', lower):
            result["wants_deadline"] = True
        # Check for "which universities" or "list universities" FIRST (before fees_compare)
        # This ensures list queries take priority over fee comparison
        # Match both singular "university" and plural "universities"
        if re.search(r'\b(which|what|list|show|all|available|how\s+many|any)\s+universit(?:y|ies|i)\b', lower):
            result["wants_list"] = True
            result["intent"] = "list_universities"
        # Check for "list a few universities", "provide some universities", "show some universities"
        elif re.search(r'\b(list|provide|show|give)\s+(a\s+few|some|few)\s+universit(?:y|ies|i)\b', lower):
            result["wants_list"] = True
            result["intent"] = "list_universities"
        # Also check for "which university" (singular) - user wants a list
        elif re.search(r'\b(which|what|how\s+many|any)\s+universit(?:y|ies|i)\s+(offer|offers|provide|provides|have|has|give|gives)\b', lower):
            result["wants_list"] = True
            result["intent"] = "list_universities"
        # Check for "is there any university" or "are there any universities"
        elif re.search(r'\b(is\s+there|are\s+there)\s+any\s+universit(?:y|ies|i)\b', lower):
            result["wants_list"] = True
            result["intent"] = "list_universities"
        # Check for "does any [city] university" or "any [city] university offering"
        # This handles patterns like "does any beijing university offering mba"
        elif re.search(r'\b(does|do)\s+any\s+\w+\s+universit(?:y|ies|i)\s+(offer|offers|provide|provides|have|has|give|gives)\b', lower):
            result["wants_list"] = True
            result["intent"] = "list_universities"
        elif re.search(r'\bany\s+\w+\s+universit(?:y|ies|i)\s+(offer|offers|provide|provides|have|has|give|gives)\b', lower):
            result["wants_list"] = True
            result["intent"] = "list_universities"
        elif re.search(r'\b(list|show|all|available|what\s+programs?|which\s+programs?|programs?\s+available|majors?\s+available|offered\s+(?:majors?|subjects?|programs?)|list\s+offered|subjects?\s+offered|majors?\s+offered)\b', lower):
            result["wants_list"] = True
            result["intent"] = "list_programs"
        # Fee comparison can be combined with list queries (set wants_fees flag)
        # Also detect "free tuition" queries - includes "no fee", "zero fee" as free tuition indicators
        if re.search(r'\b(cheapest|lowest|lowest\s+fees?|lowest\s+tuition|less\s+fee|low\s+fee|lowest\s+cost|less\s+cost|compare|comparison|free\s+tuition|tuition\s+free|zero\s+tuition|no\s+tuition|tuition\s+is\s+free|tuition\s+0|tuition\s+zero)\b', lower):
            result["wants_fees"] = True
            # Check if it's a "free tuition" query - includes "no fee", "zero fee", "without fee"
            if re.search(r'\b(free\s+tuition|tuition\s+free|zero\s+tuition|no\s+tuition|tuition\s+is\s+free|tuition\s+0|tuition\s+zero|no\s+fee|zero\s+fee|without\s+fee|no\s+fees|zero\s+fees|without\s+fees)\b', lower):
                result["wants_free_tuition"] = True
            # Only set fees_compare if not already a list intent
            if result.get("intent") not in ["list_universities", "list_programs"]:
                result["intent"] = "fees_compare"
        
        # University detection (try to find university names in text)
        # This is a simple extraction - fuzzy matching happens later
        # Look for common patterns like "at X University", "X University", "in X"
        uni_patterns = [
            r'\b(at|in|from)\s+([A-Z][a-zA-Z\s&]+(?:University|College|Institute|Medical|Normal))',
            r'\b([A-Z][a-zA-Z\s&]+(?:University|College|Institute|Medical|Normal))\b'
        ]
        for pattern in uni_patterns:
            match = re.search(pattern, text)  # Use original text (not normalized) to preserve capitalization
            if match:
                uni_candidate = match.group(2) if len(match.groups()) > 1 else match.group(1)
                if uni_candidate and len(uni_candidate.strip()) > 3:
                    result["university_raw"] = uni_candidate.strip()
                    break
        
        # Major extraction (CRITICAL: Extract before semantic stopword clearing)
        # Pattern 1: "BSC in physics" or "masters in pharmacy" -> extract after "in"
        in_pattern = re.search(r'\b(in|for|studying|study)\s+([a-z]+(?:\s+[a-z]+)*)\b', normalized, re.IGNORECASE)
        if in_pattern:
            major_candidate = in_pattern.group(2).strip()
            # Check it's not a stopword
            if major_candidate and major_candidate.lower() not in ["scholarship", "info", "information", "fees", "tuition", "cost", "requirement", "requirements", "admission", "program", "course", "china", "chinese"]:
                result["major_raw"] = major_candidate
            else:
                # Pattern 2: Extract discipline words (physics, pharmacy, computer science, etc.)
                # Remove intake terms, years, fees, etc. to get major
                cleaned = re.sub(r'\b(20[2-9]\d)\b', '', normalized, flags=re.IGNORECASE)
                cleaned = re.sub(r'\b(mar(ch)?|sep(t|tember)?|spring|fall|autumn)\b', '', cleaned, flags=re.IGNORECASE)
                cleaned = re.sub(r'\b(fee|fees|tuition|cost|price|application\s+fee|deadline|scholarship|document|documents?)\b', '', cleaned, flags=re.IGNORECASE)
                cleaned = re.sub(r'\b(list|show|all|available|what|which|programs?|majors?|courses?)\b', '', cleaned, flags=re.IGNORECASE)
                cleaned = re.sub(r'\b(bachelor|master|masters|phd|language|diploma|degree|program|course|bsc|msc|bs|ms)\b', '', cleaned, flags=re.IGNORECASE)
                cleaned = re.sub(r'\b(english|chinese|taught|in|at|for|the|a|an|and|or|of|to|from|china)\b', '', cleaned, flags=re.IGNORECASE)
                cleaned = re.sub(r'\s+', ' ', cleaned).strip()
                
                # If cleaned text has meaningful content, treat as major
                if cleaned and len(cleaned.split()) >= 1:
                    # Check if it's not a city/province by querying the database
                    # This is more accurate than a hardcoded list
                    is_city_or_province = False
                    try:
                        # Use DBQueryService to check if it's a city/province
                        from app.services.db_query_service import DBQueryService
                        db_service = DBQueryService(self.db)
                        cities = db_service.search_universities(city=cleaned, is_partner=True, limit=1)
                        provinces = db_service.search_universities(province=cleaned, is_partner=True, limit=1)
                        if cities or provinces:
                            is_city_or_province = True
                            print(f"DEBUG: '{cleaned}' detected as city/province, not treating as major")
                    except Exception as e:
                        print(f"DEBUG: Error checking city/province: {e}")
                    
                    if not is_city_or_province:
                        result["major_raw"] = cleaned
        
        if not result.get("major_raw"):
            # Pattern 2: Extract discipline words when no "in" pattern found
            # Remove intake terms, years, fees, etc. to get major
            cleaned = re.sub(r'\b(20[2-9]\d)\b', '', normalized, flags=re.IGNORECASE)
            cleaned = re.sub(r'\b(mar(ch)?|sep(t|tember)?|spring|fall|autumn)\b', '', cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r'\b(fee|fees|tuition|cost|price|application\s+fee|deadline|scholarship|document|documents?)\b', '', cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r'\b(list|show|all|available|what|which|programs?|majors?|courses?)\b', '', cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r'\b(bachelor|master|masters|phd|language|diploma|degree|program|course|bsc|msc|bs|ms)\b', '', cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r'\b(english|chinese|taught|in|at|for|the|a|an|and|or|of|to|from|china)\b', '', cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r'\s+', ' ', cleaned).strip()
            
                # If cleaned text has meaningful content, treat as major
            if cleaned and len(cleaned.split()) >= 1:
                # Check if it's not a city/province by querying the database
                # This is more accurate than a hardcoded list
                is_city_or_province = False
                try:
                    # Use DBQueryService to check if it's a city/province
                    from app.services.db_query_service import DBQueryService
                    db_service = DBQueryService(self.db)
                    cities = db_service.search_universities(city=cleaned, is_partner=True, limit=1)
                    provinces = db_service.search_universities(province=cleaned, is_partner=True, limit=1)
                    if cities or provinces:
                        is_city_or_province = True
                        print(f"DEBUG: '{cleaned}' detected as city/province, not treating as major")
                except Exception as e:
                    print(f"DEBUG: Error checking city/province: {e}")
                
                if not is_city_or_province:
                    result["major_raw"] = cleaned
        
        # Expand acronyms if major_raw is a known acronym
        if result.get("major_raw"):
            expanded = self._expand_major_acronym(result["major_raw"])
            if expanded != result["major_raw"]:
                result["major_raw"] = expanded
        
        # City and Province detection with fuzzy matching for typos
        # Common Chinese cities with fuzzy matching
        city_map = {
            "guangzhou": ["guangzhou", "guangzou", "guangjou", "guangzho", "gz"],
            "beijing": ["beijing", "beijin", "peking", "bj"],
            "shanghai": ["shanghai", "shanghae", "sh"],
            "shenzhen": ["shenzhen", "shenzen", "sz"],
            "hangzhou": ["hangzhou", "hangzho", "hz"],
            "nanjing": ["nanjing", "nanking", "nj"],
            "chengdu": ["chengdu", "chengdu", "cd"],
            "xian": ["xian", "xi'an", "xian", "xa"],
            "wuhan": ["wuhan", "wh"],
            "tianjin": ["tianjin", "tj"],
            "dalian": ["dalian", "dl"],
            "qingdao": ["qingdao", "qd"],
        }
        
        # Common Chinese provinces
        province_map = {
            "guangdong": ["guangdong", "guangdong", "gd"],
            "jiangsu": ["jiangsu", "js"],
            "zhejiang": ["zhejiang", "zj"],
            "sichuan": ["sichuan", "sc"],
            "shaanxi": ["shaanxi", "shanxi", "sx"],
            "hubei": ["hubei", "hb"],
            "shandong": ["shandong", "sd"],
            "hunan": ["hunan", "hn"],
            "beijing": ["beijing", "bj"],  # Beijing is also a province-level municipality
            "shanghai": ["shanghai", "sh"],  # Shanghai is also a province-level municipality
            "tianjin": ["tianjin", "tj"],  # Tianjin is also a province-level municipality
        }
        
        # Check for city mentions (with fuzzy matching)
        # Handle patterns like "in Guangzhou", "Guangzhou universities", "from Guangzhou", etc.
        for correct_city, variants in city_map.items():
            for variant in variants:
                # Match city in various contexts: "in guangzhou", "guangzhou universities", "from guangzhou", "at guangzhou", or just "guangzhou"
                city_patterns = [
                    rf'\b(in|from|at|near|around)\s+{variant}\b',  # "in guangzhou", "from guangzhou"
                    rf'\b{variant}\s+(universit|institut|college|city|province)',  # "guangzhou universities"
                    rf'\b{variant}\b',  # Just "guangzhou" as standalone word
                ]
                for pattern in city_patterns:
                    if re.search(pattern, lower):
                        result["city"] = correct_city.title()
                        print(f"DEBUG: parse_query_rules - detected city: {result['city']} from pattern: {pattern}")
                        break
                if result.get("city"):
                    break
            if result.get("city"):
                break
        
        # Check for province mentions (with fuzzy matching)
        for correct_province, variants in province_map.items():
            for variant in variants:
                # Match province in various contexts
                province_patterns = [
                    rf'\b(in|from|at|near|around)\s+{variant}\b',  # "in guangdong"
                    rf'\b{variant}\s+(province|universit|institut|college)',  # "guangdong province"
                    rf'\b{variant}\b',  # Just "guangdong" as standalone word
                ]
                for pattern in province_patterns:
                    if re.search(pattern, lower):
                        result["province"] = correct_province.title()
                        print(f"DEBUG: parse_query_rules - detected province: {result['province']} from pattern: {pattern}")
                        break
                if result.get("province"):
                    break
            if result.get("province"):
                break
        
        return result
    
    def extract_partner_query_state(self, conversation_history: List[Dict[str, str]], prev_state: Optional[PartnerQueryState] = None,
                                   partner_id: Optional[int] = None, conversation_id: Optional[str] = None) -> PartnerQueryState:
        """
        Extract and consolidate PartnerQueryState from conversation history using router.
        Uses two-stage routing: rules first, then LLM only if confidence < 0.75.
        Handles clarification mode and intent locking with pending state system.
        """
        if not conversation_history:
            return PartnerQueryState()
        
        # Get the latest user message
        latest_user_message = ""
        for msg in reversed(conversation_history):
            if msg.get('role') == 'user':
                latest_user_message = msg.get('content', '')
                break
        
        # Check if user is accepting a suggested major (e.g., "applied physics is ok", "that's fine", "sounds good")
        acceptance_patterns = [
            r'\b(is\s+ok|is\s+okay|that\'?s\s+fine|that\'?s\s+good|sounds\s+good|works\s+for\s+me|i\'?ll\s+take|yes|ok|okay)\b',
            r'\b(applied\s+physics|physics|computer\s+science|business|engineering)\s+(is\s+ok|is\s+okay|that\'?s\s+fine|sounds\s+good)\b'
        ]
        is_accepting_suggestion = any(re.search(pattern, latest_user_message.lower()) for pattern in acceptance_patterns)
        
        # Check for pending slot using unified state cache (MUST be done before using pending_info)
        pending_info = self._get_pending(partner_id, conversation_id)
        print(f"DEBUG: Pending check - pending_info={pending_info is not None}, slot={pending_info.get('slot') if pending_info else None}")
        
        # If user is accepting a suggestion, extract the major name from the message or previous conversation
        accepted_major = None
        if is_accepting_suggestion:
            # First, check if there's a pending slot with a suggested major
            if pending_info and pending_info.get("slot") == "major_acceptance":
                snapshot = pending_info.get("snapshot", {})
                suggested_major = snapshot.get("_suggested_major")
                if suggested_major:
                    accepted_major = suggested_major
                    print(f"DEBUG: User accepted suggested major from pending slot: '{accepted_major}'")
            
            # If not found in pending slot, try to extract from the message
            if not accepted_major:
                major_match = re.search(r'\b(applied\s+physics|physics|computer\s+science|business\s+administration|engineering|artificial\s+intelligence)\b', latest_user_message.lower())
                if major_match:
                    accepted_major = major_match.group(1)
                    print(f"DEBUG: User accepted suggested major: '{accepted_major}'")
            
            # If still not found, check previous assistant message for suggested major
            if not accepted_major:
                for msg in reversed(conversation_history[-4:]):
                    if msg.get('role') == 'assistant':
                        content = msg.get('content', '')
                        # Look for pattern like "Would you like to see details for X programs?"
                        major_match = re.search(r'Would you like to see details for ([^?]+) programs\?', content)
                        if major_match:
                            accepted_major = major_match.group(1).strip()
                            print(f"DEBUG: Extracted accepted major from previous message: '{accepted_major}'")
                    break
        
        # Also check if user is directly typing a major name (e.g., "Computer Science and Technology" after seeing a list)
        # This handles cases where user types the exact major name from a numbered list
        if not accepted_major and pending_info and pending_info.get("slot") == "major_or_university":
            # Check if the message looks like a major name (contains common major keywords)
            major_keywords = ["computer science", "artificial intelligence", "business administration", "engineering", "physics", "mathematics", "chemistry", "biology"]
            message_lower = latest_user_message.lower()
            for keyword in major_keywords:
                if keyword in message_lower:
                    # This looks like a major selection - use the message as the major query
                    accepted_major = latest_user_message.strip()
                    print(f"DEBUG: Detected major selection from list: '{accepted_major}'")
                    break
            
        # Check if user is clearly changing intent (e.g., "now change to", "instead", "admission requirements")
        intent_change_keywords = ["now change to", "instead", "admission requirements", "fee calculation", "calculate fees", "actually i want", "not scholarship"]
        is_intent_change = any(kw in latest_user_message.lower() for kw in intent_change_keywords)
        
        # If pending slot exists and user is not changing intent, handle clarification (SLOT FILL ONLY)
        if pending_info and not is_intent_change:
            print(f"DEBUG: Using pending state - slot={pending_info.get('slot')}, restoring snapshot")
            # CRITICAL: DO NOT re-run extraction - treat message as slot fill only
            snapshot = pending_info.get("snapshot", {})
            pending_slot = pending_info.get("slot")
            
            # Restore state from snapshot
            state = PartnerQueryState()
            state.intent = snapshot.get("intent", "GENERAL")
            state.degree_level = snapshot.get("degree_level")
            state.intake_term = snapshot.get("intake_term")
            state.university_query = snapshot.get("university_query")
            state.major_query = snapshot.get("major_query")
            state.teaching_language = snapshot.get("teaching_language")
            state.wants_scholarship = snapshot.get("wants_scholarship", False)
            state.wants_fees = snapshot.get("wants_fees", False)
            state.wants_free_tuition = snapshot.get("wants_free_tuition", False)
            state.wants_requirements = snapshot.get("wants_requirements", False)
            state.wants_deadline = snapshot.get("wants_deadline", False)
            state.wants_list = snapshot.get("wants_list", False)
            state.wants_earliest = snapshot.get("wants_earliest", False)
            # CRITICAL: Preserve LIST_UNIVERSITIES intent if it was in the snapshot
            if snapshot.get("intent") == self.router.INTENT_LIST_UNIVERSITIES:
                state.intent = self.router.INTENT_LIST_UNIVERSITIES
                state.wants_list = True
                print(f"DEBUG: Preserved LIST_UNIVERSITIES intent from snapshot")
            state.city = snapshot.get("city")
            state.province = snapshot.get("province")
            state.country = snapshot.get("country")
            state.intake_year = snapshot.get("intake_year")
            state.duration_years_target = snapshot.get("duration_years_target")
            state.duration_constraint = snapshot.get("duration_constraint")
            state.budget_max = snapshot.get("budget_max")
            
            # Restore university candidates if present
            state._university_candidates = snapshot.get("_university_candidates")
            state._university_candidate_ids = snapshot.get("_university_candidate_ids")
            
            # Restore focus objects
            req_focus_dict = snapshot.get("req_focus")
            if req_focus_dict:
                from app.services.slot_schema import RequirementFocus
                state.req_focus = RequirementFocus(**req_focus_dict) if isinstance(req_focus_dict, dict) else req_focus_dict
            scholarship_focus_dict = snapshot.get("scholarship_focus")
            if scholarship_focus_dict:
                from app.services.slot_schema import ScholarshipFocus
                state.scholarship_focus = ScholarshipFocus(**scholarship_focus_dict) if isinstance(scholarship_focus_dict, dict) else scholarship_focus_dict
            
            # Fill the pending slot deterministically
            if pending_slot == "teaching_language":
                lang = self.parse_teaching_language(latest_user_message)
                if lang:
                    state.teaching_language = lang
                    # Preserve wants_free_tuition, wants_list, and wants_fees from snapshot
                    if snapshot.get("wants_free_tuition"):
                        state.wants_free_tuition = snapshot.get("wants_free_tuition")
                        print(f"DEBUG: Preserved wants_free_tuition={state.wants_free_tuition} from snapshot for teaching_language reply")
                    if snapshot.get("wants_list"):
                        state.wants_list = snapshot.get("wants_list")
                        print(f"DEBUG: Preserved wants_list={state.wants_list} from snapshot for teaching_language reply")
                    if snapshot.get("wants_fees"):
                        state.wants_fees = snapshot.get("wants_fees")
                        print(f"DEBUG: Preserved wants_fees={state.wants_fees} from snapshot for teaching_language reply")
                    self._consume_pending(partner_id, conversation_id)  # Clear pending
                else:
                    # Keep pending if can't parse
                    return state
            elif pending_slot == "degree_level":
                degree = self.parse_degree_level(latest_user_message)
                # Fallback: Check if message contains "language" (e.g., "Chinese Language", "English Language")
                if not degree:
                    message_lower = latest_user_message.lower()
                    if "language" in message_lower or re.search(r'\b(chinese|english|mandarin)\s+language\b', message_lower):
                        degree = "Language"
                        print(f"DEBUG: Detected Language degree_level from '{latest_user_message}' via fallback check")
                if degree:
                    state.degree_level = degree
                    # CRITICAL: Do NOT set teaching_language for Language programs unless explicitly mentioned as a teaching requirement
                    # "Chinese Language" is a major name, not a teaching language requirement
                    # Only set if user explicitly says "taught in Chinese" or "Chinese-taught"
                    if degree == "Language":
                        # Only set teaching_language if explicitly mentioned as a teaching requirement
                        msg_lower = latest_user_message.lower()
                        if re.search(r'\b(taught\s+in\s+chinese|chinese-?taught|chinese\s+taught|mandarin-?taught)\b', msg_lower):
                            state.teaching_language = "Chinese"
                            print(f"DEBUG: Set teaching_language=Chinese for Language program (explicit teaching requirement)")
                        elif re.search(r'\b(taught\s+in\s+english|english-?taught|english\s+taught)\b', msg_lower):
                            state.teaching_language = "English"
                            print(f"DEBUG: Set teaching_language=English for Language program (explicit teaching requirement)")
                        else:
                            # Don't set teaching_language for "Chinese Language" major names
                            print(f"DEBUG: Not setting teaching_language for Language program - 'Chinese Language' is a major name, not teaching requirement")
                    # Preserve wants_deadline from snapshot if it was set
                    if snapshot.get("wants_deadline"):
                        state.wants_deadline = snapshot.get("wants_deadline")
                        print(f"DEBUG: Preserved wants_deadline={state.wants_deadline} from snapshot for degree_level reply")
                    self._consume_pending(partner_id, conversation_id)
                else:
                    return state
            elif pending_slot == "intake_term":
                term = self.parse_intake_term(latest_user_message)
                if term:
                    state.intake_term = term
                    # Preserve wants_deadline from snapshot if it was set
                    if snapshot.get("wants_deadline"):
                        state.wants_deadline = snapshot.get("wants_deadline")
                        print(f"DEBUG: Preserved wants_deadline={state.wants_deadline} from snapshot for intake_term reply")
                    # Preserve wants_free_tuition, wants_list, and wants_fees from snapshot
                    if snapshot.get("wants_free_tuition"):
                        state.wants_free_tuition = snapshot.get("wants_free_tuition")
                        print(f"DEBUG: Preserved wants_free_tuition={state.wants_free_tuition} from snapshot for intake_term reply")
                    if snapshot.get("wants_list"):
                        state.wants_list = snapshot.get("wants_list")
                        print(f"DEBUG: Preserved wants_list={state.wants_list} from snapshot for intake_term reply")
                    if snapshot.get("wants_fees"):
                        state.wants_fees = snapshot.get("wants_fees")
                        print(f"DEBUG: Preserved wants_fees={state.wants_fees} from snapshot for intake_term reply")
                    self._consume_pending(partner_id, conversation_id)
                else:
                    return state
            elif pending_slot == "major_choice":
                # User selected from numbered list (1/2/3) OR typed the full major name
                num_match = re.match(r'^(\d+)$', latest_user_message.strip())
                if num_match:
                    # User selected by number - get the candidate from the list
                    selected_num = int(num_match.group(1))
                    candidates = snapshot.get("_major_candidates", [])
                    candidate_ids = snapshot.get("_major_candidate_ids", [])
                    if 1 <= selected_num <= len(candidates):
                        selected_major = candidates[selected_num - 1]
                        selected_id = candidate_ids[selected_num - 1] if selected_num <= len(candidate_ids) else None
                        state.major_query = selected_major
                        if selected_id:
                            state._resolved_major_ids = [selected_id]
                        print(f"DEBUG: User selected major #{selected_num}: '{selected_major}'")
                        self._consume_pending(partner_id, conversation_id)
                    else:
                        print(f"DEBUG: Invalid selection number {selected_num}, keeping pending")
                        return state
                else:
                    # User typed the full major name - try to match it to one of the candidates
                    candidates = snapshot.get("_major_candidates", [])
                    candidate_ids = snapshot.get("_major_candidate_ids", [])
                    user_input_lower = latest_user_message.strip().lower()
                    
                    # Try to find a match (exact or fuzzy)
                    matched_index = None
                    for i, candidate in enumerate(candidates):
                        candidate_lower = candidate.lower().strip()
                        # Exact match
                        if user_input_lower == candidate_lower:
                            matched_index = i
                            break
                        # Partial match (user input contains candidate or vice versa)
                        # Also check if user input is a significant word in candidate (e.g., "physics" in "Applied Physics")
                        candidate_words = set(candidate_lower.split())
                        user_words = set(user_input_lower.split())
                        if user_input_lower in candidate_lower or candidate_lower in user_input_lower:
                            matched_index = i
                            break
                        # Check if user input word(s) match significant words in candidate (e.g., "physics" matches "Applied Physics")
                        elif user_words and len(user_words.intersection(candidate_words)) == len(user_words):
                            matched_index = i
                            break
            
                    if matched_index is not None:
                        selected_major = candidates[matched_index]
                        selected_id = candidate_ids[matched_index] if matched_index < len(candidate_ids) else None
                        state.major_query = selected_major
                        if selected_id:
                            state._resolved_major_ids = [selected_id]
                            # IMPORTANT: Mark that major is already resolved, so don't re-resolve later
                            print(f"DEBUG: User typed major name '{latest_user_message}', matched to candidate '{selected_major}' (ID: {selected_id}), preserving resolved_major_ids")
                        else:
                            # If no ID, still mark as resolved to avoid re-resolving with wrong filters
                            state._resolved_major_ids = []  # Will be resolved later, but mark that we shouldn't re-query now
                            print(f"DEBUG: User typed major name '{latest_user_message}', matched to candidate '{selected_major}', but no ID found - will resolve later")
                        self._consume_pending(partner_id, conversation_id)
                    else:
                        # No match found - treat as new major query
                        print(f"DEBUG: User input '{latest_user_message}' doesn't match any candidate, treating as new major query")
                        state.major_query = latest_user_message.strip()
                        self._consume_pending(partner_id, conversation_id)
            elif pending_slot == "duration":
                # User selected a duration (e.g., "1 year", "6 months")
                # Parse duration from message
                duration_parsed = None
                message_lower = latest_user_message.lower()
                
                # Parse duration (e.g., "1 year", "6 months", "1.3 years")
                if "year" in message_lower or "years" in message_lower:
                    year_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:year|years)', message_lower)
                    if year_match:
                        duration_parsed = float(year_match.group(1))
                elif "month" in message_lower or "months" in message_lower:
                    month_match = re.search(r'(\d+)\s*(?:month|months)', message_lower)
                    if month_match:
                        months = int(month_match.group(1))
                        duration_parsed = months / 12.0
                elif "half year" in message_lower or "semester" in message_lower:
                    duration_parsed = 0.5
                
                if duration_parsed:
                    state.duration_years_target = duration_parsed
                    state.duration_constraint = "approx"  # Use approx for language programs
                    
                    # CRITICAL: Use stored intake IDs from duration fallback and re-query to avoid detached session errors
                    stored_intake_ids = snapshot.get("_duration_fallback_intake_ids", [])
                    if stored_intake_ids:
                        print(f"DEBUG: Duration reply - re-querying {len(stored_intake_ids)} intakes by ID from previous query, filtering by duration={duration_parsed}")
                        # Re-query intakes from database using stored IDs to avoid detached session errors
                        from app.models import ProgramIntake
                        stored_intakes = self.db.query(ProgramIntake).filter(ProgramIntake.id.in_(stored_intake_ids)).all()
                        
                        # Filter stored intakes by duration
                        filtered_intakes = []
                        for intake in stored_intakes:
                            # Get duration from intake or major
                            intake_duration = None
                            if hasattr(intake, 'duration_years') and intake.duration_years is not None:
                                intake_duration = intake.duration_years
                            elif hasattr(intake, 'major') and intake.major and hasattr(intake.major, 'duration_years') and intake.major.duration_years is not None:
                                intake_duration = intake.major.duration_years
                            
                            if intake_duration is not None:
                                # Check if duration matches (with approx constraint: 0.75x to 1.25x)
                                if duration_parsed * 0.75 <= intake_duration <= duration_parsed * 1.25:
                                    filtered_intakes.append(intake)
                        
                        if filtered_intakes:
                            # Store filtered intakes in state for use in run_db
                            state._duration_filtered_intakes = filtered_intakes
                            print(f"DEBUG: Duration reply - filtered to {len(filtered_intakes)} intakes matching duration={duration_parsed}")
                            # CRITICAL: Preserve LIST_UNIVERSITIES intent and wants_list/wants_fees from snapshot
                            if snapshot.get("intent") == self.router.INTENT_LIST_UNIVERSITIES:
                                state.intent = self.router.INTENT_LIST_UNIVERSITIES
                                state.wants_list = True
                                if snapshot.get("wants_fees"):
                                    state.wants_fees = True
                                print(f"DEBUG: Preserved LIST_UNIVERSITIES intent and wants_list=True from snapshot")
                            self._consume_pending(partner_id, conversation_id)
                        else:
                            print(f"DEBUG: Duration reply - no intakes match duration={duration_parsed}, keeping pending")
                            # Keep pending and ask again
                            return state
                    else:
                        print(f"DEBUG: Duration reply - no stored intakes found, will re-query")
                        self._consume_pending(partner_id, conversation_id)
                else:
                    print(f"DEBUG: Duration reply - could not parse duration from '{latest_user_message}', keeping pending")
                    return state
            elif pending_slot == "major_acceptance":
                # User accepted a suggested major (e.g., "yes" to "Would you like to see details for Computer Science and Technology programs?")
                suggested_major = snapshot.get("_suggested_major")
                if suggested_major:
                    state.major_query = suggested_major
                    print(f"DEBUG: User accepted major '{suggested_major}', updating major_query")
                    # Resolve the major to get its ID
                    major_ids = self.resolve_major_ids(suggested_major, degree_level=state.degree_level)
                    if major_ids:
                        state._resolved_major_ids = major_ids
                        print(f"DEBUG: Resolved accepted major to IDs: {major_ids}")
                    self._consume_pending(partner_id, conversation_id)
                else:
                    # Fallback: try to extract from conversation history
                    for msg in reversed(conversation_history[-4:]):
                        if msg.get('role') == 'assistant':
                            content = msg.get('content', '')
                            major_match = re.search(r'Would you like to see details for ([^?]+) programs\?', content)
                            if major_match:
                                accepted_major = major_match.group(1).strip()
                                state.major_query = accepted_major
                                print(f"DEBUG: Extracted and accepted major from conversation: '{accepted_major}'")
                                # Resolve the major
                                major_ids = self.resolve_major_ids(accepted_major, degree_level=state.degree_level)
                                if major_ids:
                                    state._resolved_major_ids = major_ids
                                self._consume_pending(partner_id, conversation_id)
                                break
                    else:
                        print(f"DEBUG: Could not extract major from conversation, keeping pending")
                        return state
            elif pending_slot == "university_choice":
                # User selected a university from a list
                # First check if user selected by number (1, 2, 3, etc.)
                num_match = re.match(r'^(\d+)$', latest_user_message.strip())
                if num_match:
                    # User selected by number - get the candidate from the list
                    selected_num = int(num_match.group(1))
                    candidates = snapshot.get("_university_candidates", [])
                    candidate_ids = snapshot.get("_university_candidate_ids", [])
                    if 1 <= selected_num <= len(candidates):
                        selected_uni_name = candidates[selected_num - 1]
                        selected_uni_id = candidate_ids[selected_num - 1] if selected_num <= len(candidate_ids) else None
                        state.university_query = selected_uni_name
                        if selected_uni_id:
                            state._resolved_university_id = selected_uni_id
                        print(f"DEBUG: User selected university #{selected_num}: '{selected_uni_name}' (ID: {state._resolved_university_id})")
                        # Preserve context: major, degree, intake from snapshot (already restored above)
                        self._consume_pending(partner_id, conversation_id)
                    else:
                        print(f"DEBUG: Invalid selection number {selected_num}, keeping pending")
                        return state
                else:
                    # User typed the university name - try to match against candidates
                    candidates = snapshot.get("_university_candidates", [])
                    candidate_ids = snapshot.get("_university_candidate_ids", [])
                    
                    user_input_lower = latest_user_message.strip().lower()
                    matched_index = None
                    
                    # Try to match against candidates first
                    if candidates:
                        for i, candidate in enumerate(candidates):
                            candidate_lower = candidate.lower().strip()
                            # Exact match
                            if user_input_lower == candidate_lower:
                                matched_index = i
                                break
                            # Partial match (user input contains candidate or vice versa)
                            if user_input_lower in candidate_lower or candidate_lower in user_input_lower:
                                matched_index = i
                                break
                    
                    if matched_index is not None and matched_index < len(candidate_ids):
                        # Matched a candidate
                        selected_uni_name = candidates[matched_index]
                        selected_uni_id = candidate_ids[matched_index]
                        state.university_query = selected_uni_name
                        state._resolved_university_id = selected_uni_id
                        print(f"DEBUG: User selected university '{state.university_query}' (ID: {state._resolved_university_id}) from candidates")
                        # Preserve context: major, degree, intake from snapshot (already restored above)
                        self._consume_pending(partner_id, conversation_id)
                    else:
                        # Not in candidates - try fuzzy matching
                        uni = self.parse_university_query(latest_user_message)
                        if uni:
                            matched, uni_dict, _ = self._fuzzy_match_university(uni)
                            if matched and uni_dict:
                                state.university_query = uni_dict.get("name")
                                state._resolved_university_id = uni_dict.get("id")
                                print(f"DEBUG: User selected university '{state.university_query}' (ID: {state._resolved_university_id}) via fuzzy match")
                                self._consume_pending(partner_id, conversation_id)
                            else:
                                # Could not match - keep pending
                                print(f"DEBUG: Could not match university from '{latest_user_message}', keeping pending")
                                return state
                        else:
                            # Try fuzzy matching on the raw message
                            matched, uni_dict, _ = self._fuzzy_match_university(latest_user_message)
                            if matched and uni_dict:
                                state.university_query = uni_dict.get("name")
                                state._resolved_university_id = uni_dict.get("id")
                                print(f"DEBUG: User selected university '{state.university_query}' (ID: {state._resolved_university_id}) via fuzzy match on raw message")
                                self._consume_pending(partner_id, conversation_id)
                            else:
                                # Could not match - keep pending
                                print(f"DEBUG: Could not match university from '{latest_user_message}', keeping pending")
                                return state
            elif pending_slot == "major_or_university":
                # Parse multiple values from the same message (e.g., "mARCH, Physics")
                major = self.parse_major_query(latest_user_message)
                uni = self.parse_university_query(latest_user_message)
                # Also check for intake_term in the same message
                term = self.parse_intake_term(latest_user_message)
                if term:
                    state.intake_term = term
                
                if major:
                    # Expand acronyms (e.g., "BBA" -> "bachelor of business administration")
                    expanded_major = self._expand_major_acronym(major)
                    state.major_query = expanded_major  # Use expanded version
                    
                    # Try to resolve the major to validate it exists
                    # Use degree_level from snapshot if available
                    major_ids = self.resolve_major_ids(
                        major_query=expanded_major,
                        degree_level=state.degree_level,
                        teaching_language=state.teaching_language,
                        limit=3,
                        confidence_threshold=0.70  # Lower threshold for clarification replies
                    )
                    
                    if major_ids:
                        # Major was successfully resolved - store IDs and clear pending
                        state._resolved_major_ids = major_ids
                        print(f"DEBUG: Resolved major '{expanded_major}' (from '{major}') to {len(major_ids)} major IDs: {major_ids}")
                        self._consume_pending(partner_id, conversation_id)
                    else:
                        # Major not found - keep pending and ask again
                        print(f"DEBUG: Could not resolve major '{expanded_major}' (from '{major}'), keeping pending slot")
                        return state
                elif uni:
                    state.university_query = uni
                    self._consume_pending(partner_id, conversation_id)
                elif term:
                    # If only intake_term was found, still clear pending and continue
                    self._consume_pending(partner_id, conversation_id)
                else:
                    # Update cache with restored state
                    self._set_cached_state(partner_id, conversation_id, state, None)
                    return state
        
        # Legacy pending_state check (for backward compatibility)
        conv_key = self._get_conv_key(partner_id, conversation_id, conversation_history)
        pending_state = self._get_pending_state(conv_key)
        
        if pending_state and not is_intent_change:
            # DO NOT call router.route() - parse deterministically
            state = PartnerQueryState()
            
            # CRITICAL: Restore FULL state from pending snapshot
            partial = pending_state.partial_state
            state.intent = pending_state.intent  # Lock intent
            
            # Restore ALL fields from snapshot (preserve full context)
            state.degree_level = partial.get("degree_level")
            state.intake_term = partial.get("intake_term")
            state.university_query = partial.get("university_query")
            state.major_query = partial.get("major_query")
            state.teaching_language = partial.get("teaching_language")
            state.wants_scholarship = partial.get("wants_scholarship", False)
            state.wants_fees = partial.get("wants_fees", False)
            state.wants_requirements = partial.get("wants_requirements", False)
            state.wants_list = partial.get("wants_list", False)
            state.wants_earliest = partial.get("wants_earliest", False)
            state.city = partial.get("city")
            state.province = partial.get("province")
            state.intake_year = partial.get("intake_year")
            state.duration_years_target = partial.get("duration_years_target")
            state.duration_constraint = partial.get("duration_constraint")
            state.budget_max = partial.get("budget_max")
            
            # Restore focus objects
            req_focus_dict = partial.get("req_focus")
            if req_focus_dict:
                from app.services.slot_schema import RequirementFocus
                state.req_focus = RequirementFocus(**req_focus_dict) if isinstance(req_focus_dict, dict) else req_focus_dict
            
            scholarship_focus_dict = partial.get("scholarship_focus")
            if scholarship_focus_dict:
                from app.services.slot_schema import ScholarshipFocus
                state.scholarship_focus = ScholarshipFocus(**scholarship_focus_dict) if isinstance(scholarship_focus_dict, dict) else scholarship_focus_dict
            
            # Also allow safe slot updates if present in user message (even if not the pending slot)
            # This handles cases like "English March" when asked for teaching_language
            parsed = self.parse_query_rules(latest_user_message)
            if not state.intake_term and parsed.get("intake_term"):
                state.intake_term = parsed["intake_term"]
            if not state.degree_level and parsed.get("degree_level"):
                state.degree_level = parsed["degree_level"]
            
            # Fill missing slots using deterministic parsing
            for slot in pending_state.missing_slots:
                if slot == "degree_level" and not state.degree_level:
                    degree = self.parse_degree_level(latest_user_message)
                    if degree:
                        state.degree_level = degree
                elif slot == "intake_term" and not state.intake_term:
                    term = self.parse_intake_term(latest_user_message)
                    if term:
                        state.intake_term = term
                elif slot == "major_query" and not state.major_query:
                    major = self.parse_major_query(latest_user_message)
                    if major:
                        state.major_query = major
                elif slot == "teaching_language" and not state.teaching_language:
                    lang = self.parse_teaching_language(latest_user_message)
                    if lang:
                        state.teaching_language = lang
                elif slot == "major_or_university":
                    # Parse major or university from reply
                    major = self.parse_major_query(latest_user_message)
                    uni = self.parse_university_query(latest_user_message)
                    if major:
                        state.major_query = major
                    elif uni:
                        state.university_query = uni
                    # Also check for city
                    city_match = re.search(r'\b(guangzhou|beijing|shanghai|shenzhen|hangzhou|nanjing|chengdu|xian|wuhan)\b', self.normalize_text(latest_user_message))
                    if city_match:
                        state.city = city_match.group(1).title()
            
            # Check if all missing slots are now filled
            still_missing = []
            if "degree_level" in pending_state.missing_slots and not state.degree_level:
                still_missing.append("degree_level")
            if "intake_term" in pending_state.missing_slots and not state.intake_term and not state.wants_earliest:
                still_missing.append("intake_term")
            if "major_query" in pending_state.missing_slots and not state.major_query:
                still_missing.append("major_query")
            if "major_or_university" in pending_state.missing_slots and not state.major_query and not state.university_query and not state.city:
                still_missing.append("major_or_university")
            if "teaching_language" in pending_state.missing_slots and not state.teaching_language:
                still_missing.append("teaching_language")
            
            # If all slots filled, clear pending state
            if not still_missing:
                self._clear_pending_state(conv_key)
                self._clear_pending_slot(partner_id, conversation_id)
                state.is_clarifying = False
                state.pending_slot = None
            else:
                # Still missing slots - keep pending
                state.is_clarifying = True
                state.pending_slot = still_missing[0]
            
            # Force intent and flags for SCHOLARSHIP
            if state.intent == self.router.INTENT_SCHOLARSHIP:
                state.wants_scholarship = True
                if not state.scholarship_focus:
                    from app.services.slot_schema import ScholarshipFocus
                    state.scholarship_focus = ScholarshipFocus()
            
            # Cache the updated state
            self._set_state_cache(partner_id, conversation_id, state)
            
            return state
        
        # If pending state exists but user changed intent, clear it
        if pending_state and is_intent_change:
            self._clear_pending_state(conv_key)
        
        # Check pending_slot cache first (if prev_state not provided or doesn't have pending_slot)
        pending_slot_info = None
        if not prev_state or not prev_state.pending_slot:
            pending_slot_info = self._get_pending_slot(partner_id, conversation_id)
            if pending_slot_info:
                # CRITICAL FIX: Load full cached state if available, not just create blank state
                if not prev_state:
                    # Try to load from state cache first
                    prev_state = self._get_state_cache(partner_id, conversation_id)
                    
                    # If cache missing, try to restore from pending_state snapshot
                    if not prev_state and pending_state:
                        prev_state = PartnerQueryState()
                        partial = pending_state.partial_state
                        prev_state.intent = pending_state.intent
                        prev_state.degree_level = partial.get("degree_level")
                        prev_state.intake_term = partial.get("intake_term")
                        prev_state.university_query = partial.get("university_query")
                        prev_state.major_query = partial.get("major_query")
                        prev_state.teaching_language = partial.get("teaching_language")
                        prev_state.wants_scholarship = partial.get("wants_scholarship", False)
                        prev_state.wants_fees = partial.get("wants_fees", False)
                        prev_state.wants_requirements = partial.get("wants_requirements", False)
                        prev_state.wants_list = partial.get("wants_list", False)
                        prev_state.wants_earliest = partial.get("wants_earliest", False)
                        prev_state.city = partial.get("city")
                        prev_state.province = partial.get("province")
                        prev_state.intake_year = partial.get("intake_year")
                        prev_state.duration_years_target = partial.get("duration_years_target")
                        prev_state.duration_constraint = partial.get("duration_constraint")
                        prev_state.budget_max = partial.get("budget_max")
                        # Restore focus objects
                        req_focus_dict = partial.get("req_focus")
                        if req_focus_dict:
                            from app.services.slot_schema import RequirementFocus
                            prev_state.req_focus = RequirementFocus(**req_focus_dict) if isinstance(req_focus_dict, dict) else req_focus_dict
                        scholarship_focus_dict = partial.get("scholarship_focus")
                        if scholarship_focus_dict:
                            from app.services.slot_schema import ScholarshipFocus
                            prev_state.scholarship_focus = ScholarshipFocus(**scholarship_focus_dict) if isinstance(scholarship_focus_dict, dict) else scholarship_focus_dict
                    
                    # Only if both cache and pending_state missing, create blank state
                    if not prev_state:
                        prev_state = PartnerQueryState()
                        prev_state.intent = pending_slot_info.get("intent", "general")
                
                prev_state.pending_slot = pending_slot_info.get("slot")
                prev_state.is_clarifying = True
        
        # CLARIFICATION SHORT-CIRCUIT: If prev_state.pending_slot is set, treat input ONLY as slot value
        if prev_state and prev_state.pending_slot:
            state = PartnerQueryState()
            # Inherit intent and all flags from prev_state
            state.intent = prev_state.intent
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
            state.teaching_language = prev_state.teaching_language
            state.degree_level = prev_state.degree_level
            
            # Handle pending_slot
            if prev_state.pending_slot == "major_or_university":
                # Parse multiple values from the same message (e.g., "mARCH, Physics")
                major = self.parse_major_query(latest_user_message)
                uni = self.parse_university_query(latest_user_message)
                # Also check for intake_term in the same message
                term = self.parse_intake_term(latest_user_message)
                if term:
                    state.intake_term = term
                
                if major:
                    state.major_query = major
                elif uni:
                    state.university_query = uni
                # Also check for city
                city_match = re.search(r'\b(guangzhou|beijing|shanghai|shenzhen|hangzhou|nanjing|chengdu|xian|wuhan)\b', self.normalize_text(latest_user_message))
                if city_match:
                    state.city = city_match.group(1).title()
                
                # Clear slot if we got at least one (major, university, city, or intake_term)
                if state.major_query or state.university_query or state.city or state.intake_term:
                    state.pending_slot = None
                    state.is_clarifying = False
                    self._clear_pending_slot(partner_id, conversation_id)
                else:
                    state.pending_slot = prev_state.pending_slot
                    state.is_clarifying = True
                    return state
            elif prev_state.pending_slot == "scholarship_bundle":
                # Parse bundle: degree_level + major + intake_term + teaching_language
                # Use rule-based parsing to extract all fields at once
                parsed = self.parse_query_rules(latest_user_message)
                
                # Extract degree_level (fuzzy match ok)
                if parsed.get("degree_level"):
                    state.degree_level = parsed["degree_level"]
                else:
                    matched_degree = self.router._fuzzy_match_degree_level(latest_user_message)
                    if matched_degree:
                        state.degree_level = matched_degree
                
                # Extract intake_term
                if parsed.get("intake_term"):
                    state.intake_term = parsed["intake_term"]
                elif re.search(r'\b(mar(ch)?|spring)\b', self.router.normalize_query(latest_user_message)):
                    state.intake_term = "March"
                elif re.search(r'\b(sep(t|tember)?|fall|autumn)\b', self.router.normalize_query(latest_user_message)):
                    state.intake_term = "September"
                elif re.search(r'\b(earliest|asap|as soon as possible|soonest)\b', self.router.normalize_query(latest_user_message)):
                    state.wants_earliest = True
                
                # Extract major_query (clean stopwords)
                major_raw = parsed.get("major_raw")
                if major_raw:
                    # Clean semantic stopwords
                    if not self._is_semantic_stopword(major_raw):
                        state.major_query = major_raw
                
                # Extract teaching_language
                if parsed.get("teaching_language"):
                    state.teaching_language = parsed["teaching_language"]
                
                # Force intent and flags
                state.intent = prev_state.intent  # Keep SCHOLARSHIP
                state.wants_scholarship = True
                state.scholarship_focus = prev_state.scholarship_focus  # Inherit
                
                # Clear pending slot if we got at least degree_level
                if state.degree_level:
                    state.pending_slot = None
                    state.is_clarifying = False
                    self._clear_pending_slot(partner_id, conversation_id)
                else:
                    # Still missing degree_level, keep pending
                    state.pending_slot = prev_state.pending_slot
                    state.is_clarifying = True
                
                return state  # DO NOT call router.route() - intent locked
                
            elif prev_state.pending_slot == "degree_level":
                # Fuzzy match degree level
                matched_degree = self.router._fuzzy_match_degree_level(latest_user_message)
                if matched_degree:
                    state.degree_level = matched_degree
                    state.pending_slot = None  # Clear pending slot
                    state.is_clarifying = False
                    state.confidence = 1.0
                    state.major_query = None  # CRITICAL: Ensure degree words never become majors
                    # Preserve important flags from previous state
                    if hasattr(prev_state, 'wants_deadline'):
                        state.wants_deadline = prev_state.wants_deadline
                        if state.wants_deadline:
                            print(f"DEBUG: Preserved wants_deadline=True from previous state for degree_level reply")
                    # Preserve other context fields
                    if hasattr(prev_state, 'major_query') and prev_state.major_query:
                        state.major_query = prev_state.major_query
                    if hasattr(prev_state, 'university_query') and prev_state.university_query:
                        state.university_query = prev_state.university_query
                    if hasattr(prev_state, 'intake_term') and prev_state.intake_term:
                        state.intake_term = prev_state.intake_term
                    if hasattr(prev_state, 'intake_year') and prev_state.intake_year:
                        state.intake_year = prev_state.intake_year
                    if hasattr(prev_state, '_resolved_university_id') and prev_state._resolved_university_id:
                        state._resolved_university_id = prev_state._resolved_university_id
                    if hasattr(prev_state, '_resolved_major_ids') and prev_state._resolved_major_ids:
                        state._resolved_major_ids = prev_state._resolved_major_ids
                    # Clear from cache
                    self._clear_pending_slot(partner_id, conversation_id)
                else:
                    # If no match, keep pending_slot and return early
                    state.pending_slot = prev_state.pending_slot
                    state.is_clarifying = True
                    return state
            elif prev_state.pending_slot == "intake_term":
                # Parse intake term
                normalized = self.router.normalize_query(latest_user_message)
                if re.search(r'\b(mar(ch)?|spring)\b', normalized):
                    state.intake_term = "March"
                    state.pending_slot = None
                    state.is_clarifying = False
                    self._clear_pending_slot(partner_id, conversation_id)
                elif re.search(r'\b(sep(t|tember)?|fall|autumn)\b', normalized):
                    state.intake_term = "September"
                    state.pending_slot = None
                    state.is_clarifying = False
                    self._clear_pending_slot(partner_id, conversation_id)
                else:
                    state.pending_slot = prev_state.pending_slot
                    state.is_clarifying = True
                    return state
            elif prev_state.pending_slot == "teaching_language":
                # Parse teaching language
                normalized = self.router.normalize_query(latest_user_message)
                if re.search(r'\b(english|english-?taught|english\s+program)\b', normalized):
                    state.teaching_language = "English"
                    state.pending_slot = None
                    state.is_clarifying = False
                    self._clear_pending_slot(partner_id, conversation_id)
                elif re.search(r'\b(chinese|chinese-?taught|chinese\s+program|mandarin)\b', normalized):
                    state.teaching_language = "Chinese"
                    state.pending_slot = None
                    state.is_clarifying = False
                    self._clear_pending_slot(partner_id, conversation_id)
                else:
                    state.pending_slot = prev_state.pending_slot
                    state.is_clarifying = True
                    return state
            else:
                # Other pending slots - use router but with intent locked
                state.pending_slot = None
                state.is_clarifying = False
            
            return state
        
        # CLARIFICATION LOCKING: If pending_slot exists, DO NOT call LLM - parse deterministically
        # (Already handled above in pending_state check)
        
        # Check if user is accepting a suggested major from previous response
        if is_accepting_suggestion and accepted_major:
            # User accepted a suggestion - use the accepted major and continue with previous context
            print(f"DEBUG: User accepted major suggestion: '{accepted_major}' - continuing with previous query")
            # Try to get previous state from cache first
            cached = self._get_cached_state(partner_id, conversation_id)
            if cached and cached.get("state"):
                prev_state_obj = cached.get("state")
                # Create new state with accepted major
                state = PartnerQueryState()
                state.intent = prev_state_obj.intent if hasattr(prev_state_obj, 'intent') else "GENERAL"
                state.degree_level = prev_state_obj.degree_level if hasattr(prev_state_obj, 'degree_level') else None
                state.intake_term = prev_state_obj.intake_term if hasattr(prev_state_obj, 'intake_term') else None
                state.teaching_language = prev_state_obj.teaching_language if hasattr(prev_state_obj, 'teaching_language') else None
                state.wants_scholarship = prev_state_obj.wants_scholarship if hasattr(prev_state_obj, 'wants_scholarship') else False
                state.wants_fees = prev_state_obj.wants_fees if hasattr(prev_state_obj, 'wants_fees') else False
                state.wants_free_tuition = getattr(prev_state_obj, 'wants_free_tuition', False)
                state.wants_requirements = prev_state_obj.wants_requirements if hasattr(prev_state_obj, 'wants_requirements') else False
                state.wants_deadline = getattr(prev_state_obj, 'wants_deadline', False)
                state.wants_list = prev_state_obj.wants_list if hasattr(prev_state_obj, 'wants_list') else False
                state.major_query = accepted_major  # Use accepted major
                state.wants_earliest = prev_state_obj.wants_earliest if hasattr(prev_state_obj, 'wants_earliest') else False
                state.intake_year = prev_state_obj.intake_year if hasattr(prev_state_obj, 'intake_year') else None
                state.city = prev_state_obj.city if hasattr(prev_state_obj, 'city') else None
                state.province = prev_state_obj.province if hasattr(prev_state_obj, 'province') else None
                
                # Restore focus objects
                if hasattr(prev_state_obj, 'req_focus'):
                    state.req_focus = prev_state_obj.req_focus
                if hasattr(prev_state_obj, 'scholarship_focus'):
                    state.scholarship_focus = prev_state_obj.scholarship_focus
                
                # Resolve the accepted major to get its ID
                major_ids = self.resolve_major_ids(accepted_major, degree_level=state.degree_level)
                if major_ids:
                    state._resolved_major_ids = major_ids
                    print(f"DEBUG: Resolved accepted major '{accepted_major}' to IDs: {major_ids}")
                
                print(f"DEBUG: Restored state with accepted major: {state.major_query}, intent={state.intent}")
                # Clear any pending slot since we're accepting
                self._consume_pending(partner_id, conversation_id)
                # Cache the updated state
                self._set_cached_state(partner_id, conversation_id, state, None)
                return state
            else:
                # No cached state - try to extract from conversation history
                print(f"DEBUG: No cached state found, extracting from conversation history with accepted major: '{accepted_major}'")
                # Fall through to LLM extraction but with accepted_major set
                # We'll handle this in the LLM extraction section
        
        # FRESH QUERY: Use ALWAYS-LLM extraction
        try:
            # CRITICAL: If user accepted a major suggestion, use it immediately and skip LLM extraction
            if is_accepting_suggestion and accepted_major:
                # Try to get previous state from cache
                cached = self._get_cached_state(partner_id, conversation_id)
                if cached and cached.get("state"):
                    prev_state_obj = cached.get("state")
                    # Create new state with accepted major
                    state = PartnerQueryState()
                    state.intent = prev_state_obj.intent if hasattr(prev_state_obj, 'intent') else "GENERAL"
                    state.degree_level = prev_state_obj.degree_level if hasattr(prev_state_obj, 'degree_level') else None
                    state.intake_term = prev_state_obj.intake_term if hasattr(prev_state_obj, 'intake_term') else None
                    state.teaching_language = prev_state_obj.teaching_language if hasattr(prev_state_obj, 'teaching_language') else None
                    state.wants_scholarship = prev_state_obj.wants_scholarship if hasattr(prev_state_obj, 'wants_scholarship') else False
                    state.wants_fees = prev_state_obj.wants_fees if hasattr(prev_state_obj, 'wants_fees') else False
                    state.wants_free_tuition = getattr(prev_state_obj, 'wants_free_tuition', False)
                    state.wants_requirements = prev_state_obj.wants_requirements if hasattr(prev_state_obj, 'wants_requirements') else False
                    state.wants_deadline = getattr(prev_state_obj, 'wants_deadline', False)
                    state.wants_list = prev_state_obj.wants_list if hasattr(prev_state_obj, 'wants_list') else False
                    state.major_query = accepted_major  # Use accepted major
                    state.wants_earliest = prev_state_obj.wants_earliest if hasattr(prev_state_obj, 'wants_earliest') else False
                    state.intake_year = prev_state_obj.intake_year if hasattr(prev_state_obj, 'intake_year') else None
                    state.city = prev_state_obj.city if hasattr(prev_state_obj, 'city') else None
                    state.province = prev_state_obj.province if hasattr(prev_state_obj, 'province') else None
                    
                    # Restore focus objects
                    if hasattr(prev_state_obj, 'req_focus'):
                        state.req_focus = prev_state_obj.req_focus
                    if hasattr(prev_state_obj, 'scholarship_focus'):
                        state.scholarship_focus = prev_state_obj.scholarship_focus
                    
                    # Resolve the accepted major to get its ID
                    major_ids = self.resolve_major_ids(accepted_major, degree_level=state.degree_level)
                    if major_ids:
                        state._resolved_major_ids = major_ids
                        print(f"DEBUG: Resolved accepted major '{accepted_major}' to IDs: {major_ids}")
                    
                    print(f"DEBUG: Using accepted major '{accepted_major}' with cached state, skipping LLM extraction")
                    # Clear any pending slot since we're accepting
                    self._consume_pending(partner_id, conversation_id)
                    # Cache the updated state
                    self._set_cached_state(partner_id, conversation_id, state, None)
                    return state
            
            # CRITICAL: Check cached state BEFORE LLM extraction to preserve list intent
            # This ensures that when user provides "Language" or "March", we preserve LIST_UNIVERSITIES intent
            prev_list_intent = None
            prev_wants_list = False
            prev_wants_fees = False
            prev_wants_free_tuition = False
            prev_scholarship_focus = None
            if partner_id and conversation_id:
                cached = self._get_cached_state(partner_id, conversation_id)
                if cached and cached.get("state"):
                    prev_cached_state = cached.get("state")
                    if hasattr(prev_cached_state, 'intent') and prev_cached_state.intent in [self.router.INTENT_LIST_UNIVERSITIES, self.router.INTENT_LIST_PROGRAMS]:
                        prev_list_intent = prev_cached_state.intent
                        print(f"DEBUG: Found previous list intent in cache: {prev_list_intent}")
                    if hasattr(prev_cached_state, 'wants_list') and prev_cached_state.wants_list:
                        prev_wants_list = True
                        print(f"DEBUG: Found previous wants_list=True in cache")
                    if hasattr(prev_cached_state, 'wants_fees') and prev_cached_state.wants_fees:
                        prev_wants_fees = True
                        print(f"DEBUG: Found previous wants_fees=True in cache")
                    if hasattr(prev_cached_state, 'wants_free_tuition') and prev_cached_state.wants_free_tuition:
                        prev_wants_free_tuition = True
                        print(f"DEBUG: Found previous wants_free_tuition=True in cache")
                    # CRITICAL: Preserve scholarship_focus from cached state
                    if hasattr(prev_cached_state, 'scholarship_focus') and prev_cached_state.scholarship_focus:
                        prev_scholarship_focus = prev_cached_state.scholarship_focus
                        print(f"DEBUG: Found previous scholarship_focus in cache: csc={getattr(prev_scholarship_focus, 'csc', False)}")
            
            # Also check conversation history for "which university" patterns, "free tuition", and "CSC scholarship"
            is_list_query_from_history = False
            is_free_tuition_from_history = False
            is_csc_from_history = False
            for msg in reversed(conversation_history[-6:]):  # Check last 6 messages
                content = msg.get('content', '').lower()
                if 'which university' in content or 'list universities' in content or 'which universities' in content or 'offers free' in content:
                    is_list_query_from_history = True
                    print(f"DEBUG: Detected list query from conversation history: '{content[:50]}...'")
                # Check for free tuition indicators including "no fee", "zero fee", "without fee"
                free_tuition_patterns = [
                    'free tuition', 'tuition free', 'zero tuition', 'free tution', 
                    'no fee', 'zero fee', 'without fee', 'no fees', 'zero fees', 'without fees'
                ]
                if any(pattern in content for pattern in free_tuition_patterns):
                    is_free_tuition_from_history = True
                    print(f"DEBUG: Detected free tuition query from conversation history: '{content[:50]}...'")
                # Check for CSC/CSCA scholarship indicators
                csc_patterns = [
                    'csc scholarship', 'csca scholarship', 'china scholarship council', 
                    'csc', 'csca', 'offering csc', 'with csc'
                ]
                if any(pattern in content for pattern in csc_patterns):
                    is_csc_from_history = True
                    print(f"DEBUG: Detected CSC scholarship from conversation history: '{content[:50]}...'")
                if is_list_query_from_history:
                                break
            
            print(f"DEBUG: Calling llm_extract_state() for fresh query...")
            extracted = self.llm_extract_state(conversation_history, date.today(), prev_state)
            print(f"DEBUG: LLM extracted: intent={extracted.get('intent')}, confidence={extracted.get('confidence')}")
            
            # Convert extracted dict to PartnerQueryState
            state = PartnerQueryState()
            # CRITICAL: If we have a previous list intent OR detected from history, preserve it
            # This ensures list queries don't change to FEES when user provides additional info
            if prev_list_intent:
                state.intent = prev_list_intent
                state.wants_list = True
                if prev_wants_fees:
                    state.wants_fees = True
                if prev_wants_free_tuition:
                    state.wants_free_tuition = True
                    print(f"DEBUG: Preserved wants_free_tuition from cache")
                print(f"DEBUG: Preserved list intent from cache: {state.intent}, wants_list={state.wants_list}, wants_fees={state.wants_fees}, wants_free_tuition={getattr(state, 'wants_free_tuition', False)}")
            elif is_list_query_from_history:
                state.intent = self.router.INTENT_LIST_UNIVERSITIES
                state.wants_list = True
                if 'free' in latest_user_message.lower() or 'fee' in latest_user_message.lower() or is_free_tuition_from_history:
                    state.wants_fees = True
                # Check if it's a free tuition query
                if is_free_tuition_from_history or 'free tuition' in latest_user_message.lower() or 'tuition free' in latest_user_message.lower() or 'zero tuition' in latest_user_message.lower():
                    state.wants_free_tuition = True
                    print(f"DEBUG: Detected free tuition query from conversation history")
                print(f"DEBUG: Overriding LLM intent to LIST_UNIVERSITIES based on conversation history")
            else:
                state.intent = extracted.get("intent", "GENERAL")
            state.confidence = extracted.get("confidence", 0.5)
            
            # CRITICAL: Preserve free_tuition flag from conversation history regardless of intent path
            # This ensures "no fee" requirement persists through slot replies like "March" or "1 year"
            if is_free_tuition_from_history:
                state.wants_free_tuition = True
                state.wants_fees = True  # Also set wants_fees for free tuition queries
                print(f"DEBUG: Preserved wants_free_tuition=True from conversation history (applying to all query paths)")
            
            # Map extracted fields to state
            state.degree_level = extracted.get("degree_level")
            state.major_query = extracted.get("major_raw")  # Will be resolved later
            state.university_query = extracted.get("university_raw")  # Will be resolved later
            state.intake_term = extracted.get("intake_term")
            
            # CRITICAL: If major_query contains acronyms like LLB, BBA, MBA, infer degree_level
            # This prevents unnecessary clarification for degree level when it's already implied
            if state.major_query and not state.degree_level:
                major_lower = state.major_query.lower()
                # Check if it's a known acronym that implies degree level
                if major_lower in ['llb', 'bba', 'bcom'] or any(major_lower.startswith(acr) for acr in ['llb ', 'bba ', 'bcom ']):
                    state.degree_level = "Bachelor"
                    print(f"DEBUG: Inferred degree_level=Bachelor from major_query acronym: '{state.major_query}'")
                elif major_lower in ['mba', 'llm', 'mcom'] or any(major_lower.startswith(acr) for acr in ['mba ', 'llm ', 'mcom ']):
                    state.degree_level = "Master"
                    print(f"DEBUG: Inferred degree_level=Master from major_query acronym: '{state.major_query}'")
                else:
                    # Expand acronym and check if expanded version contains degree level keywords
                    expanded_major = self._expand_major_acronym(state.major_query)
                    if expanded_major != state.major_query:
                        expanded_lower = expanded_major.lower()
                        if expanded_lower.startswith('bachelor of'):
                            state.degree_level = "Bachelor"
                            print(f"DEBUG: Inferred degree_level=Bachelor from expanded major_query: '{expanded_major}'")
                        elif expanded_lower.startswith('master of'):
                            state.degree_level = "Master"
                            print(f"DEBUG: Inferred degree_level=Master from expanded major_query: '{expanded_major}'")
            
            # CRITICAL: Preserve scholarship_focus from cached state if available
            if prev_scholarship_focus:
                state.scholarship_focus = prev_scholarship_focus
                print(f"DEBUG: Preserved scholarship_focus from cached state: csc={getattr(state.scholarship_focus, 'csc', False)}")
            
            # CRITICAL: Check conversation history for scholarship types (Type A, Type B, Type C, CSC)
            # This ensures scholarship types are preserved when user provides slot replies like "March"
            scholarship_types_from_history = []
            if conversation_history and len(conversation_history) > 2:
                # Check last 10 messages for scholarship type mentions
                history_window = conversation_history[-10:] if len(conversation_history) > 10 else conversation_history
                for msg in reversed(history_window):
                    if msg.get('role') == 'user':
                        msg_text = msg.get('content', '').lower()
                        # Check for scholarship types in conversation history
                        # CRITICAL: Handle patterns like "B or C type of scholarship", "type B", "type-B", "B type", etc.
                        if re.search(r'\b(type-?a|type\s+a|type_a|a\s+type|a-?type)\b', msg_text, re.IGNORECASE):
                            scholarship_types_from_history.append("Type A")
                        if re.search(r'\b(type-?b|type\s+b|type_b|b\s+type|b-?type)\b', msg_text, re.IGNORECASE):
                            scholarship_types_from_history.append("Type B")
                        if re.search(r'\b(type-?c|type\s+c|type_c|c\s+type|c-?type)\b', msg_text, re.IGNORECASE):
                            scholarship_types_from_history.append("Type C")
                        # CRITICAL: CSC/CSCA/Chinese Government Scholarship/Chinese Gov/Chinese Gov. are all the same
                        if re.search(r'\b(csca|csc|china\s+scholarship\s+council|chinese\s+government\s+scholarship|chinese\s+gov\.?\s+scholarship|chinese\s+govt\.?\s+scholarship)\b', msg_text):
                            scholarship_types_from_history.append("CSC")
                        if scholarship_types_from_history:
                            break  # Found scholarship types, stop searching
            
            # CRITICAL: Extract CSC/CSCA scholarship flag and scholarship types from parse_query_rules OR conversation history
            # This must happen BEFORE setting wants_requirements to avoid false positives
            parsed_rules = self.parse_query_rules(latest_user_message)
            
            # Extract scholarship types (Type A, Type B, Type C, CSC) from current message OR conversation history
            scholarship_types = parsed_rules.get("scholarship_types", [])
            if not scholarship_types and scholarship_types_from_history:
                # Use scholarship types from conversation history if not in current message
                scholarship_types = list(set(scholarship_types_from_history))  # Remove duplicates
                print(f"DEBUG: Preserved scholarship types from conversation history: {scholarship_types}")
            
            # Also preserve from cached state if available (when conversation_id is not None)
            if not scholarship_types and prev_state and hasattr(prev_state, '_scholarship_types') and prev_state._scholarship_types:
                scholarship_types = prev_state._scholarship_types
                print(f"DEBUG: Preserved scholarship types from previous state: {scholarship_types}")
            
            # CRITICAL: If still no scholarship types but we have SCHOLARSHIP intent, check conversation history more thoroughly
            # This handles the case when conversation_id is None (no cached state) but conversation_history is available
            if not scholarship_types and state.intent == self.router.INTENT_SCHOLARSHIP and conversation_history:
                # Check the FIRST user message in conversation history (original query)
                for msg in conversation_history:
                    if msg.get('role') == 'user':
                        first_user_msg = msg.get('content', '').lower()
                        # Extract scholarship types from original query
                        temp_types = []
                        # CRITICAL: Handle patterns like "B or C type of scholarship", "type B", "type-B", "B type", etc.
                        if re.search(r'\b(type-?a|type\s+a|type_a|a\s+type|a-?type)\b', first_user_msg, re.IGNORECASE):
                            temp_types.append("Type A")
                        if re.search(r'\b(type-?b|type\s+b|type_b|b\s+type|b-?type)\b', first_user_msg, re.IGNORECASE):
                            temp_types.append("Type B")
                        if re.search(r'\b(type-?c|type\s+c|type_c|c\s+type|c-?type)\b', first_user_msg, re.IGNORECASE):
                            temp_types.append("Type C")
                        if re.search(r'\b(csca|csc|china\s+scholarship\s+council|chinese\s+government\s+scholarship|chinese\s+gov\.?\s+scholarship|chinese\s+govt\.?\s+scholarship)\b', first_user_msg):
                            temp_types.append("CSC")
                        if temp_types:
                            scholarship_types = list(set(temp_types))
                            print(f"DEBUG: Extracted scholarship types from first user message in conversation history: {scholarship_types}")
                            break
            
            if scholarship_types:
                # Store scholarship types in state for filtering
                state._scholarship_types = scholarship_types
                print(f"DEBUG: Detected scholarship types: {scholarship_types}")
            
            # Check both latest message and conversation history for CSC
            if parsed_rules.get("wants_csca_scholarship") or is_csc_from_history or "CSC" in scholarship_types:
                # Initialize scholarship_focus if not already set
                if not hasattr(state, 'scholarship_focus') or not state.scholarship_focus:
                    from app.services.slot_schema import ScholarshipFocus
                    state.scholarship_focus = ScholarshipFocus(any=True, csc=True, university=False)
                else:
                    # Preserve existing scholarship_focus but set CSC flag
                    state.scholarship_focus.csc = True
                state.wants_scholarship = True
                # CRITICAL: For CSC/CSCA queries, this is a scholarship query, NOT a requirements query
                # The word "requiring" in "requiring CSC/CSCA" refers to scholarship requirement, not document requirements
                state.wants_requirements = False
                source = "conversation history" if is_csc_from_history and not parsed_rules.get("wants_csca_scholarship") else "parse_query_rules"
                print(f"DEBUG: Detected CSC/CSCA scholarship requirement from {source} - set wants_scholarship=True, wants_requirements=False, scholarship_focus.csc=True")
            
            # CRITICAL: For scholarship queries with major provided, it should be LIST_UNIVERSITIES, not LIST_PROGRAMS
            # If user asks "Do we have any Chinese language course with B or C type scholarship?"
            # They want to see which universities offer that, not list all programs
            if state.wants_scholarship and state.major_query and not state.university_query:
                # Override intent to LIST_UNIVERSITIES if it was LIST_PROGRAMS
                if state.intent == self.router.INTENT_LIST_PROGRAMS:
                    state.intent = self.router.INTENT_LIST_UNIVERSITIES
                    state.wants_list = True
                    print(f"DEBUG: Scholarship query with major provided - changed intent from LIST_PROGRAMS to LIST_UNIVERSITIES")
            
            # CRITICAL: For SCHOLARSHIP intent, do NOT set req_focus fields to True by default
            # Only include requirement fields if user explicitly asks for them
            # This reduces context size and token usage
            if state.intent == self.router.INTENT_SCHOLARSHIP and not state.wants_requirements:
                from app.services.slot_schema import RequirementFocus
                # Reset req_focus to all False for SCHOLARSHIP intent (unless user explicitly asks for requirements)
                state.req_focus = RequirementFocus(
                    docs=False,
                    exams=False,
                    bank=False,
                    age=False,
                    inside_china=False,
                    deadline=False,
                    accommodation=False,
                    country=False
                )
                print(f"DEBUG: SCHOLARSHIP intent - reset req_focus to all False to reduce context size")
            state.intake_year = extracted.get("intake_year")
            # Parse teaching_language from extracted or from latest message if not extracted
            state.teaching_language = extracted.get("teaching_language")
            if not state.teaching_language:
                # Try to parse from latest message (handles "English taught", "english-taught", etc.)
                state.teaching_language = self.parse_teaching_language(latest_user_message)
            
            # Extract city and province (with fuzzy matching fallback from parse_query_rules)
            # BUT: Check if LLM extracted a university first - if so, don't treat it as a city
            state.city = extracted.get("city")
            state.province = extracted.get("province")
            
            # CRITICAL: If LLM extracted a city, check if it's actually a university name
            # This prevents "Nanchang University" from being treated as just "Nanchang" city
            if state.city:
                # Try to match the city name as a university (in case it's "Nanchang" from "Nanchang University")
                city_name = state.city  # Store original city name before checking
                matched, uni_dict, _ = self._fuzzy_match_university(city_name)
                if matched and uni_dict:
                    # It's actually a university, not a city
                    state.university_query = uni_dict.get("name")
                    state._resolved_university_id = uni_dict.get("id")
                    state.city = None  # Clear city since it's actually a university
                    print(f"DEBUG: LLM extracted '{city_name}' as city, but it's actually a university: {state.university_query}")
                else:
                    print(f"DEBUG: Extracted city from LLM: {state.city}")
            
            # Fallback: if LLM didn't extract city/province, try parse_query_rules (handles typos and "in X" patterns)
            if not state.city and not state.province:
                rules = self.parse_query_rules(latest_user_message)
                if rules.get("city"):
                    # Also check if parse_query_rules city is actually a university
                    matched, uni_dict, _ = self._fuzzy_match_university(rules.get("city"))
                    if matched and uni_dict:
                        state.university_query = uni_dict.get("name")
                        state._resolved_university_id = uni_dict.get("id")
                        print(f"DEBUG: parse_query_rules extracted '{rules.get('city')}' as city, but it's actually a university: {state.university_query}")
                    else:
                        state.city = rules.get("city")
                        print(f"DEBUG: Extracted city from parse_query_rules: {state.city}")
                if rules.get("province"):
                    state.province = rules.get("province")
                    print(f"DEBUG: Extracted province from parse_query_rules: {state.province}")
            elif state.province:
                print(f"DEBUG: Extracted province from LLM: {state.province}")
            
            # CRITICAL: If user accepted a major suggestion, use it instead of what LLM extracted
            if is_accepting_suggestion and accepted_major:
                state.major_query = accepted_major
                print(f"DEBUG: Overriding LLM major_query with accepted major: '{accepted_major}'")
                # Resolve the accepted major
                major_ids = self.resolve_major_ids(accepted_major, degree_level=state.degree_level)
                if major_ids:
                    state._resolved_major_ids = major_ids
                    print(f"DEBUG: Resolved accepted major to IDs: {major_ids}")
                # Clear any pending slot
                self._consume_pending(partner_id, conversation_id)
            
            # CRITICAL: Override intent if parse_query_rules detected "which universities" or "list universities"
            # This ensures list queries take priority over fee queries
            rules_result = self.parse_query_rules(latest_user_message)
            if rules_result.get("intent") == "list_universities":
                print(f"DEBUG: parse_query_rules detected LIST_UNIVERSITIES intent - overriding LLM intent from {state.intent} to LIST_UNIVERSITIES")
                state.intent = self.router.INTENT_LIST_UNIVERSITIES
                state.wants_list = True
            # CRITICAL: Also check if LLM extracted LIST_PROGRAMS but user asked about universities offering a major
            # In this case, if intake_term is specified, it should be LIST_UNIVERSITIES to get program intake details
            elif state.intent == self.router.INTENT_LIST_PROGRAMS and state.intake_term:
                # If user specified intake_term, they want program intake details, not just major list
                # Check if the query mentions "university" or "universities" - if so, it's LIST_UNIVERSITIES
                if "universit" in latest_user_message.lower():
                    print(f"DEBUG: LIST_PROGRAMS with intake_term and 'university' mention - changing to LIST_UNIVERSITIES to get program intake details")
                    state.intent = self.router.INTENT_LIST_UNIVERSITIES
                    state.wants_list = True
                # Keep wants_fees if it was set (for fee comparison)
                if extracted.get("wants_fees") or rules_result.get("wants_fees"):
                    state.wants_fees = True
                    print(f"DEBUG: Preserving wants_fees=True for fee comparison in list query")
                # Keep wants_free_tuition if it was set (for free tuition queries)
                if rules_result.get("wants_free_tuition"):
                    state.wants_free_tuition = True
                    print(f"DEBUG: Preserving wants_free_tuition=True for free tuition query")
            
            # CRITICAL: For deadline queries, force intent to GENERAL (not REQUIREMENTS)
            # Deadline queries need to find specific programs, not general requirements
            state.wants_deadline = extracted.get("wants_deadline", False)
            # Fallback: parse from latest message if LLM didn't extract it
            if not state.wants_deadline:
                rules = self.parse_query_rules(latest_user_message)
                state.wants_deadline = rules.get("wants_deadline", False)
            if state.wants_deadline:
                # Force intent to GENERAL for deadline queries (they need specific program info)
                if state.intent in ["REQUIREMENTS", self.router.INTENT_ADMISSION_REQUIREMENTS]:
                    print(f"DEBUG: Deadline query detected - changing intent from {state.intent} to GENERAL")
                    state.intent = "GENERAL"
                print(f"DEBUG: Deadline query detected - will preserve intake_term and intake_year filters if provided")
            state.duration_years_target = extracted.get("duration_years")
            state.wants_earliest = extracted.get("wants_earliest", False)
            # CRITICAL: Only set wants_scholarship from extracted if CSC/CSCA was NOT detected
            # If CSC/CSCA was detected above (from parsed_rules OR conversation history), wants_scholarship is already set to True
            if not parsed_rules.get("wants_csca_scholarship") and not is_csc_from_history:
                state.wants_scholarship = extracted.get("wants_scholarship", False)
            # Otherwise, it's already set to True above
            # CRITICAL: Only set wants_requirements if CSC/CSCA was NOT detected
            # If CSC/CSCA was detected above (from parsed_rules OR conversation history), wants_requirements is already set to False
            if not parsed_rules.get("wants_csca_scholarship") and not is_csc_from_history:
                state.wants_requirements = extracted.get("wants_requirements", False)
            # Otherwise, it's already set to False above
            # CRITICAL: Only set wants_fees from extracted if not already set from history/cache
            if not hasattr(state, 'wants_fees') or not state.wants_fees:
                state.wants_fees = extracted.get("wants_fees", False)
            # CRITICAL: Only set wants_free_tuition from extracted/rules if not already set from history/cache
            if not hasattr(state, 'wants_free_tuition') or not state.wants_free_tuition:
                # Check rules_result for wants_free_tuition (includes "no fee", "zero fee" patterns)
                if rules_result.get("wants_free_tuition"):
                    state.wants_free_tuition = True
                    print(f"DEBUG: Set wants_free_tuition=True from parse_query_rules")
            
            # CRITICAL: For slot replies like "September", preserve wants_deadline from previous context
            # This happens BEFORE context preservation, so we check cached state here too
            if not state.wants_deadline and partner_id and conversation_id:
                cached = self._get_cached_state(partner_id, conversation_id)
                if cached and cached.get("state"):
                    prev_cached = cached.get("state")
                    if hasattr(prev_cached, 'wants_deadline') and prev_cached.wants_deadline:
                        # Check if this is a slot reply (short message like "September")
                        is_slot_reply = len(latest_user_message.split()) <= 2 and any(
                            kw in latest_user_message.lower() for kw in ["bachelor", "master", "phd", "language", "march", "september", "english", "chinese"]
                        )
                        if is_slot_reply:
                            state.wants_deadline = prev_cached.wants_deadline
                            print(f"DEBUG: Preserved wants_deadline={state.wants_deadline} from cached state for slot reply")
            
            # CRITICAL: For deadline queries, force intent to GENERAL (not REQUIREMENTS)
            # Deadline queries need to find specific programs, not general requirements
            if state.wants_deadline:
                # Force intent to GENERAL for deadline queries (they need specific program info)
                if state.intent in ["REQUIREMENTS", self.router.INTENT_ADMISSION_REQUIREMENTS]:
                    print(f"DEBUG: Deadline query detected - changing intent from {state.intent} to GENERAL")
                    state.intent = "GENERAL"
                print(f"DEBUG: Deadline query detected - will preserve intake_term and intake_year filters if provided")
            state.page_action = extracted.get("page_action", "none")
            
            # CRITICAL: Initialize has_university_pattern early to avoid UnboundLocalError
            # This variable is used in multiple places to detect university switches
            has_university_pattern = False
            
            # CONTEXT PRESERVATION: Always preserve key fields (major, university, teaching_language, intake, degree_level)
            # unless user explicitly changes them or changes intent in a way that doesn't make sense
            # Check if user is changing intent (e.g., "what about fees?", "does it require bank_statement?")
            is_changing_intent = (
                state.intent != prev_state.intent if prev_state else False
            ) or any(kw in latest_user_message.lower() for kw in ["what about", "how about", "does it require", "does it need", "is", "are"])
            
            # Check if this is a list query that should preserve context
            # Check both current state and previous cached state for list intent
            is_list_query = state.intent in [self.router.INTENT_LIST_PROGRAMS, self.router.INTENT_LIST_UNIVERSITIES]
            
            # Initialize cached to None to avoid UnboundLocalError when conversation_id is None
            cached = None
            
            # Always try to preserve context from cached state if available (even if prev_state is None)
            if partner_id and conversation_id:
                cached = self._get_cached_state(partner_id, conversation_id)
                if cached and cached.get("state"):
                    prev_cached_state = cached.get("state")
                    # Also check if previous cached state had list intent (for is_list_query check)
                    if hasattr(prev_cached_state, 'intent') and prev_cached_state.intent in [self.router.INTENT_LIST_PROGRAMS, self.router.INTENT_LIST_UNIVERSITIES]:
                        is_list_query = True
                    # Also preserve if this is a slot reply (like "Bachelor" to a clarification question)
                    is_slot_reply = len(latest_user_message.split()) <= 2 and any(
                        kw in latest_user_message.lower() for kw in ["bachelor", "master", "phd", "language", "march", "september", "english", "chinese"]
                    )
                    
                    # ALWAYS preserve context - key fields (major, university, teaching_language, intake, degree_level)
                    # should be preserved until user explicitly changes them
                    # This ensures context carries across queries until user explicitly changes intent or fields
                    should_preserve = True  # Always preserve if we have cached state
                    
                    # For deadline queries (GENERAL intent with wants_deadline), also preserve context
                    # Check both current state and previous cached state for wants_deadline
                    is_deadline_query = (hasattr(state, 'wants_deadline') and state.wants_deadline) or \
                                       (hasattr(prev_cached_state, 'wants_deadline') and prev_cached_state.wants_deadline) or \
                                       any(kw in latest_user_message.lower() for kw in ["deadline", "application deadline", "when is the deadline"])
                    
                    # Check if user explicitly changed any key fields in current message
                    # Extract from conversation history (last 16 messages) to detect changes
                    history_window = conversation_history[-16:] if len(conversation_history) > 16 else conversation_history
                    user_changed_major = False
                    user_changed_university = False
                    user_changed_degree = False
                    user_changed_intake = False
                    
                    # CRITICAL: Check for explicit university changes in short messages like "What about LNPU?"
                    # This must happen BEFORE context preservation to ensure new university is used
                    # Always check for university patterns in short messages (likely university switches)
                    has_university_pattern = False
                    is_short_message = len(latest_user_message.split()) <= 5
                    if is_short_message:
                        # Check for patterns like "what about X", "how about X", "X university", etc.
                        uni_patterns = [
                            r'\b(what|how)\s+about\s+([A-Z][A-Z0-9]+|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b',
                            r'\b([A-Z][A-Z0-9]+|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(university|institute|college)\b',
                            r'^([A-Z][A-Z0-9]+|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s*[?]?$',  # Just "LNPU?" or "HIT?"
                        ]
                        for pattern in uni_patterns:
                            match = re.search(pattern, latest_user_message, re.IGNORECASE)
                            if match:
                                potential_uni = match.group(2) if match.lastindex >= 2 else match.group(1)
                                # Try to resolve it
                                matched, uni_dict, _ = self._fuzzy_match_university(potential_uni)
                                if matched and uni_dict:
                                    state.university_query = uni_dict.get("name")
                                    state._resolved_university_id = uni_dict.get("id")
                                    has_university_pattern = True
                                    print(f"DEBUG: Detected explicit university change to '{state.university_query}' from message: '{latest_user_message}'")
                            break
                    
                    # Check if current message explicitly mentions changes to key fields
                    if latest_user_message:
                        msg_lower = latest_user_message.lower()
                        # Check for major changes (if major_query is in current message but different from cached)
                        if state.major_query and hasattr(prev_cached_state, 'major_query') and prev_cached_state.major_query:
                            if state.major_query.lower() != prev_cached_state.major_query.lower():
                                user_changed_major = True
                        # Check for university changes (already detected in has_university_pattern above)
                        if has_university_pattern:
                            user_changed_university = True
                        # Check for degree level changes
                        if state.degree_level and hasattr(prev_cached_state, 'degree_level') and prev_cached_state.degree_level:
                            if state.degree_level.lower() != prev_cached_state.degree_level.lower():
                                user_changed_degree = True
                        # Check for intake changes
                        if state.intake_term and hasattr(prev_cached_state, 'intake_term') and prev_cached_state.intake_term:
                            if state.intake_term.lower() != prev_cached_state.intake_term.lower():
                                user_changed_intake = True
                    
                    if should_preserve:
                        print(f"DEBUG: Preserving context from previous query - intent change={is_changing_intent}, slot_reply={is_slot_reply}, list_query={is_list_query}, deadline_query={is_deadline_query}, changed: major={user_changed_major}, uni={user_changed_university}, degree={user_changed_degree}, intake={user_changed_intake}")
                        # ALWAYS preserve context fields unless user explicitly changed them
                        # This ensures context carries across ALL queries (not just slot replies/list queries)
                        preserve_all = True  # Always preserve all context by default
                        
                        if preserve_all:
                            # For slot replies, list queries, and deadline queries, preserve ALL context fields from previous query
                            if is_list_query:
                                context_type = "list query"
                            elif is_deadline_query:
                                context_type = "deadline query"
                            else:
                                context_type = "slot reply"
                            # Preserve major_query unless user explicitly changed it
                            if not user_changed_major and hasattr(prev_cached_state, 'major_query') and prev_cached_state.major_query:
                                if not state.major_query or state.major_query.lower() != prev_cached_state.major_query.lower():
                                    state.major_query = prev_cached_state.major_query
                                    print(f"DEBUG: Preserved major_query: {state.major_query}")
                            # CRITICAL: Also preserve _resolved_major_ids if available
                            if not user_changed_major and hasattr(prev_cached_state, '_resolved_major_ids') and prev_cached_state._resolved_major_ids:
                                state._resolved_major_ids = prev_cached_state._resolved_major_ids
                                print(f"DEBUG: Preserved _resolved_major_ids: {state._resolved_major_ids}")
                            # Preserve degree_level unless user explicitly changed it
                            if not user_changed_degree and hasattr(prev_cached_state, 'degree_level') and prev_cached_state.degree_level:
                                if not state.degree_level or state.degree_level.lower() != prev_cached_state.degree_level.lower():
                                    state.degree_level = prev_cached_state.degree_level
                                    print(f"DEBUG: Preserved degree_level: {state.degree_level}")
                            # Preserve intake_term unless user explicitly changed it
                            if not user_changed_intake and hasattr(prev_cached_state, 'intake_term') and prev_cached_state.intake_term:
                                if not state.intake_term or state.intake_term.lower() != prev_cached_state.intake_term.lower():
                                    state.intake_term = prev_cached_state.intake_term
                                    print(f"DEBUG: Preserved intake_term: {state.intake_term}")
                            # Preserve intake_year
                            if hasattr(prev_cached_state, 'intake_year') and prev_cached_state.intake_year:
                                if not state.intake_year or state.intake_year != prev_cached_state.intake_year:
                                    state.intake_year = prev_cached_state.intake_year
                                    print(f"DEBUG: Preserved intake_year: {state.intake_year}")
                            # CRITICAL: Do NOT preserve teaching_language if it was incorrectly set for Language programs
                            # Only preserve if it was explicitly requested (e.g., "taught in English")
                            # Skip preservation if degree_level is Language and teaching_language was set (likely incorrectly from major name)
                            if hasattr(prev_cached_state, 'teaching_language') and prev_cached_state.teaching_language:
                                if state.degree_level == "Language":
                                    # Don't preserve teaching_language for Language programs unless explicitly mentioned in current message
                                    msg_lower = latest_user_message.lower()
                                    if not (re.search(r'\b(taught\s+in\s+(chinese|english)|(chinese|english)-?taught)\b', msg_lower)):
                                        print(f"DEBUG: Not preserving teaching_language for Language program - likely incorrectly set from major name")
                                    else:
                                        # Explicitly mentioned in current message, preserve it
                                        if not state.teaching_language or state.teaching_language.lower() != prev_cached_state.teaching_language.lower():
                                            state.teaching_language = prev_cached_state.teaching_language
                                            print(f"DEBUG: Preserved teaching_language: {state.teaching_language}")
                                else:
                                    # For non-Language programs, preserve normally
                                    if not state.teaching_language or state.teaching_language.lower() != prev_cached_state.teaching_language.lower():
                                        state.teaching_language = prev_cached_state.teaching_language
                                        print(f"DEBUG: Preserved teaching_language: {state.teaching_language}")
                            # Only preserve university_query if user didn't explicitly change it
                            if not user_changed_university and hasattr(prev_cached_state, 'university_query') and prev_cached_state.university_query:
                                if not state.university_query or state.university_query.lower() != prev_cached_state.university_query.lower():
                                    state.university_query = prev_cached_state.university_query
                                    state._resolved_university_id = prev_cached_state._resolved_university_id if hasattr(prev_cached_state, '_resolved_university_id') else None
                                    print(f"DEBUG: Preserved university_query: {state.university_query}")
                            elif user_changed_university:
                                print(f"DEBUG: User changed university, using new university_query: {state.university_query}")
                            if hasattr(prev_cached_state, 'wants_deadline') and prev_cached_state.wants_deadline:
                                state.wants_deadline = prev_cached_state.wants_deadline
                                print(f"DEBUG: Preserved wants_deadline ({context_type}): {state.wants_deadline}")
                            # CRITICAL: Preserve scholarship types from cached state for SCHOLARSHIP or LIST_UNIVERSITIES intent
                            if (state.intent in [self.router.INTENT_SCHOLARSHIP, self.router.INTENT_LIST_UNIVERSITIES] and 
                                hasattr(prev_cached_state, '_scholarship_types') and prev_cached_state._scholarship_types):
                                state._scholarship_types = prev_cached_state._scholarship_types
                                print(f"DEBUG: Preserved _scholarship_types from cached state: {state._scholarship_types}")
                            # CRITICAL: Preserve LIST_UNIVERSITIES or LIST_PROGRAMS intent if it was set previously
                            # This ensures that when user provides additional info (like "Chinese Language Program"),
                            # the intent stays as LIST_UNIVERSITIES instead of changing to FEES
                            if hasattr(prev_cached_state, 'intent') and prev_cached_state.intent in [self.router.INTENT_LIST_UNIVERSITIES, self.router.INTENT_LIST_PROGRAMS]:
                                # Only preserve list intent if user is not explicitly changing intent
                                # Check if current message contains intent-changing keywords
                                intent_change_keywords = ["what about", "how about", "instead", "change to", "now i want", "actually"]
                                is_explicit_intent_change = any(kw in latest_user_message.lower() for kw in intent_change_keywords)
                                
                                if not is_explicit_intent_change:
                                    # User is providing additional info, preserve list intent
                                    state.intent = prev_cached_state.intent
                                    print(f"DEBUG: Preserved list intent ({context_type}): {state.intent}")
                                    # Also preserve wants_list flag
                                    state.wants_list = True
                                    print(f"DEBUG: Preserved wants_list=True for list intent")
                            if hasattr(prev_cached_state, 'wants_list') and prev_cached_state.wants_list:
                                # Only set wants_list if intent wasn't already preserved above
                                if state.intent not in [self.router.INTENT_LIST_UNIVERSITIES, self.router.INTENT_LIST_PROGRAMS]:
                                    state.wants_list = prev_cached_state.wants_list
                                    print(f"DEBUG: Preserved wants_list ({context_type}): {state.wants_list}")
                            if hasattr(prev_cached_state, 'wants_fees') and prev_cached_state.wants_fees:
                                state.wants_fees = prev_cached_state.wants_fees
                                print(f"DEBUG: Preserved wants_fees ({context_type}): {state.wants_fees}")
                            if hasattr(prev_cached_state, 'wants_free_tuition') and prev_cached_state.wants_free_tuition:
                                state.wants_free_tuition = prev_cached_state.wants_free_tuition
                                print(f"DEBUG: Preserved wants_free_tuition ({context_type}): {state.wants_free_tuition}")
                            if hasattr(prev_cached_state, '_resolved_major_ids') and prev_cached_state._resolved_major_ids:
                                state._resolved_major_ids = prev_cached_state._resolved_major_ids
                                print(f"DEBUG: Preserved _resolved_major_ids ({context_type}): {state._resolved_major_ids}")
                            if hasattr(prev_cached_state, '_resolved_university_id') and prev_cached_state._resolved_university_id:
                                state._resolved_university_id = prev_cached_state._resolved_university_id
                                print(f"DEBUG: Preserved _resolved_university_id ({context_type}): {state._resolved_university_id}")
                            # Preserve city and province context
                            if hasattr(prev_cached_state, 'city') and prev_cached_state.city:
                                if not state.city or state.city.lower() != prev_cached_state.city.lower():
                                    state.city = prev_cached_state.city
                                    print(f"DEBUG: Preserved city ({context_type}): {state.city}")
                            if hasattr(prev_cached_state, 'province') and prev_cached_state.province:
                                if not state.province or state.province.lower() != prev_cached_state.province.lower():
                                    state.province = prev_cached_state.province
                                    print(f"DEBUG: Preserved province ({context_type}): {state.province}")
                    else:
                        # ALWAYS preserve context for all queries unless user explicitly changed fields
                        # Use the same logic as above - preserve unless user changed it
                        if not user_changed_major and hasattr(prev_cached_state, 'major_query') and prev_cached_state.major_query:
                            if not state.major_query or state.major_query.lower() != prev_cached_state.major_query.lower():
                                state.major_query = prev_cached_state.major_query
                                print(f"DEBUG: Preserved major_query: {state.major_query}")
                        if not user_changed_degree and hasattr(prev_cached_state, 'degree_level') and prev_cached_state.degree_level:
                            if not state.degree_level or state.degree_level.lower() != prev_cached_state.degree_level.lower():
                                state.degree_level = prev_cached_state.degree_level
                                print(f"DEBUG: Preserved degree_level: {state.degree_level}")
                        if not user_changed_intake and hasattr(prev_cached_state, 'intake_term') and prev_cached_state.intake_term:
                            if not state.intake_term or state.intake_term.lower() != prev_cached_state.intake_term.lower():
                                state.intake_term = prev_cached_state.intake_term
                                print(f"DEBUG: Preserved intake_term: {state.intake_term}")
                        if hasattr(prev_cached_state, 'intake_year') and prev_cached_state.intake_year:
                            if not state.intake_year or state.intake_year != prev_cached_state.intake_year:
                                state.intake_year = prev_cached_state.intake_year
                                print(f"DEBUG: Preserved intake_year: {state.intake_year}")
                        if hasattr(prev_cached_state, 'teaching_language') and prev_cached_state.teaching_language:
                            if not state.teaching_language or state.teaching_language.lower() != prev_cached_state.teaching_language.lower():
                                state.teaching_language = prev_cached_state.teaching_language
                                print(f"DEBUG: Preserved teaching_language: {state.teaching_language}")
                        # Only preserve university_query if user didn't explicitly change it
                        if not user_changed_university and hasattr(prev_cached_state, 'university_query') and prev_cached_state.university_query:
                            if not state.university_query or state.university_query.lower() != prev_cached_state.university_query.lower():
                                state.university_query = prev_cached_state.university_query
                                state._resolved_university_id = prev_cached_state._resolved_university_id if hasattr(prev_cached_state, '_resolved_university_id') else None
                                print(f"DEBUG: Preserved university_query: {state.university_query}")
                        elif user_changed_university:
                            print(f"DEBUG: User changed university, using new university_query: {state.university_query}")
                        # Preserve wants_deadline flag (important for deadline queries)
                        if hasattr(prev_cached_state, 'wants_deadline') and prev_cached_state.wants_deadline:
                            if not hasattr(state, 'wants_deadline') or not state.wants_deadline:
                                state.wants_deadline = prev_cached_state.wants_deadline
                        # CRITICAL: Preserve scholarship types from cached state for SCHOLARSHIP intent
                        if state.intent == self.router.INTENT_SCHOLARSHIP and hasattr(prev_cached_state, '_scholarship_types') and prev_cached_state._scholarship_types:
                            state._scholarship_types = prev_cached_state._scholarship_types
                            print(f"DEBUG: Preserved _scholarship_types from cached state (else branch): {state._scholarship_types}")
                        # Preserve wants_list flag (important for list queries)
                        if hasattr(prev_cached_state, 'wants_list') and prev_cached_state.wants_list:
                            if not hasattr(state, 'wants_list') or not state.wants_list:
                                state.wants_list = prev_cached_state.wants_list
                                print(f"DEBUG: Preserved wants_list: {state.wants_list}")
                        # Preserve wants_fees flag (important for fee queries)
                        if hasattr(prev_cached_state, 'wants_fees') and prev_cached_state.wants_fees:
                            if not hasattr(state, 'wants_fees') or not state.wants_fees:
                                state.wants_fees = prev_cached_state.wants_fees
                                print(f"DEBUG: Preserved wants_fees: {state.wants_fees}")
                        # Preserve wants_free_tuition flag (important for free tuition queries)
                        if hasattr(prev_cached_state, 'wants_free_tuition') and prev_cached_state.wants_free_tuition:
                            if not hasattr(state, 'wants_free_tuition') or not state.wants_free_tuition:
                                state.wants_free_tuition = prev_cached_state.wants_free_tuition
                                print(f"DEBUG: Preserved wants_free_tuition: {state.wants_free_tuition}")
                                print(f"DEBUG: Preserved wants_deadline: {state.wants_deadline}")
                        # Preserve resolved IDs if available
                        if hasattr(prev_cached_state, '_resolved_major_ids') and prev_cached_state._resolved_major_ids:
                            if not hasattr(state, '_resolved_major_ids') or not state._resolved_major_ids:
                                state._resolved_major_ids = prev_cached_state._resolved_major_ids
                                print(f"DEBUG: Preserved _resolved_major_ids: {state._resolved_major_ids}")
                        if hasattr(prev_cached_state, '_resolved_university_id') and prev_cached_state._resolved_university_id:
                            if not hasattr(state, '_resolved_university_id') or not state._resolved_university_id:
                                state._resolved_university_id = prev_cached_state._resolved_university_id
                                print(f"DEBUG: Preserved _resolved_university_id: {state._resolved_university_id}")
                        # Preserve city and province context
                        if hasattr(prev_cached_state, 'city') and prev_cached_state.city:
                            if not state.city or state.city.lower() != prev_cached_state.city.lower():
                                state.city = prev_cached_state.city
                                print(f"DEBUG: Preserved city: {state.city}")
                        if hasattr(prev_cached_state, 'province') and prev_cached_state.province:
                            if not state.province or state.province.lower() != prev_cached_state.province.lower():
                                state.province = prev_cached_state.province
                                print(f"DEBUG: Preserved province: {state.province}")
                    # Preserve previous university_ids or intake_ids if available (for filtering follow-up queries)
                    if hasattr(prev_cached_state, '_previous_university_ids') and prev_cached_state._previous_university_ids:
                        state._previous_university_ids = prev_cached_state._previous_university_ids
                        print(f"DEBUG: Preserved _previous_university_ids: {state._previous_university_ids}")
                    if hasattr(prev_cached_state, '_previous_intake_ids') and prev_cached_state._previous_intake_ids:
                        state._previous_intake_ids = prev_cached_state._previous_intake_ids
                        print(f"DEBUG: Preserved _previous_intake_ids: {state._previous_intake_ids}")
            
            # CRITICAL: Even if cache is empty, try to preserve context from conversation history for list queries
            # This ensures context is preserved even if cache fails
            if is_list_query and (not cached or not cached.get("state")):
                print(f"DEBUG: List query detected but cache is empty - trying to extract context from conversation history")
                # Try to extract context from previous messages in conversation history
                if conversation_history and len(conversation_history) > 2:
                    # Look for previous user messages that might contain context (last 16 messages)
                    history_window = conversation_history[-16:] if len(conversation_history) > 16 else conversation_history
                    prev_messages = [msg for msg in history_window if msg.get('role') == 'user']
                    for prev_msg in reversed(prev_messages):
                        prev_text = prev_msg.get('content', '').lower()
                        # Try to extract key fields from previous messages
                        if not state.university_query:
                            # Look for university mentions (HIT, HUST, etc.)
                            if 'hit' in prev_text or 'harbin' in prev_text:
                                state.university_query = "Harbin Institute of Technology"
                            elif 'hust' in prev_text or 'huazhong' in prev_text:
                                state.university_query = "Huazhong University of Science and Technology"
                            elif 'lnpu' in prev_text:
                                state.university_query = "Liaoning Normal University"
                        if not state.degree_level:
                            if 'bachelor' in prev_text or 'bba' in prev_text or 'bsc' in prev_text:
                                state.degree_level = "Bachelor"
                            elif 'master' in prev_text or 'mba' in prev_text or 'msc' in prev_text:
                                state.degree_level = "Master"
                            elif 'phd' in prev_text or 'doctorate' in prev_text:
                                state.degree_level = "PhD"
                        if not state.intake_term:
                            if 'september' in prev_text or 'sept' in prev_text:
                                state.intake_term = "September"
                            elif 'march' in prev_text:
                                state.intake_term = "March"
                        if not state.major_query:
                            # Look for major mentions
                            if 'bba' in prev_text or 'business' in prev_text:
                                state.major_query = "business administration"
                            elif 'cse' in prev_text or 'computer' in prev_text:
                                state.major_query = "computer science"
                        # CRITICAL: Always preserve degree_level from previous cached state if found in conversation history
                        # Only check prev_cached_state if it's defined (it's set earlier in the function)
                        if partner_id and conversation_id:
                            cached = self._get_cached_state(partner_id, conversation_id)
                            if cached and cached.get("state"):
                                prev_cached_state = cached.get("state")
                                if state.degree_level and hasattr(prev_cached_state, 'degree_level') and prev_cached_state.degree_level:
                                    # Keep the one from conversation history if it's more recent
                                    pass  # Already set from conversation history
                                elif not state.degree_level and hasattr(prev_cached_state, 'degree_level') and prev_cached_state.degree_level:
                                    state.degree_level = prev_cached_state.degree_level
                                    print(f"DEBUG: Preserved degree_level from cached state: {state.degree_level}")
                        # If we found any context, break
                        if state.university_query or state.degree_level or state.intake_term or state.major_query:
                            print(f"DEBUG: Extracted context from conversation history: university={state.university_query}, degree={state.degree_level}, intake={state.intake_term}, major={state.major_query}")
                            break
            
            # Detect specific requirement questions (e.g., "bank_statement", "age", "hsk", "english test")
            # CRITICAL: Skip requirement detection for SCHOLARSHIP intent - scholarship queries are NOT requirement queries
            latest_lower = latest_user_message.lower()
            
            # CRITICAL: If this is a SCHOLARSHIP intent, do NOT detect requirements
            # Scholarship queries should NOT trigger requirement detection
            if state.intent == self.router.INTENT_SCHOLARSHIP:
                print(f"DEBUG: Skipping requirement detection - this is a SCHOLARSHIP intent query, not a requirements query")
            else:
                # CRITICAL: Check if this is a CSC/CSCA scholarship query first
                # "requiring CSC/CSCA" means scholarship requirement, NOT document requirements
                is_csca_query = re.search(r'\b(requiring|require|requires|required)\s+(csca|csc|china\s+scholarship\s+council)\b', latest_lower)
                
                # CRITICAL: Check for age requirement using regex with word boundaries to avoid false positives from "language"
                # Don't match single word "age" - only match phrases like "minimum age", "age requirement", etc.
                if re.search(r'\b(minimum\s+age|age\s+requirement|age\s+limit|maximum\s+age|what\s+age|age\s+restriction)\b', latest_lower):
                    state.wants_requirements = True
                    if not hasattr(state, 'req_focus') or not state.req_focus:
                        from app.services.slot_schema import RequirementFocus
                        state.req_focus = RequirementFocus()
                    state.req_focus.age = True
                    print(f"DEBUG: Detected requirement question: age, set wants_requirements=True")
                else:
                    # Check other requirement keywords (not age)
                    req_keywords = {
                        "bank": ["bank statement", "bank_statement", "financial proof", "financial", "funds"],
                        "hsk": ["hsk", "chinese test", "chinese language test"],
                        "english": ["english test", "ielts", "toefl", "english requirement"],
                        "documents": ["document", "documents", "required documents", "application documents"],
                        "accommodation": ["accommodation", "housing", "dormitory", "dorm"]
                    }
                    for req_type, keywords in req_keywords.items():
                        if any(kw in latest_lower for kw in keywords):
                            # Skip if this is a CSC/CSCA scholarship query - "requiring CSC/CSCA" is about scholarship, not documents
                            if is_csca_query and req_type in ["documents", "requirements"]:
                                print(f"DEBUG: Skipping requirements detection - this is a CSC/CSCA scholarship query, not a document requirements query")
                                continue
                            state.wants_requirements = True
                            # Update req_focus if not already set
                            if not hasattr(state, 'req_focus') or not state.req_focus:
                                from app.services.slot_schema import RequirementFocus
                                state.req_focus = RequirementFocus()
                            # Set specific focus based on keyword
                            if req_type == "bank":
                                state.req_focus.bank = True
                            elif req_type == "age":
                                state.req_focus.age = True
                            elif req_type == "hsk":
                                state.req_focus.exams = True
                            elif req_type == "english":
                                state.req_focus.exams = True
                            elif req_type == "documents":
                                state.req_focus.docs = True
                            elif req_type == "accommodation":
                                state.req_focus.accommodation = True
                            print(f"DEBUG: Detected requirement question: {req_type}, set wants_requirements=True")
                            break  # Only detect one requirement type per query
                    # For FEES intent: Keep FEES intent but allow showing specific requirement fields (bank_statement, age, etc.)
                    # Don't change to REQUIREMENTS intent - FEES should focus on fees, not full document lists
                    if state.intent == self.router.INTENT_FEES:
                        # Keep FEES intent - bank statement, age, accommodation are fee-related info
                        print(f"DEBUG: FEES intent - will show fee-related requirements (bank_statement, age, accommodation) but not full document lists")
            
            # INTENT LOCKING: If previous intent was SCHOLARSHIP/FEES/REQUIREMENTS and message is short, keep intent
            if prev_state and prev_state.intent in [self.router.INTENT_SCHOLARSHIP, self.router.INTENT_FEES, self.router.INTENT_ADMISSION_REQUIREMENTS]:
                message_tokens = len(latest_user_message.split())
                is_slot_reply = (
                    message_tokens < 4 or
                    re.search(r'\b(english|chinese|march|september|bachelor|master|phd|language)\b', latest_user_message.lower()) or
                    re.match(r'^\d+$', latest_user_message.strip())  # Number reply for disambiguation
                )
                if is_slot_reply and not is_intent_change:
                    # Lock intent from previous state
                    state.intent = prev_state.intent
                    state.wants_scholarship = prev_state.wants_scholarship
                    state.wants_fees = prev_state.wants_fees
                    state.wants_requirements = prev_state.wants_requirements
                    print(f"DEBUG: Intent locked to {state.intent} (short message/slot reply)")
            
            # CRITICAL: Check for explicit university changes in short messages like "What about LNPU?"
            # This must happen BEFORE context preservation to ensure new university is used
            # Always check for university patterns in short messages (likely university switches)
            is_short_message = len(latest_user_message.split()) <= 5
            has_university_pattern = False
            if is_short_message:
                # Check for patterns like "what about X", "how about X", "X university", etc.
                # IMPORTANT: Match full university names including "University", "Institute", "College"
                uni_patterns = [
                    r'\b(what|how)\s+about\s+([A-Z][A-Z0-9]+|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*(?:\s+(?:University|Institute|College))?)\b',
                    r'\b([A-Z][A-Z0-9]+|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(university|institute|college)\b',
                    r'^([A-Z][A-Z0-9]+|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s*[?]?$',  # Just "LNPU?" or "HIT?"
                ]
                for pattern in uni_patterns:
                    match = re.search(pattern, latest_user_message, re.IGNORECASE)
                    if match:
                        potential_uni = match.group(2) if match.lastindex >= 2 else match.group(1)
                        # Try to resolve it
                        matched, uni_dict, _ = self._fuzzy_match_university(potential_uni)
                        if matched and uni_dict:
                            state.university_query = uni_dict.get("name")
                            state._resolved_university_id = uni_dict.get("id")
                            has_university_pattern = True
                            # CRITICAL: Clear city/province if university was detected (university takes priority)
                            if state.city or state.province:
                                print(f"DEBUG: Clearing city/province ({state.city}/{state.province}) because university was detected: {state.university_query}")
                                state.city = None
                                state.province = None
                            print(f"DEBUG: Detected explicit university change to '{state.university_query}' from message: '{latest_user_message}'")
                            break
            
            # DETERMINISTIC RESOLUTION: Resolve university_raw and major_raw to IDs
            # Only resolve if we didn't already resolve it from pattern matching above
            if state.university_query and not has_university_pattern:
                matched, uni_dict, _ = self._fuzzy_match_university(state.university_query)
                if matched and uni_dict:
                    # Store resolved university_id (will be used in build_sql_params)
                    state.university_query = uni_dict.get("name")  # Keep name for reference
                    state._resolved_university_id = uni_dict.get("id")  # Store ID for SQL
                    # CRITICAL: Clear city/province if university was detected (university takes priority)
                    if state.city or state.province:
                        print(f"DEBUG: Clearing city/province ({state.city}/{state.province}) because university was detected: {state.university_query}")
                        state.city = None
                        state.province = None
                    print(f"DEBUG: Resolved university_query '{state.university_query}' to university_id: {state._resolved_university_id}")
                else:
                    print(f"DEBUG: Could not resolve university_query '{state.university_query}' to a university_id")
            
            # Special handling: if degree_level is "Language" and major_query is None or generic,
            # treat "language program" as the major_query
            if state.degree_level == "Language" and (not state.major_query or state.major_query.lower() in ["language", "language program", "language course"]):
                # Set major_query to "language program" for proper matching
                if not state.major_query:
                    state.major_query = "language program"
                    print(f"DEBUG: Set major_query='language program' for Language degree_level")
            
            # Only resolve major_ids if they haven't been resolved yet (e.g., from pending_slot handling)
            if state.major_query and (not hasattr(state, '_resolved_major_ids') or not state._resolved_major_ids):
                # Resolve major_query to major_ids
                # CRITICAL: If university_id is known, pass it to filter majors to only those at that university
                # This prevents matching majors from other universities
                university_id = None
                if hasattr(state, '_resolved_university_id') and state._resolved_university_id:
                    university_id = state._resolved_university_id
                elif state.university_query:
                    # Try to resolve university_id if not already resolved
                    university_id = self.resolve_university_id(state.university_query)
                    if university_id:
                        state._resolved_university_id = university_id
                        print(f"DEBUG: Resolved university_query '{state.university_query}' to university_id: {university_id}")
                
                # For language programs, use much higher limit (100) to get all language programs
                # Don't limit language programs - we need all of them to check durations and show options
                limit = 100 if state.degree_level == "Language" else 6
                major_ids = self.resolve_major_ids(
                    major_query=state.major_query,
                    degree_level=state.degree_level,
                    teaching_language=state.teaching_language,
                    university_id=university_id,  # CRITICAL: Filter by university if known
                    limit=limit,
                    confidence_threshold=0.78
                )
                if major_ids:
                    state._resolved_major_ids = major_ids  # Store IDs for SQL
                    # Keep major_query as text for reference
                else:
                    # No high-confidence match - will trigger clarification or major disambiguation
                    print(f"DEBUG: Major '{state.major_query}' has no high-confidence matches")
                    # Don't expand - let clarification handle it
                    
        except Exception as e:
            import traceback
            print(f"ERROR: llm_extract_state failed: {e}")
            traceback.print_exc()
            # Return default state on error, but preserve extracted fields if available
            state = PartnerQueryState()
            # Try to preserve what was extracted before the error
            if 'extracted' in locals() and extracted:
                state.intent = extracted.get("intent", "GENERAL")
                state.major_query = extracted.get("major_raw")
                state.university_query = extracted.get("university_raw")
                state.intake_term = extracted.get("intake_term")
                state.degree_level = extracted.get("degree_level")
                state.wants_scholarship = extracted.get("wants_scholarship", False)
                state.wants_earliest = extracted.get("wants_earliest", False)
                print(f"DEBUG: Preserved extracted fields: major_query={state.major_query}, intake_term={state.intake_term}, degree_level={state.degree_level}")
            
            # CRITICAL: Also try to preserve from cached state if available (for follow-up questions)
            if partner_id and conversation_id:
                cached = self._get_cached_state(partner_id, conversation_id)
                if cached and cached.get("state"):
                    prev_cached_state = cached.get("state")
                    # Preserve degree_level if not already set
                    if not state.degree_level and hasattr(prev_cached_state, 'degree_level') and prev_cached_state.degree_level:
                        state.degree_level = prev_cached_state.degree_level
                        print(f"DEBUG: Preserved degree_level from cached state after error: {state.degree_level}")
                    # Preserve major_query if not already set
                    if not state.major_query and hasattr(prev_cached_state, 'major_query') and prev_cached_state.major_query:
                        state.major_query = prev_cached_state.major_query
                        print(f"DEBUG: Preserved major_query from cached state after error: {state.major_query}")
                    # Preserve intake_term if not already set
                    if not state.intake_term and hasattr(prev_cached_state, 'intake_term') and prev_cached_state.intake_term:
                        state.intake_term = prev_cached_state.intake_term
                        print(f"DEBUG: Preserved intake_term from cached state after error: {state.intake_term}")
                    # Preserve resolved major IDs
                    if hasattr(prev_cached_state, '_resolved_major_ids') and prev_cached_state._resolved_major_ids:
                        state._resolved_major_ids = prev_cached_state._resolved_major_ids
                        print(f"DEBUG: Preserved _resolved_major_ids from cached state after error: {state._resolved_major_ids}")
                    # Preserve university
                    if not state.university_query and hasattr(prev_cached_state, 'university_query') and prev_cached_state.university_query:
                        state.university_query = prev_cached_state.university_query
                        if hasattr(prev_cached_state, '_resolved_university_id') and prev_cached_state._resolved_university_id:
                            state._resolved_university_id = prev_cached_state._resolved_university_id
                        print(f"DEBUG: Preserved university_query from cached state after error: {state.university_query}")
        
        # Handle earliest intake: infer intake_term from current month if wants_earliest=True
        if state.wants_earliest and not state.intake_term:
            state.intake_term = self._infer_earliest_intake_term()
        
        # Set university alias for backward compatibility
        if state.university_query and not state.university:
            state.university = state.university_query
        
        # Cache the final state for persistence across turns (use unified cache)
        self._set_cached_state(partner_id, conversation_id, state, None)
        
        return state
        
    def route_and_clarify(self, conversation_history: List[Dict[str, str]], prev_state: Optional[PartnerQueryState] = None,
                         partner_id: Optional[int] = None, conversation_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Stage A: Route + Clarify - NO big DB loads, no LLM long prompts.
        Returns RoutePlan dict with intent, needs_clarification, sql_plan, req_focus.
        """
        # CRITICAL FIX: Pass partner_id and conversation_id to extract_partner_query_state
        try:
            print(f"DEBUG: Extracting partner query state...")
            state = self.extract_partner_query_state(
                conversation_history,
                prev_state=prev_state,
                partner_id=partner_id,
                conversation_id=conversation_id
            )
            print(f"DEBUG: Extracted state: intent={state.intent}, degree_level={state.degree_level}, major_query={state.major_query}")
        except Exception as e:
            import traceback
            print(f"ERROR: extract_partner_query_state failed: {e}")
            traceback.print_exc()
            state = PartnerQueryState()
        
        # CRITICAL: Apply semantic stoplist BEFORE determine_missing_fields
        # This prevents "scholarship", "info", "fees" from being treated as major_query
        if state and state.major_query:
            # CRITICAL: Don't clear major_query if we have resolved major IDs from context
            # This preserves major_query that was established in previous conversation turns
            has_resolved_major_ids = hasattr(state, '_resolved_major_ids') and state._resolved_major_ids
            
            # Normalize dots in acronyms (e.g., "B.B.A." -> "BBA")
            original_major = state.major_query
            normalized_major = state.major_query.replace('.', '').strip().upper()
            if normalized_major != original_major.replace('.', '').strip().upper():
                state.major_query = normalized_major
                print(f"DEBUG: Normalized major_query from '{original_major}' to '{normalized_major}'")
            
            # Check if it's a known major acronym BEFORE checking for degree words
            # Known major acronyms: BBA, MBA, LLB, CSE, CS, CE, ECE, EEE, etc.
            expanded_major = self._expand_major_acronym(state.major_query)
            is_known_acronym = expanded_major.lower() != state.major_query.lower()
            
            # Check if it's a language program major (these should NOT be cleared even if they match degree level)
            is_language_major = any(kw in state.major_query.lower() for kw in [
                "chinese language", "language program", "language course", "foundation", "preparatory"
            ])
            
            if self._is_semantic_stopword(state.major_query):
                print(f"DEBUG: Clearing major_query '{state.major_query}' - semantic stopword")
                state.major_query = None
            # Check if it's a degree word, BUT skip this check if:
            # 1. It's a known major acronym
            # 2. We have resolved major IDs from context (preserve context)
            # 3. It's a language program major (e.g., "chinese language" is valid even though it contains "language")
            elif not is_known_acronym and not has_resolved_major_ids and not is_language_major:
                # Only check for degree words if it's NOT a known acronym, NOT from context, and NOT a language major
                matched_degree = self.router._fuzzy_match_degree_level(state.major_query)
                if matched_degree:
                    print(f"DEBUG: Clearing major_query '{state.major_query}' - it's a degree word ({matched_degree})")
                    if not state.degree_level:
                        state.degree_level = matched_degree
                    state.major_query = None
            elif is_known_acronym:
                # It's a known acronym - use the expanded version for better matching
                print(f"DEBUG: Major query '{state.major_query}' is a known acronym, expanded to '{expanded_major}'")
                state.major_query = expanded_major
            elif has_resolved_major_ids:
                print(f"DEBUG: Preserving major_query '{state.major_query}' - has resolved major IDs from context")
            elif is_language_major:
                print(f"DEBUG: Preserving major_query '{state.major_query}' - it's a language program major")
        
        # Check if clarification is needed using determine_missing_fields
        try:
            print(f"DEBUG: Determining missing fields for intent={state.intent}...")
            missing_slots, clarifying_question = self.determine_missing_fields(state.intent, state, date.today())
            needs_clar = len(missing_slots) > 0
            print(f"DEBUG: Missing slots: {missing_slots}, needs_clar: {needs_clar}")
        except Exception as e:
            import traceback
            print(f"ERROR: determine_missing_fields failed: {e}")
            traceback.print_exc()
            needs_clar = False
            clarifying_question = None
        
        # CRITICAL: For language/foundation programs, check for multiple durations and ask for clarification
        # BUT: For LIST_UNIVERSITIES with city filter, first check how many universities match
        # Only ask for duration if we have >= 3 universities (otherwise it's not necessary)
        if state and state.degree_level == "Language" and not state.duration_years_target and not needs_clar:
            # For LIST_UNIVERSITIES with city/province filter, first check university count
            should_check_duration = True
            if state.intent in [self.router.INTENT_LIST_UNIVERSITIES, self.router.INTENT_LIST_PROGRAMS] and (state.city or state.province):
                # Filter universities by location first
                location_universities = self.db_service.search_universities(
                    city=state.city,
                    province=state.province,
                    is_partner=True,
                    limit=100
                )
                location_uni_ids = [uni.id for uni in location_universities] if location_universities else []
                print(f"DEBUG: City/province filter - found {len(location_uni_ids)} universities in {state.city or state.province}")
                
                # If < 3 universities, skip duration clarification (not necessary)
                if len(location_uni_ids) < 3:
                    print(f"DEBUG: Only {len(location_uni_ids)} universities found - skipping duration clarification")
                    should_check_duration = False
                else:
                    # Filter majors to only those from universities in this location
                    print(f"DEBUG: {len(location_uni_ids)} universities found - will check durations for majors in these universities")
            
            # CRITICAL: For SCHOLARSHIP intent, skip duration check entirely
            # Duration check should only happen for LIST queries, and should use FULLY FILTERED results
            # For SCHOLARSHIP, we need to filter by scholarship first, then check durations if needed
            if should_check_duration and state.intent != self.router.INTENT_SCHOLARSHIP:
                # Check if we have resolved major_ids or need to resolve them
                major_ids_to_check = []
                if hasattr(state, '_resolved_major_ids') and state._resolved_major_ids:
                    major_ids_to_check = state._resolved_major_ids
                elif state.major_query:
                    # Resolve major_ids to check durations
                    major_ids_to_check = self.resolve_major_ids(
                        major_query=state.major_query,
                        degree_level=state.degree_level,
                        teaching_language=state.teaching_language,
                        limit=200,  # Get all language programs
                        confidence_threshold=0.70
                    )
                
                # If we have location filter, further filter majors to only those from location universities
                if major_ids_to_check and state.intent in [self.router.INTENT_LIST_UNIVERSITIES, self.router.INTENT_LIST_PROGRAMS] and (state.city or state.province):
                    # Get majors that belong to universities in the location
                    location_major_ids = []
                    for mid in major_ids_to_check:
                        major = self.db.query(Major).filter(Major.id == mid).first()
                        if major and major.university_id in location_uni_ids:
                            location_major_ids.append(mid)
                    major_ids_to_check = location_major_ids
                    print(f"DEBUG: Filtered majors to {len(major_ids_to_check)} majors from universities in {state.city or state.province}")
                
                if major_ids_to_check:
                    # First, query intakes to check how many unique universities we have
                    # Build filters for querying intakes (DO NOT include intake_term/intake_year for duration check)
                    # DO NOT include degree_level filter here - it's too restrictive and may not match correctly
                    # We'll rely on university_id filter and the fact that we're checking Language programs
                    temp_filters = {}
                    if state.university_query:
                        matched, uni_dict, _ = self._fuzzy_match_university(state.university_query)
                        if matched and uni_dict:
                            temp_filters["university_id"] = uni_dict.get("id")
                    # DO NOT add intake_term and intake_year here - we want to check all available durations
                    # DO NOT add degree_level here - it causes 0 results even with fuzzy matching
                    if state.teaching_language:
                        temp_filters["teaching_language"] = state.teaching_language
                    # DO NOT add major_ids filter - query all Language programs for this university
                    temp_filters["upcoming_only"] = True
                    
                    # Get intakes to check unique universities (without intake_term/intake_year and without major_ids)
                    temp_intakes, _ = self.db_service.find_program_intakes(
                        filters=temp_filters,
                        limit=200,
                        offset=0,
                        order_by="deadline"
                    )
                    
                    # Check number of unique universities in the results
                    unique_universities = set()
                    for intake in temp_intakes:
                        if hasattr(intake, 'university_id') and intake.university_id:
                            unique_universities.add(intake.university_id)
                    
                    print(f"DEBUG: Duration check - queried intakes without intake_term/intake_year, found {len(temp_intakes)} intakes from {len(unique_universities)} unique universities")
                    
                    # Get all majors and check for distinct durations
                    # Import Major locally to avoid UnboundLocalError (there are local imports later in this function)
                    from app.models import Major
                    distinct_durations = set()
                    duration_majors = {}  # Map duration -> list of major names
                    for mid in major_ids_to_check:
                        major = self.db.query(Major).filter(Major.id == mid).first()
                        if major and major.duration_years is not None:
                            duration = major.duration_years
                            distinct_durations.add(duration)
                            if duration not in duration_majors:
                                duration_majors[duration] = []
                            duration_majors[duration].append(major.name)
                    
                    # Only ask for duration if there are more than 6 unique universities AND multiple durations
                    # If <= 6 universities, just show the list without asking for duration
                    if len(distinct_durations) > 1 and len(unique_universities) > 6:
                        print(f"DEBUG: Language program has multiple durations: {sorted(distinct_durations)} and {len(unique_universities)} unique universities (>6), asking for duration clarification")
                        # Format duration options (convert years to readable format)
                        duration_options = []
                        for dur in sorted(distinct_durations):
                            if dur < 0.5:
                                duration_options.append(f"{int(dur * 12)} months")
                            elif dur < 1.0:
                                duration_options.append(f"{int(dur * 12)} months (half year)")
                            elif dur == 1.0:
                                duration_options.append("1 year")
                            else:
                                duration_options.append(f"{dur} years")
                        
                        state.pending_slot = "duration"
                        state.is_clarifying = True
                        clarifying_question = f"I found language programs with different durations. Which duration do you prefer: {', '.join(duration_options)}?"
                        print(f"DEBUG: Asking for duration clarification: {clarifying_question}")
                        
                        # CRITICAL: Store the intake IDs we found for duration selection (not objects to avoid detached session errors)
                        if temp_intakes:
                            # Store intake IDs in state for duration selection (not objects to avoid detached session errors)
                            state._duration_fallback_intake_ids = [intake.id for intake in temp_intakes]
                            state._duration_fallback_available = duration_options
                            print(f"DEBUG: Stored {len(temp_intakes)} intake IDs for duration clarification")
                        else:
                            print(f"DEBUG: WARNING - No intakes found for duration clarification")
                        
                        # Set pending slot with snapshot (this also caches the state with pending info)
                        self._set_pending(partner_id, conversation_id, "duration", state)
                        print(f"DEBUG: Set pending slot='duration' and cached state with {len(getattr(state, '_duration_fallback_intake_ids', []))} intake IDs")
                        
                        return {
                            "intent": state.intent,
                            "state": state,
                            "needs_clarification": True,
                            "clarifying_question": clarifying_question,
                            "sql_plan": None,
                            "req_focus": {}
                        }
                    else:
                        if len(unique_universities) <= 3:
                            print(f"DEBUG: Language program has {len(unique_universities)} unique universities (<=3), skipping duration clarification and showing list directly")
                        else:
                            print(f"DEBUG: Language program has only 1 duration, skipping duration clarification")
        
        # Check if major resolution is ambiguous (low confidence or too many candidates)
        if state.major_query and not hasattr(state, '_resolved_major_ids') and not needs_clar:
            # Try to resolve with lower threshold to see all candidates
            # CRITICAL: Pass university_id if it's known, to filter majors to only those at that university
            university_id = None
            if hasattr(state, '_resolved_university_id') and state._resolved_university_id:
                university_id = state._resolved_university_id
            major_ids = self.resolve_major_ids(
                major_query=state.major_query,
                degree_level=state.degree_level,
                teaching_language=state.teaching_language,
                university_id=university_id,  # Pass university_id to filter results
                limit=6,  # Get more candidates for disambiguation
                confidence_threshold=0.6  # Lower threshold to see all candidates
            )
            if len(major_ids) > 3:
                # Too many candidates - check if they're all the same major name
                # CRITICAL: Filter candidates by degree_level if it's established
                # This prevents showing Bachelor-level majors when user is asking about Master-level (e.g., MBA)
                candidates = []
                candidate_ids = []
                from app.models import Major
                for mid in major_ids[:6]:
                    major_obj = self.db.query(Major).filter(Major.id == mid).first()
                    if major_obj:
                        # If degree_level is established, only include majors matching that degree level
                        if state.degree_level:
                            major_degree = str(major_obj.degree_level or "").strip()
                            state_degree = str(state.degree_level).strip()
                            if major_degree.lower() != state_degree.lower():
                                # Skip majors that don't match the established degree level
                                print(f"DEBUG: Filtering out major_id={mid} ({major_obj.name}) - degree_level '{major_degree}' doesn't match established '{state_degree}'")
                                continue
                        major_name = major_obj.name
                        candidates.append(major_name)
                        candidate_ids.append(mid)
                
                # If filtering by degree_level removed all candidates, use all candidates (fallback)
                if not candidates and state.degree_level:
                    print(f"DEBUG: All candidates filtered out by degree_level={state.degree_level}, using all candidates as fallback")
                    for mid in major_ids[:6]:
                        major_obj = self.db.query(Major).filter(Major.id == mid).first()
                        if major_obj:
                            candidates.append(major_obj.name)
                            candidate_ids.append(mid)
                
                # Check if all candidates have the same name (case-insensitive)
                # Also check if they're semantically equivalent (e.g., "physics" and "Applied Physics" share the same core word)
                if candidates:
                    unique_names = set(name.lower().strip() for name in candidates)
                    if len(unique_names) == 1:
                        # All candidates are the same major - use them without asking
                        print(f"DEBUG: All {len(candidate_ids)} candidates have the same name '{candidates[0]}', using them without clarification")
                        state._resolved_major_ids = candidate_ids if candidate_ids else major_ids
                    else:
                        # Check if all candidates share the same core word(s) and are semantically equivalent
                        # e.g., "Applied Physics", "Applied Physics (Space Science)" all contain "physics"
                        # Extract core words from each candidate (remove parentheses, specializations)
                        import re
                        core_words_list = []
                        for name in candidates:
                            # Remove parenthetical content (e.g., "(Space Science)", "(Taught in French)")
                            core_name = re.sub(r'\([^)]*\)', '', name.lower().strip())
                            # Extract significant words (length >= 4 to avoid "and", "the", etc.)
                            words = [w for w in core_name.split() if len(w) >= 4]
                            core_words_list.append(set(words))
                        
                        # If all candidates share the same core word(s), treat them as equivalent
                        should_accept_all = False
                        if core_words_list:
                            common_core_words = set.intersection(*core_words_list) if len(core_words_list) > 1 else core_words_list[0]
                            # If there's at least one significant common word, and user query matches that word, accept all
                            if common_core_words and state.major_query:
                                user_words = set(state.major_query.lower().split())
                                # Check if user query word(s) match the common core words
                                if user_words.intersection(common_core_words):
                                    should_accept_all = True
                                    print(f"DEBUG: All {len(candidate_ids)} candidates share core word(s) {common_core_words} matching user query '{state.major_query}', using all without clarification")
                        
                        if should_accept_all:
                            state._resolved_major_ids = candidate_ids if candidate_ids else major_ids
                            needs_clar = False
                            missing_slots = []
                        else:
                            # Different major names - ask user to pick
                            needs_clar = True
                            missing_slots = ["major_choice"]
                        # Remove duplicates while preserving order
                        seen = set()
                        unique_candidates = []
                        unique_candidate_ids_filtered = []
                        for i, c in enumerate(candidates):
                            c_lower = c.lower().strip()
                            if c_lower not in seen:
                                seen.add(c_lower)
                                unique_candidates.append(c)
                                if i < len(candidate_ids):
                                    unique_candidate_ids_filtered.append(candidate_ids[i])
                                elif i < len(major_ids):
                                    unique_candidate_ids_filtered.append(major_ids[i])
                        clarifying_question = f"Which major did you mean? " + " ".join([f"{i+1}) {c}" for i, c in enumerate(unique_candidates)])
                        # Store candidates in state for later retrieval
                        state._major_candidates = unique_candidates
                        state._major_candidate_ids = unique_candidate_ids_filtered if unique_candidate_ids_filtered else major_ids[:6]
        
        # Set pending state if clarification needed
        if needs_clar and clarifying_question and missing_slots:
            state.pending_slot = missing_slots[0] if len(missing_slots) == 1 else "bundle"
            state.is_clarifying = True
            
            # Store in unified state cache with full snapshot
            slot = missing_slots[0]  # Ask for the first missing slot
            self._set_pending(partner_id, conversation_id, slot, state)
            
            # Also store in legacy caches for backward compatibility
            self._set_pending_slot(partner_id, conversation_id, slot, state.intent)
            conv_key = self._get_conv_key(partner_id, conversation_id, conversation_history)
            self._set_pending_state(conv_key, state.intent, missing_slots, state)
            
            # Cache the current state
            self._set_cached_state(partner_id, conversation_id, state, None)
        
        # Always cache the state (for context preservation in next query)
        # This ensures that even when clarification is not needed, the state is available for the next turn
        if not needs_clar:
            self._set_cached_state(partner_id, conversation_id, state, None)
            print(f"DEBUG: Cached state for next query (no clarification needed)")
        
        # Build SQL plan (only if no clarification needed)
        sql_plan = None
        if not needs_clar:
            sql_plan = self.build_sql_params(state)
            # CRITICAL: Validate SQL params - never query with only teaching_language or degree_level
            if sql_plan:
                has_substantive_filter = (
                    sql_plan.get("major_ids") or sql_plan.get("major_text") or
                    sql_plan.get("university_id") or sql_plan.get("university_ids") or
                    sql_plan.get("city") or sql_plan.get("province") or
                    sql_plan.get("intake_term") or state.wants_earliest
                )
                if not has_substantive_filter:
                    # Only teaching_language or degree_level - not enough, need clarification
                    print(f"DEBUG: SQL params lack substantive filter, requesting clarification")
                    needs_clar = True
                    # CRITICAL: If major_query is present (like "cse"), recognize it as a major and ask for degree_level
                    # Common major abbreviations: CSE, BBA, MBA, EEE, BA, MA, DR, PHD should be recognized as majors
                    if state.major_query and not state.degree_level:
                        # Major is present but degree_level is missing - ask for degree_level
                        missing_slots = ["degree_level"]
                        clarifying_question = "Please provide: degree level (Language/Bachelor/Master/PhD)"
                    elif not state.major_query and not state.university_query:
                        missing_slots = ["major_or_university"]
                        clarifying_question = "Please specify: major/subject, university name, or intake term (March/September)."
                    else:
                        missing_slots = ["intake_term"]
                        clarifying_question = "Please provide: intake term (March/September)."
                    sql_plan = None
        
        # Build req_focus dict
        req_focus = {
            "docs": state.req_focus.docs,
            "bank": state.req_focus.bank,
            "exams": state.req_focus.exams,
            "fees": state.wants_fees,
            "deadline": state.req_focus.deadline,
            "scholarship": state.wants_scholarship,
            "accommodation": state.req_focus.accommodation,
            "age": state.req_focus.age,
            "inside_china": state.req_focus.inside_china,
            "country": state.req_focus.country
        }
        
        return {
            "intent": state.intent,
            "needs_clarification": needs_clar,
            "clarifying_question": clarifying_question,
            "sql_plan": sql_plan,
            "req_focus": req_focus,
            "state": state  # Include full state for reference
        }
    
    def determine_missing_fields(self, intent: str, state: PartnerQueryState, today_date: date) -> Tuple[List[str], Optional[str]]:
        """
        Determine missing fields needed to run DB query for given intent.
        Returns (missing_slots: List[str], prompt_question: str | None)
        Rules: Ask at most ONE question per turn. Return minimum fields needed.
        """
        missing_slots = []
        question_parts = []
        
        if intent == self.router.INTENT_LIST_UNIVERSITIES or intent == self.router.INTENT_LIST_PROGRAMS:
            # CRITICAL: For LIST_UNIVERSITIES, user wants a LIST, so NEVER ask for university_query
            # For "which university" queries, we should provide a list, not ask for a university
            
            # Check if this is a "free tuition" query
            is_free_tuition_query = state.wants_fees and any(kw in str(state.wants_fees) for kw in ["free", "0", "zero"])
            
            # For Language programs: only need degree_level and intake_term (no major needed)
            # CRITICAL: Language programs are non-degree, so we know degree_level=Language and don't ask
            if state.degree_level and "Language" in str(state.degree_level):
                # Language program - only need intake_term (degree_level is already known)
                if not state.intake_term and not state.wants_earliest:
                    missing_slots.append("intake_term")
                    question_parts.append("intake term (March/September)")
            else:
                # Non-Language programs - need degree_level, intake_term, and optionally major_query
                # For LIST_PROGRAMS, still ask for degree_level (unless it's Language)
                has_filter = (
                    state.degree_level or state.major_query or state.city or state.province or
                    state.teaching_language or state.intake_term or state.wants_earliest
                )
                if not has_filter:
                    # Ask for the most useful filter first
                    if not state.degree_level:
                        missing_slots.append("degree_level")
                        question_parts.append("degree level (Language/Bachelor/Master/PhD)")
                    if not state.intake_term and not state.wants_earliest:
                        if not missing_slots:  # Only add if degree_level not missing
                            missing_slots.append("intake_term")
                            question_parts.append("intake term (March/September or earliest)")
                    if not missing_slots:
                        # For non-Language, major_query is optional but helpful
                        # Only ask if we have degree_level and intake_term but no major
                        if state.degree_level and state.intake_term and not state.major_query:
                            # We can still query without major_query, so don't make it required
                            pass
                elif not state.degree_level:
                    # If we have other filters but no degree_level, still ask for it (except for Language)
                    missing_slots.append("degree_level")
                    question_parts.append("degree level (Language/Bachelor/Master/PhD)")
        
        elif intent == self.router.INTENT_SCHOLARSHIP:
            # SCHOLARSHIP clarification rules (FIXED: no scholarship_bundle, default to any scholarship)
            # Default scholarship_focus.any = True (handled in state initialization)
            
            # CRITICAL: For scholarship queries, ask for intake_term FIRST before searching
            # This significantly reduces the number of results and ensures accurate scholarship filtering
            if not state.intake_term:
                missing_slots.append("intake_term")
                question_parts.append("intake term (March/September)")
            
            # Require degree_level + (major_query OR university_query OR city/province)
            if not state.degree_level:
                missing_slots.append("degree_level")
                question_parts.append("degree level (Language/Bachelor/Master/PhD)")
            
            # If degree_level present but no narrowing filter: ask for major/university/city
            if state.degree_level and not state.major_query and not state.university_query and not state.city and not state.province:
                if "degree_level" not in missing_slots and "intake_term" not in missing_slots:
                    missing_slots.append("major_or_university")
                    question_parts.append("major/subject, university name, or city")
            
            # teaching_language: will be asked AFTER DB check if both exist (handled separately)
        
        elif intent == self.router.INTENT_ADMISSION_REQUIREMENTS or intent == "REQUIREMENTS":
            # CRITICAL: For REQUIREMENTS queries, degree_level is required when we have university + major
            # This ensures we can filter programs correctly
            # Check if we have university + major but missing degree_level FIRST (before checking has_target)
            if state.university_query and state.major_query and not state.degree_level:
                # Have university and major, but missing degree_level - MUST ask for it
                missing_slots.append("degree_level")
                question_parts.append("degree level (Language/Bachelor/Master/PhD)")
                question = "Please provide: " + ", ".join(question_parts)
                return missing_slots, question
            
            # Need: either university_query OR (major_query + degree_level) OR program_intake from last list
            has_target = (
                state.university_query or
                (state.major_query and state.degree_level) or
                self.last_selected_program_intake_id
            )
            if not has_target:
                # Check what's missing
                if state.university_query and not state.major_query and not state.degree_level:
                    # Have university, but missing major and degree_level
                    missing_slots.append("major_and_degree")
                    question_parts.append("major/subject and degree level (Language/Bachelor/Master/PhD)")
                    question = "Please provide: " + ", ".join(question_parts)
                    return missing_slots, question
                else:
                    missing_slots.append("target")
                    return missing_slots, "Which program should I check? (university name OR major+degree+intake)"
        
        elif intent in [self.router.INTENT_FEES, self.router.INTENT_COMPARISON]:
            # CRITICAL: For fee comparison queries with "which universities" or city filter, 
            # treat as LIST_UNIVERSITIES (user wants a list, not a specific university)
            # Check if this is actually a list query:
            # 1. If wants_list is True (user asked "which university" or "list universities")
            # 2. OR if city/province is provided (location-based list query)
            is_list_query = (
                state.wants_list or 
                intent == self.router.INTENT_LIST_UNIVERSITIES or
                ((state.city or state.province) and 
                 state.major_query and 
                 state.degree_level and 
                 (state.intake_term or state.wants_earliest))
            )
            
            if is_list_query:
                # This is actually a list query - don't ask for university
                print(f"DEBUG: FEES intent detected as list query (wants_list={state.wants_list}, intent={intent}) - skipping university requirement")
                # For list queries, only need degree_level and intake_term (no university needed)
                if not state.degree_level:
                    missing_slots.append("degree_level")
                    question_parts.append("degree level (Language/Bachelor/Master/PhD)")
                if not state.intake_term and not state.wants_earliest:
                    missing_slots.append("intake_term")
                    question_parts.append("intake term (March/September)")
                if missing_slots:
                    question = "Please provide: " + ", ".join(question_parts)
                    return missing_slots, question
                return [], None
            
            # Need: specific program_intake OR (university_query + major_query + degree_level + intake_term OR wants_earliest)
            has_target = (
                self.last_selected_program_intake_id or
                (state.university_query and state.major_query and state.degree_level and (state.intake_term or state.wants_earliest))
            )
            if not has_target:
                missing_fields = []
                if not state.university_query:
                    missing_fields.append("university")
                if not state.major_query:
                    missing_fields.append("major")
                if not state.degree_level:
                    missing_fields.append("degree level (Language/Bachelor/Master/PhD)")
                if not state.intake_term and not state.wants_earliest:
                    missing_fields.append("intake term (March/September)")
                missing_slots.append("target")
                # Always show intake options when asking for intake_term
                question = f"Please provide: {', '.join(missing_fields)}"
                # Remove duplicate "(March/September)" if it appears twice
                if question.count("(March/September)") > 1:
                    question = question.replace("(March/September) (March/September)", "(March/September)")
                    question = question.replace("intake term (March/September) (March/September)", "intake term (March/September)")
                return missing_slots, question
        
        # For GENERAL intent with deadline questions: allow queries with university + major + intake_term, but require degree_level
        elif intent == "GENERAL" or intent == self.router.INTENT_GENERAL:
            # For deadline questions, we can work with less information, but still need degree_level when we have university + major + intake
            if hasattr(state, 'wants_deadline') and state.wants_deadline:
                # For deadline queries: need at least university OR (major + intake_term)
                has_minimum = (
                    state.university_query or
                    (state.major_query and (state.intake_term or state.wants_earliest))
                )
                if not has_minimum:
                    # Only ask for the most critical missing field
                    if not state.university_query and not state.major_query:
                        missing_slots.append("university_or_major")
                        question_parts.append("university name or major/subject")
                    elif state.major_query and not state.intake_term and not state.wants_earliest:
                        missing_slots.append("intake_term")
                        question_parts.append("intake term (March/September)")
                # If we have university + major + intake_term, we should ask for degree_level to narrow down results
                elif state.university_query and state.major_query and (state.intake_term or state.wants_earliest):
                    if not state.degree_level:
                        missing_slots.append("degree_level")
                        missing_slots.append("degree_level")
                        question_parts.append("degree level (Language/Bachelor/Master/PhD)")
                    else:
                        # For general queries, need at least one filter
                        has_filter = (
                            state.university_query or state.major_query or state.degree_level or
                            state.intake_term or state.wants_earliest or state.city or state.province
                        )
                        if not has_filter:
                            missing_slots.append("filter")
                            question_parts.append("major/subject, university name, or intake term (March/September)")
        
        if missing_slots:
            question = f"Which {question_parts[0]}?" if question_parts else None
            return missing_slots, question
        
        return [], None
    
    def build_sql_params(self, state: PartnerQueryState) -> Dict[str, Any]:
        """
        Build SQL parameters for DBQueryService based on state.
        Returns dict with query parameters.
        """
        sql_params = {
            "upcoming_only": True,  # Always default to upcoming
            "limit": 10
        }
        
        # University filter
        # First check if university_id was already resolved in extract_partner_query_state
        if hasattr(state, '_resolved_university_id') and state._resolved_university_id:
            sql_params["university_id"] = state._resolved_university_id
            print(f"DEBUG: build_sql_params - using pre-resolved university_id: {state._resolved_university_id}")
        elif state.university_query:
            # Try to find university ID via fuzzy match
            matched, uni_dict, _ = self._fuzzy_match_university(state.university_query)
            if matched and uni_dict:
                sql_params["university_id"] = uni_dict.get("id")
                print(f"DEBUG: build_sql_params - resolved university_id: {uni_dict.get('id')} for query: {state.university_query}")
            else:
                print(f"DEBUG: build_sql_params - could not resolve university_id for query: {state.university_query}")
        
        # Location filters (city/province)
        if state.city or state.province:
            # Use DBQueryService to find universities by location
            location_universities = self.db_service.search_universities(
                city=state.city,
                province=state.province,
                is_partner=True,
                limit=100  # Get all partner universities in this location
            )
            if location_universities:
                location_uni_ids = [uni.id for uni in location_universities]
                # If we already have university_id, intersect with location filter
                if sql_params.get("university_id"):
                    if sql_params["university_id"] in location_uni_ids:
                        # University matches location, keep it
                        pass
                    else:
                        # University doesn't match location, clear it
                        del sql_params["university_id"]
                        print(f"DEBUG: build_sql_params - university doesn't match location filter, clearing university_id")
                else:
                    # No specific university, use location filter
                    sql_params["university_ids"] = location_uni_ids
                    print(f"DEBUG: build_sql_params - filtering by location: city={state.city}, province={state.province}, found {len(location_uni_ids)} universities")
            else:
                print(f"DEBUG: build_sql_params - no universities found for location: city={state.city}, province={state.province}")
        
        # Major filter (use resolved IDs if available, else resolve now)
        if hasattr(state, '_resolved_major_ids') and state._resolved_major_ids:
            sql_params["major_ids"] = state._resolved_major_ids
            print(f"DEBUG: build_sql_params - using resolved major_ids: {state._resolved_major_ids}")
        elif state.major_query:
            major_ids = self.resolve_major_ids(
                major_query=state.major_query,
                degree_level=state.degree_level,
                teaching_language=state.teaching_language,
                university_id=sql_params.get("university_id")
            )
            if major_ids:
                sql_params["major_ids"] = major_ids
                print(f"DEBUG: build_sql_params - resolved major_ids: {major_ids}")
            else:
                # Fallback: use expanded query for DB ILIKE search
                expanded_major = self._expand_major_acronym(state.major_query)
                sql_params["major_text"] = expanded_major
                print(f"DEBUG: build_sql_params - no major_ids found, using major_text: {expanded_major}")
        
        # Special fallback for language programs: if no major_ids and degree_level is Language,
        # don't filter by major at all - just use degree_level
        if state.degree_level == "Language" and not sql_params.get("major_ids") and not sql_params.get("major_text"):
            print(f"DEBUG: build_sql_params - Language program with no major filter, querying all Language programs")
            # Don't add major filter - query will use degree_level only
        
        # Scholarship filter
        if state.wants_scholarship:
            sql_params["has_scholarship"] = True
            # If CSC scholarship specifically requested
            if state.scholarship_focus and state.scholarship_focus.csc:
                sql_params["scholarship_type"] = "CSC"  # May need DBQueryService support
        
        # Degree level
        if state.degree_level:
            sql_params["degree_level"] = state.degree_level
        
        # Teaching language
        if state.teaching_language:
            sql_params["teaching_language"] = state.teaching_language
        
        # Intake term - relax for deadline queries (similar to REQUIREMENTS intent)
        # For deadline queries, don't filter by intake_term to get more results
        if state.intake_term:
            # Check if this is a deadline query - if so, don't add intake_term filter
            is_deadline_query = hasattr(state, 'wants_deadline') and state.wants_deadline
            if not is_deadline_query:
                from app.models import IntakeTerm
                try:
                    sql_params["intake_term"] = IntakeTerm[state.intake_term.upper()]
                except KeyError:
                    pass
            else:
                print(f"DEBUG: build_sql_params - deadline query detected, skipping intake_term filter")
        
        # Intake year - relax for deadline queries
        if state.intake_year:
            is_deadline_query = hasattr(state, 'wants_deadline') and state.wants_deadline
            if not is_deadline_query:
                sql_params["intake_year"] = state.intake_year
            else:
                print(f"DEBUG: build_sql_params - deadline query detected, skipping intake_year filter")
        
        # Duration
        if state.duration_years_target is not None:
            sql_params["duration_years_target"] = state.duration_years_target
            # For language programs, use "approx" constraint to be more lenient
            # For other programs, use "exact" if not specified
            if state.degree_level == "Language":
                sql_params["duration_constraint"] = state.duration_constraint or "approx"
            else:
                sql_params["duration_constraint"] = state.duration_constraint or "exact"
        
        # Budget
        if state.budget_max:
            sql_params["budget_max"] = state.budget_max
        
        # For list queries, increase limit
        if state.wants_list:
            sql_params["limit"] = 24  # MAX_LIST_INTAKES
        
        return sql_params
    
    def run_db(self, route_plan: Dict[str, Any], latest_user_message: Optional[str] = None, conversation_history: Optional[List[Dict[str, str]]] = None) -> List[Any]:
        """
        Stage B: Run DB queries based on sql_plan.
        Returns list of results (ProgramIntake, University, or Major objects).
        """
        if not route_plan.get("sql_plan"):
            print(f"DEBUG: run_db() - no sql_plan, returning empty list")
            return []
        
        sql_params = route_plan["sql_plan"]
        intent = route_plan["intent"]
        state = route_plan.get("state")
        
        # CRITICAL: If we have duration-filtered intakes from previous query, use them instead of re-querying
        if state and hasattr(state, '_duration_filtered_intakes') and state._duration_filtered_intakes:
            print(f"DEBUG: run_db - Using {len(state._duration_filtered_intakes)} duration-filtered intakes from previous query (no re-query needed)")
            return state._duration_filtered_intakes
        
        print(f"DEBUG: run_db() - intent={intent}, sql_params keys={list(sql_params.keys())}")
        
        # For REQUIREMENTS intent: if user only mentioned university (not major), clear major filters
        # BUT: Preserve major if it was set from previous context (e.g., user replied "Bachelor" to clarification)
        # Check for both "REQUIREMENTS" and "ADMISSION_REQUIREMENTS" since LLM might extract either
        if (intent == self.router.INTENT_ADMISSION_REQUIREMENTS or intent == "REQUIREMENTS") and latest_user_message:
            message_lower = latest_user_message.lower()
            # Check if current message explicitly mentions a major/subject
            major_keywords = ["major", "subject", "program", "course", "degree in", "study", "computer science", "physics", "engineering"]
            mentions_major = any(kw in message_lower for kw in major_keywords)
            
            # Also check if state.major_query appears in the current message
            if state and state.major_query:
                major_lower = state.major_query.lower()
                if major_lower in message_lower:
                    mentions_major = True
            
            # Check if this is a slot reply (short message like "Bachelor", "Master", "English")
            # In this case, preserve major from previous context
            is_slot_reply = len(latest_user_message.split()) <= 2 and any(
                kw in message_lower for kw in ["bachelor", "master", "phd", "language", "march", "september", "english", "chinese"]
            )
            
            # Also check if major_ids were resolved from previous context (indicates major was set before)
            has_resolved_major_ids = state and hasattr(state, '_resolved_major_ids') and state._resolved_major_ids
            
            if not mentions_major and state and state.university_query and not is_slot_reply and not has_resolved_major_ids:
                # User only mentioned university in current message AND it's not a slot reply AND no major was resolved from context
                # Clear major filters to show all programs
                print(f"DEBUG: REQUIREMENTS query - user only mentioned university '{state.university_query}', clearing major filters")
                sql_params.pop("major_ids", None)
                sql_params.pop("major_text", None)
                if state:
                    state.major_query = None
                    if hasattr(state, '_resolved_major_ids'):
                        state._resolved_major_ids = None
            elif is_slot_reply or has_resolved_major_ids:
                # This is a slot reply or major was resolved from context - preserve it
                print(f"DEBUG: REQUIREMENTS query - preserving major from context (slot_reply={is_slot_reply}, has_resolved_major_ids={has_resolved_major_ids})")
        
        if intent == self.router.INTENT_LIST_UNIVERSITIES:
            # For LIST_UNIVERSITIES with filters (fees, scholarship, requirements, etc.), we need to get program intakes and group by university
            # This allows us to show filtered results sorted appropriately
            # Check if user wants fees, free tuition, scholarship, or other filters
            has_fee_filter = state and (state.wants_fees or (hasattr(state, 'wants_free_tuition') and state.wants_free_tuition))
            has_scholarship_filter = state and state.wants_scholarship
            has_requirement_filter = state and state.wants_requirements
            
            # Check for "no application fee" requirement in conversation (independent of wants_fees flag)
            has_no_application_fee_requirement = False
            application_fee_pattern = r'\b(no|zero|free|without)\s+application\s+fee\b'
            if latest_user_message and re.search(application_fee_pattern, latest_user_message.lower()):
                has_no_application_fee_requirement = True
            elif conversation_history:
                for msg in reversed(conversation_history):
                    if msg.get('role') == 'user':
                        content = msg.get('content', '').lower()
                        if re.search(application_fee_pattern, content):
                            has_no_application_fee_requirement = True
                            print(f"DEBUG: Detected 'no application fee' requirement from conversation history")
                            break
            
            if (has_fee_filter or has_scholarship_filter or has_requirement_filter or has_no_application_fee_requirement) and sql_params.get("major_ids"):
                # Get program intakes filtered by criteria, then group by university
                filters = {}
                # If we have prior university context, reuse it (consistency across turns)
                if state and hasattr(state, '_previous_university_ids') and state._previous_university_ids:
                    filters["university_ids"] = state._previous_university_ids
                if sql_params.get("university_ids"):
                    filters["university_ids"] = sql_params["university_ids"]
                    # CRITICAL: Filter major_ids to only include majors that belong to these universities
                    # This prevents the "0 matches" issue when major_ids are from other universities
                    university_ids = sql_params["university_ids"]
                    requested_major_ids = sql_params.get("major_ids", [])
                    
                    # Get all majors that belong to these universities
                    # CRITICAL: Import Major here if not already imported (to avoid UnboundLocalError)
                    from app.models import Major
                    majors_in_unis = self.db.query(Major).filter(
                        Major.university_id.in_(university_ids)
                    ).all()
                    major_ids_in_unis = [m.id for m in majors_in_unis]
                    
                    # Filter requested major_ids to only those in these universities
                    filtered_major_ids = [mid for mid in requested_major_ids if mid in major_ids_in_unis]
                    
                    if filtered_major_ids:
                        filters["major_ids"] = filtered_major_ids
                        print(f"DEBUG: LIST_UNIVERSITIES with fees - Filtered major_ids from {len(requested_major_ids)} to {len(filtered_major_ids)} (only majors in {len(university_ids)} universities)")
                    else:
                        # No overlap - use all majors from these universities instead
                        filters["major_ids"] = major_ids_in_unis
                        print(f"DEBUG: LIST_UNIVERSITIES with fees - No overlap! Using all {len(major_ids_in_unis)} majors from {len(university_ids)} universities instead of {len(requested_major_ids)} requested majors")
                elif sql_params.get("major_ids"):
                    filters["major_ids"] = sql_params["major_ids"]
                if sql_params.get("degree_level"):
                    filters["degree_level"] = sql_params["degree_level"]
                if sql_params.get("teaching_language"):
                    filters["teaching_language"] = sql_params["teaching_language"]
                if sql_params.get("intake_term"):
                    filters["intake_term"] = sql_params["intake_term"]
                if sql_params.get("intake_year"):
                    filters["intake_year"] = sql_params["intake_year"]
                if sql_params.get("duration_years_target") is not None:
                    filters["duration_years_target"] = sql_params["duration_years_target"]
                if sql_params.get("duration_constraint"):
                    filters["duration_constraint"] = sql_params["duration_constraint"]
                filters["upcoming_only"] = sql_params.get("upcoming_only", True)
                
                # CRITICAL: Add filters based on user requirements
                # Free tuition filter
                print(f"DEBUG: Checking wants_free_tuition - state={state is not None}, hasattr={hasattr(state, 'wants_free_tuition') if state else False}, value={getattr(state, 'wants_free_tuition', None) if state else None}")
                if state and hasattr(state, 'wants_free_tuition') and state.wants_free_tuition:
                    filters["free_tuition"] = True
                    print(f"DEBUG: LIST_UNIVERSITIES with free tuition - adding free_tuition filter")
                else:
                    print(f"DEBUG: LIST_UNIVERSITIES - NOT adding free_tuition filter (state.wants_free_tuition={getattr(state, 'wants_free_tuition', 'NOT_SET') if state else 'NO_STATE'})")
                
                # Scholarship filter
                if has_scholarship_filter:
                    filters["has_scholarship"] = True
                    print(f"DEBUG: LIST_UNIVERSITIES with scholarship - adding has_scholarship filter")
                    # Check if it's specifically CSCA scholarship
                    if state and hasattr(state, 'scholarship_focus') and state.scholarship_focus and state.scholarship_focus.csc:
                        filters["scholarship_type"] = "CSC"
                        print(f"DEBUG: LIST_UNIVERSITIES with CSCA scholarship - adding scholarship_type=CSC filter")
                    # CRITICAL: Add scholarship_types filter if provided (Type A, Type B, Type C)
                    if state and hasattr(state, '_scholarship_types') and state._scholarship_types:
                        filters["scholarship_types"] = state._scholarship_types
                        print(f"DEBUG: LIST_UNIVERSITIES - Adding scholarship_types filter: {state._scholarship_types}")
                    # If scholarship filter is on, return raw intakes to preserve scholarship details (no aggregation)
                    intakes, _ = self.db_service.find_program_intakes(
                        filters=filters,
                        limit=sql_params.get("limit", 200),
                        offset=0,
                        order_by="tuition"
                    )
                    print(f"DEBUG: LIST_UNIVERSITIES with scholarship - returning {len(intakes)} intakes (no aggregation) to keep scholarship details")
                    # Log which universities were found
                    unique_universities = {}
                    for intake in intakes:
                        if hasattr(intake, 'university') and intake.university:
                            uni_name = intake.university.name
                            if uni_name not in unique_universities:
                                unique_universities[uni_name] = {
                                    'id': intake.university.id,
                                    'scholarship_info': (intake.scholarship_info or "")[:200] if hasattr(intake, 'scholarship_info') else None
                                }
                    print(f"DEBUG: LIST_UNIVERSITIES - Found {len(unique_universities)} unique universities: {list(unique_universities.keys())}")
                    for uni_name, uni_info in unique_universities.items():
                        scholarship_preview = uni_info['scholarship_info'][:150] if uni_info['scholarship_info'] else "None"
                        print(f"DEBUG: LIST_UNIVERSITIES - University: {uni_name} (ID: {uni_info['id']}), scholarship_info preview: {scholarship_preview}...")
                    return intakes
                
                # Age requirement filter (e.g., "40 year old can study" means max_age >= 40)
                # Check latest_user_message first, then state if available
                if state and hasattr(state, 'req_focus') and state.req_focus and state.req_focus.age:
                    # Check latest_user_message for age mentions
                    if latest_user_message:
                        content = latest_user_message.lower()
                        age_match = re.search(r'\b(\d+)\s*(?:year|years?)\s*(?:old|age|aged)\b', content)
                        if age_match:
                            age_value = int(age_match.group(1))
                            filters["max_age"] = age_value  # Filter for max_age >= age_value
                            print(f"DEBUG: LIST_UNIVERSITIES with age requirement - adding max_age>={age_value} filter")
                
                # Bank statement filter (e.g., "less than 5000 USD" or "no bank statement")
                if state and hasattr(state, 'req_focus') and state.req_focus and state.req_focus.bank:
                    # Check latest_user_message for bank statement mentions
                    if latest_user_message:
                        content = latest_user_message.lower()
                        # Check for "no bank statement" or "doesn't require bank statement"
                        if re.search(r'\b(no|doesn\'?t|don\'?t|not)\s+(require|need|want)\s+bank\s+statement\b', content):
                            filters["bank_statement_required"] = False
                            print(f"DEBUG: LIST_UNIVERSITIES with no bank statement requirement - adding bank_statement_required=False filter")
                        # Check for "less than X USD" or "bank statement less than"
                        bank_amount_match = re.search(r'\b(less\s+than|under|below|maximum|max)\s+(\d+)\s*(?:usd|dollar|dollars?)\b', content)
                        if bank_amount_match:
                            max_amount = int(bank_amount_match.group(2))
                            # Convert USD to CNY if needed (approximate: 1 USD = 7 CNY)
                            max_amount_cny = max_amount * 7
                            filters["bank_statement_amount"] = max_amount_cny
                            print(f"DEBUG: LIST_UNIVERSITIES with bank statement amount - adding bank_statement_amount<={max_amount_cny} CNY filter")
                
                # Interview required filter
                if state and hasattr(state, 'req_focus') and state.req_focus:
                    if latest_user_message:
                        content = latest_user_message.lower()
                        if re.search(r'\b(no|doesn\'?t|don\'?t|not)\s+(require|need|want)\s+interview\b', content):
                            filters["interview_required"] = False
                            print(f"DEBUG: LIST_UNIVERSITIES with no interview requirement - adding interview_required=False filter")
                
                # HSK filter
                if state and hasattr(state, 'req_focus') and state.req_focus:
                    if latest_user_message:
                        content = latest_user_message.lower()
                        if re.search(r'\b(no|doesn\'?t|don\'?t|not)\s+(require|need|want)\s+hsk\b', content):
                            filters["hsk_required"] = False
                            print(f"DEBUG: LIST_UNIVERSITIES with no HSK requirement - adding hsk_required=False filter")
                        # Check for "hsk score below X" or "hsk below X"
                        hsk_score_match = re.search(r'\bhsk\s+(?:score\s+)?(?:below|under|less\s+than|maximum|max)\s+(\d+)\b', content)
                        if hsk_score_match:
                            max_score = int(hsk_score_match.group(1))
                            filters["hsk_min_score"] = max_score
                            print(f"DEBUG: LIST_UNIVERSITIES with HSK score requirement - adding hsk_min_score<={max_score} filter")
                
                # English test filter
                if state and hasattr(state, 'req_focus') and state.req_focus:
                    if latest_user_message:
                        content = latest_user_message.lower()
                        if re.search(r'\b(no|doesn\'?t|don\'?t|not)\s+(require|need|want)\s+(?:ielts|toefl|english\s+test)\b', content):
                            filters["english_test_required"] = False
                            print(f"DEBUG: LIST_UNIVERSITIES with no English test requirement - adding english_test_required=False filter")
                
                # Application fee filter
                # Use the flag we already detected above to avoid duplicate checking
                if has_no_application_fee_requirement:
                    filters["application_fee"] = False
                    print(f"DEBUG: LIST_UNIVERSITIES with no application fee - adding application_fee=False filter")
                
                # Accommodation fee filter (lowest)
                if state and hasattr(state, 'req_focus') and state.req_focus:
                    if latest_user_message:
                        content = latest_user_message.lower()
                        accommodation_match = re.search(r'\b(lowest|minimum|min)\s+accommodation\s+fee\b', content)
                        if accommodation_match:
                            # This will be handled by sorting, but we can add a max filter if needed
                            print(f"DEBUG: LIST_UNIVERSITIES with lowest accommodation fee - will sort by accommodation_fee")
                
                # Get intakes sorted by tuition (lowest first) or by deadline if no fee filter
                intakes, total_count = self.db_service.find_program_intakes(
                    filters=filters,
                    limit=200,  # Get more to group by university
                    offset=0,
                    order_by="tuition"  # Sort by tuition for fee comparison
                )
                
                print(f"DEBUG: LIST_UNIVERSITIES with fees - found {len(intakes)} intakes with all filters")
                
                # CRITICAL: Check if free_tuition filter is active
                has_free_tuition_filter = filters.get("free_tuition") is True
                
                # If 0 results with free_tuition filter, return early - don't try fallbacks
                if len(intakes) == 0 and has_free_tuition_filter:
                    print(f"DEBUG: LIST_UNIVERSITIES with free_tuition filter - 0 results found, returning empty (no fallback)")
                    return []
                
                # CRITICAL: Do NOT relax intake_term or intake_year filters - user explicitly requested these
                # If 0 results with the specified intake_term/intake_year, return empty (don't show wrong intake universities)
                if len(intakes) == 0:
                    print(f"DEBUG: LIST_UNIVERSITIES with fees - 0 results with specified filters, returning empty (not relaxing intake_term/intake_year)")
                    return []
                
                # Group by university and get lowest fee for each
                uni_fee_map = {}  # {university_id: {university_name, min_tuition, intake_info}}
                for intake in intakes:
                    uni_id = intake.university_id
                    if uni_id not in uni_fee_map:
                        uni_fee_map[uni_id] = {
                            "university_id": uni_id,
                            "university_name": intake.university.name if intake.university else "N/A",
                            "university_name_cn": intake.university.name_cn if intake.university else None,
                            "city": intake.university.city if intake.university else None,
                            "province": intake.university.province if intake.university else None,
                            "min_tuition_per_year": None,
                            "min_tuition_per_semester": None,
                            "currency": None,
                            "program_count": 0,
                            "sample_intake": None
                        }
                    
                    # Calculate effective tuition
                    tuition = intake.tuition_per_year if intake.tuition_per_year else (intake.tuition_per_semester * 2 if intake.tuition_per_semester else None)
                    current_min = uni_fee_map[uni_id]["min_tuition_per_year"] or (uni_fee_map[uni_id]["min_tuition_per_semester"] * 2 if uni_fee_map[uni_id]["min_tuition_per_semester"] else None)
                    
                    # Update if this is lower
                    if tuition and (current_min is None or tuition < current_min):
                        uni_fee_map[uni_id]["min_tuition_per_year"] = intake.tuition_per_year
                        uni_fee_map[uni_id]["min_tuition_per_semester"] = intake.tuition_per_semester
                        uni_fee_map[uni_id]["currency"] = intake.currency or "CNY"
                        uni_fee_map[uni_id]["sample_intake"] = {
                            "major_name": intake.major.name if intake.major else "N/A",
                            "intake_term": intake.intake_term.value if intake.intake_term else None,
                            "intake_year": intake.intake_year
                        }
                    
                    uni_fee_map[uni_id]["program_count"] += 1
                
                # Convert to list and sort by min_tuition
                results = list(uni_fee_map.values())
                results.sort(key=lambda x: (
                    x["min_tuition_per_year"] if x["min_tuition_per_year"] else (x["min_tuition_per_semester"] * 2 if x["min_tuition_per_semester"] else float('inf'))
                ))
                
                if results:
                    print(f"DEBUG: LIST_UNIVERSITIES with fees - returning {len(results)} universities sorted by fees")
                    return results[:sql_params.get("limit", 12)]
                else:
                    print(f"DEBUG: LIST_UNIVERSITIES with fees - no results after all fallbacks, returning empty list")
                    return []
            else:
                # Use list_universities_by_filters for non-fee queries
                # CRITICAL: Filter major_ids to only include majors that belong to universities in city/province
                major_ids_to_use = sql_params.get("major_ids")
                if major_ids_to_use and sql_params.get("university_ids"):
                    university_ids = sql_params["university_ids"]
                    # Get all majors that belong to these universities
                    # CRITICAL: Import Major here to avoid UnboundLocalError
                    from app.models import Major
                    majors_in_unis = self.db.query(Major).filter(
                        Major.university_id.in_(university_ids)
                    ).all()
                    major_ids_in_unis = [m.id for m in majors_in_unis]
                    
                    # Filter requested major_ids to only those in these universities
                    filtered_major_ids = [mid for mid in major_ids_to_use if mid in major_ids_in_unis]
                    
                    if filtered_major_ids:
                        major_ids_to_use = filtered_major_ids
                        print(f"DEBUG: LIST_UNIVERSITIES - Filtered major_ids from {len(sql_params.get('major_ids', []))} to {len(filtered_major_ids)} (only majors in {len(university_ids)} universities)")
                    else:
                        # No overlap - use all majors from these universities instead
                        major_ids_to_use = major_ids_in_unis
                        print(f"DEBUG: LIST_UNIVERSITIES - No overlap! Using all {len(major_ids_in_unis)} majors from {len(university_ids)} universities")
                
                results = self.db_service.list_universities_by_filters(
                    city=state.city if state else None,
                    province=state.province if state else None,
                    degree_level=sql_params.get("degree_level"),
                    teaching_language=sql_params.get("teaching_language"),
                    intake_term=sql_params.get("intake_term"),
                    intake_year=sql_params.get("intake_year"),
                    duration_years_target=sql_params.get("duration_years_target"),
                    duration_constraint=sql_params.get("duration_constraint"),
                    major_ids=major_ids_to_use,  # Use filtered major_ids
                    upcoming_only=sql_params.get("upcoming_only", True),
                    limit=sql_params.get("limit", 10)
                )
                
                # Enhance results with sample major names when major_ids are specified
                # This helps the LLM understand which majors are available at each university
                if major_ids_to_use and results:
                    # Import ProgramIntake locally to avoid UnboundLocalError (there are local imports later in this function)
                    from app.models import ProgramIntake
                    for result in results:
                        if "university" in result and result.get("program_count", 0) > 0:
                            uni_id = result["university"].id
                            # Get one sample intake for this university with the specified majors
                            sample_query = self.db.query(ProgramIntake).join(Major).filter(
                                ProgramIntake.university_id == uni_id,
                                Major.id.in_(major_ids_to_use)
                            )
                            if sql_params.get("intake_term"):
                                sample_query = sample_query.filter(ProgramIntake.intake_term == sql_params["intake_term"])
                            if sql_params.get("intake_year"):
                                sample_query = sample_query.filter(ProgramIntake.intake_year == sql_params["intake_year"])
                            sample_intake = sample_query.first()
                            
                            if sample_intake and sample_intake.major:
                                result["sample_intake"] = {
                                    "major_name": sample_intake.major.name,
                                    "intake_term": sample_intake.intake_term.value if hasattr(sample_intake.intake_term, 'value') else str(sample_intake.intake_term),
                                    "intake_year": sample_intake.intake_year
                                }
                
                # CRITICAL: Do NOT relax intake_term or intake_year filters - user explicitly requested these
                # If 0 results with the specified intake_term/intake_year, return empty (don't show wrong intake universities)
                if len(results) == 0:
                    print(f"DEBUG: LIST_UNIVERSITIES - 0 results with specified filters, returning empty (not relaxing intake_term/intake_year)")
                
                return results
        
        elif intent == self.router.INTENT_LIST_PROGRAMS:
            # CRITICAL: If intake_term or intake_year is specified, OR if university_id is specified,
            # query program_intakes instead of majors table to get program details (deadlines, requirements, fees, etc.)
            # This ensures users get full program information when asking about programs at a specific university
            if sql_params.get("intake_term") or sql_params.get("intake_year") or sql_params.get("university_id") or sql_params.get("university_ids"):
                print(f"DEBUG: LIST_PROGRAMS with intake_term/intake_year/university_id - querying program_intakes for detailed info")
                filters = {}
                if sql_params.get("university_id"):
                    filters["university_id"] = sql_params["university_id"]
                if sql_params.get("university_ids"):
                    filters["university_ids"] = sql_params["university_ids"]
                if sql_params.get("major_ids"):
                    filters["major_ids"] = sql_params["major_ids"]
                if sql_params.get("degree_level"):
                    filters["degree_level"] = sql_params["degree_level"]
                if sql_params.get("teaching_language"):
                    filters["teaching_language"] = sql_params["teaching_language"]
                if sql_params.get("intake_term"):
                    filters["intake_term"] = sql_params["intake_term"]
                if sql_params.get("intake_year"):
                    filters["intake_year"] = sql_params["intake_year"]
                
                # For LIST_PROGRAMS without intake_term, get upcoming intakes to show program details
                if not filters.get("intake_term") and not filters.get("intake_year"):
                    filters["upcoming_only"] = True
                
                intakes, _ = self.db_service.find_program_intakes(
                    filters=filters,
                    limit=sql_params.get("limit", 200),
                    offset=0,
                    order_by="deadline"
                )
                print(f"DEBUG: LIST_PROGRAMS with university/intake - found {len(intakes)} program intakes")
                return intakes
            else:
                # No intake or university specified - query majors table directly (shows all available majors)
                print(f"DEBUG: LIST_PROGRAMS intent - querying majors table directly (no university/intake specified)")
                majors_list = self._get_majors_for_list_query(
                    university_id=sql_params.get("university_id"),
                    university_ids=sql_params.get("university_ids"),
                    degree_level=sql_params.get("degree_level"),
                    teaching_language=sql_params.get("teaching_language")
                )
                print(f"DEBUG: LIST_PROGRAMS - found {len(majors_list)} majors")
                return majors_list
        
        elif intent in [self.router.INTENT_FEES, 
                        self.router.INTENT_ADMISSION_REQUIREMENTS, self.router.INTENT_SCHOLARSHIP,
                        self.router.INTENT_COMPARISON, self.router.INTENT_PROGRAM_DETAILS, "general", "GENERAL", "REQUIREMENTS"]:
            # Use efficient find_program_intakes method
            print(f"DEBUG: Entering find_program_intakes branch - intent={intent}")
            filters = {}
            if sql_params.get("university_id"):
                filters["university_id"] = sql_params["university_id"]
            if sql_params.get("university_ids"):
                filters["university_ids"] = sql_params["university_ids"]
            
            # For REQUIREMENTS intent: relax filters to show all programs at university
            # Only include major/teaching_language/intake filters if user explicitly mentioned them
            print(f"DEBUG: Checking intent - intent={intent}, INTENT_ADMISSION_REQUIREMENTS={self.router.INTENT_ADMISSION_REQUIREMENTS}, match={intent == self.router.INTENT_ADMISSION_REQUIREMENTS}")
            # Check for both "REQUIREMENTS" and "ADMISSION_REQUIREMENTS" since LLM might extract either
            if intent == self.router.INTENT_ADMISSION_REQUIREMENTS or intent == "REQUIREMENTS":
                print(f"DEBUG: REQUIREMENTS intent detected - relaxing filters")
                print(f"DEBUG: sql_params has intake_term: {sql_params.get('intake_term')}, intake_year: {sql_params.get('intake_year')}")
                
                # If user is asking about fees/requirements for previously shown programs, filter to only those universities
                # This preserves context when user changes intent (e.g., from SCHOLARSHIP to FEES/REQUIREMENTS)
                if state and hasattr(state, '_previous_university_ids') and state._previous_university_ids:
                    filters["university_ids"] = state._previous_university_ids
                    print(f"DEBUG: REQUIREMENTS - filtering to previous universities: {state._previous_university_ids}")
                elif state and hasattr(state, '_previous_intake_ids') and state._previous_intake_ids:
                    # If we have previous intake IDs, filter by them
                    # Extract university_ids from intake_ids
                    from app.models import ProgramIntake
                    prev_intakes = self.db.query(ProgramIntake).filter(ProgramIntake.id.in_(state._previous_intake_ids)).all()
                    if prev_intakes:
                        prev_uni_ids = list(set([i.university_id for i in prev_intakes if i.university_id]))
                        if prev_uni_ids:
                            filters["university_ids"] = prev_uni_ids
                            print(f"DEBUG: REQUIREMENTS - filtering to previous universities from intake_ids: {prev_uni_ids}")
                
                # For requirements queries, only filter by major if explicitly mentioned
                # If user only mentions university, show requirements for all programs
                if sql_params.get("major_ids"):
                    # CRITICAL: Check if these major_ids belong to the specified university
                    # If not, remove the major filter to show all programs at the university
                    if filters.get("university_id"):
                        from app.models import Major
                        # Check which of these majors belong to this university
                        majors_at_uni = self.db.query(Major).filter(
                            Major.id.in_(sql_params["major_ids"]),
                            Major.university_id == filters["university_id"]
                        ).all()
                        matching_major_ids = [m.id for m in majors_at_uni]
                        
                        if matching_major_ids:
                            # Some majors match - use only those
                            filters["major_ids"] = matching_major_ids
                            print(f"DEBUG: REQUIREMENTS - filtered major_ids to {len(matching_major_ids)} majors that belong to university_id={filters['university_id']}")
                        else:
                            # None of the majors belong to this university - remove major filter
                            # This allows showing all programs at the university
                            print(f"DEBUG: REQUIREMENTS - WARNING: None of the {len(sql_params['major_ids'])} major_ids belong to university_id={filters['university_id']}")
                            print(f"DEBUG: REQUIREMENTS - Removing major filter to show all programs at the university")
                            # Don't add major_ids to filters
                    else:
                        # No university filter - use all major_ids
                        filters["major_ids"] = sql_params["major_ids"]
                        print(f"DEBUG: REQUIREMENTS - added major_ids to filters: {len(filters['major_ids'])} majors")
                elif sql_params.get("major_text"):
                    filters["major_text"] = sql_params["major_text"]
                    print(f"DEBUG: REQUIREMENTS - added major_text to filters: {filters['major_text']}")
                
                # For deadline queries, we NEED intake_term to find the specific deadline
                # Don't relax intake_term/intake_year for deadline queries
                is_deadline_query = state and hasattr(state, 'wants_deadline') and state.wants_deadline
                if is_deadline_query:
                    # For deadline queries, include intake_term and intake_year if available
                    if sql_params.get("intake_term"):
                        filters["intake_term"] = sql_params["intake_term"]
                        print(f"DEBUG: Deadline query - adding intake_term to filters: {filters['intake_term']}")
                    if sql_params.get("intake_year"):
                        filters["intake_year"] = sql_params["intake_year"]
                        print(f"DEBUG: Deadline query - adding intake_year to filters: {filters['intake_year']}")
                else:
                    # For non-deadline REQUIREMENTS queries, don't filter by intake_term/intake_year (show all intakes)
                    # Don't filter by teaching_language for requirements queries (show all languages)
                    # Duration filter IS applied if specified (uses ProgramIntake.duration_years or Major.duration_years)
                    # This allows showing requirements for all programs at the university
                    # NOTE: intake_term and intake_year are NOT added to filters for REQUIREMENTS intent
                    print(f"DEBUG: REQUIREMENTS query - NOT adding intake_term/intake_year to filters (relaxing filters)")
                    print(f"DEBUG: REQUIREMENTS query - duration filter WILL be applied if specified")
            else:
                # For other intents, include all filters as normal
                if sql_params.get("major_ids"):
                    filters["major_ids"] = sql_params["major_ids"]
                if sql_params.get("major_text"):
                    filters["major_text"] = sql_params["major_text"]
                # CRITICAL: For SCHOLARSHIP intent, do NOT filter by teaching_language unless explicitly requested
                # teaching_language should not be applied automatically for scholarship queries
                # Only apply if it's explicitly mentioned as a requirement (e.g., "English-taught programs with scholarship")
                if sql_params.get("teaching_language") and intent != self.router.INTENT_SCHOLARSHIP:
                    filters["teaching_language"] = sql_params["teaching_language"]
                if sql_params.get("intake_term"):
                    filters["intake_term"] = sql_params["intake_term"]
                if sql_params.get("intake_year"):
                    filters["intake_year"] = sql_params["intake_year"]
            
            if sql_params.get("degree_level"):
                filters["degree_level"] = sql_params["degree_level"]
            
            # Include duration filter if specified (for all intents including REQUIREMENTS)
            # The duration filter should work correctly using ProgramIntake.duration_years or Major.duration_years
            if sql_params.get("duration_years_target") is not None:
                filters["duration_years_target"] = sql_params["duration_years_target"]
                filters["duration_constraint"] = sql_params.get("duration_constraint", "approx")
                print(f"DEBUG: Adding duration filter: target={sql_params['duration_years_target']}, constraint={sql_params.get('duration_constraint', 'approx')}")
            if sql_params.get("budget_max"):
                filters["budget_max"] = sql_params["budget_max"]
            if sql_params.get("city"):
                filters["city"] = sql_params["city"]
            if sql_params.get("province"):
                filters["province"] = sql_params["province"]
            
            # Scholarship filters - DO NOT include for REQUIREMENTS intent
            if intent != self.router.INTENT_ADMISSION_REQUIREMENTS:
                if state and state.wants_scholarship:
                    filters["has_scholarship"] = True
                    # Filter by specific scholarship types if provided (Type A, Type B, Type C, CSC)
                    print(f"DEBUG: run_db() - Checking scholarship_types: hasattr={hasattr(state, '_scholarship_types')}, value={getattr(state, '_scholarship_types', None)}")
                    if hasattr(state, '_scholarship_types') and state._scholarship_types:
                        # Store scholarship types for filtering in db_query_service
                        filters["scholarship_types"] = state._scholarship_types
                        print(f"DEBUG: Adding scholarship type filter: {state._scholarship_types}")
                    else:
                        print(f"DEBUG: run_db() - NOT adding scholarship_types filter (hasattr={hasattr(state, '_scholarship_types')}, value={getattr(state, '_scholarship_types', None)})")
                    if state.scholarship_focus.csc:
                        filters["scholarship_type"] = "csc"
                    elif state.scholarship_focus.university:
                        filters["scholarship_type"] = "university"
            
            # Requirements filters
            if state and state.wants_requirements:
                if state.req_focus.bank and state.budget_max:
                    filters["bank_statement_amount"] = state.budget_max
                if state.req_focus.exams:
                    # Check if HSK required was mentioned
                    pass  # Will be handled in query
            
            limit = sql_params.get("limit", 24)
            offset = sql_params.get("offset", 0)
            order_by = "deadline" if state and state.wants_earliest else "deadline"
            
            # Keep upcoming_only=True for REQUIREMENTS (don't change deadline filtering)
            
            print(f"DEBUG: run_db() - Calling find_program_intakes")
            print(f"DEBUG: run_db() - intent={intent}")
            print(f"DEBUG: run_db() - filters keys: {list(filters.keys())}")
            print(f"DEBUG: run_db() - has_duration={filters.get('duration_years_target') is not None}, duration_value={filters.get('duration_years_target')}")
            print(f"DEBUG: run_db() - has_intake_term={filters.get('intake_term') is not None}, intake_term_value={filters.get('intake_term')}")
            print(f"DEBUG: run_db() - has_intake_year={filters.get('intake_year') is not None}, intake_year_value={filters.get('intake_year')}")
            print(f"DEBUG: run_db() - major_ids count: {len(filters.get('major_ids', []))}")
            print(f"DEBUG: run_db() - degree_level: {filters.get('degree_level')}")
            intakes, total_count = self.db_service.find_program_intakes(
                filters=filters,
                limit=limit,
                offset=offset,
                order_by=order_by
            )
            print(f"DEBUG: run_db() - find_program_intakes returned {len(intakes)} intakes (type={type(intakes).__name__})")
            
            # Debug: Show what filters were actually applied
            if len(intakes) == 0:
                print(f"DEBUG: 0 results - checking why...")
                print(f"DEBUG: Filters applied: {list(filters.keys())}")
                print(f"DEBUG: major_ids count: {len(filters.get('major_ids', []))}")
                print(f"DEBUG: duration_years_target: {filters.get('duration_years_target')}")
                print(f"DEBUG: duration_constraint: {filters.get('duration_constraint')}")
                print(f"DEBUG: intake_term in filters: {filters.get('intake_term')}")
                print(f"DEBUG: intake_year in filters: {filters.get('intake_year')}")
                print(f"DEBUG: degree_level: {filters.get('degree_level')}")
            
            # ========== 0 RESULTS FALLBACK LOGIC ==========
            if len(intakes) == 0:
                print(f"DEBUG: 0 results found, applying fallback logic...")
                print(f"DEBUG: Fallback - state={state is not None}, state.degree_level={state.degree_level if state else None}, filters.duration_years_target={filters.get('duration_years_target')}, filters.keys()={list(filters.keys())}")
                
                # CRITICAL: For deadline queries, if intake_term is missing, ask for it instead of suggesting unrelated majors
                is_deadline_query = False
                if state:
                    is_deadline_query = hasattr(state, 'wants_deadline') and state.wants_deadline
                    print(f"DEBUG: Fallback check - state exists, hasattr wants_deadline={hasattr(state, 'wants_deadline')}, wants_deadline={getattr(state, 'wants_deadline', None)}")
                print(f"DEBUG: Fallback check - is_deadline_query={is_deadline_query}, filters.get('intake_term')={filters.get('intake_term')}, state.intake_term={state.intake_term if state else None}")
                if is_deadline_query and not filters.get("intake_term") and not (state and state.intake_term):
                    print(f"DEBUG: Deadline query with 0 results - missing intake_term, asking user for intake_term")
                    return {
                        "_fallback": True,
                        "_fallback_type": "missing_intake_term",
                        "_intakes": []
                    }
                else:
                    print(f"DEBUG: Fallback check - NOT returning missing_intake_term fallback (is_deadline_query={is_deadline_query}, has_intake_term_in_filters={filters.get('intake_term') is not None}, has_intake_term_in_state={state.intake_term if state else None})")
                
                fallback_filters = filters.copy()
                fallback_applied = False
                
                # Fallback 0: For language programs, if duration filter returns 0 results, try without duration filter
                if state and state.degree_level == "Language" and filters.get("duration_years_target") is not None:
                        print(f"DEBUG: Language program with duration filter returned 0 results, trying without duration filter...")
                        fallback_filters_no_duration = fallback_filters.copy()
                        fallback_filters_no_duration.pop("duration_years_target", None)
                        fallback_filters_no_duration.pop("duration_constraint", None)
                        fallback_intakes, _ = self.db_service.find_program_intakes(
                            filters=fallback_filters_no_duration, limit=limit, offset=offset, order_by=order_by
                        )
                        if len(fallback_intakes) > 0:
                            # Get distinct durations from results
                            distinct_durations = set()
                            for intake in fallback_intakes:
                                if hasattr(intake, 'major') and intake.major and intake.major.duration_years:
                                    distinct_durations.add(intake.major.duration_years)
                            
                            if distinct_durations:
                                # Format duration options
                                duration_options = []
                                for dur in sorted(distinct_durations):
                                    if dur < 0.5:
                                        duration_options.append(f"{int(dur * 12)} months")
                                    elif dur < 1.0:
                                        duration_options.append(f"{int(dur * 12)} months")
                                    elif dur == 1.0:
                                        duration_options.append("1 year")
                                    else:
                                        duration_options.append(f"{dur} years")
                                
                                print(f"DEBUG: Fallback - found {len(fallback_intakes)} intakes with durations: {sorted(distinct_durations)}")
                                print(f"DEBUG: Fallback - returning duration fallback with {len(duration_options)} duration options: {duration_options}")
                                return {
                                    "_fallback": True,
                                    "_fallback_type": "duration",
                                    "_available_durations": duration_options,
                                    "_intakes": fallback_intakes
                                }
                            else:
                                print(f"DEBUG: Fallback - found {len(fallback_intakes)} intakes but no distinct durations (all durations are None)")
                        else:
                            print(f"DEBUG: Fallback - removing duration filter still returned 0 results")
                else:
                    print(f"DEBUG: Fallback - duration fallback condition not met: state={state is not None}, degree_level={state.degree_level if state else None}, has_duration_filter={filters.get('duration_years_target') is not None}")
                
                # Fallback 0b: For language programs, if we have limited major_ids that don't match the university,
                # try querying all Language majors from that specific university instead of globally
                if state and state.degree_level == "Language" and filters.get("major_ids") and filters.get("university_id"):
                    university_id = filters["university_id"]
                    print(f"DEBUG: Language program with limited major_ids ({len(filters['major_ids'])}), trying all Language majors from university_id={university_id}...")
                    # Get all Language majors from this specific university
                    all_language_majors = self.db.query(Major).filter(
                        Major.university_id == university_id,
                        Major.degree_level.ilike("%Language%")
                    ).all()
                    if all_language_majors:
                        all_language_ids = [m.id for m in all_language_majors]
                        fallback_filters["major_ids"] = all_language_ids
                        # Keep degree_level filter to ensure we only get Language programs
                        fallback_intakes, _ = self.db_service.find_program_intakes(
                            filters=fallback_filters, limit=limit, offset=offset, order_by=order_by
                        )
                        if len(fallback_intakes) > 0:
                            print(f"DEBUG: Fallback - expanded to {len(all_language_ids)} Language majors from university_id={university_id}, found {len(fallback_intakes)} intakes")
                            return fallback_intakes
                    else:
                        # No Language majors found for this university - try without major_ids filter but KEEP degree_level filter
                        print(f"DEBUG: Fallback - no Language majors found for university_id={university_id}, trying without major_ids filter (keeping degree_level='Language')")
                        fallback_filters.pop("major_ids", None)
                        # CRITICAL: Keep degree_level filter to ensure we only get Language programs
                        # Don't remove degree_level - it's essential for language program queries
                        fallback_intakes, _ = self.db_service.find_program_intakes(
                            filters=fallback_filters, limit=limit, offset=offset, order_by=order_by
                        )
                        if len(fallback_intakes) > 0:
                            print(f"DEBUG: Fallback - query without major_ids filter (with degree_level='Language') returned {len(fallback_intakes)} intakes")
                            return fallback_intakes
                
                # Fallback 1: If major_query is acronym (cs/cse/ce), expand and retry
                if state and state.major_query and not filters.get("major_ids"):
                    major_lower = state.major_query.lower().strip()
                    if major_lower in ["cs", "cse", "ce", "eee", "it", "ai", "ds", "se"]:
                        # Expand acronym to multiple variations
                        expansions = {
                            "cs": ["computer science", "computer science and technology"],
                            "cse": ["computer science", "computer science and technology", "computer science engineering"],
                            "ce": ["computer engineering", "computer science engineering"],
                            "eee": ["electrical engineering", "electrical and electronic engineering"],
                            "it": ["information technology", "information science"],
                            "ai": ["artificial intelligence"],
                            "ds": ["data science"],
                            "se": ["software engineering"]
                        }
                        
                        expanded_names = expansions.get(major_lower, [])
                        for exp_name in expanded_names:
                            majors = self.db_service.search_majors(name=exp_name, degree_level=filters.get("degree_level"), limit=20)
                            if majors:
                                fallback_filters["major_ids"] = [m.id for m in majors]
                                fallback_applied = True
                                print(f"DEBUG: Expanded {major_lower} to {exp_name}, found {len(majors)} majors")
                                break  # Break from for loop when found
                
                # CRITICAL: Removed Fallback 2 - Do NOT relax intake_term or intake_year filters
                # If user specified intake_term/intake_year and got 0 results, return empty
                # Don't show universities with wrong intake terms
                
                # Fallback 3: If teaching_language causes 0, retry without it
                if not fallback_applied and filters.get("teaching_language"):
                    fallback_filters.pop("teaching_language", None)
                    fallback_intakes, _ = self.db_service.find_program_intakes(
                        filters=fallback_filters, limit=limit, offset=offset, order_by=order_by
                    )
                    if len(fallback_intakes) > 0:
                        # Check distinct languages
                        available_languages = set()
                        for intake in fallback_intakes:
                            if hasattr(intake, 'teaching_language') and intake.teaching_language:
                                available_languages.add(str(intake.teaching_language))
                            elif hasattr(intake, 'major') and intake.major and intake.major.teaching_language:
                                available_languages.add(str(intake.major.teaching_language))
                        return {
                            "_fallback": True,
                            "_fallback_type": "teaching_language",
                            "_available_languages": list(available_languages),
                            "_intakes": fallback_intakes
                        }
                
                # Fallback 4: For SCHOLARSHIP intent, if scholarship_types filter returns 0, try with just has_scholarship=True
                # CRITICAL: Do NOT remove has_scholarship filter - user explicitly asked for scholarships
                # Only relax scholarship_types if it's too restrictive
                if not fallback_applied and state and state.intent == self.router.INTENT_SCHOLARSHIP and filters.get("has_scholarship"):
                    if filters.get("scholarship_types"):
                        print(f"DEBUG: Fallback - SCHOLARSHIP intent with scholarship_types={filters.get('scholarship_types')} returned 0 results, trying with just has_scholarship=True (no type filter)")
                        fallback_filters_no_types = fallback_filters.copy()
                        fallback_filters_no_types.pop("scholarship_types", None)
                        # Keep has_scholarship=True - user explicitly asked for scholarships
                        fallback_intakes, _ = self.db_service.find_program_intakes(
                            filters=fallback_filters_no_types, limit=limit, offset=offset, order_by=order_by
                        )
                        if len(fallback_intakes) > 0:
                            print(f"DEBUG: Fallback - found {len(fallback_intakes)} intakes with scholarships (but not matching specific types)")
                            # Return these results but note that specific types weren't found
                            return {
                                "_fallback": True,
                                "_fallback_type": "scholarship_types",
                                "_requested_types": filters.get("scholarship_types"),
                                "_intakes": fallback_intakes
                            }
                    # If has_scholarship=True returns 0, that means NO programs have scholarships
                    # Do NOT remove has_scholarship filter - return empty with proper message
                    print(f"DEBUG: Fallback - has_scholarship=True returned 0 results - no programs have scholarships matching criteria")
                
                # If still 0, return empty but mark for fallback message
                return {
                    "_fallback": True,
                    "_fallback_type": "no_results",
                    "_intakes": []
                }
            
            return intakes
        
        # Default return if intent doesn't match
        return []
    
    def build_db_context(self, results: List[Any], req_focus: Dict[str, bool], list_mode: bool = False, intent: str = "general") -> str:
        """
        Build small DB_CONTEXT string from results.
        For single results, include notes. For lists, only summary fields.
        Intent-aware: For SCHOLARSHIP intent, focus on scholarship fields. For REQUIREMENTS intent, focus on requirement fields.
        """
        if not results:
            return "DATABASE CONTEXT: No matching programs found.\n"
        
        context_parts = ["DATABASE CONTEXT:\n"]
        
        # Check if single result or list
        is_single = len(results) == 1
        
        # Determine what to include based on intent
        is_scholarship_intent = intent in ["SCHOLARSHIP", "scholarship_only"]
        is_requirements_intent = intent in ["REQUIREMENTS", "ADMISSION_REQUIREMENTS", "documents_only", "eligibility_only"]
        is_fees_intent = intent in ["FEES", "fees_only"]
        is_list_universities_intent = intent == "LIST_UNIVERSITIES"
        
        for idx, result in enumerate(results[:10]):  # Limit to 10 for context
            # Check if this is a major dictionary (from _get_majors_for_list_query)
            if isinstance(result, dict) and "name" in result and "university_name" in result:
                # Major dictionary from LIST_PROGRAMS query
                context_parts.append(f"Major {idx+1}: {result.get('name')}")
                context_parts.append(f"  University: {result.get('university_name')}")
                if result.get('degree_level'):
                    context_parts.append(f"  Degree Level: {result.get('degree_level')}")
                context_parts.append("")
            
            elif isinstance(result, dict) and "university" in result:
                # From list_universities_by_filters
                uni = result["university"]
                context_parts.append(f"University {idx+1}: {uni.name}")
                if uni.city:
                    context_parts.append(f"  Location: {uni.city}, {uni.province or ''}, {uni.country or 'China'}")
                if uni.university_ranking:
                    context_parts.append(f"  Ranking: {uni.university_ranking}")
                context_parts.append(f"  Matching programs: {result.get('program_count', 0)}")
                
                # If sample_intake is provided (from fee queries), include major name
                if "sample_intake" in result and result["sample_intake"]:
                    sample = result["sample_intake"]
                    if sample.get("major_name"):
                        context_parts.append(f"  Major: {sample['major_name']}")
                        if sample.get("intake_term") and sample.get("intake_year"):
                            context_parts.append(f"  Intake: {sample['intake_term']} {sample['intake_year']}")
                
                context_parts.append("")
            
            elif hasattr(result, 'university') and hasattr(result, 'major'):
                # ProgramIntake object
                intake = result
                context_parts.append(f"Program {idx+1}:")
                context_parts.append(f"  University: {intake.university.name}")
                context_parts.append(f"  Major: {intake.major.name}")
                context_parts.append(f"  Degree: {intake.degree_type or intake.major.degree_level}")
                context_parts.append(f"  Intake: {intake.intake_term.value if hasattr(intake.intake_term, 'value') else intake.intake_term} {intake.intake_year}")
                
                if req_focus.get("deadline") and intake.application_deadline:
                    context_parts.append(f"  Deadline: {intake.application_deadline.strftime('%Y-%m-%d')}")
                
                # For SCHOLARSHIP intent: Focus ONLY on scholarship fields (optimize context size)
                if is_scholarship_intent:
                    # scholarship_available (Boolean, nullable)
                    if intake.scholarship_available is not None:
                        context_parts.append(f"  Scholarship Available: {'Yes' if intake.scholarship_available else 'No'}")
                    
                    # scholarship_info (Text)
                    if intake.scholarship_info:
                        context_parts.append(f"  Scholarship Info: {intake.scholarship_info}")
                    
                    # ProgramIntakeScholarship relationship - CRITICAL for filtering by Type A/B/C
                    scholarships = self.db_service.get_program_scholarships(intake.id)
                    if scholarships:
                        context_parts.append(f"  Structured Scholarships:")
                        for sch in scholarships:
                            sch_lines = [f"Name: {sch['scholarship']['name']}"]
                            if sch.get('covers_tuition') is not None:
                                sch_lines.append(f"Tuition: {'Covered' if sch['covers_tuition'] else 'Not covered'}")
                            if sch.get('covers_accommodation') is not None:
                                sch_lines.append(f"Accommodation: {'Covered' if sch['covers_accommodation'] else 'Not covered'}")
                            if sch.get('covers_insurance') is not None:
                                sch_lines.append(f"Insurance: {'Covered' if sch['covers_insurance'] else 'Not covered'}")
                            if sch.get('tuition_waiver_percent') is not None:
                                sch_lines.append(f"Tuition Waiver: {sch['tuition_waiver_percent']}%")
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
                                sch_lines.append(f"Eligibility: {sch['eligibility_note']}")
                            context_parts.append(f"    - {'; '.join(sch_lines)}")
                    
                    # Notes (may contain scholarship info) - only if relevant
                    if intake.notes and any(kw in intake.notes.lower() for kw in ['scholarship', 'csc', 'type a', 'type b', 'type c', 'waiver', 'stipend']):
                        context_parts.append(f"  Notes: {intake.notes}")
                    
                    # DO NOT include other fields (docs, exams, bank, age, etc.) for SCHOLARSHIP intent
                    # This reduces context size significantly
                
                # For REQUIREMENTS intent: Focus on requirement fields
                elif is_requirements_intent:
                    # Notes (important admission info)
                    if intake.notes:
                        context_parts.append(f"  Notes: {intake.notes}")
                    
                    # Age requirements
                    if intake.age_min is not None:
                        context_parts.append(f"  Age Minimum: {intake.age_min}")
                    if intake.age_max is not None:
                        context_parts.append(f"  Age Maximum: {intake.age_max}")
                    
                    # Academic requirements
                    if intake.min_average_score is not None:
                        context_parts.append(f"  Minimum Average Score: {intake.min_average_score}")
                    
                    # Test/Interview requirements
                    if intake.interview_required is not None:
                        context_parts.append(f"  Interview Required: {'Yes' if intake.interview_required else 'No'}")
                    if intake.written_test_required is not None:
                        context_parts.append(f"  Written Test Required: {'Yes' if intake.written_test_required else 'No'}")
                    if intake.acceptance_letter_required is not None:
                        context_parts.append(f"  Acceptance Letter Required: {'Yes' if intake.acceptance_letter_required else 'No'}")
                    
                    # Inside China applicants
                    if intake.inside_china_applicants_allowed is not None:
                        context_parts.append(f"  Inside China Applicants Allowed: {'Yes' if intake.inside_china_applicants_allowed else 'No'}")
                    if intake.inside_china_extra_requirements:
                        context_parts.append(f"  Inside China Extra Requirements: {intake.inside_china_extra_requirements}")
                    
                    # Bank statement requirements
                    if intake.bank_statement_required is not None:
                        context_parts.append(f"  Bank Statement Required: {'Yes' if intake.bank_statement_required else 'No'}")
                    if intake.bank_statement_amount is not None:
                        context_parts.append(f"  Bank Statement Amount: {intake.bank_statement_amount} {intake.bank_statement_currency or 'CNY'}")
                    if intake.bank_statement_note:
                        context_parts.append(f"  Bank Statement Note: {intake.bank_statement_note}")
                    
                    # HSK requirements
                    if intake.hsk_required is not None:
                        context_parts.append(f"  HSK Required: {'Yes' if intake.hsk_required else 'No'}")
                    if intake.hsk_level is not None:
                        context_parts.append(f"  HSK Level: {intake.hsk_level}")
                    if intake.hsk_min_score is not None:
                        context_parts.append(f"  HSK Minimum Score: {intake.hsk_min_score}")
                    
                    # English test requirements
                    if intake.english_test_required is not None:
                        context_parts.append(f"  English Test Required: {'Yes' if intake.english_test_required else 'No'}")
                    if intake.english_test_note:
                        context_parts.append(f"  English Test Note: {intake.english_test_note}")
                    
                    # ProgramDocument relationship
                    docs_map = self._get_program_documents_batch([intake.id])
                    documents = docs_map.get(intake.id, [])
                    if documents:
                        context_parts.append(f"  Required Documents:")
                        for doc in documents:
                            doc_lines = [doc.get('name', 'N/A')]
                            if doc.get('is_required') is not None:
                                doc_lines.append(f"Required: {'Yes' if doc['is_required'] else 'No'}")
                            if doc.get('rules'):
                                doc_lines.append(f"Rules: {doc['rules']}")
                            if doc.get('applies_to'):
                                doc_lines.append(f"Applies To: {doc['applies_to']}")
                            context_parts.append(f"    - {'; '.join(doc_lines)}")
                    
                    # ProgramExamRequirement relationship
                    exams_map = self._get_program_exam_requirements_batch([intake.id])
                    exam_reqs = exams_map.get(intake.id, [])
                    if exam_reqs:
                        context_parts.append(f"  Exam Requirements:")
                        for req in exam_reqs:
                            req_lines = [req.get('exam_name', 'N/A')]
                            if req.get('required') is not None:
                                req_lines.append(f"Required: {'Yes' if req['required'] else 'No'}")
                            if req.get('subjects'):
                                req_lines.append(f"Subjects: {req['subjects']}")
                            if req.get('min_level') is not None:
                                req_lines.append(f"Min Level: {req['min_level']}")
                            if req.get('min_score') is not None:
                                req_lines.append(f"Min Score: {req['min_score']}")
                            if req.get('exam_language'):
                                req_lines.append(f"Language: {req['exam_language']}")
                            if req.get('notes'):
                                req_lines.append(f"Notes: {req['notes']}")
                            context_parts.append(f"    - {'; '.join(req_lines)}")
                    
                    # Accommodation
                    if req_focus.get("accommodation") and intake.accommodation_note:
                        context_parts.append(f"  Accommodation Note: {intake.accommodation_note}")
                
                # For FEES intent: Show all fees + specific requirement fields if asked
                elif is_fees_intent:
                    # All fee fields
                    if intake.tuition_per_year:
                        context_parts.append(f"  Tuition/year: {intake.tuition_per_year} {intake.currency or 'CNY'}")
                    if intake.tuition_per_semester:
                        context_parts.append(f"  Tuition/semester: {intake.tuition_per_semester} {intake.currency or 'CNY'}")
                    if intake.application_fee:
                        context_parts.append(f"  Application fee: {intake.application_fee} {intake.currency or 'CNY'}")
                    if intake.accommodation_fee:
                        period = intake.accommodation_fee_period or "year"
                        context_parts.append(f"  Accommodation fee ({period}): {intake.accommodation_fee} {intake.currency or 'CNY'}")
                    if intake.medical_insurance_fee:
                        period = intake.medical_insurance_fee_period or "year"
                        context_parts.append(f"  Medical insurance fee ({period}): {intake.medical_insurance_fee} {intake.currency or 'CNY'}")
                    if intake.service_fee:
                        context_parts.append(f"  Service fee: {intake.service_fee} {intake.currency or 'CNY'}")
                    if intake.arrival_medical_checkup_fee:
                        context_parts.append(f"  Arrival medical checkup fee (one-time): {intake.arrival_medical_checkup_fee} {intake.currency or 'CNY'}")
                    if intake.visa_extension_fee:
                        context_parts.append(f"  Visa extension fee (annual): {intake.visa_extension_fee} {intake.currency or 'CNY'}")
                    if intake.application_deadline:
                        context_parts.append(f"  Application deadline: {intake.application_deadline}")
                    
                    # Specific requirement fields if user asks about them (from ProgramIntake fields, NOT from documents)
                    if req_focus.get("bank"):
                        if intake.bank_statement_required is not None:
                            context_parts.append(f"  Bank Statement Required: {'Yes' if intake.bank_statement_required else 'No'}")
                        if intake.bank_statement_amount is not None:
                            context_parts.append(f"  Bank Statement Amount: {intake.bank_statement_amount} {intake.bank_statement_currency or 'CNY'}")
                        if intake.bank_statement_note:
                            context_parts.append(f"  Bank Statement Note: {intake.bank_statement_note}")
                    
                    if req_focus.get("age"):
                        if intake.age_min is not None:
                            context_parts.append(f"  Age Minimum: {intake.age_min}")
                        if intake.age_max is not None:
                            context_parts.append(f"  Age Maximum: {intake.age_max}")
                    
                    # Always include accommodation_note for FEES intent
                    if intake.accommodation_note:
                        context_parts.append(f"  Accommodation Note: {intake.accommodation_note}")
                    
                    # Always include notes for FEES intent (may contain fee-related information)
                    if intake.notes:
                        context_parts.append(f"  Notes: {intake.notes}")
                    
                    if req_focus.get("exams"):
                        if intake.hsk_required is not None:
                            context_parts.append(f"  HSK Required: {'Yes' if intake.hsk_required else 'No'}")
                        if intake.hsk_level is not None:
                            context_parts.append(f"  HSK Level: {intake.hsk_level}")
                        if intake.hsk_min_score is not None:
                            context_parts.append(f"  HSK Minimum Score: {intake.hsk_min_score}")
                        if intake.english_test_required is not None:
                            context_parts.append(f"  English Test Required: {'Yes' if intake.english_test_required else 'No'}")
                        if intake.english_test_note:
                            context_parts.append(f"  English Test Note: {intake.english_test_note}")
                    
                    # Calculate total fees if duration is available
                    if intake.duration_years or (intake.major and intake.major.duration_years):
                        duration = intake.duration_years or (intake.major.duration_years if intake.major else None)
                        if duration:
                            context_parts.append(f"  Program Duration: {duration} year(s)")
                            # Calculate total fees over the course duration
                            total_tuition = None
                            total_accommodation = None
                            total_insurance = None
                            
                            if intake.tuition_per_year and duration:
                                total_tuition = intake.tuition_per_year * duration
                                context_parts.append(f"  Total Tuition (over {duration} year(s)): {total_tuition} {intake.currency or 'CNY'}")
                            
                            if intake.accommodation_fee and duration:
                                # Check if accommodation fee is per year or per semester
                                period = intake.accommodation_fee_period or "year"
                                if period == "year":
                                    total_accommodation = intake.accommodation_fee * duration
                                    context_parts.append(f"  Total Accommodation (over {duration} year(s)): {total_accommodation} {intake.currency or 'CNY'}")
                                elif period == "semester":
                                    # Assume 2 semesters per year
                                    total_accommodation = intake.accommodation_fee * duration * 2
                                    context_parts.append(f"  Total Accommodation (over {duration} year(s), {duration * 2} semesters): {total_accommodation} {intake.currency or 'CNY'}")
                            
                            if intake.medical_insurance_fee and duration:
                                period = intake.medical_insurance_fee_period or "year"
                                if period == "year":
                                    total_insurance = intake.medical_insurance_fee * duration
                                    context_parts.append(f"  Total Medical Insurance (over {duration} year(s)): {total_insurance} {intake.currency or 'CNY'}")
                            
                            # Calculate grand total (one-time fees + recurring fees)
                            one_time_fees = (intake.application_fee or 0) + (intake.arrival_medical_checkup_fee or 0)
                            recurring_fees = (total_tuition or 0) + (total_accommodation or 0) + (total_insurance or 0)
                            annual_recurring = (intake.visa_extension_fee or 0) * duration
                            grand_total = one_time_fees + recurring_fees + annual_recurring
                            
                            if grand_total > 0:
                                context_parts.append(f"  Estimated Total Course Fee (over {duration} year(s)): {grand_total} {intake.currency or 'CNY'}")
                                context_parts.append(f"    Breakdown: One-time fees ({one_time_fees}) + Recurring fees ({recurring_fees}) + Visa extension ({annual_recurring})")
                
                # For other intents (general, LIST_UNIVERSITIES, etc.)
                else:
                    # For deadline questions, always show application_deadline
                    if req_focus.get("deadline"):
                        if intake.application_deadline:
                            context_parts.append(f"  Application Deadline: {intake.application_deadline}")
                    
                    if req_focus.get("fees"):
                        if intake.tuition_per_year:
                            context_parts.append(f"  Tuition/year: {intake.tuition_per_year} {intake.currency or 'CNY'}")
                        if intake.accommodation_fee:
                            context_parts.append(f"  Accommodation/year: {intake.accommodation_fee} {intake.currency or 'CNY'}")
                        if intake.application_fee:
                            context_parts.append(f"  Application fee: {intake.application_fee} {intake.currency or 'CNY'}")
                    
                    # CRITICAL: For LIST_UNIVERSITIES with scholarship queries, include scholarship_info prominently
                    # This ensures the LLM sees scholarship information even when intent is LIST_UNIVERSITIES
                    if is_list_universities_intent and intake.scholarship_info:
                        context_parts.append(f"  Scholarship Information: {intake.scholarship_info}")
                        # Also include scholarship_available flag if set
                        if intake.scholarship_available is not None:
                            context_parts.append(f"  Scholarship Available: {'Yes' if intake.scholarship_available else 'No'}")
                    
                    # Include notes only for single result
                    if is_single:
                        if intake.notes:
                            context_parts.append(f"  Notes: {intake.notes}")
                        if req_focus.get("accommodation") and intake.accommodation_note:
                            context_parts.append(f"  Accommodation note: {intake.accommodation_note}")
                
                context_parts.append("")
        
        return "\n".join(context_parts)
    
    def format_answer_with_llm(self, db_context: str, user_question: str, intent: str, req_focus: Dict[str, bool], wants_fees: bool = False) -> str:
        """
        Stage B: Format final answer using LLM with small DB_CONTEXT.
        Temperature=0 for deterministic output.
        FORMATTER SAFETY: Enforces that LLM only uses provided db_results.
        """
        system_prompt = self.PARTNER_SYSTEM_PROMPT
        
        # Intent-specific instructions
        intent_instructions = ""
        if intent in ["SCHOLARSHIP", "scholarship_only"]:
            intent_instructions = """
INTENT: SCHOLARSHIP - Focus ONLY on scholarship information:
- Show scholarship_available (Yes/No) if present
- Show scholarship_info if present
- Show all structured scholarships from ProgramIntakeScholarship (name, coverage, waiver %, living allowance, deadlines, eligibility)
- Show notes if they contain scholarship-related information
- Do NOT mention documents, bank statements, age, exams, or accommodation unless they are specifically related to scholarship eligibility
- If a field is NULL in the database, it means "not required" or "not specified" - do NOT mention it
- Only say "Not provided" if the DATABASE CONTEXT explicitly says "not provided" or "Not specified"
"""
        elif intent in ["FEES", "fees_only"]:
            intent_instructions = """
INTENT: FEES - Focus ONLY on fee and cost information:
- Show all fee fields: tuition_per_year, tuition_per_semester, application_fee, accommodation_fee, medical_insurance_fee, service_fee, arrival_medical_checkup_fee, visa_extension_fee
- Show currency for all fees
- Show fee periods (per year, per semester, one-time, annual) as specified in the database
- If user asks about bank_statement, show bank_statement_required, bank_statement_amount, bank_statement_currency, bank_statement_note (from ProgramIntake fields, NOT from documents)
- If user asks about age, show age_min and age_max (from ProgramIntake fields)
- If user asks about accommodation, show accommodation_note and accommodation_fee (from ProgramIntake fields)
- If user asks about deadline, show application_deadline (from ProgramIntake field)
- If user asks about HSK/English test, show hsk_required/hsk_level/hsk_min_score and english_test_required/english_test_note (from ProgramIntake fields, NOT from documents)
- Do NOT show full document lists (ProgramDocument relationship) - only show specific requirement fields if user asks about them
- Do NOT show exam requirements list (ProgramExamRequirement relationship) - only show specific test fields if user asks about them
- If a field is NULL in the database, it means "not required" or "not specified" - do NOT mention it
- Only say "Not provided" if the DATABASE CONTEXT explicitly says "not provided" or "Not specified"
"""
        elif intent in ["REQUIREMENTS", "ADMISSION_REQUIREMENTS", "documents_only", "eligibility_only"]:
            intent_instructions = """
INTENT: REQUIREMENTS/ADMISSION - Focus ONLY on admission requirements:
- Show notes (important admission info)
- Show age requirements (min/max) if present (NULL means not required - don't mention)
- Show academic requirements (min_average_score) if present
- Show test/interview requirements (interview_required, written_test_required, acceptance_letter_required) if present (NULL means not required - don't mention)
- Show inside China applicant requirements if present
- Show bank statement requirements (required, amount, currency, note) if present (NULL means not required - don't mention)
- Show HSK requirements (required, level, min_score) if present (NULL means not required - don't mention)
- Show English test requirements (required, note) if present (NULL means not required - don't mention)
- Show all documents from ProgramDocument relationship
- Show all exam requirements from ProgramExamRequirement relationship
- Show accommodation note if present
- Do NOT mention scholarship information unless specifically asked
- If a field is NULL in the database, it means "not required" - do NOT mention it
- Only say "Not provided" if the DATABASE CONTEXT explicitly says "not provided" or "Not specified"
"""
        else:
            intent_instructions = """
INTENT: GENERAL - Show all relevant information based on focus areas.
- If a field is NULL in the database, it means "not required" or "not specified" - do NOT mention it
- Only say "Not provided" if the DATABASE CONTEXT explicitly says "not provided" or "Not specified"
"""
        
        user_prompt = f"""{db_context}

User question: {user_question}

Intent: {intent}
Focus areas: {', '.join([k for k, v in req_focus.items() if v])}
{intent_instructions}

CRITICAL RULES:
- Use ONLY the information provided in DATABASE CONTEXT above.
- Never mention a major/university that is NOT in the DATABASE CONTEXT.
- POSITIVE LANGUAGE: Always present information in a positive way. NEVER say "Not provided" unless the DATABASE CONTEXT explicitly says "not provided", "Not specified", or "N/A".
- NULL fields in database mean "not required" or "not specified" - do NOT mention them in your response.
- If a field is present in DATABASE CONTEXT, show it. Only omit if it literally says "not provided" or "Not specified" in the context.
- Do NOT use negative language like "No specific details on..." or "Not provided in our partner database" unless the context explicitly states that.
- Do NOT invent or hallucinate any information.

FORMATTING REQUIREMENTS:
- Use markdown formatting for better readability
- Use **bold** for university names, major names, and important labels (e.g., **Bank statement required:**, **Scholarship Available:**)
- Use bullet points (- or •) for lists of items (documents, fees, requirements, scholarships)
- Use numbered lists (1., 2., 3.) for sequential steps or ordered information
- Use line breaks between different universities/programs for clarity
- Group related information together (e.g., all fees together, all documents together)
- Use clear section headers with **bold** text (e.g., **Fees:**, **Required Documents:**, **Exams:**)

Example format:
**University Name:**

**Major Name:**

**Bank statement required:** Yes, amount 5000 USD.

**Other fees:**
- Medical checkup fee: 800 CNY (first year only)
- Accommodation fees: Triple room 3000 or 5000 CNY/year

**Required documents:**
- Passport
- Photo
- High school certificate & transcript
- Medical report

CONTENT REQUIREMENTS:
- For REQUIREMENTS/ADMISSION intents: Only show detailed document lists and admission process if there are LESS than 3 universities in the results
- For REQUIREMENTS/ADMISSION intents: If there are 3 or more universities, ask the user to choose a specific university first
- For LIST_UNIVERSITIES with fee comparison (wants_fees=True): DO NOT ask user to choose. Instead, COMPARE all universities and show which has the LOWEST fees. Show fee breakdown for all universities sorted by lowest fees first.
- For LIST_UNIVERSITIES: If there are MORE than 6 universities, show ONLY university names with teaching language and major/degree_level, then ask user to choose one for specific details
- For LIST_UNIVERSITIES: If there are 6 or LESS universities, show ALL universities with their actual names, teaching languages, major/degree_level, and scholarship information (if available). DO NOT use placeholders like "University A", "University B", "University C". Use the actual university names from the DATABASE CONTEXT.
- For LIST_UNIVERSITIES with scholarship requirement (including Type A, Type B, Type C, CSC): Show which universities offer scholarships and provide FULL scholarship details from the Scholarship Information field. Focus on scholarship information, NOT document requirements. Only show documents if specifically asked. Show ALL universities found in the DATABASE CONTEXT, do not limit to 2. CRITICAL: If Scholarship Information field contains Type A, Type B, or Type C, explicitly mention these scholarship types in your response.
- For LIST_PROGRAMS: Show ALL majors (even if 30-40), grouped by teaching language if multiple languages exist (don't ask user to choose language)
- For GENERAL queries: If multiple teaching languages exist and list < 4, show both Chinese and English taught programs separately (don't ask user to choose)
- Focus on the most relevant information based on the user's question (e.g., if they ask about fees, prioritize fee information)
- CRITICAL: ALWAYS use actual university names from the DATABASE CONTEXT. NEVER use placeholders like "University A", "University B", "University C", etc.

Provide a comprehensive, positive answer using ALL relevant information from the DATABASE CONTEXT above, focusing on the intent-specific fields. Format it clearly with markdown for easy reading.
"""
        
        try:
            response = self.openai_service.chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.0
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            return f"Error generating response: {str(e)}"
    
    def _fuzzy_match_university(self, user_input: str) -> Tuple[bool, Optional[Dict[str, Any]], List[Tuple[Dict[str, Any], float]]]:
        """
        Fuzzy match user input to university using lightweight cache or DBQueryService.
        Returns (matched: bool, best_match: Optional[Dict], all_matches: List[Tuple[Dict, score]])
        If multiple close matches, return top 2-3 for user to pick.
        """
        user_input_lower = user_input.lower().strip()
        
        # Try DBQueryService first (fast ILIKE search - now includes aliases)
        db_results = self.db_service.search_universities(name=user_input, is_partner=True, limit=10)
        print(f"DEBUG: _fuzzy_match_university('{user_input}') - DB search returned {len(db_results)} results")
        if db_results:
            # Check for exact match in name or aliases
            for uni in db_results:
                uni_name_lower = uni.name.lower()
                if uni_name_lower == user_input_lower:
                    return True, {"id": uni.id, "name": uni.name}, [({"id": uni.id, "name": uni.name}, 1.0)]
                
                # Check aliases for exact match
                if hasattr(uni, 'aliases') and uni.aliases:
                    aliases = uni.aliases if isinstance(uni.aliases, list) else (json.loads(uni.aliases) if isinstance(uni.aliases, str) else [])
                    for alias in aliases:
                        if str(alias).lower() == user_input_lower:
                            return True, {"id": uni.id, "name": uni.name}, [({"id": uni.id, "name": uni.name}, 1.0)]
            
            # Return top matches (check aliases for better scoring)
        matches = []
        for uni in db_results:
                uni_name_lower = uni.name.lower()
                score = SequenceMatcher(None, user_input_lower, uni_name_lower).ratio()
                
                # Check aliases for better match
                if hasattr(uni, 'aliases') and uni.aliases:
                    aliases = uni.aliases if isinstance(uni.aliases, list) else (json.loads(uni.aliases) if isinstance(uni.aliases, str) else [])
                    for alias in aliases:
                        alias_lower = str(alias).lower()
                        alias_score = SequenceMatcher(None, user_input_lower, alias_lower).ratio()
                        if alias_score > score:
                            score = alias_score
                
                    matches.append(({"id": uni.id, "name": uni.name}, score))
            
                matches.sort(key=lambda x: x[1], reverse=True)
                if matches and len(matches) > 0 and matches[0][1] >= 0.8:
                    return True, matches[0][0], matches[:3]
                else:
                    return False, None, matches[:3] if matches else []
        
        # Fallback to lightweight cache for fuzzy matching
        uni_cache = self._get_uni_name_cache()
        matches = []
        for uni in uni_cache:
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
        Find university IDs by location using DBQueryService.
        Returns list of matching university IDs.
        """
        if not city and not province:
            return []
        
        # Use DBQueryService for efficient DB query
        universities = self.db_service.search_universities(
            city=city,
            province=province,
            is_partner=True,
            limit=100  # Reasonable limit
        )
        
        return [uni.id for uni in universities]
    
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
        Fuzzy match user input to major using acronym expansion + keyword exact match + name/fuzzy similarity.
        Returns (matched: bool, best_match: Optional[Dict], all_matches: List[Tuple[Dict, score]])
        """
        # Step 1: Expand acronyms (e.g., "cse" -> "computer science")
        expanded_input = self._expand_major_acronym(user_input)
        user_input_clean = re.sub(r'[^\w\s&]', '', expanded_input.lower())
        # CRITICAL: Also keep original input for keyword matching (keywords might contain "cse", "CSE", etc.)
        original_input_clean = re.sub(r'[^\w\s&]', '', user_input.lower())
        
        # Step 2: Try DBQueryService first (fast ILIKE search with expanded input)
        # CRITICAL: Also search by keywords with original input (for acronyms like "cse")
        majors = []
        
        # First, try searching by keywords with original input (for acronyms like "cse")
        if user_input != expanded_input:
            keyword_majors = self.db_service.search_majors(
                university_id=university_id,
                keywords=user_input,  # Search keywords with original input (e.g., "cse")
                degree_level=degree_level,
                limit=top_k * 2
            )
            majors.extend(keyword_majors)
            print(f"DEBUG: _fuzzy_match_major - found {len(keyword_majors)} majors via keywords with original input '{user_input}'")
        
        # Also search by name with expanded input
        name_majors = self.db_service.search_majors(
            university_id=university_id,
            name=expanded_input,  # Use expanded input for DB search
            degree_level=degree_level,
            limit=top_k * 2  # Get more candidates for fuzzy matching
        )
        majors.extend(name_majors)
        print(f"DEBUG: _fuzzy_match_major - found {len(name_majors)} majors via name with expanded input '{expanded_input}'")
        
        # Remove duplicates (same major ID)
        seen_ids = set()
        unique_majors = []
        for m in majors:
            if m.id not in seen_ids:
                seen_ids.add(m.id)
                unique_majors.append(m)
        majors = unique_majors
        print(f"DEBUG: _fuzzy_match_major - total unique majors: {len(majors)}")
        
        # Step 3: Prepare candidates list (from DB results or cache)
        if not majors:
            # If DB returns no results, try cache-based fuzzy matching (lazy loaded)
            major_cache = self._get_majors_cached()  # Use cached loaders
            candidates = major_cache
        else:
            # Convert DB objects to dict format for compatibility
            candidates = [
                {
                    "id": m.id,
                    "name": m.name,
                    "name_cn": m.name_cn,
                    "keywords": m.keywords if isinstance(m.keywords, list) else (json.loads(m.keywords) if isinstance(m.keywords, str) else []),
                    "university_id": m.university_id,
                    "university_name": m.university.name if m.university else None,
                    "degree_level": m.degree_level,
                    "teaching_language": m.teaching_language,
                    "discipline": m.discipline,
                    "duration_years": m.duration_years,
                    "category": m.category
                }
                for m in majors
            ]
        
        # Filter by university_id and degree_level if provided (already filtered in DB query, but double-check)
        if university_id:
            candidates = [m for m in candidates if m.get("university_id") == university_id]
        if degree_level:
            candidates = [m for m in candidates if str(m.get("degree_level", "")).lower() == str(degree_level).lower()]
        
        # Convert cache format - already in dict format
        print(f"DEBUG: _fuzzy_match_major - candidates: {len(candidates)} candidates")
        all_majors = candidates[:100]  # Limit for performance
        
        if not all_majors:
            return False, None, []
        
        # Step 4: Match in order: keywords exact -> name exact/contains -> fuzzy similarity
        matches = []  # List of (major, score, match_type)
        
        for major in all_majors:
            major_name_clean = re.sub(r'[^\w\s&]', '', major["name"].lower())
            best_score_for_major = 0.0
            match_type = None
            
            # FIRST PASS: Exact keyword match (highest priority for acronyms like "cse", "cs")
            # CRITICAL: Check keywords with BOTH original input (for acronyms like "cse") AND expanded input (for full terms)
            keywords = self._normalize_keywords(major.get("keywords", []))
            if keywords:
                    for keyword in keywords:
                        keyword_clean = re.sub(r'[^\w\s&]', '', str(keyword).lower())
                        if not keyword_clean:
                            continue
                    # Exact keyword match with original input (e.g., "cse" matches keyword "cse")
                        if original_input_clean == keyword_clean:
                            if 0.98 > best_score_for_major:
                                best_score_for_major = 0.98
                                match_type = "keyword_exact_original"
                            break  # Highest priority, stop here
                    # Exact keyword match with expanded input (e.g., "computer science" matches keyword "computer science")
                        elif user_input_clean == keyword_clean:
                            if 0.98 > best_score_for_major:
                                best_score_for_major = 0.98
                                match_type = "keyword_exact_expanded"
                            break  # Highest priority, stop here
                    # Keyword contains match with original input
                        elif original_input_clean in keyword_clean or keyword_clean in original_input_clean:
                            match_ratio = SequenceMatcher(None, original_input_clean, keyword_clean).ratio()
                            if match_ratio > best_score_for_major:
                                best_score_for_major = match_ratio * 0.95  # High score for keyword contains
                                match_type = "keyword_contains_original"
                    # Keyword contains match with expanded input
                        elif user_input_clean in keyword_clean or keyword_clean in user_input_clean:
                            match_ratio = SequenceMatcher(None, user_input_clean, keyword_clean).ratio()
                            if match_ratio > best_score_for_major:
                                best_score_for_major = match_ratio * 0.95  # High score for keyword contains
                                match_type = "keyword_contains_expanded"
            
            # SECOND PASS: Exact/contains name match (only if keyword didn't match)
            if best_score_for_major < 0.9:
                if user_input_clean == major_name_clean:
                    best_score_for_major = 1.0
                    match_type = "exact_name"
                elif user_input_clean in major_name_clean or major_name_clean in user_input_clean:
                    match_ratio = SequenceMatcher(None, user_input_clean, major_name_clean).ratio()
                    if match_ratio > best_score_for_major:
                        best_score_for_major = match_ratio
                        match_type = "name_substring"
            
            # THIRD PASS: SequenceMatcher fuzzy similarity (only if previous passes didn't match well)
            if best_score_for_major < 0.75:
                match_ratio = SequenceMatcher(None, user_input_clean, major_name_clean).ratio()
                if match_ratio > best_score_for_major:
                    best_score_for_major = match_ratio * 0.85  # Slightly penalize pure fuzzy matches
                    match_type = "fuzzy_similarity"
            
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
    
    def resolve_university_id(self, university_raw: str) -> Optional[int]:
        """
        Deterministic resolution: match university_raw against universities.name/name_cn/aliases using fuzzy.
        Returns university_id if match found, None otherwise.
        """
        if not university_raw:
            return None
        
        matched, uni_dict, _ = self._fuzzy_match_university(university_raw)
        if matched and uni_dict:
            return uni_dict.get("id")
        return None
    
    def resolve_major_ids(self, major_query: str, degree_level: Optional[str] = None, 
                         teaching_language: Optional[str] = None, university_id: Optional[int] = None,
                         limit: int = 3, confidence_threshold: float = 0.78) -> List[int]:
        """
        Deterministic resolution: match major_query against majors.name/name_cn/keywords using fuzzy.
        CRITICAL: Returns max 3 IDs with confidence >= 0.78 to prevent wrong matches.
        Supports acronym expansion (ECE/CSE/EEE/CS/BBA/LLB).
        Special handling for "language program" -> category="Non-degree/Language Program".
        """
        if not major_query:
            return []
        
        # Expand major acronyms first
        expanded_query = self._expand_major_acronym(major_query)
        
        # CRITICAL: For acronyms like BBA/MBA, also try the base term without degree prefix
        # "BBA" -> "bachelor of business administration" but also try "business administration"
        # This helps match majors named "Business Administration" (without "bachelor of")
        base_query = expanded_query
        if expanded_query != major_query:  # Only if expansion happened
            # Remove degree prefixes like "bachelor of", "master of", "doctor of"
            base_query = re.sub(r'^(bachelor|master|doctor|phd)\s+of\s+', '', expanded_query.lower(), flags=re.IGNORECASE).strip()
            # If base_query is different and shorter, use it as an alternative search term
            if base_query != expanded_query.lower() and len(base_query) < len(expanded_query):
                print(f"DEBUG: resolve_major_ids - expanded '{major_query}' to '{expanded_query}', also trying base term '{base_query}'")
        
        # Special handling for language programs
        language_patterns = [
            r'\b(language\s+program|language\s+course|chinese\s+language|foundation|foundation\s+program|foundation\s+course|preparatory|preparatory\s+course|preparatory\s+non\s+degree|non-?degree\s+language)\b'
        ]
        is_language_query = any(re.search(pattern, expanded_query.lower()) for pattern in language_patterns)
        
        if is_language_query:
            print(f"DEBUG: resolve_major_ids - detected language query: '{major_query}', degree_level={degree_level}")
            
            # CRITICAL: For language programs, search by keywords FIRST (foundation, preparatory, chinese language, etc.)
            # Keywords field contains terms like "foundation", "preparatory", "chinese language course"
            language_keywords_to_search = ["language", "foundation", "preparatory", "chinese language", "non-degree", "non degree"]
            all_majors = []
            
            # Search by keywords for each language-related term
            for keyword in language_keywords_to_search:
                keyword_majors = self.db_service.search_majors(
                    university_id=university_id,
                    keywords=keyword,  # Search in keywords field
                    degree_level=degree_level,  # Also filter by degree_level if provided
                    teaching_language=teaching_language,
                    limit=200
                )
                all_majors.extend(keyword_majors)
                print(f"DEBUG: resolve_major_ids - found {len(keyword_majors)} majors with keyword '{keyword}'")
            
            # Also search by category if degree_level is "Language"
            if degree_level == "Language":
                category_majors = self.db_service.search_majors(
                    university_id=university_id,
                    degree_level="Language",
                    category="Non-degree/Language Program",
                    teaching_language=teaching_language,
                    limit=200
                )
                all_majors.extend(category_majors)
                print(f"DEBUG: resolve_major_ids - found {len(category_majors)} majors by category")
            
            # Remove duplicates (same major ID)
            seen_ids = set()
            majors = []
            for m in all_majors:
                if m.id not in seen_ids:
                    seen_ids.add(m.id)
                    majors.append(m)
            
            print(f"DEBUG: resolve_major_ids - found {len(majors)} unique Language/language-related majors (by keywords/category)")
            
            # If we found majors by keywords/category, return them
            if majors:
                print(f"DEBUG: resolve_major_ids - returning {len(majors)} language-related majors found by keywords/category")
                # For language programs, return ALL IDs (no limit) to allow duration checking and full comparison
                return [m.id for m in majors]
            else:
                # Fallback: if no majors found by keywords, try searching by degree_level only
                if degree_level == "Language":
                    fallback_majors = self.db_service.search_majors(
                        university_id=university_id,
                        degree_level="Language",
                        teaching_language=teaching_language,
                        limit=200
                    )
                    print(f"DEBUG: resolve_major_ids - fallback: found {len(fallback_majors)} Language majors by degree_level only")
                    return [m.id for m in fallback_majors]
            
            # If degree_level is not "Language", search by keywords (same approach as above)
            # This handles cases where user says "language program" but degree_level is not set to "Language"
            all_majors = []
            for keyword in language_keywords_to_search:
                keyword_majors = self.db_service.search_majors(
                    university_id=university_id,
                    keywords=keyword,
                    degree_level=degree_level,
                    teaching_language=teaching_language,
                    limit=50
                )
                all_majors.extend(keyword_majors)
            
            # Also search by category
            category_majors = self.db_service.search_majors(
                university_id=university_id,
                degree_level=degree_level,
                teaching_language=teaching_language,
                category="Non-degree/Language Program",
                limit=50
            )
            all_majors.extend(category_majors)
            
            # Remove duplicates
            seen_ids = set()
            language_majors = []
            for m in all_majors:
                if m.id not in seen_ids:
                    seen_ids.add(m.id)
                    language_majors.append(m)
            
            if language_majors:
                print(f"DEBUG: resolve_major_ids - found {len(language_majors)} language majors by keywords/category")
                return [m.id for m in language_majors[:limit]]
        
        # Use _fuzzy_match_major for resolution
        # CRITICAL: Try original query FIRST for keyword matching (acronyms like "cse" match keywords directly)
        # Then try expanded query for name matching
        matched = False
        best_match = None
        all_matches = []
        
        # First try with original query (for keyword matching - keywords might contain "cse", "CSE", etc.)
        if major_query != expanded_query:
            original_matched, original_best, original_all = self._fuzzy_match_major(
                major_query,  # Use original query for keyword matching
                university_id=university_id,
                degree_level=degree_level,
                top_k=10
            )
            if original_matched and original_all:
                # If original query matched via keywords, use those results
                print(f"DEBUG: resolve_major_ids - original query '{major_query}' matched via keywords, using those results")
                matched = original_matched
                best_match = original_best
                all_matches = original_all
        
        # If original query didn't match well, try with expanded query
        # CRITICAL: If university_id is provided, don't fall back to searching without university filter
        # This prevents matching majors from other universities when user specified a specific university
        if not matched or (all_matches and all_matches[0][1] < 0.8):
            expanded_matched, expanded_best, expanded_all = self._fuzzy_match_major(
                expanded_query,
                university_id=university_id,  # Keep university_id filter
                degree_level=degree_level,
                top_k=10  # Get more candidates, then filter by confidence
            )
            # Use expanded results if they're better or if original didn't match
            # But only if we found matches (don't use empty results if university filter is applied)
            if expanded_matched and expanded_all:
                if not matched or (expanded_all[0][1] > all_matches[0][1] if all_matches else False):
                    matched = expanded_matched
                    best_match = expanded_best
                    all_matches = expanded_all
                    print(f"DEBUG: resolve_major_ids - using expanded query '{expanded_query}' results (better match)")
            elif university_id and not expanded_all:
                # If university_id was provided and no matches found, don't try without university filter
                # This is intentional - user specified a university, so we should only return majors from that university
                print(f"DEBUG: resolve_major_ids - no matches found for '{expanded_query}' at university_id={university_id}, not falling back to all universities")
        
        # If no match and we have a base query (from acronym expansion), try that too
        # CRITICAL: Only try base query if university_id is NOT provided (to avoid false matches from other universities)
        # If university_id is provided and we found no matches, don't try without university filter
        if not matched and base_query != expanded_query.lower() and not university_id:
            print(f"DEBUG: resolve_major_ids - no match with expanded query '{expanded_query}', trying base query '{base_query}' (no university filter)")
            base_matched, base_best, base_all = self._fuzzy_match_major(
                base_query,
                university_id=None,  # Don't apply university filter for base query fallback
                degree_level=degree_level,
                top_k=10
            )
            # Use base query results if they're better or if expanded query had no results
            if base_matched or (not matched and base_all):
                matched = base_matched
                best_match = base_best
                all_matches = base_all
                print(f"DEBUG: resolve_major_ids - base query '{base_query}' found {len(base_all)} matches")
        
        if not matched or not all_matches:
            return []
        
        # Check if the match was via keywords (CSE -> Computer Science and Technology via keywords)
        # This is stored in the match_type, but we need to check the match_type from _fuzzy_match_major
        # For now, we'll check if the best match has high confidence (>=0.95) which indicates keyword match
        # Or we can check if the query is a short acronym (CSE, CS, BBA, etc.) and it matched
        matched_via_keywords = False
        if best_match and all_matches:
            best_score = all_matches[0][1] if all_matches else 0.0
            # If it's a short acronym (3-4 chars) and matched with high confidence (>=0.95), likely via keywords
            is_short_acronym = len(expanded_query.replace(' ', '')) <= 4
            if is_short_acronym and best_score >= 0.95:
                matched_via_keywords = True
                print(f"DEBUG: resolve_major_ids - '{major_query}' matched via keywords (acronym match with high confidence)")
        
        # Filter by confidence threshold and limit to top 3
        high_confidence_matches = [
            m for m in all_matches 
            if m[1] >= confidence_threshold  # m[1] is the score
        ]
        
        if not high_confidence_matches:
            # No high-confidence matches - check if best match is close enough for typos
            if all_matches:
                best_score = all_matches[0][1]
                # For common typos (like "sciance" -> "science"), lower threshold to 0.70
                # Check if it's a single-word typo (edit distance 1-2)
                if best_score >= 0.70:
                    # Check if it's likely a typo (high similarity but below threshold)
                    from difflib import SequenceMatcher
                    expanded_lower = expanded_query.lower()
                    best_major_name = all_matches[0][0].get("name", "").lower()
                    # If similarity is high (>=0.70) and word overlap is good, accept it
                    word_overlap = len(set(expanded_lower.split()) & set(best_major_name.split()))
                    if word_overlap >= 1:  # At least one word matches
                        print(f"DEBUG: resolve_major_ids('{major_query}') - accepting typo match (score={best_score:.2f}, threshold={confidence_threshold})")
                        # Return top match even if below threshold
                        return [all_matches[0][0]["id"]]
            
            best_score = all_matches[0][1] if all_matches else 0.0
            print(f"DEBUG: resolve_major_ids('{major_query}') - no matches >= {confidence_threshold} threshold (best was {best_score:.2f})")
            return []
        
        # Return top 3 IDs only
        major_ids = [m[0]["id"] for m in high_confidence_matches[:limit]]
        major_names = [m[0].get("name", "Unknown") for m in high_confidence_matches[:limit]]
        scores = [f"{m[1]:.2f}" for m in high_confidence_matches[:limit]]
        print(f"DEBUG: resolve_major_ids('{major_query}') - returning {len(major_ids)} IDs: {major_ids} ({major_names}) with scores: {scores}")
        return major_ids
    
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
        
        if show_catalog:
            # Load universities on demand (limit to 100 for context)
            universities = self.db_service.search_universities(is_partner=True, limit=100)
            uni_list = []
            for uni in universities:
                uni_info = f"- {uni.name}"
                if uni.name_cn:
                    uni_info += f" ({uni.name_cn})"
                if uni.city:
                    uni_info += f" - {uni.city}"
                if uni.province:
                    uni_info += f", {uni.province}"
                if uni.university_ranking:
                    uni_info += f" [University Ranking: {uni.university_ranking}]"
                if uni.world_ranking_band:
                    uni_info += f" [World Ranking Band: {uni.world_ranking_band}]"
                if uni.national_ranking:
                    uni_info += f" [National Ranking: {uni.national_ranking}]"
                if uni.aliases:
                    aliases = uni.aliases if isinstance(uni.aliases, list) else (json.loads(uni.aliases) if isinstance(uni.aliases, str) else [])
                    aliases_str = ", ".join(str(a) for a in aliases[:3])
                    uni_info += f" (Also known as: {aliases_str})"
                uni_list.append(uni_info)
            context_parts.append(f"\nDATABASE UNIVERSITIES (MalishaEdu Partner Universities):\n" + "\n".join(uni_list))
        
        if show_catalog:
            # Load majors on demand (limit to 200 for context)
            majors = self.db_service.search_majors(limit=200)
            major_list = []
            for major in majors:
                major_info = f"- {major.name}"
                if major.name_cn:
                    major_info += f" ({major.name_cn})"
                major_info += f" at {major.university.name if major.university else 'Unknown University'}"
                if major.degree_level:
                    major_info += f" ({major.degree_level})"
                if major.teaching_language:
                    major_info += f" [{major.teaching_language}]"
                if major.keywords:
                    keywords = major.keywords if isinstance(major.keywords, list) else (json.loads(major.keywords) if isinstance(major.keywords, str) else [])
                    keywords_str = ", ".join(str(k) for k in keywords[:3])
                    major_info += f" (Keywords: {keywords_str})"
                major_list.append(major_info)
            context_parts.append(f"\nDATABASE MAJORS (MalishaEdu Majors):\n" + "\n".join(major_list))
        
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
                        uni_name = self._get_university_name_by_id(uni_id)
                        major_name = self._get_major_name_by_id(major_id)
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
                    # Normalize fee fields for aggregated university-fee results (LIST_UNIVERSITIES with wants_fees)
                    if intake.get('tuition_per_year') is None and intake.get('min_tuition_per_year') is not None:
                        intake['tuition_per_year'] = intake.get('min_tuition_per_year')
                    if intake.get('tuition_per_semester') is None and intake.get('min_tuition_per_semester') is not None:
                        intake['tuition_per_semester'] = intake.get('min_tuition_per_semester')

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
                                uni = self.db.query(University).filter(University.id == uni_id).first()
                                uni_info = {"name": uni.name, "city": uni.city, "province": uni.province, "university_ranking": uni.university_ranking} if uni else None
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
                # Fix 3: Always show notes for documents_only/eligibility_only/scholarship_only/SCHOLARSHIP/REQUIREMENTS
                # CRITICAL: For LIST_UNIVERSITIES with wants_scholarship, also show scholarship_info prominently
                is_scholarship_query = intent in ["scholarship_only", "SCHOLARSHIP"] or (intent == self.router.INTENT_LIST_UNIVERSITIES and state and state.wants_scholarship)
                if intent in ["documents_only", "eligibility_only", "scholarship_only", "SCHOLARSHIP", "REQUIREMENTS", self.router.INTENT_ADMISSION_REQUIREMENTS]:
                    if intake.get('notes'):
                        intake_info += f"\n  Important Notes: {intake['notes']}"
                    else:
                        intake_info += f"\n  Important Notes: Not specified"
                    if is_scholarship_query and intake.get('scholarship_info'):
                        intake_info += f"\n  Scholarship Information: {intake['scholarship_info']}"
                else:
                    # For other intents, include notes if present
                    if intake.get('notes'):
                        intake_info += f"\n  Important Notes: {intake['notes']}"
                    # CRITICAL: For LIST_UNIVERSITIES with wants_scholarship, prominently show scholarship_info
                    if is_scholarship_query and intake.get('scholarship_info'):
                        intake_info += f"\n  Scholarship Information: {intake['scholarship_info']}"
                    elif intake.get('scholarship_info'):
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
                                # NULL - not specified (only show for doc/eligibility/requirements intents)
                                if intent in ["documents_only", "eligibility_only", "REQUIREMENTS", self.router.INTENT_ADMISSION_REQUIREMENTS]:
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
    
    def _normalize_keywords(self, keywords) -> List[str]:
        """
        Normalize keywords from various formats (list, JSON string, None) to list of strings.
        """
        if not keywords:
            return []
        if isinstance(keywords, list):
            return [str(k).strip().lower() for k in keywords if k]
        if isinstance(keywords, str):
            try:
                parsed = json.loads(keywords)
                if isinstance(parsed, list):
                    return [str(k).strip().lower() for k in parsed if k]
                return [str(parsed).strip().lower()] if parsed else []
            except (json.JSONDecodeError, ValueError):
                # If not JSON, treat as comma-separated or single string
                return [k.strip().lower() for k in keywords.split(',') if k.strip()]
        return [str(keywords).strip().lower()]
    
    # ========== FUZZY MATCHING HELPERS ==========
    
    def _normalize_token(self, s: str) -> str:
        """Normalize token: lowercase, remove punctuation, collapse spaces"""
        if not s:
            return ""
        s = s.lower()
        s = re.sub(r'[^\w\s]', '', s)  # Remove punctuation
        s = re.sub(r'\s+', ' ', s).strip()  # Collapse spaces
        return s
    
    def _similarity(self, a: str, b: str) -> float:
        """Calculate similarity ratio using SequenceMatcher"""
        return SequenceMatcher(None, self._normalize_token(a), self._normalize_token(b)).ratio()
    
    def _pick_best(self, candidate_strings: List[str], query: str, threshold: float = 0.72) -> Optional[Tuple[str, float]]:
        """
        Pick best matching candidate from list using fuzzy matching.
        Returns (best_match, score) or None if no match above threshold.
        """
        if not candidate_strings or not query:
            return None
        
        best_match = None
        best_score = 0.0
        query_normalized = self._normalize_token(query)
        
        for candidate in candidate_strings:
            score = self._similarity(candidate, query)
            if score > best_score:
                best_score = score
                best_match = candidate
        
        if best_score >= threshold:
            return (best_match, best_score)
        return None
    
    def _is_semantic_stopword(self, text: str) -> bool:
        """
        Check if text matches semantic stopwords that should NOT be treated as major_query.
        Returns True if text should be ignored as major_query.
        """
        if not text:
            return False
        
        text_lower = self._normalize_token(text)
        stopwords = {
            "scholarship", "scholarships", "info", "information", "fees", "fee", "tuition", 
            "cost", "price", "requirement", "requirements", "admission", "eligibility",
            "document", "documents", "doc", "docs", "paper", "papers", "deadline", "deadlines",
            "application", "apply", "bank", "statement", "hsk", "ielts", "toefl", "csca",
            "age", "accommodation", "country", "allowed", "restriction", "restrictions",
            "list", "lists", "show", "all", "available", "what", "which", "how", "much",
            "calculate", "compute", "find", "search", "filter", "sort", "compare", "comparison"
        }
        
        # Check exact match
        if text_lower in stopwords:
            return True
        
        # Check if any stopword is in the text
        words = text_lower.split()
        if any(word in stopwords for word in words):
            return True
        
        # Check fuzzy similarity to stopwords
        for stopword in stopwords:
            if self._similarity(text_lower, stopword) >= 0.85:
                return True
        
        return False
    
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
            
            # Format deadline (extract date only, no time)
            deadline_raw = intake.get('application_deadline', 'N/A')
            deadline = 'N/A'
            if deadline_raw and deadline_raw != 'N/A':
                try:
                    # Parse ISO format datetime and extract date only
                    if isinstance(deadline_raw, str):
                        deadline_dt = datetime.fromisoformat(deadline_raw.replace('Z', '+00:00'))
                        deadline = deadline_dt.date().isoformat()  # Format as YYYY-MM-DD
                    else:
                        deadline = str(deadline_raw)
                except (ValueError, AttributeError):
                    # If parsing fails, try to extract date part from string
                    if isinstance(deadline_raw, str):
                        # Try to extract YYYY-MM-DD pattern
                        import re
                        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', deadline_raw)
                        if date_match:
                            deadline = date_match.group(1)
                        else:
                            deadline = deadline_raw  # Fallback to original
                    else:
                        deadline = str(deadline_raw)
            
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
        """Store pagination state in cache - stores IDs only, not full objects"""
        cache_key = self._get_pagination_cache_key(partner_id, conversation_history, conversation_id)
        
        # Extract IDs from results (determine type from first item)
        result_ids = []
        result_type = "intake_ids"
        if results:
            first_item = results[0]
            if 'id' in first_item:  # ProgramIntake ID
                result_ids = [item.get('id') for item in results if item.get('id')]
                result_type = "intake_ids"
            elif 'university_id' in first_item:  # University ID (from grouped results)
                result_ids = list(set([item.get('university_id') for item in results if item.get('university_id')]))
                result_type = "university_ids"
            elif 'major_id' in first_item:  # Major ID
                result_ids = [item.get('major_id') for item in results if item.get('major_id')]
                result_type = "major_ids"
        
        self._pagination_cache[cache_key] = PaginationState(
            results=result_ids,
            result_type=result_type,
            offset=offset,
            total=total,
            page_size=page_size,
            intent=intent,
            timestamp=time.time(),
            last_displayed=last_displayed  # Keep full objects for duration questions
        )
        # Store this key as last pagination key for this partner (for stable fallback)
        if partner_id:
            self._last_pagination_key_by_partner[partner_id] = cache_key
        print(f"DEBUG: Stored pagination state for key: {cache_key}, offset={offset}, total={total}, page_size={page_size}, result_type={result_type}, ids_count={len(result_ids)}, last_displayed_count={len(last_displayed) if last_displayed else 0}")
    
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
                # Fetch results by IDs
                result_ids = pagination_state.results
                result_type = pagination_state.result_type
                next_offset = pagination_state.offset + pagination_state.page_size
                page_ids = result_ids[next_offset:next_offset + pagination_state.page_size]
                
                if page_ids:
                    # Fetch full objects by IDs
                    if result_type == "intake_ids":
                        intakes = self.db.query(ProgramIntake).filter(ProgramIntake.id.in_(page_ids)).all()
                        next_batch = []
                        for intake in intakes:
                            next_batch.append({
                                "id": intake.id,
                                "university_id": intake.university_id,
                                "major_id": intake.major_id,
                                "university_name": intake.university.name if intake.university else "N/A",
                                "major_name": intake.major.name if intake.major else "N/A",
                                "degree_level": intake.major.degree_level if intake.major else None,
                                "teaching_language": intake.teaching_language or (intake.major.teaching_language if intake.major else None),
                                "tuition_per_year": intake.tuition_per_year,
                                "tuition_per_semester": intake.tuition_per_semester,
                                "application_fee": intake.application_fee,
                                "application_deadline": intake.application_deadline.isoformat() if intake.application_deadline else None,
                                "intake_term": intake.intake_term.value if intake.intake_term else None,
                                "intake_year": intake.intake_year,
                                "currency": intake.currency or "CNY"
                            })
                    elif result_type == "university_ids":
                        # For university_ids, we need to fetch intakes for those universities
                        # This is more complex - for now, use last_displayed if available
                        next_batch = pagination_state.last_displayed[next_offset:next_offset + pagination_state.page_size] if pagination_state.last_displayed else []
                    else:
                        next_batch = pagination_state.last_displayed[next_offset:next_offset + pagination_state.page_size] if pagination_state.last_displayed else []
                    
                    if next_batch:
                        print(f"DEBUG: Returning list page offset={next_offset} size={len(next_batch)} total={pagination_state.total}")
                        # Update pagination state in cache (including last_displayed)
                        # Re-fetch all results to update
                        all_results = []
                        if result_type == "intake_ids":
                            all_intakes = self.db.query(ProgramIntake).filter(ProgramIntake.id.in_(result_ids)).all()
                            for intake in all_intakes:
                                all_results.append({
                                    "id": intake.id,
                                    "university_id": intake.university_id,
                                    "major_id": intake.major_id,
                                    "university_name": intake.university.name if intake.university else "N/A",
                                    "major_name": intake.major.name if intake.major else "N/A",
                                    "degree_level": intake.major.degree_level if intake.major else None,
                                    "teaching_language": intake.teaching_language or (intake.major.teaching_language if intake.major else None),
                                    "tuition_per_year": intake.tuition_per_year,
                                    "tuition_per_semester": intake.tuition_per_semester,
                                    "application_fee": intake.application_fee,
                                    "application_deadline": intake.application_deadline.isoformat() if intake.application_deadline else None,
                                    "intake_term": intake.intake_term.value if intake.intake_term else None,
                                    "intake_year": intake.intake_year,
                                    "currency": intake.currency or "CNY"
                                })
                        else:
                            all_results = pagination_state.last_displayed if pagination_state.last_displayed else []
                        
                        self._set_pagination_state(
                            partner_id=partner_id,
                            conversation_history=conversation_history,
                            results=all_results,
                            offset=next_offset,
                            total=pagination_state.total,
                            page_size=pagination_state.page_size,
                            intent=pagination_state.intent,
                            last_displayed=next_batch,
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
        
        # ========== STAGE A: Route + Clarify ==========
        # Get previous state from conversation history (if any)
        prev_state = None
        if len(conversation_history) >= 2:
            # Try to extract previous state from last assistant message or use cache
            # For now, we'll let route_and_clarify handle it via pending_slot cache
            pass
        
        # Use 2-stage pipeline: route_and_clarify
        try:
            print(f"DEBUG: Calling route_and_clarify...")
            route_plan = self.route_and_clarify(
                conversation_history,
                prev_state=prev_state,
                partner_id=partner_id,
                conversation_id=conversation_id
            )
            print(f"DEBUG: route_and_clarify returned: {route_plan.get('intent')}, needs_clarification={route_plan.get('needs_clarification')}")
        except Exception as e:
            import traceback
            print(f"ERROR: route_and_clarify failed: {e}")
            traceback.print_exc()
            return {
                "response": "I encountered an error processing your request. Please try again.",
                "used_db": False,
                "used_tavily": False,
                "sources": []
            }

        state = route_plan.get("state")
        needs_clarification = route_plan.get("needs_clarification", False)
        clarifying_question = route_plan.get("clarifying_question")
        
        # If clarification needed, return early
        if needs_clarification and clarifying_question:
            print(f"DEBUG: Clarification needed: {clarifying_question}")
            return {
                "response": clarifying_question,
                    "used_db": False,
                    "used_tavily": False,
                    "sources": []
                }
            
        # Normalize major_query if present - apply semantic stoplist and degree word check
        if state and state.major_query:
            # CRITICAL: Check semantic stopwords first
            if self._is_semantic_stopword(state.major_query):
                print(f"DEBUG: major_query '{state.major_query}' matches semantic stopword, clearing")
                state.major_query = None
            else:
                original_major_query = state.major_query
                state.major_query = self._normalize_unicode_text(state.major_query)
                # Strip degree phrases if present
                state.major_query = self._strip_degree_from_major_query(state.major_query)
            # If cleaned value is empty, set to None
            if not state.major_query or not state.major_query.strip():
                state.major_query = None

        print(f"DEBUG: Final state after normalization: {state.to_dict() if state else 'None'}")
        
        # ========== STAGE B: Run DB Query ==========
        if not state or not route_plan.get("sql_plan"):
            # No SQL plan means we can't query - return error
                return {
                "response": "I need more information to search the database. Please provide: degree level, major, or university.",
                    "used_db": False,
                    "used_tavily": False,
                    "sources": []
                }
        
        # Run DB query using route_plan
        # Pass latest user message for REQUIREMENTS intent to check if major was mentioned
        latest_user_msg = user_message_normalized if 'user_message_normalized' in locals() else user_message
        db_results = self.run_db(route_plan, latest_user_message=latest_user_msg, conversation_history=conversation_history)
        
        # Check if result is a fallback dict
        fallback_result = None
        if isinstance(db_results, dict) and db_results.get("_fallback"):
            fallback_result = db_results
            db_results = fallback_result.get("_intakes", [])
            print(f"DEBUG: Fallback triggered: type={fallback_result.get('_fallback_type')}, results={len(db_results)}")
        
        print(f"DEBUG: DB query returned {len(db_results)} results")
        
        # ========== HANDLE EMPTY RESULTS FOR LIST_PROGRAMS ==========
        # If LIST_PROGRAMS returns 0 results, try querying majors table directly
        # This handles "list majors" queries where user wants to see available majors, not specific program intakes
        if len(db_results) == 0 and state and state.intent == self.router.INTENT_LIST_PROGRAMS:
            print(f"DEBUG: LIST_PROGRAMS returned 0 results, trying to query majors table directly")
            # Check if user is asking for a list of majors (not specific program intakes)
            user_msg_lower = latest_user_msg.lower() if latest_user_msg else user_message_normalized.lower() if 'user_message_normalized' in locals() else user_message.lower()
            is_list_majors_query = any(phrase in user_msg_lower for phrase in [
                "list of majors", "list majors", "show majors", "majors of", "majors for",
                "available majors", "offered majors", "subjects", "programs offered"
            ])
            
            if is_list_majors_query or (state.university_query and state.degree_level):
                # Query majors table directly
                majors_list = self._get_majors_for_list_query(
                    university_id=state._resolved_university_id if hasattr(state, '_resolved_university_id') and state._resolved_university_id else None,
                    degree_level=state.degree_level,
                    teaching_language=state.teaching_language
                )
                
                if majors_list:
                    # Format the list of majors
                    university_name = majors_list[0].get("university_name", state.university_query or "the university")
                    major_names = [m.get("name") for m in majors_list if m.get("name")]
                    
                    if major_names:
                        # Remove duplicates while preserving order
                        seen = set()
                        unique_majors = []
                        for major in major_names:
                            if major not in seen:
                                seen.add(major)
                                unique_majors.append(major)
                        
                        majors_text = "\n".join([f"{idx+1}. {major}" for idx, major in enumerate(unique_majors)])
                        intake_str = f" for {state.intake_term} intake" if state.intake_term else ""
                        degree_str = f" {state.degree_level}" if state.degree_level else ""
                        
                        return {
                            "response": f"Here are the {degree_str} majors available at {university_name}{intake_str}:\n\n{majors_text}\n\nIf you want details about a specific major (fees, requirements, deadlines), please let me know which one interests you.",
                            "used_db": True,
                            "used_tavily": False,
                            "sources": []
                        }

        # ========== HANDLE EMPTY RESULTS FOR LIST_UNIVERSITIES ==========
        # If LIST_UNIVERSITIES with fees/free_tuition returns empty results, provide helpful message
        if len(db_results) == 0 and state and state.intent == self.router.INTENT_LIST_UNIVERSITIES:
            if state.wants_fees or (hasattr(state, 'wants_free_tuition') and state.wants_free_tuition):
                # Build a helpful message based on what filters were applied
                filter_parts = []
                if state.degree_level:
                    filter_parts.append(f"{state.degree_level} programs")
                if state.intake_term:
                    filter_parts.append(f"{state.intake_term} intake")
                if hasattr(state, 'wants_free_tuition') and state.wants_free_tuition:
                    filter_parts.append("free tuition")
                if state.city:
                    filter_parts.append(f"in {state.city}")
                if state.province:
                    filter_parts.append(f"in {state.province}")
                
                filter_str = " with " + ", ".join(filter_parts) if filter_parts else ""
                return {
                    "response": f"I couldn't find any universities{filter_str} matching your criteria. Try adjusting your filters (e.g., different intake term, degree level, or location).",
                            "used_db": True,
                            "used_tavily": False,
                            "sources": []
                        }
        
        # ========== CHECK FOR MULTIPLE PROGRAMS: Show university list with majors ==========
        # For language/foundation programs or when multiple universities have programs, show list
        if db_results and len(db_results) > 1 and state and state.degree_level == "Language":
            # Group by university and major
            from collections import defaultdict
            university_programs = defaultdict(list)
            for result in db_results:
                if hasattr(result, 'university') and hasattr(result, 'major'):
                    uni_name = result.university.name
                    major_name = result.major.name
                    deadline = result.application_deadline.strftime('%Y-%m-%d') if result.application_deadline else 'N/A'
                    # Get duration from major
                    duration_str = ""
                    if result.major.duration_years:
                        dur = result.major.duration_years
                        if dur < 0.5:
                            duration_str = f" ({int(dur * 12)} months)"
                        elif dur < 1.0:
                            duration_str = f" ({int(dur * 12)} months)"
                        elif dur == 1.0:
                            duration_str = " (1 year)"
                    else:
                            duration_str = f" ({dur} years)"
                    
                    key = (uni_name, major_name)
                    if key not in [p['key'] for p in university_programs[uni_name]]:
                        university_programs[uni_name].append({
                            'key': key,
                            'major': major_name,
                            'deadline': deadline,
                            'duration': duration_str
                        })
            
            # Count total unique programs
            total_programs = sum(len(progs) for progs in university_programs.values())
            
            # Check if we should show all results instead of asking for selection
            # If intent is FEES and results < 4, show fees for all instead of asking to choose
            should_show_all = (
                state.intent == "FEES" and total_programs < 4
            )
            
            # If multiple universities or multiple programs per university, show list
            # UNLESS we should show all (FEES intent with <4 results)
            if (len(university_programs) > 1 or any(len(progs) > 1 for progs in university_programs.values())) and not should_show_all:
                response_parts = ["I found multiple language programs. Please choose one to see specific details:\n"]
                idx = 1
                example_program = None  # Store first program for example
                for uni_name, programs in sorted(university_programs.items()):
                    for prog in programs:
                        program_line = f"{idx}. {uni_name} - {prog['major']}{prog['duration']} (Deadline: {prog['deadline']})"
                        response_parts.append(program_line)
                        if idx == 1:  # Use first program as example
                            example_program = f"{uni_name} - {prog['major']}"
                        idx += 1
                
                # Use actual first program for example instead of hardcoded "Nanjing University"
                example_text = f"'1' or '{example_program}'" if example_program else "'1'"
                response_parts.append(f"\nPlease specify which program you'd like information about (e.g., {example_text}).")
                
                return {
                    "response": "\n".join(response_parts),
                    "used_db": True,
                    "used_tavily": False,
                    "sources": []
                }
        
        # ========== MAJOR VALIDATION: Check if returned majors match user's major_query ==========
        # CRITICAL: Skip validation for language programs - they're matched by keywords/category, not exact name
        # Also skip if we already resolved major_ids (means we found matches via keywords/category)
        # Also skip if user just accepted a major suggestion (they already confirmed they want this major)
        is_language_query = (
            state and state.major_query and any(
                kw in state.major_query.lower() 
                for kw in ["language", "foundation", "preparatory", "chinese language", "non-degree"]
            )
        ) or (state and hasattr(state, '_resolved_major_ids') and state._resolved_major_ids and state.degree_level == "Language")
        
        # Check if user just accepted a major suggestion (from conversation history)
        # Look through ENTIRE conversation history for acceptance patterns
        user_accepted_major = False
        if conversation_history and len(conversation_history) >= 2:
            # Look through all messages to find if user accepted a major suggestion
            for i, msg in enumerate(conversation_history):
                if msg.get('role') == 'user':
                    user_msg = msg.get('content', '').lower()
                    acceptance_patterns = [r'\b(yes|ok|okay|sure|go ahead|that\'?s fine|sounds good|show me|provide)\b']
                    if any(re.search(pattern, user_msg) for pattern in acceptance_patterns):
                        # Check if the previous assistant message asked about a major
                        if i > 0 and conversation_history[i-1].get('role') == 'assistant':
                            prev_assistant = conversation_history[i-1].get('content', '')
                            if 'Would you like to see details for' in prev_assistant and 'programs?' in prev_assistant:
                                user_accepted_major = True
                                print(f"DEBUG: Skipping major validation - user accepted a major suggestion earlier in conversation (message {i})")
                                break
        
        # CRITICAL: Skip validation if the major was matched via keywords (CSE -> Computer Science and Technology via keywords)
        # Check if the major_query is a short acronym (CSE, CS, BBA, etc.) and if results contain majors with those keywords
        major_matched_via_keywords = False
        if state and state.major_query and db_results and len(db_results) > 0:
            # Check if major_query is a short acronym (3-5 chars, all caps or mixed case)
            major_query_clean = state.major_query.strip().replace('.', '').replace(' ', '')
            is_short_acronym = len(major_query_clean) <= 5 and (
                major_query_clean.isupper() or 
                (major_query_clean[0].isupper() if major_query_clean else False)
            )
            
            if is_short_acronym:
                # Check if any result major has keywords that match the acronym
                for result in db_results:
                    if hasattr(result, 'major') and result.major:
                        major = result.major
                        # Check keywords field
                        keywords = major.keywords if isinstance(major.keywords, list) else (
                            json.loads(major.keywords) if isinstance(major.keywords, str) else []
                        )
                        # Normalize keywords
                        keywords_lower = [str(k).lower().strip() for k in keywords]
                        major_query_lower = major_query_clean.lower()
                        
                        # Check if the acronym matches any keyword
                        if major_query_lower in keywords_lower or any(major_query_lower in k for k in keywords_lower):
                            major_matched_via_keywords = True
                            print(f"DEBUG: Skipping major validation - '{state.major_query}' matched '{major.name}' via keywords")
                            break
                    if major_matched_via_keywords:
                        break
        
        # Skip validation if language query, user accepted major, or matched via keywords
        # ALSO skip if major_ids were already resolved (means we found matches, don't ask again)
        has_resolved_major_ids = state and hasattr(state, '_resolved_major_ids') and state._resolved_major_ids
        if is_language_query or user_accepted_major or major_matched_via_keywords or has_resolved_major_ids:
            print(f"DEBUG: Skipping major validation - is_language_query={is_language_query}, user_accepted_major={user_accepted_major}, major_matched_via_keywords={major_matched_via_keywords}, has_resolved_major_ids={has_resolved_major_ids}")
            # Continue to response generation without validation
        elif state and state.major_query and db_results and len(db_results) > 0:
            # Check if any returned intake has a major that matches the user's query
            user_major_lower = state.major_query.lower().strip()
            matched_majors = []
            unmatched_count = 0
            
            # Import re at function level to avoid scoping issues
            import re as re_module
            
            for result in db_results:
                if hasattr(result, 'major') and result.major:
                    # Check if the major has keywords that match the user's query
                    major_keywords = getattr(result.major, 'keywords', None)
                    if major_keywords:
                        # Parse keywords (can be JSON array, comma-separated string, or list)
                        if isinstance(major_keywords, str):
                            try:
                                keywords = json.loads(major_keywords)
                            except:
                                keywords = [k.strip() for k in major_keywords.split(',') if k.strip()]
                        elif isinstance(major_keywords, list):
                            keywords = major_keywords
                        else:
                            keywords = []
                        
                        # Check if user's query matches any keyword
                        for keyword in keywords:
                            keyword_lower = str(keyword).lower().strip()
                            if user_major_lower == keyword_lower or user_major_lower in keyword_lower or keyword_lower in user_major_lower:
                                major_matched_via_keywords = True
                                print(f"DEBUG: Major '{result.major.name}' matched via keyword '{keyword}' (user query: '{user_major_lower}') - skipping validation")
                                break
                    if major_matched_via_keywords:
                        break
        
        # Skip validation if language query, user accepted major, or matched via keywords
        # ALSO skip if major_ids were already resolved (means we found matches, don't ask again)
        has_resolved_major_ids = state and hasattr(state, '_resolved_major_ids') and state._resolved_major_ids
        if is_language_query or user_accepted_major or major_matched_via_keywords or has_resolved_major_ids:
            print(f"DEBUG: Skipping major validation - is_language_query={is_language_query}, user_accepted_major={user_accepted_major}, major_matched_via_keywords={major_matched_via_keywords}, has_resolved_major_ids={has_resolved_major_ids}")
            # Continue to response generation without validation
        elif state and state.major_query and db_results and len(db_results) > 0:
            # Check if any returned intake has a major that matches the user's query
            user_major_lower = state.major_query.lower().strip()
            matched_majors = []
            unmatched_count = 0
            
            # Import re at function level to avoid scoping issues
            import re as re_module
            
            for result in db_results:
                if hasattr(result, 'major') and result.major:
                    major_name = result.major.name.lower() if result.major.name else ""
                    major_name_cn = result.major.name_cn.lower() if result.major.name_cn else ""
                    
                    # Use fuzzy matching to check similarity (more robust than substring)
                    major_name_clean = re_module.sub(r'[^\w\s&]', '', major_name)
                    major_name_cn_clean = re_module.sub(r'[^\w\s&]', '', major_name_cn) if major_name_cn else ""
                    user_major_clean = re_module.sub(r'[^\w\s&]', '', user_major_lower)
                    
                    # Check exact match or high similarity (>= 0.7)
                    similarity_name = SequenceMatcher(None, user_major_clean, major_name_clean).ratio()
                    similarity_cn = SequenceMatcher(None, user_major_clean, major_name_cn_clean).ratio() if major_name_cn_clean else 0.0
                    
                    # Also check if user query is a significant word in the major name (not just a substring)
                    major_words = set(major_name_clean.split())
                    user_words = set(user_major_clean.split())
                    word_overlap = len(user_words.intersection(major_words)) / max(len(user_words), 1)
                    
                    # CRITICAL: Also check keywords field for matching (CSE should match Computer Science and Technology via keywords)
                    keyword_match = False
                    major_keywords = getattr(result.major, 'keywords', None)
                    if major_keywords:
                        # Parse keywords (can be JSON array, comma-separated string, or list)
                        if isinstance(major_keywords, str):
                            try:
                                keywords = json.loads(major_keywords)
                            except:
                                keywords = [k.strip() for k in major_keywords.split(',') if k.strip()]
                        elif isinstance(major_keywords, list):
                            keywords = major_keywords
                        else:
                            keywords = []
                        
                        # Check if user's query matches any keyword
                        for keyword in keywords:
                            keyword_clean = re_module.sub(r'[^\w\s&]', '', str(keyword).lower())
                            if user_major_clean == keyword_clean or user_major_clean in keyword_clean or keyword_clean in user_major_clean:
                                keyword_match = True
                                print(f"DEBUG: Major validation - matched via keyword '{keyword}' for major '{result.major.name}'")
                                break
                    
                    # CRITICAL: If the major name exactly matches the user's query (after normalization), it's a match
                    # This handles cases where user accepted "Computer Science and Technology" and we're checking against "Computer Science and Technology" programs
                    exact_name_match = (user_major_clean == major_name_clean or 
                                       user_major_clean == major_name_cn_clean or
                                       major_name_clean.startswith(user_major_clean) or
                                       user_major_clean.startswith(major_name_clean))
                    
                    is_match = (
                        exact_name_match or  # Exact or near-exact name match (highest priority)
                        similarity_name >= 0.7 or similarity_cn >= 0.7 or
                        word_overlap >= 0.5 or
                        keyword_match or  # Match via keywords field
                        (user_major_clean in major_name_clean and len(user_major_clean) >= 4)  # Only if query is substantial
                    )
                    
                    print(f"DEBUG: Major validation - user='{user_major_lower}', major='{major_name}', exact_match={exact_name_match}, similarity={similarity_name:.2f}, word_overlap={word_overlap:.2f}, keyword_match={keyword_match}, is_match={is_match}")
                    
                    if is_match:
                        matched_majors.append(result.major.name)
                    else:
                        unmatched_count += 1
            
            # If NO results match the user's major query, provide helpful message
            if len(matched_majors) == 0 and len(db_results) > 0:
                print(f"DEBUG: Major validation failed - user asked for '{state.major_query}' but results don't match")
                # Get closest majors for suggestion (deduplicate by name)
                major_cache = self._get_majors_cached()
                _, _, closest_matches = self._fuzzy_match_major(
                    state.major_query,
                            degree_level=state.degree_level,
                    top_k=10  # Get more to deduplicate
                )
                # Deduplicate by major name to avoid showing "Applied Physics" 5 times
                seen_names = set()
                closest_major_names = []
                for m in closest_matches:
                    if m[1] >= 0.6:  # Confidence threshold
                        major_name = m[0].get("name", "")
                        if major_name and major_name not in seen_names:
                            seen_names.add(major_name)
                            closest_major_names.append(major_name)
                            if len(closest_major_names) >= 5:  # Limit to 5 unique majors
                                break
                
                # Generate a more friendly, user-friendly response
                # Don't just list database results - provide helpful guidance
                lang_str = f"{state.teaching_language or 'English'}-taught " if state.teaching_language else ""
                intake_str = f"for {state.intake_term or 'March'} intake " if state.intake_term else ""
                
                if closest_major_names:
                    # Deduplicate and format nicely
                    unique_majors = []
                    seen = set()
                    for major in closest_major_names:
                        major_lower = major.lower()
                        if major_lower not in seen:
                            seen.add(major_lower)
                            unique_majors.append(major)
                    
                    # If user asked for "cse" and we found "Computer Science and Technology", be more helpful
                    if state.major_query and state.major_query.lower() in ["cse", "cs", "computer science"]:
                        if any("computer science" in m.lower() for m in unique_majors):
                            suggested_major = "Computer Science and Technology"
                            response_msg = f"I found {lang_str}{intake_str}programs for {suggested_major}, but not exactly matching '{state.major_query}'. Would you like to see details for {suggested_major} programs?"
                            # Set pending slot to track the suggested major
                            if partner_id and conversation_id:
                                state_snapshot = PartnerQueryState()
                                state_snapshot.__dict__.update(state.__dict__)
                                state_snapshot._suggested_major = suggested_major
                                self._set_pending(partner_id, conversation_id, "major_acceptance", state_snapshot)
                                print(f"DEBUG: Set pending slot 'major_acceptance' with suggested major: {suggested_major}")
                    else:
                            response_msg = f"I couldn't find {lang_str}{intake_str}programs for {state.major_query} in our partner database. Here are similar programs available: {', '.join(unique_majors[:3])}. Which one interests you?"
                else:
                        response_msg = f"I couldn't find {lang_str}{intake_str}programs for {state.major_query} in our partner database. Here are similar programs available: {', '.join(unique_majors[:3])}. Which one interests you?"
            else:
                    response_msg = f"I couldn't find {lang_str}{intake_str}programs for {state.major_query} in our partner database. Could you try a different major or intake term?"
                                
            return {
                    "response": response_msg,
                                    "used_db": True,
                                    "used_tavily": False,
                                    "sources": []
                                }
        
        # ========== HANDLE FALLBACK RESULTS ==========
        if fallback_result:
            fallback_type = fallback_result.get("_fallback_type")
            
            if fallback_type == "duration":
                # Duration filter returned 0 results, but programs exist with other durations
                available_durations = fallback_result.get("_available_durations", [])
                fallback_intakes = fallback_result.get("_intakes", [])
                durations_str = ", ".join(available_durations) if available_durations else "different durations"
                
                # CRITICAL: Store the fallback intakes in cached state so we can use them when user selects a duration
                # Store them in a special field that will be used when user replies with duration
                if state and partner_id and conversation_id and fallback_intakes:
                    # Store the intake IDs in the state for later use (not objects to avoid detached session errors)
                    state._duration_fallback_intake_ids = [intake.id for intake in fallback_intakes]
                    state._duration_fallback_available = available_durations
                    # Set pending slot for duration clarification
                    state_snapshot = PartnerQueryState()
                    state_snapshot.__dict__.update(state.__dict__)
                    state_snapshot._duration_fallback_intake_ids = [intake.id for intake in fallback_intakes]
                    state_snapshot._duration_fallback_available = available_durations
                    self._set_pending(partner_id, conversation_id, "duration", state_snapshot)
                    self._set_cached_state(partner_id, conversation_id, state, None)
                    print(f"DEBUG: Duration fallback - stored {len(fallback_intakes)} intake IDs for duration clarification")
                
                return {
                    "response": f"I found language programs with different durations. Which duration do you prefer: {durations_str}?",
                    "used_db": True,
                    "used_tavily": False,
                    "sources": []
                }
            
            if fallback_type == "intake_term" and len(db_results) > 0:
                # Intake term removal yielded results - check how many terms are available
                available_terms = fallback_result.get("_available_terms", [])
                if len(available_terms) == 1:
                    # Only one option - use it automatically instead of asking
                    single_term = available_terms[0]
                    print(f"DEBUG: Only one intake term available ({single_term}), using it automatically")
                    # Update state with the single available term
                    from app.models import IntakeTerm
                    try:
                        # Normalize term name (e.g., "SEPTEMBER" -> "September")
                        term_normalized = single_term.title() if single_term else None
                        state.intake_term = term_normalized
                        # Re-run query with the correct intake term
                        sql_params = self.build_sql_params(state)
                        route_plan = {
                            "intent": state.intent,
                            "state": state,
                            "needs_clarification": False,
                            "sql_plan": sql_params,
                            "req_focus": {}
                        }
                        intakes = self.run_db(route_plan, latest_user_message=None, conversation_history=None)
                        if intakes and len(intakes) > 0:
                            # Format and return results using the same method as normal flow
                            db_context = self.build_db_context(intakes, state.req_focus if hasattr(state, 'req_focus') else {}, list_mode=False, intent=state.intent)
                            response_text = self.format_answer_with_llm(
                                db_context=db_context,
                                user_question=conversation_history[-1].get("content", "") if conversation_history else "",
                                intent=state.intent,
                                req_focus=state.req_focus if hasattr(state, 'req_focus') else {},
                                wants_fees=state.wants_fees if hasattr(state, 'wants_fees') else False
                            )
                            return {
                                "response": response_text,
                                "used_db": True,
                                "used_tavily": False,
                                "sources": []
                            }
                        else:
                            # Still no results even with the single term - show what we found
                            terms_str = single_term
                            return {
                                "response": f"I found programs for {terms_str} intake. Here are the available programs:",
                                "used_db": True,
                                "used_tavily": False,
                                "sources": []
                            }
                    except Exception as e:
                        import traceback
                        print(f"DEBUG: Error auto-selecting intake term: {e}")
                        traceback.print_exc()
                
                # Multiple options - ask user to choose with clear options
                terms_str = " or ".join(available_terms) if available_terms else "March or September"
                return {
                    "response": f"I found programs, but not for the intake term you specified. Available terms: {terms_str}. Which term would you like?",
                        "used_db": True,
                        "used_tavily": False,
                        "sources": []
                    }
            
            elif fallback_type == "teaching_language" and len(db_results) > 0:
                # CRITICAL: Only ask for teaching language if we have MORE than 3 results
                # If ≤3 results, show all without asking (user can see languages in results)
                if len(db_results) > 3:
                    # Teaching language removal yielded results - ask which language
                    available_languages = fallback_result.get("_available_languages", [])
                    langs_str = " or ".join(sorted(available_languages)) if available_languages else "English or Chinese"
                    
                    # Set pending state for teaching language clarification
                    state_snapshot = PartnerQueryState()
                    state_snapshot.__dict__.update(state.__dict__)
                    state_snapshot.teaching_language = None  # Clear only this slot
                    # Set pending with snapshot - this will also cache the state with pending info
                    self._set_pending(partner_id, conversation_id, "teaching_language", state_snapshot)
                    # Note: _set_pending already calls _set_cached_state, so we don't need to call it again
                    
                    print(f"DEBUG: Fallback teaching_language - available_languages={available_languages}, results={len(db_results)} (>3), setting pending")
                    
                    return {
                        "response": f"I found programs available in {langs_str}. Which do you prefer?",
                        "used_db": True,
                        "used_tavily": False,
                        "sources": []
                    }
                else:
                    # ≤3 results - don't ask, just use the fallback results (showing all languages)
                    print(f"DEBUG: Fallback teaching_language - results={len(db_results)} (≤3), using results without asking for language preference")
            
            elif fallback_type == "missing_intake_term":
                # For deadline queries, ask for intake_term instead of suggesting unrelated majors
                        return {
                    "response": "Which intake term are you interested in? (March or September)",
                            "used_db": True,
                            "used_tavily": False,
                            "sources": []
                        }
            
            elif fallback_type == "no_results" and len(db_results) == 0:
                # Still no results after fallbacks - intelligent message
                # For deadline queries, don't suggest unrelated majors - ask for missing fields instead
                is_deadline_query = state and hasattr(state, 'wants_deadline') and state.wants_deadline
                if is_deadline_query:
                    # For deadline queries, ask for missing fields
                    missing_fields = []
                    if not state.intake_term:
                        missing_fields.append("intake term (March/September)")
                    if not state.degree_level:
                        missing_fields.append("degree level (Language/Bachelor/Master/PhD)")
                    if missing_fields:
                        return {
                            "response": f"Please provide: {', '.join(missing_fields)}",
                            "used_db": True,
                            "used_tavily": False,
                            "sources": []
                        }

                # Check if major is missing and offer suggestions
                if not state.major_query:
                    # Offer top majors for degree/intake
                    major_cache = self._get_majors_cached()
                    filtered_majors = [m for m in major_cache[:100] if 
                                     (not state.degree_level or str(m.get("degree_level", "")).lower() == str(state.degree_level).lower())]
                    if filtered_majors:
                        # Deduplicate major names (remove duplicates)
                        seen_names = set()
                        unique_majors = []
                        for m in filtered_majors:
                            major_name = m.get("name", "")
                            if major_name and major_name.lower() not in seen_names:
                                seen_names.add(major_name.lower())
                                unique_majors.append(major_name)
                                if len(unique_majors) >= 5:  # Limit to 5 unique majors
                                    break
                        
                        if unique_majors:
                            # If city is provided, show universities in that city instead
                            if state.city or state.province:
                                location_str = state.city or state.province
                                # Query universities in that location
                                location_universities = self.db_service.search_universities(
                                    city=state.city,
                                    province=state.province,
                                    is_partner=True,
                                    limit=10
                                )
                                if location_universities:
                                    # If only 1 university, auto-select it and show available majors
                                    if len(location_universities) == 1:
                                        single_uni = location_universities[0]
                                        # Auto-select the university and preserve context
                                        state.university_query = single_uni.name
                                        state._resolved_university_id = single_uni.id
                                        # Cache the state with university selected
                                        self._set_cached_state(partner_id, conversation_id, state, None)
                                        
                                        # Query available majors for this university
                                        majors_for_uni = self.db_service.search_majors(
                                            university_id=single_uni.id,
                                            degree_level=state.degree_level,
                                            limit=20
                                        )
                                        if majors_for_uni:
                                            # Deduplicate major names
                                            seen_major_names = set()
                                            unique_major_names = []
                                            for m in majors_for_uni:
                                                major_name = m.name
                                                if major_name and major_name.lower() not in seen_major_names:
                                                    seen_major_names.add(major_name.lower())
                                                    unique_major_names.append(major_name)
                                                    if len(unique_major_names) >= 10:
                                                        break
                                            
                                            majors_list = ", ".join(unique_major_names[:10])
                                            return {
                                                "response": f"I found {single_uni.name} in {location_str}. Available {state.degree_level or ''} majors: {majors_list}. Which major interests you?",
                                                "used_db": True,
                                                "used_tavily": False,
                                                "sources": []
                                            }
                                        else:
                                            return {
                                                "response": f"I found {single_uni.name} in {location_str}, but no {state.degree_level or ''} majors are available. Would you like to see other degree levels?",
                                                "used_db": True,
                                                "used_tavily": False,
                                                "sources": []
                                            }
        
                                    else:
                                        # Multiple universities - ask user to choose and set pending slot
                                        uni_names = [uni.name for uni in location_universities[:5]]
                                        uni_ids = [uni.id for uni in location_universities[:5]]
                                        
                                        # Store university candidates in state for later matching
                                        state._university_candidates = uni_names
                                        state._university_candidate_ids = uni_ids
                                        
                                        # Set pending slot for university_choice
                                        state.pending_slot = "university_choice"
                                        state.is_clarifying = True
                                        self._set_pending(partner_id, conversation_id, "university_choice", state)
                                        
                                        # Cache the state
                                        self._set_cached_state(partner_id, conversation_id, state, None)
                                        
                                        return {
                                            "response": f"I found {len(location_universities)} universities in {location_str}. Here are some: {', '.join(uni_names)}. Which university would you like to explore?",
                                            "used_db": True,
                                            "used_tavily": False,
                                            "sources": []
                                        }
                                else:
                                    # No universities found in the location
                                    return {
                                        "response": f"I couldn't find any universities in {location_str} matching your criteria. Available majors for {state.degree_level or 'your criteria'}: {', '.join(unique_majors)}. Tell me a major name or say 'show available majors'.",
                                        "used_db": True,
                                        "used_tavily": False,
                                        "sources": []
                                    }
                            else:
                                # No city/province, show majors list
                                return {
                                    "response": f"I couldn't find an upcoming intake matching those filters. Available majors for {state.degree_level or 'your criteria'}: {', '.join(unique_majors)}. Tell me a major name or say 'show available majors'.",
                                    "used_db": True,
                                    "used_tavily": False,
                                    "sources": []
                                }
                
        # ========== POST-QUERY: Check for mixed teaching languages ==========
        # Use DBQueryService to check distinct languages before asking
        # CRITICAL: Skip teaching_language check for language programs (they're Chinese language programs by default)
        is_language_program = (
            (state and state.degree_level == "Language") or
            (state and state.major_query and any(kw in state.major_query.lower() for kw in ["language", "foundation", "preparatory", "chinese language", "non-degree"]))
        )
        
        # CRITICAL: Only ask for teaching language clarification if:
        # 1. Not a LIST_UNIVERSITIES intent (for list queries, show all languages without asking)
        # 2. OR if LIST_UNIVERSITIES and >3 universities (for ≤3, show all with languages)
        # 3. Not already clarifying something else
        # 4. Not a language program (language programs don't need teaching language clarification)
        should_ask_teaching_language = (
            not state.teaching_language and 
            not state.is_clarifying and 
            not is_language_program and
            state.intent != self.router.INTENT_LIST_UNIVERSITIES  # For LIST_UNIVERSITIES, handle differently
        )
        
        if db_results and should_ask_teaching_language:
            # Check if we have enough info to query distinct languages
            if state.degree_level or state.major_query or state.university_query or state.intake_term:
                filters_for_lang_check = {}
                if state.degree_level:
                    filters_for_lang_check["degree_level"] = state.degree_level
                if state.intake_term:
                    filters_for_lang_check["intake_term"] = state.intake_term
                if state.university_query:
                    matched, uni_dict, _ = self._fuzzy_match_university(state.university_query)
                    if matched and uni_dict:
                        filters_for_lang_check["university_id"] = uni_dict.get("id")
                
                # Get distinct teaching languages
                try:
                    distinct_languages = self.db_service.get_distinct_teaching_languages(filters_for_lang_check)
                    
                    # CRITICAL: Only ask for teaching language clarification if:
                    # 1. There are 2+ distinct languages
                    # 2. AND we have MORE than 3 results (if ≤3, show all without asking)
                    if len(distinct_languages) >= 2 and len(db_results) > 3:
                        # Mixed teaching languages - ask clarification (only for non-LIST_UNIVERSITIES intents)
                        langs_str = " or ".join(sorted(distinct_languages))
                        print(f"DEBUG: Mixed teaching languages detected: {distinct_languages}, results={len(db_results)} (>3), asking clarification")
                        
                        # Set pending state for teaching language (use unified cache)
                        # Create a copy of state with teaching_language=None for the snapshot
                        state_snapshot = PartnerQueryState()
                        state_snapshot.__dict__.update(state.__dict__)
                        state_snapshot.teaching_language = None  # Clear only this slot
                        # Set pending with snapshot - this will also cache the state with pending info
                        self._set_pending(partner_id, conversation_id, "teaching_language", state_snapshot)
                        # Note: _set_pending already calls _set_cached_state, so we don't need to call it again
                        
                        return {
                            "response": f"I found programs available in {langs_str}. Which do you prefer?",
                            "used_db": True,
                            "used_tavily": False,
                            "sources": []
                        }
                    elif len(distinct_languages) == 1:
                        # Auto-fill teaching language
                        state.teaching_language = list(distinct_languages)[0]
                        print(f"DEBUG: Auto-filled teaching_language: {state.teaching_language}")
                except Exception as e:
                    print(f"DEBUG: Error checking distinct languages: {e}, using fallback method")
                    # Fallback to checking results directly
                    teaching_languages = set()
                    for result in db_results:
                        if hasattr(result, 'teaching_language') and result.teaching_language:
                            teaching_languages.add(str(result.teaching_language))
                        elif hasattr(result, 'major') and result.major and result.major.teaching_language:
                            teaching_languages.add(str(result.major.teaching_language))
                    
                    # CRITICAL: Only ask if we have MORE than 3 results (if ≤3, show all without asking)
                    if len(teaching_languages) > 1 and len(db_results) > 3:
                        langs_str = " or ".join(sorted(teaching_languages))
                        # Create a copy of state with teaching_language=None for the snapshot
                        state_snapshot = PartnerQueryState()
                        state_snapshot.__dict__.update(state.__dict__)
                        state_snapshot.teaching_language = None  # Clear only this slot
                        # Set pending with snapshot - this will also cache the state with pending info
                        self._set_pending(partner_id, conversation_id, "teaching_language", state_snapshot)
                        # Note: _set_pending already calls _set_cached_state, so we don't need to call it again
                        
                        print(f"DEBUG: Fallback method - Mixed teaching languages detected: {teaching_languages}, results={len(db_results)} (>3), asking clarification")
                        return {
                            "response": f"I found programs available in {langs_str}. Which do you prefer?",
                            "used_db": True,
                            "used_tavily": False,
                            "sources": []
                        }
                    elif len(teaching_languages) > 1:
                        print(f"DEBUG: Fallback method - Mixed teaching languages detected: {teaching_languages}, results={len(db_results)} (≤3), NOT asking (showing all)")
                    elif len(teaching_languages) == 1:
                        # Auto-fill teaching language
                        state.teaching_language = list(teaching_languages)[0]
                        print(f"DEBUG: Auto-filled teaching_language: {state.teaching_language}")

        # ========== STAGE C: Format Response ==========
        # Store university_ids and intake_ids from current results for context preservation
        if db_results and state and partner_id and conversation_id:
            # Extract unique university_ids from results
            university_ids = []
            intake_ids = []
            for result in db_results:
                if hasattr(result, 'university_id') and result.university_id:
                    if result.university_id not in university_ids:
                        university_ids.append(result.university_id)
                if hasattr(result, 'id'):
                    intake_ids.append(result.id)
            
            if university_ids:
                state._previous_university_ids = university_ids
                print(f"DEBUG: Stored _previous_university_ids: {university_ids} for context preservation")
            if intake_ids:
                state._previous_intake_ids = intake_ids
                print(f"DEBUG: Stored _previous_intake_ids: {intake_ids} for context preservation")
            # Cache the state with previous IDs
            self._set_cached_state(partner_id, conversation_id, state, None)
        
        # Build DB context (small, field-aware)
        req_focus = route_plan.get("req_focus", {})
        list_mode = state.wants_list if state else False
        intent = state.intent if state else "general"
        db_context = self.build_db_context(db_results, req_focus, list_mode, intent=intent)
        
        # For list queries with many results, use deterministic formatting
        if list_mode and len(db_results) > 5:
            # Convert ProgramIntake objects to dict format for _format_list_response_deterministic
            intakes_dict = []
            for intake in db_results:
                if hasattr(intake, 'university') and hasattr(intake, 'major'):
                    intakes_dict.append({
                        "id": intake.id,
                        "university_id": intake.university_id,
                        "major_id": intake.major_id,
                        "university_name": intake.university.name if intake.university else "N/A",
                        "major_name": intake.major.name if intake.major else "N/A",
                        "degree_level": intake.major.degree_level if intake.major else None,
                        "teaching_language": intake.teaching_language or (intake.major.teaching_language if intake.major else None),
                        "tuition_per_year": intake.tuition_per_year,
                        "tuition_per_semester": intake.tuition_per_semester,
                        "application_fee": intake.application_fee,
                        "application_deadline": intake.application_deadline.isoformat() if intake.application_deadline else None,
                        "intake_term": intake.intake_term.value if hasattr(intake.intake_term, 'value') else str(intake.intake_term),
                        "intake_year": intake.intake_year,
                        "currency": intake.currency or "CNY"
                    })
            
            # Store pagination state
                self._set_pagination_state(
                    partner_id=partner_id,
                    conversation_history=conversation_history,
                results=intakes_dict,
                    offset=0,
                total=len(intakes_dict),
                page_size=12,
                intent=state.intent if state else "general",
                last_displayed=intakes_dict[:12],
                    conversation_id=conversation_id
                )
                
            return self._format_list_response_deterministic(
                intakes_dict[:24], 0, len(intakes_dict),
                duration_preference=None,
                    user_message=user_message
                )
        
        # ========== RULE: LIST_UNIVERSITIES with >6 universities - show names only ==========
        if db_results and state and state.intent == self.router.INTENT_LIST_UNIVERSITIES:
            # Extract unique universities from results
            unique_universities = {}
            for result in db_results:
                uni_id = None
                uni_name = None
                teaching_lang = None
                major_name = None
                degree_level = None
                
                if isinstance(result, dict):
                    uni_id = result.get("university_id")
                    uni_name = result.get("university_name")
                    teaching_lang = result.get("teaching_language")
                    major_name = result.get("major_name") or result.get("sample_intake", {}).get("major_name")
                    degree_level = result.get("degree_level")
                elif hasattr(result, 'university') and result.university:
                    uni_id = result.university_id
                    uni_name = result.university.name
                    if hasattr(result, 'major') and result.major:
                        major_name = result.major.name
                        degree_level = result.major.degree_level
                        teaching_lang = result.teaching_language or result.major.teaching_language
                elif hasattr(result, 'university_id') and result.university_id:
                    uni_id = result.university_id
                    from app.models import University
                    uni = self.db.query(University).filter(University.id == uni_id).first()
                    if uni:
                        uni_name = uni.name
                
                if uni_id and uni_name:
                    if uni_id not in unique_universities:
                        unique_universities[uni_id] = {
                            "name": uni_name,
                            "teaching_language": teaching_lang,
                            "major_name": major_name,
                            "degree_level": degree_level
                        }
            
            # If >6 universities, show names only and ask user to choose
            if len(unique_universities) > 6:
                response_parts = [f"I found {len(unique_universities)} universities matching your criteria. Please choose one to see specific details:\n"]
                # Store university candidates for selection
                uni_candidates = []
                uni_candidate_ids = []
                sorted_unis = sorted(unique_universities.items(), key=lambda x: x[1]["name"])
                
                for idx, (uni_id, uni_info) in enumerate(sorted_unis, 1):
                    uni_line = f"{idx}. {uni_info['name']}"
                    if uni_info.get("teaching_language"):
                        uni_line += f" ({uni_info['teaching_language']}-taught)"
                    if uni_info.get("major_name"):
                        uni_line += f" - {uni_info['major_name']}"
                    elif uni_info.get("degree_level"):
                        uni_line += f" ({uni_info['degree_level']})"
                    response_parts.append(uni_line)
                    # Store candidates for selection
                    uni_candidates.append(uni_info['name'])
                    uni_candidate_ids.append(uni_id)
                
                response_parts.append("\nPlease specify which university you'd like information about (e.g., '1' or university name).")
                
                # Store university candidates in state and set pending slot
                if state and partner_id and conversation_id:
                    state._university_candidates = uni_candidates
                    state._university_candidate_ids = uni_candidate_ids
                    # CRITICAL: Cache the state BEFORE setting pending slot
                    self._set_cached_state(partner_id, conversation_id, state, None)
                    self._set_pending(partner_id, conversation_id, "university_choice", state)
                    print(f"DEBUG: Set university_choice pending slot with {len(uni_candidates)} candidates: {uni_candidates}")
                
                return {
                    "response": "\n".join(response_parts),
                    "used_db": True,
                    "used_tavily": False,
                    "sources": []
                }
        
        # Check if there are 3+ universities - if so, ask user to choose instead of showing all details
        if db_results and intent in [self.router.INTENT_ADMISSION_REQUIREMENTS, "REQUIREMENTS", "ADMISSION_REQUIREMENTS"]:
            unique_universities = {}
            for result in db_results:
                uni_id = None
                uni_name = None
                if hasattr(result, 'university') and result.university:
                    uni_id = result.university_id
                    uni_name = result.university.name
                elif hasattr(result, 'university_id') and result.university_id:
                    uni_id = result.university_id
                    # Try to get university name from ID
                    from app.models import University
                    uni = self.db.query(University).filter(University.id == uni_id).first()
                    if uni:
                        uni_name = uni.name
                
                if uni_id and uni_name:
                    if uni_id not in unique_universities:
                        unique_universities[uni_id] = uni_name
            
            if len(unique_universities) >= 3:
                # Store university candidates for selection
                uni_candidates = []
                uni_candidate_ids = []
                sorted_unis = sorted(unique_universities.items(), key=lambda x: x[1])
                
                university_list_parts = []
                for idx, (uni_id, uni_name) in enumerate(sorted_unis, 1):
                    university_list_parts.append(f"{idx}. {uni_name}")
                    uni_candidates.append(uni_name)
                    uni_candidate_ids.append(uni_id)
                
                university_list = "\n".join(university_list_parts)
                
                # Store university candidates in state and set pending slot
                if state and partner_id and conversation_id:
                    state._university_candidates = uni_candidates
                    state._university_candidate_ids = uni_candidate_ids
                    # CRITICAL: Cache the state BEFORE setting pending slot
                    self._set_cached_state(partner_id, conversation_id, state, None)
                    self._set_pending(partner_id, conversation_id, "university_choice", state)
                    print(f"DEBUG: Set university_choice pending slot for REQUIREMENTS with {len(uni_candidates)} candidates: {uni_candidates}")
                
                return {
                    "response": f"I found information for {len(unique_universities)} universities. Please choose one to see detailed requirements:\n\n{university_list}\n\nPlease specify which university you'd like information about (e.g., '1' or university name).",
                    "used_db": True,
                    "used_tavily": False,
                    "sources": []
                }
        
        # For single result or small results, use LLM formatting
        try:
            print(f"DEBUG: Formatting answer with LLM...")
            intent = state.intent if state else "general"
            wants_fees = state.wants_fees if state else False
            formatted_response = self.format_answer_with_llm(
                db_context=db_context,
                user_question=user_message,
                intent=intent,
                req_focus=req_focus,
                wants_fees=wants_fees  # Pass wants_fees for fee comparison queries
            )
            print(f"DEBUG: LLM formatting completed, response length={len(formatted_response)} chars")
        except Exception as e:
            import traceback
            print(f"ERROR: format_answer_with_llm() failed: {e}")
            traceback.print_exc()
            # Fallback response
            formatted_response = "I found information in the database, but encountered an error formatting the response. Please try rephrasing your question."
        
        # CRITICAL: Cache the state after generating response to preserve context for follow-up questions
        if state and partner_id and conversation_id:
            # Only cache if there's no pending slot (pending slots are cached separately)
            cached = self._get_cached_state(partner_id, conversation_id)
            if not cached or not cached.get("pending"):
                self._set_cached_state(partner_id, conversation_id, state, None)
                print(f"DEBUG: Cached state after response generation (no pending slot)")
        
        return {
            "response": formatted_response,
            "used_db": True,
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
        Uses lightweight cache or DBQueryService.
        """
        lower = text.lower()
        # Use lightweight cache for alias checking
        uni_cache = self._get_uni_name_cache()
        for uni in uni_cache:
            aliases = [uni.get("name"), uni.get("name_cn")] + (uni.get("aliases") or [])
            for alias in aliases:
                if alias and str(alias).lower() in lower:
                    # Get full university info from DB
                    full_uni = self.db.query(University).filter(University.id == uni["id"]).first()
                    if full_uni:
                        return {
                            "id": full_uni.id,
                            "name": full_uni.name,
                            "name_cn": full_uni.name_cn,
                            "aliases": full_uni.aliases if isinstance(full_uni.aliases, list) else (json.loads(full_uni.aliases) if isinstance(full_uni.aliases, str) else []),
                            "city": full_uni.city,
                            "province": full_uni.province
                        }
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
        # Use DBQueryService to search majors by topic
        majors = self.db_service.search_majors(degree_level=degree_level, limit=limit * 2)  # Get more for filtering
        for m in majors:
            if degree_level and m.degree_level and degree_level.lower() not in str(m.degree_level).lower():
                continue
            kws = self._normalize_keywords(m.keywords)
            if topic_hits(m.name, kws):
                matched.append(m.id)
                matched_names.append(m.name)
                if len(matched) >= limit:
                    break
        
        if matched:
            print(f"DEBUG: _find_major_ids_by_topic matched {len(matched)} majors for topic '{topic_text}': {matched_names[:10]}")
        else:
            print(f"DEBUG: _find_major_ids_by_topic found NO matches for topic '{topic_text}' (tokens: {tokens})")
        return matched


# Lightweight test function for parse_query_rules
if __name__ == "__main__":
    # Create a minimal PartnerAgent instance for testing (without DB)
    class MockDB:
        pass
    
    class MockOpenAI:
        pass
    
    class MockTavily:
        pass
    
    # Create minimal agent instance
    agent = PartnerAgent.__new__(PartnerAgent)
    # Removed: agent.all_universities and agent.all_majors - now using lazy cached loaders
    # agent._universities_cache and agent._majors_cache are loaded only when needed
    
    # Test queries
    test_queries = [
        "I want Chinese language one semester program, March 2026 intake, tuition and application fee",
        "List universities in Guangzhou with English taught programs",
        "Ningxia Medical University Chinese Language March 2026, scholarship and required documents",
        "next page",
        "Show majors of Xidian University",
        "Masters in Pharmacy in any university, September 2026, scholarship?",
        "What documents do I need for MBBS?",
        "deadline for March intake?"
    ]
    
    print("=" * 80)
    print("Testing parse_query_rules() function")
    print("=" * 80)
    
    for query in test_queries:
        print(f"\nQuery: {query}")
        result = agent.parse_query_rules(query)
        print(f"Result: {result}")
        print("-" * 80)

