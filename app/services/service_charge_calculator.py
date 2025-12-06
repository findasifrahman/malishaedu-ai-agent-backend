"""
Service Charge Calculator for MalishaEdu
Calculates service charges based on degree level, teaching language, and scholarship type
Based on: https://malishaedu.com/single-services/Service%20Charges
"""
from typing import Optional
from app.models import ScholarshipPreference

# USD to RMB conversion rate (should be configurable, using approximate rate)
USD_TO_RMB_RATE = 7.2  # Approximate rate, should be updated regularly

# Application deposit in USD (always required)
APPLICATION_DEPOSIT_USD = 80.0

def calculate_service_charge_usd(
    degree_level: str,
    teaching_language: str,
    scholarship_preference: Optional[str],
    tuition_per_year: Optional[float] = None,
    accommodation_fee: Optional[float] = None,
    scholarship_info: Optional[str] = None
) -> float:
    """
    Calculate MalishaEdu service charge in USD based on program details
    
    Args:
        degree_level: "Bachelor", "Master", "Doctoral (PhD)", "Language", etc.
        teaching_language: "English" or "Chinese"
        scholarship_preference: "Type-A", "Type-B", "Type-C", "Type-D", "None", or None
        tuition_per_year: Annual tuition fee in CNY (for partial scholarship calculation)
        accommodation_fee: Annual accommodation fee in CNY (for partial scholarship calculation)
        scholarship_info: Scholarship information text (for parsing)
    
    Returns:
        Service charge in USD
    """
    degree_lower = degree_level.lower() if degree_level else ""
    language_lower = teaching_language.lower() if teaching_language else ""
    
    # Language programs - always 150 USD (no scholarship)
    if "language" in degree_lower or degree_lower == "non-degree":
        return 150.0
    
    # Determine scholarship type based on preference
    if scholarship_preference:
        if scholarship_preference == "Type-A":
            # Tuition free, accommodation free, stipend up to 35000 CNY
            if "bachelor" in degree_lower:
                return 900.0 if language_lower == "english" else 700.0
            elif "master" in degree_lower:
                return 900.0 if language_lower == "english" else 700.0
            elif "phd" in degree_lower or "doctoral" in degree_lower:
                return 900.0 if language_lower == "english" else 700.0
        elif scholarship_preference == "Type-B":
            # Tuition free, accommodation free, no stipend
            if "bachelor" in degree_lower:
                return 700.0 if language_lower == "english" else 600.0
            elif "master" in degree_lower:
                return 700.0 if language_lower == "english" else 600.0
            elif "phd" in degree_lower or "doctoral" in degree_lower:
                return 700.0 if language_lower == "english" else 600.0
        elif scholarship_preference == "Type-C":
            # Only tuition fee free
            if "bachelor" in degree_lower:
                return 500.0 if language_lower == "english" else 400.0
            elif "master" in degree_lower:
                return 500.0 if language_lower == "english" else 400.0
            elif "phd" in degree_lower or "doctoral" in degree_lower:
                return 500.0 if language_lower == "english" else 400.0
        elif scholarship_preference == "Type-D":
            # Only tuition fee free (alternative)
            if "bachelor" in degree_lower:
                return 500.0 if language_lower == "english" else 400.0
            elif "master" in degree_lower:
                return 500.0 if language_lower == "english" else 400.0
            elif "phd" in degree_lower or "doctoral" in degree_lower:
                return 500.0 if language_lower == "english" else 400.0
        elif scholarship_preference == "Partial-Low":
            # Partial Scholarship (<5000 CNY/year): 500 USD
            return 500.0
        elif scholarship_preference == "Partial-Mid":
            # Partial Scholarship (5100-10000 CNY/year): 350 USD
            return 350.0
        elif scholarship_preference == "Partial-High":
            # Partial Scholarship (10000-15000 CNY/year): 300 USD
            return 300.0
        elif scholarship_preference == "Self-Paid":
            # Self-Paid: 150 USD
            return 150.0
        elif scholarship_preference == "No Scholarship" or scholarship_preference == "None":
            # No scholarship (for Language programs): 150 USD
            return 150.0
    
    # If no scholarship preference but we have tuition/accommodation info, calculate partial scholarship
    if tuition_per_year is not None and accommodation_fee is not None:
        total_fees = tuition_per_year + accommodation_fee
        
        if total_fees < 5000:
            # Partial scholarship (< 5000 CNY/year)
            if "bachelor" in degree_lower:
                return 500.0
            elif "master" in degree_lower:
                return 500.0
            elif "phd" in degree_lower or "doctoral" in degree_lower:
                return 500.0
        elif 5100 <= total_fees <= 10000:
            # Partial scholarship (5100-10000 CNY/year)
            if "bachelor" in degree_lower:
                return 350.0
            elif "master" in degree_lower:
                return 350.0
            elif "phd" in degree_lower or "doctoral" in degree_lower:
                return 350.0
        elif 10000 < total_fees <= 15000:
            # Partial scholarship (10000-15000 CNY/year)
            if "bachelor" in degree_lower:
                return 300.0
            elif "master" in degree_lower:
                return 300.0
            elif "phd" in degree_lower or "doctoral" in degree_lower:
                return 300.0
        else:
            # Self-paid (> 15000 CNY/year)
            if "bachelor" in degree_lower:
                return 150.0
            elif "master" in degree_lower:
                return 150.0
            elif "phd" in degree_lower or "doctoral" in degree_lower:
                return 150.0
    
    # Default: Self-paid
    if "bachelor" in degree_lower:
        return 150.0
    elif "master" in degree_lower:
        return 150.0
    elif "phd" in degree_lower or "doctoral" in degree_lower:
        return 150.0
    
    # Fallback
    return 150.0

def calculate_payment_fee_required(
    application_fee_rmb: Optional[float],
    degree_level: str,
    teaching_language: str,
    scholarship_preference: Optional[str],
    tuition_per_year: Optional[float] = None,
    accommodation_fee: Optional[float] = None,
    scholarship_info: Optional[str] = None,
    usd_to_rmb_rate: float = USD_TO_RMB_RATE
) -> float:
    """
    Calculate payment fee required in RMB
    
    Includes:
    1. Application fee from program_intakes.application_fee (in RMB, or 0 if null/0)
    2. MalishaEdu service charge (USD converted to RMB, based on scholarship type)
    
    Note: The 80 USD deposit is NOT included in payment_fee_required.
    The 80 USD deposit is required to start the application process (shown separately).
    The service charge is paid AFTER successful admission. If no admission, the 80 USD deposit is refunded.
    
    Args:
        application_fee_rmb: Application fee from program_intake.application_fee (in RMB)
                            If None or 0, will be treated as 0
        degree_level: Degree level string
        teaching_language: Teaching language string
        scholarship_preference: Scholarship preference type
        tuition_per_year: Annual tuition in CNY
        accommodation_fee: Annual accommodation fee in CNY
        scholarship_info: Scholarship information
        usd_to_rmb_rate: USD to RMB conversion rate
    
    Returns:
        Payment fee required in RMB (university fee + service charge, excluding 80 USD deposit)
    """
    # Application fee from program_intakes table (or 0 if null/0)
    app_fee = (application_fee_rmb or 0.0) if application_fee_rmb is not None else 0.0
    
    # Calculate service charge in USD based on scholarship type
    service_charge_usd = calculate_service_charge_usd(
        degree_level=degree_level,
        teaching_language=teaching_language,
        scholarship_preference=scholarship_preference,
        tuition_per_year=tuition_per_year,
        accommodation_fee=accommodation_fee,
        scholarship_info=scholarship_info
    )
    
    # Convert USD amounts to RMB
    service_charge_rmb = service_charge_usd * usd_to_rmb_rate
    application_deposit_rmb = APPLICATION_DEPOSIT_USD * usd_to_rmb_rate
    
    # Payment fee required = University Application Fee + MalishaEdu Service Charge
    # The 80 USD deposit is NOT included (it's required to start but shown separately)
    # The service charge is paid AFTER successful admission
    total = app_fee + service_charge_rmb
    
    print(f"\n💵 PAYMENT FEE CALCULATION:")
    print(f"{'='*80}")
    print(f"University Application Fee (from program_intakes.application_fee): {app_fee:.2f} RMB")
    print(f"MalishaEdu Service Charge (based on scholarship): {service_charge_usd} USD = {service_charge_rmb:.2f} RMB")
    print(f"Payment Fee Required: {total:.2f} RMB")
    print(f"\nNote: 80 USD deposit ({application_deposit_rmb:.2f} RMB) is required to start the application process.")
    print(f"      The service charge is paid AFTER successful admission. If no admission, the 80 USD deposit is refunded.")
    print(f"{'='*80}\n")
    
    return round(total, 2)

