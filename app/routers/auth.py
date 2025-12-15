from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from jose import JWTError, jwt
from datetime import datetime, timedelta
from typing import Optional
import bcrypt
from app.database import get_db
from app.models import User, UserRole, Lead, Student, Partner
from app.config import settings
from datetime import datetime

router = APIRouter()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

class UserSignup(BaseModel):
    email: EmailStr
    password: str
    name: str
    phone: Optional[str] = None
    country: Optional[str] = None

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str
    user: dict

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password using bcrypt"""
    try:
        return bcrypt.checkpw(
            plain_password.encode('utf-8'),
            hashed_password.encode('utf-8')
        )
    except Exception:
        return False

def get_password_hash(password: str) -> str:
    """Hash password using bcrypt"""
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed.decode('utf-8')

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    # Ensure 'sub' (subject) is a string as required by JWT standard
    if "sub" in to_encode and not isinstance(to_encode["sub"], str):
        to_encode["sub"] = str(to_encode["sub"])
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(hours=24)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return encoded_jwt

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        user_id_str = payload.get("sub")
        if user_id_str is None:
            print(f"JWT Error: No 'sub' claim in token. Payload: {payload}")
            raise credentials_exception
        
        # Check if it's a partner token (format: "partner_{id}")
        if isinstance(user_id_str, str) and user_id_str.startswith("partner_"):
            # This is a partner token, not a user token
            print(f"JWT Error: Partner token detected in get_current_user: {user_id_str}")
            raise credentials_exception
        
        # Convert string user_id to int
        try:
            user_id: int = int(user_id_str)
        except (ValueError, TypeError):
            print(f"JWT Error: Invalid user_id format: {user_id_str}")
            raise credentials_exception
    except JWTError as e:
        print(f"JWT Error: {str(e)}")
        print(f"Token received: {token[:50] if token else 'None'}...")
        print(f"JWT_SECRET_KEY length: {len(settings.JWT_SECRET_KEY)}")
        raise credentials_exception
    except Exception as e:
        print(f"Unexpected error in get_current_user: {str(e)}")
        import traceback
        traceback.print_exc()
        raise credentials_exception
    
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        print(f"User not found for ID: {user_id}")
        raise credentials_exception
    return user

@router.post("/signup", response_model=Token)
async def signup(user_data: UserSignup, db: Session = Depends(get_db)):
    """User signup"""
    # Check if user exists
    existing_user = db.query(User).filter(User.email == user_data.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Create user
    hashed_password = get_password_hash(user_data.password)
    user = User(
        email=user_data.email,
        name=user_data.name,
        phone=user_data.phone,
        country=user_data.country,
        hashed_password=hashed_password,
        role=UserRole.STUDENT
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    
    # Link lead to user if lead exists with same email (convert lead to user)
    lead = db.query(Lead).filter(
        Lead.email == user_data.email,
        Lead.user_id.is_(None)  # Only link unconverted leads
    ).first()
    if lead:
        lead.user_id = user.id
        lead.converted_at = datetime.utcnow()
        db.commit()
    
    # Get default MalishaEdu partner
    default_partner = db.query(Partner).filter(Partner.email == 'malishaedu@gmail.com').first()
    if not default_partner:
        # If default partner doesn't exist, create it
        default_partner = Partner(
            name='MalishaEdu',
            company_name='MalishaEdu',
            email='malishaedu@gmail.com',
            password=get_password_hash('12345678')
        )
        db.add(default_partner)
        db.flush()
    
    # Create Student record automatically for new signups
    # Split name into given_name and family_name (simple split on first space)
    name_parts = user_data.name.strip().split(' ', 1)
    given_name = name_parts[0] if name_parts else ''
    family_name = name_parts[1] if len(name_parts) > 1 else ''
    
    student = Student(
        user_id=user.id,
        partner_id=default_partner.id,  # Assign to default MalishaEdu partner
        given_name=given_name,
        family_name=family_name,
        email=user_data.email,
        phone=user_data.phone,
        country_of_citizenship=user_data.country
    )
    db.add(student)
    db.commit()
    
    # Create token
    access_token = create_access_token(data={"sub": user.id})
    
    return Token(
        access_token=access_token,
        token_type="bearer",
        user={
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "role": user.role.value
        }
    )

@router.post("/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """User login - supports student, admin, and partner"""
    try:
        # First try to find user (student/admin)
        user = db.query(User).filter(User.email == form_data.username).first()
        if user:
            if not verify_password(form_data.password, user.hashed_password):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Incorrect email or password",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            
            access_token = create_access_token(data={"sub": user.id})
            role_value = user.role.value if user.role else "student"
            
            return Token(
                access_token=access_token,
                token_type="bearer",
                user={
                    "id": user.id,
                    "email": user.email,
                    "name": user.name,
                    "role": role_value
                }
            )
        
        # If not found in users, try partners
        partner = db.query(Partner).filter(Partner.email == form_data.username).first()
        if partner:
            if not verify_password(form_data.password, partner.password):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Incorrect email or password",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            
            # Create token with partner identifier (use negative ID to distinguish from users)
            access_token = create_access_token(data={"sub": f"partner_{partner.id}"})
            
            return Token(
                access_token=access_token,
                token_type="bearer",
                user={
                    "id": partner.id,
                    "email": partner.email,
                    "name": partner.name,
                    "role": "partner"
                }
            )
        
        # If neither found
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        print(f"Login error: {str(e)}")
        print(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )

@router.get("/me")
async def get_current_user_info(current_user: User = Depends(get_current_user)):
    """Get current user information"""
    return {
        "id": current_user.id,
        "email": current_user.email,
        "name": current_user.name,
        "phone": current_user.phone,
        "country": current_user.country,
        "role": current_user.role.value
    }

