from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from app.database import get_db
from app.models import Lead

router = APIRouter()

class LeadCreate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    country: Optional[str] = None
    device_fingerprint: Optional[str] = None

@router.post("/")
async def create_lead(lead: LeadCreate, db: Session = Depends(get_db)):
    """Create a lead (no authentication required)"""
    new_lead = Lead(
        name=lead.name,
        email=lead.email,
        phone=lead.phone,
        country=lead.country,
        device_fingerprint=lead.device_fingerprint
    )
    db.add(new_lead)
    db.commit()
    db.refresh(new_lead)
    
    return {
        "id": new_lead.id,
        "message": "Lead captured successfully"
    }

