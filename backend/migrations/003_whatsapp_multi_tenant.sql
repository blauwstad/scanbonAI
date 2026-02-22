-- Migration 003: WhatsApp Multi-Tenant Support
-- Adds whatsapp_settings and registration_tokens tables
-- Adds password_hash column to users table (if not already present)

BEGIN;

-- WhatsApp Settings (per-tenant)
CREATE TABLE IF NOT EXISTS whatsapp_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL UNIQUE REFERENCES tenants(id) ON DELETE CASCADE,
    phone_number_id VARCHAR(50) NOT NULL,
    display_phone_number VARCHAR(20),
    waba_id VARCHAR(50),
    meta_app_id VARCHAR(50),
    access_token_encrypted TEXT NOT NULL,
    webhook_verify_token VARCHAR(128) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_wa_settings_phone_number_id ON whatsapp_settings(phone_number_id);

-- Registration Tokens (for WhatsApp-initiated user registration)
CREATE TABLE IF NOT EXISTS registration_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token VARCHAR(128) NOT NULL UNIQUE,
    phone_number VARCHAR(20) NOT NULL,
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    intake_phone_number_id VARCHAR(50) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_reg_token ON registration_tokens(token);

-- Add password_hash to users if not exists
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'password_hash'
    ) THEN
        ALTER TABLE users ADD COLUMN password_hash VARCHAR(128);
    END IF;
END $$;

-- Add tenant_id to webhook_events if not exists
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'webhook_events' AND column_name = 'tenant_id'
    ) THEN
        ALTER TABLE webhook_events ADD COLUMN tenant_id UUID;
    END IF;
END $$;

COMMIT;
