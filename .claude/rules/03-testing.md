# Testing Rules

## Framework
- pytest with pytest-asyncio
- httpx.AsyncClient for API tests
- Factories for test data (InvoiceFactory, UserFactory, TenantFactory)

## Required Test Categories
- Webhook idempotency (duplicate message_id handling)
- Tenant isolation (cross-tenant access returns 403)
- Signed URL validation (valid, expired, wrong tenant)
- Quality gate edge cases (blur, low-res, skew)
- OCR timeout/retry behavior
- Admin authorization (non-admin gets 403)

## Test Data
- Use factories from tests/conftest.py
- Mock external services (WhatsApp API, DeepSeek OCR)
- Test database with rollback isolation per test
- Never use production data in tests
