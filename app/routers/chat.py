from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from app.database import get_db
from app.models import Conversation, User, Lead, Student
from app.services.rag_service import RAGService
from app.services.openai_service import OpenAIService
from app.services.groq_service import GroqService
from app.services.tavily_service import TavilyService
from app.services.sales_agent import SalesAgent
from app.services.admission_agent import AdmissionAgent
from app.services.db_query_service import DBQueryService
from app.routers.auth import get_current_user, oauth2_scheme
from fastapi import Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.config import settings
import json
import uuid

router = APIRouter()

rag_service = RAGService()
openai_service = OpenAIService()
groq_service = GroqService()
tavily_service = TavilyService()

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    message: str
    device_fingerprint: Optional[str] = None
    use_groq: bool = False

class ChatResponse(BaseModel):
    response: str
    agent_type: str  # "sales" | "admission" | "admin"
    sources: Optional[List[dict]] = None
    used_db: bool = False
    used_rag: bool = False
    used_tavily: bool = False
    missing_documents: Optional[List[str]] = None
    days_to_deadline: Optional[int] = None

# System prompt for the agent
AGENT_SYSTEM_PROMPT = """You are the MalishaEdu AI Enrollment Agent, a friendly and knowledgeable assistant helping students with:

1. **China Education Advisory**: Providing information about Chinese universities, programs, and education system
2. **University Program Recommendations**: Suggesting suitable programs based on student interests
3. **Scholarship Explanations**: Explaining various scholarship opportunities (CSC, provincial, university-specific)
4. **Document Verification & Generation**: Helping with application documents
5. **Lead Collection**: Encouraging students to sign up and provide contact information
6. **Application Reminders**: Gently reminding about missing documents and deadlines
7. **China Visa & Accommodation**: Explaining visa requirements and accommodation fees
8. **Q&A Support**: Answering questions about Chinese universities and life in China

**Your Personality:**
- Clear, friendly, and confident
- Encouraging (promote early applications)
- Student-focused, helpful, and reassuring
- Professional but approachable

**Key Guidelines:**
- Always encourage early application when discussing programs
- Calculate and mention days left until intake deadlines
- When RAG information is available, use it as the primary source
- If RAG information is insufficient, use web search results
- Always end program/university discussions with a call-to-action: "Would you like to start your application now?"
- For unregistered users, gently encourage signup after 2-3 messages
- Be specific about tuition fees, accommodation fees, and requirements when available
- Explain intake dates clearly (March and September are common)

**Response Style:**
- Be concise but comprehensive
- Use bullet points for lists
- Highlight important deadlines and requirements
- Show enthusiasm for helping students achieve their goals"""

def get_or_create_conversation(
    db: Session, 
    user_id: Optional[int] = None, 
    device_fingerprint: Optional[str] = None
) -> Conversation:
    """Get or create conversation for user/device"""
    if user_id:
        conversation = db.query(Conversation).filter(
            Conversation.user_id == user_id
        ).first()
    elif device_fingerprint:
        conversation = db.query(Conversation).filter(
            Conversation.device_fingerprint == device_fingerprint
        ).first()
    else:
        conversation = None
    
    if not conversation:
        conversation = Conversation(
            user_id=user_id,
            device_fingerprint=device_fingerprint or str(uuid.uuid4()),
            messages=[]
        )
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
    
    return conversation

def update_conversation_messages(
    db: Session,
    conversation: Conversation,
    user_message: str,
    assistant_message: str
):
    """Update conversation with new messages (keep last 12)"""
    messages = conversation.messages or []
    
    # Add new messages
    messages.append({"role": "user", "content": user_message})
    messages.append({"role": "assistant", "content": assistant_message})
    
    # Keep only last 12 messages (6 exchanges)
    if len(messages) > 12:
        messages = messages[-12:]
    
    conversation.messages = messages
    db.commit()

def collect_lead(db: Session, name: Optional[str], email: Optional[str], 
                phone: Optional[str], country: Optional[str], 
                device_fingerprint: Optional[str]):
    """Collect lead information"""
    if name or email or phone:
        lead = Lead(
            name=name,
            email=email,
            phone=phone,
            country=country,
            device_fingerprint=device_fingerprint
        )
        db.add(lead)
        db.commit()

# Optional authentication for chat
async def get_optional_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(HTTPBearer(auto_error=False))
) -> Optional[User]:
    """Optional authentication - returns None if no token provided"""
    if not credentials:
        return None
    try:
        return get_current_user(credentials.credentials)
    except:
        return None

@router.post("/", response_model=ChatResponse)
async def chat(
    request: ChatRequest, 
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_current_user)
):
    """
    Router Agent for MalishaEdu AI Enrollment System
    
    Routing Logic:
    1. If NO authenticated user_id → Use SalesAgent.generate_response()
    2. If authenticated user_id AND Student row exists → Use AdmissionAgent.generate_response()
    3. If user is admin (role = ADMIN) → Return admin tools message (not LLM chat)
    
    The router:
    - Always passes the last 12 conversation messages to the agent (for context)
    - Never bypasses the agents' DB-first logic (never calls RAG or Tavily directly)
    - Returns the agent's structured output to the frontend
    """
    try:
        # Router Logic: Determine which agent to use
        is_authenticated = current_user is not None
        user_id = current_user.id if current_user else None
        user_role = current_user.role.value if current_user else "guest"
        
        # Get or create conversation
        conversation = get_or_create_conversation(
            db, 
            user_id=user_id, 
            device_fingerprint=request.device_fingerprint
        )
        
        # Get conversation history (last 12 messages) - ALWAYS pass to agent
        messages_history = conversation.messages or []
        
        # Route to appropriate agent
        if not is_authenticated:
            # NO authenticated user_id → Use SalesAgent
            agent = SalesAgent(db)
            result = agent.generate_response(
                user_message=request.message,
                conversation_history=messages_history,
                device_fingerprint=request.device_fingerprint
            )
            
            agent_type = "sales"
            used_db = bool(result.get('db_context'))
            used_rag = bool(result.get('rag_context'))
            used_tavily = bool(result.get('tavily_context'))
            missing_documents = None
            days_to_deadline = None
            
        elif user_role == "admin":
            # Admin users → Route to admin tools (not LLM chat)
            return ChatResponse(
                response="As an admin, please use the Admin Dashboard for analytics, RAG uploads, and configuration. The chat interface is designed for students and prospects.",
                agent_type="admin",
                sources=None,
                used_db=False,
                used_rag=False,
                used_tavily=False,
                missing_documents=None,
                days_to_deadline=None
            )
            
        elif user_role == "student":
            # Authenticated user_id AND Student row exists → Use AdmissionAgent
            db_query_service = DBQueryService(db)
            student = db_query_service.get_student_profile(user_id)
            
            if not student:
                # Create student profile if it doesn't exist
                student = Student(user_id=user_id)
                db.add(student)
                db.commit()
                db.refresh(student)
            
            agent = AdmissionAgent(db, student)
            result = agent.generate_response(
                user_message=request.message,
                conversation_history=messages_history
            )
            
            agent_type = "admission"
            used_db = bool(result.get('student_context') or result.get('program_context'))
            used_rag = bool(result.get('rag_context'))
            used_tavily = bool(result.get('tavily_context'))
            missing_documents = result.get('missing_documents')
            days_to_deadline = result.get('days_to_deadline')
        else:
            raise HTTPException(status_code=400, detail=f"Unknown user role: {user_role}")
        
        # Update conversation with new messages
        update_conversation_messages(db, conversation, request.message, result['response'])
        
        # Return agent's structured output
        return ChatResponse(
            response=result['response'],
            agent_type=agent_type,
            sources=None,  # Could include DB results here if needed
            used_db=used_db,
            used_rag=used_rag,
            used_tavily=used_tavily,
            missing_documents=missing_documents,
            days_to_deadline=days_to_deadline
        )
    
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error generating response: {str(e)}")

@router.post("/stream")
async def chat_stream(
    request: ChatRequest, 
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_current_user)
):
    """
    Streaming chat endpoint with Router Agent logic
    Uses the same routing logic as the regular chat endpoint
    """
    try:
        # Router Logic: Same as regular chat endpoint
        is_authenticated = current_user is not None
        user_id = current_user.id if current_user else None
        user_role = current_user.role.value if current_user else "guest"
        
        # Get or create conversation
        conversation = get_or_create_conversation(
            db, 
            user_id=user_id, 
            device_fingerprint=request.device_fingerprint
        )
        
        # Get conversation history (last 12 messages)
        messages_history = conversation.messages or []
        
        # Route to appropriate agent (same logic as regular endpoint)
        if not is_authenticated:
            # SalesAgent for non-authenticated users
            agent = SalesAgent(db)
            result = agent.generate_response(
                user_message=request.message,
                conversation_history=messages_history,
                device_fingerprint=request.device_fingerprint
            )
            full_response = result['response']
            used_rag = bool(result.get('rag_context'))
            used_tavily = bool(result.get('tavily_context'))
            
        elif user_role == "admin":
            # Admin users → Return admin tools message
            full_response = "As an admin, please use the Admin Dashboard for analytics, RAG uploads, and configuration. The chat interface is designed for students and prospects."
            used_rag = False
            used_tavily = False
            
        elif user_role == "student":
            # AdmissionAgent for authenticated students
            db_query_service = DBQueryService(db)
            student = db_query_service.get_student_profile(user_id)
            
            if not student:
                # Create student profile if it doesn't exist
                student = Student(user_id=user_id)
                db.add(student)
                db.commit()
                db.refresh(student)
            
            agent = AdmissionAgent(db, student)
            result = agent.generate_response(
                user_message=request.message,
                conversation_history=messages_history
            )
            full_response = result['response']
            used_rag = bool(result.get('rag_context'))
            used_tavily = bool(result.get('tavily_context'))
        else:
            full_response = f"Unknown user role: {user_role}"
            used_rag = False
            used_tavily = False
        
        # Update conversation
        update_conversation_messages(db, conversation, request.message, full_response)
        
        # Stream the response
        async def generate_stream():
            # Split response into chunks for streaming effect
            words = full_response.split(' ')
            for i, word in enumerate(words):
                chunk = word + (' ' if i < len(words) - 1 else '')
                yield f"data: {json.dumps({'content': chunk, 'done': False})}\n\n"
            yield f"data: {json.dumps({'content': '', 'done': True, 'used_rag': used_rag, 'used_tavily': used_tavily})}\n\n"
        
        return StreamingResponse(generate_stream(), media_type="text/event-stream")
    
    except Exception as e:
        error_message = str(e)
        import traceback
        error_traceback = traceback.format_exc()
        print(f"Error in chat_stream: {error_message}\n{error_traceback}")
        
        async def error_stream():
            yield f"data: {json.dumps({'error': error_message})}\n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")

