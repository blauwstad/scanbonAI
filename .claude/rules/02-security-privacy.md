# Security & Privacy Rules

## Secrets Management
- NEVER commit .env files or API keys
- All secrets via environment variables
- .env.example has placeholder values only

## Signed URLs
- HMAC-SHA256 signature
- Payload: {invoice_id}:{user_id}:{tenant_id}:{link_type}:{expires_timestamp}
- Default expiry: 24 hours
- Constant-time comparison for signature validation
- Rate-limited access

## Data Retention
- Default: 7 years (Dutch tax law, Art. 52 AWR)
- Configurable per tenant
- Soft delete -> hard delete after grace period

## PII Handling
- Mask phone numbers in logs (+31***001)
- Mask IBANs (****ABNA1234)
- Mask VAT IDs in logs
- Audit log for ALL state changes (immutable)

## Access Control
- user: own invoices only
- admin: all tenant invoices, approve/override, metrics
- superadmin: cross-tenant, system config
- FUTURE expert: assigned invoices only, minimized data

## Webhook Security
- Validate X-Hub-Signature-256 on all WhatsApp webhooks
- Reject unsigned or invalid requests with 401
