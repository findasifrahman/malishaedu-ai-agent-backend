"""
Base class for portal-specific overrides
"""
from typing import Dict, Optional, Any
from abc import ABC, abstractmethod


class PortalBase(ABC):
    """Base class for university portal-specific implementations"""
    
    @abstractmethod
    def get_login_url(self) -> Optional[str]:
        """Get login URL if different from apply URL"""
        pass
    
    @abstractmethod
    def navigate_to_application_form(self, page) -> bool:
        """Navigate to the actual application form after login"""
        pass
    
    def map_field(self, field_name: str, field_id: str, label: str, placeholder: str, student_data: Dict[str, Any]) -> Optional[str]:
        """Map form field to student data (override for custom mapping)"""
        return None
    
    def map_select(self, field_name: str, field_id: str, label: str, student_data: Dict[str, Any]) -> Optional[str]:
        """Map select dropdown to student data (override for custom mapping)"""
        return None
    
    def map_document(self, field_name: str, field_id: str, label: str) -> Optional[str]:
        """Map file input to document type (override for custom mapping)"""
        return None
    
    def get_custom_selectors(self) -> Dict[str, str]:
        """Get custom CSS selectors for this portal"""
        return {}

