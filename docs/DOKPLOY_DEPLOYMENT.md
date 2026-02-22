# ScanbonAI – Dokploy Deployment Guide

This guide walks through deploying ScanbonAI to a VPS using [Dokploy](https://dokploy.com).
All commands target a fresh Ubuntu 22.04 / 24.04 server with Dokploy already installed.

---

## Prerequisites

- VPS with Dokploy installed (follow the [official quick-start](https://docs.dokploy.com/get-started/introduction))
- Domain name (e.g. `app.scanbonai.com`) with an A record pointing to the VPS public IP
- SSH access to the VPS
- Git repository containing this codebase

---

## Step 1: Create persistent data directories

Run these commands on the VPS via SSH **before** the first deploy. Docker bind mounts require
the host directories to exist in advance.

```bash
sudo mkdir -p /data/scanbonai/{postgres,redis,invoices,backups}
# UID 1000 matches the appuser created inside the backend Dockerfile
sudo chown -R 1000:1000 /data/scanbonai
# Restrict access so only the owner can read/write
sudo chmod -R 750 /data/scanbonai
```

---

## Step 2: Create project in Dokploy

1. Open the Dokploy dashboard (typically `http://<VPS-IP>:3000`).
2. Click **"Create Project"** and name it **ScanbonAI**.
3. Inside the project, click **"Create Service"** and choose **"Compose"**.
4. Connect your Git repository **or** paste the contents of `docker-compose.yml` directly.
5. Set the **Docker Compose file path** to `docker-compose.yml` (repository root).

---

## Step 3: Configure environment variables

1. Go to the Compose application's **"Environment"** tab.
2. Click **"Paste .env"** and paste the entire contents of `.env.example`.
3. Replace every placeholder value (anything that says `changeme`, `your_*`, or `change-this-*`)
   with real production values.
4. Mark the following fields as **"Secret"** so Dokploy redacts them from logs:
   - `POSTGRES_PASSWORD`
   - `DATABASE_URL`
   - `WHATSAPP_API_TOKEN`
   - `WHATSAPP_VERIFY_TOKEN`
   - `WHATSAPP_WEBHOOK_SECRET`
   - `DEEPSEEK_OCR_API_KEY`
   - `SECRET_KEY`
   - `SIGNING_KEY`
   - `ADMIN_PASSWORD`

**Generating secure random secrets:**

```bash
# Run on any machine with Python 3 installed
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Run the command twice to get two independent values for `SECRET_KEY` and `SIGNING_KEY`.

---

## Step 4: Configure persistent volumes

Dokploy exposes bind-mount configuration under **"Advanced" > "Volumes"**. Add the following
entries (the compose file already declares them; this step verifies Dokploy recognises them):

| Host path                      | Container path              | Service(s)       |
|--------------------------------|-----------------------------|------------------|
| `/data/scanbonai/postgres`     | `/var/lib/postgresql/data`  | `db`             |
| `/data/scanbonai/redis`        | `/data`                     | `redis`          |
| `/data/scanbonai/invoices`     | `/data/invoices`            | `api`, `worker`  |

If Dokploy does not auto-detect these from `docker-compose.yml`, add them manually in the UI.

---

## Step 5: Configure domains and SSL

1. Go to the **"Domains"** tab of your Compose application.
2. Click **"Add Domain"** and enter your domain, e.g. `app.scanbonai.com`.
3. Enable **"Auto SSL"** (Let's Encrypt). Dokploy will provision and auto-renew the certificate.
4. Set the **target service** to `proxy` and **port** to `443`.

> **Important:** Update `nginx/nginx.conf` before deploying:
> Replace both occurrences of `YOUR_DOMAIN` with your actual domain name, e.g.:
>
> ```nginx
> server_name app.scanbonai.com;
> ssl_certificate     /etc/letsencrypt/live/app.scanbonai.com/fullchain.pem;
> ssl_certificate_key /etc/letsencrypt/live/app.scanbonai.com/privkey.pem;
> ```

---

## Step 6: Deploy

1. Click **"Deploy"** in the Dokploy dashboard.
2. Monitor the **"Logs"** tab for build and startup progress.
3. Services start in dependency order: `db` and `redis` first, then `api` and `worker`, then
   `frontend`, then `proxy`.
4. After all services show a green health status, verify the deployment:

```bash
curl -s https://your-domain.com/api/v1/health | python3 -m json.tool
# Expected: {"status": "ok", "db": "ok", "redis": "ok"}
```

---

## Step 7: Run the initial database migration

Alembic migrations must be applied once on first deploy (and again after any schema-changing
release).

1. In the Dokploy dashboard, go to **"Terminal"**.
2. Select the **`api`** container from the dropdown.
3. Run:

```bash
alembic upgrade head
```

Expected output ends with a line like:
```
INFO  [alembic.runtime.migration] Running upgrade  -> abc123, initial schema
```

---

## Step 8: Create the admin user

After migrations complete, bootstrap the first admin account:

1. In the same Dokploy terminal (api container), run:

```bash
python -m app.scripts.create_admin
```

2. The script reads `ADMIN_EMAIL` and `ADMIN_PASSWORD` from the environment variables you set
   in Step 3.
3. Log in at `https://your-domain.com` with those credentials and **change the password
   immediately** via the admin profile page.

---

## Viewing logs

**Via the Dokploy dashboard:**

1. Go to the Compose application.
2. Click the **"Logs"** tab.
3. Use the service filter dropdown to view logs for a specific service (`api`, `worker`, `db`,
   `redis`, `frontend`, `proxy`).

**Via SSH on the VPS:**

```bash
# Follow live logs for the api service
docker compose -p scanbonai logs -f api

# Last 200 lines from the Celery worker
docker compose -p scanbonai logs --tail=200 worker

# All services simultaneously
docker compose -p scanbonai logs -f
```

---

## Restarting services

**Via the Dokploy dashboard:**

1. Go to **"Actions"** > **"Restart"** to restart all services.
2. To restart a single service, use the per-service action menu.

**Via SSH:**

```bash
# Restart only the worker (e.g. after a code fix)
docker compose -p scanbonai restart worker

# Full stack restart without rebuilding images
docker compose -p scanbonai up -d
```

---

## Deploying updates

```bash
# On the VPS, pull latest images and recreate changed services only
docker compose -p scanbonai pull
docker compose -p scanbonai up -d --build

# Run migrations if the release includes schema changes
docker compose -p scanbonai exec api alembic upgrade head
```

In Dokploy, clicking **"Redeploy"** triggers a fresh `docker compose up --build` automatically.

---

## Monitoring

| Endpoint | Description | Auth required |
|----------|-------------|---------------|
| `GET /api/v1/health` | Liveness check (db + redis status) | No |
| `GET /api/v1/admin/metrics` | Prometheus-format metrics | Admin JWT |

Example health check (suitable for an external uptime monitor):

```bash
curl -f https://your-domain.com/api/v1/health
```

---

## Database backups

A manual backup can be taken at any time:

```bash
# On the VPS
docker compose -p scanbonai exec db \
  pg_dump -U scanbonai scanbonai \
  | gzip > /data/scanbonai/backups/scanbonai_$(date +%Y%m%d_%H%M%S).sql.gz
```

For automated nightly backups, add a cron job on the VPS:

```bash
# Edit root crontab
sudo crontab -e

# Add this line (runs at 02:00 every night)
0 2 * * * docker compose -p scanbonai exec -T db pg_dump -U scanbonai scanbonai | gzip > /data/scanbonai/backups/scanbonai_$(date +\%Y\%m\%d).sql.gz && find /data/scanbonai/backups -name "*.sql.gz" -mtime +30 -delete
```

---

## One-command local run

```bash
# Clone the repository
git clone <repo-url> scanbonAI && cd scanbonAI

# Copy and edit the env file
cp .env.example .env
# Open .env in your editor and fill in the required values

# Start all services
docker compose up -d

# Wait for db and redis to be healthy, then run migrations
docker compose exec api alembic upgrade head

# Create the first admin user
docker compose exec api python -m app.scripts.create_admin

# Open the application
open http://localhost:3000
```

---

## Troubleshooting

### api service fails to start with "database connection refused"

The `api` service depends on `db` being healthy. Check:

```bash
docker compose -p scanbonai ps db
docker compose -p scanbonai logs db
```

Ensure `/data/scanbonai/postgres` exists and is owned by UID 1000.

### Celery worker shows "broker connection refused"

```bash
docker compose -p scanbonai ps redis
docker compose -p scanbonai logs redis
# Test connectivity from the worker container
docker compose -p scanbonai exec worker redis-cli -h redis ping
```

### SSL certificate errors in the proxy

Ensure the domain DNS A record points to the VPS IP and that port 80 is open in the firewall
(required for the ACME HTTP-01 challenge). Then in Dokploy, trigger a certificate renewal via
**"Domains" > "Renew Certificate"**.

### WhatsApp webhook returns 403

Verify that `WHATSAPP_VERIFY_TOKEN` in your environment matches the token you entered in the
Meta developer portal webhook subscription form exactly (case-sensitive).
