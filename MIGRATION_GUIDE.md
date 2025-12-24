# Migration Guide: SQL Generation → Data Ingestion Pipeline

## Overview

The system has been migrated from LLM-generated SQL to a production-safe data ingestion pipeline. This guide explains the changes and how to migrate.

## What Changed

### Old System (DEPRECATED)
- LLM generated PostgreSQL SQL directly
- Admin reviewed and executed SQL
- Prone to silent failures and inconsistent inserts
- SQL validation was basic

### New System (PRODUCTION-SAFE)
- LLM extracts structured data into JSON only
- All SQL is generated deterministically by backend code
- Identity rules are defined in code
- Transactional safety with proper error handling
- No silent failures

## API Endpoint Changes

### Old Endpoints (DEPRECATED - Still Available)
```
POST /api/admin/document-import/generate-sql-start
GET  /api/admin/document-import/generate-sql-status/{job_id}
POST /api/admin/document-import/execute-sql
```

### New Endpoints (USE THESE)
```
POST /api/admin/document-import/extract-data-start
GET  /api/admin/document-import/extract-data-status/{job_id}
POST /api/admin/document-import/ingest-data
```

## Frontend Changes

The frontend has been updated to use the new endpoints. The UI now shows:
1. **Extracted Data** - JSON structure with university, majors, intakes, documents, scholarships
2. **Review Section** - Summary of extracted data with expandable JSON view
3. **Ingest Button** - Commits data to database with deterministic SQL

## Testing Checklist

### 1. Test Data Extraction
- [ ] Upload a PDF document
- [ ] Verify extraction completes successfully
- [ ] Check extracted JSON structure
- [ ] Verify all majors, intakes, documents are extracted

### 2. Test Data Ingestion
- [ ] Review extracted data
- [ ] Click "Ingest Data" button
- [ ] Verify success message with counts
- [ ] Check database for inserted/updated records
- [ ] Verify no duplicate records created

### 3. Test Error Handling
- [ ] Upload document with missing university (should show error)
- [ ] Upload document with invalid data (should show warnings)
- [ ] Test with existing data (should update, not duplicate)

### 4. Test Edge Cases
- [ ] Document with multiple majors
- [ ] Document with multiple intakes per major
- [ ] Document with scholarships
- [ ] Document with complex fee structures
- [ ] Document with missing optional fields

## Monitoring

### Key Metrics to Monitor
1. **Extraction Success Rate**: % of documents that extract successfully
2. **Ingestion Success Rate**: % of extractions that ingest successfully
3. **Error Types**: Common errors in extraction/ingestion
4. **Processing Time**: Average time for extraction and ingestion

### Logging
- All extraction jobs are logged with job_id
- All ingestion operations are logged with counts
- Errors are logged with full tracebacks

## Rollback Plan

If issues occur, the old SQL generation endpoints are still available:
1. Use old endpoints temporarily
2. Investigate issues with new pipeline
3. Fix issues and retry

## Next Steps

1. ✅ Update frontend to use new endpoints (DONE)
2. ⏳ Test with real documents
3. ⏳ Monitor extraction success rate
4. ⏳ Remove old SQL generation code after migration is complete

## Removing Old Code (After Migration Complete)

Once the new system is proven stable, remove:
- `backend/app/services/sql_generator_service.py` (or mark as deprecated)
- Old endpoints in `backend/app/routers/admin.py`:
  - `generate_sql_from_document`
  - `start_sql_generation`
  - `generate_sql_background`
  - `get_sql_generation_status`
  - `execute_generated_sql`
- Frontend code for SQL generation (keep only ingestion)

## Support

For issues or questions:
1. Check logs in backend console
2. Review extracted JSON for data quality
3. Check database for actual inserted records
4. Review `DATA_INGESTION_PIPELINE.md` for architecture details

