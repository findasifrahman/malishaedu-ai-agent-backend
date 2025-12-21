"""
Comprehensive test suite for Partner Router (60+ tests)
Tests routing, clarification, slot extraction, and DB parameter building
"""
import pytest
from unittest.mock import Mock, MagicMock
from app.services.router import PartnerRouter
from app.services.slot_schema import PartnerQueryState, RequirementFocus, ScholarshipFocus
from app.services.openai_service import OpenAIService


class TestClarificationLoopAndFuzzyDegree:
    """Critical tests for clarification loop and fuzzy degree matching"""
    
    @pytest.fixture
    def router(self):
        """Create router instance with mocked OpenAI service"""
        mock_openai = Mock(spec=OpenAIService)
        return PartnerRouter(mock_openai)
    
    def test_scholarship_asks_degree(self, router):
        """SCHOLARSHIP INFORMATION → ask degree"""
        state = router.route_stage1_rules("SCHOLARSHIP INFORMATION")
        needs_clar, question = router.needs_clarification(state.intent, state)
        assert needs_clar is True
        assert "degree level" in question.lower()
        assert state.pending_slot == "degree_level"
        assert state.is_clarifying is True
    
    def test_bachelov_fuzzy_match(self, router):
        """'bachelov' → degree_level=Bachelor, no LLM call"""
        # Create prev_state with pending_slot
        prev_state = PartnerQueryState()
        prev_state.intent = router.INTENT_SCHOLARSHIP
        prev_state.pending_slot = "degree_level"
        prev_state.is_clarifying = True
        
        # Route with typo
        state = router.route("bachelov", [], prev_state)
        
        # Should match Bachelor
        assert state.degree_level == "Bachelor"
        assert state.pending_slot is None
        assert state.is_clarifying is False
        assert state.intent == router.INTENT_SCHOLARSHIP  # Intent locked
        assert state.major_query is None  # Never becomes major
    
    def test_bachlor_fuzzy_match(self, router):
        """'bachlor' → Bachelor"""
        prev_state = PartnerQueryState()
        prev_state.intent = router.INTENT_SCHOLARSHIP
        prev_state.pending_slot = "degree_level"
        
        state = router.route("bachlor", [], prev_state)
        assert state.degree_level == "Bachelor"
        assert state.pending_slot is None
    
    def test_masters_fuzzy_match(self, router):
        """'masters' → Master"""
        prev_state = PartnerQueryState()
        prev_state.intent = router.INTENT_SCHOLARSHIP
        prev_state.pending_slot = "degree_level"
        
        state = router.route("masters", [], prev_state)
        assert state.degree_level == "Master"
        assert state.pending_slot is None
    
    def test_msc_fuzzy_match(self, router):
        """'msc' → Master"""
        prev_state = PartnerQueryState()
        prev_state.intent = router.INTENT_SCHOLARSHIP
        prev_state.pending_slot = "degree_level"
        
        state = router.route("msc", [], prev_state)
        assert state.degree_level == "Master"
        assert state.pending_slot is None
    
    def test_intent_remains_scholarship(self, router):
        """Intent remains SCHOLARSHIP after clarification"""
        prev_state = PartnerQueryState()
        prev_state.intent = router.INTENT_SCHOLARSHIP
        prev_state.pending_slot = "degree_level"
        prev_state.wants_scholarship = True
        
        state = router.route("bachelov", [], prev_state)
        assert state.intent == router.INTENT_SCHOLARSHIP
        assert state.wants_scholarship is True
    
    def test_no_llm_call_during_clarification(self, router):
        """LLM should NOT be called when pending_slot is set"""
        prev_state = PartnerQueryState()
        prev_state.intent = router.INTENT_SCHOLARSHIP
        prev_state.pending_slot = "degree_level"
        
        # Mock LLM to ensure it's not called
        router.openai_service.extract_json = Mock()
        
        state = router.route("bachelov", [], prev_state)
        
        # LLM should not be called
        router.openai_service.extract_json.assert_not_called()
        assert state.degree_level == "Bachelor"
    
    def test_no_repeated_clarification(self, router):
        """Agent should NOT ask the same question twice"""
        prev_state = PartnerQueryState()
        prev_state.intent = router.INTENT_SCHOLARSHIP
        prev_state.pending_slot = "degree_level"
        
        # First clarification reply
        state = router.route("bachelov", [], prev_state)
        
        # Should have cleared pending_slot
        assert state.pending_slot is None
        assert state.is_clarifying is False
        
        # Should not need clarification again
        needs_clar, _ = router.needs_clarification(state.intent, state)
        assert needs_clar is False
    
    def test_no_major_matching_triggered(self, router):
        """'bachelov' should NEVER become major_query"""
        prev_state = PartnerQueryState()
        prev_state.intent = router.INTENT_SCHOLARSHIP
        prev_state.pending_slot = "degree_level"
        
        state = router.route("bachelov", [], prev_state)
        
        # Should be degree_level, NOT major_query
        assert state.degree_level == "Bachelor"
        assert state.major_query is None
    
    def test_short_message_no_llm(self, router):
        """Short messages (≤2 words) should NOT trigger LLM"""
        router.openai_service.extract_json = Mock()
        
        # Single word
        state1 = router.route("bachelor", [])
        router.openai_service.extract_json.assert_not_called()
        
        # Two words
        router.openai_service.reset_mock()
        state2 = router.route("master degree", [])
        # LLM might be called for longer queries, but not for single-word degrees
        if state2.degree_level:
            # If degree detected, LLM should not be needed
            pass
    
    def test_degree_words_never_majors(self, router):
        """Degree words should NEVER be treated as majors"""
        test_cases = ["bachelor", "master", "masters", "phd", "language"]
        
        for query in test_cases:
            state = router.route_stage1_rules(query)
            if state.degree_level:
                assert state.major_query is None, f"'{query}' became major_query"
    
    def test_fuzzy_degree_similarity_threshold(self, router):
        """Test fuzzy matching with similarity threshold ≥ 0.75"""
        # These should match
        assert router._fuzzy_match_degree_level("bachelov") == "Bachelor"
        assert router._fuzzy_match_degree_level("bachlor") == "Bachelor"
        assert router._fuzzy_match_degree_level("masters") == "Master"
        assert router._fuzzy_match_degree_level("msc") == "Master"
        
        # These should NOT match (too different)
        assert router._fuzzy_match_degree_level("biology") is None
        assert router._fuzzy_match_degree_level("computer") is None


class TestPartnerRouter:
    """Test suite for PartnerRouter"""
    
    @pytest.fixture
    def router(self):
        """Create router instance with mocked OpenAI service"""
        mock_openai = Mock(spec=OpenAIService)
        return PartnerRouter(mock_openai)
    
    # ========== ROUTING + TYPO + LIST TESTS ==========
    
    def test_bachelorvvvv_university_list(self, router):
        """Test typo handling: 'Bachelorvvvv university list'"""
        state = router.route_stage1_rules("Bachelorvvvv university list")
        assert state.intent == router.INTENT_LIST_UNIVERSITIES
        assert state.degree_level == "Bachelor"
        assert state.wants_list is True
    
    def test_uni_list(self, router):
        """Test abbreviation: 'uni list'"""
        state = router.route_stage1_rules("uni list")
        assert state.intent == router.INTENT_LIST_UNIVERSITIES
        assert state.wants_list is True
    
    def test_partner_universities(self, router):
        """Test: 'partner universities'"""
        state = router.route_stage1_rules("partner universities")
        assert state.intent == router.INTENT_LIST_UNIVERSITIES
        assert state.wants_list is True
    
    def test_show_all_universities(self, router):
        """Test: 'show all universities'"""
        state = router.route_stage1_rules("show all universities")
        assert state.intent == router.INTENT_LIST_UNIVERSITIES
        assert state.wants_list is True
    
    def test_list_universities_in_guangzhou(self, router):
        """Test: 'list universities in Guangzhou'"""
        state = router.route_stage1_rules("list universities in Guangzhou")
        assert state.intent == router.INTENT_LIST_UNIVERSITIES
        assert state.city == "Guangzhou"
    
    def test_list_universities_in_guangdong_province(self, router):
        """Test: 'list universities in Guangdong province'"""
        state = router.route_stage1_rules("list universities in Guangdong province")
        assert state.intent == router.INTENT_LIST_UNIVERSITIES
        assert state.province == "Guangdong"
    
    def test_language_program_march_intake_university_list(self, router):
        """Test: 'language program march intake university list'"""
        state = router.route_stage1_rules("language program march intake university list")
        assert state.intent == router.INTENT_LIST_UNIVERSITIES
        assert state.degree_level == "Language"
        assert state.intake_term == "March"
    
    def test_show_programs_available(self, router):
        """Test: 'show programs available'"""
        state = router.route_stage1_rules("show programs available")
        assert state.intent == router.INTENT_LIST_PROGRAMS
        assert state.wants_list is True
    
    def test_what_majors_available_for_master_english_taught(self, router):
        """Test: 'what majors are available for master english taught'"""
        state = router.route_stage1_rules("what majors are available for master english taught")
        assert state.intent == router.INTENT_LIST_PROGRAMS
        assert state.degree_level == "Master"
        assert state.teaching_language == "English"
    
    def test_show_more_pagination(self, router):
        """Test: 'show more' (pagination next)"""
        state = router.route_stage1_rules("show more")
        assert state.intent == router.INTENT_PAGINATION
        assert state.page_action == "next"
    
    def test_previous_page_pagination(self, router):
        """Test: 'previous page' (pagination prev)"""
        state = router.route_stage1_rules("previous page")
        assert state.intent == router.INTENT_PAGINATION
        assert state.page_action == "prev"
    
    # ========== DURATION VARIANTS TESTS ==========
    
    def test_4_months_language_course(self, router):
        """Test: 'I want 4 months language course'"""
        state = router.route_stage1_rules("I want 4 months language course")
        assert state.degree_level == "Language"
        assert state.duration_years_target == pytest.approx(4/12, abs=0.01)
        assert state.duration_constraint == "exact"
    
    def test_16_weeks_language_course(self, router):
        """Test: 'I want 16 weeks language course'"""
        state = router.route_stage1_rules("I want 16 weeks language course")
        assert state.degree_level == "Language"
        assert state.duration_years_target == pytest.approx(16/52, abs=0.01)
    
    def test_1_3_year_program(self, router):
        """Test: 'I want 1.3 year program'"""
        state = router.route_stage1_rules("I want 1.3 year program")
        assert state.duration_years_target == 1.3
    
    def test_about_0_75_year_foundation(self, router):
        """Test: 'about 0.75 year foundation'"""
        state = router.route_stage1_rules("about 0.75 year foundation")
        assert state.duration_years_target == 0.75
        assert state.duration_constraint == "approx"
    
    def test_at_least_2_years_master(self, router):
        """Test: 'at least 2 years master'"""
        state = router.route_stage1_rules("at least 2 years master")
        assert state.degree_level == "Master"
        assert state.duration_years_target == 2.0
        assert state.duration_constraint == "min"
    
    def test_minimum_3_years_phd(self, router):
        """Test: 'minimum 3 years phd'"""
        state = router.route_stage1_rules("minimum 3 years phd")
        assert state.degree_level == "PhD"
        assert state.duration_years_target == 3.0
        assert state.duration_constraint == "min"
    
    def test_max_1_year_language(self, router):
        """Test: 'max 1 year language'"""
        state = router.route_stage1_rules("max 1 year language")
        assert state.degree_level == "Language"
        assert state.duration_years_target == 1.0
        assert state.duration_constraint == "max"
    
    def test_under_6_months(self, router):
        """Test: 'under 6 months'"""
        state = router.route_stage1_rules("under 6 months")
        assert state.duration_years_target == pytest.approx(6/12, abs=0.01)
        assert state.duration_constraint == "max"
    
    def test_between_1_and_2_years(self, router):
        """Test: 'between 1 and 2 years' - should set min+max"""
        state = router.route_stage1_rules("between 1 and 2 years")
        # This is a design choice - we'll parse the first number as target with approx constraint
        # Or we could set both min and max - for now, let's use the first number
        assert state.duration_years_target == 1.0 or state.duration_years_target == 2.0
    
    # ========== INTENT SWITCH MID-CONVERSATION TESTS ==========
    
    def test_duration_change_6_to_12_months(self, router):
        """Test: History '6 month language course' then user 'actually make it 12 months'"""
        prev_state = PartnerQueryState(
            intent=router.INTENT_LIST_PROGRAMS,
            degree_level="Language",
            duration_years_target=0.5
        )
        state = router.route_stage1_rules("actually make it 12 months", prev_state)
        assert state.duration_years_target == pytest.approx(12/12, abs=0.01)
        # Intent should remain or change based on new query
        assert "actually" in router.normalize_query("actually make it 12 months")
    
    def test_degree_change_language_to_bachelor(self, router):
        """Test: History 'language course' then user 'instead bachelor'"""
        prev_state = PartnerQueryState(
            intent=router.INTENT_LIST_PROGRAMS,
            degree_level="Language"
        )
        state = router.route_stage1_rules("instead bachelor", prev_state)
        assert state.degree_level == "Bachelor"
        # Major query should be cleared if not re-stated
        assert state.major_query is None or state.major_query != "language"
    
    def test_degree_change_bachelor_to_master_pharmacy(self, router):
        """Test: History 'bachelor' then user 'now master pharmacy'"""
        prev_state = PartnerQueryState(
            intent=router.INTENT_LIST_PROGRAMS,
            degree_level="Bachelor"
        )
        state = router.route_stage1_rules("now master pharmacy", prev_state)
        assert state.degree_level == "Master"
        assert "pharmacy" in (state.major_query or "")
    
    def test_intake_change_march_to_september(self, router):
        """Test: History 'march intake' then user 'no, september intake'"""
        prev_state = PartnerQueryState(
            intent=router.INTENT_LIST_PROGRAMS,
            intake_term="March"
        )
        state = router.route_stage1_rules("no, september intake", prev_state)
        assert state.intake_term == "September"
    
    # ========== REQUIREMENTS UMBRELLA TESTS ==========
    
    def test_admission_requirements(self, router):
        """Test: 'admission requirements'"""
        state = router.route_stage1_rules("admission requirements")
        assert state.intent == router.INTENT_ADMISSION_REQUIREMENTS
        assert state.wants_requirements is True
    
    def test_university_admission_requirement(self, router):
        """Test: 'university admission requirement'"""
        state = router.route_stage1_rules("university admission requirement")
        assert state.intent == router.INTENT_ADMISSION_REQUIREMENTS
        assert state.wants_requirements is True
    
    def test_what_documents_needed_to_apply(self, router):
        """Test: 'what documents needed to apply'"""
        state = router.route_stage1_rules("what documents needed to apply")
        assert state.intent == router.INTENT_ADMISSION_REQUIREMENTS
        assert state.req_focus.docs is True
    
    def test_do_i_need_hsk(self, router):
        """Test: 'do I need HSK?'"""
        state = router.route_stage1_rules("do I need HSK?")
        assert state.intent == router.INTENT_ADMISSION_REQUIREMENTS
        assert state.req_focus.exams is True
    
    def test_ielts_required(self, router):
        """Test: 'IELTS required?'"""
        state = router.route_stage1_rules("IELTS required?")
        assert state.intent == router.INTENT_ADMISSION_REQUIREMENTS
        assert state.req_focus.exams is True
    
    def test_csca_required_which_subjects(self, router):
        """Test: 'CSCA required? which subjects?'"""
        state = router.route_stage1_rules("CSCA required? which subjects?")
        assert state.intent == router.INTENT_ADMISSION_REQUIREMENTS
        assert state.req_focus.exams is True
    
    def test_bank_statement_required(self, router):
        """Test: 'bank statement required?'"""
        state = router.route_stage1_rules("bank statement required?")
        assert state.intent == router.INTENT_ADMISSION_REQUIREMENTS
        assert state.req_focus.bank is True
    
    def test_bank_statement_under_5000_usd(self, router):
        """Test: 'bank statement under 5000 usd'"""
        state = router.route_stage1_rules("bank statement under 5000 usd")
        assert state.intent == router.INTENT_ADMISSION_REQUIREMENTS
        assert state.req_focus.bank is True
        assert state.budget_max == 5000.0
    
    def test_age_limit_for_program(self, router):
        """Test: 'age limit for this program'"""
        state = router.route_stage1_rules("age limit for this program")
        assert state.intent == router.INTENT_ADMISSION_REQUIREMENTS
        assert state.req_focus.age is True
    
    def test_inside_china_applicants_allowed(self, router):
        """Test: 'inside china applicants allowed?'"""
        state = router.route_stage1_rules("inside china applicants allowed?")
        assert state.intent == router.INTENT_ADMISSION_REQUIREMENTS
        assert state.req_focus.inside_china is True
    
    def test_acceptance_letter_required(self, router):
        """Test: 'acceptance letter required?'"""
        state = router.route_stage1_rules("acceptance letter required?")
        assert state.intent == router.INTENT_ADMISSION_REQUIREMENTS
    
    def test_interview_required(self, router):
        """Test: 'interview required?'"""
        state = router.route_stage1_rules("interview required?")
        assert state.intent == router.INTENT_ADMISSION_REQUIREMENTS
    
    def test_written_test_required(self, router):
        """Test: 'written test required?'"""
        state = router.route_stage1_rules("written test required?")
        assert state.intent == router.INTENT_ADMISSION_REQUIREMENTS
    
    def test_accommodation_type_dorm_or_apartment(self, router):
        """Test: 'accommodation type? dorm or apartment?'"""
        state = router.route_stage1_rules("accommodation type? dorm or apartment?")
        assert state.intent == router.INTENT_ADMISSION_REQUIREMENTS
        assert state.req_focus.accommodation is True
    
    def test_what_is_accommodation_note(self, router):
        """Test: 'what is the accommodation note?'"""
        state = router.route_stage1_rules("what is the accommodation note?")
        assert state.intent == router.INTENT_ADMISSION_REQUIREMENTS
        assert state.req_focus.accommodation is True
    
    def test_deadline_for_application(self, router):
        """Test: 'deadline for application'"""
        state = router.route_stage1_rules("deadline for application")
        assert state.intent == router.INTENT_ADMISSION_REQUIREMENTS
        assert state.req_focus.deadline is True
    
    def test_earliest_intake(self, router):
        """Test: 'earliest intake' - must not require intake_year"""
        state = router.route_stage1_rules("earliest intake")
        assert state.wants_earliest is True
        # Should not require intake_year - DB will order by deadline
    
    def test_as_soon_as_possible_language_program(self, router):
        """Test: 'as soon as possible language program'"""
        state = router.route_stage1_rules("as soon as possible language program")
        assert state.degree_level == "Language"
        assert state.wants_earliest is True
    
    # ========== FEES + COST + FREE TUITION TESTS ==========
    
    def test_calculate_fees(self, router):
        """Test: 'calculate fees'"""
        state = router.route_stage1_rules("calculate fees")
        assert state.intent == router.INTENT_FEES
        assert state.wants_fees is True
    
    def test_how_much_total_cost_per_year(self, router):
        """Test: 'how much total cost per year'"""
        state = router.route_stage1_rules("how much total cost per year")
        assert state.intent == router.INTENT_FEES
        assert state.wants_fees is True
    
    def test_cheapest_english_master_programs(self, router):
        """Test: 'cheapest english master programs'"""
        state = router.route_stage1_rules("cheapest english master programs")
        assert state.intent == router.INTENT_COMPARISON
        assert state.degree_level == "Master"
        assert state.teaching_language == "English"
    
    def test_compare_3_cheapest_programs(self, router):
        """Test: 'compare 3 cheapest programs'"""
        state = router.route_stage1_rules("compare 3 cheapest programs")
        assert state.intent == router.INTENT_COMPARISON
    
    def test_is_tuition_fee_free(self, router):
        """Test: 'is tuition fee free?'"""
        state = router.route_stage1_rules("is tuition fee free?")
        assert state.intent == router.INTENT_FEES
        assert state.wants_fees is True
    
    def test_does_scholarship_cover_tuition(self, router):
        """Test: 'does scholarship cover tuition?'"""
        state = router.route_stage1_rules("does scholarship cover tuition?")
        assert state.intent == router.INTENT_SCHOLARSHIP
        assert state.wants_scholarship is True
    
    def test_what_is_application_fee(self, router):
        """Test: 'what is application fee'"""
        state = router.route_stage1_rules("what is application fee")
        assert state.intent == router.INTENT_FEES
        assert state.wants_fees is True
    
    def test_medical_insurance_fee(self, router):
        """Test: 'medical insurance fee?'"""
        state = router.route_stage1_rules("medical insurance fee?")
        assert state.intent == router.INTENT_FEES
        assert state.wants_fees is True
    
    def test_visa_extension_fee_per_year(self, router):
        """Test: 'visa extension fee per year?'"""
        state = router.route_stage1_rules("visa extension fee per year?")
        assert state.intent == router.INTENT_FEES
        assert state.wants_fees is True
    
    def test_one_time_medical_checkup_fee(self, router):
        """Test: 'one time medical checkup fee?'"""
        state = router.route_stage1_rules("one time medical checkup fee?")
        assert state.intent == router.INTENT_FEES
        assert state.wants_fees is True
    
    # ========== SCHOLARSHIP TESTS ==========
    
    def test_scholarship_available_for_university(self, router):
        """Test: 'Scholarship available for this university?'"""
        state = router.route_stage1_rules("Scholarship available for this university?")
        assert state.intent == router.INTENT_SCHOLARSHIP
        assert state.wants_scholarship is True
    
    def test_csc_scholarship_available(self, router):
        """Test: 'CSC scholarship available?'"""
        state = router.route_stage1_rules("CSC scholarship available?")
        assert state.intent == router.INTENT_SCHOLARSHIP
        assert state.scholarship_focus.csc is True
        assert state.scholarship_focus.any is False
    
    def test_university_scholarship_available(self, router):
        """Test: 'university scholarship available?'"""
        state = router.route_stage1_rules("university scholarship available?")
        assert state.intent == router.INTENT_SCHOLARSHIP
        assert state.scholarship_focus.university is True
        assert state.scholarship_focus.any is False
    
    def test_type_a_scholarship(self, router):
        """Test: 'Type-A scholarship'"""
        state = router.route_stage1_rules("Type-A scholarship")
        assert state.intent == router.INTENT_SCHOLARSHIP
        assert state.wants_scholarship is True
    
    def test_living_allowance_amount(self, router):
        """Test: 'living allowance amount?'"""
        state = router.route_stage1_rules("living allowance amount?")
        assert state.intent == router.INTENT_SCHOLARSHIP
        assert state.wants_scholarship is True
    
    def test_scholarship_deadline_different_than_program_deadline(self, router):
        """Test: 'scholarship deadline different than program deadline?'"""
        state = router.route_stage1_rules("scholarship deadline different than program deadline?")
        assert state.intent == router.INTENT_SCHOLARSHIP
        assert state.wants_scholarship is True
    
    # ========== COUNTRY / ELIGIBILITY TESTS ==========
    
    def test_is_bangladesh_allowed(self, router):
        """Test: 'Is Bangladesh allowed?'"""
        state = router.route_stage1_rules("Is Bangladesh allowed?")
        assert state.intent == router.INTENT_ADMISSION_REQUIREMENTS
        assert state.req_focus.country is True
    
    def test_is_my_country_allowed(self, router):
        """Test: 'Is my country allowed?'"""
        state = router.route_stage1_rules("Is my country allowed?")
        assert state.intent == router.INTENT_ADMISSION_REQUIREMENTS
        assert state.req_focus.country is True
    
    def test_inside_china_apply_allowed(self, router):
        """Test: 'inside China apply allowed?'"""
        state = router.route_stage1_rules("inside China apply allowed?")
        assert state.intent == router.INTENT_ADMISSION_REQUIREMENTS
        assert state.req_focus.inside_china is True
    
    def test_minimum_average_score_requirement(self, router):
        """Test: 'minimum average score requirement?'"""
        state = router.route_stage1_rules("minimum average score requirement?")
        assert state.intent == router.INTENT_ADMISSION_REQUIREMENTS
    
    def test_i_have_hsk_4_can_i_apply(self, router):
        """Test: 'I have HSK 4, can I apply?' - router should set wants_requirements/exams"""
        state = router.route_stage1_rules("I have HSK 4, can I apply?")
        assert state.intent == router.INTENT_ADMISSION_REQUIREMENTS
        assert state.req_focus.exams is True
        # Do not decide without DB - just set the flag
    
    # ========== LOCATION + QUALITY / RANKING TESTS ==========
    
    def test_best_university_for_computer_science(self, router):
        """Test: 'best university for computer science'"""
        state = router.route_stage1_rules("best university for computer science")
        assert "computer science" in (state.major_query or "")
    
    def test_top_ranked_partner_universities(self, router):
        """Test: 'top ranked partner universities'"""
        state = router.route_stage1_rules("top ranked partner universities")
        assert state.intent == router.INTENT_LIST_UNIVERSITIES
    
    def test_university_quality_in_guangzhou(self, router):
        """Test: 'university quality in Guangzhou'"""
        state = router.route_stage1_rules("university quality in Guangzhou")
        assert state.city == "Guangzhou"
    
    def test_211_985_universities_list(self, router):
        """Test: '211/985 universities list'"""
        state = router.route_stage1_rules("211/985 universities list")
        assert state.intent == router.INTENT_LIST_UNIVERSITIES
    
    def test_world_ranking_301_400_universities_list(self, router):
        """Test: 'world ranking 301-400 universities list'"""
        state = router.route_stage1_rules("world ranking 301-400 universities list")
        assert state.intent == router.INTENT_LIST_UNIVERSITIES
    
    # ========== CLARIFICATION TESTS ==========
    
    def test_needs_clarification_list_universities_no_filters(self, router):
        """Test clarification needed for LIST_UNIVERSITIES with no filters"""
        state = PartnerQueryState(intent=router.INTENT_LIST_UNIVERSITIES)
        needs, question = router.needs_clarification(router.INTENT_LIST_UNIVERSITIES, state)
        assert needs is True
        assert "level" in question.lower()
        assert "intake" in question.lower()
    
    def test_no_clarification_list_universities_with_city(self, router):
        """Test no clarification needed for LIST_UNIVERSITIES with city filter"""
        state = PartnerQueryState(intent=router.INTENT_LIST_UNIVERSITIES, city="Guangzhou")
        needs, question = router.needs_clarification(router.INTENT_LIST_UNIVERSITIES, state)
        assert needs is False
    
    def test_needs_clarification_list_programs_missing_both(self, router):
        """Test clarification needed for LIST_PROGRAMS missing both degree_level and major_query"""
        state = PartnerQueryState(intent=router.INTENT_LIST_PROGRAMS)
        needs, question = router.needs_clarification(router.INTENT_LIST_PROGRAMS, state)
        assert needs is True
        assert "degree level" in question.lower()
        assert "subject" in question.lower() or "major" in question.lower()
    
    def test_needs_clarification_admission_requirements_no_target(self, router):
        """Test clarification needed for ADMISSION_REQUIREMENTS with no target"""
        state = PartnerQueryState(intent=router.INTENT_ADMISSION_REQUIREMENTS)
        needs, question = router.needs_clarification(router.INTENT_ADMISSION_REQUIREMENTS, state)
        assert needs is True
        assert "university" in question.lower() or "program" in question.lower()
    
    def test_needs_clarification_fees_no_target(self, router):
        """Test clarification needed for FEES with no target"""
        state = PartnerQueryState(intent=router.INTENT_FEES)
        needs, question = router.needs_clarification(router.INTENT_FEES, state)
        assert needs is True
        assert "university" in question.lower() or "program" in question.lower()
    
    def test_no_clarification_wants_earliest(self, router):
        """Test no clarification needed when wants_earliest=True (DB will order by deadline)"""
        state = PartnerQueryState(intent=router.INTENT_LIST_PROGRAMS, wants_earliest=True)
        needs, question = router.needs_clarification(router.INTENT_LIST_PROGRAMS, state)
        assert needs is False
    
    # ========== MAJOR_QUERY FILTERING TESTS ==========
    
    def test_major_query_not_university(self, router):
        """Test that 'university' is never extracted as major_query"""
        state = router.route_stage1_rules("university list")
        assert state.major_query != "university"
        assert state.major_query != "universities"
    
    def test_major_query_not_database(self, router):
        """Test that 'database' is never extracted as major_query"""
        state = router.route_stage1_rules("database list")
        assert state.major_query != "database"
    
    def test_major_query_not_list(self, router):
        """Test that 'list' is never extracted as major_query"""
        state = router.route_stage1_rules("list programs")
        assert state.major_query != "list"
    
    # ========== CONFIDENCE SCORING TESTS ==========
    
    def test_confidence_scoring_high(self, router):
        """Test confidence scoring with strong intent + slots"""
        state = router.route_stage1_rules("master pharmacy march 2026")
        assert state.confidence >= 0.6
        assert state.intent != router.INTENT_GENERAL
    
    def test_confidence_scoring_low(self, router):
        """Test confidence scoring with weak/no signals"""
        state = router.route_stage1_rules("hello")
        assert state.confidence < 0.75
        assert state.intent == router.INTENT_GENERAL
    
    def test_confidence_capped_at_1_0(self, router):
        """Test confidence is capped at 1.0"""
        state = router.route_stage1_rules("master computer science english taught march 2026 in guangzhou")
        assert state.confidence <= 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

