# Filtered-Retrieval RAG Schema Implementation

## Overview
Replaced the old `rag_documents` and `rag_embeddings` tables with a new filtered-retrieval schema using `rag_sources` and `rag_chunks` tables. This reduces latency and token usage by filtering chunks by `doc_type` and `audience` before vector search.

## Key Features

1. **Filtered Retrieval**: Never scans all chunks - always filters by `doc_type` and optionally `audience` before vector search
2. **Token Limit**: Returns at most `top_k=3` chunks with max combined context of 1200 tokens
3. **HNSW Index**: Uses cosine distance with HNSW index (`vector_cosine_ops`) for fast similarity search
4. **Deduplication**: Uses `chunk_hash` (MD5) to prevent duplicate chunks on re-upload
5. **Document Types**: Supports `csca`, `b2c_study`, `b2b_partner`, `people_contact`, `service_policy`

## Files Created/Modified

### 1. Migration Script
**File:** `backend/migrate_rag_filtered_schema.py`
- Creates new `rag_sources` and `rag_chunks` tables
- Migrates data from old tables (if they exist)
- Creates necessary indexes including HNSW index for embeddings

**To run:**
```bash
python backend/migrate_rag_filtered_schema.py
```

### 2. New Models
**File:** `backend/app/models.py`
- `RagSource`: Stores source document metadata (name, doc_type, audience, version, status)
- `RagChunk`: Stores chunked content with embeddings, filtered by source

### 3. Updated RAGService
**File:** `backend/app/services/rag_service.py`

**New Methods:**
- `embed_query(text) -> List[float]`: Generate embedding for query
- `retrieve(db, query, doc_type, audience=None, top_k=3) -> List[Dict]`: Filtered retrieval with doc_type/audience filtering
- `ingest_source(...)`: Ingest documents with deduplication
- `chunk_text(text, chunk_size=700, overlap=120)`: Chunk text with overlap
- `compute_chunk_hash(chunk_text, doc_type, version)`: MD5 hash for deduplication

**Legacy Methods (for backward compatibility):**
- `search_similar()`: Still available but deprecated
- `create_embeddings_for_document()`: Deprecated

### 4. Updated SalesAgent
**File:** `backend/app/services/sales_agent.py`

**New Method:**
- `_determine_doc_type_and_audience(user_message, intent) -> (doc_type, audience)`: Determines doc_type and audience based on query

**Routing Logic:**
- CSCA questions → `doc_type='csca'`
- Partnership/commission/reporting/legal/privacy → `doc_type='b2b_partner'`, `audience='partner'`
- Chairman/Dr. Maruf/contact → `doc_type='people_contact'`
- Service charge/refund/hidden fees → `doc_type='service_policy'`
- Else → `doc_type='b2c_study'`

**Updated Lead Collection:**
- Partner questions: Ask for WhatsApp/WeChat/email (ONE question)
- Student questions: Ask for nationality OR contact OR level/major (ONE question max)

### 5. Updated Router
**File:** `backend/app/routers/rag.py`
- Updated `/upload` endpoint to use new `ingest_source()` method
- Accepts `doc_type`, `audience`, `version` in metadata

### 6. CLI Ingestion Script
**File:** `backend/scripts/ingest_rag_folder.py`

**Usage:**
```bash
python -m scripts.ingest_rag_folder app/rag_docs --doc-type b2c_study --audience student --version 2026
```

**Features:**
- Auto-detects doc_type from filename patterns
- Processes all `.md` files in folder
- Handles deduplication automatically

## Document Type Routing

| Query Type | doc_type | audience |
|------------|----------|----------|
| CSCA/CSC/Chinese Government Scholarship | `csca` | None |
| Partnership/commission/reporting/legal/privacy/complaints | `b2b_partner` | `partner` |
| Chairman/Dr. Maruf/contact person | `people_contact` | None |
| Service charge/refund/hidden fees/payment | `service_policy` | None |
| General studying in China questions | `b2c_study` | `student` |

## Database Schema

### rag_sources
- `id` (BIGSERIAL PRIMARY KEY)
- `name` (TEXT NOT NULL)
- `doc_type` (TEXT NOT NULL) - Indexed
- `audience` (TEXT NOT NULL DEFAULT 'student')
- `version` (TEXT)
- `status` (TEXT NOT NULL DEFAULT 'active') - Indexed
- `source_url` (TEXT)
- `last_verified_at` (TIMESTAMPTZ)
- `created_at` (TIMESTAMPTZ)

### rag_chunks
- `id` (BIGSERIAL PRIMARY KEY)
- `source_id` (BIGINT REFERENCES rag_sources) - Indexed
- `chunk_index` (INT NOT NULL)
- `chunk_hash` (TEXT NOT NULL UNIQUE) - MD5 hash for deduplication
- `content` (TEXT NOT NULL)
- `embedding` (vector(1536) NOT NULL) - HNSW index with cosine ops
- `priority` (SMALLINT NOT NULL DEFAULT 3)
- `metadata` (JSONB NOT NULL DEFAULT '{}') - GIN index
- `created_at` (TIMESTAMPTZ)

## Migration Steps

1. **Run Migration:**
   ```bash
   python backend/migrate_rag_filtered_schema.py
   ```

2. **Ingest Existing Documents:**
   ```bash
   # Ingest CSCA FAQ
   python -m scripts.ingest_rag_folder backend/app/rag_docs --doc-type csca --version 2026
   
   # Ingest general FAQ
   python -m scripts.ingest_rag_folder backend/app/rag_docs --doc-type b2c_study --version 2026
   ```

3. **Verify:**
   - Check that chunks are filtered by doc_type
   - Verify deduplication works (re-upload should skip existing chunks)
   - Test retrieval with different doc_types

## Performance Improvements

1. **Reduced Latency**: Filtering before vector search reduces search space significantly
2. **Lower Token Usage**: Max 1200 tokens per retrieval (top_k=3 chunks)
3. **Faster Queries**: HNSW index provides fast approximate nearest neighbor search
4. **No Duplicates**: Hash-based deduplication prevents bloat on re-uploads

## Backward Compatibility

- Legacy `search_similar()` method still works but is deprecated
- Old `rag_documents` and `rag_embeddings` tables are preserved after migration (can be dropped after verification)
- Router still accepts old upload format but uses new ingestion internally

## Important Notes

- **Tavily Usage**: Only used when user explicitly asks for latest/current policy AND local RAG chunks have no answer
- **Token Counting**: Uses `tiktoken` for accurate token counting (cl100k_base encoding)
- **Chunk Size**: Default 700 chars (~500-800 chars) with 120 char overlap
- **Priority**: Chunks have priority (1=high, 2=medium, 3=low) for ranking

