"""
Harbin Institute of Technology (HIT) portal override
"""
from typing import Dict, Optional, Any
from .base import PortalBase


class HITPortal(PortalBase):
    """HIT-specific portal implementation"""
    
    def get_login_url(self) -> Optional[str]:
        return None  # Use provided URL
    
    def navigate_to_application_form(self, page) -> bool:
        """Navigate to HIT application form"""
        try:
            # Look for application link/button
            app_link = page.query_selector("a:has-text('Application'), a:has-text('Apply'), a:has-text('申请')")
            if app_link:
                app_link.click()
                page.wait_for_load_state("networkidle")
                return True
        except:
            pass
        return False
    
    def map_field(self, field_name: str, field_id: str, label: str, placeholder: str, student_data: Dict[str, Any]) -> Optional[str]:
        """HIT-specific field mapping"""
        combined = f"{field_name} {field_id} {label} {placeholder}".lower()
        
        # Add HIT-specific mappings here
        if "hit" in combined and "student" in combined:
            return student_data.get("full_name")
        
        return None
    
    def get_custom_selectors(self) -> Dict[str, str]:
        """HIT-specific selectors"""
        return {
            "application_form": ".application-form",
            "submit_button": "button.submit-application"
        }

