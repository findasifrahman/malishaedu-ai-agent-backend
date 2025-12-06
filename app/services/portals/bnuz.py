"""
BNUZ (Beijing Normal University Zhuhai) portal override
"""
from typing import Dict, Optional, Any
from .base import PortalBase


class BNUZPortal(PortalBase):
    """BNUZ-specific portal implementation"""
    
    def get_login_url(self) -> Optional[str]:
        return None
    
    def navigate_to_application_form(self, page) -> bool:
        """Navigate to BNUZ application form"""
        try:
            app_link = page.query_selector("a:has-text('Application'), a:has-text('申请')")
            if app_link:
                app_link.click()
                page.wait_for_load_state("networkidle")
                return True
        except:
            pass
        return False

