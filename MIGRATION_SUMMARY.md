# Database Migration Summary for AdmissionAgent

## Overview
This document summarizes the database schema changes made to support the enhanced AdmissionAgent prompt requirements.

## Changes Made

### 1. Student Model Enhancements

#### Added COVA (China Visa Application) Fields:
- `home_address` (TEXT) - Permanent home address
- `current_address` (TEXT) - Current residence address
- `emergency_contact_name` (VARCHAR) - Emergency contact person name
- `emergency_contact_phone` (VARCHAR) - Emergency contact phone number
- `emergency_contact_relationship` (VARCHAR) - Relationship to student (e.g., "Father", "Mother", "Spouse")
- `education_history` (JSONB) - JSON array of education records
- `employment_history` (JSONB) - JSON array of employment records
- `family_members` (JSONB) - JSON array of family member information
- `planned_arrival_date` (TIMESTAMP WITH TIME ZONE) - When student plans to arrive in China
- `intended_address_china` (TEXT) - Usually university dorm address
- `previous_visa_china` (BOOLEAN) - Has student had a Chinese visa before?
- `previous_visa_details` (TEXT) - Details about previous visa if any
- `previous_travel_to_china` (BOOLEAN) - Has student traveled to China before?
- `previous_travel_details` (TEXT) - Details about previous travel

#### Added Document Field:
- `study_plan_url` (VARCHAR) - Study plan / motivation letter URL

#### Fixed Missing Fields:
- `phone` (VARCHAR) - Already existed in model, now in migration
- `email` (VARCHAR) - Already existed in model, now in migration
- `date_of_birth` (TIMESTAMP WITH TIME ZONE) - Already existed in model, now in migration
- `passport_number` (VARCHAR) - Already existed in model, now in migration

### 2. Students Router Updates

#### Fixed Field Name Mismatches:
- Changed `passport_name` → `full_name` / `given_name` + `family_name`
- Changed `nationality` → `country_of_citizenship`
- Changed `passport_expiry` → `passport_expiry_date`

#### Enhanced StudentProfile Model:
- Added all basic identification fields
- Added passport information fields
- Added application intent fields
- Added COVA information fields

#### Updated Endpoints:
- `GET /api/students/me` - Now returns all student fields including COVA data
- `PUT /api/students/me` - Now accepts and updates all student fields including COVA data

### 3. Migration Scripts

#### Created:
- `migrate_cova_fields.py` - Adds COVA-related fields to students table
- `migrate_missing_columns.py` - Adds missing basic fields (phone, email, date_of_birth, passport_number)

#### Updated:
- `migrate_new_tables.py` - Now includes all new fields in the migration

## Running Migrations

### Option 1: Run Individual Migration Scripts
```bash
cd backend
python migrate_missing_columns.py
python migrate_cova_fields.py
```

### Option 2: Run Complete Migration
```bash
cd backend
python migrate_new_tables.py
```

### Option 3: Manual SQL (if you have database access)
```sql
-- Add missing basic fields
ALTER TABLE students ADD COLUMN IF NOT EXISTS phone VARCHAR;
ALTER TABLE students ADD COLUMN IF NOT EXISTS email VARCHAR;
ALTER TABLE students ADD COLUMN IF NOT EXISTS date_of_birth TIMESTAMP WITH TIME ZONE;
ALTER TABLE students ADD COLUMN IF NOT EXISTS passport_number VARCHAR;

-- Add study plan field
ALTER TABLE students ADD COLUMN IF NOT EXISTS study_plan_url VARCHAR;

-- Add COVA fields
ALTER TABLE students ADD COLUMN IF NOT EXISTS home_address TEXT;
ALTER TABLE students ADD COLUMN IF NOT EXISTS current_address TEXT;
ALTER TABLE students ADD COLUMN IF NOT EXISTS emergency_contact_name VARCHAR;
ALTER TABLE students ADD COLUMN IF NOT EXISTS emergency_contact_phone VARCHAR;
ALTER TABLE students ADD COLUMN IF NOT EXISTS emergency_contact_relationship VARCHAR;
ALTER TABLE students ADD COLUMN IF NOT EXISTS education_history JSONB;
ALTER TABLE students ADD COLUMN IF NOT EXISTS employment_history JSONB;
ALTER TABLE students ADD COLUMN IF NOT EXISTS family_members JSONB;
ALTER TABLE students ADD COLUMN IF NOT EXISTS planned_arrival_date TIMESTAMP WITH TIME ZONE;
ALTER TABLE students ADD COLUMN IF NOT EXISTS intended_address_china TEXT;
ALTER TABLE students ADD COLUMN IF NOT EXISTS previous_visa_china BOOLEAN DEFAULT FALSE;
ALTER TABLE students ADD COLUMN IF NOT EXISTS previous_visa_details TEXT;
ALTER TABLE students ADD COLUMN IF NOT EXISTS previous_travel_to_china BOOLEAN DEFAULT FALSE;
ALTER TABLE students ADD COLUMN IF NOT EXISTS previous_travel_details TEXT;
```

## AdmissionAgent Prompt Requirements Supported

All fields mentioned in the AdmissionAgent prompt are now supported:

✅ **Profile Understanding**: All student profile fields (country, DOB, HSK, CSCA, target programs)
✅ **Program-Specific Guidance**: ProgramIntake fields (deadline, tuition, documents_required, scholarship_info, notes)
✅ **Document Guidance**: All document URL fields mapped correctly
✅ **COVA Awareness**: All COVA-related fields for visa application preparation
✅ **Document Generation**: study_plan_url field for generated study plans
✅ **Passport Validation**: passport_number, passport_expiry_date, passport_scanned_url fields
✅ **Consistency Checks**: All fields needed for profile vs passport comparison

## Next Steps

1. Run the migration scripts to add the new fields to your database
2. Test the AdmissionAgent to ensure it can access all required fields
3. Update frontend forms to collect COVA information when students reach VISA_PROCESSING stage
4. Test document upload and validation flows

