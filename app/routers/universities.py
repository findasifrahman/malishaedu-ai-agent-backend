from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional, List, Union
from datetime import datetime
from app.database import get_db
from app.models import University, User
from app.routers.auth import get_current_user

router = APIRouter()

class UniversityCreate(BaseModel):
    name: str
    city: Optional[str] = None
    province: Optional[str] = None
    country: str = "China"
    is_partner: bool = True
    university_ranking: Optional[int] = None
    logo_url: Optional[str] = None
    description: Optional[str] = None
    website: Optional[str] = None
    contact_email: Optional[str] = None  # Changed from EmailStr to str to allow empty strings
    contact_wechat: Optional[str] = None
    
    @field_validator('contact_email', mode='before')
    @classmethod
    def validate_email(cls, v):
        if v is None or v == '' or v == 'null' or (isinstance(v, str) and v.strip() == ''):
            return None
        return v.strip() if isinstance(v, str) else v
    
    @field_validator('logo_url', 'website', 'description', 'contact_wechat', 'city', 'province', mode='before')
    @classmethod
    def validate_optional_strings(cls, v):
        if v is None or v == '' or v == 'null' or (isinstance(v, str) and v.strip() == ''):
            return None
        return v.strip() if isinstance(v, str) else v

class UniversityUpdate(BaseModel):
    name: Optional[str] = None
    city: Optional[str] = None
    province: Optional[str] = None
    country: Optional[str] = None
    is_partner: Optional[bool] = None
    university_ranking: Optional[int] = None
    logo_url: Optional[str] = None
    description: Optional[str] = None
    website: Optional[str] = None
    contact_email: Optional[EmailStr] = None
    contact_wechat: Optional[str] = None

class UniversityResponse(BaseModel):
    id: int
    name: str
    city: Optional[str]
    province: Optional[str]
    country: str
    is_partner: bool
    university_ranking: Optional[int]
    logo_url: Optional[str]
    description: Optional[str]
    website: Optional[str]
    contact_email: Optional[str]
    contact_wechat: Optional[str]
    created_at: str
    updated_at: Optional[str]
    
    class Config:
        from_attributes = True

async def _list_universities(
    is_partner: Optional[bool] = None,
    city: Optional[str] = None,
    province: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """List all universities with optional filters"""
    query = db.query(University)
    
    if is_partner is not None:
        query = query.filter(University.is_partner == is_partner)
    if city:
        query = query.filter(University.city.ilike(f"%{city}%"))
    if province:
        query = query.filter(University.province.ilike(f"%{province}%"))
    
    universities = query.order_by(University.name).all()
    # Convert datetime fields to ISO strings
    result = []
    for uni in universities:
        uni_dict = {
            'id': uni.id,
            'name': uni.name,
            'city': uni.city,
            'province': uni.province,
            'country': uni.country,
            'is_partner': uni.is_partner,
            'university_ranking': uni.university_ranking,
            'logo_url': uni.logo_url,
            'description': uni.description,
            'website': uni.website,
            'contact_email': uni.contact_email,
            'contact_wechat': uni.contact_wechat,
            'created_at': uni.created_at.isoformat() if uni.created_at else None,
            'updated_at': uni.updated_at.isoformat() if uni.updated_at else None,
        }
        result.append(uni_dict)
    return result

@router.get("", response_model=List[UniversityResponse])
async def list_universities(*args, **kwargs):
    return await _list_universities(*args, **kwargs)

@router.get("/", response_model=List[UniversityResponse])
async def list_universities_with_slash(*args, **kwargs):
    return await _list_universities(*args, **kwargs)

@router.get("/{university_id}", response_model=UniversityResponse)
async def get_university(university_id: int, db: Session = Depends(get_db)):
    """Get a specific university by ID"""
    university = db.query(University).filter(University.id == university_id).first()
    if not university:
        raise HTTPException(status_code=404, detail="University not found")
    return {
        'id': university.id,
        'name': university.name,
        'city': university.city,
        'province': university.province,
        'country': university.country,
        'is_partner': university.is_partner,
        'university_ranking': university.university_ranking,
        'logo_url': university.logo_url,
        'description': university.description,
        'website': university.website,
        'contact_email': university.contact_email,
        'contact_wechat': university.contact_wechat,
        'created_at': university.created_at.isoformat() if university.created_at else None,
        'updated_at': university.updated_at.isoformat() if university.updated_at else None,
    }

async def _create_university(
    university_data: UniversityCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new university (admin only)"""
    if current_user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Convert empty strings to None for optional fields
    data = university_data.model_dump()
    for key in ['logo_url', 'website', 'description', 'contact_email', 'contact_wechat', 'city', 'province']:
        if key in data and (data[key] == '' or data[key] is None):
            data[key] = None
    
    university = University(**data)
    db.add(university)
    db.commit()
    db.refresh(university)
    return {
        'id': university.id,
        'name': university.name,
        'city': university.city,
        'province': university.province,
        'country': university.country,
        'is_partner': university.is_partner,
        'university_ranking': university.university_ranking,
        'logo_url': university.logo_url,
        'description': university.description,
        'website': university.website,
        'contact_email': university.contact_email,
        'contact_wechat': university.contact_wechat,
        'created_at': university.created_at.isoformat() if university.created_at else None,
        'updated_at': university.updated_at.isoformat() if university.updated_at else None,
    }

@router.post("", response_model=UniversityResponse, status_code=status.HTTP_201_CREATED)
async def create_university(*args, **kwargs):
    return await _create_university(*args, **kwargs)

@router.post("/", response_model=UniversityResponse, status_code=status.HTTP_201_CREATED)
async def create_university_with_slash(*args, **kwargs):
    return await _create_university(*args, **kwargs)

@router.put("/{university_id}", response_model=UniversityResponse)
async def update_university(
    university_id: int,
    university_data: UniversityUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update a university (admin only)"""
    if current_user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    university = db.query(University).filter(University.id == university_id).first()
    if not university:
        raise HTTPException(status_code=404, detail="University not found")
    
    update_data = university_data.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(university, field, value)
    
    db.commit()
    db.refresh(university)
    return {
        'id': university.id,
        'name': university.name,
        'city': university.city,
        'province': university.province,
        'country': university.country,
        'is_partner': university.is_partner,
        'university_ranking': university.university_ranking,
        'logo_url': university.logo_url,
        'description': university.description,
        'website': university.website,
        'contact_email': university.contact_email,
        'contact_wechat': university.contact_wechat,
        'created_at': university.created_at.isoformat() if university.created_at else None,
        'updated_at': university.updated_at.isoformat() if university.updated_at else None,
    }

@router.delete("/{university_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_university(
    university_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a university (admin only)"""
    if current_user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    university = db.query(University).filter(University.id == university_id).first()
    if not university:
        raise HTTPException(status_code=404, detail="University not found")
    
    db.delete(university)
    db.commit()
    return None

