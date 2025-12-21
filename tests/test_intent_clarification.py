"""
Tests for intent locking and clarification flow
"""
import pytest
from unittest.mock import Mock, patch
from app.services.router import PartnerRouter
from app.services.slot_schema import PartnerQueryState
from app.services.openai_service import OpenAIService


class TestIntentClarification:
    """Test intent locking and clarification handling"""
    
    @pytest.fixture
    def router(self):
        """Create router instance"""
        mock_openai = Mock(spec=OpenAIService)
        return PartnerRouter(mock_openai)
    
    def test_scholarship_intent_detected(self, router):
        """Test: 'SCHOLARSHIP INFORMATION' → intent=SCHOLARSHIP"""
        history = [{"role": "user", "content": "SCHOLARSHIP INFORMATION"}]
        state = router.route("SCHOLARSHIP INFORMATION", history)
        assert state.intent == router.INTENT_SCHOLARSHIP
        assert state.wants_scholarship is True
    
    def test_scholarship_clarification_asked(self, router):
        """Test: SCHOLARSHIP intent asks clarification when no degree/intake"""
        state = PartnerQueryState(intent=router.INTENT_SCHOLARSHIP, wants_scholarship=True)
        needs, question = router.needs_clarification(router.INTENT_SCHOLARSHIP, state)
        assert needs is True
        assert "degree level" in question.lower()
        assert "intake" in question.lower()
    
    def test_single_word_bachelor_clarification_reply(self, router):
        """Test: User replies 'Bachelor' to clarification → degree_level set, intent unchanged"""
        prev_state = PartnerQueryState(
            intent=router.INTENT_SCHOLARSHIP,
            wants_scholarship=True
        )
        history = [
            {"role": "assistant", "content": "Which degree level (Language/Bachelor/Master/PhD) and which intake?"},
            {"role": "user", "content": "Bachelor"}
        ]
        state = router.route("Bachelor", history, prev_state)
        assert state.intent == router.INTENT_SCHOLARSHIP  # Intent locked
        assert state.degree_level == "Bachelor"
        assert state.wants_scholarship is True
        assert state.major_query is None  # No major lookup
    
    def test_single_word_masters_clarification_reply(self, router):
        """Test: User replies 'Masters' → degree_level set, no major matching"""
        prev_state = PartnerQueryState(
            intent=router.INTENT_SCHOLARSHIP,
            wants_scholarship=True
        )
        history = [
            {"role": "assistant", "content": "Which degree level?"},
            {"role": "user", "content": "Masters"}
        ]
        state = router.route("Masters", history, prev_state)
        assert state.intent == router.INTENT_SCHOLARSHIP
        assert state.degree_level == "Master"
        assert state.major_query is None  # "Masters" NOT treated as major
    
    def test_no_llm_call_on_clarification_reply(self, router):
        """Test: LLM NOT called on single-word clarification reply"""
        prev_state = PartnerQueryState(intent=router.INTENT_SCHOLARSHIP)
        history = [
            {"role": "assistant", "content": "Which degree level?"},
            {"role": "user", "content": "Bachelor"}
        ]
        with patch.object(router, 'route_stage2_llm') as mock_llm:
            state = router.route("Bachelor", history, prev_state)
            mock_llm.assert_not_called()  # LLM should NOT be called
    
    def test_no_major_lookup_on_single_word_degree(self, router):
        """Test: Single-word degree does NOT trigger major lookup"""
        state = router.route("Bachelor", [])
        assert state.degree_level == "Bachelor"
        assert state.major_query is None
    
    def test_masters_not_treated_as_major(self, router):
        """Test: 'Masters' does NOT trigger major matching"""
        state = router.route("Masters", [])
        assert state.degree_level == "Master"
        assert state.major_query is None or state.major_query != "masters"
    
    def test_instead_master_pharmacy_triggers_major(self, router):
        """Test: 'instead master pharmacy' DOES trigger major matching"""
        prev_state = PartnerQueryState(intent=router.INTENT_LIST_PROGRAMS)
        state = router.route("instead master pharmacy", [], prev_state)
        assert state.degree_level == "Master"
        assert "pharmacy" in (state.major_query or "")
    
    def test_now_change_to_bachelor_overrides_degree_only(self, router):
        """Test: 'now change to bachelor' overrides degree only"""
        prev_state = PartnerQueryState(
            intent=router.INTENT_SCHOLARSHIP,
            degree_level="Master",
            wants_scholarship=True
        )
        state = router.route("now change to bachelor", [], prev_state)
        assert state.intent == router.INTENT_SCHOLARSHIP  # Intent preserved
        assert state.degree_level == "Bachelor"  # Degree overridden
        assert state.wants_scholarship is True  # Flags preserved
    
    def test_intent_locked_during_clarification(self, router):
        """Test: Intent locked during clarification, not reset"""
        prev_state = PartnerQueryState(
            intent=router.INTENT_SCHOLARSHIP,
            wants_scholarship=True
        )
        history = [
            {"role": "assistant", "content": "Which degree level?"},
            {"role": "user", "content": "Bachelor"}
        ]
        state = router.route("Bachelor", history, prev_state)
        assert state.intent == router.INTENT_SCHOLARSHIP
        assert state.wants_scholarship is True
    
    def test_scholarship_proceeds_with_degree_level_only(self, router):
        """Test: SCHOLARSHIP intent proceeds with degree_level only (no major/university required)"""
        state = PartnerQueryState(
            intent=router.INTENT_SCHOLARSHIP,
            degree_level="Master",
            wants_scholarship=True
        )
        needs, _ = router.needs_clarification(router.INTENT_SCHOLARSHIP, state)
        assert needs is False  # Should NOT ask clarification if degree_level provided
    
    def test_no_duplicate_clarification(self, router):
        """Test: Agent does NOT ask same clarification twice"""
        # First clarification
        state1 = PartnerQueryState(intent=router.INTENT_SCHOLARSHIP)
        needs1, q1 = router.needs_clarification(router.INTENT_SCHOLARSHIP, state1)
        assert needs1 is True
        
        # After user provides degree_level
        state2 = PartnerQueryState(intent=router.INTENT_SCHOLARSHIP, degree_level="Master")
        needs2, q2 = router.needs_clarification(router.INTENT_SCHOLARSHIP, state2)
        assert needs2 is False  # Should NOT ask again
    
    def test_typo_degree_word_handled(self, router):
        """Test: Typo like 'Bacheler' still recognized as degree"""
        state = router.route("Bacheler", [])
        assert state.degree_level == "Bachelor"
        assert state.major_query is None
    
    def test_degree_word_in_major_query_cleared(self, router):
        """Test: If 'masters' extracted as major_query, it's cleared"""
        # Simulate rules extracting "masters" as major
        state = router.route_stage1_rules("masters")
        # Should be cleared in route() method
        final_state = router.route("masters", [])
        assert final_state.major_query != "masters"
        assert final_state.degree_level == "Master" or final_state.major_query is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

