# Dokploy Deployment Rules

## Persistent Data
- All persistent data at /data/scanbonai/ on VPS (NOT inside containers)
- Postgres: /data/scanbonai/postgres
- Redis: /data/scanbonai/redis
- Invoices: /data/scanbonai/invoices/{tenant_id}/{user_id}/YYYY-MM/
- Backups: /data/scanbonai/backups

## Docker Compose
- Must be Dokploy-compatible (standard compose format)
- Health checks on all services
- Resource limits (memory, CPU)
- Network isolation (internal for db/redis)
- Restart policies (unless-stopped)

## Environment Variables
- Set via Dokploy "Environment" tab
- Sensitive values marked as "Secret"
- NEVER hardcode in docker-compose.yml or Dockerfiles

## Domains & SSL
- Configure via Dokploy "Domains" tab
- Auto SSL via Let's Encrypt
- Force HTTPS redirect

## Migrations
- Run via Dokploy terminal: docker compose exec api alembic upgrade head
- Or add to deploy hook

## Monitoring
- Liveness: GET /health (fast, no deps)
- Readiness: GET /health/ready (checks DB + Redis, returns 503 on failure)
- Metrics: GET /metrics (Prometheus endpoint)
- Admin metrics: GET /api/v1/admin/metrics (admin auth required)
- Logs: Dokploy "Logs" tab
