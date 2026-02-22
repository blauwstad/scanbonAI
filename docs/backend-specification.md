# ScanbonAI Backend Specification

> Tax Administration via WhatsApp Invoices

---

## DELIVERABLE 1: Database Schema (PostgreSQL)

### Prerequisites

```sql
-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Custom ENUM types
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
```

---

### MVP Tables

#### 1. tenants

```sql
-- Multi-tenant root table. Every business/organisation is a tenant.
-- settings_json stores locale, default currency, VAT config, export prefs, etc.
CREATE TABLE tenants (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            VARCHAR(255) NOT NULL,
    settings_json   JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_tenants_name ON tenants (name);
CREATE INDEX idx_tenants_created_at ON tenants (created_at);
```

#### 2. users

```sql
-- Application users. Identified by WhatsApp phone number.
-- auth_token stores the current valid bearer/session token (nullable when logged out).
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
```

#### 3. invoices

```sql
-- Core invoice record. One row per uploaded image/document.
-- file_hash is SHA-256 of the original file for deduplication.
-- month_partition (YYYY-MM) enables fast filtering by fiscal period.
-- whatsapp_message_id links back to the originating WhatsApp message.
CREATE TABLE invoices (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    file_path           TEXT NOT NULL,
    file_hash           VARCHAR(64) NOT NULL,
    status              invoice_status NOT NULL DEFAULT 'uploaded',
    upload_source       VARCHAR(50) NOT NULL DEFAULT 'whatsapp',
    whatsapp_message_id VARCHAR(128),
    month_partition     VARCHAR(7) NOT NULL,  -- 'YYYY-MM'
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
```

#### 4. quality_checks

```sql
-- Image quality assessment results. Runs before OCR.
-- Stores individual metric scores and an overall pass/fail with reasons.
CREATE TABLE quality_checks (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    invoice_id      UUID NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    blur_score      REAL,           -- Laplacian variance; higher = sharper
    resolution_ok   BOOLEAN,        -- meets minimum px threshold
    skew_angle      REAL,           -- degrees of rotation detected
    shadow_score    REAL,           -- 0.0 (no shadow) to 1.0 (heavy shadow)
    exposure_score  REAL,           -- 0.0 (under) to 1.0 (over), 0.5 = ideal
    overall_pass    BOOLEAN NOT NULL,
    failure_reasons JSONB DEFAULT '[]'::jsonb,  -- e.g. ["blur","low_resolution"]
    checked_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_quality_checks_invoice ON quality_checks (invoice_id);
CREATE INDEX idx_quality_checks_overall_pass ON quality_checks (overall_pass);
```

#### 5. ocr_results

```sql
-- Raw OCR output per invoice. Supports multiple OCR runs (model comparison).
-- processing_time_ms tracks latency for monitoring.
CREATE TABLE ocr_results (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    invoice_id          UUID NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    raw_text            TEXT NOT NULL,
    model_name          VARCHAR(100) NOT NULL,  -- e.g. 'donut-v2', 'gpt-4o-vision'
    model_version       VARCHAR(50) NOT NULL,
    processing_time_ms  INTEGER NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_ocr_results_invoice_id ON ocr_results (invoice_id);
CREATE INDEX idx_ocr_results_model_name ON ocr_results (model_name);
```

#### 6. extracted_data

```sql
-- Structured data extracted from OCR output.
-- extracted_json follows the Invoice Metadata JSON Schema (see Deliverable 2).
-- confidence_scores mirrors the same structure with per-field confidence (0.0-1.0).
CREATE TABLE extracted_data (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    invoice_id          UUID NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    ocr_result_id       UUID NOT NULL REFERENCES ocr_results(id) ON DELETE CASCADE,
    extracted_json      JSONB NOT NULL,
    confidence_scores   JSONB NOT NULL,
    extraction_version  VARCHAR(50) NOT NULL,  -- e.g. 'v1.2.0'
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_extracted_data_invoice_id ON extracted_data (invoice_id);
CREATE INDEX idx_extracted_data_ocr_result_id ON extracted_data (ocr_result_id);
-- GIN index for querying into the extracted JSON (e.g. supplier name searches)
CREATE INDEX idx_extracted_data_json ON extracted_data USING gin (extracted_json jsonb_path_ops);
```

#### 7. user_corrections

```sql
-- User-submitted corrections to extracted data.
-- diff_json records only the fields that changed (old_value / new_value pairs).
-- These corrections feed back into the training pipeline.
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
```

#### 8. admin_reviews

```sql
-- Admin review decisions on invoices.
-- override_json contains the admin's final field values when action = 'overridden'.
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
```

#### 9. audit_log

```sql
-- Immutable audit trail for every significant action.
-- entity_type + entity_id form a polymorphic reference (e.g. 'invoice', <uuid>).
CREATE TABLE audit_log (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id         UUID REFERENCES users(id) ON DELETE SET NULL,
    action          VARCHAR(100) NOT NULL,   -- e.g. 'invoice.uploaded', 'correction.submitted'
    entity_type     VARCHAR(50) NOT NULL,
    entity_id       UUID NOT NULL,
    details         JSONB DEFAULT '{}'::jsonb,
    ip_address      INET,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Audit log is append-only; these indexes support common query patterns
CREATE INDEX idx_audit_log_tenant_id ON audit_log (tenant_id);
CREATE INDEX idx_audit_log_user_id ON audit_log (user_id);
CREATE INDEX idx_audit_log_entity ON audit_log (entity_type, entity_id);
CREATE INDEX idx_audit_log_action ON audit_log (action);
CREATE INDEX idx_audit_log_created_at ON audit_log (created_at);
CREATE INDEX idx_audit_log_tenant_created ON audit_log (tenant_id, created_at);
```

#### 10. signed_links

```sql
-- Short-lived signed/tokenized links for image viewing, exports, magic login, etc.
-- Token is a cryptographically random string validated on access.
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
```

#### 11. webhook_events

```sql
-- Idempotency table for inbound WhatsApp webhook messages.
-- whatsapp_message_id is UNIQUE to guarantee exactly-once processing.
-- retry_count tracks redelivery attempts for failed events.
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
```

---

### FUTURE Tables

#### 12. experts

```sql
-- FUTURE: Expert Pool
-- Domain experts who provide high-quality invoice reviews.
-- reputation_score is updated via a weighted algorithm after each review.
-- payout_config stores banking/payment preferences per expert.
CREATE TABLE experts (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    specializations     JSONB NOT NULL DEFAULT '[]'::jsonb,  -- e.g. ["construction","healthcare"]
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
```

#### 13. expert_assignments

```sql
-- FUTURE: Expert Pool
-- Assigns an invoice to an expert for review. Supports priority-based routing.
CREATE TABLE expert_assignments (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    invoice_id          UUID NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    expert_id           UUID NOT NULL REFERENCES experts(id) ON DELETE CASCADE,
    assignment_reason   VARCHAR(255),           -- e.g. 'low_confidence', 'user_flagged'
    priority            INTEGER NOT NULL DEFAULT 0,  -- higher = more urgent
    status              expert_assignment_status NOT NULL DEFAULT 'pending',
    assigned_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ,

    CONSTRAINT uq_assignment_invoice_expert UNIQUE (invoice_id, expert_id)
);

CREATE INDEX idx_expert_assignments_invoice ON expert_assignments (invoice_id);
CREATE INDEX idx_expert_assignments_expert ON expert_assignments (expert_id);
CREATE INDEX idx_expert_assignments_status ON expert_assignments (status);
CREATE INDEX idx_expert_assignments_priority ON expert_assignments (priority DESC, assigned_at);
```

#### 14. expert_reviews

```sql
-- FUTURE: Expert Pool
-- The expert's actual review submission for an assigned invoice.
-- strategy_notes captures the expert's reasoning for tax treatment decisions.
-- evidence_refs links to supporting documents or regulations.
CREATE TABLE expert_reviews (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    assignment_id           UUID NOT NULL REFERENCES expert_assignments(id) ON DELETE CASCADE,
    expert_id               UUID NOT NULL REFERENCES experts(id) ON DELETE CASCADE,
    corrected_json          JSONB NOT NULL,
    strategy_notes          JSONB DEFAULT '{}'::jsonb,
    evidence_refs           JSONB DEFAULT '[]'::jsonb,  -- e.g. [{"type":"regulation","ref":"BTW Art.15"}]
    review_quality_score    REAL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_expert_reviews_assignment ON expert_reviews (assignment_id);
CREATE INDEX idx_expert_reviews_expert ON expert_reviews (expert_id);
CREATE INDEX idx_expert_reviews_quality ON expert_reviews (review_quality_score DESC);
```

#### 15. expert_payouts

```sql
-- FUTURE: Expert Pool
-- Tracks payments owed and made to experts for completed reviews.
-- period is a fiscal period label (e.g. '2026-01').
CREATE TABLE expert_payouts (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    expert_id           UUID NOT NULL REFERENCES experts(id) ON DELETE CASCADE,
    period              VARCHAR(7) NOT NULL,    -- 'YYYY-MM'
    amount              NUMERIC(12, 2) NOT NULL,
    currency            VARCHAR(3) NOT NULL DEFAULT 'EUR',
    status              expert_payout_status NOT NULL DEFAULT 'pending',
    trigger_details     JSONB DEFAULT '{}'::jsonb,  -- which reviews/invoices triggered this payout
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    paid_at             TIMESTAMPTZ
);

CREATE INDEX idx_expert_payouts_expert ON expert_payouts (expert_id);
CREATE INDEX idx_expert_payouts_status ON expert_payouts (status);
CREATE INDEX idx_expert_payouts_period ON expert_payouts (period);
```

#### 16. expert_disputes

```sql
-- FUTURE: Expert Pool
-- Dispute resolution when experts disagree or a user challenges an expert review.
CREATE TABLE expert_disputes (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    invoice_id          UUID NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    expert_reviews_ids  UUID[] NOT NULL,        -- array of expert_reviews.id involved
    resolution_method   expert_dispute_resolution,
    resolved_by         UUID REFERENCES users(id) ON DELETE SET NULL,
    outcome             JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at         TIMESTAMPTZ
);

CREATE INDEX idx_expert_disputes_invoice ON expert_disputes (invoice_id);
CREATE INDEX idx_expert_disputes_resolved_by ON expert_disputes (resolved_by);
CREATE INDEX idx_expert_disputes_created_at ON expert_disputes (created_at);
```

#### 17. training_datasets

```sql
-- FUTURE: Expert Pool
-- Curated labelled data derived from corrections, expert reviews, and admin overrides.
-- Used to fine-tune extraction models. weight controls sampling priority.
CREATE TABLE training_datasets (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    version         VARCHAR(50) NOT NULL,       -- dataset version label
    source_type     training_source_type NOT NULL,
    invoice_id      UUID NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    label_json      JSONB NOT NULL,             -- the ground-truth structured data
    weight          REAL NOT NULL DEFAULT 1.0,  -- sampling weight for training
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_training_datasets_version ON training_datasets (version);
CREATE INDEX idx_training_datasets_source_type ON training_datasets (source_type);
CREATE INDEX idx_training_datasets_invoice ON training_datasets (invoice_id);
CREATE INDEX idx_training_datasets_created_at ON training_datasets (created_at);
```

---

### Updated-at Trigger (applied to all mutable tables)

```sql
-- Auto-update updated_at on row modification
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
```

---

## DELIVERABLE 2: Invoice Metadata JSON Schema

### TypeScript Interface

```typescript
// ============================================================
// ScanbonAI - Extracted Invoice Metadata
// ============================================================

/** Confidence score for any extracted field: 0.0 (no confidence) to 1.0 (certain) */
type Confidence = number; // 0.0 - 1.0

/** Per-field confidence wrapper */
interface FieldWithConfidence<T> {
  value: T | null;
  confidence: Confidence;
}

/** Supplier / vendor information */
interface Supplier {
  name: FieldWithConfidence<string>;
  address: FieldWithConfidence<string>;
  country: FieldWithConfidence<string>;      // ISO 3166-1 alpha-2
  vat_id: FieldWithConfidence<string>;       // e.g. "NL123456789B01"
  kvk_coc: FieldWithConfidence<string>;      // Dutch KVK / Chamber of Commerce number
  iban: FieldWithConfidence<string>;
}

/** Individual line item on the invoice */
interface LineItem {
  description: FieldWithConfidence<string>;
  quantity: FieldWithConfidence<number>;
  unit_price: FieldWithConfidence<number>;
  vat_rate: FieldWithConfidence<number>;     // e.g. 21.0 for 21%
  amount: FieldWithConfidence<number>;       // quantity * unit_price
}

/** AI-suggested expense category */
interface CategorySuggestion {
  category: string;           // e.g. "office_supplies", "travel", "professional_services"
  confidence: Confidence;
}

/** AI-suggested booking / accounting treatment */
interface BookingSuggestion {
  account_code: string;       // e.g. "4200" (general ledger code)
  cost_center: string | null; // e.g. "DEPT-ENGINEERING"
  tax_treatment: string;      // e.g. "input_vat_deductible", "reverse_charge", "exempt"
  confidence: Confidence;
}

/** Processing metadata */
interface ExtractionMetadata {
  ocr_model: string;          // e.g. "donut-v2"
  extraction_version: string; // e.g. "v1.2.0"
  processing_timestamp: string; // ISO 8601
}

/** Top-level extracted invoice metadata */
interface InvoiceMetadata {
  supplier: Supplier;
  invoice_number: FieldWithConfidence<string>;
  invoice_date: FieldWithConfidence<string>;    // ISO 8601 date: "YYYY-MM-DD"
  due_date: FieldWithConfidence<string>;        // ISO 8601 date: "YYYY-MM-DD"
  payment_terms: FieldWithConfidence<string>;   // e.g. "Net 30"
  currency: FieldWithConfidence<string>;        // ISO 4217: "EUR", "USD"
  subtotal: FieldWithConfidence<number>;
  vat_amount: FieldWithConfidence<number>;
  vat_rate: FieldWithConfidence<number>;        // primary VAT rate, e.g. 21.0
  total_amount: FieldWithConfidence<number>;
  line_items: LineItem[];
  category_suggestion: CategorySuggestion;
  booking_suggestion: BookingSuggestion;
  metadata: ExtractionMetadata;
}
```

### JSON Schema (Draft-07)

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "https://scanbonai.com/schemas/invoice-metadata/v1",
  "title": "ScanbonAI Invoice Metadata",
  "description": "Structured data extracted from a scanned invoice image, with per-field confidence scores.",
  "type": "object",
  "required": [
    "supplier",
    "invoice_number",
    "invoice_date",
    "due_date",
    "payment_terms",
    "currency",
    "subtotal",
    "vat_amount",
    "vat_rate",
    "total_amount",
    "line_items",
    "category_suggestion",
    "booking_suggestion",
    "metadata"
  ],
  "definitions": {
    "confidence": {
      "type": "number",
      "minimum": 0.0,
      "maximum": 1.0,
      "description": "Confidence score from 0.0 (no confidence) to 1.0 (certain)."
    },
    "field_string": {
      "type": "object",
      "required": ["value", "confidence"],
      "properties": {
        "value": { "type": ["string", "null"] },
        "confidence": { "$ref": "#/definitions/confidence" }
      },
      "additionalProperties": false
    },
    "field_number": {
      "type": "object",
      "required": ["value", "confidence"],
      "properties": {
        "value": { "type": ["number", "null"] },
        "confidence": { "$ref": "#/definitions/confidence" }
      },
      "additionalProperties": false
    },
    "supplier": {
      "type": "object",
      "required": ["name", "address", "country", "vat_id", "kvk_coc", "iban"],
      "properties": {
        "name":    { "$ref": "#/definitions/field_string" },
        "address": { "$ref": "#/definitions/field_string" },
        "country": {
          "allOf": [{ "$ref": "#/definitions/field_string" }],
          "description": "ISO 3166-1 alpha-2 country code"
        },
        "vat_id":  { "$ref": "#/definitions/field_string" },
        "kvk_coc": {
          "allOf": [{ "$ref": "#/definitions/field_string" }],
          "description": "Dutch KVK / Chamber of Commerce number"
        },
        "iban":    { "$ref": "#/definitions/field_string" }
      },
      "additionalProperties": false
    },
    "line_item": {
      "type": "object",
      "required": ["description", "quantity", "unit_price", "vat_rate", "amount"],
      "properties": {
        "description": { "$ref": "#/definitions/field_string" },
        "quantity":    { "$ref": "#/definitions/field_number" },
        "unit_price":  { "$ref": "#/definitions/field_number" },
        "vat_rate":    {
          "allOf": [{ "$ref": "#/definitions/field_number" }],
          "description": "VAT percentage, e.g. 21.0 for 21%"
        },
        "amount":      { "$ref": "#/definitions/field_number" }
      },
      "additionalProperties": false
    },
    "category_suggestion": {
      "type": "object",
      "required": ["category", "confidence"],
      "properties": {
        "category":   { "type": "string" },
        "confidence": { "$ref": "#/definitions/confidence" }
      },
      "additionalProperties": false
    },
    "booking_suggestion": {
      "type": "object",
      "required": ["account_code", "cost_center", "tax_treatment", "confidence"],
      "properties": {
        "account_code":  { "type": "string" },
        "cost_center":   { "type": ["string", "null"] },
        "tax_treatment": { "type": "string" },
        "confidence":    { "$ref": "#/definitions/confidence" }
      },
      "additionalProperties": false
    },
    "extraction_metadata": {
      "type": "object",
      "required": ["ocr_model", "extraction_version", "processing_timestamp"],
      "properties": {
        "ocr_model":            { "type": "string" },
        "extraction_version":   { "type": "string" },
        "processing_timestamp": {
          "type": "string",
          "format": "date-time"
        }
      },
      "additionalProperties": false
    }
  },
  "properties": {
    "supplier":            { "$ref": "#/definitions/supplier" },
    "invoice_number":      { "$ref": "#/definitions/field_string" },
    "invoice_date":        {
      "allOf": [{ "$ref": "#/definitions/field_string" }],
      "description": "ISO 8601 date string YYYY-MM-DD"
    },
    "due_date":            {
      "allOf": [{ "$ref": "#/definitions/field_string" }],
      "description": "ISO 8601 date string YYYY-MM-DD"
    },
    "payment_terms":       { "$ref": "#/definitions/field_string" },
    "currency":            {
      "allOf": [{ "$ref": "#/definitions/field_string" }],
      "description": "ISO 4217 currency code"
    },
    "subtotal":            { "$ref": "#/definitions/field_number" },
    "vat_amount":          { "$ref": "#/definitions/field_number" },
    "vat_rate":            {
      "allOf": [{ "$ref": "#/definitions/field_number" }],
      "description": "Primary VAT rate as percentage, e.g. 21.0"
    },
    "total_amount":        { "$ref": "#/definitions/field_number" },
    "line_items": {
      "type": "array",
      "items": { "$ref": "#/definitions/line_item" },
      "minItems": 0
    },
    "category_suggestion": { "$ref": "#/definitions/category_suggestion" },
    "booking_suggestion":  { "$ref": "#/definitions/booking_suggestion" },
    "metadata":            { "$ref": "#/definitions/extraction_metadata" }
  },
  "additionalProperties": false
}
```

### Example Invoice Metadata Document

```json
{
  "supplier": {
    "name":    { "value": "Bakkerij Van Dijk B.V.", "confidence": 0.97 },
    "address": { "value": "Keizersgracht 123, 1015 CJ Amsterdam", "confidence": 0.91 },
    "country": { "value": "NL", "confidence": 0.99 },
    "vat_id":  { "value": "NL123456789B01", "confidence": 0.95 },
    "kvk_coc": { "value": "12345678", "confidence": 0.88 },
    "iban":    { "value": "NL91ABNA0417164300", "confidence": 0.93 }
  },
  "invoice_number": { "value": "INV-2026-00142", "confidence": 0.98 },
  "invoice_date":   { "value": "2026-02-15", "confidence": 0.96 },
  "due_date":       { "value": "2026-03-17", "confidence": 0.90 },
  "payment_terms":  { "value": "Net 30", "confidence": 0.85 },
  "currency":       { "value": "EUR", "confidence": 0.99 },
  "subtotal":       { "value": 1250.00, "confidence": 0.94 },
  "vat_amount":     { "value": 262.50, "confidence": 0.94 },
  "vat_rate":       { "value": 21.0, "confidence": 0.97 },
  "total_amount":   { "value": 1512.50, "confidence": 0.95 },
  "line_items": [
    {
      "description": { "value": "Catering services - February event", "confidence": 0.92 },
      "quantity":    { "value": 1, "confidence": 0.99 },
      "unit_price":  { "value": 750.00, "confidence": 0.93 },
      "vat_rate":    { "value": 21.0, "confidence": 0.97 },
      "amount":      { "value": 750.00, "confidence": 0.95 }
    },
    {
      "description": { "value": "Delivery fee", "confidence": 0.89 },
      "quantity":    { "value": 1, "confidence": 0.99 },
      "unit_price":  { "value": 500.00, "confidence": 0.91 },
      "vat_rate":    { "value": 21.0, "confidence": 0.97 },
      "amount":      { "value": 500.00, "confidence": 0.93 }
    }
  ],
  "category_suggestion": {
    "category": "catering",
    "confidence": 0.87
  },
  "booking_suggestion": {
    "account_code": "4600",
    "cost_center": "EVENTS",
    "tax_treatment": "input_vat_deductible",
    "confidence": 0.82
  },
  "metadata": {
    "ocr_model": "donut-v2",
    "extraction_version": "v1.2.0",
    "processing_timestamp": "2026-02-15T14:32:07.123Z"
  }
}
```

---

## DELIVERABLE 3: API Endpoints

### Authentication Model

All endpoints except the webhook and public signed-link resolution require a valid bearer token. The token is obtained via magic-link authentication.

```
Authorization: Bearer <token>
```

Role-based access:
- **user** -- own invoices only
- **admin** -- all invoices within their tenant
- **superadmin** -- cross-tenant access

---

### MVP Endpoint Reference

| # | Method | Path | Description | Auth |
|---|--------|------|-------------|------|
| 1 | POST | `/api/v1/webhooks/whatsapp` | Receive inbound WhatsApp messages | HMAC signature |
| 2 | GET | `/api/v1/invoices` | List invoices (filtered) | user+ |
| 3 | GET | `/api/v1/invoices/{id}` | Get invoice detail with extraction | user+ |
| 4 | GET | `/api/v1/invoices/{id}/image` | Serve invoice image (signed URL redirect) | user+ |
| 5 | PUT | `/api/v1/invoices/{id}/corrections` | Submit user corrections | user+ |
| 6 | POST | `/api/v1/invoices/{id}/approve` | Admin approve invoice | admin+ |
| 7 | POST | `/api/v1/invoices/{id}/override` | Admin override with changes | admin+ |
| 8 | GET | `/api/v1/admin/metrics` | Dashboard metrics | admin+ |
| 9 | GET | `/api/v1/admin/invoices` | Admin invoice list with diffs | admin+ |
| 10 | POST | `/api/v1/auth/magic-link` | Request magic link via WhatsApp | none |
| 11 | GET | `/api/v1/auth/verify` | Verify magic link token | none |
| 12 | GET | `/api/v1/export/invoices` | Export invoices as JSON/CSV | admin+ |
| 13 | GET | `/api/v1/signed/{token}` | Resolve signed link | none (token-based) |

### FUTURE Endpoint Stubs

| # | Method | Path | Description | Auth |
|---|--------|------|-------------|------|
| F1 | GET | `/api/v1/expert/queue` | Expert review queue | expert |
| F2 | POST | `/api/v1/expert/reviews/{assignment_id}` | Submit expert review | expert |
| F3 | GET | `/api/v1/admin/experts` | Manage expert pool | admin+ |
| F4 | GET | `/api/v1/admin/payouts` | Payout management | admin+ |
| F5 | POST | `/api/v1/expert/disputes/{id}/resolve` | Resolve expert dispute | admin+ |

---

### Detailed Endpoint Specifications (Top 5)

---

#### 1. POST `/api/v1/webhooks/whatsapp`

Receives inbound WhatsApp messages from the WhatsApp Business API (Meta Cloud API). Validates the HMAC-SHA256 signature in the `X-Hub-Signature-256` header. Uses `webhook_events` table for idempotency.

**Auth:** HMAC signature verification (no bearer token)

**Request Headers:**
```
Content-Type: application/json
X-Hub-Signature-256: sha256=<hmac_hex_digest>
```

**Request Body:**
```json
{
  "object": "whatsapp_business_account",
  "entry": [
    {
      "id": "WHATSAPP_BUSINESS_ACCOUNT_ID",
      "changes": [
        {
          "value": {
            "messaging_product": "whatsapp",
            "metadata": {
              "display_phone_number": "31612345678",
              "phone_number_id": "PHONE_NUMBER_ID"
            },
            "messages": [
              {
                "from": "31687654321",
                "id": "wamid.HBgNMzE2MTIzNDU2NzgVAgASGBQzQUY3RkE3Njc2REQ1MEE0MDUA",
                "timestamp": "1708012345",
                "type": "image",
                "image": {
                  "mime_type": "image/jpeg",
                  "sha256": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
                  "id": "MEDIA_ID_123456",
                  "caption": "Factuur februari kantoorspullen"
                }
              }
            ]
          },
          "field": "messages"
        }
      ]
    }
  ]
}
```

**Response (200 OK -- acknowledged):**
```json
{
  "status": "accepted",
  "event_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "message": "Webhook event received and queued for processing."
}
```

**Response (409 Conflict -- duplicate):**
```json
{
  "status": "duplicate",
  "event_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "message": "This webhook event has already been processed."
}
```

**Response (401 Unauthorized -- bad signature):**
```json
{
  "error": "invalid_signature",
  "message": "HMAC signature verification failed."
}
```

**Processing Flow:**
1. Verify HMAC signature
2. Check `webhook_events` for duplicate `whatsapp_message_id` (idempotency)
3. Insert row with status `received`
4. Look up or auto-register user by phone number
5. Download media via WhatsApp Media API
6. Compute `file_hash`, check for duplicate invoice
7. Store file, create `invoices` row with status `uploaded`
8. Enqueue quality check + OCR pipeline job
9. Update webhook event status to `processed`

---

#### 2. GET `/api/v1/invoices`

Lists invoices for the authenticated user (or all tenant invoices for admins). Supports pagination, filtering by status, month, and date range.

**Auth:** Bearer token (user+)

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `page` | integer | no | Page number (default: 1) |
| `per_page` | integer | no | Items per page (default: 20, max: 100) |
| `status` | string | no | Filter by status (comma-separated) |
| `month` | string | no | Filter by month_partition (YYYY-MM) |
| `from_date` | string | no | ISO 8601 date lower bound |
| `to_date` | string | no | ISO 8601 date upper bound |
| `sort` | string | no | Sort field (default: `created_at`) |
| `order` | string | no | `asc` or `desc` (default: `desc`) |

**Example Request:**
```
GET /api/v1/invoices?month=2026-02&status=extracted,reviewed&page=1&per_page=10
Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
```

**Response (200 OK):**
```json
{
  "data": [
    {
      "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
      "tenant_id": "c56a4180-65aa-42ec-a945-5fd21dec0538",
      "user_id": "b3daa77b-70a7-4b38-840d-3b5680c7c1b6",
      "file_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "status": "extracted",
      "upload_source": "whatsapp",
      "month_partition": "2026-02",
      "created_at": "2026-02-15T14:30:00.000Z",
      "updated_at": "2026-02-15T14:32:10.000Z",
      "extraction_summary": {
        "supplier_name": "Bakkerij Van Dijk B.V.",
        "invoice_number": "INV-2026-00142",
        "total_amount": 1512.50,
        "currency": "EUR",
        "overall_confidence": 0.94
      }
    },
    {
      "id": "9a8b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d",
      "tenant_id": "c56a4180-65aa-42ec-a945-5fd21dec0538",
      "user_id": "b3daa77b-70a7-4b38-840d-3b5680c7c1b6",
      "file_hash": "d7a8fbb307d7809469ca9abcb0082e4f8d5651e46d3cdb762d02d0bf37c9e592",
      "status": "reviewed",
      "upload_source": "whatsapp",
      "month_partition": "2026-02",
      "created_at": "2026-02-10T09:15:00.000Z",
      "updated_at": "2026-02-11T11:20:00.000Z",
      "extraction_summary": {
        "supplier_name": "Schoonmaakbedrijf Glans",
        "invoice_number": "G-2026-0087",
        "total_amount": 484.00,
        "currency": "EUR",
        "overall_confidence": 0.89
      }
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 10,
    "total_items": 2,
    "total_pages": 1
  }
}
```

---

#### 3. GET `/api/v1/invoices/{id}`

Returns full invoice detail including quality check results, extracted data, any user corrections, and admin review history.

**Auth:** Bearer token (user+ -- own invoices; admin+ -- any invoice in tenant)

**Example Request:**
```
GET /api/v1/invoices/f47ac10b-58cc-4372-a567-0e02b2c3d479
Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
```

**Response (200 OK):**
```json
{
  "data": {
    "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    "tenant_id": "c56a4180-65aa-42ec-a945-5fd21dec0538",
    "user_id": "b3daa77b-70a7-4b38-840d-3b5680c7c1b6",
    "file_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "status": "extracted",
    "upload_source": "whatsapp",
    "whatsapp_message_id": "wamid.HBgNMzE2MTIzNDU2NzgVAgASGBQzQUY3RkE3Njc2REQ1MEE0MDUA",
    "month_partition": "2026-02",
    "created_at": "2026-02-15T14:30:00.000Z",
    "updated_at": "2026-02-15T14:32:10.000Z",
    "image_url": "/api/v1/invoices/f47ac10b-58cc-4372-a567-0e02b2c3d479/image",
    "quality_check": {
      "id": "d1e2f3a4-b5c6-7890-d1e2-f3a4b5c67890",
      "blur_score": 245.7,
      "resolution_ok": true,
      "skew_angle": 1.2,
      "shadow_score": 0.15,
      "exposure_score": 0.52,
      "overall_pass": true,
      "failure_reasons": [],
      "checked_at": "2026-02-15T14:30:05.000Z"
    },
    "extracted_data": {
      "id": "e4d3c2b1-a098-7654-e4d3-c2b1a0987654",
      "extraction_version": "v1.2.0",
      "created_at": "2026-02-15T14:32:07.123Z",
      "data": {
        "supplier": {
          "name":    { "value": "Bakkerij Van Dijk B.V.", "confidence": 0.97 },
          "address": { "value": "Keizersgracht 123, 1015 CJ Amsterdam", "confidence": 0.91 },
          "country": { "value": "NL", "confidence": 0.99 },
          "vat_id":  { "value": "NL123456789B01", "confidence": 0.95 },
          "kvk_coc": { "value": "12345678", "confidence": 0.88 },
          "iban":    { "value": "NL91ABNA0417164300", "confidence": 0.93 }
        },
        "invoice_number": { "value": "INV-2026-00142", "confidence": 0.98 },
        "invoice_date":   { "value": "2026-02-15", "confidence": 0.96 },
        "due_date":       { "value": "2026-03-17", "confidence": 0.90 },
        "payment_terms":  { "value": "Net 30", "confidence": 0.85 },
        "currency":       { "value": "EUR", "confidence": 0.99 },
        "subtotal":       { "value": 1250.00, "confidence": 0.94 },
        "vat_amount":     { "value": 262.50, "confidence": 0.94 },
        "vat_rate":       { "value": 21.0, "confidence": 0.97 },
        "total_amount":   { "value": 1512.50, "confidence": 0.95 },
        "line_items": [
          {
            "description": { "value": "Catering services - February event", "confidence": 0.92 },
            "quantity":    { "value": 1, "confidence": 0.99 },
            "unit_price":  { "value": 750.00, "confidence": 0.93 },
            "vat_rate":    { "value": 21.0, "confidence": 0.97 },
            "amount":      { "value": 750.00, "confidence": 0.95 }
          },
          {
            "description": { "value": "Delivery fee", "confidence": 0.89 },
            "quantity":    { "value": 1, "confidence": 0.99 },
            "unit_price":  { "value": 500.00, "confidence": 0.91 },
            "vat_rate":    { "value": 21.0, "confidence": 0.97 },
            "amount":      { "value": 500.00, "confidence": 0.93 }
          }
        ],
        "category_suggestion": { "category": "catering", "confidence": 0.87 },
        "booking_suggestion": {
          "account_code": "4600",
          "cost_center": "EVENTS",
          "tax_treatment": "input_vat_deductible",
          "confidence": 0.82
        },
        "metadata": {
          "ocr_model": "donut-v2",
          "extraction_version": "v1.2.0",
          "processing_timestamp": "2026-02-15T14:32:07.123Z"
        }
      }
    },
    "corrections": [],
    "admin_reviews": []
  }
}
```

**Response (404 Not Found):**
```json
{
  "error": "not_found",
  "message": "Invoice not found or you do not have access."
}
```

---

#### 4. PUT `/api/v1/invoices/{id}/corrections`

Submits user corrections to extracted invoice data. The server computes the diff between the original extraction and the corrected version. Updates the invoice status to `reviewed`.

**Auth:** Bearer token (user+ -- own invoices only)

**Example Request:**
```
PUT /api/v1/invoices/f47ac10b-58cc-4372-a567-0e02b2c3d479/corrections
Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
Content-Type: application/json
```

**Request Body:**
```json
{
  "extracted_data_id": "e4d3c2b1-a098-7654-e4d3-c2b1a0987654",
  "corrected_fields": {
    "supplier": {
      "kvk_coc": { "value": "87654321", "confidence": 1.0 }
    },
    "due_date": { "value": "2026-03-15", "confidence": 1.0 },
    "line_items": [
      {
        "index": 1,
        "description": { "value": "Express delivery fee", "confidence": 1.0 }
      }
    ]
  }
}
```

**Response (200 OK):**
```json
{
  "data": {
    "correction_id": "c1d2e3f4-a5b6-7890-c1d2-e3f4a5b67890",
    "invoice_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    "status": "reviewed",
    "diff": {
      "supplier.kvk_coc": {
        "old": { "value": "12345678", "confidence": 0.88 },
        "new": { "value": "87654321", "confidence": 1.0 }
      },
      "due_date": {
        "old": { "value": "2026-03-17", "confidence": 0.90 },
        "new": { "value": "2026-03-15", "confidence": 1.0 }
      },
      "line_items[1].description": {
        "old": { "value": "Delivery fee", "confidence": 0.89 },
        "new": { "value": "Express delivery fee", "confidence": 1.0 }
      }
    },
    "created_at": "2026-02-16T10:05:00.000Z"
  }
}
```

**Response (409 Conflict -- invoice already approved):**
```json
{
  "error": "conflict",
  "message": "Cannot correct an invoice that has already been approved or exported."
}
```

---

#### 5. POST `/api/v1/webhooks/whatsapp` (Verification / GET variant)

This is the webhook verification endpoint used during WhatsApp Cloud API setup. WhatsApp sends a GET request to verify the endpoint.

Since the webhook was already detailed as #1, we use this slot for **POST `/api/v1/invoices/{id}/approve`** instead.

#### 5. POST `/api/v1/invoices/{id}/approve`

Admin approves an invoice after review. Sets invoice status to `approved`. Creates an audit log entry.

**Auth:** Bearer token (admin+)

**Example Request:**
```
POST /api/v1/invoices/f47ac10b-58cc-4372-a567-0e02b2c3d479/approve
Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
Content-Type: application/json
```

**Request Body:**
```json
{
  "notes": "Verified against original supplier quote. KVK correction accepted."
}
```

**Response (200 OK):**
```json
{
  "data": {
    "review_id": "r1a2b3c4-d5e6-7890-f1a2-b3c4d5e67890",
    "invoice_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    "action": "approved",
    "reviewer_id": "a1b2c3d4-e5f6-7890-a1b2-c3d4e5f67890",
    "status": "approved",
    "notes": "Verified against original supplier quote. KVK correction accepted.",
    "created_at": "2026-02-16T15:30:00.000Z"
  }
}
```

**Response (403 Forbidden):**
```json
{
  "error": "forbidden",
  "message": "Only admin or superadmin users can approve invoices."
}
```

**Response (422 Unprocessable Entity):**
```json
{
  "error": "invalid_state",
  "message": "Invoice must be in 'extracted' or 'reviewed' status to be approved. Current status: 'uploaded'."
}
```

---

### Remaining MVP Endpoints (Compact Specification)

---

#### 6. POST `/api/v1/invoices/{id}/override`

Admin overrides extracted data with their own values and approves.

**Auth:** Bearer token (admin+)

**Request Body:**
```json
{
  "override_fields": {
    "total_amount": { "value": 1500.00 },
    "vat_amount": { "value": 260.00 },
    "booking_suggestion": {
      "account_code": "4700",
      "cost_center": "GENERAL",
      "tax_treatment": "input_vat_deductible"
    }
  },
  "notes": "Adjusted total after verifying with supplier. Original receipt had a typo."
}
```

**Response (200 OK):**
```json
{
  "data": {
    "review_id": "o1p2q3r4-s5t6-7890-u1v2-w3x4y5z67890",
    "invoice_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    "action": "overridden",
    "status": "approved",
    "override_json": { "...override_fields as submitted..." },
    "notes": "Adjusted total after verifying with supplier. Original receipt had a typo.",
    "created_at": "2026-02-16T16:00:00.000Z"
  }
}
```

---

#### 7. GET `/api/v1/invoices/{id}/image`

Generates a short-lived signed URL and redirects to the stored image file. The signed link expires after 15 minutes.

**Auth:** Bearer token (user+ -- own invoices; admin+ -- any)

**Response (302 Redirect):**
```
HTTP/1.1 302 Found
Location: /api/v1/signed/abc123xyz...?t=1708012345
Cache-Control: no-store
```

**Alternative Response (200 OK with URL):**
```json
{
  "data": {
    "signed_url": "/api/v1/signed/abc123xyz456def789ghi012jkl345mno",
    "expires_at": "2026-02-15T15:00:00.000Z",
    "content_type": "image/jpeg"
  }
}
```

---

#### 8. GET `/api/v1/admin/metrics`

Dashboard metrics for admins. Returns counts and breakdowns by status, processing times, and correction rates.

**Auth:** Bearer token (admin+)

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `month` | string | Filter by month (YYYY-MM). Default: current month |
| `from_date` | string | ISO 8601 start date |
| `to_date` | string | ISO 8601 end date |

**Response (200 OK):**
```json
{
  "data": {
    "period": {
      "from": "2026-02-01",
      "to": "2026-02-28"
    },
    "totals": {
      "invoices_uploaded": 347,
      "invoices_processed": 312,
      "invoices_approved": 289,
      "invoices_exported": 265,
      "quality_failures": 18
    },
    "status_breakdown": {
      "uploaded": 5,
      "quality_failed": 18,
      "processing": 2,
      "extracted": 15,
      "reviewed": 18,
      "approved": 24,
      "exported": 265
    },
    "processing": {
      "avg_ocr_time_ms": 2340,
      "avg_end_to_end_minutes": 12.5,
      "p95_ocr_time_ms": 4100
    },
    "accuracy": {
      "invoices_with_corrections": 67,
      "correction_rate_pct": 21.5,
      "avg_confidence": 0.91,
      "most_corrected_fields": [
        { "field": "supplier.kvk_coc", "count": 23 },
        { "field": "due_date", "count": 18 },
        { "field": "line_items.description", "count": 14 }
      ]
    },
    "admin_activity": {
      "approvals": 289,
      "overrides": 12,
      "flags": 3
    }
  }
}
```

---

#### 9. GET `/api/v1/admin/invoices`

Admin invoice list showing extraction data alongside any corrections/diffs. Supports the same filters as the user endpoint, plus shows correction diffs inline.

**Auth:** Bearer token (admin+)

**Query Parameters:** Same as `GET /api/v1/invoices` plus:

| Parameter | Type | Description |
|-----------|------|-------------|
| `has_corrections` | boolean | Filter to invoices with user corrections |
| `has_overrides` | boolean | Filter to invoices with admin overrides |
| `min_confidence` | float | Minimum overall confidence (0.0-1.0) |
| `max_confidence` | float | Maximum overall confidence (0.0-1.0) |

**Response (200 OK):**
```json
{
  "data": [
    {
      "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
      "status": "reviewed",
      "month_partition": "2026-02",
      "created_at": "2026-02-15T14:30:00.000Z",
      "user": {
        "id": "b3daa77b-70a7-4b38-840d-3b5680c7c1b6",
        "display_name": "Pieter Jansen",
        "whatsapp_phone": "+31687654321"
      },
      "extraction_summary": {
        "supplier_name": "Bakkerij Van Dijk B.V.",
        "invoice_number": "INV-2026-00142",
        "total_amount": 1512.50,
        "currency": "EUR",
        "overall_confidence": 0.94
      },
      "has_corrections": true,
      "correction_count": 1,
      "latest_diff": {
        "supplier.kvk_coc": {
          "old": "12345678",
          "new": "87654321"
        },
        "due_date": {
          "old": "2026-03-17",
          "new": "2026-03-15"
        }
      },
      "admin_review": null
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 20,
    "total_items": 1,
    "total_pages": 1
  }
}
```

---

#### 10. POST `/api/v1/auth/magic-link`

Sends a magic link to the user's WhatsApp number. The link contains a short-lived token that can be verified to obtain an auth session.

**Auth:** None

**Request Body:**
```json
{
  "whatsapp_phone": "+31687654321",
  "tenant_id": "c56a4180-65aa-42ec-a945-5fd21dec0538"
}
```

**Response (200 OK):**
```json
{
  "status": "sent",
  "message": "Magic link sent to your WhatsApp. It expires in 10 minutes.",
  "expires_in_seconds": 600
}
```

**Response (404 Not Found):**
```json
{
  "error": "not_found",
  "message": "No user found with this phone number for the specified tenant."
}
```

---

#### 11. GET `/api/v1/auth/verify`

Verifies a magic link token and returns a session bearer token.

**Auth:** None

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `token` | string | The magic link token |

**Example Request:**
```
GET /api/v1/auth/verify?token=ml_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6
```

**Response (200 OK):**
```json
{
  "data": {
    "user_id": "b3daa77b-70a7-4b38-840d-3b5680c7c1b6",
    "tenant_id": "c56a4180-65aa-42ec-a945-5fd21dec0538",
    "display_name": "Pieter Jansen",
    "role": "user",
    "auth_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyX2lkIjoiYjNkYWE3N2ItNzBhNy00YjM4LTg0MGQtM2I1NjgwYzdjMWI2IiwidGVuYW50X2lkIjoiYzU2YTQxODAtNjVhYS00MmVjLWE5NDUtNWZkMjFkZWMwNTM4Iiwicm9sZSI6InVzZXIiLCJleHAiOjE3MDgxMDAwMDB9.signature",
    "expires_at": "2026-02-22T14:30:00.000Z"
  }
}
```

**Response (401 Unauthorized):**
```json
{
  "error": "invalid_token",
  "message": "Token is invalid or has expired."
}
```

---

#### 12. GET `/api/v1/export/invoices`

Exports approved invoices as JSON or CSV for accounting systems.

**Auth:** Bearer token (admin+)

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `format` | string | `json` or `csv` (default: `json`) |
| `month` | string | Filter by month (YYYY-MM) |
| `from_date` | string | ISO 8601 start date |
| `to_date` | string | ISO 8601 end date |
| `status` | string | Filter by status (default: `approved,exported`) |

**Response (200 OK -- JSON format):**
```json
{
  "data": {
    "export_id": "exp-2026-02-22-001",
    "format": "json",
    "generated_at": "2026-02-22T10:00:00.000Z",
    "invoice_count": 265,
    "invoices": [
      {
        "invoice_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
        "supplier_name": "Bakkerij Van Dijk B.V.",
        "supplier_vat_id": "NL123456789B01",
        "invoice_number": "INV-2026-00142",
        "invoice_date": "2026-02-15",
        "due_date": "2026-03-15",
        "currency": "EUR",
        "subtotal": 1250.00,
        "vat_rate": 21.0,
        "vat_amount": 262.50,
        "total_amount": 1512.50,
        "account_code": "4600",
        "cost_center": "EVENTS",
        "tax_treatment": "input_vat_deductible",
        "category": "catering",
        "approved_at": "2026-02-16T15:30:00.000Z",
        "approved_by": "Admin User"
      }
    ]
  }
}
```

**Response (200 OK -- CSV format):**
```
Content-Type: text/csv
Content-Disposition: attachment; filename="scanbonai-export-2026-02.csv"

invoice_id,supplier_name,supplier_vat_id,invoice_number,invoice_date,due_date,currency,subtotal,vat_rate,vat_amount,total_amount,account_code,cost_center,tax_treatment,category,approved_at
f47ac10b-58cc-4372-a567-0e02b2c3d479,"Bakkerij Van Dijk B.V.",NL123456789B01,INV-2026-00142,2026-02-15,2026-03-15,EUR,1250.00,21.0,262.50,1512.50,4600,EVENTS,input_vat_deductible,catering,2026-02-16T15:30:00.000Z
```

---

#### 13. GET `/api/v1/signed/{token}`

Resolves a signed link token and serves the associated resource (image, PDF, export file). Validates token existence, expiration, and records access time.

**Auth:** None (token-based authentication)

**Example Request:**
```
GET /api/v1/signed/abc123xyz456def789ghi012jkl345mno
```

**Response (200 OK -- for image_view):**
```
Content-Type: image/jpeg
Content-Length: 245678
Cache-Control: private, max-age=900
X-ScanbonAI-Invoice-Id: f47ac10b-58cc-4372-a567-0e02b2c3d479

<binary image data>
```

**Response (410 Gone -- expired):**
```json
{
  "error": "link_expired",
  "message": "This signed link has expired. Please request a new one."
}
```

**Response (404 Not Found):**
```json
{
  "error": "not_found",
  "message": "Invalid signed link token."
}
```

---

### FUTURE Endpoint Stubs (Compact)

---

#### F1. GET `/api/v1/expert/queue`

Returns the list of invoices assigned to the authenticated expert for review.

**Auth:** Bearer token (expert role required)

**Query Parameters:** `status`, `priority`, `page`, `per_page`

**Response:** Paginated list of `expert_assignments` with invoice summaries.

---

#### F2. POST `/api/v1/expert/reviews/{assignment_id}`

Submits an expert's review for an assigned invoice.

**Auth:** Bearer token (expert role required)

**Request Body:**
```json
{
  "corrected_json": { "...full corrected invoice metadata..." },
  "strategy_notes": {
    "tax_treatment_reasoning": "Reverse charge applies under EU B2B services directive.",
    "category_reasoning": "Professional services, not consulting -- distinct GL code."
  },
  "evidence_refs": [
    { "type": "regulation", "ref": "EU VAT Directive Art. 44" },
    { "type": "precedent", "ref": "Similar invoice INV-2025-00891" }
  ]
}
```

**Response:** Created expert review object with `review_quality_score` (computed asynchronously).

---

#### F3. GET `/api/v1/admin/experts`

Lists all experts with filtering by status, specialization, and reputation score.

**Auth:** Bearer token (admin+)

**Query Parameters:** `status`, `specialization`, `min_reputation`, `page`, `per_page`

**Response:** Paginated list of expert profiles with review statistics.

---

#### F4. GET `/api/v1/admin/payouts`

Lists expert payouts with filtering by period, status, and expert.

**Auth:** Bearer token (admin+)

**Query Parameters:** `period`, `status`, `expert_id`, `page`, `per_page`

**Response:** Paginated list of payout records with totals.

---

#### F5. POST `/api/v1/expert/disputes/{id}/resolve`

Resolves an expert dispute with admin decision.

**Auth:** Bearer token (admin+)

**Request Body:**
```json
{
  "resolution_method": "admin_override",
  "outcome": {
    "accepted_review_id": "r1a2b3c4-d5e6-7890-f1a2-b3c4d5e67890",
    "final_data": { "...merged invoice metadata..." },
    "reasoning": "Expert A's VAT treatment is correct per Art. 44."
  }
}
```

**Response:** Updated dispute record with resolution details.

---

### Standard Error Response Format

All error responses follow a consistent structure:

```json
{
  "error": "error_code",
  "message": "Human-readable description of the error.",
  "details": {}
}
```

**Common HTTP Status Codes:**

| Code | Meaning | Example |
|------|---------|---------|
| 400 | Bad Request | Invalid JSON, missing required fields |
| 401 | Unauthorized | Missing or invalid bearer token |
| 403 | Forbidden | Insufficient role for this operation |
| 404 | Not Found | Resource does not exist or no access |
| 409 | Conflict | Duplicate resource or invalid state transition |
| 422 | Unprocessable Entity | Valid JSON but business rule violation |
| 429 | Too Many Requests | Rate limit exceeded |
| 500 | Internal Server Error | Unexpected server failure |

---

### Rate Limiting

All endpoints are rate-limited per tenant:

| Tier | Limit |
|------|-------|
| Webhook (POST) | 100 req/min |
| Read (GET) | 300 req/min |
| Write (PUT/POST) | 60 req/min |
| Export | 10 req/min |

Rate limit headers are included in every response:
```
X-RateLimit-Limit: 300
X-RateLimit-Remaining: 297
X-RateLimit-Reset: 1708012400
```
