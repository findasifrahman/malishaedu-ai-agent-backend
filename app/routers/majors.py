from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from app.database import get_db
from app.models import Major, University, User
from app.routers.auth import get_current_user

router = APIRouter()

class MajorCreate(BaseModel):
    university_id: int
    name: str
    name_cn: Optional[str] = None
    degree_level: str  # Changed from DegreeLevel enum to str
    teaching_language: str  # Changed from TeachingLanguage enum to str
    duration_years: Optional[float] = None
    description: Optional[str] = None
    discipline: Optional[str] = None
    category: Optional[str] = None  # "Non-degree/Language Program" vs "Degree Program"
    keywords: Optional[List[str]] = None  # Array of keywords for matching
    is_featured: bool = False
    is_active: bool = True

class MajorUpdate(BaseModel):
    name: Optional[str] = None
    name_cn: Optional[str] = None
    degree_level: Optional[str] = None  # Changed from DegreeLevel enum to str
    teaching_language: Optional[str] = None  # Changed from TeachingLanguage enum to str
    duration_years: Optional[float] = None
    description: Optional[str] = None
    discipline: Optional[str] = None
    category: Optional[str] = None
    keywords: Optional[List[str]] = None
    is_featured: Optional[bool] = None
    is_active: Optional[bool] = None

class MajorResponse(BaseModel):
    id: int
    university_id: int
    name: str
    name_cn: Optional[str]
    degree_level: str
    teaching_language: str
    duration_years: Optional[float]
    description: Optional[str]
    discipline: Optional[str]
    category: Optional[str]
    keywords: Optional[List[str]]
    is_featured: bool
    is_active: bool
    created_at: str
    updated_at: Optional[str]
    
    class Config:
        from_attributes = True

async def _list_majors(
    university_id: Optional[int] = None,
    degree_level: Optional[str] = None,  # Changed from DegreeLevel enum to str
    teaching_language: Optional[str] = None,  # Changed from TeachingLanguage enum to str
    is_featured: Optional[bool] = None,
    is_active: Optional[bool] = None,
    search: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db)
):
    """List majors with optional filters, search, and pagination"""
    from sqlalchemy import func, or_, cast, String
    
    query = db.query(Major).join(University)  # Join with University to allow searching by university name
    
    if university_id:
        query = query.filter(Major.university_id == university_id)
    if degree_level:
        query = query.filter(Major.degree_level == degree_level)
    if teaching_language:
        query = query.filter(Major.teaching_language == teaching_language)
    if is_featured is not None:
        query = query.filter(Major.is_featured == is_featured)
    if is_active is not None:
        query = query.filter(Major.is_active == is_active)
    
    # Search functionality - includes keywords search
    if search:
        search_term = f"%{search}%"
        # Search in keywords JSON array - cast JSONB to text and search
        # For PostgreSQL JSONB, casting to text allows searching within the array
        keywords_search = cast(Major.keywords, String).ilike(search_term)
        search_filter = or_(
            Major.name.ilike(search_term),
            Major.name_cn.ilike(search_term),
            Major.description.ilike(search_term),
            Major.discipline.ilike(search_term),
            University.name.ilike(search_term),
            keywords_search
        )
        query = query.filter(search_filter)
    
    # Get total count before pagination
    total = query.count()
    
    # Apply pagination
    offset = (page - 1) * page_size
    majors = query.order_by(Major.name).offset(offset).limit(page_size).all()
    
    result = []
    for major in majors:
        result.append({
            'id': major.id,
            'university_id': major.university_id,
            'university_name': major.university.name if major.university else None,
            'name': major.name,
            'name_cn': major.name_cn,
            'degree_level': major.degree_level.value if hasattr(major.degree_level, 'value') else str(major.degree_level),
            'teaching_language': major.teaching_language.value if hasattr(major.teaching_language, 'value') else str(major.teaching_language),
            'duration_years': major.duration_years,
            'description': major.description,
            'discipline': major.discipline,
            'category': major.category,
            'keywords': major.keywords,
            'is_featured': major.is_featured,
            'is_active': major.is_active,
            'created_at': major.created_at.isoformat() if major.created_at else None,
            'updated_at': major.updated_at.isoformat() if major.updated_at else None,
        })
    
    return {
        'items': result,
        'total': total,
        'page': page,
        'page_size': page_size,
        'total_pages': (total + page_size - 1) // page_size
    }

@router.get("")
async def list_majors(
    university_id: Optional[int] = None,
    degree_level: Optional[str] = None,
    teaching_language: Optional[str] = None,
    is_featured: Optional[bool] = None,
    is_active: Optional[bool] = None,
    search: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db)
):
    return await _list_majors(university_id=university_id, degree_level=degree_level, teaching_language=teaching_language, is_featured=is_featured, is_active=is_active, search=search, page=page, page_size=page_size, db=db)

@router.get("/")
async def list_majors_with_slash(
    university_id: Optional[int] = None,
    degree_level: Optional[str] = None,
    teaching_language: Optional[str] = None,
    is_featured: Optional[bool] = None,
    is_active: Optional[bool] = None,
    search: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db)
):
    return await _list_majors(university_id=university_id, degree_level=degree_level, teaching_language=teaching_language, is_featured=is_featured, is_active=is_active, search=search, page=page, page_size=page_size, db=db)

@router.get("/{major_id}", response_model=MajorResponse)
async def get_major(major_id: int, db: Session = Depends(get_db)):
    """Get a specific major by ID"""
    major = db.query(Major).filter(Major.id == major_id).first()
    if not major:
        raise HTTPException(status_code=404, detail="Major not found")
    return {
        'id': major.id,
        'university_id': major.university_id,
        'name': major.name,
        'name_cn': major.name_cn,
        'degree_level': major.degree_level.value if hasattr(major.degree_level, 'value') else str(major.degree_level),
        'teaching_language': major.teaching_language.value if hasattr(major.teaching_language, 'value') else str(major.teaching_language),
        'duration_years': major.duration_years,
        'description': major.description,
        'discipline': major.discipline,
        'category': major.category,
        'keywords': major.keywords,
        'is_featured': major.is_featured,
        'is_active': major.is_active,
        'created_at': major.created_at.isoformat() if major.created_at else None,
        'updated_at': major.updated_at.isoformat() if major.updated_at else None,
    }

async def _create_major(
    major_data: MajorCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new major (admin only)"""
    if current_user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Verify university exists
    university = db.query(University).filter(University.id == major_data.university_id).first()
    if not university:
        raise HTTPException(status_code=404, detail="University not found")
    
    major = Major(**major_data.dict())
    db.add(major)
    db.commit()
    db.refresh(major)
    return {
        'id': major.id,
        'university_id': major.university_id,
        'name': major.name,
        'name_cn': major.name_cn,
        'degree_level': major.degree_level.value if hasattr(major.degree_level, 'value') else str(major.degree_level),
        'teaching_language': major.teaching_language.value if hasattr(major.teaching_language, 'value') else str(major.teaching_language),
        'duration_years': major.duration_years,
        'description': major.description,
        'discipline': major.discipline,
        'category': major.category,
        'keywords': major.keywords,
        'is_featured': major.is_featured,
        'is_active': major.is_active,
        'created_at': major.created_at.isoformat() if major.created_at else None,
        'updated_at': major.updated_at.isoformat() if major.updated_at else None,
    }

@router.post("", response_model=MajorResponse, status_code=status.HTTP_201_CREATED)
async def create_major(
    major_data: MajorCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return await _create_major(major_data=major_data, current_user=current_user, db=db)

@router.post("/", response_model=MajorResponse, status_code=status.HTTP_201_CREATED)
async def create_major_with_slash(
    major_data: MajorCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return await _create_major(major_data=major_data, current_user=current_user, db=db)

@router.put("/{major_id}", response_model=MajorResponse)
async def update_major(
    major_id: int,
    major_data: MajorUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update a major (admin only)"""
    if current_user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    major = db.query(Major).filter(Major.id == major_id).first()
    if not major:
        raise HTTPException(status_code=404, detail="Major not found")
    
    update_data = major_data.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(major, field, value)
    
    db.commit()
    db.refresh(major)
    return {
        'id': major.id,
        'university_id': major.university_id,
        'name': major.name,
        'name_cn': major.name_cn,
        'degree_level': major.degree_level.value if hasattr(major.degree_level, 'value') else str(major.degree_level),
        'teaching_language': major.teaching_language.value if hasattr(major.teaching_language, 'value') else str(major.teaching_language),
        'duration_years': major.duration_years,
        'description': major.description,
        'discipline': major.discipline,
        'category': major.category,
        'keywords': major.keywords,
        'is_featured': major.is_featured,
        'is_active': major.is_active,
        'created_at': major.created_at.isoformat() if major.created_at else None,
        'updated_at': major.updated_at.isoformat() if major.updated_at else None,
    }

@router.delete("/{major_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_major(
    major_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a major (admin only)"""
    if current_user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    major = db.query(Major).filter(Major.id == major_id).first()
    if not major:
        raise HTTPException(status_code=404, detail="Major not found")
    
    db.delete(major)
    db.commit()
    return None

