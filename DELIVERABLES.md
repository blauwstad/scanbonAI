# ScanbonAI -- DevOps & Security/Privacy Deliverables

---

## DELIVERABLE 1: DevOps Engineer Report

---

### 1.1 VPS Folder Structure

```
/opt/scanbonai/                          # Application root (code + orchestration)
├── docker-compose.yml
├── .env                                 # Secrets -- NEVER committed to git
├── .env.example                         # Template -- committed to git
├── nginx/
│   ├── nginx.conf                       # Main Nginx configuration
│   ├── conf.d/
│   │   └── default.conf                 # Server block / upstream definitions
│   └── certs/                           # TLS certs (Let's Encrypt or manual)
│       ├── fullchain.pem
│       └── privkey.pem
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── alembic/                         # DB migrations
│   ├── app/
│   │   ├── main.py                      # FastAPI entrypoint
│   │   ├── config.py                    # Settings (pydantic-settings)
│   │   ├── models/                      # SQLAlchemy models
│   │   ├── schemas/                     # Pydantic schemas
│   │   ├── api/                         # Route modules
│   │   │   ├── webhooks.py              # WhatsApp webhook receiver
│   │   │   ├── invoices.py              # Invoice CRUD + signed URLs
│   │   │   ├── auth.py                  # Login / token refresh
│   │   │   └── admin.py                 # Admin endpoints
│   │   ├── services/                    # Business logic
│   │   │   ├── ocr.py                   # DeepSeek OCR integration
│   │   │   ├── whatsapp.py              # WhatsApp Cloud API client
│   │   │   ├── signed_urls.py           # HMAC URL generation/validation
│   │   │   └── retention.py             # Retention / cleanup service
│   │   ├── tasks/                       # Background task definitions
│   │   │   ├── ocr_tasks.py
│   │   │   └── cleanup_tasks.py
│   │   ├── middleware/                   # Auth, tenant isolation, logging
│   │   └── utils/
│   │       ├── logging.py               # Structured JSON logging + PII redaction
│   │       └── security.py              # HMAC, hashing helpers
│   └── tests/
├── worker/
│   └── Dockerfile                       # Reuses backend image, different CMD
├── frontend/
│   ├── Dockerfile                       # Multi-stage: build + nginx
│   ├── package.json
│   ├── vite.config.ts
│   └── src/
└── scripts/
    ├── backup.sh                        # Automated backup script
    ├── restore.sh                       # Restore from backup
    └── init-db.sh                       # First-run DB initialisation

/data/scanbonai/                         # PERSISTENT DATA (separate partition recommended)
├── postgres/                            # PostgreSQL data directory
│   └── data/
├── redis/                               # Redis AOF + RDB persistence
│   └── data/
├── invoices/                            # Invoice image storage
│   └── {tenant_id}/
│       └── {user_id}/
│           └── YYYY-MM/
│               └── {invoice_id}.jpg
├── backups/                             # Scheduled backup artefacts
│   ├── db/
│   │   └── scanbonai_YYYYMMDD_HHMMSS.sql.gz
│   └── invoices/
│       └── scanbonai_invoices_YYYYMMDD.tar.gz.gpg
└── logs/                                # Centralised log directory (optional)
    ├── api/
    ├── worker/
    └── nginx/
```

---

### 1.2 Docker Compose (Dokploy-compatible)

```yaml
# /opt/scanbonai/docker-compose.yml
# =============================================================================
# ScanbonAI -- Production Docker Compose
# Compatible with Dokploy managed deployments
# =============================================================================

version: "3.9"

x-common-env: &common-env
  DATABASE_URL: ${DATABASE_URL}
  REDIS_URL: ${REDIS_URL}
  SECRET_KEY: ${SECRET_KEY}
  SIGNING_KEY: ${SIGNING_KEY}
  LOG_LEVEL: ${LOG_LEVEL:-info}
  SENTRY_DSN: ${SENTRY_DSN:-}
  STORAGE_PATH: /data/invoices
  WHATSAPP_API_TOKEN: ${WHATSAPP_API_TOKEN}
  WHATSAPP_VERIFY_TOKEN: ${WHATSAPP_VERIFY_TOKEN}
  WHATSAPP_PHONE_NUMBER_ID: ${WHATSAPP_PHONE_NUMBER_ID}
  DEEPSEEK_OCR_API_KEY: ${DEEPSEEK_OCR_API_KEY}
  DEEPSEEK_OCR_API_URL: ${DEEPSEEK_OCR_API_URL}
  CORS_ORIGINS: ${CORS_ORIGINS}
  ALLOWED_HOSTS: ${ALLOWED_HOSTS}

# =============================================================================
# Networks
# =============================================================================
networks:
  frontend:
    driver: bridge
    name: scanbonai_frontend
  backend:
    driver: bridge
    internal: true                       # No external access -- DB and Redis only
    name: scanbonai_backend

# =============================================================================
# Volumes (mapped to /data/scanbonai on host)
# =============================================================================
volumes:
  postgres_data:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /data/scanbonai/postgres/data
  redis_data:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /data/scanbonai/redis/data
  invoice_data:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /data/scanbonai/invoices

# =============================================================================
# Services
# =============================================================================
services:

  # ---------------------------------------------------------------------------
  # PostgreSQL 16
  # ---------------------------------------------------------------------------
  db:
    image: postgres:16-alpine
    container_name: scanbonai_db
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-scanbonai}
      POSTGRES_USER: ${POSTGRES_USER:-scanbonai}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      PGDATA: /var/lib/postgresql/data/pgdata
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks:
      - backend
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-scanbonai} -d ${POSTGRES_DB:-scanbonai}"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 30s
    deploy:
      resources:
        limits:
          cpus: "1.0"
          memory: 1G
        reservations:
          cpus: "0.25"
          memory: 256M
    logging:
      driver: json-file
      options:
        max-size: "50m"
        max-file: "5"

  # ---------------------------------------------------------------------------
  # Redis 7
  # ---------------------------------------------------------------------------
  redis:
    image: redis:7-alpine
    container_name: scanbonai_redis
    restart: unless-stopped
    command: >
      redis-server
      --appendonly yes
      --maxmemory 256mb
      --maxmemory-policy allkeys-lru
      --requirepass ${REDIS_PASSWORD}
    volumes:
      - redis_data:/data
    networks:
      - backend
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "${REDIS_PASSWORD}", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s
    deploy:
      resources:
        limits:
          cpus: "0.5"
          memory: 512M
        reservations:
          cpus: "0.1"
          memory: 64M
    logging:
      driver: json-file
      options:
        max-size: "20m"
        max-file: "3"

  # ---------------------------------------------------------------------------
  # FastAPI Backend (API Server)
  # ---------------------------------------------------------------------------
  api:
    build:
      context: ./backend
      dockerfile: Dockerfile
    image: scanbonai/api:${IMAGE_TAG:-latest}
    container_name: scanbonai_api
    restart: unless-stopped
    command: >
      uvicorn app.main:app
      --host 0.0.0.0
      --port 8000
      --workers ${API_WORKERS:-2}
      --log-level ${LOG_LEVEL:-info}
      --proxy-headers
      --forwarded-allow-ips='*'
    environment:
      <<: *common-env
    volumes:
      - invoice_data:/data/invoices
    networks:
      - frontend
      - backend
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
      interval: 15s
      timeout: 10s
      retries: 3
      start_period: 40s
    deploy:
      resources:
        limits:
          cpus: "1.0"
          memory: 1G
        reservations:
          cpus: "0.25"
          memory: 256M
    logging:
      driver: json-file
      options:
        max-size: "100m"
        max-file: "10"

  # ---------------------------------------------------------------------------
  # Background Worker (Celery / RQ -- same image, different entrypoint)
  # ---------------------------------------------------------------------------
  worker:
    build:
      context: ./backend
      dockerfile: ../worker/Dockerfile
    image: scanbonai/worker:${IMAGE_TAG:-latest}
    container_name: scanbonai_worker
    restart: unless-stopped
    command: >
      celery -A app.tasks.celery_app worker
      --loglevel=${LOG_LEVEL:-info}
      --concurrency=${WORKER_CONCURRENCY:-2}
      --queues=ocr,cleanup,default
      --max-tasks-per-child=100
    environment:
      <<: *common-env
    volumes:
      - invoice_data:/data/invoices
    networks:
      - backend
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "celery -A app.tasks.celery_app inspect ping --timeout 10 | grep -q 'pong'"]
      interval: 30s
      timeout: 15s
      retries: 3
      start_period: 30s
    deploy:
      resources:
        limits:
          cpus: "1.5"
          memory: 1.5G
        reservations:
          cpus: "0.25"
          memory: 256M
    logging:
      driver: json-file
      options:
        max-size: "100m"
        max-file: "10"

  # ---------------------------------------------------------------------------
  # Celery Beat Scheduler (periodic tasks: cleanup, backups)
  # ---------------------------------------------------------------------------
  beat:
    image: scanbonai/worker:${IMAGE_TAG:-latest}
    container_name: scanbonai_beat
    restart: unless-stopped
    command: >
      celery -A app.tasks.celery_app beat
      --loglevel=${LOG_LEVEL:-info}
      --schedule=/tmp/celerybeat-schedule
    environment:
      <<: *common-env
    networks:
      - backend
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "test -f /tmp/celerybeat-schedule"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 15s
    deploy:
      resources:
        limits:
          cpus: "0.25"
          memory: 256M
        reservations:
          cpus: "0.05"
          memory: 64M
    logging:
      driver: json-file
      options:
        max-size: "20m"
        max-file: "3"

  # ---------------------------------------------------------------------------
  # Frontend (React/Vite -- nginx-served production build)
  # ---------------------------------------------------------------------------
  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
      args:
        VITE_API_BASE_URL: ${VITE_API_BASE_URL:-/api}
    image: scanbonai/frontend:${IMAGE_TAG:-latest}
    container_name: scanbonai_frontend
    restart: unless-stopped
    networks:
      - frontend
    healthcheck:
      test: ["CMD", "wget", "--no-verbose", "--tries=1", "--spider", "http://localhost:3000/"]
      interval: 15s
      timeout: 5s
      retries: 3
      start_period: 10s
    deploy:
      resources:
        limits:
          cpus: "0.25"
          memory: 128M
        reservations:
          cpus: "0.05"
          memory: 32M
    logging:
      driver: json-file
      options:
        max-size: "20m"
        max-file: "3"

  # ---------------------------------------------------------------------------
  # Nginx Reverse Proxy (TLS termination, routing)
  # ---------------------------------------------------------------------------
  proxy:
    image: nginx:1.27-alpine
    container_name: scanbonai_proxy
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./nginx/conf.d:/etc/nginx/conf.d:ro
      - ./nginx/certs:/etc/nginx/certs:ro
      - /data/scanbonai/logs/nginx:/var/log/nginx
    networks:
      - frontend
    depends_on:
      api:
        condition: service_healthy
      frontend:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "wget", "--no-verbose", "--tries=1", "--spider", "http://localhost:80/health-proxy"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 10s
    deploy:
      resources:
        limits:
          cpus: "0.5"
          memory: 256M
        reservations:
          cpus: "0.1"
          memory: 64M
    logging:
      driver: json-file
      options:
        max-size: "50m"
        max-file: "5"

  # ===========================================================================
  # FUTURE SERVICES (uncomment when ready)
  # ===========================================================================

  # ---------------------------------------------------------------------------
  # payments-webhook: Stripe/Mollie webhook receiver
  # ---------------------------------------------------------------------------
  # payments-webhook:
  #   image: scanbonai/api:${IMAGE_TAG:-latest}
  #   container_name: scanbonai_payments_webhook
  #   restart: unless-stopped
  #   command: >
  #     uvicorn app.payments.webhook:app
  #     --host 0.0.0.0
  #     --port 8001
  #     --workers 1
  #     --proxy-headers
  #   environment:
  #     <<: *common-env
  #     PAYMENT_PROVIDER_KEY: ${PAYMENT_PROVIDER_KEY}
  #     PAYMENT_WEBHOOK_SECRET: ${PAYMENT_WEBHOOK_SECRET}
  #   networks:
  #     - frontend
  #     - backend
  #   depends_on:
  #     db:
  #       condition: service_healthy
  #     redis:
  #       condition: service_healthy
  #   healthcheck:
  #     test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')"]
  #     interval: 15s
  #     timeout: 10s
  #     retries: 3
  #     start_period: 20s
  #   deploy:
  #     resources:
  #       limits:
  #         cpus: "0.5"
  #         memory: 512M

  # ---------------------------------------------------------------------------
  # expert-scoring-worker: Processes expert review queue
  # ---------------------------------------------------------------------------
  # expert-scoring-worker:
  #   image: scanbonai/worker:${IMAGE_TAG:-latest}
  #   container_name: scanbonai_expert_worker
  #   restart: unless-stopped
  #   command: >
  #     celery -A app.tasks.celery_app worker
  #     --loglevel=${LOG_LEVEL:-info}
  #     --concurrency=1
  #     --queues=expert-scoring
  #     --max-tasks-per-child=50
  #   environment:
  #     <<: *common-env
  #     EXPERT_POOL_ENABLED: "true"
  #   networks:
  #     - backend
  #   depends_on:
  #     db:
  #       condition: service_healthy
  #     redis:
  #       condition: service_healthy
  #   deploy:
  #     resources:
  #       limits:
  #         cpus: "0.5"
  #         memory: 512M
```

---

### 1.3 Nginx Configuration

```nginx
# /opt/scanbonai/nginx/nginx.conf

user  nginx;
worker_processes  auto;
pid   /var/run/nginx.pid;

error_log  /var/log/nginx/error.log warn;

events {
    worker_connections  1024;
    multi_accept        on;
}

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;

    # -------------------------------------------------------------------------
    # Security Headers (global)
    # -------------------------------------------------------------------------
    server_tokens off;

    # -------------------------------------------------------------------------
    # Logging -- structured JSON for ELK/Loki
    # -------------------------------------------------------------------------
    log_format json_combined escape=json
        '{'
            '"time":"$time_iso8601",'
            '"remote_addr":"$remote_addr",'
            '"request_method":"$request_method",'
            '"request_uri":"$request_uri",'
            '"status":$status,'
            '"body_bytes_sent":$body_bytes_sent,'
            '"http_referer":"$http_referer",'
            '"http_user_agent":"$http_user_agent",'
            '"request_time":$request_time,'
            '"upstream_response_time":"$upstream_response_time",'
            '"x_request_id":"$request_id"'
        '}';

    access_log /var/log/nginx/access.log json_combined;

    # -------------------------------------------------------------------------
    # Performance
    # -------------------------------------------------------------------------
    sendfile        on;
    tcp_nopush      on;
    tcp_nodelay     on;
    keepalive_timeout  65;
    client_max_body_size 20M;           # Max invoice upload size

    # Gzip
    gzip  on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 4;
    gzip_types text/plain text/css application/json application/javascript text/xml;

    # -------------------------------------------------------------------------
    # Rate Limiting Zones
    # -------------------------------------------------------------------------
    limit_req_zone $binary_remote_addr zone=api_general:10m rate=30r/s;
    limit_req_zone $binary_remote_addr zone=webhook:10m rate=60r/s;
    limit_req_zone $binary_remote_addr zone=auth:10m rate=5r/m;
    limit_req_zone $binary_remote_addr zone=signed_urls:10m rate=20r/s;

    # -------------------------------------------------------------------------
    # Upstreams
    # -------------------------------------------------------------------------
    upstream api_backend {
        server api:8000;
        keepalive 16;
    }

    upstream frontend_app {
        server frontend:3000;
    }

    # -------------------------------------------------------------------------
    # Redirect HTTP -> HTTPS
    # -------------------------------------------------------------------------
    server {
        listen 80;
        server_name _;

        # Let's Encrypt ACME challenge
        location /.well-known/acme-challenge/ {
            root /var/www/certbot;
        }

        # Proxy health check (does not require TLS)
        location = /health-proxy {
            access_log off;
            return 200 'ok';
            add_header Content-Type text/plain;
        }

        location / {
            return 301 https://$host$request_uri;
        }
    }

    # -------------------------------------------------------------------------
    # HTTPS Server
    # -------------------------------------------------------------------------
    server {
        listen 443 ssl http2;
        server_name app.scanbonai.nl;   # Replace with actual domain

        # TLS
        ssl_certificate     /etc/nginx/certs/fullchain.pem;
        ssl_certificate_key /etc/nginx/certs/privkey.pem;
        ssl_protocols       TLSv1.2 TLSv1.3;
        ssl_ciphers         HIGH:!aNULL:!MD5:!RC4;
        ssl_prefer_server_ciphers on;
        ssl_session_cache   shared:SSL:10m;
        ssl_session_timeout 10m;

        # HSTS
        add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;

        # Security headers
        add_header X-Frame-Options        "SAMEORIGIN" always;
        add_header X-Content-Type-Options  "nosniff" always;
        add_header X-XSS-Protection        "1; mode=block" always;
        add_header Referrer-Policy          "strict-origin-when-cross-origin" always;
        add_header Content-Security-Policy  "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self';" always;
        add_header Permissions-Policy       "camera=(), microphone=(), geolocation=()" always;

        # Pass request ID downstream
        proxy_set_header X-Request-ID $request_id;

        # -----------------------------------------------------------------
        # WhatsApp Webhook  (high rate, specific path)
        # -----------------------------------------------------------------
        location /api/v1/webhooks/whatsapp {
            limit_req zone=webhook burst=100 nodelay;

            proxy_pass http://api_backend;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_set_header X-Request-ID $request_id;
        }

        # -----------------------------------------------------------------
        # Auth endpoints (tight rate limit)
        # -----------------------------------------------------------------
        location /api/v1/auth/ {
            limit_req zone=auth burst=3 nodelay;

            proxy_pass http://api_backend;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_set_header X-Request-ID $request_id;
        }

        # -----------------------------------------------------------------
        # Signed URL access (invoice images served via API)
        # -----------------------------------------------------------------
        location /api/v1/invoices/view/ {
            limit_req zone=signed_urls burst=10 nodelay;

            proxy_pass http://api_backend;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_set_header X-Request-ID $request_id;

            # Caching for signed URLs (they are immutable and expire)
            proxy_cache_valid 200 10m;
        }

        # -----------------------------------------------------------------
        # General API
        # -----------------------------------------------------------------
        location /api/ {
            limit_req zone=api_general burst=50 nodelay;

            proxy_pass http://api_backend;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_set_header X-Request-ID $request_id;

            proxy_read_timeout 60s;
            proxy_connect_timeout 10s;
        }

        # -----------------------------------------------------------------
        # API Health (no rate limit)
        # -----------------------------------------------------------------
        location = /api/health {
            proxy_pass http://api_backend/health;
            access_log off;
        }

        # -----------------------------------------------------------------
        # Frontend (React SPA)
        # -----------------------------------------------------------------
        location / {
            proxy_pass http://frontend_app;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;

            # SPA: fall back to index.html for client-side routing
            proxy_intercept_errors on;
            error_page 404 = /index.html;
        }

        # Static assets with long cache
        location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff2?)$ {
            proxy_pass http://frontend_app;
            expires 30d;
            add_header Cache-Control "public, immutable";
        }

        # -----------------------------------------------------------------
        # Deny dotfiles
        # -----------------------------------------------------------------
        location ~ /\. {
            deny all;
            access_log off;
            log_not_found off;
        }
    }
}
```

---

### 1.4 Backend Dockerfile

```dockerfile
# /opt/scanbonai/backend/Dockerfile

# =============================================================================
# Stage 1: Build dependencies
# =============================================================================
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# =============================================================================
# Stage 2: Runtime
# =============================================================================
FROM python:3.12-slim AS runtime

# Security: run as non-root
RUN groupadd -r scanbonai && useradd -r -g scanbonai -d /app -s /sbin/nologin scanbonai

RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 curl && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

WORKDIR /app
COPY --chown=scanbonai:scanbonai . .

USER scanbonai

EXPOSE 8000

# Default: API server. Override CMD in docker-compose for worker/beat.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

### 1.5 Worker Dockerfile

```dockerfile
# /opt/scanbonai/worker/Dockerfile
# Reuses the same backend code but with a Celery worker entrypoint.

FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim AS runtime

RUN groupadd -r scanbonai && useradd -r -g scanbonai -d /app -s /sbin/nologin scanbonai

RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 curl && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

WORKDIR /app
COPY --chown=scanbonai:scanbonai backend/ .

USER scanbonai

# Default: Celery worker. Override in docker-compose for beat scheduler.
CMD ["celery", "-A", "app.tasks.celery_app", "worker", "--loglevel=info", "--concurrency=2"]
```

---

### 1.6 Frontend Dockerfile

```dockerfile
# /opt/scanbonai/frontend/Dockerfile
# Multi-stage: Node build + nginx static serve

# =============================================================================
# Stage 1: Build
# =============================================================================
FROM node:20-alpine AS builder

ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --frozen-lockfile

COPY . .
RUN npm run build

# =============================================================================
# Stage 2: Serve with nginx
# =============================================================================
FROM nginx:1.27-alpine AS runtime

RUN rm /etc/nginx/conf.d/default.conf

# Custom nginx config for SPA
COPY --from=builder /build/dist /usr/share/nginx/html

# Inline nginx config for the frontend container
RUN cat > /etc/nginx/conf.d/default.conf <<'NGINX'
server {
    listen 3000;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff2?)$ {
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    # Health check
    location = /health {
        access_log off;
        return 200 'ok';
        add_header Content-Type text/plain;
    }
}
NGINX

EXPOSE 3000

CMD ["nginx", "-g", "daemon off;"]
```

---

### 1.7 Environment Variable Template

```bash
# /opt/scanbonai/.env.example
# =============================================================================
# ScanbonAI -- Environment Variables
# Copy to .env and fill in real values. NEVER commit .env to version control.
# =============================================================================

# -----------------------------------------------------------------------------
# Database
# -----------------------------------------------------------------------------
POSTGRES_DB=scanbonai
POSTGRES_USER=scanbonai
POSTGRES_PASSWORD=                        # REQUIRED: strong random password (32+ chars)
DATABASE_URL=postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB}

# -----------------------------------------------------------------------------
# Redis
# -----------------------------------------------------------------------------
REDIS_PASSWORD=                           # REQUIRED: strong random password
REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/0

# -----------------------------------------------------------------------------
# WhatsApp Cloud API
# -----------------------------------------------------------------------------
WHATSAPP_API_TOKEN=                       # REQUIRED: Meta Business API permanent token
WHATSAPP_VERIFY_TOKEN=                    # REQUIRED: random string for webhook verification
WHATSAPP_PHONE_NUMBER_ID=                 # REQUIRED: phone number ID from Meta dashboard
WHATSAPP_API_VERSION=v21.0               # Meta Graph API version

# -----------------------------------------------------------------------------
# DeepSeek OCR
# -----------------------------------------------------------------------------
DEEPSEEK_OCR_API_KEY=                     # REQUIRED: DeepSeek API key
DEEPSEEK_OCR_API_URL=https://api.deepseek.com/v1/chat/completions
DEEPSEEK_OCR_MODEL=deepseek-chat         # Model to use for OCR extraction
DEEPSEEK_OCR_TIMEOUT=30                  # Timeout in seconds for OCR API calls

# -----------------------------------------------------------------------------
# Application Security
# -----------------------------------------------------------------------------
SECRET_KEY=                               # REQUIRED: 64-char hex string for JWT signing
                                          #   Generate: python -c "import secrets; print(secrets.token_hex(32))"
SIGNING_KEY=                              # REQUIRED: separate 64-char hex string for HMAC signed URLs
                                          #   Generate: python -c "import secrets; print(secrets.token_hex(32))"
SIGNED_URL_EXPIRY_SECONDS=86400          # Default: 24 hours
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7
JWT_ALGORITHM=HS256

# -----------------------------------------------------------------------------
# Admin Bootstrap (first-run only)
# -----------------------------------------------------------------------------
ADMIN_EMAIL=admin@scanbonai.nl
ADMIN_PASSWORD_HASH=                      # REQUIRED: bcrypt hash of admin password
                                          #   Generate: python -c "from passlib.hash import bcrypt; print(bcrypt.hash('your-password'))"

# -----------------------------------------------------------------------------
# Web / CORS
# -----------------------------------------------------------------------------
CORS_ORIGINS=https://app.scanbonai.nl     # Comma-separated list of allowed origins
ALLOWED_HOSTS=app.scanbonai.nl,api.scanbonai.nl

# -----------------------------------------------------------------------------
# Storage
# -----------------------------------------------------------------------------
STORAGE_PATH=/data/scanbonai/invoices     # Invoice file storage root
MAX_UPLOAD_SIZE_MB=20                     # Maximum invoice image size

# -----------------------------------------------------------------------------
# Observability
# -----------------------------------------------------------------------------
LOG_LEVEL=info                            # debug | info | warning | error | critical
SENTRY_DSN=                               # Optional: Sentry error tracking DSN
ENVIRONMENT=production                    # production | staging | development

# -----------------------------------------------------------------------------
# Deployment
# -----------------------------------------------------------------------------
IMAGE_TAG=latest
API_WORKERS=2                             # Uvicorn worker count
WORKER_CONCURRENCY=2                      # Celery worker concurrency
VITE_API_BASE_URL=/api                    # Frontend API base URL

# -----------------------------------------------------------------------------
# Data Retention (Dutch fiscal: 7 years)
# -----------------------------------------------------------------------------
RETENTION_YEARS=7
RETENTION_GRACE_PERIOD_DAYS=30            # Days between soft-delete and hard-delete

# -----------------------------------------------------------------------------
# FUTURE: Expert Pool
# -----------------------------------------------------------------------------
# EXPERT_POOL_ENABLED=false
# EXPERT_ASSIGNMENT_TIMEOUT_HOURS=48
# EXPERT_MAX_CONCURRENT_REVIEWS=5

# -----------------------------------------------------------------------------
# FUTURE: Payments
# -----------------------------------------------------------------------------
# PAYMENT_PROVIDER=mollie                 # mollie | stripe
# PAYMENT_PROVIDER_KEY=
# PAYMENT_WEBHOOK_SECRET=
# PAYMENT_CURRENCY=EUR
```

---

### 1.8 Container Volume Mounts -- Detailed Reference

| Service | Container Path | Host Path | Mode | Purpose |
|---------|---------------|-----------|------|---------|
| `db` | `/var/lib/postgresql/data` | `/data/scanbonai/postgres/data` | `rw` | PostgreSQL data directory |
| `redis` | `/data` | `/data/scanbonai/redis/data` | `rw` | Redis AOF + RDB persistence |
| `api` | `/data/invoices` | `/data/scanbonai/invoices` | `rw` | Read/write invoice images |
| `worker` | `/data/invoices` | `/data/scanbonai/invoices` | `rw` | Read/write invoice images (OCR processing) |
| `beat` | (none) | (none) | -- | No persistent storage needed |
| `frontend` | (none) | (none) | -- | Static build baked into image |
| `proxy` | `/etc/nginx/nginx.conf` | `/opt/scanbonai/nginx/nginx.conf` | `ro` | Nginx main config |
| `proxy` | `/etc/nginx/conf.d` | `/opt/scanbonai/nginx/conf.d` | `ro` | Nginx server blocks |
| `proxy` | `/etc/nginx/certs` | `/opt/scanbonai/nginx/certs` | `ro` | TLS certificates |
| `proxy` | `/var/log/nginx` | `/data/scanbonai/logs/nginx` | `rw` | Nginx access/error logs |

**Host directory initialisation (run once before first deploy):**

```bash
#!/usr/bin/env bash
# /opt/scanbonai/scripts/init-dirs.sh
set -euo pipefail

DATA_ROOT="/data/scanbonai"

dirs=(
    "$DATA_ROOT/postgres/data"
    "$DATA_ROOT/redis/data"
    "$DATA_ROOT/invoices"
    "$DATA_ROOT/backups/db"
    "$DATA_ROOT/backups/invoices"
    "$DATA_ROOT/logs/api"
    "$DATA_ROOT/logs/worker"
    "$DATA_ROOT/logs/nginx"
)

for dir in "${dirs[@]}"; do
    mkdir -p "$dir"
    echo "Created: $dir"
done

# PostgreSQL requires specific ownership (UID 70 in alpine image, 999 in debian)
chown -R 70:70 "$DATA_ROOT/postgres/data"

# Redis runs as UID 999 in alpine image
chown -R 999:999 "$DATA_ROOT/redis/data"

# App user (UID 1000 in our Dockerfile)
chown -R 1000:1000 "$DATA_ROOT/invoices"
chown -R 1000:1000 "$DATA_ROOT/logs/api"
chown -R 1000:1000 "$DATA_ROOT/logs/worker"

echo "All directories initialised."
```

### 1.9 Backup Script

```bash
#!/usr/bin/env bash
# /opt/scanbonai/scripts/backup.sh
# Run via cron or Celery Beat: daily at 02:00 UTC
set -euo pipefail

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/data/scanbonai/backups"
COMPOSE_DIR="/opt/scanbonai"

# ---- Database Backup ----
echo "[${TIMESTAMP}] Starting PostgreSQL backup..."
docker compose -f "${COMPOSE_DIR}/docker-compose.yml" exec -T db \
    pg_dump -U scanbonai -Fc scanbonai \
    | gzip > "${BACKUP_DIR}/db/scanbonai_${TIMESTAMP}.sql.gz"

echo "[${TIMESTAMP}] Database backup complete."

# ---- Invoice Files Backup (incremental with rsync, encrypted) ----
echo "[${TIMESTAMP}] Starting invoice backup..."
tar czf - -C /data/scanbonai/invoices . \
    | gpg --batch --yes --symmetric --cipher-algo AES256 \
           --passphrase-file /opt/scanbonai/.backup-passphrase \
    > "${BACKUP_DIR}/invoices/scanbonai_invoices_${TIMESTAMP}.tar.gz.gpg"

echo "[${TIMESTAMP}] Invoice backup complete."

# ---- Prune old backups (keep 30 days) ----
find "${BACKUP_DIR}/db" -name "*.sql.gz" -mtime +30 -delete
find "${BACKUP_DIR}/invoices" -name "*.tar.gz.gpg" -mtime +30 -delete

echo "[${TIMESTAMP}] Backup rotation complete."
```

---
---

## DELIVERABLE 2: Security & Privacy Officer Report

---

### 2.1 Access Control Model (RBAC)

#### Role Definitions

| Role | Scope | Description |
|------|-------|-------------|
| `user` | Own data within tenant | End-user (WhatsApp user who sends invoices) |
| `admin` | All data within own tenant | Tenant administrator (accountant/business owner) |
| `superadmin` | All data, all tenants | Platform operator (ScanbonAI team) |
| `expert` *(FUTURE)* | Assigned invoices only | External tax expert for review/scoring |

#### Permission Matrix

| Permission | `user` | `admin` | `superadmin` | `expert` *(FUTURE)* |
|------------|--------|---------|--------------|----------------------|
| View own invoices | YES | YES | YES | -- |
| View own signed URLs | YES | YES | YES | -- |
| Edit own corrections | YES | YES | YES | -- |
| Export own data | YES | YES | YES | -- |
| View all tenant invoices | -- | YES | YES | -- |
| Approve/override OCR results | -- | YES | YES | -- |
| View tenant metrics/dashboard | -- | YES | YES | -- |
| Manage tenant users | -- | YES | YES | -- |
| Configure tenant settings | -- | YES | YES | -- |
| View assigned invoices | -- | -- | -- | YES |
| Submit expert review | -- | -- | -- | YES |
| Manage tenants (CRUD) | -- | -- | YES | -- |
| System configuration | -- | -- | YES | -- |
| View platform-wide metrics | -- | -- | YES | -- |
| Manage admin accounts | -- | -- | YES | -- |
| Access audit logs (all) | -- | -- | YES | -- |

#### Implementation: Row-Level Security (RLS)

```sql
-- PostgreSQL RLS policy: tenant isolation at the database level
ALTER TABLE invoices ENABLE ROW LEVEL SECURITY;

-- Users can only see invoices in their tenant
CREATE POLICY tenant_isolation ON invoices
    USING (tenant_id = current_setting('app.current_tenant_id')::uuid);

-- Users can only see their own invoices (additional filter)
CREATE POLICY user_isolation ON invoices
    FOR SELECT
    USING (
        user_id = current_setting('app.current_user_id')::uuid
        OR current_setting('app.current_role') IN ('admin', 'superadmin')
    );
```

#### Implementation: Middleware Enforcement (Python)

```python
# app/middleware/tenant.py
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware

class TenantIsolationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        user = request.state.user  # Set by auth middleware

        # Superadmin can specify tenant via header
        if user.role == "superadmin":
            tenant_id = request.headers.get(
                "X-Tenant-ID", user.tenant_id
            )
        else:
            tenant_id = user.tenant_id

        # Bind tenant to request and DB session
        request.state.tenant_id = tenant_id

        # Set PostgreSQL session variable for RLS
        async with request.state.db.begin():
            await request.state.db.execute(
                f"SET LOCAL app.current_tenant_id = '{tenant_id}'"
            )
            await request.state.db.execute(
                f"SET LOCAL app.current_user_id = '{user.id}'"
            )
            await request.state.db.execute(
                f"SET LOCAL app.current_role = '{user.role}'"
            )

        response = await call_next(request)
        return response
```

---

### 2.2 Signed URL Approach

#### Generation

```python
# app/services/signed_urls.py
import hashlib
import hmac
import time
from urllib.parse import urlencode
from app.config import settings

def generate_signed_url(
    invoice_id: str,
    user_id: str,
    tenant_id: str,
    link_type: str = "view",       # "view" | "download" | "thumbnail"
    expires_seconds: int | None = None,
) -> str:
    """
    Generate an HMAC-SHA256 signed URL for secure invoice access.

    URL format:
        /api/v1/invoices/view/{invoice_id}?uid={user_id}&tid={tenant_id}
            &type={link_type}&exp={timestamp}&sig={signature}
    """
    if expires_seconds is None:
        expires_seconds = settings.SIGNED_URL_EXPIRY_SECONDS

    expires_at = int(time.time()) + expires_seconds

    # Canonical string: deterministic ordering prevents replay with reordered params
    message = f"{invoice_id}:{user_id}:{tenant_id}:{link_type}:{expires_at}"

    signature = hmac.new(
        key=settings.SIGNING_KEY.encode("utf-8"),
        msg=message.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    params = urlencode({
        "uid": user_id,
        "tid": tenant_id,
        "type": link_type,
        "exp": expires_at,
        "sig": signature,
    })

    return f"/api/v1/invoices/view/{invoice_id}?{params}"


def validate_signed_url(
    invoice_id: str,
    user_id: str,
    tenant_id: str,
    link_type: str,
    expires_at: int,
    signature: str,
) -> bool:
    """Validate HMAC signature and check expiry."""
    # Check expiry first (fast path)
    if int(time.time()) > expires_at:
        return False

    # Recompute expected signature
    message = f"{invoice_id}:{user_id}:{tenant_id}:{link_type}:{expires_at}"
    expected = hmac.new(
        key=settings.SIGNING_KEY.encode("utf-8"),
        msg=message.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    # Constant-time comparison to prevent timing attacks
    return hmac.compare_digest(signature, expected)
```

#### Validation Middleware

```python
# app/middleware/signed_url.py
from fastapi import Request, HTTPException, Depends
from app.services.signed_urls import validate_signed_url

async def verify_signed_url(request: Request):
    """FastAPI dependency for signed URL endpoints."""
    params = request.query_params
    invoice_id = request.path_params.get("invoice_id")

    required = ["uid", "tid", "type", "exp", "sig"]
    missing = [p for p in required if p not in params]
    if missing:
        raise HTTPException(400, f"Missing parameters: {missing}")

    valid = validate_signed_url(
        invoice_id=invoice_id,
        user_id=params["uid"],
        tenant_id=params["tid"],
        link_type=params["type"],
        expires_at=int(params["exp"]),
        signature=params["sig"],
    )

    if not valid:
        raise HTTPException(403, "Invalid or expired link")

    # Enforce tenant boundary: signed URL tenant must match request context
    if hasattr(request.state, "tenant_id"):
        if params["tid"] != str(request.state.tenant_id):
            raise HTTPException(403, "Tenant boundary violation")

    return {
        "invoice_id": invoice_id,
        "user_id": params["uid"],
        "tenant_id": params["tid"],
        "link_type": params["type"],
    }
```

#### Configuration Summary

| Parameter | Default | Notes |
|-----------|---------|-------|
| Algorithm | HMAC-SHA256 | Constant-time comparison enforced |
| Default expiry | 24 hours (86400s) | Configurable per tenant |
| Rate limit | 20 req/s per IP | Nginx `signed_urls` zone |
| Tenant boundary | Enforced | Signature includes `tenant_id` |
| Link types | `view`, `download`, `thumbnail` | Extensible |

---

### 2.3 Retention Policy

#### Dutch Tax Law Requirement

Under Dutch tax law (*Algemene wet inzake rijksbelastingen*, Art. 52), businesses must retain financial administration records, including invoices, for **7 years** (10 years for real estate). ScanbonAI uses 7 years as the default.

#### Implementation

```python
# app/services/retention.py
from datetime import datetime, timedelta
from sqlalchemy import update, delete, and_
from app.models.invoice import Invoice, InvoiceStatus

RETENTION_YEARS = int(settings.RETENTION_YEARS)          # Default: 7
GRACE_PERIOD_DAYS = int(settings.RETENTION_GRACE_PERIOD_DAYS)  # Default: 30

async def soft_delete_expired_invoices(db):
    """
    Mark invoices past retention period as 'pending_deletion'.
    Run daily via Celery Beat.
    """
    cutoff = datetime.utcnow() - timedelta(days=RETENTION_YEARS * 365)

    result = await db.execute(
        update(Invoice)
        .where(
            and_(
                Invoice.created_at < cutoff,
                Invoice.status != InvoiceStatus.PENDING_DELETION,
                Invoice.status != InvoiceStatus.DELETED,
            )
        )
        .values(
            status=InvoiceStatus.PENDING_DELETION,
            deletion_scheduled_at=datetime.utcnow() + timedelta(days=GRACE_PERIOD_DAYS),
        )
    )
    return result.rowcount


async def hard_delete_expired_invoices(db, storage):
    """
    Permanently delete invoices past grace period.
    Run daily via Celery Beat, AFTER soft_delete.
    """
    cutoff = datetime.utcnow()

    invoices = await db.execute(
        Invoice.__table__.select().where(
            and_(
                Invoice.status == InvoiceStatus.PENDING_DELETION,
                Invoice.deletion_scheduled_at <= cutoff,
            )
        )
    )

    deleted_count = 0
    for invoice in invoices:
        # 1. Delete file from storage
        await storage.delete_file(invoice.file_path)

        # 2. Log deletion to audit trail (immutable)
        await log_audit_event(
            db,
            event="invoice.hard_deleted",
            entity_id=invoice.id,
            tenant_id=invoice.tenant_id,
            metadata={"original_created_at": str(invoice.created_at)},
        )

        # 3. Delete database record
        await db.execute(
            delete(Invoice).where(Invoice.id == invoice.id)
        )
        deleted_count += 1

    return deleted_count


async def export_before_delete(db, storage, tenant_id: str):
    """
    Export all invoices pending deletion for a tenant (ZIP download).
    Must be called before hard delete if tenant requests it.
    """
    # Implementation: gather files + metadata, create ZIP, return signed URL
    pass
```

#### Retention Lifecycle

```
Invoice Created ──> Active (7 years) ──> Soft Delete (pending_deletion)
                                               │
                                    Grace Period (30 days)
                                               │
                              ┌─────────────────┼──────────────────┐
                              │                 │                  │
                         Tenant exports    No action taken    Tenant cancels
                         data (ZIP)              │             deletion
                              │                  │                │
                              v                  v                v
                        Hard Delete         Hard Delete      Restore to
                       (file + DB row)     (file + DB row)    Active
```

---

### 2.4 Logging & Redaction

#### Structured JSON Logging

```python
# app/utils/logging.py
import logging
import json
import re
import uuid
from datetime import datetime, timezone

# PII patterns for redaction
PII_PATTERNS = {
    # Dutch phone: +31 6 12345678 or 06-12345678
    "phone": (
        re.compile(r"(\+31|0031|0)\s*[1-9]\d{1,2}[\s-]?\d{6,8}"),
        lambda m: m.group()[:4] + "****" + m.group()[-2:]
    ),
    # IBAN: NL followed by digits and letters
    "iban": (
        re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7,25}\b"),
        lambda m: m.group()[:4] + "****" + m.group()[-4:]
    ),
    # Dutch BTW (VAT) number
    "vat": (
        re.compile(r"\bNL\d{9}B\d{2}\b"),
        lambda m: "NL*******" + m.group()[-3:]
    ),
    # Email addresses
    "email": (
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
        lambda m: m.group()[0] + "***@" + m.group().split("@")[1]
    ),
    # BSN (Dutch social security number) -- 9 digits
    "bsn": (
        re.compile(r"\b\d{9}\b"),
        lambda m: "***" + m.group()[-3:]
    ),
}


def redact_pii(text: str) -> str:
    """Replace PII patterns with masked versions."""
    for name, (pattern, replacer) in PII_PATTERNS.items():
        text = pattern.sub(replacer, text)
    return text


class JSONFormatter(logging.Formatter):
    """Structured JSON log formatter with PII redaction."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_pii(str(record.getMessage())),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add correlation ID if present
        if hasattr(record, "correlation_id"):
            log_entry["correlation_id"] = record.correlation_id

        # Add tenant/user context if present
        if hasattr(record, "tenant_id"):
            log_entry["tenant_id"] = record.tenant_id
        if hasattr(record, "user_id"):
            log_entry["user_id"] = record.user_id

        # Add extra fields
        if hasattr(record, "extra_data"):
            log_entry["data"] = record.extra_data

        # Add exception info
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": redact_pii(str(record.exc_info[1])),
                "traceback": redact_pii(self.formatException(record.exc_info)),
            }

        return json.dumps(log_entry, default=str)


def setup_logging(log_level: str = "INFO"):
    """Configure application-wide structured logging."""
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())

    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level.upper()))
    root.handlers = [handler]

    # Silence noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
```

#### Correlation ID Middleware

```python
# app/middleware/correlation.py
import uuid
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

class CorrelationIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Use X-Request-ID from Nginx or generate new
        correlation_id = request.headers.get(
            "X-Request-ID", str(uuid.uuid4())
        )
        request.state.correlation_id = correlation_id

        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        return response
```

#### Audit Log Table

```sql
-- Immutable audit trail for all state changes
CREATE TABLE audit_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT now(),
    correlation_id  UUID,
    tenant_id       UUID NOT NULL,
    user_id         UUID,                        -- NULL for system actions
    actor_role      VARCHAR(20) NOT NULL,         -- user/admin/superadmin/system
    event           VARCHAR(100) NOT NULL,         -- e.g. "invoice.created"
    entity_type     VARCHAR(50) NOT NULL,          -- e.g. "invoice"
    entity_id       UUID NOT NULL,
    changes         JSONB,                         -- {field: {old: x, new: y}}
    metadata        JSONB,                         -- Additional context
    ip_address      INET,
    user_agent      TEXT
);

-- Index for querying by entity
CREATE INDEX idx_audit_entity ON audit_log (entity_type, entity_id, timestamp DESC);
-- Index for querying by tenant
CREATE INDEX idx_audit_tenant ON audit_log (tenant_id, timestamp DESC);
-- Index for querying by user
CREATE INDEX idx_audit_user ON audit_log (user_id, timestamp DESC);

-- Prevent UPDATE/DELETE on audit_log (immutability)
CREATE OR REPLACE FUNCTION prevent_audit_modification()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Audit log records are immutable and cannot be modified or deleted';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_log_immutable_update
    BEFORE UPDATE ON audit_log FOR EACH ROW
    EXECUTE FUNCTION prevent_audit_modification();

CREATE TRIGGER audit_log_immutable_delete
    BEFORE DELETE ON audit_log FOR EACH ROW
    EXECUTE FUNCTION prevent_audit_modification();
```

#### Events to Audit

| Event | Trigger |
|-------|---------|
| `invoice.created` | New invoice received via WhatsApp |
| `invoice.ocr_completed` | OCR processing finished |
| `invoice.correction_submitted` | User corrects extracted data |
| `invoice.approved` | Admin approves invoice |
| `invoice.overridden` | Admin overrides OCR result |
| `invoice.soft_deleted` | Retention policy triggered |
| `invoice.hard_deleted` | Permanent deletion after grace period |
| `invoice.exported` | User/admin exports invoice data |
| `invoice.viewed_signed_url` | Invoice accessed via signed URL |
| `user.login` | Successful authentication |
| `user.login_failed` | Failed authentication attempt |
| `user.created` | New user account |
| `user.role_changed` | Role assignment/change |
| `user.deactivated` | Account deactivated |
| `tenant.created` | New tenant onboarded |
| `tenant.settings_changed` | Tenant configuration updated |
| `expert.assigned` *(FUTURE)* | Expert assigned to invoice |
| `expert.review_submitted` *(FUTURE)* | Expert submits review |

---

### 2.5 Encryption

#### At Rest

| Layer | Implementation | Notes |
|-------|---------------|-------|
| PostgreSQL | LUKS-encrypted volume on host | Encrypt `/data/scanbonai/postgres` partition |
| Redis | LUKS-encrypted volume on host | Encrypt `/data/scanbonai/redis` partition |
| Invoice files | LUKS-encrypted volume on host | Encrypt `/data/scanbonai/invoices` partition |
| Backups | GPG symmetric encryption (AES-256) | Encrypted before writing to disk |
| Application secrets | `.env` file with 600 permissions | Owned by root, readable by docker |

**LUKS setup (one-time, on VPS):**

```bash
# Example: encrypting the /data partition
sudo cryptsetup luksFormat /dev/sdb1
sudo cryptsetup open /dev/sdb1 scanbonai_data
sudo mkfs.ext4 /dev/mapper/scanbonai_data
sudo mount /dev/mapper/scanbonai_data /data/scanbonai

# Add to /etc/crypttab for auto-unlock (with key file or TPM)
```

#### In Transit

| Connection | Encryption | Implementation |
|-----------|------------|----------------|
| Client <-> Proxy | TLS 1.2+ | Nginx with Let's Encrypt |
| Proxy <-> API | Plain HTTP (internal Docker network) | Isolated network, no external access |
| API <-> PostgreSQL | Plain TCP (internal Docker network) | Optional: `sslmode=require` in DATABASE_URL |
| API <-> Redis | Plain TCP (internal Docker network) | Password-protected, internal network only |
| API <-> DeepSeek OCR | HTTPS | External API, always TLS |
| API <-> WhatsApp Cloud API | HTTPS | Meta enforces TLS |
| Signed URLs | HMAC-SHA256 | Integrity verification, not encryption |

#### TLS Configuration (Let's Encrypt with auto-renewal)

```bash
# Initial certificate (run on host, before starting proxy)
sudo apt install certbot
sudo certbot certonly --standalone -d app.scanbonai.nl

# Copy to nginx cert directory
sudo cp /etc/letsencrypt/live/app.scanbonai.nl/fullchain.pem /opt/scanbonai/nginx/certs/
sudo cp /etc/letsencrypt/live/app.scanbonai.nl/privkey.pem /opt/scanbonai/nginx/certs/

# Auto-renewal cron (runs twice daily)
# 0 0,12 * * * certbot renew --deploy-hook "docker exec scanbonai_proxy nginx -s reload"
```

---

### 2.6 Threat Model (Top 10)

| # | Threat | Severity | Likelihood | Mitigation |
|---|--------|----------|-----------|------------|
| 1 | **Unauthorized invoice access** -- attacker guesses or brute-forces invoice URL | Critical | Medium | HMAC-SHA256 signed URLs with expiry; tenant isolation in signature; rate limiting (20 req/s); URL entropy makes brute-force infeasible (256-bit HMAC) |
| 2 | **WhatsApp webhook spoofing** -- attacker sends fake webhook payloads to inject malicious invoices | High | Medium | Validate `X-Hub-Signature-256` header on every webhook request using app secret; reject unsigned/invalid payloads; log all rejected attempts |
| 3 | **SQL injection** -- attacker injects SQL via API parameters | Critical | Low | All queries via SQLAlchemy ORM with parameterized statements; no raw SQL in application code; input validation with Pydantic schemas; PostgreSQL RLS as defense-in-depth |
| 4 | **XSS in review UI** -- attacker injects scripts via invoice data (vendor names, descriptions) | High | Medium | React auto-escapes JSX output by default; strict Content-Security-Policy headers (`script-src 'self'`); sanitize OCR-extracted text before storage; `X-Content-Type-Options: nosniff` |
| 5 | **File upload attacks** -- malicious file disguised as invoice image | High | Medium | Validate MIME type server-side (accept only `image/jpeg`, `image/png`, `application/pdf`); check magic bytes (not just extension); limit file size to 20MB; store outside web root; never execute uploaded files; optional ClamAV scan |
| 6 | **Tenant boundary breach** -- user in Tenant A accesses Tenant B data | Critical | Low | `tenant_id` included in every database query; PostgreSQL RLS policies; `tenant_id` embedded in signed URL signatures; middleware enforcement at API layer; integration tests for isolation |
| 7 | **JWT/token theft** -- stolen authentication token grants unauthorized access | High | Medium | Short-lived access tokens (30 min); refresh token rotation; secure cookie flags (`HttpOnly`, `Secure`, `SameSite=Strict`); token blacklist on logout; bind tokens to IP/user-agent fingerprint (optional) |
| 8 | **DDoS on webhook endpoint** -- volumetric attack on `/api/v1/webhooks/whatsapp` | Medium | High | Nginx rate limiting (60 req/s burst 100); webhook requests queued to Redis immediately (fast response, async processing); cloud provider DDoS protection; Meta whitelisting (optional) |
| 9 | **OCR data exfiltration** -- compromised OCR service leaks invoice data | High | Low | DeepSeek API calls over HTTPS only; API key stored in `.env` (never in code); OCR service has no access to database; network isolation: worker on `backend` network only; monitor API usage for anomalies; consider self-hosted OCR for high-security tenants |
| 10 | **Admin account compromise** -- attacker gains admin credentials | Critical | Low | Bcrypt password hashing (cost factor 12+); mandatory 2FA (TOTP) for admin/superadmin roles; audit logging of all admin actions; IP allowlisting for admin panel (optional); session timeout (15 min inactive); alert on login from new IP |

#### Additional Threats (Honorable Mentions)

| # | Threat | Mitigation |
|---|--------|------------|
| 11 | SSRF via OCR URL parameter | Validate/sanitize URLs; blocklist internal IPs; use allowlist for OCR endpoints |
| 12 | Dependency vulnerability (supply chain) | Dependabot/Snyk scanning; pin dependency versions; regular updates |
| 13 | Container escape | Minimal base images (Alpine/slim); non-root containers; read-only filesystems where possible; Docker security options |
| 14 | Secrets in logs/errors | PII redaction in structured logging; Sentry `before_send` scrubbing; never log request bodies containing credentials |
| 15 | Invoice tampering (integrity) | SHA-256 hash of original file stored in DB at upload; verify hash on every access; audit trail is immutable |

---

### 2.7 FUTURE: Expert Privacy Controls

#### Data Minimisation

When the expert review system is activated, experts must operate under strict data minimisation principles:

```python
# app/schemas/expert.py
from pydantic import BaseModel
from typing import Optional

class ExpertInvoiceView(BaseModel):
    """
    Minimised invoice view for expert reviewers.
    Experts see ONLY what they need for the review task.
    """
    # Identifiers (anonymised)
    assignment_id: str                    # Expert's assignment reference
    invoice_id: str                       # For reference in review

    # Invoice image
    signed_image_url: str                 # Time-limited, expert-scoped signed URL

    # Extracted fields (for verification)
    vendor_name: str                      # Full (needed for tax categorisation)
    invoice_date: str
    invoice_number: str
    total_amount: str
    vat_amount: str
    vat_rate: str
    currency: str
    category_suggestion: str              # OCR-suggested category

    # Masked fields
    iban: str                             # "NL91****4567" -- last 4 only
    phone: Optional[str] = None          # "+31 6 ****78" -- if present
    address: Optional[str] = None        # "******, Amsterdam" -- city only

    # NOT included (never sent to expert):
    # - user_id, user_name, user_email, user_phone
    # - tenant internal data
    # - other invoices from same user
    # - historical corrections
    # - any authentication tokens

    class Config:
        # Prevent extra fields from leaking
        extra = "forbid"
```

#### Field Masking Implementation

```python
# app/services/expert_masking.py

def mask_iban(iban: str) -> str:
    """Show first 4 and last 4 characters only."""
    if len(iban) < 8:
        return "****"
    return iban[:4] + "****" + iban[-4:]

def mask_phone(phone: str) -> str:
    """Show country code and last 2 digits only."""
    if len(phone) < 6:
        return "****"
    return phone[:4] + " ****" + phone[-2:]

def mask_address(address: str) -> str:
    """Show city only (last component after comma)."""
    parts = address.split(",")
    if len(parts) >= 2:
        return "******, " + parts[-1].strip()
    return "******"

def prepare_expert_view(invoice, extracted_data: dict) -> dict:
    """Transform full invoice data into expert-safe view."""
    return {
        "vendor_name": extracted_data.get("vendor_name", ""),
        "invoice_date": extracted_data.get("invoice_date", ""),
        "invoice_number": extracted_data.get("invoice_number", ""),
        "total_amount": extracted_data.get("total_amount", ""),
        "vat_amount": extracted_data.get("vat_amount", ""),
        "vat_rate": extracted_data.get("vat_rate", ""),
        "currency": extracted_data.get("currency", "EUR"),
        "category_suggestion": extracted_data.get("category", ""),
        "iban": mask_iban(extracted_data.get("iban", "")),
        "phone": mask_phone(extracted_data.get("phone", "")) if extracted_data.get("phone") else None,
        "address": mask_address(extracted_data.get("address", "")) if extracted_data.get("address") else None,
    }
```

#### Expert Access Logging

```sql
-- Every expert view is logged with granular detail
CREATE TABLE expert_access_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT now(),
    expert_id       UUID NOT NULL REFERENCES users(id),
    assignment_id   UUID NOT NULL REFERENCES expert_assignments(id),
    invoice_id      UUID NOT NULL REFERENCES invoices(id),
    action          VARCHAR(50) NOT NULL,          -- "viewed", "downloaded_image", "submitted_review"
    fields_accessed TEXT[] NOT NULL,                -- ["vendor_name", "total_amount", "iban_masked"]
    ip_address      INET NOT NULL,
    user_agent      TEXT,
    session_id      UUID NOT NULL
);

CREATE INDEX idx_expert_access_invoice ON expert_access_log (invoice_id, timestamp);
CREATE INDEX idx_expert_access_expert ON expert_access_log (expert_id, timestamp);
```

#### Time-Limited Expert Assignments

```python
# app/models/expert_assignment.py
class ExpertAssignment(Base):
    __tablename__ = "expert_assignments"

    id = Column(UUID, primary_key=True, default=uuid4)
    expert_id = Column(UUID, ForeignKey("users.id"), nullable=False)
    invoice_id = Column(UUID, ForeignKey("invoices.id"), nullable=False)
    tenant_id = Column(UUID, ForeignKey("tenants.id"), nullable=False)

    assigned_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)       # assigned_at + 48 hours
    completed_at = Column(DateTime, nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    # pending -> in_review -> completed | expired | revoked

    # Expert can ONLY access invoice if:
    # 1. assignment.status == "pending" or "in_review"
    # 2. assignment.expires_at > now()
    # 3. assignment.expert_id == current_user.id
```

#### Dispute Integrity

```python
# Expert reviews form an immutable chain
class ExpertReview(Base):
    __tablename__ = "expert_reviews"

    id = Column(UUID, primary_key=True, default=uuid4)
    assignment_id = Column(UUID, ForeignKey("expert_assignments.id"), nullable=False)
    expert_id = Column(UUID, ForeignKey("users.id"), nullable=False)
    invoice_id = Column(UUID, ForeignKey("invoices.id"), nullable=False)

    # Review content
    verdict = Column(String(20), nullable=False)         # "correct" | "incorrect" | "unclear"
    corrections = Column(JSONB, nullable=True)            # {field: new_value}
    confidence_score = Column(Float, nullable=False)
    notes = Column(Text, nullable=True)

    # Integrity
    submitted_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    content_hash = Column(String(64), nullable=False)     # SHA-256 of review content
    previous_review_hash = Column(String(64), nullable=True)  # Chain integrity

    # Immutable: no UPDATE/DELETE allowed (enforced by trigger, same as audit_log)
```

---

### 2.8 Privacy/Security Pre-Launch Checklist

```markdown
## ScanbonAI -- Pre-Launch Security & Privacy Checklist

### Authentication & Authorization
- [ ] JWT secret key is cryptographically random (64+ hex chars)
- [ ] JWT access tokens expire within 30 minutes
- [ ] JWT refresh tokens expire within 7 days
- [ ] Refresh token rotation is implemented (old token invalidated on use)
- [ ] Password hashing uses bcrypt with cost factor >= 12
- [ ] Admin/superadmin accounts require 2FA (TOTP)
- [ ] Failed login attempts are rate-limited (5/minute)
- [ ] Failed login attempts are logged with IP address
- [ ] Account lockout after 10 consecutive failures
- [ ] Session timeout for admin panel (15 min inactivity)
- [ ] RBAC roles enforced at API middleware level
- [ ] RBAC roles enforced at database level (RLS)
- [ ] All API endpoints have explicit permission decorators

### Tenant Isolation
- [ ] tenant_id included in ALL database queries (no exceptions)
- [ ] PostgreSQL Row-Level Security enabled on all tenant-scoped tables
- [ ] Tenant boundary validated in signed URL signatures
- [ ] Tenant isolation middleware applied to all routes
- [ ] Integration tests verify cross-tenant access is blocked
- [ ] File storage paths include tenant_id prefix
- [ ] No shared state between tenants in Redis (key prefix or separate DB)

### Data Protection
- [ ] Signed URLs use HMAC-SHA256 with dedicated signing key
- [ ] Signed URLs include expiry timestamp (default 24h)
- [ ] Signed URL validation uses constant-time comparison
- [ ] Invoice files stored outside web-accessible directories
- [ ] File uploads validated: MIME type, magic bytes, size limit
- [ ] No user-uploaded content served with executable content types
- [ ] PII redacted from all application logs
- [ ] PII redacted from error tracking (Sentry before_send)
- [ ] Database backups encrypted (GPG AES-256)
- [ ] Host volumes encrypted (LUKS)
- [ ] .env file permissions set to 600 (owner read/write only)

### Network Security
- [ ] TLS 1.2+ enforced on all external connections
- [ ] HSTS header with includeSubDomains and preload
- [ ] HTTP redirects to HTTPS
- [ ] Database (PostgreSQL) on internal-only Docker network
- [ ] Redis on internal-only Docker network
- [ ] Redis requires password authentication
- [ ] No database ports exposed to host/internet
- [ ] Nginx rate limiting configured for all endpoint groups
- [ ] Webhook endpoint rate limited (60/s with burst)
- [ ] Auth endpoints rate limited (5/minute)

### Application Security
- [ ] All SQL queries use parameterized statements (SQLAlchemy ORM)
- [ ] No raw SQL in application code (or if present: reviewed and parameterized)
- [ ] Input validation on all API endpoints (Pydantic schemas)
- [ ] Content-Security-Policy header configured
- [ ] X-Frame-Options: SAMEORIGIN
- [ ] X-Content-Type-Options: nosniff
- [ ] Referrer-Policy: strict-origin-when-cross-origin
- [ ] CORS origins explicitly whitelisted (no wildcard in production)
- [ ] WhatsApp webhook signature (X-Hub-Signature-256) validated
- [ ] File type validation on invoice uploads (server-side)
- [ ] Dependency vulnerability scan (pip-audit / safety / Snyk)
- [ ] Docker images use minimal base (slim/alpine)
- [ ] Docker containers run as non-root user
- [ ] Docker images pinned to specific versions (not :latest in production)
- [ ] No secrets in Docker images or build args

### Audit & Monitoring
- [ ] All API requests logged with correlation_id
- [ ] Structured JSON logging configured
- [ ] Audit log table created with immutability triggers
- [ ] All authentication events logged (login, logout, failure)
- [ ] All state changes logged to audit table
- [ ] All signed URL accesses logged
- [ ] All admin actions logged with before/after values
- [ ] Sentry or equivalent error tracking configured
- [ ] Log rotation configured (json-file driver with max-size)
- [ ] Health check endpoints for all services
- [ ] Alerting configured for: service down, error rate spike, auth failures

### Data Retention & Compliance
- [ ] Default retention period set to 7 years (Dutch tax law)
- [ ] Retention period configurable per tenant
- [ ] Soft delete implemented (30-day grace period)
- [ ] Hard delete removes both file and database record
- [ ] Export-before-delete capability available
- [ ] Automated cleanup job scheduled (Celery Beat)
- [ ] Deletion events recorded in immutable audit log
- [ ] Privacy policy published and accessible
- [ ] Data processing agreement (DPA) template available for tenants
- [ ] GDPR data subject access request (DSAR) process documented
- [ ] GDPR right to erasure process documented (with tax law exception noted)
- [ ] Data Protection Impact Assessment (DPIA) completed

### Backup & Recovery
- [ ] Automated daily database backups
- [ ] Automated daily invoice file backups (encrypted)
- [ ] Backup retention: 30 days
- [ ] Backup restoration tested and documented
- [ ] Recovery Time Objective (RTO) defined and tested
- [ ] Recovery Point Objective (RPO) defined (< 24 hours)

### Infrastructure
- [ ] SSH key-only authentication on VPS (password auth disabled)
- [ ] Firewall configured (only 80, 443 open; 22 restricted to admin IPs)
- [ ] Unattended security updates enabled on host OS
- [ ] Docker socket not exposed to containers
- [ ] Resource limits (CPU, memory) set for all containers
- [ ] Container restart policies configured (unless-stopped)
- [ ] Host OS disk space monitoring and alerting
```

---

### Appendix A: WhatsApp Webhook Signature Validation

```python
# app/services/whatsapp.py
import hashlib
import hmac
from fastapi import Request, HTTPException

async def validate_whatsapp_signature(request: Request) -> bytes:
    """
    Validate X-Hub-Signature-256 header from Meta/WhatsApp Cloud API.
    Must be called before processing any webhook payload.
    """
    signature_header = request.headers.get("X-Hub-Signature-256", "")
    if not signature_header.startswith("sha256="):
        raise HTTPException(401, "Missing or malformed signature header")

    expected_signature = signature_header[7:]  # Strip "sha256=" prefix
    body = await request.body()

    computed = hmac.new(
        key=settings.WHATSAPP_APP_SECRET.encode("utf-8"),
        msg=body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(computed, expected_signature):
        # Log the attempt (without the body, which may contain PII)
        logger.warning(
            "WhatsApp webhook signature validation failed",
            extra={"ip": request.client.host},
        )
        raise HTTPException(401, "Invalid webhook signature")

    return body
```

### Appendix B: Invoice File Hash Integrity

```python
# app/services/file_integrity.py
import hashlib
from pathlib import Path

def compute_file_hash(file_path: str | Path) -> str:
    """Compute SHA-256 hash of an invoice file for integrity verification."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()

def verify_file_integrity(file_path: str | Path, expected_hash: str) -> bool:
    """Verify file has not been tampered with since upload."""
    actual_hash = compute_file_hash(file_path)
    return hmac.compare_digest(actual_hash, expected_hash)
```
