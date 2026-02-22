-- ============================================================
-- ScanbonAI - Initial Database Schema
-- Migration: 001_initial_schema
-- Description: Creates all tables for MVP + FUTURE expert pool
-- ============================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================
-- ENUM TYPES
-- ============================================================

CREATE TYPE user_role AS ENUM ('user', 'admin', 'superadmin');

CREATE TYPE invoice_status AS ENUM (
    'uploaded',
    'quality_failed',
    'processing',
    'extracted',
    'reviewed',
    'approved',
    'exported'
);

CREATE TYPE admin_review_action AS ENUM ('approved', 'overridden', 'flagged');

CREATE TYPE signed_link_type AS ENUM ('image_view', 'pdf_download', 'magic_login', 'export_download');

CREATE TYPE webhook_event_status AS ENUM ('received', 'processing', 'processed', 'failed');

-- FUTURE: Expert Pool
CREATE TYPE expert_status AS ENUM ('pending', 'active', 'suspended', 'inactive');
CREATE TYPE expert_assignment_status AS ENUM ('pending', 'accepted', 'in_progress', 'completed', 'expired', 'declined');
CREATE TYPE expert_payout_status AS ENUM ('pending', 'approved', 'processing', 'paid', 'failed');
CREATE TYPE expert_dispute_resolution AS ENUM ('consensus', 'admin_override', 'escalated', 'withdrawn');
CREATE TYPE training_source_type AS ENUM ('user_correction', 'expert_review', 'admin_override');

-- ============================================================
-- MVP TABLES
-- ============================================================

-- 1. tenants: Multi-tenant root table
CREATE TABLE tenants (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            VARCHAR(255) NOT NULL,
    settings_json   JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_tenants_name ON tenants (name);
CREATE INDEX idx_tenants_created_at ON tenants (created_at);

-- 2. users: Application users identified by WhatsApp phone
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    whatsapp_phone  VARCHAR(20) NOT NULL,
    display_name    VARCHAR(255),
    role            user_role NOT NULL DEFAULT 'user',
    auth_token      VARCHAR(512),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_users_tenant_phone UNIQUE (tenant_id, whatsapp_phone)
);

CREATE INDEX idx_users_tenant_id ON users (tenant_id);
CREATE INDEX idx_users_whatsapp_phone ON users (whatsapp_phone);
CREATE INDEX idx_users_role ON users (role);
CREATE INDEX idx_users_auth_token ON users (auth_token) WHERE auth_token IS NOT NULL;

-- 3. invoices: Core invoice record, one per uploaded image/document
CREATE TABLE invoices (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    file_path           TEXT NOT NULL,
    file_hash           VARCHAR(64) NOT NULL,
    status              invoice_status NOT NULL DEFAULT 'uploaded',
    upload_source       VARCHAR(50) NOT NULL DEFAULT 'whatsapp',
    whatsapp_message_id VARCHAR(128),
    month_partition     VARCHAR(7) NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_invoices_tenant_file_hash UNIQUE (tenant_id, file_hash)
);

CREATE INDEX idx_invoices_tenant_id ON invoices (tenant_id);
CREATE INDEX idx_invoices_user_id ON invoices (user_id);
CREATE INDEX idx_invoices_status ON invoices (status);
CREATE INDEX idx_invoices_month_partition ON invoices (month_partition);
CREATE INDEX idx_invoices_created_at ON invoices (created_at);
CREATE INDEX idx_invoices_tenant_status ON invoices (tenant_id, status);
CREATE INDEX idx_invoices_tenant_month ON invoices (tenant_id, month_partition);
CREATE INDEX idx_invoices_whatsapp_msg ON invoices (whatsapp_message_id)
    WHERE whatsapp_message_id IS NOT NULL;

-- 4. quality_checks: Image quality assessment before OCR
CREATE TABLE quality_checks (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    invoice_id      UUID NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    blur_score      REAL,
    resolution_ok   BOOLEAN,
    skew_angle      REAL,
    shadow_score    REAL,
    exposure_score  REAL,
    overall_pass    BOOLEAN NOT NULL,
    failure_reasons JSONB DEFAULT '[]'::jsonb,
    checked_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_quality_checks_invoice ON quality_checks (invoice_id);
CREATE INDEX idx_quality_checks_overall_pass ON quality_checks (overall_pass);

-- 5. ocr_results: Raw OCR output per invoice
CREATE TABLE ocr_results (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    invoice_id          UUID NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    raw_text            TEXT NOT NULL,
    model_name          VARCHAR(100) NOT NULL,
    model_version       VARCHAR(50) NOT NULL,
    processing_time_ms  INTEGER NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_ocr_results_invoice_id ON ocr_results (invoice_id);
CREATE INDEX idx_ocr_results_model_name ON ocr_results (model_name);

-- 6. extracted_data: Structured data extracted from OCR output
CREATE TABLE extracted_data (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    invoice_id          UUID NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    ocr_result_id       UUID NOT NULL REFERENCES ocr_results(id) ON DELETE CASCADE,
    extracted_json      JSONB NOT NULL,
    confidence_scores   JSONB NOT NULL,
    extraction_version  VARCHAR(50) NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_extracted_data_invoice_id ON extracted_data (invoice_id);
CREATE INDEX idx_extracted_data_ocr_result_id ON extracted_data (ocr_result_id);
CREATE INDEX idx_extracted_data_json ON extracted_data USING gin (extracted_json jsonb_path_ops);

-- 7. user_corrections: User-submitted corrections to extracted data
CREATE TABLE user_corrections (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    invoice_id          UUID NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    extracted_data_id   UUID NOT NULL REFERENCES extracted_data(id) ON DELETE CASCADE,
    corrected_json      JSONB NOT NULL,
    diff_json           JSONB NOT NULL,
    corrected_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_user_corrections_invoice_id ON user_corrections (invoice_id);
CREATE INDEX idx_user_corrections_user_id ON user_corrections (corrected_by_user_id);
CREATE INDEX idx_user_corrections_created_at ON user_corrections (created_at);

-- 8. admin_reviews: Admin review decisions on invoices
CREATE TABLE admin_reviews (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    invoice_id      UUID NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    reviewer_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    action          admin_review_action NOT NULL,
    override_json   JSONB,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_admin_reviews_invoice_id ON admin_reviews (invoice_id);
CREATE INDEX idx_admin_reviews_reviewer_id ON admin_reviews (reviewer_id);
CREATE INDEX idx_admin_reviews_action ON admin_reviews (action);
CREATE INDEX idx_admin_reviews_created_at ON admin_reviews (created_at);

-- 9. audit_log: Immutable audit trail for every significant action
CREATE TABLE audit_log (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id         UUID REFERENCES users(id) ON DELETE SET NULL,
    action          VARCHAR(100) NOT NULL,
    entity_type     VARCHAR(50) NOT NULL,
    entity_id       UUID NOT NULL,
    details         JSONB DEFAULT '{}'::jsonb,
    ip_address      INET,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_log_tenant_id ON audit_log (tenant_id);
CREATE INDEX idx_audit_log_user_id ON audit_log (user_id);
CREATE INDEX idx_audit_log_entity ON audit_log (entity_type, entity_id);
CREATE INDEX idx_audit_log_action ON audit_log (action);
CREATE INDEX idx_audit_log_created_at ON audit_log (created_at);
CREATE INDEX idx_audit_log_tenant_created ON audit_log (tenant_id, created_at);

-- 10. signed_links: Short-lived tokenized links
CREATE TABLE signed_links (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    token           VARCHAR(256) NOT NULL UNIQUE,
    invoice_id      UUID REFERENCES invoices(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    link_type       signed_link_type NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    accessed_at     TIMESTAMPTZ
);

CREATE INDEX idx_signed_links_token ON signed_links (token);
CREATE INDEX idx_signed_links_expires_at ON signed_links (expires_at);
CREATE INDEX idx_signed_links_user_id ON signed_links (user_id);

-- 11. webhook_events: Idempotency table for inbound WhatsApp webhooks
CREATE TABLE webhook_events (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    whatsapp_message_id VARCHAR(128) NOT NULL UNIQUE,
    payload             JSONB NOT NULL,
    status              webhook_event_status NOT NULL DEFAULT 'received',
    retry_count         INTEGER NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at        TIMESTAMPTZ
);

CREATE INDEX idx_webhook_events_status ON webhook_events (status);
CREATE INDEX idx_webhook_events_created_at ON webhook_events (created_at);
CREATE INDEX idx_webhook_events_status_retry ON webhook_events (status, retry_count)
    WHERE status = 'failed';

-- ============================================================
-- FUTURE TABLES: Expert Pool
-- ============================================================

-- 12. experts: Domain experts who provide high-quality invoice reviews
-- FUTURE: Expert Pool
CREATE TABLE experts (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    specializations     JSONB NOT NULL DEFAULT '[]'::jsonb,
    reputation_score    REAL NOT NULL DEFAULT 0.0,
    verified_at         TIMESTAMPTZ,
    status              expert_status NOT NULL DEFAULT 'pending',
    payout_config       JSONB DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_experts_user ON experts (user_id);
CREATE INDEX idx_experts_status ON experts (status);
CREATE INDEX idx_experts_reputation ON experts (reputation_score DESC);
CREATE INDEX idx_experts_specializations ON experts USING gin (specializations jsonb_path_ops);

-- 13. expert_assignments: Assigns invoices to experts for review
-- FUTURE: Expert Pool
CREATE TABLE expert_assignments (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    invoice_id          UUID NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    expert_id           UUID NOT NULL REFERENCES experts(id) ON DELETE CASCADE,
    assignment_reason   VARCHAR(255),
    priority            INTEGER NOT NULL DEFAULT 0,
    status              expert_assignment_status NOT NULL DEFAULT 'pending',
    assigned_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ,

    CONSTRAINT uq_assignment_invoice_expert UNIQUE (invoice_id, expert_id)
);

CREATE INDEX idx_expert_assignments_invoice ON expert_assignments (invoice_id);
CREATE INDEX idx_expert_assignments_expert ON expert_assignments (expert_id);
CREATE INDEX idx_expert_assignments_status ON expert_assignments (status);
CREATE INDEX idx_expert_assignments_priority ON expert_assignments (priority DESC, assigned_at);

-- 14. expert_reviews: Expert review submissions
-- FUTURE: Expert Pool
CREATE TABLE expert_reviews (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    assignment_id           UUID NOT NULL REFERENCES expert_assignments(id) ON DELETE CASCADE,
    expert_id               UUID NOT NULL REFERENCES experts(id) ON DELETE CASCADE,
    corrected_json          JSONB NOT NULL,
    strategy_notes          JSONB DEFAULT '{}'::jsonb,
    evidence_refs           JSONB DEFAULT '[]'::jsonb,
    review_quality_score    REAL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_expert_reviews_assignment ON expert_reviews (assignment_id);
CREATE INDEX idx_expert_reviews_expert ON expert_reviews (expert_id);
CREATE INDEX idx_expert_reviews_quality ON expert_reviews (review_quality_score DESC);

-- 15. expert_payouts: Payment tracking for expert reviews
-- FUTURE: Expert Pool
CREATE TABLE expert_payouts (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    expert_id           UUID NOT NULL REFERENCES experts(id) ON DELETE CASCADE,
    period              VARCHAR(7) NOT NULL,
    amount              NUMERIC(12, 2) NOT NULL,
    currency            VARCHAR(3) NOT NULL DEFAULT 'EUR',
    status              expert_payout_status NOT NULL DEFAULT 'pending',
    trigger_details     JSONB DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    paid_at             TIMESTAMPTZ
);

CREATE INDEX idx_expert_payouts_expert ON expert_payouts (expert_id);
CREATE INDEX idx_expert_payouts_status ON expert_payouts (status);
CREATE INDEX idx_expert_payouts_period ON expert_payouts (period);

-- 16. expert_disputes: Dispute resolution between experts
-- FUTURE: Expert Pool
CREATE TABLE expert_disputes (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    invoice_id          UUID NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    expert_reviews_ids  UUID[] NOT NULL,
    resolution_method   expert_dispute_resolution,
    resolved_by         UUID REFERENCES users(id) ON DELETE SET NULL,
    outcome             JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at         TIMESTAMPTZ
);

CREATE INDEX idx_expert_disputes_invoice ON expert_disputes (invoice_id);
CREATE INDEX idx_expert_disputes_resolved_by ON expert_disputes (resolved_by);
CREATE INDEX idx_expert_disputes_created_at ON expert_disputes (created_at);

-- 17. training_datasets: Curated labelled data for model fine-tuning
-- FUTURE: Expert Pool
CREATE TABLE training_datasets (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    version         VARCHAR(50) NOT NULL,
    source_type     training_source_type NOT NULL,
    invoice_id      UUID NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    label_json      JSONB NOT NULL,
    weight          REAL NOT NULL DEFAULT 1.0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_training_datasets_version ON training_datasets (version);
CREATE INDEX idx_training_datasets_source_type ON training_datasets (source_type);
CREATE INDEX idx_training_datasets_invoice ON training_datasets (invoice_id);
CREATE INDEX idx_training_datasets_created_at ON training_datasets (created_at);

-- ============================================================
-- TRIGGERS
-- ============================================================

CREATE OR REPLACE FUNCTION trigger_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER set_updated_at_tenants
    BEFORE UPDATE ON tenants FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE TRIGGER set_updated_at_users
    BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE TRIGGER set_updated_at_invoices
    BEFORE UPDATE ON invoices FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

-- FUTURE: Expert Pool
CREATE TRIGGER set_updated_at_experts
    BEFORE UPDATE ON experts FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();
