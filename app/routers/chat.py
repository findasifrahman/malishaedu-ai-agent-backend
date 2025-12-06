from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
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

# In-memory conversation history storage
# Maps chat_session_id or device_fingerprint to list of messages
# Format: {"role": "user" | "assistant", "content": str}
_conversation_history_store: Dict[str, List[Dict[str, str]]] = {}

def get_conversation_history(session_key: str) -> List[Dict[str, str]]:
    """Get conversation history for a session key (chat_session_id or device_fingerprint)"""
    return _conversation_history_store.get(session_key, [])

def append_to_conversation_history(session_key: str, role: str, content: str):
    """Append a message to conversation history for a session key"""
    if session_key not in _conversation_history_store:
        _conversation_history_store[session_key] = []
    
    _conversation_history_store[session_key].append({"role": role, "content": content})
    
    # Keep only last 12 messages (6 exchanges)
    if len(_conversation_history_store[session_key]) > 12:
        _conversation_history_store[session_key] = _conversation_history_store[session_key][-12:]

def clear_conversation_history(session_key: str):
    """Clear conversation history for a session (for new chat sessions)"""
    if session_key in _conversation_history_store:
        del _conversation_history_store[session_key]

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    message: str
    device_fingerprint: Optional[str] = None  # Keep for backward compatibility
    chat_session_id: Optional[str] = None  # New: per-chat session identifier
    messages: Optional[List[ChatMessage]] = None  # New: frontend can send conversation history
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
    show_lead_form: bool = False  # Flag to show lead capture form
    lead_form_prefill: Optional[Dict[str, Any]] = None  # Pre-fill data for lead form

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
    device_fingerprint: Optional[str] = None,
    chat_session_id: Optional[str] = None
) -> Optional[Conversation]:
    """
    Get or create conversation for user/device/session.
    
    For logged-in users: Use user_id (persist in DB).
    For anonymous users: Use chat_session_id (persist in DB only if chat_session_id provided).
    If no chat_session_id for anonymous users, return None (don't persist).
    """
    if user_id:
        # Logged-in user: use user_id
        conversation = db.query(Conversation).filter(
            Conversation.user_id == user_id
        ).first()
        if not conversation:
            conversation = Conversation(
                user_id=user_id,
                chat_session_id=chat_session_id,
                messages=[]
            )
            db.add(conversation)
            db.commit()
            db.refresh(conversation)
        return conversation
    elif chat_session_id:
        # Anonymous user with chat_session_id: use chat_session_id
        conversation = db.query(Conversation).filter(
            Conversation.chat_session_id == chat_session_id,
            Conversation.user_id.is_(None)  # Only anonymous conversations
        ).first()
        if not conversation:
            conversation = Conversation(
                chat_session_id=chat_session_id,
                device_fingerprint=device_fingerprint,  # Keep for backward compatibility
                messages=[]
            )
            db.add(conversation)
            db.commit()
            db.refresh(conversation)
        return conversation
    else:
        # Anonymous user without chat_session_id: don't persist
        return None

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
    credentials: Optional[HTTPAuthorizationCredentials] = Security(HTTPBearer(auto_error=False)),
    db: Session = Depends(get_db)
) -> Optional[User]:
    """Optional authentication - returns None if no token provided"""
    from app.routers.auth import get_current_user
    from jose import JWTError, jwt
    from app.config import settings
    from app.models import User
    from fastapi import HTTPException, status
    
    if not credentials:
        print("DEBUG: get_optional_current_user - No credentials provided")
        return None
    
    token = credentials.credentials
    print(f"DEBUG: get_optional_current_user - Token received: {token[:50] if token else 'None'}...")
    
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        user_id_str = payload.get("sub")
        if user_id_str is None:
            print(f"DEBUG: get_optional_current_user - No 'sub' claim in token. Payload: {payload}")
            return None
        try:
            user_id: int = int(user_id_str)
        except (ValueError, TypeError):
            print(f"DEBUG: get_optional_current_user - Invalid user_id format: {user_id_str}")
            return None
    except JWTError as e:
        print(f"DEBUG: get_optional_current_user - JWT Error: {str(e)}")
        return None
    except Exception as e:
        print(f"DEBUG: get_optional_current_user - Unexpected error: {str(e)}")
        import traceback
        traceback.print_exc()
        return None
    
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        print(f"DEBUG: get_optional_current_user - User not found for ID: {user_id}")
        return None
    
    print(f"DEBUG: get_optional_current_user - User authenticated: {user.id} ({user.role.value})")
    return user

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
        
        # DEBUG: Log authentication status
        print(f"\n{'='*80}")
        print(f"DEBUG: Chat Router - Authentication Check")
        print(f"DEBUG: is_authenticated = {is_authenticated}")
        print(f"DEBUG: user_id = {user_id}")
        print(f"DEBUG: user_role = {user_role}")
        print(f"DEBUG: current_user = {current_user}")
        print(f"{'='*80}\n")
        
        # Determine session key for in-memory storage
        # Priority: chat_session_id > device_fingerprint > user_id
        session_key = None
        if request.chat_session_id:
            session_key = f"session_{request.chat_session_id}"
        elif request.device_fingerprint:
            session_key = f"device_{request.device_fingerprint}"
        elif user_id:
            session_key = f"user_{user_id}"
        
        # Get conversation history from in-memory store
        messages_history = []
        conversation = None
        
        if is_authenticated:
            # Logged-in user: use DB as primary, but also check in-memory store
            conversation = get_or_create_conversation(
                db, 
                user_id=user_id, 
                chat_session_id=request.chat_session_id
            )
            # Prefer in-memory store if available (more up-to-date), fallback to DB
            if session_key:
                messages_history = get_conversation_history(session_key)
            if not messages_history and conversation:
                messages_history = conversation.messages or []
        else:
            # Anonymous user: use in-memory store as primary source
            if session_key:
                messages_history = get_conversation_history(session_key)
            
            # Fallback: if no in-memory history and chat_session_id provided, try DB
            if not messages_history and request.chat_session_id:
                conversation = get_or_create_conversation(
                    db,
                    chat_session_id=request.chat_session_id,
                    device_fingerprint=request.device_fingerprint
                )
                if conversation:
                    messages_history = conversation.messages or []
                    # Sync DB history to in-memory store
                    if session_key and messages_history:
                        _conversation_history_store[session_key] = messages_history.copy()
        
        # Append current user message to history BEFORE calling agent
        if session_key:
            append_to_conversation_history(session_key, "user", request.message)
            # Refresh messages_history to include the new user message
            messages_history = get_conversation_history(session_key)
        
        # Check if lead exists for this chat_session_id (for anonymous users)
        use_db = False
        if not is_authenticated and request.chat_session_id:
            from app.models import Lead
            lead = db.query(Lead).filter(
                Lead.chat_session_id == request.chat_session_id
            ).first()
            use_db = lead is not None
        
        # Route to appropriate agent
        show_lead_form = False  # Initialize to False, will be set by SalesAgent
        
        if not is_authenticated:
            # NO authenticated user_id → Use SalesAgent
            agent = SalesAgent(db)
            result = agent.generate_response(
                user_message=request.message,
                conversation_history=messages_history,
                chat_session_id=request.chat_session_id,
                use_db=use_db
            )
            
            agent_type = "sales"
            used_db = bool(result.get('db_context'))
            used_rag = bool(result.get('rag_context'))
            used_tavily = bool(result.get('tavily_context'))
            missing_documents = None
            days_to_deadline = None
            show_lead_form = result.get('show_lead_form', False)
            lead_form_prefill = result.get('lead_form_prefill', {})
            
            # DEBUG: Log show_lead_form flag to verify it's being set
            print(f"\n{'='*80}")
            print(f"DEBUG: SalesAgent result - show_lead_form = {show_lead_form}")
            print(f"DEBUG: lead_form_prefill = {lead_form_prefill}")
            print(f"DEBUG: result keys = {list(result.keys())}")
            print(f"DEBUG: result['show_lead_form'] = {result.get('show_lead_form')}")
            print(f"{'='*80}\n")
            
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
                days_to_deadline=None,
                show_lead_form=False
            )
            
        elif user_role == "student":
            # Authenticated user_id AND Student row exists → Use AdmissionAgent
            print("DEBUG: Routing to AdmissionAgent (student role)")
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
        
        # Append assistant response to in-memory history
        if session_key:
            append_to_conversation_history(session_key, "assistant", result['response'])
        
        # Update conversation in DB (only if conversation exists or should be created)
        if conversation:
            update_conversation_messages(db, conversation, request.message, result['response'])
        elif not is_authenticated and request.chat_session_id:
            # Anonymous user with chat_session_id: create conversation if it doesn't exist
            conversation = get_or_create_conversation(
                db,
                chat_session_id=request.chat_session_id,
                device_fingerprint=request.device_fingerprint
            )
            if conversation:
                update_conversation_messages(db, conversation, request.message, result['response'])
        
        # Return agent's structured output
        final_show_lead_form = show_lead_form if 'show_lead_form' in locals() else False
        final_lead_form_prefill = result.get('lead_form_prefill', {}) if 'lead_form_prefill' in result else (lead_form_prefill if 'lead_form_prefill' in locals() else {})
        
        # DEBUG: Log final response to verify show_lead_form is being sent
        print(f"\n{'='*80}")
        print(f"DEBUG: ChatResponse being returned - show_lead_form = {final_show_lead_form}")
        print(f"DEBUG: lead_form_prefill = {final_lead_form_prefill}")
        print(f"DEBUG: agent_type = {agent_type}")
        print(f"DEBUG: response length = {len(result['response']) if 'response' in result else 'N/A'} chars")
        print(f"{'='*80}\n")
        
        return ChatResponse(
            response=result['response'],
            agent_type=agent_type,
            sources=None,  # Could include DB results here if needed
            used_db=used_db,
            used_rag=used_rag,
            used_tavily=used_tavily,
            missing_documents=missing_documents,
            days_to_deadline=days_to_deadline,
            show_lead_form=final_show_lead_form,
            lead_form_prefill=final_lead_form_prefill
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
        
        # Determine session key for in-memory storage
        session_key = None
        if request.chat_session_id:
            session_key = f"session_{request.chat_session_id}"
        elif request.device_fingerprint:
            session_key = f"device_{request.device_fingerprint}"
        elif user_id:
            session_key = f"user_{user_id}"
        
        # Get conversation history from in-memory store
        messages_history = []
        conversation = None
        
        if is_authenticated:
            # Logged-in user: use DB as primary, but also check in-memory store
            conversation = get_or_create_conversation(
                db, 
                user_id=user_id, 
                chat_session_id=request.chat_session_id
            )
            # Prefer in-memory store if available (more up-to-date), fallback to DB
            if session_key:
                messages_history = get_conversation_history(session_key)
            if not messages_history and conversation:
                messages_history = conversation.messages or []
        else:
            # Anonymous user: use in-memory store as primary source
            if session_key:
                messages_history = get_conversation_history(session_key)
            
            # Fallback: if no in-memory history and chat_session_id provided, try DB
            if not messages_history and request.chat_session_id:
                conversation = get_or_create_conversation(
                    db,
                    chat_session_id=request.chat_session_id,
                    device_fingerprint=request.device_fingerprint
                )
                if conversation:
                    messages_history = conversation.messages or []
                    # Sync DB history to in-memory store
                    if session_key and messages_history:
                        _conversation_history_store[session_key] = messages_history.copy()
        
        # Append current user message to history BEFORE calling agent
        if session_key:
            append_to_conversation_history(session_key, "user", request.message)
            # Refresh messages_history to include the new user message
            messages_history = get_conversation_history(session_key)
        
        # Check if lead exists for this chat_session_id (for anonymous users)
        use_db = False
        if not is_authenticated and request.chat_session_id:
            from app.models import Lead
            lead = db.query(Lead).filter(
                Lead.chat_session_id == request.chat_session_id
            ).first()
            use_db = lead is not None
        
        # Route to appropriate agent (same logic as regular endpoint)
        show_lead_form = False
        if not is_authenticated:
            # SalesAgent for non-authenticated users
            agent = SalesAgent(db)
            result = agent.generate_response(
                user_message=request.message,
                conversation_history=messages_history,
                chat_session_id=request.chat_session_id,
                use_db=use_db
            )
            full_response = result['response']
            used_rag = bool(result.get('rag_context'))
            used_tavily = bool(result.get('tavily_context'))
            show_lead_form = result.get('show_lead_form', False)
            lead_form_prefill = result.get('lead_form_prefill', {})
            
            # DEBUG: Log show_lead_form in streaming endpoint
            print(f"\n{'='*80}")
            print(f"DEBUG: chat_stream - show_lead_form = {show_lead_form}")
            print(f"DEBUG: lead_form_prefill = {lead_form_prefill}")
            print(f"DEBUG: result keys = {list(result.keys())}")
            print(f"DEBUG: result['show_lead_form'] = {result.get('show_lead_form')}")
            print(f"{'='*80}\n")
            
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
        
        # Append assistant response to in-memory history
        if session_key:
            append_to_conversation_history(session_key, "assistant", full_response)
        
        # Update conversation in DB (only if conversation exists or should be created)
        if conversation:
            update_conversation_messages(db, conversation, request.message, full_response)
        elif not is_authenticated and request.chat_session_id:
            conversation = get_or_create_conversation(
                db,
                chat_session_id=request.chat_session_id,
                device_fingerprint=request.device_fingerprint
            )
            if conversation:
                update_conversation_messages(db, conversation, request.message, full_response)
        
        # Stream the response
        async def generate_stream():
            # Split response into chunks for streaming effect
            words = full_response.split(' ')
            for i, word in enumerate(words):
                chunk = word + (' ' if i < len(words) - 1 else '')
                yield f"data: {json.dumps({'content': chunk, 'done': False})}\n\n"
            lead_form_prefill = result.get('lead_form_prefill', {}) if 'lead_form_prefill' in result else {}
            yield f"data: {json.dumps({'content': '', 'done': True, 'used_rag': used_rag, 'used_tavily': used_tavily, 'show_lead_form': show_lead_form, 'lead_form_prefill': lead_form_prefill})}\n\n"
        
        return StreamingResponse(generate_stream(), media_type="text/event-stream")
    
    except Exception as e:
        error_message = str(e)
        import traceback
        error_traceback = traceback.format_exc()
        print(f"Error in chat_stream: {error_message}\n{error_traceback}")
        
        async def error_stream():
            yield f"data: {json.dumps({'error': error_message})}\n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")

