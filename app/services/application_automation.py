"""
Semi-Automated University Application Assistant
Uses Playwright to fill application forms with student data
"""
import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from urllib.parse import urlparse

# Optional Playwright import - server can start without it
try:
    from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    # Create dummy types for type hints
    Page = Any
    Browser = Any
    BrowserContext = Any
    sync_playwright = None

from sqlalchemy.orm import Session

from app.models import Student, Application, DocumentType, StudentDocument
from app.services.r2_service import R2Service

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class StudentLoader:
    """Loads student data from database"""
    
    def __init__(self, db: Session, student_id: int):
        self.db = db
        self.student_id = student_id
        self.student: Optional[Student] = None
        self.application: Optional[Application] = None
    
    def load(self) -> Dict[str, Any]:
        """Load student and application data"""
        self.student = self.db.query(Student).filter(Student.id == self.student_id).first()
        if not self.student:
            raise ValueError(f"Student with ID {self.student_id} not found")
        
        # Get the most recent application for this student
        self.application = self.db.query(Application).filter(
            Application.student_id == self.student_id
        ).order_by(Application.created_at.desc()).first()
        
        if not self.application:
            raise ValueError(f"No application found for student {self.student_id}")
        
        # Helper function to safely get attribute with default
        def safe_get(attr_name, default=""):
            """Safely get attribute from student, returning default if not found"""
            try:
                value = getattr(self.student, attr_name, None)
                if value is None:
                    return default
                # Handle date objects
                if hasattr(value, 'strftime'):
                    return value.strftime("%Y-%m-%d")
                # Handle enum values
                if hasattr(value, 'value'):
                    return value.value
                return str(value) if value else default
            except (AttributeError, TypeError):
                return default
        
        # Build student data dictionary - using safe_get to handle missing attributes
        student_data = {
            # Personal Info
            "full_name": f"{safe_get('given_name')} {safe_get('family_name')}".strip(),
            "given_name": safe_get('given_name'),
            "family_name": safe_get('family_name'),
            "name_in_chinese": safe_get('name_in_chinese'),
            "date_of_birth": safe_get('date_of_birth'),
            "gender": safe_get('gender'),
            "nationality": safe_get('country_of_citizenship'),
            "passport_number": safe_get('passport_number'),
            "passport_expiry_date": safe_get('passport_expiry_date'),
            
            # Contact Info
            "email": safe_get('email'),
            "phone": safe_get('phone'),
            "wechat_id": safe_get('wechat_id'),
            
            # Address
            "home_address": safe_get('home_address'),
            "current_address": safe_get('current_address'),
            "current_country": safe_get('current_country_of_residence'),
            
            # Academic Info
            "highest_degree": safe_get('highest_degree_name'),
            "highest_degree_institution": safe_get('highest_degree_institution'),
            "highest_degree_country": safe_get('highest_degree_country'),
            "highest_degree_year": safe_get('highest_degree_year'),
            "highest_degree_cgpa": safe_get('highest_degree_cgpa'),
            
            # Test Scores
            "hsk_level": safe_get('hsk_level'),
            "csca_status": safe_get('csca_status'),
            "csca_score_math": safe_get('csca_score_math'),
            "csca_score_specialized_chinese": safe_get('csca_score_specialized_chinese'),
            "csca_score_physics": safe_get('csca_score_physics'),
            "csca_score_chemistry": safe_get('csca_score_chemistry'),
            "english_test_type": safe_get('english_test_type'),
            "english_test_score": safe_get('english_test_score'),
            
            # Emergency Contact
            "emergency_contact_name": safe_get('emergency_contact_name'),
            "emergency_contact_phone": safe_get('emergency_contact_phone'),
            "emergency_contact_relationship": safe_get('emergency_contact_relationship'),
            
            # Application Info
            "target_major": self.application.program_intake.major.name if self.application.program_intake and self.application.program_intake.major else "",
            "target_university": self.application.program_intake.university.name if self.application.program_intake and self.application.program_intake.university else "",
            "intake_term": self.application.program_intake.intake_term.value if self.application.program_intake and self.application.program_intake.intake_term else "",
            "intake_year": str(self.application.program_intake.intake_year) if self.application.program_intake and self.application.program_intake.intake_year else "",
        }
        
        return student_data


class DocumentLoader:
    """Loads and prepares student documents for upload"""
    
    def __init__(self, db: Session, student_id: int, r2_service: R2Service):
        self.db = db
        self.student_id = student_id
        self.r2_service = r2_service
        self.documents: Dict[str, str] = {}  # doc_type -> local_file_path
    
    def load(self) -> Dict[str, str]:
        """Load all student documents and download to temp files"""
        # Query student documents
        student_docs = self.db.query(StudentDocument).filter(
            StudentDocument.student_id == self.student_id
        ).all()
        
        # Also check student table for document URLs
        student = self.db.query(Student).filter(Student.id == self.student_id).first()
        if not student:
            raise ValueError(f"Student with ID {self.student_id} not found")
        
        # Helper function to safely get document URL
        def safe_get_doc_url(attr_name):
            """Safely get document URL attribute, returning None if not found"""
            try:
                return getattr(student, attr_name, None)
            except (AttributeError, TypeError):
                return None
        
        # Map document types to student fields - using safe_get_doc_url to handle missing attributes
        doc_mapping = {
            DocumentType.PASSPORT_PAGE: safe_get_doc_url('passport_page_url'),
            DocumentType.PHOTO: safe_get_doc_url('passport_photo_url'),
            DocumentType.DIPLOMA: safe_get_doc_url('highest_degree_diploma_url'),
            DocumentType.TRANSCRIPT: safe_get_doc_url('academic_transcript_url'),
            DocumentType.NON_CRIMINAL: safe_get_doc_url('police_clearance_url'),
            DocumentType.PHYSICAL_EXAM: safe_get_doc_url('physical_examination_form_url'),
            DocumentType.BANK_STATEMENT: safe_get_doc_url('bank_statement_url'),
            DocumentType.CV_RESUME: safe_get_doc_url('cv_resume_url'),
            DocumentType.RECOMMENDATION_LETTER: safe_get_doc_url('recommendation_letter_1_url') or safe_get_doc_url('recommendation_letter_2_url'),
            DocumentType.STUDY_PLAN: safe_get_doc_url('study_plan_url'),
            DocumentType.ENGLISH_PROFICIENCY: safe_get_doc_url('english_proficiency_certificate_url'),
            DocumentType.JW202_JW201: safe_get_doc_url('jw202_jw201_url'),
            DocumentType.GUARANTEE_LETTER: safe_get_doc_url('guarantee_letter_url'),
            DocumentType.BANK_GUARANTOR_LETTER: safe_get_doc_url('bank_guarantor_letter_url'),
        }
        
        # Download documents from R2 to temp files
        temp_dir = tempfile.mkdtemp()
        
        for doc_type, url in doc_mapping.items():
            if url:
                try:
                    # Handle document_type - could be enum or string
                    doc_type_value = doc_type.value if hasattr(doc_type, 'value') else str(doc_type)
                    
                    # Download from R2
                    local_path = os.path.join(temp_dir, f"{doc_type_value}.pdf")
                    self.r2_service.download_file(url, local_path)
                    self.documents[doc_type_value] = local_path
                    logger.info(f"Downloaded {doc_type_value} to {local_path}")
                except Exception as e:
                    doc_type_value = doc_type.value if hasattr(doc_type, 'value') else str(doc_type)
                    logger.warning(f"Failed to download {doc_type_value}: {e}")
        
        # Also check StudentDocument table
        for doc in student_docs:
            if not doc.file_url:
                continue
            
            # Handle document_type - could be enum or string
            doc_type_value = doc.document_type.value if hasattr(doc.document_type, 'value') else str(doc.document_type)
            
            if doc_type_value not in self.documents:
                try:
                    local_path = os.path.join(temp_dir, f"{doc_type_value}_{doc.id}.pdf")
                    self.r2_service.download_file(doc.file_url, local_path)
                    self.documents[doc_type_value] = local_path
                    logger.info(f"Downloaded {doc_type_value} from StudentDocument to {local_path}")
                except Exception as e:
                    logger.warning(f"Failed to download {doc_type_value} from StudentDocument: {e}")
        
        return self.documents
    
    def cleanup(self):
        """Clean up temporary files"""
        for file_path in self.documents.values():
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception as e:
                logger.warning(f"Failed to remove temp file {file_path}: {e}")


class FieldDetector:
    """Detects form fields on the page"""
    
    def __init__(self, page: Page):
        self.page = page
    
    def detect_fields(self) -> List[Dict[str, Any]]:
        """Detect all form fields on the page"""
        fields = []
        
        # Detect input fields
        inputs = self.page.query_selector_all("input")
        for input_elem in inputs:
            field_type = input_elem.get_attribute("type") or "text"
            name = input_elem.get_attribute("name") or ""
            id_attr = input_elem.get_attribute("id") or ""
            placeholder = input_elem.get_attribute("placeholder") or ""
            
            # Get label
            label = self._get_label(input_elem, id_attr, name)
            
            fields.append({
                "type": field_type,
                "name": name,
                "id": id_attr,
                "placeholder": placeholder,
                "label": label,
                "selector": self._get_selector(input_elem, id_attr, name),
                "element": input_elem
            })
        
        # Detect textarea fields
        textareas = self.page.query_selector_all("textarea")
        for textarea in textareas:
            name = textarea.get_attribute("name") or ""
            id_attr = textarea.get_attribute("id") or ""
            placeholder = textarea.get_attribute("placeholder") or ""
            label = self._get_label(textarea, id_attr, name)
            
            fields.append({
                "type": "textarea",
                "name": name,
                "id": id_attr,
                "placeholder": placeholder,
                "label": label,
                "selector": self._get_selector(textarea, id_attr, name),
                "element": textarea
            })
        
        # Detect select fields
        selects = self.page.query_selector_all("select")
        for select in selects:
            name = select.get_attribute("name") or ""
            id_attr = select.get_attribute("id") or ""
            label = self._get_label(select, id_attr, name)
            
            fields.append({
                "type": "select",
                "name": name,
                "id": id_attr,
                "label": label,
                "selector": self._get_selector(select, id_attr, name),
                "element": select
            })
        
        return fields
    
    def _get_label(self, element, id_attr: str, name: str) -> str:
        """Get label for a form field"""
        # Try to find associated label
        if id_attr:
            label_elem = self.page.query_selector(f"label[for='{id_attr}']")
            if label_elem:
                return label_elem.inner_text().strip()
        
        # Try to find parent label
        parent = element.evaluate_handle("el => el.parentElement")
        if parent:
            try:
                parent_tag = parent.evaluate("el => el.tagName")
                if parent_tag and parent_tag.lower() == "label":
                    return parent.inner_text().strip()
            except:
                pass
        
        # Try to find preceding label
        try:
            prev_sibling = element.evaluate_handle("el => el.previousElementSibling")
            if prev_sibling:
                prev_tag = prev_sibling.evaluate("el => el.tagName")
                if prev_tag and prev_tag.lower() == "label":
                    return prev_sibling.inner_text().strip()
        except:
            pass
        
        return ""
    
    def _get_selector(self, element, id_attr: str, name: str) -> str:
        """Get CSS selector for element"""
        if id_attr:
            return f"#{id_attr}"
        elif name:
            return f"input[name='{name}'], textarea[name='{name}'], select[name='{name}']"
        else:
            # Fallback to XPath or other method
            return ""


class FormFillerEngine:
    """Fills form fields with student data"""
    
    def __init__(self, page: Page, student_data: Dict[str, Any], documents: Dict[str, str]):
        self.page = page
        self.student_data = student_data
        self.documents = documents
        self.filled_fields: Dict[str, bool] = {}
        self.uploaded_files: Dict[str, str] = {}
    
    def fill_form(self, fields: List[Dict[str, Any]], portal_override=None) -> Dict[str, Any]:
        """Fill all form fields"""
        for field in fields:
            try:
                if field["type"] == "file":
                    self._fill_file_input(field)
                elif field["type"] == "select":
                    self._fill_select(field, portal_override)
                elif field["type"] in ["text", "email", "tel", "date", "number"]:
                    self._fill_text_input(field, portal_override)
                elif field["type"] == "textarea":
                    self._fill_textarea(field, portal_override)
            except Exception as e:
                logger.warning(f"Failed to fill field {field.get('name', field.get('id', 'unknown'))}: {e}")
        
        return {
            "filled_fields": self.filled_fields,
            "uploaded_files": self.uploaded_files
        }
    
    def _fill_text_input(self, field: Dict[str, Any], portal_override=None):
        """Fill text input field"""
        field_name = field.get("name", "").lower()
        field_id = field.get("id", "").lower()
        label = field.get("label", "").lower()
        placeholder = field.get("placeholder", "").lower()
        
        # Try portal override first
        if portal_override:
            value = portal_override.map_field(field_name, field_id, label, placeholder, self.student_data)
            if value:
                self.page.fill(field["selector"], str(value))
                self.filled_fields[field_name or field_id] = True
                return
        
        # Default mapping logic
        value = self._map_field_to_data(field_name, field_id, label, placeholder)
        if value:
            self.page.fill(field["selector"], str(value))
            self.filled_fields[field_name or field_id] = True
    
    def _fill_textarea(self, field: Dict[str, Any], portal_override=None):
        """Fill textarea field"""
        self._fill_text_input(field, portal_override)
    
    def _fill_select(self, field: Dict[str, Any], portal_override=None):
        """Fill select dropdown"""
        field_name = field.get("name", "").lower()
        field_id = field.get("id", "").lower()
        label = field.get("label", "").lower()
        
        # Try portal override first
        if portal_override:
            value = portal_override.map_select(field_name, field_id, label, self.student_data)
            if value:
                self.page.select_option(field["selector"], str(value))
                self.filled_fields[field_name or field_id] = True
                return
        
        # Default mapping
        value = self._map_field_to_data(field_name, field_id, label, "")
        if value:
            try:
                self.page.select_option(field["selector"], str(value))
                self.filled_fields[field_name or field_id] = True
            except:
                # Try by label
                try:
                    self.page.select_option(field["selector"], label=value)
                    self.filled_fields[field_name or field_id] = True
                except:
                    pass
    
    def _fill_file_input(self, field: Dict[str, Any]):
        """Fill file input field"""
        field_name = field.get("name", "").lower()
        field_id = field.get("id", "").lower()
        label = field.get("label", "").lower()
        
        # Map document type
        doc_type = self._map_document_type(field_name, field_id, label)
        if doc_type and doc_type in self.documents:
            try:
                self.page.set_input_files(field["selector"], self.documents[doc_type])
                self.uploaded_files[doc_type] = "ok"
                logger.info(f"Uploaded {doc_type} to {field_name or field_id}")
            except Exception as e:
                self.uploaded_files[doc_type] = f"error: {str(e)}"
                logger.error(f"Failed to upload {doc_type}: {e}")
        else:
            self.uploaded_files[field_name or field_id] = "missing document or mapping"
    
    def _map_field_to_data(self, field_name: str, field_id: str, label: str, placeholder: str) -> Optional[str]:
        """Map form field to student data"""
        # Combine all identifiers
        combined = f"{field_name} {field_id} {label} {placeholder}".lower()
        
        # Name mappings
        if any(term in combined for term in ["name", "full name", "姓名"]):
            return self.student_data.get("full_name")
        if any(term in combined for term in ["given name", "first name", "名"]):
            return self.student_data.get("given_name")
        if any(term in combined for term in ["family name", "last name", "surname", "姓"]):
            return self.student_data.get("family_name")
        
        # Personal info
        if any(term in combined for term in ["date of birth", "dob", "birth", "出生日期"]):
            return self.student_data.get("date_of_birth")
        if any(term in combined for term in ["gender", "sex", "性别"]):
            return self.student_data.get("gender")
        if any(term in combined for term in ["nationality", "country", "国籍"]):
            return self.student_data.get("nationality")
        
        # Passport
        if any(term in combined for term in ["passport", "护照"]):
            return self.student_data.get("passport_number")
        if any(term in combined for term in ["passport expiry", "passport expiration", "护照有效期"]):
            return self.student_data.get("passport_expiry_date")
        
        # Contact
        if any(term in combined for term in ["email", "e-mail", "邮箱"]):
            return self.student_data.get("email")
        if any(term in combined for term in ["phone", "telephone", "mobile", "电话"]):
            return self.student_data.get("phone")
        if any(term in combined for term in ["wechat", "微信"]):
            return self.student_data.get("wechat_id")
        
        # Address
        if any(term in combined for term in ["address", "地址"]):
            return self.student_data.get("home_address") or self.student_data.get("current_address")
        if any(term in combined for term in ["current country", "residence", "居住国"]):
            return self.student_data.get("current_country")
        
        # Academic
        if any(term in combined for term in ["degree", "学历", "highest degree"]):
            return self.student_data.get("highest_degree")
        if any(term in combined for term in ["institution", "university", "school", "学校"]):
            return self.student_data.get("highest_degree_institution")
        if any(term in combined for term in ["gpa", "cgpa", "成绩"]):
            return self.student_data.get("highest_degree_cgpa")
        
        # Test scores
        if any(term in combined for term in ["hsk", "汉语水平"]):
            return self.student_data.get("hsk_level")
        if any(term in combined for term in ["ielts", "toefl", "english", "英语"]):
            return self.student_data.get("english_test_score")
        
        return None
    
    def _map_document_type(self, field_name: str, field_id: str, label: str) -> Optional[str]:
        """Map file input to document type"""
        combined = f"{field_name} {field_id} {label}".lower()
        
        if any(term in combined for term in ["passport", "护照"]):
            return "passport_page"
        if any(term in combined for term in ["photo", "picture", "照片"]):
            return "photo"
        if any(term in combined for term in ["diploma", "degree", "毕业证"]):
            return "diploma"
        if any(term in combined for term in ["transcript", "成绩单"]):
            return "transcript"
        if any(term in combined for term in ["cv", "resume", "简历"]):
            return "cv_resume"
        if any(term in combined for term in ["police", "clearance", "无犯罪"]):
            return "non_criminal"
        if any(term in combined for term in ["physical", "medical", "体检"]):
            return "physical_exam"
        if any(term in combined for term in ["bank", "statement", "银行"]):
            return "bank_statement"
        if any(term in combined for term in ["recommendation", "推荐信"]):
            return "recommendation_letter"
        if any(term in combined for term in ["study plan", "学习计划"]):
            return "study_plan"
        if any(term in combined for term in ["english", "proficiency", "英语能力"]):
            return "english_proficiency"
        
        return None


class PlaywrightSession:
    """Manages Playwright browser session"""
    
    def __init__(self, headless: bool = False, slow_mo: int = 50):
        self.headless = headless
        self.slow_mo = slow_mo
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
    
    def __enter__(self):
        if not PLAYWRIGHT_AVAILABLE:
            raise ImportError("Playwright is not installed")
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            slow_mo=self.slow_mo
        )
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        # Don't close browser automatically - keep it open for human review
        # The browser will stay open until manually closed
        # Only stop playwright if there was an error
        if exc_type is not None:
            # Error occurred - close browser
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
        # Otherwise, keep browser open for human review
    
    def navigate(self, url: str, timeout: int = 30000):
        """Navigate to URL"""
        self.page.goto(url, wait_until="networkidle", timeout=timeout)
    
    def screenshot(self, path: str):
        """Take screenshot"""
        self.page.screenshot(path=path, full_page=True)
    
    def login(self, username: str, password: str) -> bool:
        """Attempt to login if login fields are detected"""
        try:
            # Look for username/email field
            username_selectors = [
                "input[type='email']",
                "input[name*='user']",
                "input[name*='email']",
                "input[id*='user']",
                "input[id*='email']",
                "input[placeholder*='user']",
                "input[placeholder*='email']",
            ]
            
            password_selectors = [
                "input[type='password']",
                "input[name*='pass']",
                "input[id*='pass']",
            ]
            
            username_field = None
            password_field = None
            
            for selector in username_selectors:
                try:
                    username_field = self.page.query_selector(selector)
                    if username_field:
                        break
                except:
                    continue
            
            for selector in password_selectors:
                try:
                    password_field = self.page.query_selector(selector)
                    if password_field:
                        break
                except:
                    continue
            
            if username_field and password_field:
                username_field.fill(username)
                password_field.fill(password)
                
                # Look for submit button
                submit_selectors = [
                    "button[type='submit']",
                    "input[type='submit']",
                    "button:has-text('Login')",
                    "button:has-text('Sign in')",
                    "button:has-text('登录')",
                ]
                
                for selector in submit_selectors:
                    try:
                        submit_btn = self.page.query_selector(selector)
                        if submit_btn:
                            submit_btn.click()
                            self.page.wait_for_load_state("networkidle")
                            return True
                    except:
                        continue
                
                # Try pressing Enter
                password_field.press("Enter")
                self.page.wait_for_load_state("networkidle")
                return True
            
            return False
        except Exception as e:
            logger.warning(f"Login attempt failed: {e}")
            return False


class ApplicationAutomation:
    """Main automation orchestrator"""
    
    def __init__(self, db: Session, r2_service: R2Service):
        self.db = db
        self.r2_service = r2_service
        self.logs: List[str] = []
    
    def run(
        self,
        student_id: int,
        apply_url: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        portal_override=None
    ) -> Dict[str, Any]:
        """Run the automation"""
        if not PLAYWRIGHT_AVAILABLE:
            raise ImportError(
                "Playwright is not installed. Please install it with: "
                "pip install playwright==1.40.0 && playwright install chromium"
            )
        
        start_time = datetime.now()
        
        try:
            # Load student data
            logger.info(f"Loading student data for student_id={student_id}")
            student_loader = StudentLoader(self.db, student_id)
            student_data = student_loader.load()
            self._log(f"Loaded student data: {student_data.get('full_name')}")
            
            # Load documents
            logger.info("Loading student documents")
            doc_loader = DocumentLoader(self.db, student_id, self.r2_service)
            documents = doc_loader.load()
            self._log(f"Loaded {len(documents)} documents")
            
            # Run Playwright automation - browser stays open after completion
            session = PlaywrightSession(headless=False, slow_mo=50)
            session.__enter__()
            
            try:
                # Navigate to URL
                self._log(f"Navigating to {apply_url}")
                session.navigate(apply_url)
                
                # Always wait for manual login (don't attempt automatic login)
                self._log("Waiting for manual login...")
                self._log("Please login manually in the browser window.")
                
                # Show alert asking user to login
                session.page.evaluate("""
                    alert('Please login manually. After logging in, navigate to the application form. The automation will automatically fill fields when it detects the form. Click OK to continue.');
                """)
                
                # Wait for login completion (URL change or password field disappears)
                import time
                initial_url = session.page.url
                initial_has_password_field = session.page.query_selector("input[type='password']") is not None
                
                max_wait = 600  # 10 minutes for login
                waited = 0
                login_detected = False
                
                while waited < max_wait:
                    current_url = session.page.url
                    has_password = session.page.query_selector("input[type='password']") is not None
                    
                    if current_url != initial_url or (initial_has_password_field and not has_password):
                        self._log("Login detected - waiting for page to load...")
                        session.page.wait_for_load_state("networkidle", timeout=15000)
                        login_detected = True
                        break
                    
                    time.sleep(2)
                    waited += 2
                
                if not login_detected:
                    self._log("Timeout waiting for login. Continuing anyway...")
                
                # After login, try to navigate to application form (if portal override exists)
                # Otherwise, wait for user to navigate manually
                if portal_override:
                    self._log("Attempting to navigate to application form using portal override...")
                    try:
                        if portal_override.navigate_to_application_form(session.page):
                            self._log("Navigated to application form")
                            session.page.wait_for_load_state("networkidle", timeout=15000)
                        else:
                            self._log("Portal override could not navigate. Waiting for manual navigation...")
                            session.page.evaluate("""
                                alert('Please navigate to the application form. The automation will fill fields automatically when detected.');
                            """)
                    except Exception as e:
                        self._log(f"Portal navigation failed: {e}. Waiting for manual navigation...")
                        session.page.evaluate("""
                            alert('Please navigate to the application form. The automation will fill fields automatically when detected.');
                        """)
                else:
                    self._log("No portal override. Waiting for you to navigate to the application form...")
                    session.page.evaluate("""
                        alert('Please navigate to the application form. The automation will fill fields automatically when detected.');
                    """)
                
                # Continuously monitor for form fields and fill them when detected
                self._log("Monitoring for application form fields...")
                field_detector = FieldDetector(session.page)
                form_filler = FormFillerEngine(session.page, student_data, documents)
                
                max_form_wait = 600  # 10 minutes to find and fill form
                form_wait_start = time.time()
                fields_filled = False
                fill_result = {"filled_fields": {}, "uploaded_files": {}}
                
                while (time.time() - form_wait_start) < max_form_wait:
                    # Detect form fields
                    fields = field_detector.detect_fields()
                    
                    if len(fields) > 0:
                        self._log(f"Detected {len(fields)} form fields - filling form...")
                        
                        # Fill form
                        fill_result = form_filler.fill_form(fields, portal_override)
                        filled_count = len(fill_result['filled_fields'])
                        uploaded_count = len([k for k, v in fill_result['uploaded_files'].items() if v == 'ok'])
                        
                        self._log(f"Filled {filled_count} fields")
                        self._log(f"Uploaded {uploaded_count} files")
                        
                        if filled_count > 0 or uploaded_count > 0:
                            fields_filled = True
                            # Show success message
                            session.page.evaluate(f"""
                                alert('Automation filled {filled_count} fields and uploaded {uploaded_count} files. Please review and add any missing information, then submit manually. The browser will remain open.');
                            """)
                            break
                    else:
                        # Wait a bit before checking again
                        time.sleep(3)
                
                if not fields_filled:
                    self._log("WARNING: No form fields were filled. The application form may not have been reached yet.")
                    session.page.evaluate("""
                        alert('No form fields detected. Please ensure you are on the application form page. The browser will remain open for manual completion.');
                    """)
                
                # Cleanup documents (but keep browser open)
                doc_loader.cleanup()
                
                # Return result - browser stays open for human review
                self._log("Automation completed. Browser window remains open for review.")
                self._log("IMPORTANT: The browser will stay open. Please review the form, add missing fields, and submit manually.")
                
                # Don't call __exit__ - keep browser open
                # Store session reference so it doesn't get garbage collected
                # Note: In production, you might want to store this in a global dict or return session info
                
                return {
                    "status": "ok",
                    "log": "\n".join(self.logs),
                    "filled_fields": fill_result.get("filled_fields", {}),
                    "uploaded_files": fill_result.get("uploaded_files", {}),
                    "duration_seconds": (datetime.now() - start_time).total_seconds(),
                    "message": "Automation completed. Browser window is open for review. Please check the form, add missing fields, and submit manually."
                }
            
            except Exception as e:
                # On error, log but keep browser open for debugging
                self._log(f"Error during automation: {e}")
                logger.error(f"Automation error: {e}", exc_info=True)
                # Don't close browser on error - let user see what happened
                raise
        
        except Exception as e:
            logger.error(f"Automation failed: {e}", exc_info=True)
            self._log(f"ERROR: {str(e)}")
            return {
                "status": "error",
                "log": "\n".join(self.logs),
                "error": str(e)
            }
    
    def _log(self, message: str):
        """Add log message"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_msg = f"[{timestamp}] {message}"
        self.logs.append(log_msg)
        logger.info(message)

