"""
SQL Generator Service for Document Import
Generates PostgreSQL SQL scripts from university program documents
"""
from typing import Dict, List, Optional
from app.services.openai_service import OpenAIService
from app.services.document_parser import DocumentParser
import json
import re


class SQLGeneratorService:
    """Service to generate SQL from university program documents"""
    
    def __init__(self):
        self.openai_service = OpenAIService()
        self.document_parser = DocumentParser()
    
    def extract_text_from_document(self, file_content: bytes, filename: str) -> str:
        """Extract text from uploaded document"""
        file_type = filename.split('.')[-1].lower() if '.' in filename else 'txt'
        
        if file_type == 'pdf':
            return self.document_parser.extract_text_from_pdf(file_content)
        elif file_type in ['doc', 'docx']:
            return self.document_parser.extract_text_from_docx(file_content)
        elif file_type == 'txt':
            return file_content.decode('utf-8', errors='ignore')
        else:
            # Try to decode as text
            try:
                return file_content.decode('utf-8', errors='ignore')
            except:
                raise ValueError(f"Unsupported file type: {file_type}")
    
    def _get_intake_term_enum_values(self) -> list:
        """Query database to get actual IntakeTerm enum values"""
        try:
            from app.database import SessionLocal
            from sqlalchemy import text
            db = SessionLocal()
            try:
                result = db.execute(text("SELECT unnest(enum_range(NULL::intaketerm))::text"))
                values = [row[0] for row in result.fetchall()]
                return values if values else ['March', 'September', 'Other']  # Fallback
            finally:
                db.close()
        except Exception as e:
            print(f"⚠️  Could not query enum values: {e}, using defaults")
            return ['March', 'September', 'Other']  # Default fallback
    
    def generate_sql_from_text(self, document_text: str) -> str:
        """Generate PostgreSQL SQL script from document text using LLM"""
        
        # Estimate token count (rough: 1 token ≈ 4 characters)
        # gpt-4o-mini has 128K context, but we'll be conservative and limit to 100K tokens
        # System prompt is ~3000 chars (~750 tokens), so we have ~99K tokens for document
        MAX_DOCUMENT_CHARS = 400000  # ~100K tokens, leaving room for system prompt and response
        
        # Truncate document if too large, but keep the beginning (most important info usually at start)
        original_length = len(document_text)
        if len(document_text) > MAX_DOCUMENT_CHARS:
            print(f"⚠️  Document is too large ({original_length} chars). Truncating to {MAX_DOCUMENT_CHARS} chars.")
            # Keep first 80% and last 20% to preserve structure
            first_part = document_text[:int(MAX_DOCUMENT_CHARS * 0.8)]
            last_part = document_text[-int(MAX_DOCUMENT_CHARS * 0.2):]
            document_text = first_part + "\n\n[... document truncated ...]\n\n" + last_part
            print(f"📊 Truncated document: {len(document_text)} chars")
        
        # Get actual enum values from database
        enum_values = self._get_intake_term_enum_values()
        enum_values_str = ', '.join([f"'{v}'" for v in enum_values])
        
        system_prompt = f"""You are a PostgreSQL SQL generator for MalishaEdu university program import system.

Your task: Read the document text and generate EXACTLY ONE valid PostgreSQL SQL script that:
1. Finds the university by name (case-insensitive)
2. Inserts/updates majors
3. Inserts/updates program_intakes
4. Inserts/updates program_documents
5. Inserts/updates scholarships and links
6. Returns a final SELECT with counts and errors

CRITICAL OUTPUT RULES:
- Output ONLY valid PostgreSQL SQL text
- NO markdown, NO backticks, NO explanations, NO JSON
- NO characters that are not part of SQL
- Produce a single SQL script that completes everything in one run
- Script must be idempotent (safe to run multiple times)
- NO DROP/TRUNCATE/DELETE operations

SCHEMA TABLES:
- universities(id, name, ...)
- majors(id, university_id, name, degree_level, teaching_language, duration_years, discipline, category, keywords JSON, is_featured, is_active)
- program_intakes(id, university_id, major_id, intake_term enum IntakeTerm('March','September','Other'), intake_year, application_deadline timestamptz, deadline_type, program_start_date date, tuition_per_semester, tuition_per_year, application_fee, accommodation_fee, accommodation_fee_period, accommodation_note, service_fee, medical_insurance_fee, medical_insurance_fee_period, arrival_medical_checkup_fee, arrival_medical_checkup_is_one_time, visa_extension_fee, notes, scholarship_available, scholarship_info, age_min, age_max, min_average_score, interview_required, written_test_required, acceptance_letter_required, inside_china_applicants_allowed, inside_china_extra_requirements, bank_statement_required, bank_statement_amount, bank_statement_currency, bank_statement_note, hsk_required, hsk_level, hsk_min_score, english_test_required, english_test_note, currency, teaching_language, duration_years, degree_type)
- program_documents(program_intake_id, name, is_required, rules, applies_to)
- scholarships(id, name, provider, notes)
- program_intake_scholarships(program_intake_id, scholarship_id, covers_tuition, covers_accommodation, covers_insurance, tuition_waiver_percent, living_allowance_monthly, living_allowance_yearly, first_year_only, renewal_required, deadline, eligibility_note)

MANDATORY FIXES (APPLY ALL):

1) INTAKE_TERM ENUM CAST (CRITICAL):
   - program_intakes.intake_term is a PostgreSQL enum type named 'intaketerm'
   - The ACTUAL enum values in the database are: {enum_values_str} (use these EXACT strings, case-sensitive)
   - ALWAYS cast enum values using the exact strings above: '{enum_values[0] if enum_values else "March"}'::intaketerm
   - NEVER use 'March'::text or just 'March' without the ::intaketerm cast
   - The enum type name is 'intaketerm' (lowercase, no spaces)
   - Apply enum cast in: data CTEs, joins, WHERE clauses, INSERT/UPDATE statements
   - CRITICAL: Use the EXACT enum values listed above - do not use variations like 'march' or 'MARCH' unless they match exactly

2) DEADLINE MAPPING:
   - application_deadline: Store the PRIMARY deadline date (usually university deadline) as timestamptz
   - deadline_type: Clean label like 'University' or 'CSC' (NO dates in this field)
   - If multiple deadlines exist (e.g., CSC vs University):
     * Store university deadline in application_deadline with deadline_type='University'
     * Add CSC deadline to notes field: "CSC deadline: YYYY-MM-DD."
   - Do NOT put dates or "CSC-February 10th" into deadline_type field

3) SCHOLARSHIP-SPECIFIC ACCOMMODATION:
   - Keep program_intakes.accommodation_fee/accommodation_fee_period NULL unless there is a GENERAL accommodation fee for ALL students
   - If accommodation fee varies by scholarship type (e.g., Type B requires paid accommodation 4500-9000 RMB/year):
     * Store this in program_intake_scholarships.eligibility_note: "Accommodation fee: 4500-9000 RMB/year (paid by student)"
     * OR in program_intakes.notes if eligibility_note field is not available
   - Do NOT set program_intakes.accommodation_fee for scholarship-specific accommodation rules
   - When storing ranges, record the lower bound (e.g., 4500) in the note, not in numeric field

4) DO NOT INVENT SCHOLARSHIP FIELDS:
   - Only set first_year_only=true if document explicitly states scholarship is first year only
   - Only set tuition_waiver_percent=100 if document explicitly states full tuition waiver
   - Registration fee "only first year" and medical "only one year" are UNIVERSITY payments, NOT scholarship duration fields
   - Keep scholarship link fields conservative:
     * covers_tuition/covers_accommodation/covers_insurance: only when explicitly stated
     * living_allowance_monthly: from document if provided
     * Everything else NULL unless document explicitly states

5) ERRORS AGGREGATION (MUST BE FLAT text[]):
   - Errors CTE must be rows of text, NOT an array column
   - Use this pattern:
     errors AS (
       SELECT NULL::text AS err WHERE false
       UNION ALL SELECT 'University ... not found' WHERE NOT EXISTS (SELECT 1 FROM university_cte)
       UNION ALL SELECT 'Missing intake year' WHERE ...
       UNION ALL SELECT '...' WHERE ...
     )
   - Final errors column:
     (SELECT COALESCE(array_agg(err), ARRAY[]::text[]) FROM errors WHERE err IS NOT NULL) AS errors

6) DOCUMENTS LIST (MUST MATCH DOCUMENT COMPLETELY):
   - Extract ALL documents listed in the document
   - Normalize names properly:
     * "Last Academic Transcript and Certificate (Notarized)" → "Transcript" (rules: "Notarized") AND "Highest Degree Certificate" (rules: "Notarized")
     * "English Proficiency Certificate (IELTS 6.0 / TOEFL 80 / other)" → "English Proficiency Certificate" (rules: "IELTS 6.0 / TOEFL 80 / other")
     * "Health Check Up Certificate" → "Health Check Up Form"
     * "Police Clearance" → "Non Criminal Record"
     * "Two recommendation Letter (From Professors / Associate Professors)" → "Recommendation Letter" (rules: "Two letters from Professors/Associate Professors")
     * "Study Plan / Research Proposal" → "Study Plan"
     * "Work Experience Certificate (Including internship) required (one year or longer preferred)" → "Work Experience Certificate" (rules: "Required; one year or longer preferred; internships included")
     * "Publication (If Applicable)" → "Publication" (is_required=false, applies_to='if_applicable')
     * "Resume" → "Resume"
     * "Award/Extracurricular certificates" → "Award/Extracurricular Certificates"
   - Ensure program_documents is upserted for BOTH newly inserted AND already-existing program_intakes matching this document

7) KEYWORDS (SUBJECT-ONLY, NO LOCATION/CAMPUS):
   - keywords JSON must be array of 1-5 items
   - Must ONLY be major/subject similarity synonyms/abbreviations
   - REMOVE: campus names, university names, location names, intake terms, years, scholarship names
   - Examples to REMOVE: "zhuhai", "bnu", "beijing", "campus", "china", "march", "2026", "csc"
   - Examples to KEEP: "cs", "computer science", "cse", "computing", "engineering"

8) UNIVERSITY GUARD (NO-OP IF UNIVERSITY NOT FOUND):
   - Add guard CTE: guard AS (SELECT 1 AS ok FROM university_cte)
   - Every INSERT/UPDATE source must select from guard (directly or via WHERE EXISTS (SELECT 1 FROM guard))
   - This makes the script a no-op when university is not found
   - Only the final SELECT with errors should run if university not found

9) DOC-FAITHFUL NOTES FOR UNIVERSITY PAYMENTS:
   - application_fee: Store numeric value (e.g., 600)
   - medical_insurance_fee: Store numeric value (e.g., 400), period 'year' is OK, but add to notes: "Medical fee only for one year."
   - visa_extension_fee: Store numeric value (e.g., 400), add to notes: "Visa extension fee: 400 CNY per year."
   - registration fee: NOT a mapped numeric field, put in notes: "Registration fee: 800 CNY (only first year)."
   - Include CSC deadline note and Type B accommodation rule note in notes field accordingly

DATA EXTRACTION RULES:
A) University: Extract name, find university_id with: SELECT id FROM universities WHERE lower(name)=lower(:university_name) LIMIT 1
B) Majors: 
   CRITICAL GUARDRAIL: NEVER INSERT a major if it already exists. Always check first!
   Matching criteria: university_id AND lower(name) AND degree_level AND teaching_language
   MUST use one of these patterns:
   - INSERT INTO majors (...) SELECT ... WHERE NOT EXISTS (SELECT 1 FROM majors WHERE university_id=... AND lower(name)=lower(...) AND degree_level=... AND teaching_language=...)
   - INSERT INTO majors (...) VALUES (...) ON CONFLICT DO NOTHING (if unique constraint exists)
   - First SELECT to check existence, then INSERT only if not found
   If major exists, UPDATE it instead of inserting. Use UPDATE ... WHERE university_id=... AND lower(name)=lower(...) AND degree_level=... AND teaching_language=...
   keywords JSON must be array of 1-5 keywords (subject-only, no location/campus). category: "Non-degree/Language Program" for language/foundation/non-degree, else "Degree Program"
C) Program Intakes: intake_term must use enum cast 'March'::intaketerm, 'September'::intaketerm, or 'Other'::intaketerm. intake_year required.
D) Fees: tuition_per_semester, tuition_per_year, application_fee, service_fee, medical_insurance_fee, arrival_medical_checkup_fee, visa_extension_fee. Use 'CNY' if RMB/CNY, 'USD' if USD, else default 'CNY'. Keep accommodation_fee NULL unless general fee for all students.
E) Deadlines: application_deadline as timestamptz (primary deadline), deadline_type as clean label (no dates), additional deadlines in notes
F) Requirements: bank_statement_required, bank_statement_amount, bank_statement_currency, bank_statement_note, hsk_required, hsk_level, hsk_min_score, english_test_required, english_test_note, age_min, age_max, min_average_score
G) Program Documents: Extract ALL documents from document, normalize names, capture rules, set is_required and applies_to appropriately
H) Scholarships: Create scholarship rows and link via program_intake_scholarships. Only set fields explicitly stated in document.

ERROR HANDLING:
- If university_id not found, DO NOT insert anything. Only return errors in final SELECT.
- Use guard CTE to prevent all inserts/updates when university not found
- Maintain error list: missing intake year, unknown university, conflicting fees, unclear currency, etc.
- Errors must be flat text[] array (use UNION ALL pattern, then array_agg)
- Final SELECT must return: majors_inserted, majors_updated, program_intakes_inserted, program_intakes_updated, documents_inserted, documents_updated, scholarships_inserted, scholarships_updated, links_inserted, errors text array

SQL STYLE:
- Use WITH CTE patterns
- Add guard CTE that depends on university_cte
- CRITICAL: For majors, ALWAYS check existence before INSERT. Use INSERT ... WHERE NOT EXISTS or check with SELECT first
- NEVER insert a major without checking if it already exists (same university_id + lower(name) + degree_level + teaching_language)
- Use INSERT ... ON CONFLICT DO NOTHING or INSERT ... WHERE NOT EXISTS + UPDATE for idempotency
- Use lower(name)=lower(:name) matching
- Use COALESCE where needed
- ALWAYS cast intake_term enum: 'March'::intaketerm, 'September'::intaketerm, 'Other'::intaketerm
- If anything is ambiguous or missing, write issue into errors array, keep nullable fields NULL

VALIDATION CHECKLIST (self-check before output):
- No 'March'::text remains (must be 'March'::intaketerm)
- deadline_type is clean label (no dates)
- errors is flat text[] (not array column)
- program_documents includes ALL items from document with proper normalization
- keywords contain only subject terms (no location/campus/university names)
- No invented scholarship fields (first_year_only, tuition_waiver_percent) unless doc states
- Type B accommodation range is in eligibility_note/notes, not overriding intake accommodation fields
- Guard CTE prevents all inserts/updates when university not found

OUTPUT FORMAT:
Pure SQL only. Start with WITH clauses (university_cte, guard, data CTEs), then INSERT/UPDATE statements (all guarded), end with final SELECT showing counts and errors."""

        user_prompt = f"""Document text to parse:

{document_text}

Generate the PostgreSQL SQL script following all rules above. Output ONLY SQL, nothing else."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        try:
            # Log context size for debugging
            total_chars = len(system_prompt) + len(user_prompt)
            estimated_tokens = total_chars / 4  # Rough estimate
            print(f"📊 SQL Generation Context: ~{estimated_tokens:.0f} tokens ({total_chars:,} chars)")
            
            # Use chat_completion with retry logic (max_retries handled in openai_service)
            response = self.openai_service.chat_completion(
                messages=messages,
                temperature=0.1,  # Low temperature for deterministic SQL
                top_p=0.9,
                max_retries=3  # Retry up to 3 times for connection errors
            )
            
            # Log token usage if available
            if hasattr(response, 'usage') and response.usage:
                print(f"📊 Tokens used: {response.usage.total_tokens} (prompt: {response.usage.prompt_tokens}, completion: {response.usage.completion_tokens})")
            
            sql_content = response.choices[0].message.content.strip()
            
            # Clean up: Remove markdown code blocks if present
            sql_content = re.sub(r'^```(?:sql|postgresql|sqlite)?\s*\n', '', sql_content, flags=re.MULTILINE)
            sql_content = re.sub(r'\n```\s*$', '', sql_content, flags=re.MULTILINE)
            sql_content = sql_content.strip()
            
            # Log SQL generation result
            if sql_content:
                print(f"✅ SQL generated: {len(sql_content)} characters")
                print(f"📄 First 200 chars: {sql_content[:200]}...")
            else:
                print("⚠️  WARNING: SQL content is empty after generation")
            
            return sql_content
            
        except Exception as e:
            # Log the error
            import traceback
            from openai import APIConnectionError, APITimeoutError, RateLimitError
            
            error_trace = traceback.format_exc()
            error_type = type(e).__name__
            error_msg = str(e)
            
            print(f"❌ Exception in SQL generation ({error_type}): {error_msg}")
            
            # Provide user-friendly error messages
            if isinstance(e, APIConnectionError):
                user_error = "OpenAI API connection failed. Please check your internet connection and try again."
            elif isinstance(e, APITimeoutError):
                user_error = "OpenAI API request timed out. The document may be too large. Please try with a smaller document or try again later."
            elif isinstance(e, RateLimitError):
                user_error = "OpenAI API rate limit exceeded. Please wait a few minutes and try again."
            else:
                user_error = f"SQL generation failed: {error_msg}"
            
            # Return error SQL if generation fails
            error_sql = f"""-- SQL Generation Error: {user_error}
SELECT 
    0 as majors_inserted,
    0 as majors_updated,
    0 as program_intakes_inserted,
    0 as program_intakes_updated,
    0 as documents_inserted,
    0 as documents_updated,
    0 as scholarships_inserted,
    0 as scholarships_updated,
    0 as links_inserted,
    ARRAY['SQL generation failed: {str(e)}'] as errors;"""
            return error_sql
    
    def validate_sql(self, sql: str) -> Dict[str, any]:
        """Basic validation of generated SQL"""
        errors = []
        warnings = []
        
        # Check for dangerous operations
        dangerous_patterns = [
            (r'\bDROP\s+TABLE\b', 'DROP TABLE detected'),
            (r'\bTRUNCATE\b', 'TRUNCATE detected'),
            (r'\bDELETE\s+FROM\s+universities\b', 'DELETE from universities detected'),
            (r'\bDELETE\s+FROM\s+majors\b', 'DELETE from majors detected'),
            (r'\bDELETE\s+FROM\s+program_intakes\b', 'DELETE from program_intakes detected'),
        ]
        
        for pattern, message in dangerous_patterns:
            if re.search(pattern, sql, re.IGNORECASE):
                errors.append(message)
        
        # Check for required final SELECT
        if not re.search(r'SELECT.*majors_inserted', sql, re.IGNORECASE):
            warnings.append("Final SELECT with counts may be missing")
        
        # Check for university lookup
        if not re.search(r'universities.*WHERE.*lower\(name\)', sql, re.IGNORECASE):
            warnings.append("University lookup may be missing")
        
        # Check for major existence guardrail
        major_insert_patterns = [
            r'INSERT.*majors.*WHERE\s+NOT\s+EXISTS',
            r'INSERT.*majors.*ON\s+CONFLICT',
            r'INSERT.*majors.*SELECT.*WHERE\s+NOT\s+EXISTS',
        ]
        has_major_guardrail = any(re.search(pattern, sql, re.IGNORECASE | re.DOTALL) for pattern in major_insert_patterns)
        
        # Check for direct INSERT INTO majors without guardrail
        direct_insert_pattern = r'INSERT\s+INTO\s+majors\s*\([^)]+\)\s*VALUES'
        has_direct_insert = re.search(direct_insert_pattern, sql, re.IGNORECASE)
        
        if has_direct_insert and not has_major_guardrail:
            errors.append("CRITICAL: Major insertion detected without existence check. Must use WHERE NOT EXISTS or ON CONFLICT to prevent duplicates.")
        
        # Check for major existence check in CTE or subquery
        if re.search(r'INSERT.*majors', sql, re.IGNORECASE) and not has_major_guardrail:
            # Check if there's a SELECT before INSERT that checks existence
            if not re.search(r'SELECT.*FROM\s+majors.*WHERE.*university_id.*AND.*lower\(name\)', sql, re.IGNORECASE | re.DOTALL):
                warnings.append("Major insertion may be missing existence check. Ensure majors are not inserted if they already exist.")
        
        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings
        }

