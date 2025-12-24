# Testing Checklist for Data Ingestion Pipeline

## Pre-Testing Setup

1. Ensure university exists in database (required for ingestion)
2. Have sample documents ready (PDF, DOCX, TXT)
3. Check backend logs are visible
4. Verify database connection

## Test Scenarios

### Scenario 1: Basic Document Extraction
**Document**: Simple university program document with 1-2 majors
**Steps**:
1. Upload document via frontend
2. Wait for extraction to complete
3. Verify extracted JSON contains:
   - `university_name` (non-empty)
   - `majors` array (at least 1 major)
   - Each major has `name`, `degree_level`, `teaching_language`
   - Each major has at least 1 intake with `intake_term`, `intake_year`
4. Check for errors/warnings in extracted data

**Expected Result**: Extraction completes successfully, JSON is valid

### Scenario 2: Data Ingestion
**Prerequisites**: Extraction completed successfully
**Steps**:
1. Review extracted data in UI
2. Click "Ingest Data" button
3. Verify success message shows counts
4. Check database:
   - `majors` table has new/updated records
   - `program_intakes` table has new/updated records
   - `program_documents` table has new/updated records
   - `scholarships` table has new/updated records (if applicable)
   - `program_intake_scholarships` table has links (if applicable)

**Expected Result**: All data ingested successfully, no duplicates created

### Scenario 3: University Not Found
**Document**: Document with university name that doesn't exist in database
**Steps**:
1. Upload document
2. Wait for extraction
3. Click "Ingest Data"
4. Verify error message: "University not found: {name}"

**Expected Result**: Ingestion fails with clear error, no data inserted

### Scenario 4: Duplicate Major Handling
**Prerequisites**: Major already exists in database
**Steps**:
1. Extract data from document
2. Ingest data (creates major)
3. Extract same document again
4. Ingest again
5. Check database: major should be updated, not duplicated

**Expected Result**: Major is updated, not duplicated

### Scenario 5: Multiple Majors and Intakes
**Document**: Document with 3+ majors, each with multiple intakes
**Steps**:
1. Upload document
2. Verify all majors extracted
3. Verify all intakes extracted for each major
4. Ingest data
5. Verify all majors and intakes in database

**Expected Result**: All majors and intakes ingested correctly

### Scenario 6: Scholarships
**Document**: Document with scholarship information
**Steps**:
1. Extract data
2. Verify scholarships in extracted JSON
3. Ingest data
4. Check `scholarships` table
5. Check `program_intake_scholarships` table for links

**Expected Result**: Scholarships created and linked correctly

### Scenario 7: Error Handling
**Document**: Document with missing critical fields
**Steps**:
1. Upload document
2. Check extracted data for errors array
3. Verify errors are descriptive
4. Attempt ingestion (should fail or warn)

**Expected Result**: Errors are caught and reported clearly

## Monitoring Checklist

After testing, monitor:
- [ ] Extraction success rate (should be > 90%)
- [ ] Ingestion success rate (should be > 95%)
- [ ] Average extraction time (should be < 2 minutes)
- [ ] Average ingestion time (should be < 5 seconds)
- [ ] Error frequency and types
- [ ] Database integrity (no orphaned records)

## Common Issues and Solutions

### Issue: Extraction returns empty majors array
**Solution**: Check document text extraction, verify LLM prompt is working

### Issue: Ingestion fails with "University not found"
**Solution**: Ensure university exists in database with exact name match (case-insensitive)

### Issue: Duplicate records created
**Solution**: Check identity rules in `DataIngestionService`, verify matching logic

### Issue: Missing fields in database
**Solution**: Check extracted JSON, verify all required fields are present

## Performance Benchmarks

- **Extraction**: 60-120 seconds for typical document
- **Ingestion**: < 5 seconds for typical document
- **Total**: < 3 minutes end-to-end

## Success Criteria

✅ System is production-ready when:
1. Extraction success rate > 90%
2. Ingestion success rate > 95%
3. No duplicate records created
4. All identity rules working correctly
5. Error messages are clear and actionable
6. Frontend UI is intuitive and responsive

