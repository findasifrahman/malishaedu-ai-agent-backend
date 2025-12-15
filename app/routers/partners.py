from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from typing import Optional
from app.database import get_db
from app.models import Partner, User
from app.routers.auth import get_current_user
import bcrypt

router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

class PartnerCreate(BaseModel):
    name: str
    company_name: Optional[str] = None
    phone1: Optional[str] = None
    phone2: Optional[str] = None
    email: EmailStr
    city: Optional[str] = None
    country: Optional[str] = None
    full_address: Optional[str] = None
    website: Optional[str] = None
    notes: Optional[str] = None
    password: str

class PartnerUpdate(BaseModel):
    name: Optional[str] = None
    company_name: Optional[str] = None
    phone1: Optional[str] = None
    phone2: Optional[str] = None
    email: Optional[EmailStr] = None
    city: Optional[str] = None
    country: Optional[str] = None
    full_address: Optional[str] = None
    website: Optional[str] = None
    notes: Optional[str] = None
    password: Optional[str] = None

class PartnerResponse(BaseModel):
    id: int
    name: str
    company_name: Optional[str]
    phone1: Optional[str]
    phone2: Optional[str]
    email: str
    city: Optional[str]
    country: Optional[str]
    full_address: Optional[str]
    website: Optional[str]
    notes: Optional[str]
    created_at: str
    updated_at: Optional[str]
    
    class Config:
        from_attributes = True

@router.get("", response_model=list[PartnerResponse])
@router.get("/", response_model=list[PartnerResponse])
async def list_partners(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List all partners (admin only)"""
    if current_user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    partners = db.query(Partner).order_by(Partner.name).all()
    return [
        {
            'id': p.id,
            'name': p.name,
            'company_name': p.company_name,
            'phone1': p.phone1,
            'phone2': p.phone2,
            'email': p.email,
            'city': p.city,
            'country': p.country,
            'full_address': p.full_address,
            'website': p.website,
            'notes': p.notes,
            'created_at': p.created_at.isoformat() if p.created_at else None,
            'updated_at': p.updated_at.isoformat() if p.updated_at else None,
        }
        for p in partners
    ]

@router.get("/{partner_id}", response_model=PartnerResponse)
async def get_partner(
    partner_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get a specific partner (admin only)"""
    if current_user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    partner = db.query(Partner).filter(Partner.id == partner_id).first()
    if not partner:
        raise HTTPException(status_code=404, detail="Partner not found")
    
    return {
        'id': partner.id,
        'name': partner.name,
        'company_name': partner.company_name,
        'phone1': partner.phone1,
        'phone2': partner.phone2,
        'email': partner.email,
        'city': partner.city,
        'country': partner.country,
        'full_address': partner.full_address,
        'website': partner.website,
        'notes': partner.notes,
        'created_at': partner.created_at.isoformat() if partner.created_at else None,
        'updated_at': partner.updated_at.isoformat() if partner.updated_at else None,
    }

@router.post("", response_model=PartnerResponse, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=PartnerResponse, status_code=status.HTTP_201_CREATED)
async def create_partner(
    partner_data: PartnerCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new partner (admin only)"""
    if current_user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Check if email already exists
    existing = db.query(Partner).filter(Partner.email == partner_data.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Hash password
    hashed_password = bcrypt.hashpw(partner_data.password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    
    partner = Partner(
        name=partner_data.name,
        company_name=partner_data.company_name,
        phone1=partner_data.phone1,
        phone2=partner_data.phone2,
        email=partner_data.email,
        city=partner_data.city,
        country=partner_data.country,
        full_address=partner_data.full_address,
        website=partner_data.website,
        notes=partner_data.notes,
        password=hashed_password
    )
    db.add(partner)
    db.commit()
    db.refresh(partner)
    
    return {
        'id': partner.id,
        'name': partner.name,
        'company_name': partner.company_name,
        'phone1': partner.phone1,
        'phone2': partner.phone2,
        'email': partner.email,
        'city': partner.city,
        'country': partner.country,
        'full_address': partner.full_address,
        'website': partner.website,
        'notes': partner.notes,
        'created_at': partner.created_at.isoformat() if partner.created_at else None,
        'updated_at': partner.updated_at.isoformat() if partner.updated_at else None,
    }

@router.put("/{partner_id}", response_model=PartnerResponse)
async def update_partner(
    partner_id: int,
    partner_data: PartnerUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update a partner (admin only)"""
    if current_user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    partner = db.query(Partner).filter(Partner.id == partner_id).first()
    if not partner:
        raise HTTPException(status_code=404, detail="Partner not found")
    
    update_data = partner_data.dict(exclude_unset=True)
    
    # If email is being updated, check for duplicates
    if 'email' in update_data and update_data['email'] != partner.email:
        existing = db.query(Partner).filter(Partner.email == update_data['email']).first()
        if existing:
            raise HTTPException(status_code=400, detail="Email already registered")
    
    # Hash password if provided
    if 'password' in update_data:
        update_data['password'] = bcrypt.hashpw(update_data['password'].encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    
    for field, value in update_data.items():
        setattr(partner, field, value)
    
    db.commit()
    db.refresh(partner)
    
    return {
        'id': partner.id,
        'name': partner.name,
        'company_name': partner.company_name,
        'phone1': partner.phone1,
        'phone2': partner.phone2,
        'email': partner.email,
        'city': partner.city,
        'country': partner.country,
        'full_address': partner.full_address,
        'website': partner.website,
        'notes': partner.notes,
        'created_at': partner.created_at.isoformat() if partner.created_at else None,
        'updated_at': partner.updated_at.isoformat() if partner.updated_at else None,
    }

@router.delete("/{partner_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_partner(
    partner_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a partner (admin only)"""
    if current_user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    partner = db.query(Partner).filter(Partner.id == partner_id).first()
    if not partner:
        raise HTTPException(status_code=404, detail="Partner not found")
    
    # Check if partner has students
    if partner.students:
        raise HTTPException(status_code=400, detail="Cannot delete partner with associated students. Please reassign students first.")
    
    db.delete(partner)
    db.commit()
    return None

# Partner-specific endpoints (requires partner authentication)
def get_current_partner(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    """Get current partner from JWT token"""
    from app.routers.auth import get_current_user
    from jose import JWTError, jwt
    from app.config import settings
    
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        sub = payload.get("sub")
        if sub is None:
            raise credentials_exception
        
        # Check if it's a partner token (format: "partner_{id}")
        if isinstance(sub, str) and sub.startswith("partner_"):
            partner_id = int(sub.replace("partner_", ""))
            partner = db.query(Partner).filter(Partner.id == partner_id).first()
            if partner is None:
                raise credentials_exception
            return partner
        else:
            raise credentials_exception
    except (JWTError, ValueError, TypeError):
        raise credentials_exception

@router.get("/me/stats")
async def get_partner_stats(
    current_partner: Partner = Depends(get_current_partner),
    db: Session = Depends(get_db)
):
    """Get partner statistics"""
    from app.models import Student, Application
    
    total_students = db.query(Student).filter(Student.partner_id == current_partner.id).count()
    active_applications = db.query(Application).join(Student).filter(
        Student.partner_id == current_partner.id,
        Application.status.in_(['draft', 'submitted', 'under_review'])
    ).count()
    
    from datetime import datetime, timedelta
    recent_students = db.query(Student).filter(
        Student.partner_id == current_partner.id,
        Student.created_at >= datetime.utcnow() - timedelta(days=7)
    ).count()
    
    return {
        'total_students': total_students,
        'active_applications': active_applications,
        'recent_students': recent_students
    }

@router.get("/me/students")
async def list_partner_students(
    page: int = 1,
    page_size: int = 20,
    search: Optional[str] = None,
    current_partner: Partner = Depends(get_current_partner),
    db: Session = Depends(get_db)
):
    """List students for current partner with pagination and search"""
    from app.models import Student
    from sqlalchemy import or_
    
    query = db.query(Student).filter(Student.partner_id == current_partner.id)
    
    # Search functionality
    if search:
        search_filter = or_(
            Student.given_name.ilike(f'%{search}%'),
            Student.family_name.ilike(f'%{search}%'),
            Student.email.ilike(f'%{search}%'),
            Student.phone.ilike(f'%{search}%'),
            Student.passport_number.ilike(f'%{search}%'),
            Student.country_of_citizenship.ilike(f'%{search}%')
        )
        query = query.filter(search_filter)
    
    total = query.count()
    students = query.order_by(Student.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    
    return {
        'items': [
            {
                'id': s.id,
                'full_name': f"{s.given_name or ''} {s.family_name or ''}".strip() or None,
                'given_name': s.given_name,
                'family_name': s.family_name,
                'email': s.email,
                'phone': s.phone,
                'country_of_citizenship': s.country_of_citizenship,
                'passport_number': s.passport_number,
                'created_at': s.created_at.isoformat() if s.created_at else None
            }
            for s in students
        ],
        'total': total,
        'page': page,
        'page_size': page_size,
        'total_pages': (total + page_size - 1) // page_size
    }

@router.post("/me/students", status_code=status.HTTP_201_CREATED)
async def create_partner_student(
    student_data: dict,
    current_partner: Partner = Depends(get_current_partner),
    db: Session = Depends(get_db)
):
    """Create a new student for the current partner"""
    from app.models import User, Student, UserRole
    from app.routers.auth import get_password_hash
    
    # Check if email already exists
    existing_user = db.query(User).filter(User.email == student_data['email']).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Create user
    hashed_password = get_password_hash(student_data['password'])
    user = User(
        email=student_data['email'],
        name=student_data.get('name', ''),
        phone=student_data.get('phone'),
        country=student_data.get('country'),
        hashed_password=hashed_password,
        role=UserRole.STUDENT
    )
    db.add(user)
    db.flush()
    
    # Create student linked to partner
    # Split name into given_name and family_name
    name = student_data.get('name', '')
    name_parts = name.strip().split(' ', 1)
    given_name = name_parts[0] if name_parts else ''
    family_name = name_parts[1] if len(name_parts) > 1 else ''
    
    student = Student(
        user_id=user.id,
        partner_id=current_partner.id,
        given_name=given_name,
        family_name=family_name,
        email=student_data['email'],
        phone=student_data.get('phone'),
        country_of_citizenship=student_data.get('country')
    )
    db.add(student)
    db.commit()
    db.refresh(student)
    
    return {
        'id': student.id,
        'full_name': f"{student.given_name or ''} {student.family_name or ''}".strip() or None,
        'given_name': student.given_name,
        'family_name': student.family_name,
        'email': student.email,
        'phone': student.phone,
        'country_of_citizenship': student.country_of_citizenship
    }

@router.delete("/me/students/{student_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_partner_student(
    student_id: int,
    current_partner: Partner = Depends(get_current_partner),
    db: Session = Depends(get_db)
):
    """Delete a student (only if owned by current partner)"""
    from app.models import Student, User
    
    student = db.query(Student).filter(
        Student.id == student_id,
        Student.partner_id == current_partner.id
    ).first()
    
    if not student:
        raise HTTPException(status_code=404, detail="Student not found or access denied")
    
    # Delete user if exists
    if student.user_id:
        user = db.query(User).filter(User.id == student.user_id).first()
        if user:
            db.delete(user)
    
    db.delete(student)
    db.commit()
    return None

@router.get("/me/students/{student_id}/profile")
async def get_partner_student_profile(
    student_id: int,
    current_partner: Partner = Depends(get_current_partner),
    db: Session = Depends(get_db)
):
    """Get student profile (only if owned by current partner)"""
    from app.models import Student
    
    student = db.query(Student).filter(
        Student.id == student_id,
        Student.partner_id == current_partner.id
    ).first()
    
    if not student:
        raise HTTPException(status_code=404, detail="Student not found or access denied")
    
    # Return student profile data (similar to admin endpoint)
    from datetime import datetime
    student_dict = {}
    for column in Student.__table__.columns:
        value = getattr(student, column.name)
        if isinstance(value, datetime):
            student_dict[column.name] = value.isoformat()
        elif hasattr(value, 'value'):  # Enum
            student_dict[column.name] = value.value
        else:
            student_dict[column.name] = value
    
    return student_dict

@router.put("/me/students/{student_id}/profile")
async def update_partner_student_profile(
    student_id: int,
    profile_data: dict,
    current_partner: Partner = Depends(get_current_partner),
    db: Session = Depends(get_db)
):
    """Update student profile (only if owned by current partner)"""
    from app.models import Student
    
    student = db.query(Student).filter(
        Student.id == student_id,
        Student.partner_id == current_partner.id
    ).first()
    
    if not student:
        raise HTTPException(status_code=404, detail="Student not found or access denied")
    
    # Update student fields (similar to admin endpoint)
    from app.routers.students import StudentProfile
    from datetime import datetime, date
    
    # Convert dict to StudentProfile for validation
    try:
        if isinstance(profile_data, dict):
            profile = StudentProfile(**profile_data)
        else:
            profile = profile_data
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid profile data: {str(e)}")
    
    # Update all fields from profile
    for field, value in profile.dict(exclude_unset=True).items():
        if hasattr(student, field):
            # Handle date strings
            if isinstance(value, str) and (field.endswith('_date') or field == 'date_of_birth'):
                try:
                    value = datetime.fromisoformat(value.replace('Z', '+00:00')).date() if value else None
                except:
                    try:
                        value = date.fromisoformat(value) if value else None
                    except:
                        pass
            setattr(student, field, value)
    
    db.commit()
    db.refresh(student)
    
    # Return student profile data
    student_dict = {}
    for column in Student.__table__.columns:
        value = getattr(student, column.name)
        if isinstance(value, (datetime, date)):
            student_dict[column.name] = value.isoformat()
        elif hasattr(value, 'value'):  # Enum
            student_dict[column.name] = value.value
        else:
            student_dict[column.name] = value
    
    return student_dict

@router.get("/me/students/{student_id}/applications")
async def get_partner_student_applications(
    student_id: int,
    current_partner: Partner = Depends(get_current_partner),
    db: Session = Depends(get_db)
):
    """Get student applications (only if owned by current partner)"""
    from app.models import Student, Application
    
    student = db.query(Student).filter(
        Student.id == student_id,
        Student.partner_id == current_partner.id
    ).first()
    
    if not student:
        raise HTTPException(status_code=404, detail="Student not found or access denied")
    
    applications = db.query(Application).filter(Application.student_id == student_id).all()
    
    return [
        {
            'id': app.id,
            'university_id': app.university_id,
            'major_id': app.major_id,
            'program_intake_id': app.program_intake_id,
            'scholarship_preference': app.scholarship_preference,
            'status': app.status,
            'degree_level': app.degree_level,
            'created_at': app.created_at.isoformat() if app.created_at else None,
            'updated_at': app.updated_at.isoformat() if app.updated_at else None
        }
        for app in applications
    ]

@router.get("/me/students/{student_id}/documents")
async def get_partner_student_documents(
    student_id: int,
    current_partner: Partner = Depends(get_current_partner),
    db: Session = Depends(get_db)
):
    """Get student documents (only if owned by current partner)"""
    from app.models import Student, StudentDocument
    
    student = db.query(Student).filter(
        Student.id == student_id,
        Student.partner_id == current_partner.id
    ).first()
    
    if not student:
        raise HTTPException(status_code=404, detail="Student not found or access denied")
    
    documents = db.query(StudentDocument).filter(StudentDocument.student_id == student_id).all()
    
    return [
        {
            'id': doc.id,
            'document_type': doc.document_type,
            'file_path': doc.file_path,
            'is_verified': doc.is_verified,
            'verification_notes': doc.verification_notes,
            'uploaded_at': doc.uploaded_at.isoformat() if doc.uploaded_at else None
        }
        for doc in documents
    ]

@router.post("/me/students/{student_id}/documents/verify-and-upload")
async def upload_partner_student_document(
    student_id: int,
    file: UploadFile = File(...),
    document_type: str = Form(...),
    current_partner: Partner = Depends(get_current_partner),
    db: Session = Depends(get_db)
):
    """Upload and verify student document (only if owned by current partner)"""
    from app.models import Student, StudentDocument, DocumentType
    from app.services.document_verification_service import DocumentVerificationService
    from app.services.r2_service import R2Service
    from app.routers.document_verification import validate_passport_photo
    import base64
    import io
    
    student = db.query(Student).filter(
        Student.id == student_id,
        Student.partner_id == current_partner.id
    ).first()
    
    if not student:
        raise HTTPException(status_code=404, detail="Student not found or access denied")
    
    # Read file content
    file_content = await file.read()
    
    # Validate passport photo if applicable
    if document_type == 'passport_photo':
        is_valid, error_msg = validate_passport_photo(file_content, file.filename)
        if not is_valid:
            raise HTTPException(status_code=400, detail=error_msg)
    
    # Verify document using Vision API
    verification_service = DocumentVerificationService()
    r2_service = R2Service()
    
    # Convert to base64 for Vision API
    file_base64 = base64.b64encode(file_content).decode('utf-8')
    
    verification_result = await verification_service.verify_document(
        document_type=document_type,
        file_base64=file_base64,
        filename=file.filename
    )
    
    if verification_result['status'] != 'ok':
        raise HTTPException(
            status_code=400,
            detail=f"Document verification failed: {verification_result.get('message', 'Unknown error')}"
        )
    
    # Upload to R2
    r2_path = f"verified/students/{student_id}/{document_type}/{file.filename}"
    r2_url = await r2_service.upload_file(file_content, r2_path, file.content_type)
    
    # Save to database
    doc = StudentDocument(
        student_id=student_id,
        document_type=DocumentType(document_type),
        file_path=r2_url,
        is_verified=True,
        verification_notes=verification_result.get('message', 'Verified successfully')
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    
    return {
        'id': doc.id,
        'document_type': doc.document_type.value,
        'file_path': doc.file_path,
        'is_verified': doc.is_verified,
        'verification_notes': doc.verification_notes
    }

@router.delete("/me/students/{student_id}/documents/{document_id}")
async def delete_partner_student_document(
    student_id: int,
    document_id: int,
    current_partner: Partner = Depends(get_current_partner),
    db: Session = Depends(get_db)
):
    """Delete student document (only if owned by current partner)"""
    from app.models import Student, StudentDocument
    
    student = db.query(Student).filter(
        Student.id == student_id,
        Student.partner_id == current_partner.id
    ).first()
    
    if not student:
        raise HTTPException(status_code=404, detail="Student not found or access denied")
    
    document = db.query(StudentDocument).filter(
        StudentDocument.id == document_id,
        StudentDocument.student_id == student_id
    ).first()
    
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    db.delete(document)
    db.commit()
    return {"message": "Document deleted successfully"}

@router.get("/me/students/{student_id}/password")
async def get_partner_student_password_info(
    student_id: int,
    current_partner: Partner = Depends(get_current_partner),
    db: Session = Depends(get_db)
):
    """Get student password info (only if owned by current partner)"""
    from app.models import Student, User
    
    student = db.query(Student).filter(
        Student.id == student_id,
        Student.partner_id == current_partner.id
    ).first()
    
    if not student:
        raise HTTPException(status_code=404, detail="Student not found or access denied")
    
    if not student.user_id:
        return {
            'student_id': student_id,
            'email': student.email,
            'has_password': False,
            'note': 'No user account linked'
        }
    
    user = db.query(User).filter(User.id == student.user_id).first()
    if not user:
        return {
            'student_id': student_id,
            'email': student.email,
            'has_password': False,
            'note': 'User account not found'
        }
    
    return {
        'student_id': student_id,
        'email': user.email,
        'has_password': bool(user.hashed_password),
        'note': 'Password is hashed and cannot be retrieved. Use set_password endpoint to set a new password.'
    }

@router.post("/me/students/{student_id}/set-password")
async def set_partner_student_password(
    student_id: int,
    password_data: dict,
    current_partner: Partner = Depends(get_current_partner),
    db: Session = Depends(get_db)
):
    """Set student password (only if owned by current partner)"""
    from app.models import Student, User
    from app.routers.auth import get_password_hash
    
    student = db.query(Student).filter(
        Student.id == student_id,
        Student.partner_id == current_partner.id
    ).first()
    
    if not student:
        raise HTTPException(status_code=404, detail="Student not found or access denied")
    
    if not student.user_id:
        raise HTTPException(status_code=400, detail="Student has no user account")
    
    user = db.query(User).filter(User.id == student.user_id).first()
    if not user:
        raise HTTPException(status_code=400, detail="User account not found")
    
    user.hashed_password = get_password_hash(password_data['password'])
    db.commit()
    
    return {"message": "Password updated successfully"}

@router.get("/me/conversations")
async def list_partner_conversations(
    current_partner: Partner = Depends(get_current_partner),
    db: Session = Depends(get_db)
):
    """List conversations for students of current partner"""
    from app.models import Conversation, Student, User
    
    # Get all student user IDs for this partner
    partner_students = db.query(Student).filter(Student.partner_id == current_partner.id).all()
    student_user_ids = [s.user_id for s in partner_students if s.user_id]
    
    if not student_user_ids:
        return []
    
    conversations = db.query(Conversation).filter(
        Conversation.user_id.in_(student_user_ids)
    ).order_by(Conversation.updated_at.desc()).all()
    
    # Get student info for each conversation
    result = []
    for conv in conversations:
        student = db.query(Student).filter(Student.user_id == conv.user_id).first()
        result.append({
            'id': conv.id,
            'student': {
                'id': student.id if student else None,
                'full_name': f"{student.given_name or ''} {student.family_name or ''}".strip() if student else None,
                'email': student.email if student else None
            } if student else None,
            'message_count': len(conv.messages) if conv.messages else 0,
            'messages': conv.messages[-12:] if conv.messages else [],  # Last 12 messages
            'updated_at': conv.updated_at.isoformat() if conv.updated_at else None
        })
    
    return result
