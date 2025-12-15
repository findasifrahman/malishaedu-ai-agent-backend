from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from app.database import get_db
from app.models import ProgramExamRequirement, ProgramIntake, User
from app.routers.auth import get_current_user

router = APIRouter()

class ProgramExamRequirementCreate(BaseModel):
    program_intake_id: int
    exam_name: str
    required: bool = True
    subjects: Optional[str] = None
    min_level: Optional[int] = None
    min_score: Optional[int] = None
    exam_language: Optional[str] = None
    notes: Optional[str] = None

class ProgramExamRequirementUpdate(BaseModel):
    exam_name: Optional[str] = None
    required: Optional[bool] = None
    subjects: Optional[str] = None
    min_level: Optional[int] = None
    min_score: Optional[int] = None
    exam_language: Optional[str] = None
    notes: Optional[str] = None

class ProgramExamRequirementResponse(BaseModel):
    id: int
    program_intake_id: int
    exam_name: str
    required: bool
    subjects: Optional[str]
    min_level: Optional[int]
    min_score: Optional[int]
    exam_language: Optional[str]
    notes: Optional[str]
    created_at: str
    updated_at: Optional[str]
    
    class Config:
        from_attributes = True

@router.get("/program-intakes/{intake_id}/exam-requirements")
async def list_program_exam_requirements(
    intake_id: int,
    db: Session = Depends(get_db)
):
    """Get all exam requirements for a specific program intake"""
    # Verify program intake exists
    intake = db.query(ProgramIntake).filter(ProgramIntake.id == intake_id).first()
    if not intake:
        raise HTTPException(status_code=404, detail="Program intake not found")
    
    exam_requirements = db.query(ProgramExamRequirement).filter(
        ProgramExamRequirement.program_intake_id == intake_id
    ).order_by(ProgramExamRequirement.exam_name).all()
    
    return [
        {
            'id': req.id,
            'program_intake_id': req.program_intake_id,
            'exam_name': req.exam_name,
            'required': req.required,
            'subjects': req.subjects,
            'min_level': req.min_level,
            'min_score': req.min_score,
            'exam_language': req.exam_language,
            'notes': req.notes,
            'created_at': req.created_at.isoformat() if req.created_at else None,
            'updated_at': req.updated_at.isoformat() if req.updated_at else None,
        }
        for req in exam_requirements
    ]

@router.get("/{exam_requirement_id}", response_model=ProgramExamRequirementResponse)
async def get_program_exam_requirement(
    exam_requirement_id: int,
    db: Session = Depends(get_db)
):
    """Get a specific exam requirement by ID"""
    exam_req = db.query(ProgramExamRequirement).filter(ProgramExamRequirement.id == exam_requirement_id).first()
    if not exam_req:
        raise HTTPException(status_code=404, detail="Exam requirement not found")
    
    return {
        'id': exam_req.id,
        'program_intake_id': exam_req.program_intake_id,
        'exam_name': exam_req.exam_name,
        'required': exam_req.required,
        'subjects': exam_req.subjects,
        'min_level': exam_req.min_level,
        'min_score': exam_req.min_score,
        'exam_language': exam_req.exam_language,
        'notes': exam_req.notes,
        'created_at': exam_req.created_at.isoformat() if exam_req.created_at else None,
        'updated_at': exam_req.updated_at.isoformat() if exam_req.updated_at else None,
    }

@router.post("", response_model=ProgramExamRequirementResponse, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=ProgramExamRequirementResponse, status_code=status.HTTP_201_CREATED)
async def create_program_exam_requirement(
    exam_req_data: ProgramExamRequirementCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new exam requirement (admin only)"""
    if current_user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Verify program intake exists
    intake = db.query(ProgramIntake).filter(ProgramIntake.id == exam_req_data.program_intake_id).first()
    if not intake:
        raise HTTPException(status_code=404, detail="Program intake not found")
    
    exam_req = ProgramExamRequirement(**exam_req_data.dict())
    db.add(exam_req)
    db.commit()
    db.refresh(exam_req)
    
    return {
        'id': exam_req.id,
        'program_intake_id': exam_req.program_intake_id,
        'exam_name': exam_req.exam_name,
        'required': exam_req.required,
        'subjects': exam_req.subjects,
        'min_level': exam_req.min_level,
        'min_score': exam_req.min_score,
        'exam_language': exam_req.exam_language,
        'notes': exam_req.notes,
        'created_at': exam_req.created_at.isoformat() if exam_req.created_at else None,
        'updated_at': exam_req.updated_at.isoformat() if exam_req.updated_at else None,
    }

@router.put("/{exam_requirement_id}", response_model=ProgramExamRequirementResponse)
async def update_program_exam_requirement(
    exam_requirement_id: int,
    exam_req_data: ProgramExamRequirementUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update an exam requirement (admin only)"""
    if current_user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    exam_req = db.query(ProgramExamRequirement).filter(ProgramExamRequirement.id == exam_requirement_id).first()
    if not exam_req:
        raise HTTPException(status_code=404, detail="Exam requirement not found")
    
    update_data = exam_req_data.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(exam_req, field, value)
    
    db.commit()
    db.refresh(exam_req)
    
    return {
        'id': exam_req.id,
        'program_intake_id': exam_req.program_intake_id,
        'exam_name': exam_req.exam_name,
        'required': exam_req.required,
        'subjects': exam_req.subjects,
        'min_level': exam_req.min_level,
        'min_score': exam_req.min_score,
        'exam_language': exam_req.exam_language,
        'notes': exam_req.notes,
        'created_at': exam_req.created_at.isoformat() if exam_req.created_at else None,
        'updated_at': exam_req.updated_at.isoformat() if exam_req.updated_at else None,
    }

@router.delete("/{exam_requirement_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_program_exam_requirement(
    exam_requirement_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete an exam requirement (admin only)"""
    if current_user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    exam_req = db.query(ProgramExamRequirement).filter(ProgramExamRequirement.id == exam_requirement_id).first()
    if not exam_req:
        raise HTTPException(status_code=404, detail="Exam requirement not found")
    
    db.delete(exam_req)
    db.commit()
    return None

