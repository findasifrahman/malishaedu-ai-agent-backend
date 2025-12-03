from typing import Dict, Optional
import re
from datetime import datetime
import pytesseract
from pdf2image import convert_from_bytes
from PIL import Image
import io

class DocumentParser:
    """Parse documents, especially passports"""
    
    @staticmethod
    def parse_passport(file_content: bytes, filename: str) -> Dict:
        """Extract information from passport image/PDF"""
        extracted_data = {
            "passport_number": None,
            "name": None,
            "date_of_birth": None,
            "nationality": None,
            "expiry_date": None,
            "raw_text": ""
        }
        
        try:
            # Convert PDF to image if needed
            if filename.lower().endswith('.pdf'):
                images = convert_from_bytes(file_content)
                text = ""
                for image in images:
                    text += pytesseract.image_to_string(image, lang='eng')
            else:
                # Assume it's an image
                image = Image.open(io.BytesIO(file_content))
                text = pytesseract.image_to_string(image, lang='eng')
            
            extracted_data["raw_text"] = text
            
            # Extract passport number (usually alphanumeric, 6-9 characters)
            passport_match = re.search(r'[A-Z]{1,2}[0-9]{6,9}', text.upper())
            if passport_match:
                extracted_data["passport_number"] = passport_match.group()
            
            # Extract name (look for patterns like "Surname/Given Name" or "Name:")
            name_patterns = [
                r'Name[:\s]+([A-Z\s/]+)',
                r'([A-Z]{2,}\s+[A-Z]{2,})',
                r'Surname[:\s]+([A-Z]+)',
            ]
            for pattern in name_patterns:
                match = re.search(pattern, text.upper())
                if match:
                    extracted_data["name"] = match.group(1).strip()
                    break
            
            # Extract date of birth (DD.MM.YYYY or DD/MM/YYYY)
            dob_patterns = [
                r'(\d{2}[./]\d{2}[./]\d{4})',
                r'DOB[:\s]+(\d{2}[./]\d{2}[./]\d{4})',
            ]
            for pattern in dob_patterns:
                match = re.search(pattern, text)
                if match:
                    date_str = match.group(1)
                    try:
                        extracted_data["date_of_birth"] = datetime.strptime(
                            date_str.replace('.', '/'), '%d/%m/%Y'
                        ).isoformat()
                    except:
                        pass
                    break
            
            # Extract nationality
            nationality_match = re.search(r'Nationality[:\s]+([A-Z]+)', text.upper())
            if nationality_match:
                extracted_data["nationality"] = nationality_match.group(1).strip()
            
            # Extract expiry date
            expiry_patterns = [
                r'Expiry[:\s]+(\d{2}[./]\d{2}[./]\d{4})',
                r'Valid until[:\s]+(\d{2}[./]\d{2}[./]\d{4})',
            ]
            for pattern in expiry_patterns:
                match = re.search(pattern, text)
                if match:
                    date_str = match.group(1)
                    try:
                        extracted_data["expiry_date"] = datetime.strptime(
                            date_str.replace('.', '/'), '%d/%m/%Y'
                        ).isoformat()
                    except:
                        pass
                    break
        
        except Exception as e:
            print(f"Error parsing passport: {e}")
            extracted_data["error"] = str(e)
        
        return extracted_data
    
    @staticmethod
    def extract_text_from_pdf(file_content: bytes) -> str:
        """Extract text from PDF"""
        try:
            from PyPDF2 import PdfReader
            pdf_reader = PdfReader(io.BytesIO(file_content))
            text = ""
            for page in pdf_reader.pages:
                text += page.extract_text()
            return text
        except Exception as e:
            print(f"Error extracting PDF text: {e}")
            return ""
    
    @staticmethod
    def extract_text_from_docx(file_content: bytes) -> str:
        """Extract text from DOCX"""
        try:
            from docx import Document
            doc = Document(io.BytesIO(file_content))
            text = ""
            for paragraph in doc.paragraphs:
                text += paragraph.text + "\n"
            return text
        except Exception as e:
            print(f"Error extracting DOCX text: {e}")
            return ""

