"""
Document Verification Service using OpenAI Vision API
Verifies documents for China university admission requirements
"""
from openai import OpenAI
from app.config import settings
from typing import Dict, Any, Optional
import json
import base64
import requests

class DocumentVerificationService:
    def __init__(self):
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = "gpt-4.1-mini"  # Vision model
    
    def verify_document(
        self, 
        file_url: str, 
        doc_type: str,
        file_content: Optional[bytes] = None
    ) -> Dict[str, Any]:
        """
        Verify a document using OpenAI Vision API
        
        Args:
            file_url: URL to the document image/file
            doc_type: Type of document (passport, diploma, transcript, etc.)
            file_content: Optional file content bytes (if available)
        
        Returns:
            {
                "status": "ok" | "blurry" | "fake" | "incomplete",
                "reason": "...",
                "extracted": {...}
            }
        """
        print(f"\n{'='*80}")
        print(f"🔍 DOCUMENT VERIFICATION STARTED")
        print(f"{'='*80}")
        print(f"📄 Document Type: {doc_type}")
        print(f"🔗 File URL: {file_url}")
        print(f"📦 File Content Size: {len(file_content) if file_content else 0} bytes")
        print(f"🤖 Using Model: {self.model}")
        # Get image content
        if file_content:
            # Convert bytes to base64
            image_base64 = base64.b64encode(file_content).decode('utf-8')
            image_url = f"data:image/jpeg;base64,{image_base64}"
        else:
            # Download image from URL
            try:
                response = requests.get(file_url, timeout=30)
                response.raise_for_status()
                image_base64 = base64.b64encode(response.content).decode('utf-8')
                # Determine content type
                content_type = response.headers.get('content-type', 'image/jpeg')
                if 'png' in content_type:
                    image_url = f"data:image/png;base64,{image_base64}"
                elif 'pdf' in content_type:
                    # For PDFs, we might need to convert to image first
                    # For now, treat as image
                    image_url = f"data:application/pdf;base64,{image_base64}"
                else:
                    image_url = f"data:image/jpeg;base64,{image_base64}"
            except Exception as e:
                return {
                    "status": "incomplete",
                    "reason": f"Failed to download document: {str(e)}",
                    "extracted": {}
                }
        
        # Build system prompt based on document type
        system_prompt = self._get_verification_prompt(doc_type)
        
        print(f"\n📋 VERIFICATION PROMPT:")
        print(f"{'-'*80}")
        print(system_prompt[:500] + "..." if len(system_prompt) > 500 else system_prompt)
        print(f"{'-'*80}\n")
        
        # Create messages for vision API
        user_message_text = f"Please verify this {doc_type} document for China university admission requirements. Analyze the image and provide a structured JSON response."
        
        messages = [
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": user_message_text
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_url
                        }
                    }
                ]
            }
        ]
        
        print(f"💬 User Message: {user_message_text}")
        print(f"🖼️  Image Format: {image_url[:50]}... (base64 encoded)")
        print(f"\n🚀 Calling OpenAI Vision API...\n")
        
        try:
            # Call OpenAI Vision API
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.3,
                max_tokens=2000
            )
            
            print(f"✅ OpenAI API Response Received")
            print(f"📊 Tokens Used: {response.usage.total_tokens if hasattr(response, 'usage') else 'N/A'}")
            
            # Parse response
            content = response.choices[0].message.content
            
            print(f"\n📥 RAW OPENAI RESPONSE:")
            print(f"{'-'*80}")
            print(content)
            print(f"{'-'*80}\n")
            
            # Try to extract JSON from response
            try:
                # Look for JSON in the response
                if "```json" in content:
                    json_str = content.split("```json")[1].split("```")[0].strip()
                elif "```" in content:
                    json_str = content.split("```")[1].split("```")[0].strip()
                else:
                    json_str = content
                
                result = json.loads(json_str)
                
                # Ensure required fields
                if "status" not in result:
                    result["status"] = "incomplete"
                if "reason" not in result:
                    result["reason"] = "Verification completed but no reason provided"
                if "extracted" not in result:
                    result["extracted"] = {}
                
                print(f"\n✅ VERIFICATION RESULT:")
                print(f"{'='*80}")
                print(f"📊 Status: {result['status']}")
                print(f"📝 Reason: {result['reason']}")
                print(f"📋 Extracted Data: {json.dumps(result.get('extracted', {}), indent=2)}")
                print(f"{'='*80}\n")
                
                return result
                
            except json.JSONDecodeError:
                # If JSON parsing fails, try to extract status from text
                status = "incomplete"
                reason = content
                
                # Try to detect status from text
                content_lower = content.lower()
                if "ok" in content_lower or "valid" in content_lower or "acceptable" in content_lower:
                    status = "ok"
                elif "blurry" in content_lower or "unclear" in content_lower or "low quality" in content_lower:
                    status = "blurry"
                elif "fake" in content_lower or "forged" in content_lower or "fraudulent" in content_lower:
                    status = "fake"
                elif "incomplete" in content_lower or "missing" in content_lower:
                    status = "incomplete"
                
                return {
                    "status": status,
                    "reason": reason,
                    "extracted": {}
                }
                
        except Exception as e:
            print(f"\n❌ VERIFICATION ERROR:")
            print(f"{'='*80}")
            print(f"Error Type: {type(e).__name__}")
            print(f"Error Message: {str(e)}")
            print(f"{'='*80}\n")
            return {
                "status": "incomplete",
                "reason": f"Verification error: {str(e)}",
                "extracted": {}
            }
    
    def _get_verification_prompt(self, doc_type: str) -> str:
        """Get verification prompt based on document type"""
        
        base_prompt = """You are an expert document verification specialist for China university admission applications. Your task is to verify documents submitted by international students.

VERIFICATION CRITERIA:
1. **Readability**: Is the document clear, sharp, and all text readable? Check for blur, low resolution, glare, or poor scanning quality.
2. **Completeness**: Does the document contain all required information? Are all pages present? Are seals/stamps visible?
3. **Authenticity**: Look for signs of forgery, tampering, or manipulation. Check for:
   - Inconsistent fonts or formatting
   - Suspicious stamps or seals
   - Altered dates or information
   - Mismatched signatures
   - Unusual paper quality or printing
4. **Notarization**: If required, is the document properly notarized or certified?
5. **Data Extraction**: Extract key information from the document.

**SPECIAL NOTE FOR PHOTOS**: For passport-size photos, the background MUST be pure white or off-white. Any colored background, patterns, shadows, or non-white backgrounds are NOT acceptable and must result in status "incomplete" with a clear reason stating the background requirement violation.

RESPONSE FORMAT:
You MUST respond with a valid JSON object in this exact format:
{
  "status": "ok" | "blurry" | "fake" | "incomplete",
  "reason": "Detailed explanation of verification result",
  "extracted": {
    // Document-specific extracted data
  }
}

STATUS MEANINGS:
- "ok": Document is clear, complete, authentic, and acceptable
- "blurry": Document is unclear, low quality, or hard to read
- "fake": Signs of forgery, tampering, or manipulation detected
- "incomplete": Missing information, pages, or required elements

Be strict but fair. Only mark as "ok" if the document truly meets all requirements."""

        # Add document-specific requirements
        doc_specific = {
            "passport": """
PASSPORT SPECIFIC REQUIREMENTS:
- All corners and edges must be visible
- Photo must be clear and match the holder
- Passport number, name, date of birth, nationality, expiry date must be readable
- MRZ (Machine Readable Zone) should be visible if present
- No glare or reflections on the photo page
- Document should not be expired or expiring soon
- Extract the following information in the "extracted" field as JSON:
  {
    "passport_number": "string (e.g., A12345678)",
    "name": "string (full name as shown on passport)",
    "given_name": "string (first name)",
    "family_name": "string (last name/surname)",
    "father_name": "string (if visible on passport)",
    "date_of_birth": "YYYY-MM-DD format",
    "nationality": "string (country name)",
    "expiry_date": "YYYY-MM-DD format",
    "issuing_country": "string (country that issued the passport)"
  }
- If any field is not visible or readable, use null for that field
""",
            "diploma": """
DIPLOMA SPECIFIC REQUIREMENTS:
- Full document must be visible (all corners)
- University name, student name, degree type, graduation date must be clear
- Official seals/stamps must be visible and authentic-looking
- Signature of authority must be present
- Extract: institution_name, student_name, degree_type, graduation_date, major
""",
            "transcript": """
TRANSCRIPT SPECIFIC REQUIREMENTS:
- All pages must be present and visible
- Grades and courses must be readable
- Official seals/stamps must be visible
- Institution name and student name must be clear
- Extract: institution_name, student_name, courses, grades, gpa, total_credits
""",
            "bank_statement": """
BANK STATEMENT SPECIFIC REQUIREMENTS:
- Account holder name must match student or guarantor
- Balance must be clearly visible
- Statement must be recent (within 6 months)
- Bank name and logo must be visible
- Extract: account_holder_name, bank_name, balance, currency, statement_date
""",
            "police_clearance": """
POLICE CLEARANCE SPECIFIC REQUIREMENTS:
- Official seal/stamp must be visible
- Issuing authority name must be clear
- Issue date must be recent (usually within 6 months)
- No criminal record statement must be clear
- Extract: issuing_authority, issue_date, expiry_date, certificate_number
""",
            "physical_exam": """
PHYSICAL EXAMINATION FORM SPECIFIC REQUIREMENTS:
- All sections must be filled out
- Doctor's signature and stamp must be present
- Examination date must be recent (within 6 months)
- All required tests must be completed
- Extract: examination_date, doctor_name, hospital_name, test_results
""",
            "photo": """
PASSPORT SIZE PHOTO SPECIFIC REQUIREMENTS (Colored 2-inch bare-headed photo):
- **CRITICAL: Background MUST be pure white or off-white (no colors, patterns, borders, or shadows)**
- **CRITICAL: Photo must be colored (not grayscale or black and white)**
- **CRITICAL: Width must be less than height (portrait orientation)**
- **CRITICAL: Head must account for approximately 2/3 (66-70%) of the photo size**
- Photo must be passport size (typically 35mm x 45mm or 2x2 inches)
- Face must be clearly visible, centered, and take up 66-70% of the photo height
- **Bare-headed: No headwear (except for religious reasons, must not obscure face)**
- Neutral expression (no smiling, mouth closed)
- Eyes must be open and clearly visible
- No glasses (unless medically necessary, must not obscure eyes)
- Recent photo (taken within last 6 months)
- High quality, sharp, and clear
- Proper lighting with no shadows on face or background
- Professional appearance
- Extract: photo_quality, background_color, face_visibility, photo_size, head_proportion, is_colored, orientation, meets_requirements
""",
            "passport_photo": """
PASSPORT SIZE PHOTO SPECIFIC REQUIREMENTS (Colored 2-inch bare-headed photo):
- **CRITICAL: Background MUST be pure white or off-white (no colors, patterns, borders, or shadows)**
- **CRITICAL: Photo must be colored (not grayscale or black and white)**
- **CRITICAL: Width must be less than height (portrait orientation)**
- **CRITICAL: Head must account for approximately 2/3 (66-70%) of the photo size**
- Photo must be passport size (typically 35mm x 45mm or 2x2 inches)
- Face must be clearly visible, centered, and take up 66-70% of the photo height
- **Bare-headed: No headwear (except for religious reasons, must not obscure face)**
- Neutral expression (no smiling, mouth closed)
- Eyes must be open and clearly visible
- No glasses (unless medically necessary, must not obscure eyes)
- Recent photo (taken within last 6 months)
- High quality, sharp, and clear
- Proper lighting with no shadows on face or background
- Professional appearance
- Extract: photo_quality, background_color, face_visibility, photo_size, head_proportion, is_colored, orientation, meets_requirements
"""
        }
        
        specific_requirements = doc_specific.get(doc_type.lower(), "")
        
        # If no specific requirements found, try alternative names
        if not specific_requirements:
            # Try alternative document type names
            alt_names = {
                "passport_page": "passport",
                "passport_photo": "photo",
                "passport_size_photo": "photo"
            }
            alt_type = alt_names.get(doc_type.lower(), doc_type.lower())
            specific_requirements = doc_specific.get(alt_type, "")
        
        return base_prompt + specific_requirements

