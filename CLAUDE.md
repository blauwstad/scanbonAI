# ScanbonAI - Project Context

## Product Summary
ScanbonAI is a Tax Administration system where users send invoice photos via WhatsApp. The system:
1. Ingests images securely via WhatsApp Business API webhook
2. Runs quality gates (blur, skew, exposure, resolution)
3. Performs OCR using DeepSeek OCR 2 via adapter interface
4. Extracts structured invoice metadata with per-field confidence scores
5. Presents user with review/edit UI (signed links, time-limited)
6. Stores corrections as feedback (human-in-the-loop learning)
7. Admin dashboard shows AI vs user diffs for audit/approval
8. Export to JSON/CSV/integration-ready payloads

## Tech Stack
- Backend: Python FastAPI (backend/)
- Database: PostgreSQL 16 (via SQLAlchemy async)
- Queue: Redis + Celery workers
- Frontend: React 19 + TypeScript + Vite + Tailwind CSS + shadcn/ui (src/)
- Storage: Local VPS volume at /data/scanbonai/invoices/{tenant_id}/{user_id}/YYYY-MM/
- Deployment: Docker Compose via Dokploy
- OCR: DeepSeek OCR 2 (adapter pattern, API key via env var)
- Auth: Magic link (WhatsApp-bound) + admin password

## Repository Structure
- backend/ - FastAPI application + Celery workers
- src/ - React/Vite frontend
- db/migrations/ - SQL migration files
- tests/ - pytest test suite
- docs/ - Architecture specs, OCR pipeline design, backend specification
- nginx/ - Reverse proxy configuration
- docker-compose.yml - Dokploy-compatible deployment
- SPEC.md - Full product specification
- DELIVERABLES.md - DevOps + Security deliverables

## Key Conventions
- Multi-tenant: tenant_id in ALL tables and queries
- Privacy-first: no plaintext secrets in code, PII masked in logs
- Signed links: HMAC-SHA256, 24h expiry, tenant-scoped
- Storage: /data/{tenant_id}/{user_id}/YYYY-MM/{invoice_id}.jpg
- All external API keys via environment variables (.env, never committed)

## Model Strategy
- Default: claude-sonnet-4-6 for routine coding
- Opus 4.6: architecture, security, ML/OCR, product decisions
- Haiku 4.5: test generation

## Definition of Done
- Feature works end-to-end
- tenant_id checked in all new queries
- Tests pass (pytest)
- No secrets in code
- Type hints on all functions

## FUTURE: Expert Pool Ecosystem
Designed but not yet implemented. External tax experts review "hard cases".
See SPEC.md for full design. DB tables and API stubs exist.
