"""
Chat service with enhanced logic for calculating intake deadlines
"""
from datetime import datetime, timedelta
from typing import Optional

def calculate_days_until_intake(intake: str, intake_year: int) -> Optional[int]:
    """Calculate days until intake deadline"""
    current_year = datetime.now().year
    current_month = datetime.now().month
    
    # Determine intake month
    if intake.lower() == "march":
        intake_month = 3
    elif intake.lower() == "september":
        intake_month = 9
    else:
        return None
    
    # Calculate deadline (usually 2-3 months before intake)
    deadline_month = intake_month - 2
    deadline_year = intake_year
    
    if deadline_month <= 0:
        deadline_month += 12
        deadline_year -= 1
    
    deadline = datetime(deadline_year, deadline_month, 1)
    days_left = (deadline - datetime.now()).days
    
    return days_left if days_left > 0 else None

def format_intake_reminder(intake: str, intake_year: int) -> str:
    """Format intake deadline reminder message"""
    days_left = calculate_days_until_intake(intake, intake_year)
    
    if days_left is None:
        return f"Intake: {intake} {intake_year}"
    
    if days_left < 30:
        urgency = "⚠️ URGENT: "
    elif days_left < 60:
        urgency = "⏰ "
    else:
        urgency = ""
    
    return f"{urgency}Only {days_left} days left until {intake} {intake_year} intake deadline! Apply now to secure your spot."

