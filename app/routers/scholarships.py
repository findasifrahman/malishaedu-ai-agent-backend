from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from datetime import date
from app.database import get_db
from app.models import Scholarship, ProgramIntakeScholarship, ProgramIntake, User
from app.routers.auth import get_current_user

router = APIRouter()

# ========== Scholarship Models ==========
class ScholarshipCreate(BaseModel):
    name: str
    provider: Optional[str] = None
    notes: Optional[str] = None

class ScholarshipUpdate(BaseModel):
    name: Optional[str] = None
    provider: Optional[str] = None
    notes: Optional[str] = None

class ScholarshipResponse(BaseModel):
    id: int
    name: str
    provider: Optional[str]
    notes: Optional[str]
    created_at: str
    updated_at: Optional[str]
    
    class Config:
        from_attributes = True

# ========== Program Intake Scholarship Models ==========
class ProgramIntakeScholarshipCreate(BaseModel):
    program_intake_id: int
    scholarship_id: int
    covers_tuition: Optional[bool] = None
    covers_accommodation: Optional[bool] = None
    covers_insurance: Optional[bool] = None
    tuition_waiver_percent: Optional[int] = None
    living_allowance_monthly: Optional[float] = None
    living_allowance_yearly: Optional[float] = None
    first_year_only: Optional[bool] = None
    renewal_required: Optional[bool] = None
    deadline: Optional[date] = None
    eligibility_note: Optional[str] = None

class ProgramIntakeScholarshipUpdate(BaseModel):
    covers_tuition: Optional[bool] = None
    covers_accommodation: Optional[bool] = None
    covers_insurance: Optional[bool] = None
    tuition_waiver_percent: Optional[int] = None
    living_allowance_monthly: Optional[float] = None
    living_allowance_yearly: Optional[float] = None
    first_year_only: Optional[bool] = None
    renewal_required: Optional[bool] = None
    deadline: Optional[date] = None
    eligibility_note: Optional[str] = None

class ProgramIntakeScholarshipResponse(BaseModel):
    id: int
    program_intake_id: int
    scholarship_id: int
    scholarship_name: str
    covers_tuition: Optional[bool]
    covers_accommodation: Optional[bool]
    covers_insurance: Optional[bool]
    tuition_waiver_percent: Optional[int]
    living_allowance_monthly: Optional[float]
    living_allowance_yearly: Optional[float]
    first_year_only: Optional[bool]
    renewal_required: Optional[bool]
    deadline: Optional[str]
    eligibility_note: Optional[str]
    created_at: str
    updated_at: Optional[str]
    
    class Config:
        from_attributes = True

# ========== Scholarship CRUD ==========
@router.get("", response_model=List[ScholarshipResponse])
@router.get("/", response_model=List[ScholarshipResponse])
async def list_scholarships(db: Session = Depends(get_db)):
    """Get all scholarships"""
    scholarships = db.query(Scholarship).order_by(Scholarship.name).all()
    return [
        {
            'id': s.id,
            'name': s.name,
            'provider': s.provider,
            'notes': s.notes,
            'created_at': s.created_at.isoformat() if s.created_at else None,
            'updated_at': s.updated_at.isoformat() if s.updated_at else None,
        }
        for s in scholarships
    ]

@router.get("/{scholarship_id}", response_model=ScholarshipResponse)
async def get_scholarship(scholarship_id: int, db: Session = Depends(get_db)):
    """Get a specific scholarship by ID"""
    scholarship = db.query(Scholarship).filter(Scholarship.id == scholarship_id).first()
    if not scholarship:
        raise HTTPException(status_code=404, detail="Scholarship not found")
    
    return {
        'id': scholarship.id,
        'name': scholarship.name,
        'provider': scholarship.provider,
        'notes': scholarship.notes,
        'created_at': scholarship.created_at.isoformat() if scholarship.created_at else None,
        'updated_at': scholarship.updated_at.isoformat() if scholarship.updated_at else None,
    }

@router.post("", response_model=ScholarshipResponse, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=ScholarshipResponse, status_code=status.HTTP_201_CREATED)
async def create_scholarship(
    scholarship_data: ScholarshipCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new scholarship (admin only)"""
    if current_user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    scholarship = Scholarship(**scholarship_data.dict())
    db.add(scholarship)
    db.commit()
    db.refresh(scholarship)
    
    return {
        'id': scholarship.id,
        'name': scholarship.name,
        'provider': scholarship.provider,
        'notes': scholarship.notes,
        'created_at': scholarship.created_at.isoformat() if scholarship.created_at else None,
        'updated_at': scholarship.updated_at.isoformat() if scholarship.updated_at else None,
    }

@router.put("/{scholarship_id}", response_model=ScholarshipResponse)
async def update_scholarship(
    scholarship_id: int,
    scholarship_data: ScholarshipUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update a scholarship (admin only)"""
    if current_user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    scholarship = db.query(Scholarship).filter(Scholarship.id == scholarship_id).first()
    if not scholarship:
        raise HTTPException(status_code=404, detail="Scholarship not found")
    
    update_data = scholarship_data.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(scholarship, field, value)
    
    db.commit()
    db.refresh(scholarship)
    
    return {
        'id': scholarship.id,
        'name': scholarship.name,
        'provider': scholarship.provider,
        'notes': scholarship.notes,
        'created_at': scholarship.created_at.isoformat() if scholarship.created_at else None,
        'updated_at': scholarship.updated_at.isoformat() if scholarship.updated_at else None,
    }

@router.delete("/{scholarship_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scholarship(
    scholarship_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a scholarship (admin only)"""
    if current_user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    scholarship = db.query(Scholarship).filter(Scholarship.id == scholarship_id).first()
    if not scholarship:
        raise HTTPException(status_code=404, detail="Scholarship not found")
    
    db.delete(scholarship)
    db.commit()
    return None

# ========== Program Intake Scholarships CRUD ==========
@router.get("/program-intakes/{intake_id}/scholarships")
async def list_program_intake_scholarships(
    intake_id: int,
    db: Session = Depends(get_db)
):
    """Get all scholarships for a specific program intake"""
    # Verify program intake exists
    intake = db.query(ProgramIntake).filter(ProgramIntake.id == intake_id).first()
    if not intake:
        raise HTTPException(status_code=404, detail="Program intake not found")
    
    scholarships = db.query(ProgramIntakeScholarship).filter(
        ProgramIntakeScholarship.program_intake_id == intake_id
    ).all()
    
    result = []
    for pis in scholarships:
        scholarship = db.query(Scholarship).filter(Scholarship.id == pis.scholarship_id).first()
        result.append({
            'id': pis.id,
            'program_intake_id': pis.program_intake_id,
            'scholarship_id': pis.scholarship_id,
            'scholarship_name': scholarship.name if scholarship else 'Unknown',
            'covers_tuition': pis.covers_tuition,
            'covers_accommodation': pis.covers_accommodation,
            'covers_insurance': pis.covers_insurance,
            'tuition_waiver_percent': pis.tuition_waiver_percent,
            'living_allowance_monthly': pis.living_allowance_monthly,
            'living_allowance_yearly': pis.living_allowance_yearly,
            'first_year_only': pis.first_year_only,
            'renewal_required': pis.renewal_required,
            'deadline': pis.deadline.isoformat() if pis.deadline else None,
            'eligibility_note': pis.eligibility_note,
            'created_at': pis.created_at.isoformat() if pis.created_at else None,
            'updated_at': pis.updated_at.isoformat() if pis.updated_at else None,
        })
    
    return result

@router.post("/program-intakes/scholarships", response_model=ProgramIntakeScholarshipResponse, status_code=status.HTTP_201_CREATED)
async def create_program_intake_scholarship(
    scholarship_data: ProgramIntakeScholarshipCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new program intake scholarship (admin only)"""
    if current_user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Verify program intake exists
    intake = db.query(ProgramIntake).filter(ProgramIntake.id == scholarship_data.program_intake_id).first()
    if not intake:
        raise HTTPException(status_code=404, detail="Program intake not found")
    
    # Verify scholarship exists
    scholarship = db.query(Scholarship).filter(Scholarship.id == scholarship_data.scholarship_id).first()
    if not scholarship:
        raise HTTPException(status_code=404, detail="Scholarship not found")
    
    pis = ProgramIntakeScholarship(**scholarship_data.dict())
    db.add(pis)
    db.commit()
    db.refresh(pis)
    
    return {
        'id': pis.id,
        'program_intake_id': pis.program_intake_id,
        'scholarship_id': pis.scholarship_id,
        'scholarship_name': scholarship.name,
        'covers_tuition': pis.covers_tuition,
        'covers_accommodation': pis.covers_accommodation,
        'covers_insurance': pis.covers_insurance,
        'tuition_waiver_percent': pis.tuition_waiver_percent,
        'living_allowance_monthly': pis.living_allowance_monthly,
        'living_allowance_yearly': pis.living_allowance_yearly,
        'first_year_only': pis.first_year_only,
        'renewal_required': pis.renewal_required,
        'deadline': pis.deadline.isoformat() if pis.deadline else None,
        'eligibility_note': pis.eligibility_note,
        'created_at': pis.created_at.isoformat() if pis.created_at else None,
        'updated_at': pis.updated_at.isoformat() if pis.updated_at else None,
    }

@router.put("/program-intakes/scholarships/{pis_id}", response_model=ProgramIntakeScholarshipResponse)
async def update_program_intake_scholarship(
    pis_id: int,
    scholarship_data: ProgramIntakeScholarshipUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update a program intake scholarship (admin only)"""
    if current_user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    pis = db.query(ProgramIntakeScholarship).filter(ProgramIntakeScholarship.id == pis_id).first()
    if not pis:
        raise HTTPException(status_code=404, detail="Program intake scholarship not found")
    
    update_data = scholarship_data.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(pis, field, value)
    
    db.commit()
    db.refresh(pis)
    
    scholarship = db.query(Scholarship).filter(Scholarship.id == pis.scholarship_id).first()
    
    return {
        'id': pis.id,
        'program_intake_id': pis.program_intake_id,
        'scholarship_id': pis.scholarship_id,
        'scholarship_name': scholarship.name if scholarship else 'Unknown',
        'covers_tuition': pis.covers_tuition,
        'covers_accommodation': pis.covers_accommodation,
        'covers_insurance': pis.covers_insurance,
        'tuition_waiver_percent': pis.tuition_waiver_percent,
        'living_allowance_monthly': pis.living_allowance_monthly,
        'living_allowance_yearly': pis.living_allowance_yearly,
        'first_year_only': pis.first_year_only,
        'renewal_required': pis.renewal_required,
        'deadline': pis.deadline.isoformat() if pis.deadline else None,
        'eligibility_note': pis.eligibility_note,
        'created_at': pis.created_at.isoformat() if pis.created_at else None,
        'updated_at': pis.updated_at.isoformat() if pis.updated_at else None,
    }

@router.delete("/program-intakes/scholarships/{pis_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_program_intake_scholarship(
    pis_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a program intake scholarship (admin only)"""
    if current_user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    pis = db.query(ProgramIntakeScholarship).filter(ProgramIntakeScholarship.id == pis_id).first()
    if not pis:
        raise HTTPException(status_code=404, detail="Program intake scholarship not found")
    
    db.delete(pis)
    db.commit()
    return None

