# Partner Agent Improvements Summary

## Overview
This document summarizes the improvements made to the Partner Agent (`partner_agent.py`) to enhance accuracy, consistency, and partner usefulness.

## Key Changes

### A) System Prompt Updates

#### 1. Strict Response Format (7 Sections)
The agent now follows a mandatory 7-section response format:
1. **Best Match**: University + Program + Intake + Teaching Language
2. **Deadlines**: Application deadline (with days remaining from CURRENT DATE), Scholarship deadline (if different)
3. **Eligibility**: Age, Minimum Score, Language Tests, Exams, Interview, Written Test
4. **Cost Summary**: Tuition, Accommodation (with period + annual estimate if monthly), Insurance (with period + annual estimate if monthly), One-time fees, Visa extension, Year 1 Total Estimate
5. **Scholarships**: For each scholarship - Name, Coverage, Living Allowance, First Year Only, Renewal Required, Deadline
6. **Required Documents**: List ALL documents (not truncated), with rules
7. **Next Step Question**: Exactly 1 question to move the lead forward

#### 2. Database-First Policy
- Answer from DB tables whenever possible: `majors`, `program_intakes`, `program_documents`, `program_exam_requirements`, `program_intake_scholarships`
- Only use web search (Tavily) for:
  - Visa rules (X1/X2, work rules, part-time work)
  - Rankings (if not in DB)
  - City climate/safety/campus lifestyle (if not in DB notes)
  - Halal food, Muslim-friendly dorms (if not in DB)
  - Latest CSCA policy updates
  - When NO program match exists in DB
- When web is used: Cite sources, summarize briefly, note policies may vary by country

#### 3. Current Date Awareness
- Use CURRENT DATE from DATABASE CONTEXT as the single source of truth
- Do NOT use system time directly
- Calculate days remaining from CURRENT DATE (explicitly provided in DB context)

#### 4. Fee Period Handling
- Never assume accommodation is "per year"
- Use `accommodation_fee_period` and `medical_insurance_fee_period` exactly
- If monthly: Show both monthly amount AND estimated annual (×12, marked "estimate")
- If period missing: Say "period not specified in database"

#### 5. No Hallucination Policy
- If field missing in DB: Say "not provided in our database" - do NOT infer
- Do NOT make up values for fees, deadlines, or requirements
- Do NOT guess which university/major user meant

#### 6. Fuzzy Matching Improvements
- If multiple close matches: Return top 2-3 options and ask user to pick
- For ambiguous queries: Ask only 1-2 key disambiguators (degree level + intake/year)
- Do NOT assume which option user meant

#### 7. Document Listing
- List ALL documents (do not truncate to top 5)
- Include rules and notes for each document
- Specify if documents apply to inside_china_only applicants

### B) Code Changes

#### 1. Fuzzy Matching Functions Updated
- `_fuzzy_match_university()`: Now returns `(matched: bool, best_match: Optional[Dict], all_matches: List[Tuple[Dict, score]])`
  - High confidence (≥80%): Returns best match + top 3 for context
  - Medium confidence (60-80%): Returns top 2-3 options for user to pick
  - Low confidence (<60%): Returns empty list

- `_fuzzy_match_major()`: Same signature as university matching
  - Handles exact matches, substring matches, keyword matches
  - Returns top 2-3 options when uncertain

#### 2. Document Listing
- Changed from `documents[:5]` to `documents` (show all)
- Added `applies_to` field display

#### 3. Cost Calculation
- Added annual estimate calculation for monthly fees:
  ```python
  if period and 'month' in str(period).lower():
      annual_estimate = float(fee) * 12
      intake_info += f" (estimated annual: {annual_estimate:.2f} {currency})"
  ```

#### 4. Database Context Building
- Added age requirements, minimum score, interview/written test to intake info
- Enhanced scholarship display with all fields (covers_accommodation, covers_insurance, first_year_only, renewal_required, eligibility_note)
- Added arrival medical checkup fee with one-time indicator

#### 5. Web Search (Tavily) Logic
- Updated to be DB-first:
  - Only triggers for specific keywords: visa, x1, x2, work, part-time, halal, muslim, dorm friendly, campus lifestyle, climate, safety, csca policy, ranking
  - Checks if question is about DB content first (universities, majors, fees, deadlines, etc.)
  - Only uses web if NOT a DB question AND matches web-only topics
  - Includes sources in response

### C) Test Harness

Created `backend/tests/test_partner_agent.py` with:
- Unit tests for prompt structure validation
- Tests for no-hallucination instructions
- Tests for fuzzy matching behavior
- Test questions covering all high-frequency patterns:
  - Program search
  - Deadlines (scholarship vs self-funded)
  - Eligibility (age, scores, exams)
  - Costs (Year 1 estimate, monthly vs annual)
  - Scholarships (coverage, first-year-only, renewal)
  - Documents (all required + rules)
  - Process questions
  - Campus life (halal food - web search)
  - Visa questions (web search)

## High-Frequency Question Patterns (Acceptance Tests)

1. **Program Search**: "Do you have [Major] in English for Sep 2026?"
   - Expected: Best Match, Deadlines, Eligibility, Cost, Scholarships, Documents, Next Step
   - Should use DB: Yes
   - Should use Web: No

2. **Deadlines**: "Scholarship deadline vs self-funded deadline?"
   - Expected: Deadlines section with both explicitly shown
   - Should use DB: Yes

3. **Eligibility**: "Age 28 eligible?" "IELTS 5.5 ok?"
   - Expected: Eligibility section
   - Should use DB: Yes

4. **Exams**: "Is CSCA required? subjects?"
   - Expected: Eligibility/Exams section
   - Should use DB: Yes

5. **Costs**: "Year 1 total estimate" "Dorm per month or per year?"
   - Expected: Cost Summary with Year 1 Total Estimate, Accommodation with period
   - Should use DB: Yes

6. **Scholarships**: "First/Second/Third class coverage?"
   - Expected: Scholarships section with coverage details
   - Should use DB: Yes

7. **Documents**: "List all required documents + rules"
   - Expected: Required Documents section (ALL documents, not truncated)
   - Should use DB: Yes

8. **Campus Life**: "Halal food?"
   - Expected: May use web search if not in DB
   - Should use DB: First (check DB), then Web if not found

9. **Visa**: "X1/X2? Can work part-time?"
   - Expected: Uses web search (policies vary by country)
   - Should use DB: No
   - Should use Web: Yes

## Files Modified

1. `backend/app/services/partner_agent.py`
   - Updated `PARTNER_SYSTEM_PROMPT` with strict response format
   - Updated `_fuzzy_match_university()` signature and logic
   - Updated `_fuzzy_match_major()` signature and logic
   - Updated `_build_database_context()` to show all documents and enhanced cost display
   - Updated `generate_response()` to implement DB-first web search policy

2. `backend/tests/test_partner_agent.py` (NEW)
   - Test harness with unit tests and acceptance test questions

## Next Steps

1. Run the test harness to validate improvements
2. Test with real partner questions
3. Monitor for any hallucination issues
4. Collect feedback from partners on response quality
5. Iterate based on feedback

