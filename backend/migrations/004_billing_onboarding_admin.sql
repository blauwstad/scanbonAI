-- Migration 004: Billing, phone-only onboarding, admin client management
-- Adds user status, company profile, billing tables, registry enrichment audit

BEGIN;

-- 1. Add status column to users (default pending; existing users set to active)
ALTER TABLE users ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'pending';
UPDATE users SET status = 'active' WHERE status = 'pending';

-- 2. Add company profile fields to users
ALTER TABLE users ADD COLUMN IF NOT EXISTS company_name VARCHAR(255);
ALTER TABLE users ADD COLUMN IF NOT EXISTS legal_name VARCHAR(255);
ALTER TABLE users ADD COLUMN IF NOT EXISTS contact_name VARCHAR(255);
ALTER TABLE users ADD COLUMN IF NOT EXISTS address_street VARCHAR(255);
ALTER TABLE users ADD COLUMN IF NOT EXISTS address_postal_code VARCHAR(20);
ALTER TABLE users ADD COLUMN IF NOT EXISTS address_city VARCHAR(100);
ALTER TABLE users ADD COLUMN IF NOT EXISTS address_country VARCHAR(2);
ALTER TABLE users ADD COLUMN IF NOT EXISTS vat_number VARCHAR(50);
ALTER TABLE users ADD COLUMN IF NOT EXISTS kvk_number VARCHAR(8);
ALTER TABLE users ADD COLUMN IF NOT EXISTS kbo_number VARCHAR(12);

-- 3. Add enrichment tracking fields
ALTER TABLE users ADD COLUMN IF NOT EXISTS enrichment_source VARCHAR(20);
ALTER TABLE users ADD COLUMN IF NOT EXISTS enrichment_last_fetched_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS enrichment_status VARCHAR(20) NOT NULL DEFAULT 'never_fetched';
ALTER TABLE users ADD COLUMN IF NOT EXISTS enrichment_error TEXT;

-- 4. Add privacy consent fields
ALTER TABLE users ADD COLUMN IF NOT EXISTS consent_registry_enrichment BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS consent_registry_enrichment_at TIMESTAMPTZ;

-- 5. Add admin notes
ALTER TABLE users ADD COLUMN IF NOT EXISTS admin_notes TEXT;

-- 6. Add token_type to registration_tokens
ALTER TABLE registration_tokens ADD COLUMN IF NOT EXISTS token_type VARCHAR(20) NOT NULL DEFAULT 'registration';

-- 7. Create indexes on users for admin queries
CREATE INDEX IF NOT EXISTS ix_user_kvk_number ON users(kvk_number) WHERE kvk_number IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_user_kbo_number ON users(kbo_number) WHERE kbo_number IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_user_status ON users(status);

-- 8. billing_plans (seeded)
CREATE TABLE IF NOT EXISTS billing_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(50) NOT NULL UNIQUE,
    name VARCHAR(100) NOT NULL,
    plan_type VARCHAR(20) NOT NULL,
    credits_amount INTEGER,
    stripe_product_id VARCHAR(100) NOT NULL,
    stripe_price_id VARCHAR(100) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 9. user_subscriptions
CREATE TABLE IF NOT EXISTS user_subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    plan_code VARCHAR(50) NOT NULL,
    stripe_customer_id VARCHAR(100) NOT NULL,
    stripe_subscription_id VARCHAR(100),
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    current_period_end TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_user_sub_user_id ON user_subscriptions(user_id);
CREATE INDEX IF NOT EXISTS ix_user_sub_stripe_customer_id ON user_subscriptions(stripe_customer_id);

-- 10. user_credits_ledger (append-only)
CREATE TABLE IF NOT EXISTS user_credits_ledger (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    delta INTEGER NOT NULL,
    reason VARCHAR(100) NOT NULL,
    invoice_id UUID,
    stripe_payment_intent_id VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_credits_user_id ON user_credits_ledger(user_id);
CREATE INDEX IF NOT EXISTS ix_credits_created_at ON user_credits_ledger(created_at);

-- 11. registry_enrichment_events (audit trail)
CREATE TABLE IF NOT EXISTS registry_enrichment_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    registry_type VARCHAR(10) NOT NULL,
    identifier VARCHAR(20) NOT NULL,
    requested_by_admin_id UUID NOT NULL REFERENCES users(id),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    response_status_code INTEGER,
    response_summary JSONB,
    applied_fields_json JSONB,
    success BOOLEAN NOT NULL,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS ix_enrichment_user_id ON registry_enrichment_events(user_id);

-- 12. Seed billing plans (Stripe IDs to be configured after Stripe product creation)
INSERT INTO billing_plans (code, name, plan_type, credits_amount, stripe_product_id, stripe_price_id)
VALUES
    ('CREDITS_100', '100 Invoice Credits', 'credits', 100, 'prod_PLACEHOLDER', 'price_PLACEHOLDER'),
    ('UNLIMITED', 'Unlimited Monthly', 'subscription', NULL, 'prod_PLACEHOLDER', 'price_PLACEHOLDER'),
    ('ULTRA', 'Ultra - Unlimited + Expert Review', 'subscription', NULL, 'prod_PLACEHOLDER', 'price_PLACEHOLDER')
ON CONFLICT (code) DO NOTHING;

COMMIT;
