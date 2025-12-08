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
    degree_level: str  # Changed from DegreeLevel enum to str
    teaching_language: str  # Changed from TeachingLanguage enum to str
    duration_years: Optional[float] = None
    description: Optional[str] = None
    discipline: Optional[str] = None
    is_featured: bool = False

class MajorUpdate(BaseModel):
    name: Optional[str] = None
    degree_level: Optional[str] = None  # Changed from DegreeLevel enum to str
    teaching_language: Optional[str] = None  # Changed from TeachingLanguage enum to str
    duration_years: Optional[float] = None
    description: Optional[str] = None
    discipline: Optional[str] = None
    is_featured: Optional[bool] = None

class MajorResponse(BaseModel):
    id: int
    university_id: int
    name: str
    degree_level: str
    teaching_language: str
    duration_years: Optional[float]
    description: Optional[str]
    discipline: Optional[str]
    is_featured: bool
    created_at: str
    updated_at: Optional[str]
    
    class Config:
        from_attributes = True

async def _list_majors(
    university_id: Optional[int] = None,
    degree_level: Optional[str] = None,  # Changed from DegreeLevel enum to str
    teaching_language: Optional[str] = None,  # Changed from TeachingLanguage enum to str
    is_featured: Optional[bool] = None,
    db: Session = Depends(get_db)
):
    """List all majors with optional filters"""
    query = db.query(Major)
    
    if university_id:
        query = query.filter(Major.university_id == university_id)
    if degree_level:
        query = query.filter(Major.degree_level == degree_level)
    if teaching_language:
        query = query.filter(Major.teaching_language == teaching_language)
    if is_featured is not None:
        query = query.filter(Major.is_featured == is_featured)
    
    majors = query.order_by(Major.name).all()
    result = []
    for major in majors:
        result.append({
            'id': major.id,
            'university_id': major.university_id,
            'name': major.name,
            'degree_level': major.degree_level.value if hasattr(major.degree_level, 'value') else str(major.degree_level),
            'teaching_language': major.teaching_language.value if hasattr(major.teaching_language, 'value') else str(major.teaching_language),
            'duration_years': major.duration_years,
            'description': major.description,
            'discipline': major.discipline,
            'is_featured': major.is_featured,
            'created_at': major.created_at.isoformat() if major.created_at else None,
            'updated_at': major.updated_at.isoformat() if major.updated_at else None,
        })
    return result

@router.get("", response_model=List[MajorResponse])
async def list_majors(
    university_id: Optional[int] = None,
    degree_level: Optional[str] = None,
    teaching_language: Optional[str] = None,
    is_featured: Optional[bool] = None,
    db: Session = Depends(get_db)
):
    return await _list_majors(university_id=university_id, degree_level=degree_level, teaching_language=teaching_language, is_featured=is_featured, db=db)

@router.get("/", response_model=List[MajorResponse])
async def list_majors_with_slash(
    university_id: Optional[int] = None,
    degree_level: Optional[str] = None,
    teaching_language: Optional[str] = None,
    is_featured: Optional[bool] = None,
    db: Session = Depends(get_db)
):
    return await _list_majors(university_id=university_id, degree_level=degree_level, teaching_language=teaching_language, is_featured=is_featured, db=db)

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
        'degree_level': major.degree_level.value if hasattr(major.degree_level, 'value') else str(major.degree_level),
        'teaching_language': major.teaching_language.value if hasattr(major.teaching_language, 'value') else str(major.teaching_language),
        'duration_years': major.duration_years,
        'description': major.description,
        'discipline': major.discipline,
        'is_featured': major.is_featured,
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
        'degree_level': major.degree_level.value if hasattr(major.degree_level, 'value') else str(major.degree_level),
        'teaching_language': major.teaching_language.value if hasattr(major.teaching_language, 'value') else str(major.teaching_language),
        'duration_years': major.duration_years,
        'description': major.description,
        'discipline': major.discipline,
        'is_featured': major.is_featured,
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
        'degree_level': major.degree_level.value if hasattr(major.degree_level, 'value') else str(major.degree_level),
        'teaching_language': major.teaching_language.value if hasattr(major.teaching_language, 'value') else str(major.teaching_language),
        'duration_years': major.duration_years,
        'description': major.description,
        'discipline': major.discipline,
        'is_featured': major.is_featured,
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

