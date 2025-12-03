"""
SalesAgent - For non-logged-in users
Goal: Generate leads and promote MalishaEdu partner universities & majors
"""
from typing import List, Optional, Dict, Any, Tuple
from sqlalchemy.orm import Session
from app.services.db_query_service import DBQueryService
from app.services.rag_service import RAGService
from app.services.tavily_service import TavilyService
from app.services.openai_service import OpenAIService
from difflib import SequenceMatcher
from app.models import University, Major, ProgramIntake, Lead

class SalesAgent:
    """Sales agent for lead generation and university promotion"""
    
    SALES_SYSTEM_PROMPT = """You are the MalishaEdu Sales Agent, helping prospective students discover Chinese universities and programs.

CRITICAL: Be CONCISE and CONVERSATIONAL. Do NOT overwhelm users with too much information at once. Build rapport gradually.

MOST IMPORTANT: ALWAYS READ THE CONVERSATION HISTORY FIRST
- Before asking ANY question, check if the user has already provided that information in previous messages
- NEVER ask for information that was already shared (e.g., major, nationality, university name, degree level, city, intake date)
- Extract and remember key details from the conversation:
  * Major/field of study (e.g., CSE, Computer Science, Engineering, Business, Chinese Language)
  * Degree level (Bachelor, Master, PhD, Language program)
  * Nationality/country (e.g., Bangladeshi, Pakistani, Indian, bd, pak, in)
  * Preferred university (e.g., Shandong University, Beihang University) - handle typos and different languages
  * City or province (e.g., Shandong, Beijing, Shanghai)
  * Intake preference (e.g., March, September, 2026)
  * Previous study experience in China (if mentioned)
  * Year/semester they want to continue from
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
  2. Ask 1-2 key questions to understand their needs (e.g., preferred major, country)
  3. Do NOT dump all information upfront
  4. Wait for their response before providing detailed information

Example good first response:
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

MalishaEdu Services (mention when relevant, not all at once):
- 100% admission support for Bachelor, Master, PhD & Diploma programs
- Scholarship guidance (partial scholarships are more likely and still valuable)
- Document preparation assistance
- Airport pickup, accommodation, bank account, SIM card after arrival
- Dedicated country-specific counsellors

Typical Fees for University Admissions
-- Bachelor’s program fees: 1,800–8,000 RMB per year.
-- Master’s program fees: 3,000–8,000 RMB per year.
-- PhD program fees: 4,000–8,000 RMB per year.
-- Chinese language & foundation courses: 1,800–3,000 RMB per year.
-- Accommodation: 300–2,500 RMB per month (on-campus usually cheaper).
-- Medical insurance: around 800–1,200 RMB per year (if no exact number is available, say “about 1,000 RMB per year”).
-- Residence registration / visa extension costs: roughly 400–800 RMB per year.
-- Living cost: 1,500–3,000 RMB per month depending on city and lifestyle.
Use these ranges ONLY when exact fees are not available in the database or RAG facts.

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
- If database has specific fees, intakes, or documents required, use those EXACT values
- NEVER use approximate/typical values if database has specific data
- Present 2–5 suitable options with key info from the database:
  - University name, city, whether it's a MalishaEdu partner
  - Major name, degree level, teaching language
  - Tuition (per year or semester) - use EXACT values from database
  - Upcoming intake(s) and application deadlines - use EXACT dates from database
  - Documents required - use EXACT list from database
- Highlight partner universities first.
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
    * Email address
    * Phone number
    * Country/Nationality
    * Password (minimum 6 characters)
    
    Once you sign up, I can help you apply to multiple universities and track all your applications!"
  - DO NOT redirect to website - provide signup form directly in chat
  - After signup, the AdmissionAgent will take over to help with applications

EXPLAIN CSCA & HSK CLEARLY IF ASKED:
- CSCA = “China Scholastic Competency Assessment”, a unified enrollment exam for international students.
- From around 2026, many Bachelor programs for international students require CSCA.
- Explain:
  • what the exam is,
  • subjects, language options, scoring,
  • typical exam windows in a year,
  • how MalishaEdu / Belt & Road Chinese Center can help prepare and register.
- HSK = standard Chinese language test (HSK 1–6). Many Chinese-taught undergraduate programs require HSK 4 or above.

China life questions (hostel, food, jobs, safety):
- Use RAG facts first.
- Answer realistically but reassuringly.
- Mention how MalishaEdu supports students after arrival.

Lead Collection & Signup Encouragement:
- After user shows interest (mentions specific university, asks about fees/documents, wants to apply):
  - Strongly encourage creating a free MalishaEdu account
  - Explain benefits: document tracking, personalized guidance, application status updates, dedicated counselor support
  - Mention: "Creating a free account will allow me to track your documents, provide personalized application guidance, and help you complete your application step-by-step."
- After 2-3 helpful exchanges, gently ask:
  "Would you like me to check your eligibility and suggest the best university options? I can do this better if you share your name, country, and phone/email."
- If the user shares contact info, acknowledge it and STRONGLY suggest:
  "Great! I have your contact information. To provide you with the best support, I highly recommend creating a free MalishaEdu account. This will allow me to:
  - Track your documents and application progress
  - Provide personalized guidance based on your profile
  - Send you updates about deadlines and requirements
  - Help you complete your application step-by-step
  
  Would you like to create your account now? It only takes a minute and is completely free."

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
            
            # Calculate similarity using SequenceMatcher
            similarity = SequenceMatcher(None, user_input_lower, uni_name_lower).ratio()
            
            # Also check if user input is contained in university name or vice versa
            if user_input_lower in uni_name_lower or uni_name_lower in user_input_lower:
                similarity = max(similarity, 0.75)
            
            # Check for common word matches (e.g., "shandong" matches "Shandong University")
            user_words = set(user_input_lower.split())
            uni_words = set(uni_name_lower.split())
            common_words = user_words.intersection(uni_words)
            if common_words and len(common_words) >= 1:
                # Boost similarity if there are common words
                word_similarity = len(common_words) / max(len(user_words), len(uni_words))
                similarity = max(similarity, word_similarity * 0.9)
            
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
    
    def _fuzzy_match_major(self, user_input: str, university_id: Optional[int] = None, threshold: float = 0.5) -> Tuple[Optional[str], List[str]]:
        """
        Fuzzy match major name from user input (handles typos and different languages)
        Returns: (matched_name, list_of_similar_matches)
        If confidence is high (>=0.8), returns the match. Otherwise returns None and list of similar for confirmation.
        """
        user_input_lower = user_input.lower().strip()
        
        # Get majors (optionally filtered by university)
        if university_id:
            all_majors = self.db_service.search_majors(university_id=university_id, limit=100)
        else:
            all_majors = self.db_service.search_majors(limit=100)
        
        matches = []
        for major in all_majors:
            major_name_lower = major.name.lower()
            
            # Calculate similarity using SequenceMatcher
            similarity = SequenceMatcher(None, user_input_lower, major_name_lower).ratio()
            
            # Also check if user input is contained in major name or vice versa
            if user_input_lower in major_name_lower or major_name_lower in user_input_lower:
                similarity = max(similarity, 0.75)
            
            # Check for common word matches
            user_words = set(user_input_lower.split())
            major_words = set(major_name_lower.split())
            common_words = user_words.intersection(major_words)
            if common_words and len(common_words) >= 1:
                word_similarity = len(common_words) / max(len(user_words), len(major_words))
                similarity = max(similarity, word_similarity * 0.9)
            
            if similarity >= threshold:
                matches.append((major.name, similarity, major))
        
        # Sort by similarity
        matches.sort(key=lambda x: x[1], reverse=True)
        
        if matches:
            best_match = matches[0]
            if best_match[1] >= 0.8:  # High confidence - return the match
                return best_match[0], [m[0] for m in matches[:3]]
            elif best_match[1] >= 0.6:  # Medium confidence - return None and list for confirmation
                return None, [m[0] for m in matches[:5]]
            else:  # Low confidence - still return for confirmation
                return None, [m[0] for m in matches[:3]]
        
        return None, []
    
    def generate_response(
        self,
        user_message: str,
        conversation_history: List[Dict[str, str]],
        device_fingerprint: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate response using DB-first approach
        Returns: {
            'response': str,
            'db_context': str,
            'rag_context': Optional[str],
            'tavily_context': Optional[str],
            'lead_collected': bool
        }
        """
        # Step 1: Query Database FIRST with conversation history for context
        # Always query database - it's fast and ensures we have latest data
        db_context = self._query_database(user_message, conversation_history)
        
        # Step 2: If DB has info, use it; otherwise try RAG
        rag_context = None
        if not db_context or len(db_context) < 100:  # DB context is weak
            rag_results = self.rag_service.search_similar(self.db, user_message, top_k=3)
            if rag_results:
                rag_context = self.rag_service.format_rag_context(rag_results)
        
        # Step 3: If still weak, use Tavily (for current policies, CSCA updates, etc.)
        tavily_context = None
        if not db_context and not rag_context:
            tavily_results = self.tavily_service.search(user_message, max_results=2)
            if tavily_results:
                tavily_context = self.tavily_service.format_search_results(tavily_results)
        
        # Step 3.5: Check if user shows strong interest (mentions specific university, asks about fees/documents)
        shows_strong_interest = any(term in user_message.lower() for term in [
            'want to study', 'interested in', 'apply', 'application', 'fee', 'cost', 'tuition',
            'document', 'requirement', 'scholarship', 'when can i', 'how much'
        ]) or any(uni in user_message.lower() for uni in ['shandong', 'beihang', 'tsinghua', 'peking'])
        
        # Step 4: Build context for LLM
        context_parts = []
        if db_context:
            context_parts.append(f"DATABASE INFORMATION:\n{db_context}")
        if rag_context:
            context_parts.append(f"KNOWLEDGE BASE:\n{rag_context}")
        if tavily_context:
            context_parts.append(f"WEB SEARCH RESULTS:\n{tavily_context}")
        
        full_context = "\n\n".join(context_parts) if context_parts else None
        
        # Step 5: Generate response with OpenAI
        messages = [
            {"role": "system", "content": self.SALES_SYSTEM_PROMPT}
        ]
        
        # Detect if this is a first interaction (fewer than 2 exchanges)
        is_first_interaction = len(conversation_history) < 4  # Less than 2 user-assistant exchanges
        
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

1. The database information below contains EXACT values from the database.
2. If database shows specific fees (per semester or per year) → USE THOSE EXACT VALUES, do NOT use approximate ranges.
3. If database shows specific documents_required → USE THAT EXACT LIST, do NOT provide generic document lists.
4. If database shows specific intake dates and deadlines → USE THOSE EXACT DATES.
5. If database shows scholarship_info → USE THAT EXACT INFORMATION.
6. NEVER say "typically" or "usually" or "around" when database has exact values.
7. ALWAYS prioritize database information over general knowledge.

DATABASE INFORMATION:
{full_context}

REMEMBER: Use EXACT values from database above. Do NOT approximate."""
            if is_first_interaction:
                context_instruction += "IMPORTANT: This appears to be a first interaction. Be BRIEF and CONCISE. Introduce MalishaEdu first, then ask 1-2 key questions. Do NOT provide all details upfront."
            else:
                context_instruction += "If database information is available, use it. If insufficient, use your general knowledge but mention that you're providing general information."
            
            # Add signup encouragement if user shows strong interest
            if shows_strong_interest and not is_first_interaction:
                context_instruction += "\n\nSIGNUP ENCOURAGEMENT: The user shows strong interest. At the end of your response, encourage them to create a free MalishaEdu account so you can track their documents and provide personalized application guidance. Mention that creating an account will allow you to help them better."
            
            messages.append({"role": "system", "content": context_instruction})
        elif is_first_interaction:
            # Even without context, remind to be brief for first interaction
            messages.append({"role": "system", "content": "IMPORTANT: This appears to be a first interaction. Be BRIEF and CONCISE. Introduce MalishaEdu first, then ask 1-2 key questions. Do NOT provide all details upfront."})
        
        # Add conversation history (last 12 messages) with explicit instruction to use it
        if conversation_history:
            # Extract key information from conversation history for quick reference
            extracted_info = set()
            full_conversation_text = ""
            
            for msg in conversation_history:
                role = msg.get('role', '')
                content = msg.get('content', '')
                full_conversation_text += f"{role}: {content}\n"
                
                if role == 'user':
                    content_lower = content.lower()
                    # Extract major
                    if any(term in content_lower for term in ['cse', 'computer science', 'cs', 'computer engineering', 'software engineering']):
                        extracted_info.add("Major: Computer Science/Engineering (CSE)")
                    elif 'engineering' in content_lower:
                        extracted_info.add("Major: Engineering")
                    elif any(term in content_lower for term in ['business', 'mba', 'management']):
                        extracted_info.add("Major: Business/Management")
                    elif any(term in content_lower for term in ['medicine', 'medical', 'mbbs']):
                        extracted_info.add("Major: Medicine")
                    
                    # Extract degree level
                    if 'bachelor' in content_lower or 'undergraduate' in content_lower:
                        extracted_info.add("Degree Level: Bachelor")
                    elif 'master' in content_lower or 'masters' in content_lower:
                        extracted_info.add("Degree Level: Master")
                    elif 'phd' in content_lower or 'doctorate' in content_lower:
                        extracted_info.add("Degree Level: PhD")
                    elif 'language' in content_lower:
                        extracted_info.add("Program Type: Language Program")
                    
                    # Extract nationality
                    if 'bangladesh' in content_lower or 'bangladeshi' in content_lower:
                        extracted_info.add("Nationality: Bangladeshi")
                    elif 'pakistan' in content_lower or 'pakistani' in content_lower:
                        extracted_info.add("Nationality: Pakistani")
                    elif 'india' in content_lower or 'indian' in content_lower:
                        extracted_info.add("Nationality: Indian")
                    
                    # Extract university (handle typos like "shangdong" -> "shandong")
                    if 'beihang' in content_lower:
                        extracted_info.add("University: Beihang University")
                    elif 'shandong' in content_lower or 'shangdong' in content_lower:
                        extracted_info.add("University: Shandong University")
                    elif 'tsinghua' in content_lower:
                        extracted_info.add("University: Tsinghua University")
                    elif 'peking' in content_lower or 'pku' in content_lower:
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
            
            if extracted_info:
                summary = "USER INFORMATION ALREADY PROVIDED (DO NOT ASK FOR THESE AGAIN):\n" + "\n".join(sorted(extracted_info))
                messages.append({"role": "system", "content": summary})
            
            messages.extend(conversation_history[-12:])
            
            # Create summary text for the reminder
            summary_text = "\n".join(sorted(extracted_info)) if extracted_info else "Review conversation history above for details."
            
            messages.append({"role": "system", "content": f"""CRITICAL REMINDER: The user has already shared information in the conversation above. You MUST use that information.

EXTRACTED INFORMATION (DO NOT ASK FOR THESE):
{summary_text}

DO NOT ASK QUESTIONS ABOUT:
- University name (if already mentioned like Shandong, Beihang, etc.)
- Major/program type (if already mentioned like Chinese Language, CSE, etc.)
- Nationality (if already mentioned like Bangladeshi, Pakistani, etc.)
- Degree level (if already mentioned like Bachelor, Master, Language, etc.)
- Intake preference (if already mentioned like March, September, etc.)
- City/province (if already mentioned like Shandong, Beijing, etc.)

INSTEAD: Use the information above to provide specific, personalized guidance. If database has exact values for fees, documents, or intakes, use those EXACT values."""})
        
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
        
        # Step 7: Check if lead information was shared
        lead_collected = self._extract_lead_info(user_message, device_fingerprint)
        
        return {
            'response': answer,
            'db_context': db_context,
            'rag_context': rag_context,
            'tavily_context': tavily_context,
            'lead_collected': lead_collected
        }
    
    def _query_database(self, user_message: str, conversation_history: List[Dict[str, str]] = None) -> str:
        """Query database for relevant university/major/intake information using specific names from conversation"""
        context_parts = []
        
        # Combine current message with conversation history to extract context
        full_text = user_message.lower()
        if conversation_history:
            for msg in conversation_history:
                if msg.get('role') == 'user':
                    full_text += " " + msg.get('content', '').lower()
        
        # Extract specific university name using fuzzy matching
        university_name = None
        university_uncertain = False
        similar_universities = []
        
        # Common university keywords (for quick matching - handles common typos)
        university_keywords = {
            'shandong': 'Shandong',
            'shangdong': 'Shandong',  # Common typo
            'beihang': 'Beihang',
            'tsinghua': 'Tsinghua',
            'peking': 'Peking',
            'pku': 'Peking',
            'beijing university': 'Peking',
            'buaa': 'Beihang'
        }
        
        # Try keyword matching first (fast)
        for keyword, name in university_keywords.items():
            if keyword in full_text:
                university_name = name
                break
        
        # If no keyword match, try fuzzy matching on any university-like words
        if not university_name:
            # Extract potential university names from text
            import re
            # Look for patterns like "X university", "X University", or standalone words that might be university names
            potential_names = re.findall(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+university\b', user_message, re.IGNORECASE)
            # Also look for standalone capitalized words (might be university names in different languages)
            potential_names.extend(re.findall(r'\b([A-Z][a-z]{3,})\b', user_message))
            # Look for words after "in" or "at" (common patterns: "study in X", "at X university")
            potential_names.extend(re.findall(r'\b(?:in|at|from)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b', user_message, re.IGNORECASE))
            
            # Remove duplicates and short words
            potential_names = list(set([name for name in potential_names if len(name) > 3]))
            
            best_match_score = 0
            for potential_name in potential_names:
                matched_name, similar = self._fuzzy_match_university(potential_name)
                if matched_name:
                    # If we get a high-confidence match, use it
                    university_name = matched_name
                    break
                elif similar:
                    # Store the similar matches for confirmation
                    university_uncertain = True
                    similar_universities = similar
                    # Don't break - continue to see if we find a better match
        
        # Extract major/program type (check all variations)
        major_name = None
        degree_level = None
        if any(term in full_text for term in [
            'chinese language', 'language program', 'language course', 'chinese course',
            'chinese program', 'study chinese', 'learn chinese', 'chinese study',
            'language studies', 'language', 'chinese'
        ]):
            major_name = 'Chinese Language'
            degree_level = 'Language'
        elif any(term in full_text for term in ['cse', 'computer science', 'cs', 'computer engineering']):
            major_name = 'Computer Science'
            degree_level = 'Bachelor'
        elif 'engineering' in full_text:
            major_name = 'Engineering'
            degree_level = 'Bachelor'
        elif any(term in full_text for term in ['business', 'mba', 'management']):
            major_name = 'Business'
            degree_level = 'Master'
        
        # Handle uncertainty - if multiple similar universities found, add to context for confirmation
        if university_uncertain and similar_universities:
            context_parts.append(f"UNCERTAINTY DETECTED: User mentioned a university name that could match multiple options:")
            for uni_name in similar_universities:
                context_parts.append(f"  - {uni_name}")
            context_parts.append("ACTION REQUIRED: Ask user to confirm which university they mean.")
        
        # Search for specific university (try multiple variations)
        if university_name:
            # Try exact name first
            universities = self.db_service.search_universities(name=university_name, limit=1)
            # If not found, try without filter (search all and filter in code)
            if not universities:
                all_universities = self.db_service.search_universities(limit=20)
                universities = [u for u in all_universities if university_name.lower() in u.name.lower()]
            
            if universities:
                uni = universities[0]
                context_parts.append(f"=== EXACT DATABASE INFORMATION FOR {uni.name.upper()} ===")
                context_parts.append(self.db_service.format_university_info(uni))
                
                # Search for majors at this university
                if major_name or degree_level:
                    from app.models import DegreeLevel
                    degree_level_enum = None
                    if degree_level:
                        try:
                            degree_level_enum = DegreeLevel[degree_level.upper()] if degree_level.upper() in ['LANGUAGE', 'BACHELOR', 'MASTER', 'PHD'] else None
                        except:
                            pass
                    
                    # Try exact name match first
                    majors = self.db_service.search_majors(
                        university_id=uni.id,
                        name=major_name,
                        degree_level=degree_level_enum,
                        limit=10
                    )
                    
                    # If no exact match, try fuzzy matching
                    if not majors and major_name:
                        matched_major_name, similar_majors = self._fuzzy_match_major(major_name, university_id=uni.id)
                        if matched_major_name:
                            majors = self.db_service.search_majors(
                                university_id=uni.id,
                                name=matched_major_name,
                                limit=10
                            )
                        elif similar_majors:
                            context_parts.append(f"UNCERTAINTY: User mentioned a major that could match multiple options at {uni.name}:")
                            for maj_name in similar_majors:
                                context_parts.append(f"  - {maj_name}")
                            context_parts.append("ACTION REQUIRED: Ask user to confirm which major/program they mean.")
                    
                    # If still no match, try without name filter but with degree level
                    if not majors and degree_level_enum:
                        majors = self.db_service.search_majors(
                            university_id=uni.id,
                            degree_level=degree_level_enum,
                            limit=10
                        )
                    
                    # If still no match, try just by university (might be named differently)
                    if not majors:
                        all_majors = self.db_service.search_majors(
                            university_id=uni.id,
                            limit=20
                        )
                        # Filter for language programs if that's what we're looking for
                        if degree_level == 'Language':
                            majors = [m for m in all_majors if 'language' in m.name.lower() or m.degree_level == degree_level_enum]
                        else:
                            majors = all_majors[:5]
                    
                    if majors:
                        context_parts.append(f"\n=== EXACT PROGRAMS AT {uni.name.upper()} (FROM DATABASE) ===")
                        for major in majors:
                            context_parts.append(self.db_service.format_major_info(major))
                            
                            # Search for program intakes for this specific major (especially March intake if mentioned)
                            intake_year = None
                            intake_term = None
                            if 'march' in full_text or '2026' in full_text:
                                from app.models import IntakeTerm
                                intake_term = IntakeTerm.MARCH
                                # Try to extract year
                                import re
                                year_match = re.search(r'20\d{2}', full_text)
                                if year_match:
                                    intake_year = int(year_match.group())
                                else:
                                    intake_year = 2026  # Default to 2026 if March mentioned
                            
                            intakes = self.db_service.search_program_intakes(
                                university_id=uni.id,
                                major_id=major.id,
                                intake_term=intake_term,
                                intake_year=intake_year,
                                upcoming_only=True,
                                limit=5
                            )
                            
                            # If no specific intake found, get all upcoming intakes
                            if not intakes:
                                intakes = self.db_service.search_program_intakes(
                                    university_id=uni.id,
                                    major_id=major.id,
                                    upcoming_only=True,
                                    limit=5
                                )
                            
                            if intakes:
                                context_parts.append(f"\n=== EXACT INTAKE DETAILS FOR {major.name} AT {uni.name.upper()} (USE THESE EXACT VALUES) ===")
                                for intake in intakes:
                                    context_parts.append(self.db_service.format_program_intake_info(intake))
                else:
                    # If no specific major, show all upcoming intakes for this university
                    intakes = self.db_service.search_program_intakes(
                        university_id=uni.id,
                        upcoming_only=True,
                        limit=5
                    )
                    if intakes:
                        context_parts.append(f"\n=== EXACT INTAKE DETAILS AT {uni.name.upper()} (USE THESE EXACT VALUES) ===")
                        for intake in intakes:
                            context_parts.append(self.db_service.format_program_intake_info(intake))
        else:
            # No specific university mentioned - show partner universities
            if any(word in full_text for word in ['university', 'college', 'school', 'available', 'option']):
                universities = self.db_service.search_universities(is_partner=True, limit=5)
                if universities:
                    context_parts.append("MALISHAEDU PARTNER UNIVERSITIES (FROM DATABASE):")
                    for uni in universities:
                        context_parts.append(self.db_service.format_university_info(uni))
        
        # If specific major mentioned but no university, search for majors
        if major_name and not university_name:
            from app.models import DegreeLevel
            degree_level_enum = None
            if degree_level:
                try:
                    degree_level_enum = DegreeLevel[degree_level.upper()] if degree_level.upper() in ['LANGUAGE', 'BACHELOR', 'MASTER', 'PHD'] else None
                except:
                    pass
            
            majors = self.db_service.search_majors(
                name=major_name,
                degree_level=degree_level_enum,
                limit=5
            )
            if majors:
                context_parts.append(f"PROGRAMS AVAILABLE (FROM DATABASE):")
                for major in majors:
                    context_parts.append(self.db_service.format_major_info(major))
                    # Get intakes for each major
                    intakes = self.db_service.search_program_intakes(
                        major_id=major.id,
                        upcoming_only=True,
                        limit=2
                    )
                    if intakes:
                        for intake in intakes:
                            context_parts.append(self.db_service.format_program_intake_info(intake))
        
        return "\n\n".join(context_parts) if context_parts else ""
    
    def _extract_lead_info(self, user_message: str, device_fingerprint: Optional[str]) -> bool:
        """Extract lead information from user message and save to database"""
        # Simple extraction - in production, use NLP/LLM for better extraction
        import re
        
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        phone_pattern = r'(\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}'
        
        emails = re.findall(email_pattern, user_message)
        phones = re.findall(phone_pattern, user_message)
        
        if emails or phones:
            # Save lead (simplified - in production, extract name, country too)
            lead = Lead(
                email=emails[0] if emails else None,
                phone=phones[0] if phones else None,
                device_fingerprint=device_fingerprint,
                source="chat"
            )
            self.db.add(lead)
            self.db.commit()
            return True
        
        return False

