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
    
        # Unified state cache: keyed by (partner_id, conversation_id) -> {"state": PartnerQueryState, "pending": PendingInfo, "ts": float}
        # PendingInfo = {"slot": str, "snapshot": dict}
        self._state_cache: Dict[Tuple[Optional[int], str], Dict[str, Any]] = {}
        self._state_cache_ttl: float = 1800.0  # 30 minutes TTL
        
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
        print(f"DEBUG: _get_cached_state - key={key}, cache_keys={list(self._state_cache.keys())}")
        if key in self._state_cache:
            cached = self._state_cache[key]
            age = time.time() - cached.get("ts", 0)
            if age < self._state_cache_ttl:
                print(f"DEBUG: _get_cached_state - found cached state (age={age:.1f}s, pending={cached.get('pending') is not None})")
                return cached
            else:
                print(f"DEBUG: _get_cached_state - cached state expired (age={age:.1f}s > {self._state_cache_ttl}s)")
                del self._state_cache[key]
        else:
            print(f"DEBUG: _get_cached_state - key not found in cache")
        return None
    
    def _set_cached_state(self, partner_id: Optional[int], conversation_id: Optional[str], 
                         state: PartnerQueryState, pending: Optional[Dict[str, Any]] = None):
        """Cache unified state for conversation (state + pending info)"""
        if not conversation_id:
            return
        key = (partner_id, conversation_id)
        self._state_cache[key] = {
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
                "wants_requirements": snapshot.wants_requirements,
                "wants_deadline": getattr(snapshot, 'wants_deadline', False),
                "city": snapshot.city,
                "province": snapshot.province,
                "country": snapshot.country,
                "budget_max": snapshot.budget_max,
                "duration_years_target": snapshot.duration_years_target,
                "duration_constraint": snapshot.duration_constraint,
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
- For teaching_language: Extract "English" from phrases like "English", "English taught", "English-taught", "taught in English", "English program". Extract "Chinese" from "Chinese", "Chinese taught", "Mandarin", "中文".
- For deadline questions (e.g., "application deadline", "when is the deadline"), set wants_deadline=true and intent can be GENERAL if no other intent is clear.
- For fee-related questions (e.g., "tuition fee", "tuition", "fee", "cost", "price", "bank statement amount"), set wants_fees=true and intent=FEES.
- For scholarship questions (e.g., "scholarship", "scholarship info", "scholarship opportunity"), set wants_scholarship=true and intent=SCHOLARSHIP.
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
        
        # Acronym expansion map (includes ECE, BBA, LLB, etc.)
        acronym_map = {
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
            "chem": "chemistry",
            "bio": "biology",
            "phy": "physics",
            "math": "mathematics",
            "econ": "economics",
            "fin": "finance",
            "bba": "bachelor of business administration",
            "mba": "master of business administration",
            "llb": "bachelor of laws",
        }
        
        # Check if text is short (<=6 chars) or all-caps-like (mostly uppercase)
        is_short = len(text.replace(' ', '')) <= 6
        is_caps_like = len([c for c in text if c.isupper()]) > len([c for c in text if c.islower()]) if text else False
        
        if is_short or is_caps_like:
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
        language_patterns = ["language", "non-degree", "foundation", "preparatory"]
        if any(pat in text_norm for pat in language_patterns):
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
        elif re.search(r'\b(language\s+program|language\s+course|non-?degree|foundation|foundation\s+program)\b', lower):
            result["degree_level"] = "Language"
        elif re.search(r'\b(diploma|associate|assoc)\b', lower):
            result["degree_level"] = "Diploma"
        
        # Teaching language detection
        if re.search(r'\b(english|english-?taught|english\s+program)\b', lower):
            result["teaching_language"] = "English"
        elif re.search(r'\b(chinese|chinese-?taught|chinese\s+program|mandarin)\b', lower):
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
        if re.search(r'\b(scholarship|waiver|type-?a|type-?b|type-?c|type-?d|partial|stipend|how\s+to\s+get|how\s+can\s+i\s+get)\b', lower):
            result["wants_scholarship"] = True
            result["intent"] = "scholarship_only"
        if re.search(r'\b(document|documents|required\s+documents?|doc\s+list|paper|papers|materials|what\s+doc|what\s+documents?)\b', lower):
            result["wants_documents"] = True
            result["intent"] = "documents_only"
        if re.search(r'\b(fee|fees|tuition|cost|price|how\s+much|budget|per\s+year|per\s+month|application\s+fee)\b', lower):
            result["wants_fees"] = True
            if result["intent"] == "general":
                result["intent"] = "fees_only"
        if re.search(r'\b(deadline|when|application\s+deadline|last\s+date|due\s+date)\b', lower):
            result["wants_deadline"] = True
        if re.search(r'\b(cheapest|lowest|lowest\s+fees?|lowest\s+tuition|less\s+fee|low\s+fee|lowest\s+cost|less\s+cost|compare|comparison)\b', lower):
            result["intent"] = "fees_compare"
        if re.search(r'\b(list|show|all|available|what\s+programs?|which\s+programs?|programs?\s+available|majors?\s+available)\b', lower):
            result["wants_list"] = True
            result["intent"] = "list_programs"
        
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
                    # Check if it's not just a city/province
                    city_province_keywords = ["guangzhou", "beijing", "shanghai", "guangdong", "jiangsu", "zhejiang"]
                    if cleaned.lower() not in city_province_keywords:
                        result["major_raw"] = cleaned
        else:
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
                # Check if it's not just a city/province
                city_province_keywords = ["guangzhou", "beijing", "shanghai", "guangdong", "jiangsu", "zhejiang"]
                if cleaned.lower() not in city_province_keywords:
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
        
        # If user is accepting a suggestion, extract the major name from the message
        accepted_major = None
        if is_accepting_suggestion:
            # Try to extract major name from the message
            major_match = re.search(r'\b(applied\s+physics|physics|computer\s+science|business\s+administration|engineering|artificial\s+intelligence)\b', latest_user_message.lower())
            if major_match:
                accepted_major = major_match.group(1)
                print(f"DEBUG: User accepted suggested major: '{accepted_major}'")
        
        # Check for pending slot using unified state cache
        pending_info = self._get_pending(partner_id, conversation_id)
        print(f"DEBUG: Pending check - pending_info={pending_info is not None}, slot={pending_info.get('slot') if pending_info else None}")
        
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
            state.wants_requirements = snapshot.get("wants_requirements", False)
            state.wants_deadline = snapshot.get("wants_deadline", False)
            state.wants_list = snapshot.get("wants_list", False)
            state.wants_earliest = snapshot.get("wants_earliest", False)
            state.city = snapshot.get("city")
            state.province = snapshot.get("province")
            state.country = snapshot.get("country")
            state.intake_year = snapshot.get("intake_year")
            state.duration_years_target = snapshot.get("duration_years_target")
            state.duration_constraint = snapshot.get("duration_constraint")
            state.budget_max = snapshot.get("budget_max")
            
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
                    self._consume_pending(partner_id, conversation_id)  # Clear pending
                else:
                    # Keep pending if can't parse
                    return state
            elif pending_slot == "degree_level":
                degree = self.parse_degree_level(latest_user_message)
                if degree:
                    state.degree_level = degree
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
                    self._consume_pending(partner_id, conversation_id)
                else:
                    return state
            elif pending_slot == "major_choice":
                # User selected from numbered list (1/2/3)
                num_match = re.match(r'^(\d+)$', latest_user_message.strip())
                if num_match:
                    # This would need to be stored in pending_info for disambiguation
                    # For now, treat as major_query
                    self._consume_pending(partner_id, conversation_id)
                else:
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
                    return state
            
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
            # Get previous assistant message to extract context
            prev_assistant_msg = ""
            for msg in reversed(conversation_history):
                if msg.get('role') == 'assistant':
                    prev_assistant_msg = msg.get('content', '')
                    break
            
            # Check if previous message suggested majors
            if "closest available majors" in prev_assistant_msg.lower() or "couldn't find" in prev_assistant_msg.lower():
                # This is a follow-up to a "couldn't find" message - restore previous state and use accepted major
                # Try to get previous state from cache
                cached = self._get_cached_state(partner_id, conversation_id)
                if cached and cached.get("state"):
                    prev_state_obj = cached.get("state")
                    # Create new state with accepted major
                    state = PartnerQueryState()
                    state.intent = prev_state_obj.intent if hasattr(prev_state_obj, 'intent') else "SCHOLARSHIP"
                    state.degree_level = prev_state_obj.degree_level if hasattr(prev_state_obj, 'degree_level') else None
                    state.intake_term = prev_state_obj.intake_term if hasattr(prev_state_obj, 'intake_term') else None
                    state.teaching_language = prev_state_obj.teaching_language if hasattr(prev_state_obj, 'teaching_language') else None
                    state.wants_scholarship = prev_state_obj.wants_scholarship if hasattr(prev_state_obj, 'wants_scholarship') else False
                    state.major_query = accepted_major  # Use accepted major
                    state.wants_earliest = prev_state_obj.wants_earliest if hasattr(prev_state_obj, 'wants_earliest') else False
                    state.intake_year = prev_state_obj.intake_year if hasattr(prev_state_obj, 'intake_year') else None
                    
                    # Restore focus objects
                    if hasattr(prev_state_obj, 'req_focus'):
                        state.req_focus = prev_state_obj.req_focus
                    if hasattr(prev_state_obj, 'scholarship_focus'):
                        state.scholarship_focus = prev_state_obj.scholarship_focus
                    
                    print(f"DEBUG: Restored state with accepted major: {state.major_query}, intent={state.intent}")
                    # Cache the updated state
                    self._set_cached_state(partner_id, conversation_id, state, None)
                    return state
                else:
                    print(f"DEBUG: Could not restore cached state for accepted major - falling through to fresh query")
        
        # FRESH QUERY: Use ALWAYS-LLM extraction
        try:
            print(f"DEBUG: Calling llm_extract_state() for fresh query...")
            extracted = self.llm_extract_state(conversation_history, date.today(), prev_state)
            print(f"DEBUG: LLM extracted: intent={extracted.get('intent')}, confidence={extracted.get('confidence')}")
            
            # Convert extracted dict to PartnerQueryState
            state = PartnerQueryState()
            state.intent = extracted.get("intent", "GENERAL")
            state.confidence = extracted.get("confidence", 0.5)
            
            # Map extracted fields to state
            state.degree_level = extracted.get("degree_level")
            state.major_query = extracted.get("major_raw")  # Will be resolved later
            state.university_query = extracted.get("university_raw")  # Will be resolved later
            state.intake_term = extracted.get("intake_term")
            state.intake_year = extracted.get("intake_year")
            # Parse teaching_language from extracted or from latest message if not extracted
            state.teaching_language = extracted.get("teaching_language")
            if not state.teaching_language:
                # Try to parse from latest message (handles "English taught", "english-taught", etc.)
                state.teaching_language = self.parse_teaching_language(latest_user_message)
            
            # Extract city and province (with fuzzy matching fallback from parse_query_rules)
            state.city = extracted.get("city")
            state.province = extracted.get("province")
            # Fallback: if LLM didn't extract city/province, try parse_query_rules (handles typos and "in X" patterns)
            if not state.city and not state.province:
                rules = self.parse_query_rules(latest_user_message)
                if rules.get("city"):
                    state.city = rules.get("city")
                    print(f"DEBUG: Extracted city from parse_query_rules: {state.city}")
                if rules.get("province"):
                    state.province = rules.get("province")
                    print(f"DEBUG: Extracted province from parse_query_rules: {state.province}")
            elif state.city:
                print(f"DEBUG: Extracted city from LLM: {state.city}")
            elif state.province:
                print(f"DEBUG: Extracted province from LLM: {state.province}")
            
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
            state.wants_scholarship = extracted.get("wants_scholarship", False)
            state.wants_requirements = extracted.get("wants_requirements", False)
            state.wants_fees = extracted.get("wants_fees", False)
            state.wants_deadline = extracted.get("wants_deadline", False)
            # Fallback: parse from latest message if LLM didn't extract it
            if not state.wants_deadline:
                rules = self.parse_query_rules(latest_user_message)
                state.wants_deadline = rules.get("wants_deadline", False)
            
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
            
            # CONTEXT PRESERVATION: Always preserve key fields (major, university, teaching_language, intake, degree_level)
            # unless user explicitly changes them or changes intent in a way that doesn't make sense
            # Check if user is changing intent (e.g., "what about fees?", "does it require bank_statement?")
            is_changing_intent = (
                state.intent != prev_state.intent if prev_state else False
            ) or any(kw in latest_user_message.lower() for kw in ["what about", "how about", "does it require", "does it need", "is", "are"])
            
            # Check if this is a list query that should preserve context
            is_list_query = state.intent in [self.router.INTENT_LIST_PROGRAMS, self.router.INTENT_LIST_UNIVERSITIES]
            
            # Always try to preserve context from cached state if available (even if prev_state is None)
            if partner_id and conversation_id:
                cached = self._get_cached_state(partner_id, conversation_id)
                if cached and cached.get("state"):
                    prev_cached_state = cached.get("state")
                    # Also preserve if this is a slot reply (like "Bachelor" to a clarification question)
                    is_slot_reply = len(latest_user_message.split()) <= 2 and any(
                        kw in latest_user_message.lower() for kw in ["bachelor", "master", "phd", "language", "march", "september", "english", "chinese"]
                    )
                    
                    # ALWAYS preserve context - key fields (major, university, teaching_language, intake, degree_level)
                    # should be preserved until user explicitly changes them
                    # This ensures context carries across queries until user explicitly changes intent or fields
                    should_preserve = True  # Always preserve if we have cached state
                    
                    if should_preserve:
                        print(f"DEBUG: Preserving context from previous query - intent change={is_changing_intent}, slot_reply={is_slot_reply}, list_query={is_list_query}, missing fields detected")
                        # Preserve context fields if not explicitly mentioned in current query
                        # For slot replies and list queries, always preserve ALL context fields from previous query
                        if is_slot_reply or is_list_query:
                            # For slot replies and list queries, preserve ALL context fields from previous query
                            context_type = "list query" if is_list_query else "slot reply"
                            if hasattr(prev_cached_state, 'major_query') and prev_cached_state.major_query:
                                state.major_query = prev_cached_state.major_query
                                print(f"DEBUG: Preserved major_query ({context_type}): {state.major_query}")
                            if hasattr(prev_cached_state, 'degree_level') and prev_cached_state.degree_level:
                                state.degree_level = prev_cached_state.degree_level
                                print(f"DEBUG: Preserved degree_level ({context_type}): {state.degree_level}")
                            if hasattr(prev_cached_state, 'intake_term') and prev_cached_state.intake_term:
                                state.intake_term = prev_cached_state.intake_term
                                print(f"DEBUG: Preserved intake_term ({context_type}): {state.intake_term}")
                            if hasattr(prev_cached_state, 'intake_year') and prev_cached_state.intake_year:
                                state.intake_year = prev_cached_state.intake_year
                                print(f"DEBUG: Preserved intake_year ({context_type}): {state.intake_year}")
                            if hasattr(prev_cached_state, 'teaching_language') and prev_cached_state.teaching_language:
                                state.teaching_language = prev_cached_state.teaching_language
                                print(f"DEBUG: Preserved teaching_language ({context_type}): {state.teaching_language}")
                            # Only preserve university_query if user didn't explicitly mention a new one
                            # Check if the current message contains a university mention
                            has_university_mention = False
                            if latest_user_message:
                                # Check for common university patterns
                                uni_mention_patterns = [
                                    r'\b(what|how)\s+about\s+([A-Z][A-Z0-9]+|[A-Z][a-z]+)',
                                    r'\b([A-Z][A-Z0-9]+|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(university|institute|college)',
                                    r'^([A-Z][A-Z0-9]+)\s*[?]?$',  # Just "LNPU?" or "HIT?"
                                ]
                                for pattern in uni_mention_patterns:
                                    if re.search(pattern, latest_user_message, re.IGNORECASE):
                                        has_university_mention = True
                                        break
                            
                            if not has_university_mention and hasattr(prev_cached_state, 'university_query') and prev_cached_state.university_query:
                                state.university_query = prev_cached_state.university_query
                                print(f"DEBUG: Preserved university_query ({context_type}): {state.university_query}")
                            elif has_university_mention:
                                print(f"DEBUG: Detected university mention in message ({context_type}), not preserving old university_query")
                            if hasattr(prev_cached_state, 'wants_deadline') and prev_cached_state.wants_deadline:
                                state.wants_deadline = prev_cached_state.wants_deadline
                                print(f"DEBUG: Preserved wants_deadline ({context_type}): {state.wants_deadline}")
                            if hasattr(prev_cached_state, '_resolved_major_ids') and prev_cached_state._resolved_major_ids:
                                state._resolved_major_ids = prev_cached_state._resolved_major_ids
                                print(f"DEBUG: Preserved _resolved_major_ids ({context_type}): {state._resolved_major_ids}")
                            if hasattr(prev_cached_state, '_resolved_university_id') and prev_cached_state._resolved_university_id:
                                state._resolved_university_id = prev_cached_state._resolved_university_id
                                print(f"DEBUG: Preserved _resolved_university_id ({context_type}): {state._resolved_university_id}")
                        else:
                            # For non-slot replies, only preserve if not explicitly mentioned
                            if not state.major_query and hasattr(prev_cached_state, 'major_query') and prev_cached_state.major_query:
                                state.major_query = prev_cached_state.major_query
                                print(f"DEBUG: Preserved major_query: {state.major_query}")
                            if not state.degree_level and hasattr(prev_cached_state, 'degree_level') and prev_cached_state.degree_level:
                                state.degree_level = prev_cached_state.degree_level
                                print(f"DEBUG: Preserved degree_level: {state.degree_level}")
                            if not state.intake_term and hasattr(prev_cached_state, 'intake_term') and prev_cached_state.intake_term:
                                state.intake_term = prev_cached_state.intake_term
                                print(f"DEBUG: Preserved intake_term: {state.intake_term}")
                            if not state.intake_year and hasattr(prev_cached_state, 'intake_year') and prev_cached_state.intake_year:
                                state.intake_year = prev_cached_state.intake_year
                                print(f"DEBUG: Preserved intake_year: {state.intake_year}")
                            if not state.teaching_language and hasattr(prev_cached_state, 'teaching_language') and prev_cached_state.teaching_language:
                                state.teaching_language = prev_cached_state.teaching_language
                                print(f"DEBUG: Preserved teaching_language: {state.teaching_language}")
                            # Only preserve university_query if user didn't explicitly mention a new one
                            # Check if the current message contains a university mention
                            has_university_mention = False
                            if latest_user_message:
                                # Check for common university patterns
                                uni_mention_patterns = [
                                    r'\b(what|how)\s+about\s+([A-Z][A-Z0-9]+|[A-Z][a-z]+)',
                                    r'\b([A-Z][A-Z0-9]+|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(university|institute|college)',
                                    r'^([A-Z][A-Z0-9]+)\s*[?]?$',  # Just "LNPU?" or "HIT?"
                                ]
                                for pattern in uni_mention_patterns:
                                    if re.search(pattern, latest_user_message, re.IGNORECASE):
                                        has_university_mention = True
                                        break
                            
                            if not state.university_query and not has_university_mention and hasattr(prev_cached_state, 'university_query') and prev_cached_state.university_query:
                                state.university_query = prev_cached_state.university_query
                                print(f"DEBUG: Preserved university_query: {state.university_query}")
                            elif has_university_mention:
                                print(f"DEBUG: Detected university mention in message, not preserving old university_query")
                            # Preserve wants_deadline flag (important for deadline queries)
                            if hasattr(prev_cached_state, 'wants_deadline') and prev_cached_state.wants_deadline:
                                state.wants_deadline = prev_cached_state.wants_deadline
                                print(f"DEBUG: Preserved wants_deadline: {state.wants_deadline}")
                            # Preserve resolved IDs if available
                            if hasattr(prev_cached_state, '_resolved_major_ids') and prev_cached_state._resolved_major_ids:
                                state._resolved_major_ids = prev_cached_state._resolved_major_ids
                                print(f"DEBUG: Preserved _resolved_major_ids: {state._resolved_major_ids}")
                            if hasattr(prev_cached_state, '_resolved_university_id') and prev_cached_state._resolved_university_id:
                                state._resolved_university_id = prev_cached_state._resolved_university_id
                                print(f"DEBUG: Preserved _resolved_university_id: {state._resolved_university_id}")
                            # Preserve city and province context (only if not explicitly mentioned in current query)
                            if not state.city and hasattr(prev_cached_state, 'city') and prev_cached_state.city:
                                state.city = prev_cached_state.city
                                print(f"DEBUG: Preserved city: {state.city}")
                            if not state.province and hasattr(prev_cached_state, 'province') and prev_cached_state.province:
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
                    # Look for previous user messages that might contain context
                    prev_messages = [msg for msg in conversation_history[-6:] if msg.get('role') == 'user']
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
                        # If we found any context, break
                        if state.university_query or state.degree_level or state.intake_term or state.major_query:
                            print(f"DEBUG: Extracted context from conversation history: university={state.university_query}, degree={state.degree_level}, intake={state.intake_term}, major={state.major_query}")
                            break
            
            # Detect specific requirement questions (e.g., "bank_statement", "age", "hsk", "english test")
            req_keywords = {
                "bank": ["bank", "bank statement", "bank_statement", "financial", "funds"],
                "age": ["age", "minimum age", "age requirement"],
                "hsk": ["hsk", "chinese test", "chinese language test"],
                "english": ["english test", "ielts", "toefl", "english requirement"],
                "documents": ["document", "documents", "required documents", "application documents"],
                "accommodation": ["accommodation", "housing", "dormitory", "dorm"]
            }
            latest_lower = latest_user_message.lower()
            for req_type, keywords in req_keywords.items():
                if any(kw in latest_lower for kw in keywords):
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
            
            # DETERMINISTIC RESOLUTION: Resolve university_raw and major_raw to IDs
            # Only resolve if we didn't already resolve it from pattern matching above
            if state.university_query and not has_university_pattern:
                matched, uni_dict, _ = self._fuzzy_match_university(state.university_query)
                if matched and uni_dict:
                    # Store resolved university_id (will be used in build_sql_params)
                    state.university_query = uni_dict.get("name")  # Keep name for reference
                    state._resolved_university_id = uni_dict.get("id")  # Store ID for SQL
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
            
            if state.major_query:
                # Resolve major_query to major_ids
                # For language programs, use much higher limit (100) to get all language programs
                # Don't limit language programs - we need all of them to check durations and show options
                limit = 100 if state.degree_level == "Language" else 6
                major_ids = self.resolve_major_ids(
                    major_query=state.major_query,
                    degree_level=state.degree_level,
                    teaching_language=state.teaching_language,
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
            
            if self._is_semantic_stopword(state.major_query):
                print(f"DEBUG: Clearing major_query '{state.major_query}' - semantic stopword")
                state.major_query = None
            # Check if it's a degree word, BUT skip this check if it's a known major acronym
            elif not is_known_acronym:
                # Only check for degree words if it's NOT a known acronym
                matched_degree = self.router._fuzzy_match_degree_level(state.major_query)
                if matched_degree:
                    print(f"DEBUG: Clearing major_query '{state.major_query}' - it's a degree word ({matched_degree})")
                    if not state.degree_level:
                        state.degree_level = matched_degree
                    state.major_query = None
            else:
                # It's a known acronym - use the expanded version for better matching
                print(f"DEBUG: Major query '{state.major_query}' is a known acronym, expanded to '{expanded_major}'")
                state.major_query = expanded_major
        
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
            
            if should_check_duration:
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
                    # Get all majors and check for distinct durations
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
                    
                    # If multiple distinct durations exist, ask for clarification
                    if len(distinct_durations) > 1:
                        print(f"DEBUG: Language program has multiple durations: {sorted(distinct_durations)}")
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
                        return {
                            "intent": state.intent,
                            "state": state,
                            "needs_clarification": True,
                            "clarifying_question": clarifying_question,
                            "sql_plan": None,
                            "req_focus": {}
                        }
        
        # Check if major resolution is ambiguous (low confidence or too many candidates)
        if state.major_query and not hasattr(state, '_resolved_major_ids') and not needs_clar:
            # Try to resolve with lower threshold to see all candidates
            major_ids = self.resolve_major_ids(
                major_query=state.major_query,
                degree_level=state.degree_level,
                teaching_language=state.teaching_language,
                limit=6,  # Get more candidates for disambiguation
                confidence_threshold=0.6  # Lower threshold to see all candidates
            )
            if len(major_ids) > 3:
                # Too many candidates - check if they're all the same major name
                candidates = []
                for mid in major_ids[:6]:
                    major_name = self._get_major_name_by_id(mid)
                    candidates.append(major_name)
                
                # Check if all candidates have the same name (case-insensitive)
                if candidates:
                    unique_names = set(name.lower().strip() for name in candidates)
                    if len(unique_names) == 1:
                        # All candidates are the same major - use them without asking
                        print(f"DEBUG: All {len(major_ids)} candidates have the same name '{candidates[0]}', using them without clarification")
                        state._resolved_major_ids = major_ids
                    else:
                        # Different major names - ask user to pick
                        needs_clar = True
                        missing_slots = ["major_choice"]
                        # Remove duplicates while preserving order
                        seen = set()
                        unique_candidates = []
                        for c in candidates:
                            c_lower = c.lower().strip()
                            if c_lower not in seen:
                                seen.add(c_lower)
                                unique_candidates.append(c)
                        clarifying_question = f"Which major did you mean? " + " ".join([f"{i+1}) {c}" for i, c in enumerate(unique_candidates)])
                        # Store candidates in state for later retrieval
                        state._major_candidates = unique_candidates
                        state._major_candidate_ids = major_ids[:6]
        
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
                    missing_slots = ["major_or_university"] if not state.major_query and not state.university_query else ["intake_term"]
                    clarifying_question = "Please specify: major/subject, university name, or intake term (March/September)."
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
            # Need at least ONE filter
            has_filter = (
                state.degree_level or state.major_query or state.city or state.province or
                state.teaching_language or state.intake_term or state.wants_earliest or
                state.university_query
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
                    missing_slots.append("major_query")
                    question_parts.append("subject/major")
        
        elif intent == self.router.INTENT_SCHOLARSHIP:
            # SCHOLARSHIP clarification rules (FIXED: no scholarship_bundle, default to any scholarship)
            # Default scholarship_focus.any = True (handled in state initialization)
            
            # Require degree_level + (major_query OR university_query OR city/province)
            if not state.degree_level:
                missing_slots.append("degree_level")
                question_parts.append("degree level (Language/Bachelor/Master/PhD)")
            
            # If degree_level present but no narrowing filter: ask for major/university/city
            if state.degree_level and not state.major_query and not state.university_query and not state.city and not state.province:
                if "degree_level" not in missing_slots:
                    missing_slots.append("major_or_university")
                    question_parts.append("major/subject, university name, or city")
            
            # Intake_term is optional but preferred
            # teaching_language: will be asked AFTER DB check if both exist (handled separately)
        
        elif intent == self.router.INTENT_ADMISSION_REQUIREMENTS:
            # Need: either university_query OR (major_query + degree_level) OR program_intake from last list
            has_target = (
                state.university_query or
                (state.major_query and state.degree_level) or
                self.last_selected_program_intake_id
            )
            if not has_target:
                missing_slots.append("target")
                return missing_slots, "Which program should I check? (university name OR major+degree+intake)"
        
        elif intent in [self.router.INTENT_FEES, self.router.INTENT_COMPARISON]:
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
                    missing_fields.append("degree level")
                if not state.intake_term and not state.wants_earliest:
                    missing_fields.append("intake term")
                missing_slots.append("target")
                return missing_slots, f"Please provide: {', '.join(missing_fields)}"
        
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
    
    def run_db(self, route_plan: Dict[str, Any], latest_user_message: Optional[str] = None) -> List[Any]:
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
            # Use list_universities_by_filters
            results = self.db_service.list_universities_by_filters(
                city=state.city if state else None,
                province=state.province if state else None,
                degree_level=sql_params.get("degree_level"),
                teaching_language=sql_params.get("teaching_language"),
                intake_term=sql_params.get("intake_term"),
                intake_year=sql_params.get("intake_year"),
                duration_years_target=sql_params.get("duration_years_target"),
                duration_constraint=sql_params.get("duration_constraint"),
                upcoming_only=sql_params.get("upcoming_only", True),
                limit=sql_params.get("limit", 10)
            )
            return results
        
        elif intent in [self.router.INTENT_LIST_PROGRAMS, self.router.INTENT_FEES, 
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
                    # Check if major was likely mentioned in current query (not just from prev_state)
                    # For now, include it but we'll relax other filters
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
                if sql_params.get("teaching_language"):
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
            try:
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
                    
                    # Fallback 0b: For language programs, if we have limited major_ids, try expanding to all Language majors
                    if state and state.degree_level == "Language" and filters.get("major_ids"):
                        print(f"DEBUG: Language program with limited major_ids ({len(filters['major_ids'])}), trying all Language majors...")
                        # Get all Language majors
                        all_language_majors = self.db_service.search_majors(
                            degree_level="Language",
                            limit=200
                        )
                        if all_language_majors:
                            all_language_ids = [m.id for m in all_language_majors]
                            fallback_filters["major_ids"] = all_language_ids
                            fallback_intakes, _ = self.db_service.find_program_intakes(
                                filters=fallback_filters, limit=limit, offset=offset, order_by=order_by
                            )
                            if len(fallback_intakes) > 0:
                                print(f"DEBUG: Fallback - expanded to {len(all_language_ids)} Language majors, found {len(fallback_intakes)} intakes")
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
            
                    # Fallback 2: If intake_term causes 0 results and user didn't explicitly demand it, retry without term
                    if not fallback_applied and filters.get("intake_term") and state:
                        # Check if user explicitly mentioned the term in original query
                        # (This is a heuristic - in practice you'd track if term was explicit)
                        fallback_filters.pop("intake_term", None)
                        fallback_intakes, _ = self.db_service.find_program_intakes(
                            filters=fallback_filters, limit=limit, offset=offset, order_by=order_by
                        )
                        if len(fallback_intakes) > 0:
                            # Get distinct intake terms from results
                            available_terms = set()
                            for intake in fallback_intakes:
                                if hasattr(intake, 'intake_term') and intake.intake_term:
                                    available_terms.add(str(intake.intake_term))
                            return {
                                "_fallback": True,
                                "_fallback_type": "intake_term",
                                "_available_terms": list(available_terms),
                                "_intakes": fallback_intakes
                            }
                    
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
                    
                    # If still 0, return empty but mark for fallback message
                    return {
                        "_fallback": True,
                        "_fallback_type": "no_results",
                        "_intakes": []
                    }
                
                return intakes
            except Exception as e:
                import traceback
                print(f"ERROR: find_program_intakes failed: {e}")
                traceback.print_exc()
                return []
        
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
        
        for idx, result in enumerate(results[:10]):  # Limit to 10 for context
            if isinstance(result, dict) and "university" in result:
                # From list_universities_by_filters
                uni = result["university"]
                context_parts.append(f"University {idx+1}: {uni.name}")
                if uni.city:
                    context_parts.append(f"  Location: {uni.city}, {uni.province or ''}, {uni.country or 'China'}")
                if uni.university_ranking:
                    context_parts.append(f"  Ranking: {uni.university_ranking}")
                context_parts.append(f"  Matching programs: {result.get('program_count', 0)}")
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
                
                # For SCHOLARSHIP intent: Focus on scholarship fields
                if is_scholarship_intent:
                    # scholarship_available (Boolean, nullable)
                    if intake.scholarship_available is not None:
                        context_parts.append(f"  Scholarship Available: {'Yes' if intake.scholarship_available else 'No'}")
                    
                    # scholarship_info (Text)
                    if intake.scholarship_info:
                        context_parts.append(f"  Scholarship Info: {intake.scholarship_info}")
                    
                    # ProgramIntakeScholarship relationship
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
                    
                    # Notes (may contain scholarship info)
                    if intake.notes:
                        context_parts.append(f"  Notes: {intake.notes}")
                
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
                
                # For other intents (general, etc.)
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
                    
                    # Include notes only for single result
                    if is_single:
                        if intake.notes:
                            context_parts.append(f"  Notes: {intake.notes}")
                        if req_focus.get("accommodation") and intake.accommodation_note:
                            context_parts.append(f"  Accommodation note: {intake.accommodation_note}")
                
                context_parts.append("")
        
        return "\n".join(context_parts)
    
    def format_answer_with_llm(self, db_context: str, user_question: str, intent: str, req_focus: Dict[str, bool]) -> str:
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
- If there are 3 or more universities, ask the user to choose a specific university first
- Focus on the most relevant information based on the user's question (e.g., if they ask about fees, prioritize fee information)

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
            if matches[0][1] >= 0.8:
                return True, matches[0][0], matches[:3]
            return False, None, matches[:3]
        
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
        
        # Step 2: Try DBQueryService first (fast ILIKE search with expanded input)
        majors = self.db_service.search_majors(
            university_id=university_id,
            name=expanded_input,  # Use expanded input for DB search
            degree_level=degree_level,
            limit=top_k * 2  # Get more candidates for fuzzy matching
        )
        
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
        
        # Filter by university_id and degree_level if provided
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
            keywords = self._normalize_keywords(major.get("keywords", []))
            if keywords:
                for keyword in keywords:
                    keyword_clean = re.sub(r'[^\w\s&]', '', str(keyword).lower())
                    if not keyword_clean:
                        continue
                    # Exact keyword match (case-insensitive)
                    if user_input_clean == keyword_clean:
                        if 0.98 > best_score_for_major:
                            best_score_for_major = 0.98
                            match_type = "keyword_exact"
                        break  # Highest priority, stop here
                    # Keyword contains match
                    elif user_input_clean in keyword_clean or keyword_clean in user_input_clean:
                        match_ratio = SequenceMatcher(None, user_input_clean, keyword_clean).ratio()
                        if match_ratio > best_score_for_major:
                            best_score_for_major = match_ratio * 0.95  # High score for keyword contains
                            match_type = "keyword_contains"
            
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
        matched, best_match, all_matches = self._fuzzy_match_major(
            expanded_query,
            university_id=university_id,
            degree_level=degree_level,
            top_k=10  # Get more candidates, then filter by confidence
        )
        
        if not matched or not all_matches:
            return []
        
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
                if intent in ["documents_only", "eligibility_only", "scholarship_only", "SCHOLARSHIP", "REQUIREMENTS", self.router.INTENT_ADMISSION_REQUIREMENTS]:
                    if intake.get('notes'):
                        intake_info += f"\n  Important Notes: {intake['notes']}"
                    else:
                        intake_info += f"\n  Important Notes: Not specified"
                    if intent in ["scholarship_only", "SCHOLARSHIP"] and intake.get('scholarship_info'):
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
        db_results = self.run_db(route_plan, latest_user_message=latest_user_msg)
        
        # Check if result is a fallback dict
        fallback_result = None
        if isinstance(db_results, dict) and db_results.get("_fallback"):
            fallback_result = db_results
            db_results = fallback_result.get("_intakes", [])
            print(f"DEBUG: Fallback triggered: type={fallback_result.get('_fallback_type')}, results={len(db_results)}")
        
        print(f"DEBUG: DB query returned {len(db_results)} results")
        
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
            
            # If multiple universities or multiple programs per university, show list
            if len(university_programs) > 1 or any(len(progs) > 1 for progs in university_programs.values()):
                response_parts = ["I found multiple language programs. Please choose one to see specific details:\n"]
                idx = 1
                for uni_name, programs in sorted(university_programs.items()):
                    for prog in programs:
                        response_parts.append(f"{idx}. {uni_name} - {prog['major']}{prog['duration']} (Deadline: {prog['deadline']})")
                        idx += 1
                
                response_parts.append("\nPlease specify which program you'd like information about (e.g., '1' or 'Nanjing University - Chinese Language Program').")
                
                return {
                    "response": "\n".join(response_parts),
                    "used_db": True,
                    "used_tavily": False,
                    "sources": []
                }
        
        # ========== MAJOR VALIDATION: Check if returned majors match user's major_query ==========
        # CRITICAL: Skip validation for language programs - they're matched by keywords/category, not exact name
        # Also skip if we already resolved major_ids (means we found matches via keywords/category)
        is_language_query = (
            state and state.major_query and any(
                kw in state.major_query.lower() 
                for kw in ["language", "foundation", "preparatory", "chinese language", "non-degree"]
            )
        ) or (state and hasattr(state, '_resolved_major_ids') and state._resolved_major_ids and state.degree_level == "Language")
        
        if state and state.major_query and db_results and len(db_results) > 0 and not is_language_query:
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
                    
                    is_match = (
                        similarity_name >= 0.7 or similarity_cn >= 0.7 or
                        word_overlap >= 0.5 or
                        (user_major_clean in major_name_clean and len(user_major_clean) >= 4)  # Only if query is substantial
                    )
                    
                    print(f"DEBUG: Major validation - user='{user_major_lower}', major='{major_name}', similarity={similarity_name:.2f}, word_overlap={word_overlap:.2f}, is_match={is_match}")
                    
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
                
                response_msg = f"I couldn't find an {state.teaching_language or 'English'}-taught {state.intake_term or 'March'} intake for {state.major_query} in our partner database."
                if closest_major_names:
                    response_msg += f" Here are the closest available majors: {', '.join(closest_major_names)}."
                
                return {
                    "response": response_msg,
                    "used_db": True,
                    "used_tavily": False,
                    "sources": []
                }
        
        # ========== HANDLE FALLBACK RESULTS ==========
        if fallback_result:
            fallback_type = fallback_result.get("_fallback_type")
            
            if fallback_type == "duration" and len(db_results) > 0:
                # Duration filter returned 0 results, but programs exist with other durations
                available_durations = fallback_result.get("_available_durations", [])
                durations_str = ", ".join(available_durations) if available_durations else "different durations"
                return {
                    "response": f"I found language programs, but not with the duration you specified. Available durations: {durations_str}. Which duration do you prefer?",
                    "used_db": True,
                    "used_tavily": False,
                    "sources": []
                }
            
            if fallback_type == "intake_term" and len(db_results) > 0:
                # Intake term removal yielded results - ask user to choose term
                available_terms = fallback_result.get("_available_terms", [])
                terms_str = " or ".join(available_terms) if available_terms else "March or September"
                return {
                    "response": f"I found programs, but not for the intake term you specified. Available terms: {terms_str}. Which term would you like?",
                    "used_db": True,
                    "used_tavily": False,
                    "sources": []
                }
            
            elif fallback_type == "teaching_language" and len(db_results) > 0:
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
                
                print(f"DEBUG: Fallback teaching_language - available_languages={available_languages}, setting pending")
                
                return {
                    "response": f"I found programs available in {langs_str}. Which do you prefer?",
                    "used_db": True,
                    "used_tavily": False,
                    "sources": []
                }
            
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
                        top_majors = [m.get("name") for m in filtered_majors[:5]]
                        return {
                            "response": f"I couldn't find an upcoming intake matching those filters. Available majors for {state.degree_level or 'your criteria'}: {', '.join(top_majors)}. Tell me a major name or say 'show available majors'.",
                            "used_db": True,
                            "used_tavily": False,
                            "sources": []
                        }
                
                return {
                    "response": "I couldn't find an upcoming intake matching those filters. Tell me a nearby major name (or say 'show available majors').",
                    "used_db": True,
                    "used_tavily": False,
                    "sources": []
                }
        
        # ========== POST-QUERY: Check for mixed teaching languages ==========
        # Use DBQueryService to check distinct languages before asking
        if db_results and not state.teaching_language and not state.is_clarifying:
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
                    
                    if len(distinct_languages) >= 2:
                        # Mixed teaching languages - ask clarification
                        langs_str = " or ".join(sorted(distinct_languages))
                        print(f"DEBUG: Mixed teaching languages detected: {distinct_languages}, asking clarification")
                        
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
                    
                    if len(teaching_languages) > 1:
                        langs_str = " or ".join(sorted(teaching_languages))
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
        
        # Check if there are 3+ universities - if so, ask user to choose instead of showing all details
        if db_results and intent in [self.router.INTENT_ADMISSION_REQUIREMENTS, "REQUIREMENTS", "ADMISSION_REQUIREMENTS"]:
            unique_universities = set()
            for result in db_results:
                if hasattr(result, 'university') and result.university:
                    unique_universities.add(result.university.name)
                elif hasattr(result, 'university_id') and result.university_id:
                    # Try to get university name from ID
                    from app.models import University
                    uni = self.db.query(University).filter(University.id == result.university_id).first()
                    if uni:
                        unique_universities.add(uni.name)
            
            if len(unique_universities) >= 3:
                # Ask user to choose a university
                university_list = "\n".join([f"- {uni}" for uni in sorted(unique_universities)])
                return {
                    "response": f"I found information for {len(unique_universities)} universities. Please choose one to see detailed requirements:\n\n{university_list}",
                    "used_db": True,
                    "used_tavily": False,
                    "sources": []
                }
        
        # For single result or small results, use LLM formatting
        try:
            print(f"DEBUG: Formatting answer with LLM...")
            intent = state.intent if state else "general"
            formatted_response = self.format_answer_with_llm(
                db_context=db_context,
                user_question=user_message,
                intent=intent,
                req_focus=req_focus
            )
            print(f"DEBUG: LLM formatting completed, response length={len(formatted_response)} chars")
        except Exception as e:
            import traceback
            print(f"ERROR: format_answer_with_llm() failed: {e}")
            traceback.print_exc()
            # Fallback response
            formatted_response = "I found information in the database, but encountered an error formatting the response. Please try rephrasing your question."
        
        return {
            "response": formatted_response,
            "used_db": True,
            "used_tavily": False,
            "sources": []
        }

        # ========== OLD CODE BELOW (to be removed/refactored) ==========
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
                    uni = self.db.query(University).filter(University.id == self.last_selected_university_id).first()
                    uni_info = {"name": uni.name, "id": uni.id} if uni else None
                    if uni_info:
                        state.university = uni_info["name"]
                        print(f"DEBUG: Using conversation memory - last_selected_university_id={self.last_selected_university_id} ({uni_info['name']})")
                
                if self.last_selected_major_id and not state.major_query:
                    major = self.db.query(Major).filter(Major.id == self.last_selected_major_id).first()
                    if major:
                        # Don't set major_query, but we'll use major_id directly later
                        print(f"DEBUG: Using conversation memory - last_selected_major_id={self.last_selected_major_id} ({major.name})")
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
                uni = self.db.query(University).filter(University.id == self.last_selected_university_id).first()
                uni_info = {"name": uni.name, "id": uni.id} if uni else None
                if uni_info:
                    state.university = uni_info["name"]
                    print(f"DEBUG: Using conversation memory - last_selected_university_id={self.last_selected_university_id} ({uni_info['name']})")
            if self.last_selected_major_id:
                major = self.db.query(Major).filter(Major.id == self.last_selected_major_id).first()
                if major:
                    state.major_query = major.name
                    print(f"DEBUG: Using conversation memory - last_selected_major_id={self.last_selected_major_id} ({major.name})")

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
                
                uni_name = self._get_university_name_by_id(university_id) if university_id else "this university"
                intake_term_str = norm_intake_term.value.title() if norm_intake_term else state.intake_term
                intake_year_str = f" {state.intake_year}" if state.intake_year else ""
                
                response_parts = [
                    f"I found {len(all_language_intakes)} {intake_term_str}{intake_year_str} language program(s) at {uni_name}:"
                ]
                
                for idx, intake in enumerate(all_language_intakes[:10], 1):  # Limit to 10 for readability
                    eff_lang = intake.get('effective_teaching_language') or intake.get('teaching_language') or 'N/A'
                    deadline_raw = intake.get('application_deadline', 'N/A')
                    deadline_str = 'N/A'
                    if deadline_raw and deadline_raw != 'N/A':
                        try:
                            if isinstance(deadline_raw, str):
                                deadline_dt = datetime.fromisoformat(deadline_raw.replace('Z', '+00:00'))
                                deadline_str = deadline_dt.date().isoformat()
                            else:
                                deadline_str = str(deadline_raw)
                        except (ValueError, AttributeError):
                            if isinstance(deadline_raw, str):
                                import re
                                date_match = re.search(r'(\d{4}-\d{2}-\d{2})', deadline_raw)
                                if date_match:
                                    deadline_str = date_match.group(1)
                                else:
                                    deadline_str = deadline_raw
                            else:
                                deadline_str = str(deadline_raw)
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
                uni = self.db.query(University).filter(University.id == university_id).first()
                uni_dict = {"name": uni.name, "name_cn": uni.name_cn, "aliases": uni.aliases if isinstance(uni.aliases, list) else (json.loads(uni.aliases) if isinstance(uni.aliases, str) else [])} if uni else None
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
                            uni_name = self._get_university_name_by_id(major_match.get("university_id")) if major_match.get("university_id") else "Unknown"
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
                        uni_name = self._get_university_name_by_id(major_match.get("university_id")) if major_match.get("university_id") else "Unknown"
                        match_notes.append(f"  - {major_match['name']} at {uni_name} (match score: {score:.2f})")
                    match_notes.append("Please pick one from the list above.")
                    print(f"DEBUG: Medium confidence - showing top 3 matches for '{cleaned_major_query}': {[m[0]['name'] for m in top_3]}")
            else:
                print(f"DEBUG: NO fuzzy matches found for major_query '{cleaned_major_query}' (degree_level={state.degree_level}, university_id={university_id})")
        
        # Use conversation memory for follow-up queries if no major_ids found yet
        if not major_ids and intent in follow_up_intents and self.last_selected_major_id:
            # Verify the major still matches current filters
            major = self.db.query(Major).filter(Major.id == self.last_selected_major_id).first()
            if major:
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
                                uni_name = self._get_university_name_by_id(university_id) if university_id else "this university"
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
                uni_name = self._get_university_name_by_id(university_id) if university_id else "this university"
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
                    uni_name = self._get_university_name_by_id(university_id) if university_id else "this university" if university_id else "partner universities"
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
                major = self.db.query(Major).filter(Major.id == mid).first()
                if major:
                    uni_name = self._get_university_name_by_id(major.university_id)
                    matched_major_details.append(f"id={mid}, name='{major.name}', degree_level={major.degree_level}, university='{uni_name}' (id={major.university_id})")
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
                major = self.db.query(Major).filter(Major.id == intake.get('major_id')).first() if intake.get('major_id') else None
                major_info = {"duration_years": major.duration_years if major else None, "name": major.name if major else None}
                
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
                uni = self.db.query(University).filter(University.id == uni_id).first()
                uni_info = {"name": uni.name, "city": uni.city, "province": uni.province} if uni else None
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
                        uni_name = self._get_university_name_by_id(university_id) if university_id else "this university"
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
                    uni_name = self._get_university_name_by_id(university_id) if university_id else "this university"
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
                        uni_name = self._get_university_name_by_id(university_id) if university_id else "this university" if university_id else "partner universities"
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
                uni_name = self._get_university_name_by_id(university_id) if university_id else "this university" if university_id else "partner universities"
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
                        uni_name = self._get_university_name_by_id(university_id) if university_id else "this university"
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
                        uni_name = self._get_university_name_by_id(university_id) if university_id else "this university" if university_id else "partner universities"
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
                        uni_name = self._get_university_name_by_id(university_id) if university_id else "this university" if university_id else "partner universities"
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
            
            # Fix 4: For list queries with Language degree_level OR fees_only/fees_compare OR documents_only/eligibility_only, ALWAYS use deterministic formatter
            # Never build DB context or call OpenAI for these
            is_language_list = state.degree_level and "Language" in str(state.degree_level)
            should_use_deterministic = (intent in ["fees_compare", "fees_only", "documents_only", "eligibility_only"]) or is_language_list
            
            if should_use_deterministic:
                print(f"DEBUG: Using deterministic formatter for list query with intent={intent}, is_language={is_language_list}")
                # Group by university
                from collections import defaultdict
                by_university = defaultdict(list)
                for intake in filtered_intakes:
                    by_university[intake.get('university_id')].append(intake)
                
                # Check if single-university case OR documents_only/eligibility_only intent
                unique_uni_ids = set(i.get('university_id') for i in filtered_intakes if i.get('university_id'))
                is_single_university = len(unique_uni_ids) == 1
                should_show_all_programs = is_single_university or intent in ["documents_only", "eligibility_only"] or state.duration_preference
                
                if should_show_all_programs:
                    # Show ALL programs (not just one per university)
                    print(f"DEBUG: Single-university or documents_only/eligibility_only - showing all {len(filtered_intakes)} programs")
                    selected_intakes = filtered_intakes
                    total_for_formatter = len(filtered_intakes)
                else:
                    # Multi-university: Select best intake per university
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
        # For SCHOLARSHIP intent, also include requirements (docs, exams, eligibility) since user might ask about admission requirements
        include_docs = include_exams = include_scholarships = include_eligibility = include_deadlines = include_cost = True
        if intent == "SCHOLARSHIP" or intent == "scholarship_only":
            # Keep all flags True for SCHOLARSHIP to show full requirements
            pass
        elif intent == "fees_only":
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
            include_exams = True  # Include exam requirements (HSK, English tests)
            include_scholarships = True
            include_eligibility = True  # Include eligibility requirements (age, bank statement, etc.)
            include_deadlines = True
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
                temperature=0.0  # Deterministic
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

