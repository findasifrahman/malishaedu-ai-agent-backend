"""
Lead capture endpoints with fuzzy matching
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from app.database import get_db
from app.models import Lead, University, Major
from app.services.sales_agent import SalesAgent
from datetime import datetime, timezone

router = APIRouter()

class LeadFormData(BaseModel):
    chat_session_id: str  # Required: per-chat session identifier
    name: str
    phone: str  # Required: phone number
    age: Optional[int] = None
    nationality: str  # Text, any language - will be fuzzy matched
    subject_major: str  # Text, any language - will be fuzzy matched
    degree_type: str  # Dropdown: "Non-degree", "Associate", "Bachelor", "Master", "Doctoral (PhD)", "Language", "Short Program", "Study Tour Program", "Upgrade from Junior College Student to University Student"
    preferred_city: Optional[str] = None  # Optional, text, fuzzy matched
    intake: str  # Dropdown: "March", "September", "Other"
    intake_year: Optional[int] = None  # Optional: e.g., 2026
    university: Optional[str] = None  # Optional: "any" or "not certain" or specific university name
    device_fingerprint: Optional[str] = None  # Keep for backward compatibility

class LeadFormResponse(BaseModel):
    success: bool
    message: str
    lead_id: Optional[int] = None
    matched_university_id: Optional[int] = None
    matched_major_id: Optional[int] = None

@router.post("/submit", response_model=LeadFormResponse)
async def submit_lead_form(
    form_data: LeadFormData,
    db: Session = Depends(get_db)
):
    """
    Submit lead form with fuzzy matching for nationality, city, university, and major
    """
    try:
        # Initialize SalesAgent for fuzzy matching
        agent = SalesAgent(db)
        
        # Fuzzy match university if provided
        matched_university_id = None
        if form_data.university:
            matched_name, _ = agent._fuzzy_match_university(form_data.university)
            if matched_name:
                uni = db.query(University).filter(University.name == matched_name).first()
                if uni:
                    matched_university_id = uni.id
        
        # Fuzzy match major if provided
        matched_major_id = None
        if form_data.subject_major:
            matched_name, _ = agent._fuzzy_match_major(form_data.subject_major)
            if matched_name:
                major = db.query(Major).filter(Major.name == matched_name).first()
                if major:
                    matched_major_id = major.id
        
        # Normalize nationality (fuzzy match country)
        normalized_nationality = form_data.nationality
        if form_data.nationality:
            normalized_nationality = agent._normalize_country(form_data.nationality) or form_data.nationality
        
        # Normalize city if provided
        normalized_city = form_data.preferred_city
        if form_data.preferred_city:
            matched_city = agent._fuzzy_match_city(form_data.preferred_city)
            if matched_city:
                normalized_city = matched_city
        
        # Handle "any" or "not certain" for university
        # If user selects "any" or "not certain", don't set interested_university_id
        if form_data.university and form_data.university.lower() in ['any', 'not certain', 'not sure', 'any university']:
            matched_university_id = None
        
        # Parse intake term
        intake_term = form_data.intake  # "March", "September", "Other"
        
        # Create lead
        lead = Lead(
            name=form_data.name,
            phone=form_data.phone,
            country=normalized_nationality,
            chat_session_id=form_data.chat_session_id,
            device_fingerprint=form_data.device_fingerprint,  # Keep for backward compatibility
            interested_university_id=matched_university_id,
            interested_major_id=matched_major_id,
            intake_term=intake_term,
            intake_year=form_data.intake_year,
            source="chat_form",
            notes=f"Age: {form_data.age or 'N/A'}, Degree: {form_data.degree_type}, Major: {form_data.subject_major}, City: {normalized_city or 'N/A'}, Intake: {form_data.intake} {form_data.intake_year or ''}"
        )
        
        db.add(lead)
        db.commit()
        db.refresh(lead)
        
        return LeadFormResponse(
            success=True,
            message="Thank you! We've received your information and will contact you soon.",
            lead_id=lead.id,
            matched_university_id=matched_university_id,
            matched_major_id=matched_major_id
        )
    
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error submitting lead form: {str(e)}")

@router.get("/universities")
async def get_universities_for_form(
    search: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Get list of universities for autocomplete (optional)"""
    query = db.query(University)
    if search:
        query = query.filter(University.name.ilike(f"%{search}%"))
    universities = query.limit(50).all()
    return [{"id": uni.id, "name": uni.name, "city": uni.city} for uni in universities]

@router.get("/majors")
async def get_majors_for_form(
    search: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Get list of majors for autocomplete (optional)"""
    query = db.query(Major)
    if search:
        query = query.filter(Major.name.ilike(f"%{search}%"))
    majors = query.limit(100).all()
    return [{"id": major.id, "name": major.name, "university": major.university.name if major.university else None} for major in majors]
