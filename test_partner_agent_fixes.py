"""
Test examples for PartnerAgent fixes.

These are simple test cases to verify the fixes work correctly.
Run these manually or integrate into your test suite.
"""

def test_cumt_march_language_docs_disambiguation():
    """
    Test: CUMT March language program docs question -> disambiguation listing both programs
    
    Expected behavior:
    - Should NOT fuzzy-match "language program" to a single major
    - Should query ALL Language programs for CUMT with March intake
    - If multiple found (e.g., Elementary + Medium), return disambiguation response
    - Response should list both programs with teaching language and deadlines
    - If <=2 items and user asked "documents required", show BOTH document lists
    """
    test_query = "CUMT March 2026 language program documents required"
    
    expected_behavior = """
    Expected response should:
    1. List all March Language programs at CUMT (e.g., Elementary Chinese Language, Medium Chinese Language)
    2. Show teaching language for each (English/Chinese)
    3. Show deadlines for each
    4. If <=2 programs, show document requirements for BOTH
    5. If >2 programs, ask which one they want documents for
    """
    
    print("Test: CUMT March language program docs disambiguation")
    print(expected_behavior)
    return test_query, expected_behavior


def test_lowest_fees_march_language():
    """
    Test: lowest fees March language program -> returns cheapest from all intakes (not limited to "Chinese Language")
    
    Expected behavior:
    - Should NOT pre-filter by major name "Chinese Language"
    - Should query ALL March Language intakes across partner universities
    - Should compute cheapest by tuition_per_year (fallback to semester*2)
    - Should return the cheapest option(s)
    - If user asks "one year" vs "one semester", filter AFTER retrieving intakes
    """
    test_query = "which language program has the lowest fees for next March intake"
    
    expected_behavior = """
    Expected response should:
    1. Query ALL March Language intakes (not just "Chinese Language" major)
    2. Rank by effective tuition (per year preferred, semester*2 if needed)
    3. Return cheapest program(s) with fees
    4. If user asks "one year" vs "one semester", filter by major name AFTER retrieval
    """
    
    print("Test: Lowest fees March language program")
    print(expected_behavior)
    return test_query, expected_behavior


def test_scholarship_no_upcoming_deadline_available_false():
    """
    Test: scholarship question when no upcoming deadline but scholarship_available=False -> confident "not available" answer
    
    Expected behavior:
    - Should use latest intakes fallback (any deadline)
    - Should check scholarship_available flag
    - If scholarship_available=False, say confidently: "Scholarship is not available for this intake (per database)"
    - Should NOT say "notify you" or imply background monitoring
    - Should still show scholarship_info and notes if present
    """
    test_query = "LNPU English bachelor Artificial Intelligence scholarship details even if deadline closed"
    
    expected_behavior = """
    Expected response should:
    1. Use latest intakes fallback (not require upcoming deadline)
    2. Check scholarship_available flag from database
    3. If False: "Scholarship is not available for this intake (per database)"
    4. If True/None and scholarships exist: show scholarship details
    5. Always read and mention scholarship_info and notes if present
    6. Never say "notify you" or "I will notify you"
    """
    
    print("Test: Scholarship with no upcoming deadline, scholarship_available=False")
    print(expected_behavior)
    return test_query, expected_behavior


def test_documents_always_show_structured_fields():
    """
    Test: documents_only intent should always show structured requirement fields
    
    Expected behavior:
    - Teaching language (effective: ProgramIntake override or Major default)
    - Bank statement: if True show amount+currency+note; if False say "not required"; if NULL say "not specified"
    - HSK: if True show level+min_score; if False say "not required"; if NULL say "not specified"
    - English test: similar handling
    - Inside China applicants: show allowed status + extra requirements
    - Important notes from ProgramIntake.notes
    """
    test_query = "CUMT March 2026 language program documents required"
    
    expected_behavior = """
    Expected response should ALWAYS include:
    1. Teaching Language: [English/Chinese] (explicit)
    2. Bank Statement Required: [amount currency] or "No (not required)" or "Not specified"
    3. HSK Required: [Level X, Min Score Y] or "No (not required)" or "Not specified"
    4. English Test Required: [details] or "No (not required)" or "Not specified"
    5. Inside China Applicants: [Allowed/Not Allowed/Not specified] + extra requirements if any
    6. Important Notes: [from ProgramIntake.notes if present]
    """
    
    print("Test: Documents always show structured fields")
    print(expected_behavior)
    return test_query, expected_behavior


def test_scholarship_forms_detection():
    """
    Test: scholarship_only should detect and list scholarship-specific forms from scholarship_info and notes
    
    Expected behavior:
    - Read ProgramIntake.scholarship_info
    - Read ProgramIntake.notes
    - Detect forms mentioned (e.g., "Outstanding Talents Scholarship Application Form")
    - List them under "Scholarship-Specific Documents Required"
    """
    test_query = "Beihang CSE bachelor September intake what scholarships exist and how hard is 100%"
    
    expected_behavior = """
    Expected response should:
    1. Show teaching language explicitly
    2. Show scholarship details if available
    3. Show competitiveness note based on university ranking
    4. List scholarship-specific documents if mentioned in scholarship_info or notes
    5. Show scholarship deadlines
    """
    
    print("Test: Scholarship forms detection")
    print(expected_behavior)
    return test_query, expected_behavior


if __name__ == "__main__":
    print("=" * 80)
    print("PartnerAgent Fix Test Examples")
    print("=" * 80)
    print()
    
    test_cumt_march_language_docs_disambiguation()
    print()
    
    test_lowest_fees_march_language()
    print()
    
    test_scholarship_no_upcoming_deadline_available_false()
    print()
    
    test_documents_always_show_structured_fields()
    print()
    
    test_scholarship_forms_detection()
    print()
    
    print("=" * 80)
    print("All test examples defined. Run these queries against the PartnerAgent to verify fixes.")
    print("=" * 80)

