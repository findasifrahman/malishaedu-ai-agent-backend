# Production-Safe Data Ingestion Pipeline

## Overview

This document describes the new production-safe data ingestion pipeline that replaces the previous LLM-generated SQL approach. The new system ensures:

1. **LLM only extracts structured data** into JSON (no SQL generation)
2. **All SQL is generated deterministically** by backend code
3. **Database identity rules are defined in code**, not inferred
4. **Transactional safety** with proper error handling
5. **No silent failures** - all operations are validated

## Architecture

### Components

1. **DocumentExtractionService** (`backend/app/services/document_extraction_service.py`)
   - Extracts text from PDF/DOCX/TXT files
   - Uses LLM to extract structured data into JSON
   - Outputs strict JSON schema matching `ExtractedData` Pydantic model

2. **DataIngestionService** (`backend/app/services/data_ingestion_service.py`)
   - Validates extracted JSON
   - Normalizes enums (degree_level, teaching_language, intake_term)
   - Resolves foreign keys deterministically
   - Executes INSERT/UPDATE logic using SQLAlchemy ORM
   - Defines identity rules in code

3. **Pydantic Schemas** (`backend/app/schemas/document_import.py`)
   - Defines strict JSON schema for extraction
   - Validates data before ingestion
   - Provides type safety

## Identity Rules (Defined in Code)

These rules determine how entities are matched for upserts:

### University
- **Identity**: `name` (case-insensitive)
- **Resolution**: `WHERE lower(name) = lower(:name)`

### Major
- **Identity**: `(university_id, lower(name), degree_level, teaching_language)`
- **Resolution**: Exact match on all four fields
- **Code Location**: `DataIngestionService._process_major()`

### ProgramIntake
- **Identity**: `(major_id, intake_term, intake_year)`
- **Resolution**: Exact match on all three fields
- **Code Location**: `DataIngestionService._process_program_intake()`

### ProgramDocument
- **Identity**: `(program_intake_id, name)`
- **Resolution**: Exact match on both fields
- **Code Location**: `DataIngestionService._process_documents()`

### Scholarship
- **Identity**: `name` (case-insensitive, global)
- **Resolution**: `WHERE lower(name) = lower(:name)`
- **Code Location**: `DataIngestionService._process_scholarships()`

### ProgramIntakeScholarship (Link)
- **Identity**: `(program_intake_id, scholarship_id)`
- **Resolution**: Exact match on both fields
- **Code Location**: `DataIngestionService._process_scholarships()`

## API Endpoints

### 1. Start Data Extraction
```
POST /api/admin/document-import/extract-data-start
```
- Uploads document file
- Starts background extraction job
- Returns `job_id` immediately

**Response:**
```json
{
  "job_id": "uuid",
  "status": "processing",
  "message": "Data extraction started..."
}
```

### 2. Get Extraction Status
```
GET /api/admin/document-import/extract-data-status/{job_id}
```
- Polls extraction job status
- Returns extracted JSON when complete

**Response:**
```json
{
  "status": "completed",
  "progress": "Complete",
  "result": {
    "extracted_data": { ... },
    "document_text_preview": "..."
  }
}
```

### 3. Ingest Extracted Data
```
POST /api/admin/document-import/ingest-data
```
- Takes extracted JSON
- Validates and ingests into database
- Returns counts and errors

**Request:**
```json
{
  "extracted_data": {
    "university_name": "...",
    "majors": [ ... ],
    "errors": [ ... ]
  }
}
```

**Response:**
```json
{
  "success": true,
  "message": "Data ingested successfully",
  "counts": {
    "majors_inserted": 2,
    "majors_updated": 0,
    "program_intakes_inserted": 2,
    "program_intakes_updated": 0,
    "documents_inserted": 26,
    "documents_updated": 0,
    "scholarships_inserted": 3,
    "scholarships_updated": 0,
    "links_inserted": 6
  },
  "errors": []
}
```

## Execution Flow

1. **Upload Document**
   - Admin uploads PDF/DOCX/TXT file
   - Frontend calls `/extract-data-start`
   - Backend returns `job_id` immediately

2. **Extract Data (Background)**
   - Backend extracts text from document
   - LLM extracts structured data into JSON
   - JSON is validated against Pydantic schema
   - Result stored in `extraction_jobs[job_id]`

3. **Review Data (Frontend)**
   - Frontend polls `/extract-data-status/{job_id}`
   - Displays extracted JSON for admin review
   - Admin can edit JSON if needed

4. **Confirm Insert**
   - Admin clicks "Confirm Insert" button
   - Frontend calls `/ingest-data` with extracted JSON
   - Backend validates and ingests data

5. **Database Transaction**
   - All inserts/updates happen in single transaction
   - Identity rules determine upsert behavior
   - Transaction commits or rolls back on error

6. **Return Results**
   - Backend returns counts of inserted/updated rows
   - Any errors are included in response
   - Frontend displays success/error message

## Error Handling

### Extraction Errors
- If LLM fails to extract data, error is stored in `extraction_jobs[job_id].error`
- If JSON is invalid, validation error is returned
- Errors are included in `extracted_data.errors` array

### Ingestion Errors
- If university not found, transaction aborts with error
- If critical entity (program_intake) has zero inserts/updates, warning is added
- All errors are returned in response `errors` array
- Transaction rolls back on any exception

## Migration from Old System

### Old Endpoints (DEPRECATED)
The following endpoints are kept for backward compatibility but should not be used:
- `POST /api/admin/document-import/generate-sql`
- `POST /api/admin/document-import/generate-sql-start`
- `GET /api/admin/document-import/generate-sql-status/{job_id}`
- `POST /api/admin/document-import/execute-sql`

### New Endpoints (USE THESE)
- `POST /api/admin/document-import/extract-data-start`
- `GET /api/admin/document-import/extract-data-status/{job_id}`
- `POST /api/admin/document-import/ingest-data`

## Example Usage

### Python Client
```python
import requests

# 1. Upload document and start extraction
with open("university_program.pdf", "rb") as f:
    response = requests.post(
        "https://api.example.com/api/admin/document-import/extract-data-start",
        files={"file": f},
        headers={"Authorization": "Bearer <token>"}
    )
job_id = response.json()["job_id"]

# 2. Poll for extraction status
while True:
    status = requests.get(
        f"https://api.example.com/api/admin/document-import/extract-data-status/{job_id}",
        headers={"Authorization": "Bearer <token>"}
    ).json()
    
    if status["status"] == "completed":
        extracted_data = status["result"]["extracted_data"]
        break
    elif status["status"] == "failed":
        raise Exception(status["error"])
    time.sleep(2)

# 3. Ingest extracted data
result = requests.post(
    "https://api.example.com/api/admin/document-import/ingest-data",
    json={"extracted_data": extracted_data},
    headers={"Authorization": "Bearer <token>"}
).json()

print(f"Inserted: {result['counts']['majors_inserted']} majors")
print(f"Errors: {result['errors']}")
```

## Testing

### Unit Tests
- Test identity rule matching
- Test enum normalization
- Test date parsing
- Test error handling

### Integration Tests
- Test full extraction → ingestion flow
- Test transaction rollback on errors
- Test duplicate handling (upserts)

## Production Considerations

1. **Job Storage**: Currently uses in-memory dict. For production, use Redis or database.
2. **Rate Limiting**: Add rate limiting to prevent abuse.
3. **Logging**: All operations are logged for audit trail.
4. **Monitoring**: Monitor extraction success rate and ingestion errors.
5. **Validation**: Pydantic schemas ensure data integrity before ingestion.

## Future Enhancements

1. **Batch Processing**: Support multiple documents in one job
2. **Incremental Updates**: Only update changed fields
3. **Conflict Resolution**: UI for resolving conflicts
4. **Data Preview**: Show diff before committing
5. **Rollback**: Ability to rollback last ingestion

