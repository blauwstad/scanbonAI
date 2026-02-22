# Architecture Rules

## Service Boundaries
- **api**: FastAPI backend, handles HTTP requests, validates auth, routes to services
- **worker**: Celery tasks for async processing (OCR, notifications, exports)
- **db**: PostgreSQL 16, single source of truth
- **redis**: Job queue + caching
- **frontend**: React SPA, communicates only via /api/* endpoints
- **proxy**: Nginx reverse proxy, TLS termination, rate limiting

## Multi-Tenant Rules
- Every table with user data MUST have tenant_id column
- Every query MUST filter by tenant_id (no exceptions)
- File storage is isolated: /data/{tenant_id}/{user_id}/YYYY-MM/
- Signed URLs embed tenant_id and are validated server-side
- PostgreSQL Row-Level Security as defense-in-depth

## API Conventions
- All endpoints under /api/v1/
- Webhooks under /hook/
- Response format: { "data": ..., "message": "..." }
- Paginated: { "data": [...], "total": N, "page": N, "page_size": N, "total_pages": N }
- Errors: { "detail": "...", "code": "..." }

## Database Conventions
- UUIDs for primary keys
- created_at/updated_at on all tables
- Soft deletes where applicable (deleted_at)
- JSONB for flexible fields (extracted_json, confidence_scores, etc.)
- Indexes on tenant_id, status, created_at for all major tables
