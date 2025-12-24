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
                print(f"📋 Queried enum values: {values}")
                return values if values else ['March', 'September', 'Other']  # Fallback
            finally:
                db.close()
        except Exception as e:
            print(f"⚠️  Could not query enum values: {e}, using defaults")
            # Use Python enum values as fallback
            from app.models import IntakeTerm
            return [e.value for e in IntakeTerm]  # ['March', 'September', 'Other']
    
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
        
        # Get actual enum values from database (with timeout to avoid blocking)
        try:
            enum_values = self._get_intake_term_enum_values()
        except Exception as e:
            print(f"⚠️  Enum query failed: {e}, using Python enum values")
            from app.models import IntakeTerm
            enum_values = [e.value for e in IntakeTerm]
        
        enum_values_str = ', '.join([f"'{v}'" for v in enum_values])
        
        system_prompt = f"""Generate PostgreSQL SQL for MalishaEdu university program import.

Task: Generate ONE PostgreSQL SQL script that:
1. Finds university by name (case-insensitive: WHERE lower(name)=lower('...'))
2. Upserts majors (check existence first: university_id + lower(name) + degree_level + teaching_language)
3. Upserts program_intakes (with enum cast: '{enum_values[0] if enum_values else "March"}'::intaketerm)
4. Upserts program_documents (all from doc, normalized names)
5. Upserts scholarships and links
6. Final SELECT: majors_inserted, majors_updated, program_intakes_inserted, program_intakes_updated, documents_inserted, documents_updated, scholarships_inserted, links_inserted, errors text[]

OUTPUT: Pure SQL only. No markdown/backticks/explanations. Idempotent. No DROP/TRUNCATE/DELETE.

TABLES:
- universities(id, name)
- majors(id, university_id, name, degree_level, teaching_language, duration_years, discipline, category, keywords JSON, is_featured, is_active)
  * degree_level: Use 'Master' (capitalized), NOT 'masters' or 'Masters'. Valid values: 'Bachelor', 'Master', 'Phd', 'Language Program', 'Associate', 'Vocational College', 'Non Degree', 'Junior high', 'Senior high'
- program_intakes(id, university_id, major_id, intake_term enum, intake_year, application_deadline timestamptz, deadline_type, program_start_date date, tuition_per_year, application_fee, accommodation_fee, accommodation_fee_period, accommodation_note, service_fee, medical_insurance_fee, medical_insurance_fee_period, arrival_medical_checkup_fee, arrival_medical_checkup_is_one_time, visa_extension_fee, notes, scholarship_available, scholarship_info, age_min, age_max, min_average_score, interview_required, written_test_required, acceptance_letter_required, inside_china_applicants_allowed, inside_china_extra_requirements, bank_statement_required, bank_statement_amount, bank_statement_currency, bank_statement_note, hsk_required, hsk_level, hsk_min_score, english_test_required, english_test_note, currency, teaching_language, duration_years, degree_type)
- program_documents(program_intake_id, name, is_required, rules, applies_to)
- scholarships(id, name, provider, notes)
- program_intake_scholarships(program_intake_id, scholarship_id, covers_tuition, covers_accommodation, covers_insurance, tuition_waiver_percent, living_allowance_monthly, living_allowance_yearly, first_year_only, renewal_required, deadline, eligibility_note)

RULES:
1) ENUM: intake_term = '{enum_values[0] if enum_values else "March"}'::intaketerm (use EXACT values: {enum_values_str}, case-sensitive). Type: 'intaketerm'. Do NOT use 'MARCH' if enum has 'March'.
2) DEADLINE: application_deadline=timestamptz (primary deadline, usually university), deadline_type='University' (no dates). If CSC deadline exists, add to notes: "CSC deadline: YYYY-MM-DD."
3) ACCOMMODATION FEES (CRITICAL): 
   - If document shows accommodation fee ANYWHERE (e.g., "Accommodation: 4500-9000RMB/Year" or "Type B: 4500-9000 RMB/year"):
     * Set accommodation_fee = LOWER BOUND (e.g., 4500, NOT 9000) in program_intakes
     * Set accommodation_fee_period = 'year' or 'month' or 'semester' as specified
     * Set accommodation_note = FULL TEXT from document (e.g., "Accommodation: 4500-9000RMB(645$-1290$)/Year")
     * DO NOT leave accommodation_fee or accommodation_note as NULL if document mentions accommodation fee
   - If accommodation is scholarship-specific (e.g., "Type B: 4500-9000 RMB/year"):
     * ALSO put in program_intake_scholarships.eligibility_note: "Accommodation fee: 4500-9000 RMB/year (paid by student)"
     * BUT STILL set program_intakes.accommodation_fee = 4500 and accommodation_note with full text
   - ALWAYS use LOWER BOUND for numeric fee fields when range is given
   - EXAMPLE: "Accommodation: 4500-9000RMB(645$-1290$)/Year" → accommodation_fee=4500, accommodation_fee_period='year', accommodation_note='Accommodation: 4500-9000RMB(645$-1290$)/Year'
4) FEES WITH RANGES: For ANY fee with range (e.g., "2500-3500", "4500-9000"):
   - Numeric field = LOWER BOUND value
   - Full range text goes in corresponding _note field (accommodation_note, notes, etc.)
   - Example: "Accommodation: 4500-9000RMB/Year" → accommodation_fee=4500, accommodation_note="Accommodation: 4500-9000RMB(645$-1290$)/Year"
5) SCHOLARSHIPS: Only set fields from doc. first_year_only should be NULL or false unless document explicitly states scholarship is first year only. Registration/medical fees "only first year" are UNIVERSITY payments, NOT scholarship duration. tuition_waiver_percent=100 only if doc explicitly states full tuition waiver.
6) ERRORS: errors AS (SELECT NULL::text AS err WHERE false UNION ALL SELECT '...' WHERE ...), final: array_agg(err) AS errors
7) DOCUMENTS: Extract ALL  documents. CRITICAL: "Last Academic Transcript and Certificate(Notarized)" = TWO documents: "Transcript" (rules: "Notarized") AND "Highest Degree Certificate" (rules: "Notarized"). "English Proficiency Certificate(IELTS...)" = "English Proficiency Certificate" (rules: include IELTS/TOEFL requirements). Normalize: "Health Check Up Certificate"→"Health Check Up Form", "Police Clearance"→"Non Criminal Record", "Two recommendation Letter"→"Recommendation Letter" (rules: "Two letters from Professors/Associate Professors"), "Study Plan /Research Proposal"→"Study Plan", "Work Experience Certificate" (include rules), "Publication(If Applicable)"→"Publication" (is_required=false, applies_to='if_applicable'), "Resume", "Award/Extracurricular certificates"→"Award/Extracurricular Certificates"
8) KEYWORDS (CRITICAL - TYPE CASTING): JSON array format: '["keyword1","keyword2"]'::jsonb. 1-5 items, subject-only. Remove: campus/university/location/intake/year/scholarship names (e.g., remove "zhuhai", "bnu", "beijing", "campus", "china", "march", "2026", "csc"). CRITICAL: keywords column is jsonb type. 
   - If using VALUES in majors_data: ('Major Name', 'Master', 'English', 2, NULL, NULL, '["keyword1","keyword2"]'::jsonb) - cast in VALUES
   - If using SELECT in majors_data: NULL::jsonb AS keywords or '["keyword1","keyword2"]'::jsonb AS keywords - cast in SELECT
   - NEVER use NULL without ::jsonb cast. ALWAYS cast to jsonb: '["keyword1","keyword2"]'::jsonb or NULL::jsonb
9) RETURNING CLAUSE (CRITICAL): ALL INSERT/UPDATE CTEs that will be referenced later for counting MUST have a RETURNING clause. This is MANDATORY for: majors_upsert (RETURNING id or RETURNING 1), majors_update (RETURNING m.id or RETURNING 1), program_intakes_upsert (RETURNING id or RETURNING 1), program_intakes_update (RETURNING pi.id or RETURNING 1), program_documents_upsert (RETURNING 1), program_documents_update (RETURNING 1), scholarships_upsert (RETURNING 1), scholarships_update (RETURNING 1), program_intake_scholarships_upsert (RETURNING 1). NO EXCEPTIONS. PostgreSQL requires RETURNING clause if you want to count rows from the CTE later. Example: INSERT INTO majors ... RETURNING 1 (or RETURNING id if you need the ID)
10) GUARD (CRITICAL): guard AS (SELECT 1 AS ok FROM university_cte), ALL INSERT/UPDATE statements MUST include WHERE EXISTS (SELECT 1 FROM guard). This is MANDATORY for: majors_upsert, majors_update, program_intakes_upsert, program_intakes_update, program_documents_upsert, program_documents_update, scholarships_upsert, program_intake_scholarships_upsert. NO EXCEPTIONS. Example: INSERT INTO majors ... WHERE EXISTS (SELECT 1 FROM guard) AND NOT EXISTS (...)
11) INSERTION ORDER (CRITICAL - EXECUTION SEQUENCE): 
    - PostgreSQL WITH CTEs execute in order: majors_upsert executes FIRST and commits, THEN majors_update executes, THEN program_intakes_data can see the inserted/updated majors
    - CRITICAL: program_intakes_data MUST be defined AFTER majors_upsert and majors_update CTEs in the WITH clause
    - CRITICAL: program_intakes_data MUST JOIN with majors_data CTE to get the exact majors that were processed, then JOIN with actual majors table to get the IDs. Use: FROM majors_data md JOIN majors m ON m.university_id = md.university_id AND lower(m.name) = lower(md.name) AND m.degree_level = md.degree_level AND m.teaching_language = md.teaching_language. This ensures you get the exact majors that were inserted/updated, regardless of existing data or WHERE clause matching issues.
    - ALTERNATIVE (if JOIN doesn't work): Use WHERE clause with EXACT matching: FROM majors m WHERE m.university_id = (SELECT id FROM university_cte) AND lower(m.name) IN (lower('Major Name 1'), lower('Major Name 2')) AND m.degree_level = 'Master' AND m.teaching_language = 'English'. Use EXACT string values from majors_data: if majors_data has 'Master', use 'Master' (not 'masters' or 'Masters'). If majors_data has 'English', use 'English' (not 'english' or 'ENGLISH')
    - CRITICAL: The majors_update CTE MUST update degree_level and teaching_language if they might differ from existing data, so program_intakes_data can find them. Include degree_level and teaching_language in the UPDATE SET clause if they are in majors_data.
    - After program_intakes_upsert/update execute, program_documents_data must use: FROM program_intakes pi WHERE pi.university_id = (SELECT id FROM university_cte) AND pi.intake_term = 'MARCH'::intaketerm AND pi.intake_year = 2026 (reference actual table, use exact enum value)
    - After program_intakes_upsert/update execute, program_intake_scholarships_data must use: FROM program_intakes pi WHERE pi.university_id = (SELECT id FROM university_cte) AND pi.intake_term = 'MARCH'::intaketerm AND pi.intake_year = 2026 (reference actual table, use exact enum value)
    - If program_intakes_data returns 0 rows, check: 1) Did majors_upsert actually insert? 2) Do the WHERE criteria match exactly? 3) Are the enum values correct? 4) PREFERRED: Use JOIN with majors_data CTE instead of WHERE clause to ensure exact matching
12) NOTES: Combine all fee notes: "Registration fee: 800 CNY (only first year). Medical fee only for one year. Visa extension fee: 400 CNY per year. CSC deadline: YYYY-MM-DD." (if CSC deadline exists). Include accommodation note if applicable. CRITICAL: medical_insurance_fee MUST be set to numeric value (e.g., 400) if document mentions medical fee amount. Do NOT leave it NULL if document states "Medical: 400CNY" or similar.

EXTRACTION:
- University: WHERE lower(name)=lower('...')
- Majors: Check existence first (university_id + lower(name) + degree_level + teaching_language), then INSERT ... WHERE NOT EXISTS or UPDATE. MUST insert majors before using them in program_intakes. Use 'Master' (capitalized, singular) for degree_level, NOT 'masters' or 'Masters'. Valid: 'Bachelor', 'Master', 'Phd', 'Language Program'. CRITICAL: keywords column is jsonb type. In majors_data CTE, always cast: NULL::jsonb AS keywords (if no keywords) or '["keyword1","keyword2"]'::jsonb AS keywords. NEVER use just NULL without ::jsonb cast.
- Intakes: intake_term='{enum_values[0] if enum_values else "March"}'::intaketerm, intake_year required. JOIN with majors FROM ACTUAL TABLE with ALL matching criteria: FROM majors m WHERE m.university_id = (SELECT id FROM university_cte) AND lower(m.name) IN (...) AND m.degree_level = 'Master' AND m.teaching_language = 'English' (use EXACT values from majors_data - if majors_data has 'Master', use 'Master' in WHERE clause)
- Fees: 
  * CNY if RMB/CNY, USD if USD, else CNY
  * accommodation_fee: If document mentions accommodation fee ANYWHERE (even in scholarship section), use LOWER BOUND (e.g., 4500) in numeric field, put FULL TEXT in accommodation_note. DO NOT leave NULL if document mentions accommodation.
  * accommodation_fee_period: 'year'/'month'/'semester' as specified in document
  * accommodation_note: Full text from doc (e.g., "Accommodation: 4500-9000RMB(645$-1290$)/Year"). MUST be set if accommodation fee is mentioned.
  * medical_insurance_fee MUST be numeric if doc mentions medical fee (e.g., "Medical: 400CNY" → medical_insurance_fee=400, medical_insurance_fee_period='year')
  * application_fee from doc (e.g., "application fee of 600 RMB" → application_fee=600)
  * visa_extension_fee from doc (e.g., "Visa Fees: 400CNY/Year" → visa_extension_fee=400)
  * For ANY fee with range, use LOWER BOUND in numeric field, full text in note field
- Requirements:
  * english_test_required=true if doc mentions "English Proficiency Certificate" or "IELTS" or "TOEFL"
  * english_test_note should include requirements (e.g., "IELTS 6.0 or TOFEL 80 or any other valid English Proficiency certificate")
- Documents: All from doc, normalized, with rules. "Last Academic Transcript and Certificate(Notarized)" = TWO documents: "Transcript" (rules: "Notarized") AND "Highest Degree Certificate" (rules: "Notarized"). Use: FROM program_intakes pi WHERE pi.university_id = (SELECT id FROM university_cte) AND pi.intake_term = '...' AND pi.intake_year = ... (reference actual table, not CTE)
- Scholarships: Create and link, only explicit fields. first_year_only should be NULL/false unless doc explicitly states scholarship duration is first year only. Use: FROM program_intakes pi WHERE pi.university_id = (SELECT id FROM university_cte) AND pi.intake_term = '...' AND pi.intake_year = ... (reference actual table, not CTE)

STYLE: Use WITH CTEs pattern. Idempotent. Pure SQL. Follow this EXACT structure (order matters):
1) university_cte: SELECT id FROM universities WHERE lower(name)=lower('...')
2) guard: SELECT 1 AS ok FROM university_cte
3) majors_data: Use VALUES with explicit casting: (university_id, name, degree_level, teaching_language, duration_years, discipline, category, '["kw1","kw2"]'::jsonb, is_featured, is_active) OR use SELECT with casting: NULL::jsonb AS keywords or '["kw1","kw2"]'::jsonb AS keywords
4) majors_upsert: INSERT INTO majors ... FROM majors_data WHERE EXISTS (SELECT 1 FROM guard) AND NOT EXISTS (...) RETURNING 1 (or RETURNING id)
5) majors_update: UPDATE majors ... FROM majors_data WHERE EXISTS (SELECT 1 FROM guard) AND ... RETURNING 1 (or RETURNING m.id)
6) program_intakes_data: PREFERRED APPROACH - JOIN with majors_data CTE to get exact majors processed: SELECT m.id AS major_id, m.university_id, ... FROM majors_data md JOIN majors m ON m.university_id = md.university_id AND lower(m.name) = lower(md.name) AND m.degree_level = md.degree_level AND m.teaching_language = md.teaching_language. This ensures you get the exact majors that were inserted/updated, regardless of existing data.
   - ALTERNATIVE: SELECT FROM majors m WHERE m.university_id = (SELECT id FROM university_cte) AND lower(m.name) IN (lower('Exact Name 1'), lower('Exact Name 2')) AND m.degree_level = 'Master' AND m.teaching_language = 'English'. Use EXACT same values from majors_data: if majors_data has 'Master', use 'Master' (case-sensitive). If majors_data has 'English', use 'English' (case-sensitive). For names, use lower() for case-insensitive matching but include the EXACT strings from majors_data
7) program_intakes_upsert: INSERT INTO program_intakes ... FROM program_intakes_data WHERE EXISTS (SELECT 1 FROM guard) AND NOT EXISTS (...) RETURNING 1 (or RETURNING id)
8) program_intakes_update: UPDATE program_intakes ... FROM program_intakes_data WHERE EXISTS (SELECT 1 FROM guard) AND ... RETURNING 1 (or RETURNING pi.id)
9) program_documents_data: SELECT FROM program_intakes pi (actual table, MUST be after program_intakes_upsert/update) WHERE pi.university_id = (SELECT id FROM university_cte) AND pi.intake_term = 'MARCH'::intaketerm AND pi.intake_year = 2026
10) program_documents_upsert: INSERT INTO program_documents ... FROM program_documents_data WHERE EXISTS (SELECT 1 FROM guard) AND NOT EXISTS (...) RETURNING 1 (MANDATORY - needed for counting)
11) program_documents_update: UPDATE program_documents ... FROM program_documents_data WHERE EXISTS (SELECT 1 FROM guard) AND ... RETURNING 1 (MANDATORY - needed for counting)
12) scholarships_upsert: INSERT INTO scholarships ... FROM scholarships_data WHERE EXISTS (SELECT 1 FROM guard) AND NOT EXISTS (...) RETURNING 1 (MANDATORY - needed for counting)
13) scholarships_update: UPDATE scholarships ... FROM scholarships_data WHERE EXISTS (SELECT 1 FROM guard) AND ... RETURNING 1 (MANDATORY - needed for counting)
14) program_intake_scholarships_data: SELECT FROM program_intakes pi (actual table, MUST be after program_intakes_upsert/update) WHERE pi.university_id = (SELECT id FROM university_cte) AND pi.intake_term = 'MARCH'::intaketerm AND pi.intake_year = 2026
15) program_intake_scholarships_upsert: INSERT INTO program_intake_scholarships ... FROM program_intake_scholarships_data WHERE EXISTS (SELECT 1 FROM guard) AND NOT EXISTS (...) RETURNING 1 (MANDATORY - needed for counting)
16) Final SELECT: counts + errors array
CRITICAL: 
- CTE execution order: majors_upsert executes and commits, THEN majors_update executes, THEN program_intakes_data can see the inserted/updated majors
- Always reference actual database tables (majors, program_intakes) AFTER their INSERT/UPDATE CTEs, never reference CTEs with uninserted data
- Use EXACT string matching: if majors_data inserts 'Master', program_intakes_data WHERE must use 'Master' (not 'Masters' or 'master')"""

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

