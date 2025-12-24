"""
Document Extraction Service
LLM extracts structured data from documents into JSON only.
NO SQL generation - that is handled by the ingestion service.
"""
from typing import Dict, Optional
from app.services.openai_service import OpenAIService
from app.services.document_parser import DocumentParser
from app.schemas.document_import import ExtractedData
import json
import re


class DocumentExtractionService:
    """Service to extract structured data from university program documents using LLM"""
    
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
            try:
                return file_content.decode('utf-8', errors='ignore')
            except:
                raise ValueError(f"Unsupported file type: {file_type}")
    
    def extract_data_from_text(self, document_text: str) -> Dict:
        """Extract structured data from document text using LLM"""
        
        # Truncate document if too large (conservative limit for LLM context)
        MAX_DOCUMENT_CHARS = 400000  # ~100K tokens
        original_length = len(document_text)
        if len(document_text) > MAX_DOCUMENT_CHARS:
            print(f"⚠️  Document is too large ({original_length} chars). Truncating to {MAX_DOCUMENT_CHARS} chars.")
            first_part = document_text[:int(MAX_DOCUMENT_CHARS * 0.8)]
            last_part = document_text[-int(MAX_DOCUMENT_CHARS * 0.2):]
            document_text = first_part + "\n\n[... document truncated ...]\n\n" + last_part
            print(f"📊 Truncated document: {len(document_text)} chars")
        
        system_prompt = """You are a data extraction agent for MalishaEdu university program import system.

YOUR TASK:
Extract factual data from university program documents and output STRICT JSON only.

CRITICAL RULES:
1. Output ONLY valid JSON. No markdown, no code blocks, no explanations, no SQL.
2. Extract ONLY facts present in the document. Do NOT infer, guess, or invent data.
3. Use NULL (null in JSON) for missing values. Do NOT invent values.
4. Use EXACT enum values from the allowed lists below.
5. If you cannot extract required data, add an error message to the errors array.

OUTPUT FORMAT:
You must output a JSON object matching this exact structure.

OPTIMIZATION FOR MANY MAJORS:
If a document lists many majors (5+) that ALL share the same:
- degree_level, teaching_language, duration_years, discipline, category, keywords
- AND the same intake(s) with same fees, requirements, documents, scholarships

Then use "major_groups" instead of "majors" to avoid repeating the same data:
{
  "university_name": "University Name",
  "major_groups": [
    {
      "major_names": ["Major 1", "Major 2", "Major 3", ...],  // List of major names
      "degree_level": "Phd",
      "teaching_language": "Chinese",
      "duration_years": 4.0,
      "discipline": "Engineering",
      "category": "Degree Program",
      "keywords": ["engineering", "technology"],
      "is_featured": false,
      "is_active": true,
      "intakes": [/* shared intake data */]
    }
  ]
}

If majors have different properties or intakes, use the regular "majors" format.

IMPORTANT: The "scholarships" array can be:
- An array with 1 or more scholarship objects if scholarships are mentioned
- An empty array [] if no scholarships are mentioned or document says "NO Scholarship"
- Different for different majors/intakes (some may have scholarships, others may not)

Example structure (regular format):
{
  "university_name": "University Name Exactly As In Document",
  "majors": [
    {
      "name": "Major Name Exactly As In Document",
      "degree_level": "Master",  // Must be one of: Bachelor, Master, Phd, Language Program, Associate, Vocational College, Non Degree, Junior high, Senior high
      "teaching_language": "English",  // Must be one of: Chinese, English, Bilingual
      "duration_years": 2.0,  // null if not specified
      "discipline": "Engineering",  // null if not specified
      "category": "Degree Program",  // null, "Degree Program", or "Non-degree/Language Program"
      "keywords": ["keyword1", "keyword2"],  // 1-5 items (prefer 1-3), subject-only, extracted from major name if not in document. NEVER null - always extract at least 1-2 keywords
      "is_featured": false,
      "is_active": true,
      "intakes": [
        {
          "intake_term": "March",  // Must be: March, September, or Other
          "intake_year": 2026,  // REQUIRED - must be extracted
          "application_deadline": "2026-03-10",  // YYYY-MM-DD or YYYY-MM-DD HH:MM:SS, null if not specified
          "deadline_type": "University",  // null or descriptive label (no dates in this field)
          "program_start_date": "2026-09-01",  // YYYY-MM-DD, null if not specified
          "fees": {
            "tuition_per_semester": 6000.0,  // null if not specified
            "tuition_per_year": 12000.0,  // null if not specified
            "application_fee": 400.0,  // null if not specified
            "accommodation_fee": 4500.0,  // LOWER BOUND if range (e.g., 4500-9000 → 4500), null if not specified
            "accommodation_fee_period": "year",  // "month", "year", or "semester", null if not specified
            "accommodation_note": "Accommodation: 4500-9000RMB/Year",  // Full text from document, null if not specified
            "service_fee": null,  // MalishaEdu service fee, null if not specified
            "medical_insurance_fee": 400.0,  // null if not specified
            "medical_insurance_fee_period": "year",  // "year" or "semester", null if not specified
            "arrival_medical_checkup_fee": 400.0,  // null if not specified
            "arrival_medical_checkup_is_one_time": true,  // null if not specified
            "visa_extension_fee": 400.0,  // null if not specified
            "currency": "CNY",  // "CNY" or "USD", default "CNY"
            "notes": "Registration fee: 800 CNY (only first year). Medical fee only for one year. CSC deadline: 2026-02-10."  // Combined fee notes, null if none
          },
          "requirements": {
            "age_min": null,  // null if not specified
            "age_max": null,  // null if not specified
            "min_average_score": null,  // null if not specified
            "interview_required": false,  // null if not specified
            "written_test_required": false,  // null if not specified
            "acceptance_letter_required": true,  // null if not specified
            "inside_china_applicants_allowed": true,  // null if not specified
            "inside_china_extra_requirements": null,  // null if not specified
            "bank_statement_required": null,  // null if not specified - ONLY extract if document explicitly mentions bank statement requirement
            "bank_statement_amount": null,  // null if not specified
            "bank_statement_currency": null,  // "CNY" or "USD", null if not specified
            "bank_statement_note": null,  // null if not specified
            "hsk_required": false,  // null if not specified
            "hsk_level": null,  // null if not specified
            "hsk_min_score": null,  // null if not specified
            "english_test_required": true,  // null if not specified
            "english_test_note": ""  // null if not specified
          },
          "documents": [
            {
              "name": "Passport",  // Normalized name: Passport, Photo, Transcript, Highest Degree Certificate, English Proficiency Certificate, Health Check Up Form, Non Criminal Record, Recommendation Letter, Study Plan, Work Experience Certificate, Publication, Resume, Award/Extracurricular Certificates
              "is_required": true,
              "rules": null,  // e.g., "Notarized", "IELTS 6.0 / TOEFL 80", "Two letters from Professors/Associate Professors"
              "applies_to": null  // null, "if_applicable", "under_18_only", "inside_china_only"
            }
          ],
          "scholarships": [  // Array of scholarship objects. Use [] if no scholarships mentioned or document says "NO Scholarship"
            {
              "name": "CSC type B",  // Scholarship name exactly as in document (e.g., "Chinese Government Scholarship", "First-class Scholarship", "Second-class Scholarship")
              "provider": null,  // null if not specified
              "notes": "Full Tuition fees free, Full Accommodation fees free, Insurance fees free, Monthly stipend: 3000CNY/Month",
              "covers_tuition": true,  // null if not specified
              "covers_accommodation": true,  // null if not specified
              "covers_insurance": true,  // null if not specified
              "tuition_waiver_percent": 100,  // null if not specified, only if doc explicitly states
              "living_allowance_monthly": 3000.0,  // null if not specified
              "living_allowance_yearly": null,  // null if not specified
              "first_year_only": null,  // null or false unless doc explicitly states scholarship is first year only
              "renewal_required": null,  // null if not specified
              "deadline": null,  // YYYY-MM-DD, null if not specified
              "eligibility_note": null  // e.g., "Accommodation fee: 4500-9000 RMB/year (paid by student)" for Type B
            },
            {
              "name": "Type A",  // Extract ALL scholarships mentioned in document
              "provider": null,
              "notes": "Full Tuition fees free, Full Accommodation fees free, Insurance fees free, Monthly stipend: 2000CNY/Month",
              "covers_tuition": true,
              "covers_accommodation": true,
              "covers_insurance": true,
              "tuition_waiver_percent": 100,
              "living_allowance_monthly": 2000.0,
              "living_allowance_yearly": null,
              "first_year_only": null,
              "renewal_required": null,
              "deadline": null,
              "eligibility_note": null
            },
            {
              "name": "Type B",  // If document lists multiple scholarships, extract ALL of them
              "provider": null,
              "notes": "Full Tuition fees free, Insurance fees free, Monthly stipend: 1000CNY/Month",
              "covers_tuition": true,
              "covers_accommodation": false,  // Type B does NOT cover accommodation
              "covers_insurance": true,
              "tuition_waiver_percent": 100,
              "living_allowance_monthly": 1000.0,
              "living_allowance_yearly": null,
              "first_year_only": null,
              "renewal_required": null,
              "deadline": null,
              "eligibility_note": "Accommodation fee: 4500-9000 RMB/year (paid by student)"  // Type B requires student to pay accommodation
            }
          ],  // NOTE: If document has NO scholarships or says "NO Scholarship", use: "scholarships": []
          "scholarship_available": true,  // null if unclear, true/false if clear
          "scholarship_info": "CSC type B, Type A, Type B scholarships available with full tuition, accommodation, insurance and monthly stipends."  // null if not specified
        }
      ]
    }
  ],
  "errors": []  // Array of error/warning messages (e.g., "Missing intake_year for major X", "Unknown university name format")
}

OR (OPTIMIZED FORMAT - Use when 5+ majors share the same properties and intakes):
{
  "university_name": "University Name Exactly As In Document",
  "major_groups": [
    {
      "major_names": ["Major Name 1", "Major Name 2", "Major Name 3", ...],  // List of major names that share the same properties
      "degree_level": "Phd",  // Shared by all majors in group
      "teaching_language": "Chinese",  // Shared by all majors
      "duration_years": 4.0,  // Shared by all majors
      "discipline": "Engineering",  // Shared by all majors (or null)
      "category": "Degree Program",  // Shared by all majors
      "keywords": ["engineering", "technology"],  // Shared by all majors - always extract 1-3 keywords from major names, NEVER null
      "is_featured": false,  // Shared by all majors
      "is_active": true,  // Shared by all majors
      "intakes": [
        {
          "intake_term": "September",  // Must be: March, September, or Other
          "intake_year": 2026,  // REQUIRED
          "application_deadline": "2026-02-28",  // YYYY-MM-DD or YYYY-MM-DD HH:MM:SS
          "deadline_type": "University",  // null or descriptive label
          "program_start_date": "2026-09-01",  // YYYY-MM-DD
          "fees": { /* same structure as above */ },
          "requirements": { /* same structure as above */ },
          "documents": [ /* same structure as above */ ],
          "scholarships": [ /* same structure as above, or [] if none */ ],
          "scholarship_available": true,
          "scholarship_info": "Chinese Government Scholarship available."
        }
      ]
    }
  ],
  "errors": []
}

ENUM VALUES (MUST USE EXACTLY):
- degree_level: "Bachelor", "Master", "Phd", "Language Program", "Associate", "Vocational College", "Non Degree", "Junior high", "Senior high"
- teaching_language: "Chinese", "English", "Bilingual"
- intake_term: "March", "September", "Other"
- currency: "CNY", "USD"
- accommodation_fee_period: "month", "year", "semester"
- medical_insurance_fee_period: "year", "semester"
- category: "Degree Program", "Non-degree/Language Program"

DOCUMENT NORMALIZATION RULES:
- "Last Academic Transcript and Certificate(Notarized)" → TWO documents: "Transcript" (rules: "Notarized") AND "Highest Degree Certificate" (rules: "Notarized")
- "English Proficiency Certificate(IELTS...)" → "English Proficiency Certificate" (rules: include IELTS/TOEFL requirements)
- "Health Check Up Certificate" → "Health Check Up Form"
- "Police Clearance" → "Non Criminal Record"
- "Two recommendation Letter" → "Recommendation Letter" (rules: "Two letters from Professors/Associate Professors")
- "Study Plan /Research Proposal" → "Study Plan"
- "Publication(If Applicable)" → "Publication" (is_required: false, applies_to: "if_applicable")
- "Award/Extracurricular certificates" → "Award/Extracurricular Certificates"

FEE EXTRACTION RULES:
- If accommodation fee is a range (e.g., "4500-9000"), use LOWER BOUND (4500) in accommodation_fee, put FULL TEXT in accommodation_note
- If medical fee is mentioned (e.g., "Medical: 400CNY"), set medical_insurance_fee = 400 (NOT null)
- Combine all fee notes in fees.notes (registration fees, CSC deadlines, etc.)
- If CSC deadline exists, add to fees.notes: "CSC deadline: YYYY-MM-DD."

SCHOLARSHIP RULES:
- CRITICAL: Extract ALL scholarships mentioned in the document. If document lists multiple scholarships (e.g., "CSC type B", "Type A", "Type B", "First-class Scholarship", "Second-class Scholarship", "Chinese Government Scholarship"), extract ALL of them as separate scholarship objects in the scholarships array.
- If document explicitly states "NO Scholarship" or "No scholarship available" or does not mention any scholarships at all, use an empty array: "scholarships": []
- If document mentions scholarships but only for specific majors/intakes, extract them only for those majors/intakes. For majors/intakes without scholarships, use empty array: "scholarships": []
- Scholarship names should match exactly as written in document (e.g., "First-class Scholarship", "Second-class Scholarship", "Chinese Government Scholarship", "CSC type B")
- Only set fields explicitly stated in document
- first_year_only should be null or false unless document explicitly states scholarship duration is first year only
- Registration/medical fees "only first year" are UNIVERSITY payments, NOT scholarship duration
- tuition_waiver_percent = 100 only if doc explicitly states full tuition waiver
- If scholarship mentions accommodation fee (e.g., "Type B: 4500-9000 RMB/year"), put in eligibility_note: "Accommodation fee: 4500-9000 RMB/year (paid by student)", BUT ALSO set accommodation_fee in fees
- Examples:
  * Document with 3 scholarships: Extract all 3 as separate objects
  * Document with 1 scholarship: Extract 1 object, array has 1 item
  * Document with no scholarships mentioned: Use empty array "scholarships": []
  * Document says "NO Scholarship" for specific major: Use empty array "scholarships": [] for that major

OPTIMIZATION RULES (CRITICAL FOR MANY MAJORS):
- If document lists 5+ majors that ALL share the same:
  * degree_level, teaching_language, duration_years, discipline, category, keywords
  * AND the same intake(s) with same fees, requirements, documents, scholarships
- Then use "major_groups" format instead of "majors" format
- This dramatically reduces JSON size and generation time
- Example: 10 PhD majors with same properties → use 1 major_group with 10 names in major_names array
- If majors have different properties or intakes, use regular "majors" format

KEYWORDS RULES:
- CRITICAL: Always extract 1-3 keywords for each major, even if not explicitly mentioned in document
- Extract keywords from the major name itself by breaking it down into subject terms
- 1-5 items maximum (prefer 1-3)
- Subject-only terms (e.g., "business", "administration", "management", "computer science", "engineering", "software")
- Include common abbreviations/acronyms when obvious (e.g., "cse" for Computer Science and Engineering, "mba" for Masters in Business Administration)
- Remove: campus/university/location/intake/year/scholarship names (e.g., remove "zhuhai", "bnu", "beijing", "campus", "china", "march", "2026", "csc")
- Examples:
  * "Computer Science and Engineering" → ["computer science", "engineering", "cse"]
  * "Computer Science and Technology" → ["computer science", "technology", "cst"]
  * "Software Engineering" → ["software", "engineering", "se"]
  * "Masters in Business Administration (MBA)" → ["business", "administration", "mba", "management"]
  * "Information and Communication Engineering" → ["information", "communication", "engineering", "ice"]
  * "Electronic Science and Technology" → ["electronic", "science", "technology", "electronics"]
  * "Cyberspace Security" → ["cybersecurity", "security", "cyberspace"]
  * "Control Science and Engineering" → ["control", "engineering", "automation"]
  * "Mathematics" → ["mathematics", "math"]
  * "Physics" → ["physics"]
  * "Management Science and Engineering" → ["management", "engineering", "science"]
- DO NOT use null - always extract at least 1-2 keywords from the major name

REQUIREMENTS EXTRACTION RULES:
- bank_statement_required, bank_statement_amount, bank_statement_currency, bank_statement_note: ONLY extract if document explicitly mentions bank statement requirement. If not mentioned, set all to null.
- Do NOT infer bank statement requirements. Only extract if explicitly stated in document.

ERROR HANDLING:
- If university name cannot be extracted, add to errors: "Could not extract university name"
- If intake_year is missing for any intake, add to errors: "Missing intake_year for major: {major_name}"
- If required enum values don't match, add to errors: "Invalid {field} value: {value}"
- If critical data is missing, add descriptive error messages

OUTPUT REQUIREMENTS:
- Output ONLY the JSON object, nothing else
- No markdown code blocks
- No explanations
- No SQL
- Valid JSON that can be parsed by json.loads()"""

        user_prompt = f"""Extract data from this university program document:

{document_text}

Output the JSON object following the schema exactly. Extract only facts from the document. Use null for missing values."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        try:
            total_chars = len(system_prompt) + len(user_prompt)
            estimated_tokens = total_chars / 4
            print(f"📊 Extraction Context: ~{estimated_tokens:.0f} tokens ({total_chars:,} chars)")
            
            response = self.openai_service.chat_completion(
                messages=messages,
                temperature=0.1,  # Low temperature for deterministic extraction
                top_p=0.9,
                max_retries=3
            )
            
            if hasattr(response, 'usage') and response.usage:
                print(f"📊 Tokens used: {response.usage.total_tokens} (prompt: {response.usage.prompt_tokens}, completion: {response.usage.completion_tokens})")
            
            content = response.choices[0].message.content.strip()
            
            # Remove markdown code blocks if present
            content = re.sub(r'^```(?:json)?\s*\n', '', content, flags=re.MULTILINE)
            content = re.sub(r'\n```\s*$', '', content, flags=re.MULTILINE)
            content = content.strip()
            
            # Parse JSON
            try:
                extracted_data = json.loads(content)
                print(f"✅ Data extracted successfully: {len(extracted_data.get('majors', []))} majors")
                return extracted_data
            except json.JSONDecodeError as e:
                print(f"❌ JSON parse error: {e}")
                print(f"📄 Content preview: {content[:500]}")
                raise ValueError(f"LLM output is not valid JSON: {str(e)}")
            
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            error_type = type(e).__name__
            error_msg = str(e)
            
            print(f"❌ Exception in data extraction ({error_type}): {error_msg}")
            print(f"Traceback: {error_trace}")
            
            # Return error structure
            return {
                "university_name": "",
                "majors": [],
                "errors": [f"Data extraction failed: {error_msg}"]
            }

