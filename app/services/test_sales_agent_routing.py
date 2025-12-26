"""
Unit tests for SalesAgent routing improvements
"""
from app.services.sales_agent import SalesAgent
from app.services.sales_agent import StudentProfileState
from app.database import get_db

def test_routing():
    """Test routing for common questions"""
    db = next(get_db())
    agent = SalesAgent(db)
    
    # Test 1: "How do you handle the application and admission process?"
    query1 = "How do you handle the application and admission process?"
    state1 = StudentProfileState()
    result1 = agent.classify_query(query1, state1)
    assert result1['intent'] in ['general_faq', 'service_policy'], f"Expected general_faq or service_policy, got {result1['intent']}"
    assert result1['needs_db'] == False, "Should not need DB for process question"
    assert result1['doc_type'] in ['b2c_study', 'service_policy'], f"Expected b2c_study or service_policy, got {result1['doc_type']}"
    print("✓ Test 1 passed: Process question classified correctly")
    
    # Test 2: "What is your Dhaka office address?"
    query2 = "What is your Dhaka office address?"
    state2 = StudentProfileState()
    result2 = agent.classify_query(query2, state2)
    assert result2['intent'] == 'people_contact', f"Expected people_contact, got {result2['intent']}"
    assert result2['needs_db'] == False, "Should not need DB for contact question"
    assert result2['doc_type'] == 'people_contact', f"Expected people_contact, got {result2['doc_type']}"
    print("✓ Test 2 passed: Contact question classified correctly")
    
    # Test 3: "List top universities for Computer Science"
    query3 = "List top universities for Computer Science"
    state3 = StudentProfileState()
    state3.major = "Computer Science"
    result3 = agent.classify_query(query3, state3)
    assert result3['intent'] == 'program_specific', f"Expected program_specific, got {result3['intent']}"
    assert result3['needs_db'] == True, "Should need DB for university listing"
    print("✓ Test 3 passed: University listing classified correctly")
    
    # Test 4: "CSCA scholarship coverage"
    query4 = "CSCA scholarship coverage"
    state4 = StudentProfileState()
    result4 = agent.classify_query(query4, state4)
    assert result4['intent'] == 'csca', f"Expected csca, got {result4['intent']}"
    assert result4['needs_db'] == False, "Should not need DB for CSCA question"
    assert result4['doc_type'] == 'csca', f"Expected csca, got {result4['doc_type']}"
    print("✓ Test 4 passed: CSCA question classified correctly")
    
    print("\nAll routing tests passed!")

if __name__ == "__main__":
    test_routing()

