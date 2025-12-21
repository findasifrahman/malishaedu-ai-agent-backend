"""
Unit tests for DB-first 2-stage router PartnerAgent
Tests route_and_clarify(), build_sql_params(), and SQL parameter building
"""
import pytest
from unittest.mock import Mock, MagicMock, patch
from datetime import date, datetime, timezone
from app.services.partner_agent import PartnerAgent
from app.services.slot_schema import PartnerQueryState
from app.services.router import PartnerRouter
from app.models import IntakeTerm


class TestPartnerAgentDBFirst:
    """Test suite for DB-first PartnerAgent routing"""
    
    @pytest.fixture
    def mock_db(self):
        """Create mock database session"""
        return Mock()
    
    @pytest.fixture
    def agent(self, mock_db):
        """Create PartnerAgent instance with mocked dependencies"""
        with patch('app.services.partner_agent.DBQueryService'), \
             patch('app.services.partner_agent.TavilyService'), \
             patch('app.services.partner_agent.OpenAIService'):
            agent = PartnerAgent(mock_db)
            # Mock db_service methods
            agent.db_service = Mock()
            agent.db_service.search_universities = Mock(return_value=[])
            agent.db_service.search_majors = Mock(return_value=[])
            agent.db_service.search_program_intakes = Mock(return_value=[])
            agent.db_service.list_universities_by_filters = Mock(return_value=[])
            agent.db_service.get_program_scholarships = Mock(return_value=[])
            return agent
    
    # ========== ROUTING + TYPO TESTS ==========
    
    def test_bachelorvvvv_university_list(self, agent):
        """Test: 'Bachelorvvvv university list'"""
        history = [{"role": "user", "content": "Bachelorvvvv university list"}]
        route_plan = agent.route_and_clarify(history)
        assert route_plan["intent"] == agent.router.INTENT_LIST_UNIVERSITIES
        assert route_plan["state"].degree_level == "Bachelor"
        assert route_plan["needs_clarification"] is False
    
    def test_university_list(self, agent):
        """Test: 'university list'"""
        history = [{"role": "user", "content": "university list"}]
        route_plan = agent.route_and_clarify(history)
        assert route_plan["intent"] == agent.router.INTENT_LIST_UNIVERSITIES
        assert route_plan["needs_clarification"] is True  # No filters, needs clarification
    
    def test_language_program_march_intake_university_list(self, agent):
        """Test: 'language program march intake university list'"""
        history = [{"role": "user", "content": "language program march intake university list"}]
        route_plan = agent.route_and_clarify(history)
        assert route_plan["intent"] == agent.router.INTENT_LIST_UNIVERSITIES
        assert route_plan["state"].degree_level == "Language"
        assert route_plan["state"].intake_term == "March"
        assert route_plan["needs_clarification"] is False
    
    # ========== DURATION VARIANTS TESTS ==========
    
    def test_4_months_language_course(self, agent):
        """Test: 'I want 4 months language course'"""
        history = [{"role": "user", "content": "I want 4 months language course"}]
        route_plan = agent.route_and_clarify(history)
        state = route_plan["state"]
        assert state.degree_level == "Language"
        assert state.duration_years_target == pytest.approx(4/12, abs=0.01)
        assert state.duration_constraint == "exact"
        assert route_plan["sql_plan"]["duration_years_target"] == pytest.approx(4/12, abs=0.01)
    
    def test_1_3_year_program(self, agent):
        """Test: 'I want 1.3 year program'"""
        history = [{"role": "user", "content": "I want 1.3 year program"}]
        route_plan = agent.route_and_clarify(history)
        state = route_plan["state"]
        assert state.duration_years_target == 1.3
        assert route_plan["sql_plan"]["duration_years_target"] == 1.3
    
    def test_at_least_2_years_master(self, agent):
        """Test: 'at least 2 years master'"""
        history = [{"role": "user", "content": "at least 2 years master"}]
        route_plan = agent.route_and_clarify(history)
        state = route_plan["state"]
        assert state.degree_level == "Master"
        assert state.duration_years_target == 2.0
        assert state.duration_constraint == "min"
        assert route_plan["sql_plan"]["duration_constraint"] == "min"
    
    def test_max_1_year_language(self, agent):
        """Test: 'max 1 year language'"""
        history = [{"role": "user", "content": "max 1 year language"}]
        route_plan = agent.route_and_clarify(history)
        state = route_plan["state"]
        assert state.degree_level == "Language"
        assert state.duration_years_target == 1.0
        assert state.duration_constraint == "max"
        assert route_plan["sql_plan"]["duration_constraint"] == "max"
    
    # ========== INTENT SWITCH MID-CONVERSATION TESTS ==========
    
    def test_now_change_to_bachelor(self, agent):
        """Test: History 'language course' then user 'now change to bachelor'"""
        prev_state = PartnerQueryState(
            intent=agent.router.INTENT_LIST_PROGRAMS,
            degree_level="Language"
        )
        history = [{"role": "user", "content": "now change to bachelor"}]
        route_plan = agent.route_and_clarify(history, prev_state)
        assert route_plan["state"].degree_level == "Bachelor"
        # Major query should be cleared if not re-stated
        assert route_plan["state"].major_query is None or route_plan["state"].major_query != "language"
    
    def test_instead_master_pharmacy(self, agent):
        """Test: History 'bachelor' then user 'instead master pharmacy'"""
        prev_state = PartnerQueryState(
            intent=agent.router.INTENT_LIST_PROGRAMS,
            degree_level="Bachelor"
        )
        history = [{"role": "user", "content": "instead master pharmacy"}]
        route_plan = agent.route_and_clarify(history, prev_state)
        assert route_plan["state"].degree_level == "Master"
        assert "pharmacy" in (route_plan["state"].major_query or "")
    
    # ========== ADMISSION REQUIREMENTS TESTS ==========
    
    def test_admission_requirements_all_flags(self, agent):
        """Test: 'admission requirements' should set all req_focus flags"""
        history = [{"role": "user", "content": "admission requirements"}]
        route_plan = agent.route_and_clarify(history)
        assert route_plan["intent"] == agent.router.INTENT_ADMISSION_REQUIREMENTS
        req_focus = route_plan["req_focus"]
        assert req_focus["docs"] is True
        assert req_focus["bank"] is True
        assert req_focus["exams"] is True
        assert req_focus["age"] is True
        assert req_focus["deadline"] is True
    
    def test_bank_statement_under_5000_usd(self, agent):
        """Test: 'bank statement under 5000 usd' - should set ONLY bank flag"""
        history = [{"role": "user", "content": "bank statement under 5000 usd"}]
        route_plan = agent.route_and_clarify(history)
        assert route_plan["intent"] == agent.router.INTENT_ADMISSION_REQUIREMENTS
        req_focus = route_plan["req_focus"]
        assert req_focus["bank"] is True
        assert req_focus["docs"] is False  # Only bank, not all flags
        assert route_plan["state"].budget_max == 5000.0
    
    def test_hsk_required(self, agent):
        """Test: 'HSK required?' - should set ONLY exams flag"""
        history = [{"role": "user", "content": "HSK required?"}]
        route_plan = agent.route_and_clarify(history)
        assert route_plan["intent"] == agent.router.INTENT_ADMISSION_REQUIREMENTS
        req_focus = route_plan["req_focus"]
        assert req_focus["exams"] is True
        assert req_focus["docs"] is False  # Only exams, not all flags
    
    # ========== EARLIEST INTAKE TESTS ==========
    
    def test_earliest_intake_no_year_required(self, agent):
        """Test: 'earliest intake' should not require intake_year"""
        history = [{"role": "user", "content": "earliest intake"}]
        route_plan = agent.route_and_clarify(history)
        state = route_plan["state"]
        assert state.wants_earliest is True
        assert state.intake_year is None
        # Should infer intake_term from current month
        assert state.intake_term is not None
        assert route_plan["sql_plan"]["upcoming_only"] is True
    
    def test_earliest_intake_infers_march_for_dec_jan_feb(self, agent):
        """Test earliest intake inference for Dec/Jan/Feb => March"""
        with patch('app.services.partner_agent.date') as mock_date:
            mock_date.today.return_value = date(2025, 12, 15)
            mock_date.side_effect = lambda *args, **kw: date(*args, **kw)
            history = [{"role": "user", "content": "earliest intake"}]
            route_plan = agent.route_and_clarify(history)
            assert route_plan["state"].intake_term == "March"
    
    def test_earliest_intake_infers_september_for_mar_aug(self, agent):
        """Test earliest intake inference for Mar-Aug => September"""
        with patch('app.services.partner_agent.date') as mock_date:
            mock_date.today.return_value = date(2025, 6, 15)
            mock_date.side_effect = lambda *args, **kw: date(*args, **kw)
            history = [{"role": "user", "content": "earliest intake"}]
            route_plan = agent.route_and_clarify(history)
            assert route_plan["state"].intake_term == "September"
    
    # ========== SCHOLARSHIP TESTS ==========
    
    def test_scholarship_available_for_university(self, agent):
        """Test: 'Scholarship available for university'"""
        history = [{"role": "user", "content": "Scholarship available for university"}]
        route_plan = agent.route_and_clarify(history)
        assert route_plan["intent"] == agent.router.INTENT_SCHOLARSHIP
        assert route_plan["req_focus"]["scholarship"] is True
        # Should NOT load all universities/majors
        agent.db_service.search_universities.assert_not_called()
        agent.db_service.search_majors.assert_not_called()
    
    def test_csc_scholarship_available(self, agent):
        """Test: 'CSC scholarship available'"""
        history = [{"role": "user", "content": "CSC scholarship available"}]
        route_plan = agent.route_and_clarify(history)
        assert route_plan["intent"] == agent.router.INTENT_SCHOLARSHIP
        assert route_plan["state"].scholarship_focus.csc is True
        # Should ask clarification if no target
        assert route_plan["needs_clarification"] is True
    
    # ========== FEES TESTS ==========
    
    def test_calculate_fees(self, agent):
        """Test: 'calculate fees'"""
        history = [{"role": "user", "content": "calculate fees"}]
        route_plan = agent.route_and_clarify(history)
        assert route_plan["intent"] == agent.router.INTENT_FEES
        assert route_plan["req_focus"]["fees"] is True
        # Should ask clarification if no target
        assert route_plan["needs_clarification"] is True
    
    def test_is_tuition_fee_free(self, agent):
        """Test: 'is tuition fee free?'"""
        history = [{"role": "user", "content": "is tuition fee free?"}]
        route_plan = agent.route_and_clarify(history)
        assert route_plan["intent"] == agent.router.INTENT_FEES
        assert route_plan["req_focus"]["fees"] is True
    
    # ========== REQUIREMENTS TESTS ==========
    
    def test_provide_requirement_for_program_intakes(self, agent):
        """Test: Provide requirement for ProgramIntakes"""
        history = [{"role": "user", "content": "admission requirements for master computer science"}]
        route_plan = agent.route_and_clarify(history)
        assert route_plan["intent"] == agent.router.INTENT_ADMISSION_REQUIREMENTS
        assert route_plan["state"].degree_level == "Master"
        assert "computer science" in (route_plan["state"].major_query or "")
        assert route_plan["sql_plan"] is not None
    
    # ========== LOCATION TESTS ==========
    
    def test_list_universities_in_guangzhou(self, agent):
        """Test: 'list universities in Guangzhou'"""
        history = [{"role": "user", "content": "list universities in Guangzhou"}]
        route_plan = agent.route_and_clarify(history)
        assert route_plan["intent"] == agent.router.INTENT_LIST_UNIVERSITIES
        assert route_plan["state"].city == "Guangzhou"
        assert route_plan["sql_plan"] is not None
        # Should use DBQueryService, not load all universities
        agent.db_service.list_universities_by_filters.assert_not_called()  # Called in run_db, not route_and_clarify
    
    # ========== COUNTRY / ELIGIBILITY TESTS ==========
    
    def test_is_country_allowed(self, agent):
        """Test: 'Is country allowed?'"""
        history = [{"role": "user", "content": "Is Bangladesh allowed?"}]
        route_plan = agent.route_and_clarify(history)
        assert route_plan["intent"] == agent.router.INTENT_ADMISSION_REQUIREMENTS
        assert route_plan["req_focus"]["country"] is True
    
    # ========== ACCOMMODATION TESTS ==========
    
    def test_type_of_accommodation_of_program_intake(self, agent):
        """Test: 'type of accommodation of a program_intake'"""
        history = [{"role": "user", "content": "type of accommodation"}]
        route_plan = agent.route_and_clarify(history)
        assert route_plan["intent"] == agent.router.INTENT_ADMISSION_REQUIREMENTS
        assert route_plan["req_focus"]["accommodation"] is True
    
    # ========== SQL PARAMETER BUILDING TESTS ==========
    
    def test_build_sql_params_with_all_filters(self, agent):
        """Test build_sql_params with all filters"""
        state = PartnerQueryState(
            intent=agent.router.INTENT_LIST_PROGRAMS,
            degree_level="Master",
            major_query="computer science",
            university_query="Beijing University",
            city="Beijing",
            intake_term="March",
            intake_year=2026,
            duration_years_target=2.0,
            duration_constraint="min",
            teaching_language="English",
            budget_max=50000.0
        )
        sql_params = agent.build_sql_params(state)
        assert sql_params["degree_level"] == "Master"
        assert sql_params["intake_term"] == IntakeTerm.MARCH
        assert sql_params["intake_year"] == 2026
        assert sql_params["duration_years_target"] == 2.0
        assert sql_params["duration_constraint"] == "min"
        assert sql_params["teaching_language"] == "English"
        assert sql_params["budget_max"] == 50000.0
        assert sql_params["upcoming_only"] is True
    
    def test_build_sql_params_upcoming_only_default(self, agent):
        """Test that upcoming_only defaults to True"""
        state = PartnerQueryState(intent=agent.router.INTENT_LIST_PROGRAMS)
        sql_params = agent.build_sql_params(state)
        assert sql_params["upcoming_only"] is True
    
    def test_build_sql_params_list_increases_limit(self, agent):
        """Test that list queries increase limit"""
        state = PartnerQueryState(
            intent=agent.router.INTENT_LIST_PROGRAMS,
            wants_list=True
        )
        sql_params = agent.build_sql_params(state)
        assert sql_params["limit"] == 24  # MAX_LIST_INTAKES
    
    # ========== NO EAGER LOADING TESTS ==========
    
    def test_no_eager_loading_on_init(self, agent):
        """Test that __init__ does NOT load all universities/majors"""
        # Verify that _get_uni_name_cache is None initially
        assert agent._uni_name_cache is None
        assert agent._uni_cache_timestamp is None
    
    def test_lazy_cache_loads_only_when_needed(self, agent):
        """Test that lazy cache loads only when needed"""
        # Cache should be None initially
        assert agent._uni_name_cache is None
        
        # Access cache (should trigger load)
        cache = agent._get_uni_name_cache()
        
        # Now cache should be loaded
        assert agent._uni_name_cache is not None
        assert agent._uni_cache_timestamp is not None
    
    # ========== CLARIFICATION TESTS ==========
    
    def test_clarification_for_vague_scholarship_query(self, agent):
        """Test that vague 'SCHOLARSHIP INFORMATION' asks clarification"""
        history = [{"role": "user", "content": "Scholarship information"}]
        route_plan = agent.route_and_clarify(history)
        assert route_plan["intent"] == agent.router.INTENT_SCHOLARSHIP
        # Should ask clarification if no target
        assert route_plan["needs_clarification"] is True
        assert route_plan["clarifying_question"] is not None
        assert "degree level" in route_plan["clarifying_question"].lower()
    
    def test_no_clarification_when_filters_present(self, agent):
        """Test no clarification when filters are present"""
        history = [{"role": "user", "content": "master computer science march 2026"}]
        route_plan = agent.route_and_clarify(history)
        assert route_plan["needs_clarification"] is False
        assert route_plan["sql_plan"] is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

