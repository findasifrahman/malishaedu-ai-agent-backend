"""
Portal-specific overrides for different university application systems
"""
from .base import PortalBase
from .hit import HITPortal
from .beihang import BeihangPortal
from .bnuz import BNUZPortal

__all__ = ["PortalBase", "HITPortal", "BeihangPortal", "BNUZPortal"]

