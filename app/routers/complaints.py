from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from app.database import get_db
from app.models import Complaint, User, ComplaintStatus, UserRole
from app.routers.auth import get_current_user

router = APIRouter()

class ComplaintCreate(BaseModel):
    subject: str
    message: str
    device_fingerprint: Optional[str] = None

class ComplaintResponse(BaseModel):
    id: int
    subject: str
    message: str
    status: str
    admin_response: Optional[str]
    created_at: str

@router.post("/", response_model=ComplaintResponse)
async def create_complaint(
    complaint: ComplaintCreate,
    current_user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a complaint (can be anonymous)"""
    new_complaint = Complaint(
        user_id=current_user.id if current_user else None,
        device_fingerprint=complaint.device_fingerprint,
        subject=complaint.subject,
        message=complaint.message,
        status=ComplaintStatus.PENDING
    )
    db.add(new_complaint)
    db.commit()
    db.refresh(new_complaint)
    
    return ComplaintResponse(
        id=new_complaint.id,
        subject=new_complaint.subject,
        message=new_complaint.message,
        status=new_complaint.status.value,
        admin_response=new_complaint.admin_response,
        created_at=new_complaint.created_at.isoformat() if new_complaint.created_at else ""
    )

@router.get("/")
async def get_complaints(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get complaints for current user"""
    if current_user.role == UserRole.ADMIN:
        # Admin can see all complaints
        complaints = db.query(Complaint).all()
    else:
        # Regular users see only their own
        complaints = db.query(Complaint).filter(Complaint.user_id == current_user.id).all()
    
    return [
        {
            "id": comp.id,
            "subject": comp.subject,
            "message": comp.message,
            "status": comp.status.value,
            "admin_response": comp.admin_response,
            "created_at": comp.created_at.isoformat() if comp.created_at else None
        }
        for comp in complaints
    ]

@router.put("/{complaint_id}/resolve")
async def resolve_complaint(
    complaint_id: int,
    admin_response: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Resolve a complaint (admin only)"""
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    complaint = db.query(Complaint).filter(Complaint.id == complaint_id).first()
    if not complaint:
        raise HTTPException(status_code=404, detail="Complaint not found")
    
    complaint.status = ComplaintStatus.RESOLVED
    complaint.admin_response = admin_response
    complaint.resolved_at = datetime.utcnow()
    
    db.commit()
    db.refresh(complaint)
    
    return {"message": "Complaint resolved successfully"}

