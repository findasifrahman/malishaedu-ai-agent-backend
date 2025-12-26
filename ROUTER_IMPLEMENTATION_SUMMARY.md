# Deterministic Answer Router Implementation Summary

## Overview
Implemented a comprehensive routing system for SalesAgent that deterministically routes questions to appropriate knowledge sources (DB, FAQ, RAG, Tavily) based on query classification.

## Files Created/Modified

### 1. New Knowledge Base Files
**File:** `backend/app/rag_docs/malishaedu_answer_bank.md`
- Contains approved Q&A answers for all FAQ categories
- Covers: studying in China requirements, cost, accommodation, safety, application process, MalishaEdu services, partnerships, post-study career
- Note: This file needs to be ingested into the RAG database via the `/api/rag/upload` endpoint to be searchable

**File:** `backend/app/rag_docs/csca_2026_undergrad_scholarship_faq.md`
- Contains comprehensive CSCA (China Scholastic Competency Assessment) and Chinese Government Scholarship (CSC) information for 2026/2027 intake
- Covers: CSCA exam structure, registration, fees, timing, undergraduate scholarship requirements, application process
- **CRITICAL:** This file MUST be ingested into the RAG database for CSCA/CSC questions to work properly
- Note: All files in `app/rag_docs/` folder should be indexed via `/api/rag/upload` endpoint

### 2. Modified: `backend/app/services/sales_agent.py`

#### New Functions Added:

**`classify_query(user_message, student_state) -> Dict[str, Any]`**
- Deterministic query classification function
- Returns routing decisions: `intent`, `needs_db`, `needs_csca_rag`, `needs_general_rag`, `needs_web`
- Priority order:
  1. CSCA questions → CSCA RAG mode
  2. Program-specific queries → DB mode
  3. General FAQ → FAQService/RAG mode
  4. Only if needed → Tavily (domain-restricted)

**`_build_single_lead_question(student_state) -> Optional[str]`**
- Builds a single lead collection question with priority:
  1. Nationality (if missing)
  2. Contact info (if missing)
  3. Degree level or major (if missing)
- Returns None if all important fields collected

#### Enhanced Functions:

**FAQService - Expanded Keywords**
- Added comprehensive keyword list including: living cost, safety, accommodation, halal, food, payment, bank account, visa, travel, insurance, post-arrival, process time, service charge, hidden charges, contact, get started, etc.

**`generate_response()` - New Routing Logic**
- Step 0.8: Deterministic query classification
- CSCA questions: RAG-only (strict rule, no fabrication)
- General FAQ: FAQService first, then RAG from answer bank, Tavily only if latest info needed
- Program-specific: DB-first (unchanged behavior)
- All responses end with max 1 lead question

**Tavily Restrictions**
- Only used when `needs_web=True` (explicit latest/current policy requests)
- Domain-restricted to `site:malishaedu.com`
- Never used for program-specific queries
- Never pulls competitor/non-MalishaEdu content

## Routing Rules

### Program-Specific Queries (DB-First)
Triggers when query mentions:
- Tuition/fee/cost/deadline for specific university/major/intake
- "list universities", "top ranked university", "best university" (with program context)
- Comparison queries (compare universities, cheapest, lowest cost)
- Documents required for specific program
- Scholarship for specific program

**Response:** DB query → Show results → Add 1 lead question

### General FAQ Queries (FAQ/RAG-First)
Triggers when query mentions:
- Safety, accommodation, living cost, halal food, part-time work
- Service charge, hidden charges, payment, process time
- Visa, travel, insurance, bank account
- About MalishaEdu, contact, get started, consultation
- Post-arrival support, after arrival services

**Response:** FAQService match → If no match, RAG from answer bank → If latest info needed, Tavily (domain-restricted) → Add 1 lead question

### CSCA/CSC/Chinese Government Scholarship Questions (RAG-Only, Strict)
Triggers: Contains "CSCA", "CSC", "China Scholastic Competency Assessment", "Chinese Government Scholarship", "Chinese Goverment Scholarship", or "China Scholarship Council"

**Response:** RAG search only → Answer from RAG context → Safe template if details missing → Add 1 lead question
- **STRICT RULE:** Answer ONLY from retrieved RAG chunks
- NO database queries
- NO fabrication if RAG doesn't contain answer
- NO success rates, testimonials, or statistics unless explicitly in chunks
- If chunks don't contain the requested detail (e.g., success rate, testimonials, specific numbers), use safe template asking for nationality/major/intake
- If RAG search returns no results, use safe template immediately
- Safe template: "I'd be happy to help you with that! To provide you with the most accurate and personalized information about CSCA and Chinese Government Scholarship, could you please share: Your nationality, Your preferred major/field of study, Your target intake (March or September) and year"

### "Top Ranked Universities" Handling
- MUST only suggest from DB universities table
- If DB has ranking data, use it
- If DB missing ranking data, present "top options from partner list" based on general prestige signals (only if those universities exist in DB)
- If no DB matches, ask user for major/city/budget and show shortlist from DB
- NEVER suggest non-partner universities

## Lead Collection (Max 1 Question)

Priority order:
1. **Nationality**: "Which country are you from?"
2. **Contact**: "What's your WhatsApp/WeChat (or email) so our counselor can guide you faster?"
3. **Degree/Major**: "Which level & major are you planning (Bachelor/Master/PhD/Language)?"

Only asks ONE question per response, following the priority above.

## Testing Checklist

Using FAQ_Questions.docx as a test checklist:

### General FAQ Questions (should route to FAQ/RAG, NOT Tavily):
- ✅ "Is China safe for international students?"
- ✅ "What is the monthly living cost in China?"
- ✅ "What is your service charge?"
- ✅ "Are there any hidden charges?"
- ✅ "What documents are required?"
- ✅ "Can I work part-time while studying?"
- ✅ "Is halal food available?"
- ✅ "How long does the application process take?"
- ✅ "What services does MalishaEdu provide?"
- ✅ "Which universities are you partnered with?" (should use FAQ: "We work with 250+ Chinese universities")

### Program-Specific Questions (should route to DB):
- ✅ "Tuition fee for X university March intake"
- ✅ "List universities for Master's in Computer Science"
- ✅ "Top ranked universities for Bachelor's in Business"
- ✅ "Compare costs for PhD programs"

### CSCA/CSC/Chinese Government Scholarship Questions (should route to RAG only, strict):
- ✅ "What is CSCA?"
- ✅ "What is Chinese Government Scholarship?"
- ✅ "Is CSCA mandatory for Master's programs?"
- ✅ "CSCA registration process"
- ✅ "CSC scholarship requirements"
- ✅ "Success rate of CSCA scholarship" (should use safe template if not in chunks)
- ✅ "Testimonials from CSCA students" (should use safe template if not in chunks)

## Next Steps

1. **Ingest RAG documents into RAG database**
   - Use `/api/rag/upload` endpoint (admin access required)
   - Files to ingest:
     - `backend/app/rag_docs/malishaedu_answer_bank.md` (general FAQ answers)
     - `backend/app/rag_docs/csca_2026_undergrad_scholarship_faq.md` (CSCA/CSC scholarship info) - **REQUIRED for CSCA questions**
   - Once ingested, RAGService.search_similar() will automatically search them
   - **Note:** All markdown files in `app/rag_docs/` folder should be indexed to ensure comprehensive coverage

2. **Test with FAQ_Questions.docx**
   - Extract each question
   - Verify routing (classify_query returns correct intent)
   - Verify answers come from correct source (FAQ/RAG/DB)
   - Verify Tavily is NOT called for general FAQ questions
   - Verify "top ranked" only shows DB universities

3. **Monitor and refine**
   - Adjust classification thresholds if needed
   - Expand FAQ keywords if common questions miss FAQ matching
   - Review lead collection question priority based on conversion data

## Important Notes

- FAQ_Questions.docx is for testing/coverage only - NOT loaded as RAG doc
- All existing behavior rules preserved (partner university restrictions, DB-first for programs, etc.)
- Tavily is significantly restricted - only for explicit latest policy requests
- Lead collection is automatic (no popup) - questions are soft CTAs in responses
- Responses are concise and engaging - max 1 lead question per response

