# Application Automation Setup Guide

## Overview

The Application Automation system uses Playwright to semi-automatically fill university application forms with student data. It does NOT auto-submit - human review is required.

## Installation

1. Install Playwright:
```bash
cd backend
pip install playwright==1.40.0
playwright install chromium
```

2. Ensure all dependencies are installed:
```bash
pip install -r requirements.txt
```

## Usage

### From Admin Dashboard

1. Navigate to Admin Dashboard → Automation tab
2. Click "New Automation" or use "Auto-Fill" button on an application
3. Fill in:
   - **Student ID**: The student's database ID
   - **Application URL**: The university's application form URL
   - **Username/Password** (optional): Portal login credentials
   - **Portal Type** (optional): Pre-configured portal (HIT, Beihang, BNUZ)
4. Click "Run Automation"
5. Review the logs and screenshot
6. **Manually submit** the form in the browser window

### API Endpoint

```bash
POST /api/admin/automation/run
{
  "student_id": 123,
  "apply_url": "https://university.edu/apply",
  "username": "optional",
  "password": "optional",
  "portal_type": "hit"  // optional: "hit", "beihang", "bnuz"
}
```

## How It Works

1. **StudentLoader**: Fetches student data from PostgreSQL
2. **DocumentLoader**: Downloads student documents from Cloudflare R2 to temp files
3. **PlaywrightSession**: Opens a browser (non-headless for debugging)
4. **FieldDetector**: Detects all form fields on the page
5. **FormFillerEngine**: Maps and fills fields with student data
6. **Document Upload**: Uploads documents to file input fields
7. **Screenshot**: Takes a full-page screenshot for review
8. **Alert**: Shows "Automation completed. Please review and click Submit manually."

## Portal-Specific Overrides

Create custom portal implementations in `backend/app/services/portals/`:

```python
# portals/custom.py
from .base import PortalBase

class CustomPortal(PortalBase):
    def navigate_to_application_form(self, page):
        # Custom navigation logic
        pass
    
    def map_field(self, field_name, field_id, label, placeholder, student_data):
        # Custom field mapping
        pass
```

Then register in `portals/__init__.py` and `admin.py`.

## Field Mapping

The system uses heuristic mapping to match form fields to student data:

- **Name fields**: "name", "full name", "姓名" → student full name
- **Email**: "email", "e-mail", "邮箱" → student email
- **Passport**: "passport", "护照" → passport number
- **Date of Birth**: "dob", "birth", "出生日期" → date of birth
- **Documents**: "passport", "transcript", "diploma", etc. → corresponding document files

## Safety Features

- **No Auto-Submit**: Form is never automatically submitted
- **Human Review Required**: Alert shown, screenshot saved
- **Error Handling**: Continues even if some fields fail
- **Logging**: Full timestamped logs of all actions
- **Screenshot**: Full-page screenshot for verification

## Troubleshooting

1. **Browser doesn't open**: Check Playwright installation (`playwright install chromium`)
2. **Documents not uploading**: Check R2 credentials and file URLs
3. **Fields not filling**: Check field labels/names match mapping logic
4. **Login fails**: Verify credentials and portal structure

## Notes

- Browser runs in **non-headless mode** for debugging
- All temp files are cleaned up after automation
- Screenshots are saved to R2 and local temp directory
- Logs include all actions with timestamps

