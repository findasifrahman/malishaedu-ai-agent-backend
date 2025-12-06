"""
Beihang University portal override
"""
from typing import Dict, Optional, Any
from .base import PortalBase


class BeihangPortal(PortalBase):
    """Beihang-specific portal implementation"""
    
    def get_login_url(self) -> Optional[str]:
        return None
    
    def navigate_to_application_form(self, page) -> bool:
        """Navigate to Beihang application form"""
        try:
            # Beihang-specific navigation
            app_link = page.query_selector("a:has-text('Online Application'), a:has-text('在线申请')")
            if app_link:
                app_link.click()
                page.wait_for_load_state("networkidle")
                return True
        except:
            pass
        return False
    
    def map_field(self, field_name: str, field_id: str, label: str, placeholder: str, student_data: Dict[str, Any]) -> Optional[str]:
        """Beihang-specific field mapping"""
        combined = f"{field_name} {field_id} {label} {placeholder}".lower()
        
        # Beihang-specific mappings
        if "beihang" in combined and "id" in combined:
            return student_data.get("passport_number")
        
        return None

