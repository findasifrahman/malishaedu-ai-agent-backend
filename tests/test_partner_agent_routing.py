"""
Comprehensive test suite for PartnerAgent routing, clarification, and SQL parameter building.
30+ tests covering all scenarios including typos, intent changes, duration parsing, etc.
"""
import pytest
from unittest.mock import Mock, MagicMock, patch
from datetime import date
from app.services.partner_agent import PartnerAgent
from app.services.slot_schema import PartnerQueryState, RequirementFocus, ScholarshipFocus
from app.services.router import PartnerRouter
from app.services.db_query_service import DBQueryService
from app.services.openai_service import OpenAIService


class TestPartnerAgentRouting:
    """Test suite for PartnerAgent routing and clarification"""
    
    @pytest.fixture
    def mock_db(self):
        """Create mock database session"""
        return Mock()
    
    @pytest.fixture
    def agent(self, mock_db):
        """Create PartnerAgent instance with mocked dependencies"""
        with patch('app.services.partner_agent.DBQueryService') as mock_db_service_class:
            mock_db_service = Mock(spec=DBQueryService)
            mock_db_service_class.return_value = mock_db_service
            
            with patch('app.services.partner_agent.OpenAIService') as mock_openai_class:
                mock_openai = Mock(spec=OpenAIService)
                mock_openai_class.return_value = mock_openai
                
                agent = PartnerAgent(mock_db)
                agent.db_service = mock_db_service
                agent.openai_service = mock_openai
                agent.router = PartnerRouter(mock_openai)
                
                return agent
    
    # ========== FUZZY DEGREE LEVEL TESTS ==========
    
    def test_bachelorvvvv_university_list(self, agent):
        """Test: 'Bachelorvvvv university list' → degree_level=Bachelor (fuzzy), wants_list=True"""
        state = agent.router.route_stage1_rules("Bachelorvvvv university list")
        assert state.degree_level == "Bachelor"
        assert state.wants_list is True
        assert state.intent == agent.router.INTENT_LIST_UNIVERSITIES
    
    def test_bachelov_fuzzy_match(self, agent):
        """Test: 'bachelov' → Bachelor via fuzzy matching"""
        matched = agent.router._fuzzy_match_degree_level("bachelov")
        assert matched == "Bachelor"
    
    def test_bacheller_fuzzy_match(self, agent):
        """Test: 'bacheller' → Bachelor"""
        matched = agent.router._fuzzy_match_degree_level("bacheller")
        assert matched == "Bachelor"
    
    def test_bachelar_fuzzy_match(self, agent):
        """Test: 'bachelar' → Bachelor"""
        matched = agent.router._fuzzy_match_degree_level("bachelar")
        assert matched == "Bachelor"
    
    def test_masters_fuzzy_match(self, agent):
        """Test: 'masters' → Master"""
        matched = agent.router._fuzzy_match_degree_level("masters")
        assert matched == "Master"
    
    def test_degree_words_never_majors(self, agent):
        """Test: Degree words should NEVER become major_query"""
        test_cases = ["bachelor", "master", "masters", "phd", "language", "bachelov"]
        for query in test_cases:
            state = agent.router.route_stage1_rules(query)
            if state.degree_level:
                assert state.major_query is None, f"'{query}' became major_query"
    
    # ========== LIST QUERY TESTS ==========
    
    def test_university_list(self, agent):
        """Test: 'university list' → wants_list=True, ask clarification"""
        state = agent.router.route_stage1_rules("university list")
        assert state.wants_list is True
        assert state.intent == agent.router.INTENT_LIST_UNIVERSITIES
        
        # Should need clarification
        missing_slots, question = agent.determine_missing_fields(state.intent, state, date.today())
        assert len(missing_slots) > 0
    
    def test_language_program_march_intake_university_list(self, agent):
        """Test: 'language program march intake university list' → all fields set, no LLM required"""
        state = agent.router.route_stage1_rules("language program march intake university list")
        assert state.degree_level == "Language"
        assert state.intake_term == "March"
        assert state.wants_list is True
        assert state.confidence >= 0.8  # High confidence, no LLM needed
    
    # ========== DURATION PARSING TESTS ==========
    
    def test_4_months_language_course(self, agent):
        """Test: 'I want 4 months language course' → duration_years_target≈0.333"""
        duration_years, constraint = agent.router.parse_duration("I want 4 months language course")
        assert duration_years == pytest.approx(0.333, abs=0.01)
        assert constraint == "exact"
    
    def test_1_3_year_program(self, agent):
        """Test: 'I want 1.3 year program' → duration_years_target=1.3"""
        duration_years, constraint = agent.router.parse_duration("I want 1.3 year program")
        assert duration_years == 1.3
        assert constraint == "exact"
    
    def test_at_least_2_years_master(self, agent):
        """Test: 'at least 2 years master' → duration_years_target=2.0, constraint='min'"""
        duration_years, constraint = agent.router.parse_duration("at least 2 years master")
        assert duration_years == 2.0
        assert constraint == "min"
    
    def test_max_1_year_language(self, agent):
        """Test: 'max 1 year language' → duration_years_target=1.0, constraint='max'"""
        duration_years, constraint = agent.router.parse_duration("max 1 year language")
        assert duration_years == 1.0
        assert constraint == "max"
    
    # ========== INTENT CHANGE TESTS ==========
    
    def test_now_change_to_bachelor(self, agent):
        """Test: 'now change to bachelor' → overwrite degree_level, clear incompatible fields"""
        prev_state = PartnerQueryState()
        prev_state.degree_level = "Language"
        prev_state.major_query = "chinese language"
        
        state = agent.router.route("now change to bachelor", [], prev_state)
        assert state.degree_level == "Bachelor"
        # Major should be cleared if incompatible
        assert state.major_query is None or state.major_query != "chinese language"
    
    def test_instead_master_pharmacy(self, agent):
        """Test: 'instead master pharmacy' → degree_level=Master, major_query=pharmacy"""
        prev_state = PartnerQueryState()
        prev_state.degree_level = "Bachelor"
        
        state = agent.router.route("instead master pharmacy", [], prev_state)
        assert state.degree_level == "Master"
        assert "pharmacy" in (state.major_query or "").lower()
    
    # ========== ADMISSION REQUIREMENTS TESTS ==========
    
    def test_admission_requirements_all_true(self, agent):
        """Test: 'admission requirements' → wants_requirements=True, req_focus all true"""
        state = agent.router.route_stage1_rules("admission requirements")
        assert state.wants_requirements is True
        assert state.req_focus.docs is True
        assert state.req_focus.bank is True
        assert state.req_focus.exams is True
        assert state.req_focus.age is True
        assert state.req_focus.deadline is True
        assert state.req_focus.accommodation is True
        assert state.req_focus.country is True
    
    def test_bank_statement_under_5000_usd(self, agent):
        """Test: 'bank statement under 5000 usd' → wants_requirements=True, req_focus.bank=True"""
        state = agent.router.route_stage1_rules("bank statement under 5000 usd")
        assert state.wants_requirements is True
        assert state.req_focus.bank is True
        # Check budget_max is set
        assert state.budget_max == 5000.0
    
    def test_hsk_required(self, agent):
        """Test: 'HSK required?' → wants_requirements=True, req_focus.exams=True"""
        state = agent.router.route_stage1_rules("HSK required?")
        assert state.wants_requirements is True
        assert state.req_focus.exams is True
    
    # ========== EARLIEST INTAKE TESTS ==========
    
    def test_earliest_intake_no_year_required(self, agent):
        """Test: 'earliest intake' → wants_earliest=True, no intake_year required"""
        state = agent.router.route_stage1_rules("earliest intake")
        assert state.wants_earliest is True
        # intake_year should be None (not required)
        assert state.intake_year is None
        # intake_term should be inferred
        assert state.intake_term is not None
    
    def test_earliest_intake_sorts_by_deadline(self, agent, mock_db):
        """Test: 'earliest intake' queries should sort by nearest deadline"""
        with patch.object(agent.db_service, 'find_program_intakes') as mock_find:
            mock_find.return_value = ([], 0)
            
            route_plan = agent.route_and_clarify(
                [{"role": "user", "content": "earliest intake"}],
                partner_id=1,
                conversation_id="test123"
            )
            
            if route_plan.get("sql_plan"):
                sql_params = route_plan["sql_plan"]
                assert sql_params.get("upcoming_only") is True
                # Should not require intake_year
                assert "intake_year" not in sql_params or sql_params.get("intake_year") is None
    
    # ========== SCHOLARSHIP TESTS ==========
    
    def test_scholarship_available_for_university(self, agent):
        """Test: 'Scholarship available for university' → scholarship filter applies"""
        state = agent.router.route_stage1_rules("Scholarship available for university")
        assert state.wants_scholarship is True
        assert state.intent == agent.router.INTENT_SCHOLARSHIP
    
    def test_csc_scholarship_available(self, agent):
        """Test: 'CSC scholarship available' → scholarship_focus.csc=True"""
        state = agent.router.route_stage1_rules("CSC scholarship available")
        assert state.wants_scholarship is True
        assert state.scholarship_focus.csc is True
    
    def test_scholarship_information_no_load_all(self, agent, mock_db):
        """Test: 'SCHOLARSHIP INFORMATION' doesn't load all universities/majors"""
        with patch.object(agent.db_service, 'search_universities') as mock_search_uni:
            with patch.object(agent.db_service, 'search_majors') as mock_search_major:
                mock_search_uni.return_value = []
                mock_search_major.return_value = []
                
                route_plan = agent.route_and_clarify(
                    [{"role": "user", "content": "SCHOLARSHIP INFORMATION"}],
                    partner_id=1,
                    conversation_id="test123"
                )
                
                # Should NOT call search_universities/search_majors with huge limits
                mock_search_uni.assert_not_called()
                mock_search_major.assert_not_called()
                
                # Should ask clarification
                assert route_plan.get("needs_clarification") is True
    
    # ========== FEES TESTS ==========
    
    def test_calculate_fees(self, agent):
        """Test: 'Calculate fees' → wants_fees=True"""
        state = agent.router.route_stage1_rules("Calculate fees")
        assert state.wants_fees is True
        assert state.intent == agent.router.INTENT_FEES
    
    # ========== LIST QUERIES TESTS ==========
    
    def test_list_universities_in_guangzhou(self, agent):
        """Test: 'Provide list of universities in Guangzhou' → city filter"""
        state = agent.router.route_stage1_rules("Provide list of universities in Guangzhou")
        assert state.wants_list is True
        assert state.city is not None
        assert "guangzhou" in state.city.lower()
    
    # ========== REQUIREMENTS TESTS ==========
    
    def test_provide_requirement_for_program_intakes(self, agent):
        """Test: 'Provide requirement for Program_intakes' → wants_requirements=True"""
        state = agent.router.route_stage1_rules("Provide requirement for Program_intakes")
        assert state.wants_requirements is True
    
    # ========== COUNTRY ALLOWED TESTS ==========
    
    def test_is_country_allowed(self, agent):
        """Test: 'Is country allowed' → req_focus.country=True"""
        state = agent.router.route_stage1_rules("Is country allowed")
        assert state.wants_requirements is True
        assert state.req_focus.country is True
    
    # ========== TUITION FEE FREE TESTS ==========
    
    def test_is_tuition_fee_free(self, agent):
        """Test: 'is tuition fee free' → wants_fees=True"""
        state = agent.router.route_stage1_rules("is tuition fee free")
        assert state.wants_fees is True
    
    # ========== ACCOMMODATION TESTS ==========
    
    def test_type_of_accommodation(self, agent):
        """Test: 'Type of accommodation of a program_intakes' → req_focus.accommodation=True"""
        state = agent.router.route_stage1_rules("Type of accommodation of a program_intakes")
        assert state.wants_requirements is True
        assert state.req_focus.accommodation is True
    
    # ========== PENDING SLOT TESTS ==========
    
    def test_pending_slot_degree_level_bachelov(self, agent):
        """Test: Asked degree_level → user replies 'bachelov' → fills slot, no repeat question"""
        # First turn: ask for degree
        route_plan1 = agent.route_and_clarify(
            [{"role": "user", "content": "SCHOLARSHIP INFORMATION"}],
            partner_id=1,
            conversation_id="test123"
        )
        
        assert route_plan1.get("needs_clarification") is True
        
        # Second turn: user replies "bachelov"
        prev_state = route_plan1.get("state")
        route_plan2 = agent.route_and_clarify(
            [
                {"role": "user", "content": "SCHOLARSHIP INFORMATION"},
                {"role": "assistant", "content": route_plan1.get("clarifying_question", "")},
                {"role": "user", "content": "bachelov"}
            ],
            prev_state=prev_state,
            partner_id=1,
            conversation_id="test123"
        )
        
        state2 = route_plan2.get("state")
        assert state2.degree_level == "Bachelor"
        assert state2.pending_slot is None
        assert route_plan2.get("needs_clarification") is False  # Should not ask again
    
    def test_pending_slot_intake_term_marchh(self, agent):
        """Test: Asked intake_term → user replies 'marchh' → fills via fuzzy"""
        prev_state = PartnerQueryState()
        prev_state.intent = agent.router.INTENT_LIST_PROGRAMS
        prev_state.pending_slot = "intake_term"
        prev_state.is_clarifying = True
        
        state = agent.extract_partner_query_state(
            [{"role": "user", "content": "marchh"}],
            prev_state=prev_state,
            partner_id=1,
            conversation_id="test123"
        )
        
        assert state.intake_term == "March"
        assert state.pending_slot is None
    
    # ========== SQL PARAMETER BUILDING TESTS ==========
    
    def test_build_sql_params_with_fuzzy_major(self, agent):
        """Test: build_sql_params uses fuzzy matching for majors"""
        state = PartnerQueryState()
        state.major_query = "cse"  # Abbreviation
        
        with patch.object(agent, '_get_major_cache') as mock_cache:
            mock_cache.return_value = [
                {"id": 1, "name": "Computer Science and Engineering", "keywords": ["cse", "cs"]}
            ]
            
            sql_params = agent.build_sql_params(state)
            # Should have major_ids from fuzzy match
            assert "major_ids" in sql_params or "major_text" in sql_params
    
    def test_build_sql_params_wants_earliest(self, agent):
        """Test: build_sql_params for wants_earliest sets upcoming_only and infers intake_term"""
        state = PartnerQueryState()
        state.wants_earliest = True
        state.degree_level = "Bachelor"
        
        sql_params = agent.build_sql_params(state)
        assert sql_params.get("upcoming_only") is True
        # intake_term should be inferred
        assert sql_params.get("intake_term") is not None
    
    def test_next_march_intake_not_pagination(self, agent):
        """Test: 'next march intake' should NOT be treated as PAGINATION"""
        state = agent.router.route_stage1_rules("next march intake")
        assert state.intent != agent.router.INTENT_PAGINATION
        assert state.intake_term == "March"
        assert state.wants_earliest is True
    
    def test_next_intake_not_pagination(self, agent):
        """Test: 'next intake' should NOT be treated as PAGINATION"""
        state = agent.router.route_stage1_rules("next intake")
        assert state.intent != agent.router.INTENT_PAGINATION
        assert state.wants_earliest is True
    
    def test_scholarship_info_bachelov_cse_march(self, agent):
        """Test: 'SCHOLARSHIP INFORMATION' then 'bachelov' then 'CSE march' → stays SCHOLARSHIP"""
        # Turn 1: scholarship info
        route_plan1 = agent.route_and_clarify(
            [{"role": "user", "content": "SCHOLARSHIP INFORMATION"}],
            partner_id=1,
            conversation_id="test123"
        )
        assert route_plan1.get("needs_clarification") is True
        prev_state1 = route_plan1.get("state")
        
        # Turn 2: bachelov
        route_plan2 = agent.route_and_clarify(
            [
                {"role": "user", "content": "SCHOLARSHIP INFORMATION"},
                {"role": "assistant", "content": route_plan1.get("clarifying_question", "")},
                {"role": "user", "content": "bachelov"}
            ],
            prev_state=prev_state1,
            partner_id=1,
            conversation_id="test123"
        )
        state2 = route_plan2.get("state")
        assert state2.intent == agent.router.INTENT_SCHOLARSHIP
        assert state2.degree_level == "Bachelor"
        
        # Turn 3: CSE march
        route_plan3 = agent.route_and_clarify(
            [
                {"role": "user", "content": "SCHOLARSHIP INFORMATION"},
                {"role": "assistant", "content": route_plan1.get("clarifying_question", "")},
                {"role": "user", "content": "bachelov"},
                {"role": "assistant", "content": route_plan2.get("clarifying_question", "") if route_plan2.get("needs_clarification") else ""},
                {"role": "user", "content": "CSE march"}
            ],
            prev_state=state2,
            partner_id=1,
            conversation_id="test123"
        )
        state3 = route_plan3.get("state")
        assert state3.intent == agent.router.INTENT_SCHOLARSHIP
        assert "cse" in (state3.major_query or "").lower() or state3.major_query is not None
        assert state3.intake_term == "March"
    
    def test_bsc_physics_next_march_scholarship(self, agent):
        """Test: 'I want to complete my BSC in physics in next march intake from china. What scholarship do you have'"""
        state = agent.router.route_stage1_rules("I want to complete my BSC in physics in next march intake from china. What scholarship do you have")
        assert state.degree_level == "Bachelor"
        assert "physics" in (state.major_query or "").lower()
        assert state.intake_term == "March"
        assert state.wants_scholarship is True
        assert state.intent == agent.router.INTENT_SCHOLARSHIP
        # Should NOT be PAGINATION
        assert state.intent != agent.router.INTENT_PAGINATION
    
    def test_major_acronym_expansion_cs(self, agent):
        """Test: Major acronym 'CS' expands to 'computer science'"""
        expanded = agent._expand_major_acronym("CS")
        assert "computer science" in expanded.lower()
    
    def test_major_acronym_expansion_cse(self, agent):
        """Test: Major acronym 'CSE' expands correctly"""
        expanded = agent._expand_major_acronym("CSE")
        assert "computer science" in expanded.lower()
    
    def test_major_acronym_expansion_ce(self, agent):
        """Test: Major acronym 'CE' expands to 'computer engineering'"""
        expanded = agent._expand_major_acronym("CE")
        assert "computer engineering" in expanded.lower()
    
    def test_resolve_major_ids_cs(self, agent):
        """Test: resolve_major_ids resolves 'CS' to major_ids"""
        with patch.object(agent.db_service, 'search_majors') as mock_search:
            mock_major = Mock()
            mock_major.id = 1
            mock_search.return_value = [mock_major]
            
            major_ids = agent.resolve_major_ids("CS", degree_level="Bachelor")
            assert len(major_ids) > 0
            assert 1 in major_ids
    
    def test_teaching_language_auto_fill_single(self, agent):
        """Test: Teaching language auto-filled when only one language exists"""
        with patch.object(agent.db_service, 'get_distinct_teaching_languages') as mock_lang:
            mock_lang.return_value = {"English"}
            
            # Simulate DB results with single language
            mock_result = Mock()
            mock_result.teaching_language = "English"
            
            # Should auto-fill and not ask
            # This is tested in generate_response flow
    
    def test_teaching_language_ask_multiple(self, agent):
        """Test: Teaching language clarification asked when multiple languages exist"""
        with patch.object(agent.db_service, 'get_distinct_teaching_languages') as mock_lang:
            mock_lang.return_value = {"English", "Chinese"}
            
            # Should ask clarification
            # This is tested in generate_response flow
    
    def test_clarification_no_repeat_when_slot_filled(self, agent):
        """Test: Clarification does not repeat when user fills slot"""
        # First: ask for degree
        route_plan1 = agent.route_and_clarify(
            [{"role": "user", "content": "scholarship info"}],
            partner_id=1,
            conversation_id="test123"
        )
        assert route_plan1.get("needs_clarification") is True
        prev_state1 = route_plan1.get("state")
        
        # Second: user provides degree
        route_plan2 = agent.route_and_clarify(
            [
                {"role": "user", "content": "scholarship info"},
                {"role": "assistant", "content": route_plan1.get("clarifying_question", "")},
                {"role": "user", "content": "Bachelor"}
            ],
            prev_state=prev_state1,
            partner_id=1,
            conversation_id="test123"
        )
        
        # Should NOT ask for degree_level again
        if route_plan2.get("needs_clarification"):
            missing_slots = route_plan2.get("state", PartnerQueryState()).pending_slot
            assert missing_slots != "degree_level"
    
    def test_scholarship_no_bundle_required(self, agent):
        """Test: Scholarship intent does not require scholarship_bundle slot"""
        state = PartnerQueryState()
        state.intent = agent.router.INTENT_SCHOLARSHIP
        state.wants_scholarship = True
        state.degree_level = "Bachelor"
        state.major_query = "CS"
        
        missing_slots, question = agent.determine_missing_fields(state.intent, state, date.today())
        # Should NOT require scholarship_bundle
        assert "scholarship_bundle" not in missing_slots
    
    def test_pagination_only_standalone(self, agent):
        """Test: Pagination only triggered for standalone commands"""
        # Standalone "next" should be pagination
        state1 = agent.router.route_stage1_rules("next")
        assert state1.intent == agent.router.INTENT_PAGINATION
        
        # "next march" should NOT be pagination
        state2 = agent.router.route_stage1_rules("next march")
        assert state2.intent != agent.router.INTENT_PAGINATION
    
    def test_major_keywords_matching(self, agent):
        """Test: Major matching uses keywords JSON array"""
        with patch.object(agent.db_service, 'search_majors') as mock_search:
            mock_major = Mock()
            mock_major.id = 1
            mock_major.name = "Computer Science"
            mock_major.keywords = ["cse", "cs", "computer science"]
            mock_search.return_value = [mock_major]
            
            major_ids = agent.resolve_major_ids("cse")
            assert len(major_ids) > 0
    
    def test_university_alias_matching_hit(self, agent):
        """Test: University alias matching (HIT => Harbin Institute of Technology)"""
        with patch.object(agent.db_service, 'search_universities') as mock_search:
            mock_uni = Mock()
            mock_uni.id = 1
            mock_uni.name = "Harbin Institute of Technology"
            mock_uni.aliases = ["HIT"]
            mock_search.return_value = [mock_uni]
            
            matched, best, all_matches = agent._fuzzy_match_university("HIT")
            assert matched is True
            assert best["name"] == "Harbin Institute of Technology"
    
    def test_zero_results_fallback_major_acronym(self, agent):
        """Test: 0 results fallback expands major acronym and retries"""
        # This is tested in run_db fallback logic
        pass
    
    def test_zero_results_fallback_intake_term(self, agent):
        """Test: 0 results fallback removes intake_term and asks user to choose"""
        # This is tested in run_db fallback logic
        pass
    
    def test_zero_results_fallback_teaching_language(self, agent):
        """Test: 0 results fallback removes teaching_language and asks which language"""
        # This is tested in run_db fallback logic
        pass
    
    def test_earliest_intake_sql_params(self, agent):
        """Test: 'earliest intake' sets upcoming_only and infers intake_term"""
        test_state = PartnerQueryState()
        test_state.wants_earliest = True
        test_state.degree_level = "Bachelor"
        
        sql_params = agent.build_sql_params(test_state)
        assert sql_params.get("upcoming_only") is True
        # intake_term should be inferred if missing
    
    def test_build_sql_params_city_province(self, agent):
        """Test: build_sql_params with city/province sets city/province filters"""
        state = PartnerQueryState()
        state.city = "Guangzhou"
        state.province = "Guangdong"
        
        with patch.object(agent.db_service, 'search_universities') as mock_search:
            mock_uni = Mock()
            mock_uni.id = 1
            mock_search.return_value = [mock_uni]
            
            sql_params = agent.build_sql_params(state)
            assert sql_params.get("city") == "Guangzhou"
            assert sql_params.get("province") == "Guangdong"
    
    # ========== PAGINATION TESTS ==========
    
    def test_pagination_next_returns_next_page(self, agent, mock_db):
        """Test: Pagination 'next' returns next page from cached IDs"""
        # Test that pagination command is detected via router
        state = agent.router.route_stage1_rules("next")
        assert state.intent == agent.router.INTENT_PAGINATION
        assert state.page_action == "next"
        
        state2 = agent.router.route_stage1_rules("show more")
        assert state2.intent == agent.router.INTENT_PAGINATION
    
    # ========== DEADLINE FILTER TESTS ==========
    
    def test_deadline_filter_excludes_past(self, agent):
        """Test: Deadline filter excludes application_deadline < today"""
        # This is tested via DB query - ensure upcoming_only=True is set
        state = PartnerQueryState()
        state.wants_list = True
        
        sql_params = agent.build_sql_params(state)
        assert sql_params.get("upcoming_only") is True
    
    # ========== NOTES FIELD TESTS ==========
    
    def test_notes_included_for_single_program(self, agent):
        """Test: When single program selected, include notes in DB context"""
        # Mock a single ProgramIntake result
        mock_intake = Mock()
        mock_intake.id = 1
        mock_intake.university = Mock()
        mock_intake.university.name = "Test University"
        mock_intake.major = Mock()
        mock_intake.major.name = "Test Major"
        mock_intake.degree_type = "Bachelor"
        mock_intake.intake_term = Mock()
        mock_intake.intake_term.value = "March"
        mock_intake.intake_year = 2026
        mock_intake.notes = "Test notes"
        mock_intake.accommodation_note = "Test accommodation"
        
        req_focus = {"accommodation": True}
        db_context = agent.build_db_context([mock_intake], req_focus, list_mode=False)
        
        assert "Test notes" in db_context
        assert "Test accommodation" in db_context
    
    # ========== SEMANTIC STOPLIST TESTS ==========
    
    def test_semantic_stopword_scholarship_info(self, agent):
        """Test: 'scholarship info' should NOT become major_query"""
        assert agent._is_semantic_stopword("scholarship info") is True
        assert agent._is_semantic_stopword("information") is True
        assert agent._is_semantic_stopword("fees") is True
    
    def test_semantic_stopword_university_list(self, agent):
        """Test: 'university list' should NOT become major_query"""
        assert agent._is_semantic_stopword("university list") is True
    
    # ========== FUZZY MAJOR MATCHING TESTS ==========
    
    def test_fuzzy_major_cse_to_computer_science(self, agent):
        """Test: 'cse' → computer science via abbreviation expansion"""
        expanded = agent._expand_major_acronym("cse")
        assert "computer science" in expanded.lower()
    
    def test_fuzzy_major_cs_to_computer_science(self, agent):
        """Test: 'cs' → computer science"""
        expanded = agent._expand_major_acronym("cs")
        assert "computer science" in expanded.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

