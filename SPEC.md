# ScanbonAI -- Tax Administration via WhatsApp Invoices

**Version:** 0.1.0-MVP
**Date:** 2026-02-22
**Status:** Draft Specification

---

# DELIVERABLE 1: Product Manager Report

---

## 1. MVP Scope & User Stories

### US-01: WhatsApp Invoice Intake

> **As a** small-business owner,
> **I want** to send a photo of my invoice to a WhatsApp number,
> **so that** the system automatically ingests it for tax processing without me needing a separate app.

**Acceptance Criteria:**

- The system accepts images (JPEG, PNG, HEIC) and PDF attachments via WhatsApp Business API.
- Each incoming message is associated with the sender's phone number and resolved to a `tenant_id` + `user_id`.
- The system stores the original file in tenant-isolated storage at `/data/{tenant_id}/{user_id}/YYYY-MM/{uuid}.{ext}`.
- A background job is enqueued within 2 seconds of receipt.
- Duplicate detection: if the same file hash is received from the same user within 24 hours, the system responds with a link to the existing receipt rather than creating a duplicate.
- The system responds with a WhatsApp acknowledgment message within 5 seconds: *"Receipt received. Processing your invoice..."*

---

### US-02: Receipt Confirmation via WhatsApp with Secure Link

> **As a** small-business owner,
> **I want** to receive a WhatsApp confirmation with a secure link to review the extracted data,
> **so that** I can verify the OCR results without logging in to a separate portal.

**Acceptance Criteria:**

- After OCR processing completes, the system sends a WhatsApp message containing: extracted vendor name, total amount, date, and a signed URL.
- The signed URL is valid for 72 hours and scoped to the specific receipt.
- The URL opens a mobile-optimized review page (React frontend) requiring no login -- the signed token acts as authentication.
- The confirmation message is sent within 60 seconds of ingestion for receipts under 5 MB.
- If OCR confidence is below the quality threshold (see US-04), the rejection flow (US-03) is triggered instead.

---

### US-03: Quality Gate -- Unreadable Invoice Rejection

> **As a** small-business owner,
> **I want** to be notified immediately when my invoice photo is unreadable,
> **so that** I can retake the photo and resubmit without waiting for manual review.

**Acceptance Criteria:**

- The OCR pipeline assigns an overall `readability_score` (0.0 - 1.0) to each image.
- If `readability_score < 0.40`, the receipt is marked `status = REJECTED_QUALITY`.
- The system sends a WhatsApp message within 30 seconds: *"We couldn't read your invoice. Common issues: blurry photo, poor lighting, folded paper. Please retake and resend."*
- The message includes a thumbnail of the problematic image for context.
- The original file is retained for audit purposes but not processed further unless resubmitted.
- Rejection events are logged with reason codes: `BLUR`, `LOW_CONTRAST`, `TRUNCATED`, `UNSUPPORTED_LANGUAGE`.

---

### US-04: OCR Extraction with Confidence Scores

> **As a** system (automated pipeline),
> **I want** to extract structured fields from invoices with per-field confidence scores,
> **so that** downstream review can focus human attention on low-confidence fields.

**Acceptance Criteria:**

- The OCR pipeline extracts at minimum: `vendor_name`, `invoice_date`, `invoice_number`, `line_items[]` (description, quantity, unit_price, vat_rate, line_total), `subtotal`, `vat_amount`, `total_amount`, `currency`, `payment_method`.
- Each field carries a `confidence` float (0.0 - 1.0).
- Fields with `confidence < 0.70` are flagged as `needs_review = true`.
- The overall receipt `confidence_score` is the weighted average of all field confidences.
- Extraction results are persisted in PostgreSQL with full field-level provenance (raw OCR text, normalized value, confidence).
- The pipeline supports Dutch, English, French, and German invoices at MVP.

---

### US-05: User Review/Edit Page for Extracted Metadata

> **As a** small-business owner,
> **I want** to review and correct the extracted invoice data on a web page,
> **so that** my tax records are accurate before they are finalized.

**Acceptance Criteria:**

- The review page displays the original invoice image side-by-side with extracted fields.
- Fields flagged `needs_review` are visually highlighted (amber border).
- The user can edit any field; edits are tracked as `user_override` with timestamp.
- The user can mark the receipt as `CONFIRMED` or `DISPUTED`.
- On confirmation, the receipt status transitions to `REVIEWED` and the admin audit trail is updated.
- The page is fully responsive and optimized for mobile (80%+ of users will arrive via WhatsApp link on phone).
- Auto-save drafts every 10 seconds; explicit "Confirm" button for final submission.

---

### US-06: Admin Audit Dashboard -- AI vs. User Diffs

> **As a** tax administrator (accountant),
> **I want** to view a dashboard showing differences between AI-extracted data and user-corrected data,
> **so that** I can audit accuracy, identify systematic OCR errors, and ensure compliance.

**Acceptance Criteria:**

- The dashboard lists all receipts for the tenant, filterable by: status, date range, confidence score range, user, vendor.
- Each receipt row shows: thumbnail, vendor, total, date, overall confidence, status, diff count.
- Clicking a receipt opens a diff view: AI-extracted value vs. user-edited value, field by field, with color-coded changes (red = AI wrong, green = AI correct, amber = user override).
- The admin can override user edits with a mandatory reason field.
- Bulk export of audit data as CSV.
- Aggregate statistics: OCR accuracy rate by field, by vendor, by time period.
- Role-based access: only users with `role = ADMIN` can access the dashboard.

---

### US-07: Export/Integration-Ready Payloads

> **As a** tax administrator,
> **I want** to export finalized receipt data in standard formats,
> **so that** I can import it into accounting software (e.g., Exact Online, Twinfield, Xero).

**Acceptance Criteria:**

- Export formats supported at MVP: JSON, CSV, UBL 2.1 (XML).
- Exports include all extracted and user-confirmed fields plus metadata (original file hash, processing timestamps, confidence scores).
- Batch export: select multiple receipts and download as a single ZIP archive.
- API endpoint: `GET /api/v1/tenants/{tenant_id}/receipts/export?format=json&from=2026-01-01&to=2026-01-31` with API key authentication.
- Webhook integration: optional outbound webhook on receipt `FINALIZED` status with JSON payload.
- All exports are logged in the audit trail.

---

### US-08: Multi-Tenant User Management

> **As a** platform operator,
> **I want** to onboard and manage multiple tenants (accounting firms, businesses),
> **so that** each tenant's data is fully isolated and independently configurable.

**Acceptance Criteria:**

- Each tenant has: `tenant_id` (UUID), display name, subscription tier, active user count, storage quota.
- Users belong to exactly one tenant and have roles: `OWNER`, `ADMIN`, `USER`.
- Tenant onboarding creates: database row, storage directory, default configuration.
- Phone numbers are registered per-tenant; one phone can belong to multiple tenants via a disambiguation flow.
- Tenant-level settings: OCR language preferences, confidence thresholds, export format defaults, WhatsApp reply templates.
- Deactivated tenants retain data for 90 days (configurable) before deletion.

---

### US-09: WhatsApp Phone Number Registration & Linking

> **As a** new user,
> **I want** to link my WhatsApp number to my tenant account via a one-time verification,
> **so that** subsequent invoice photos are automatically routed to the correct account.

**Acceptance Criteria:**

- First-time senders receive: *"Welcome to ScanbonAI! Send us your registration code to link your account."*
- Registration codes are generated by admins in the dashboard and valid for 48 hours.
- After linking, all future messages from that number are auto-routed to the tenant.
- Users can unlink via WhatsApp command: *"STOP"* or *"UNLINK"*.
- Admins can view and manage linked phone numbers in the dashboard.

---

### US-10: Receipt Status Lifecycle & Notifications

> **As a** small-business owner,
> **I want** to check the status of my submitted invoices via WhatsApp,
> **so that** I know which ones still need my review.

**Acceptance Criteria:**

- Receipt statuses: `RECEIVED` -> `PROCESSING` -> `EXTRACTED` -> `REVIEW_PENDING` -> `REVIEWED` -> `FINALIZED` (or `REJECTED_QUALITY` / `REJECTED_DUPLICATE`).
- User can send *"STATUS"* via WhatsApp to get a summary: *"You have 3 invoices pending review. [Review now](link)"*.
- Status changes trigger WhatsApp notifications (configurable per tenant: all changes, or only actionable ones).
- Receipts not reviewed within 7 days trigger a reminder notification.

---

### US-11: Secure File Access & Expiring URLs

> **As a** platform operator,
> **I want** all file access to go through signed, expiring URLs,
> **so that** invoice images are never publicly accessible and access is auditable.

**Acceptance Criteria:**

- All image/PDF URLs served to users or admins are signed with HMAC-SHA256 and expire after a configurable TTL (default: 72h for users, 24h for admin dashboard).
- Expired URLs return HTTP 403 with a user-friendly message and option to request a new link.
- URL generation and access events are logged (who, when, which file, IP address).
- Files are never served directly; always proxied through the API layer.

---

### US-12: System Health & Operational Monitoring

> **As a** platform operator,
> **I want** health checks and basic metrics exposed,
> **so that** I can monitor system uptime and processing throughput.

**Acceptance Criteria:**

- `/health` endpoint returns service status, database connectivity, Redis connectivity, storage accessibility.
- `/metrics` endpoint exposes: receipts processed (total, last hour), average OCR processing time, queue depth, error rate.
- Failed jobs are retried up to 3 times with exponential backoff.
- Dead-letter queue for permanently failed jobs with admin alerting.

---

## 2. UX Flows

### Flow 1: Happy Path -- WhatsApp Intake to Export

```
Step 1: USER sends invoice photo via WhatsApp
        |
Step 2: SYSTEM (WhatsApp Business API) receives message
        |  - Validates media type (image/pdf)
        |  - Resolves sender phone -> tenant_id + user_id
        |  - Stores original file: /data/{tenant_id}/{user_id}/YYYY-MM/{uuid}.ext
        |  - Enqueues OCR job to Redis
        |
Step 3: SYSTEM sends WhatsApp reply:
        "Receipt received (#REC-20260222-A3F2). Processing your invoice..."
        |
Step 4: WORKER picks up job from Redis queue
        |  - Runs quality gate (readability_score)
        |  - If score >= 0.40: proceed to OCR extraction
        |  - Extracts fields with confidence scores
        |  - Persists to PostgreSQL
        |  - Generates signed review URL
        |
Step 5: SYSTEM sends WhatsApp confirmation:
        "Your invoice from [Vendor] for EUR [Total] on [Date] has been processed.
         Review & confirm: [signed-url]
         (Link valid for 72 hours)"
        |
Step 6: USER taps link -> opens mobile review page
        |  - Sees invoice image (left/top) + extracted fields (right/bottom)
        |  - Low-confidence fields highlighted in amber
        |  - Edits any incorrect fields
        |  - Taps "Confirm"
        |
Step 7: SYSTEM transitions receipt to REVIEWED status
        |  - Records user_override diffs
        |  - Notifies admin dashboard (real-time via WebSocket or polling)
        |
Step 8: ADMIN opens audit dashboard
        |  - Sees new REVIEWED receipt in list
        |  - Opens diff view: AI values vs. user corrections
        |  - Optionally overrides with reason
        |  - Marks as FINALIZED
        |
Step 9: SYSTEM marks receipt FINALIZED
        |  - Fires outbound webhook (if configured)
        |  - Receipt available for export
        |
Step 10: ADMIN exports batch of finalized receipts
         - Selects date range, format (JSON/CSV/UBL)
         - Downloads ZIP archive or receives via API
```

### Flow 2: Unreadable Invoice Rejection

```
Step 1: USER sends blurry/unreadable invoice photo via WhatsApp
        |
Step 2: SYSTEM receives and stores file (same as happy path)
        |  - Enqueues OCR job
        |
Step 3: SYSTEM sends initial acknowledgment:
        "Receipt received. Processing your invoice..."
        |
Step 4: WORKER picks up job
        |  - Runs quality gate
        |  - readability_score = 0.22 (below 0.40 threshold)
        |  - Classifies rejection reason: BLUR
        |  - Sets status = REJECTED_QUALITY
        |
Step 5: SYSTEM sends WhatsApp rejection:
        "We couldn't read your invoice (reason: image is blurry).
         Tips: Hold your phone steady, ensure good lighting,
         lay the invoice flat on a contrasting surface.
         Please retake the photo and send again."
        [Attached: thumbnail of rejected image]
        |
Step 6a: USER retakes photo and resends -> back to Flow 1, Step 1
        |
Step 6b: USER does nothing -> receipt stays REJECTED_QUALITY
         |  - After 7 days: reminder notification
         |  - Admin can view rejected receipts in dashboard
```

### Flow 3: Admin Audit and Override

```
Step 1: ADMIN logs into ScanbonAI web dashboard
        |  - Authenticates via email/password + optional 2FA
        |  - Sees tenant-scoped overview
        |
Step 2: ADMIN navigates to Audit Queue
        |  - Filters: status=REVIEWED (user-confirmed, awaiting admin sign-off)
        |  - Sorts by: most recent, lowest confidence, most user edits
        |
Step 3: ADMIN clicks receipt row
        |  - Diff view loads:
        |    Field           | AI Extracted     | User Corrected | Delta
        |    vendor_name     | "Alber Heijn"    | "Albert Heijn" | CORRECTED
        |    total_amount    | 47.50            | 47.50          | MATCH
        |    invoice_date    | 2026-02-20       | 2026-02-20     | MATCH
        |    vat_amount      | 8.25             | 9.25           | CORRECTED
        |
Step 4: ADMIN reviews diffs
        |  Option A: Approves user corrections -> clicks "Finalize"
        |  Option B: Overrides a field -> enters correct value + mandatory reason
        |            (e.g., "VAT recalculated from line items; user total was wrong")
        |  Option C: Sends back to user -> clicks "Request Re-review"
        |            -> User gets WhatsApp: "Your accountant has a question about
        |               invoice #REC-... Please review again: [link]"
        |
Step 5: Receipt transitions to FINALIZED (Option A/B)
        |  - All override actions logged: admin_id, timestamp, old_value,
        |    new_value, reason
        |  - Receipt locked from further user edits
        |
Step 6: ADMIN runs aggregate reports
        - OCR accuracy by vendor (e.g., "Albert Heijn invoices: 94% field accuracy")
        - Most-corrected fields (e.g., "vat_amount corrected in 18% of receipts")
        - Trends over time (improving as ML retrains? -- FUTURE)
```

---

## 3. Competitor Benchmark Alignment

| Capability                         | Lyanthe           | Basecone (WK)     | Rydoo              | ScanbonAI MVP       | ScanbonAI FUTURE     |
|------------------------------------|-------------------|--------------------|---------------------|---------------------|----------------------|
| **Intake: Email**                  | Yes               | Yes                | Yes                 | No (FUTURE)         | Planned              |
| **Intake: WhatsApp**               | No                | No                 | No                  | **Yes (core)**      | Enhanced             |
| **Intake: Mobile App**             | Yes               | Yes                | Yes                 | Via WhatsApp link   | Native app           |
| **Intake: Bulk Upload**            | Yes               | Yes                | Yes                 | No (FUTURE)         | Planned              |
| **OCR Extraction**                 | AI-powered        | AI-powered         | AI-powered          | **AI + confidence** | ML retraining        |
| **Confidence Scoring**             | Internal only     | Internal only      | Not exposed         | **Per-field exposed**| Expert adjudication  |
| **User Review/Edit**               | Web portal        | Web portal         | Mobile app          | **Mobile-first web**| Native app           |
| **Admin Audit Dashboard**          | Basic             | Comprehensive      | Basic               | **Diff-focused**    | Full analytics       |
| **AI vs. User Diff View**          | No                | Limited             | No                  | **Yes (core)**      | ML feedback loop     |
| **Multi-Tenant**                   | Yes               | Yes (firm-level)   | Yes (company-level) | **Yes (row-level)** | Enhanced             |
| **Export: CSV/JSON**               | Yes               | Yes                | Yes                 | **Yes**             | Enhanced             |
| **Export: UBL/XML**                | Limited           | Yes                | No                  | **Yes (UBL 2.1)**  | More formats         |
| **Accounting Integration**         | Exact, Twinfield  | 20+ integrations   | ERP integrations    | Webhook + API       | Direct integrations  |
| **Expense Policy**                 | No                | No                 | Yes                 | No                  | Possible             |
| **Credit Card Reconciliation**     | No                | No                 | Yes                 | No                  | Out of scope         |
| **Expert Review Pool**             | No                | No                 | No                  | No                  | **Unique: Planned**  |
| **Reward/Payout System**           | No                | No                 | No                  | No                  | **Unique: Planned**  |
| **WhatsApp-Native Status Queries** | No                | No                 | No                  | **Yes (core)**      | NLP commands         |

### Key MVP Differentiators vs. Competitors

1. **WhatsApp-First Intake** -- None of the three competitors offer WhatsApp as an intake channel. ScanbonAI makes WhatsApp the primary interface, reducing onboarding friction to near zero.
2. **Exposed Confidence Scoring** -- Competitors keep OCR confidence internal. ScanbonAI exposes per-field confidence to both users and admins, creating transparency and trust.
3. **AI vs. User Diff Audit** -- No competitor offers a dedicated diff view. ScanbonAI makes this the centerpiece of the admin experience, enabling data-driven OCR improvement.
4. **FUTURE: Expert Pool** -- No competitor has a crowdsourced expert review system. This is a unique moat if executed correctly.

---

## 4. FUTURE: Expert Pool Ecosystem

### 4.1 Expert Roles

| Role                 | Description                                                 | Access Level           |
|----------------------|-------------------------------------------------------------|------------------------|
| **Junior Reviewer**  | Reviews low-confidence fields; limited to single-field edits| Read receipt image + flagged fields only |
| **Senior Reviewer**  | Reviews full receipts; can override AI and junior edits     | Read receipt image + all fields |
| **Specialist**       | Domain expert (e.g., medical invoices, construction)        | Same as Senior + domain queue |
| **Adjudicator**      | Resolves disputes between reviewers                         | Full read + conflict resolution |

### 4.2 Queue & Assignment Logic

```
1. Receipt enters Expert Queue when:
   - Overall confidence < 0.70 AND user has not reviewed within 48h
   - Admin explicitly routes to expert review
   - User disputes AI extraction and requests expert help

2. Assignment Algorithm:
   a. Filter experts by: language, domain tags, availability, current load
   b. Score candidates by: accuracy_rating * availability_weight * domain_match
   c. Assign to top-scoring expert with < 20 active tasks
   d. If no expert available within 15 min: escalate to next tier
   e. SLA: Expert must begin review within 2 hours, complete within 8 hours

3. Dual-Review Protocol (for high-value invoices > EUR 5,000):
   - Two independent experts review
   - If both agree: auto-finalize
   - If they disagree: route to Adjudicator
```

### 4.3 Reward System

| Metric                  | Scoring Rule                                          | Payout Trigger                    |
|-------------------------|-------------------------------------------------------|-----------------------------------|
| **Accuracy Score**      | Starts at 0.80; +0.01 per confirmed correct review; -0.05 per overturned review | Score > 0.90 unlocks higher-tier tasks |
| **Speed Bonus**         | Completed within 1 hour: +10% payout                 | Per-task                          |
| **Volume Tier**         | 0-50 reviews/month: base rate; 51-200: +15%; 201+: +25% | Monthly settlement               |
| **Streak Bonus**        | 10 consecutive correct reviews: +EUR 5 bonus         | Per streak                        |
| **Payout Frequency**    | Weekly settlement, minimum EUR 25 threshold           | Automated bank transfer           |
| **Fraud Prevention**    | Random re-review of 5% of expert-reviewed receipts by Adjudicators; sudden accuracy drops trigger review freeze; IP/device fingerprinting for multi-account detection | Continuous monitoring |

### 4.4 Governance: Conflict Resolution & Adjudication

```
Dispute Flow:
1. Expert A submits review
2. System detects conflict:
   - Expert A disagrees with AI on >3 fields, OR
   - User disputes Expert A's review, OR
   - Dual-review experts disagree
3. Adjudicator receives conflict package:
   - Original image
   - AI extraction
   - Expert A review (anonymized)
   - Expert B review (if dual-review, anonymized)
   - User's original edits (if any)
4. Adjudicator makes final determination with mandatory reasoning
5. Outcomes:
   - Expert accuracy scores updated
   - If expert was consistently wrong: temporary suspension + retraining requirement
   - If AI was wrong: data fed to ML retraining pipeline (FUTURE)
6. Appeals: Experts can appeal Adjudicator decisions once per quarter
   - Appeals reviewed by platform operator
```

### 4.5 Compliance: Data Minimization & Audit Logs

- **Data Minimization:** Experts see only the receipt image and the specific fields assigned to them. They never see: user name, phone number, tenant name, or historical data. Images are watermarked with `REVIEW COPY - {expert_task_id}` to prevent screenshot leakage.
- **Session Controls:** Expert review sessions auto-terminate after 30 minutes of inactivity. Images are not downloadable; rendered in a secure viewer with right-click disabled (defense in depth, not relied upon).
- **Audit Logging:** Every expert action is logged: `{expert_id, task_id, action, timestamp, ip_address, field_changes}`. Logs retained for 7 years (Dutch tax retention requirement).
- **GDPR:** Experts sign a Data Processing Agreement (DPA). Expert data subject to its own retention policy. Tenant data remains under tenant's DPA.

---
---

# DELIVERABLE 2: Solution Architect Report

---

## 1. Architecture Diagram

```
                              EXTERNAL BOUNDARY
 ============================================================================

   [WhatsApp Users]                              [Admin/User Browsers]
        |                                               |
        | (HTTPS/WSS)                                   | (HTTPS)
        v                                               v
 +------------------+                          +------------------+
 | WhatsApp Business|                          |   React SPA      |
 | API (Meta Cloud) |                          |   (Frontend)     |
 +--------+---------+                          +--------+---------+
          |                                             |
          | Webhook (HTTPS POST)                        | HTTPS REST/WS
          |                                             |
 =========|=============================================|=================
          |          REVERSE PROXY (Traefik)            |
          |        +---------------------------+        |
          +------->|  :443 TLS termination     |<-------+
                   |  /api/*   -> api:8000     |
                   |  /ws/*    -> api:8000     |
                   |  /hook/*  -> api:8000     |
                   |  /*       -> frontend:80  |
                   +------------+--------------+
                                |
          INTERNAL NETWORK (Docker bridge: scanbonai_internal)
          ======================================================
                                |
                   +------------+--------------+
                   |     FastAPI Backend       |
                   |       (api:8000)          |
                   |                           |
                   |  - /hook/whatsapp  (intake)|
                   |  - /api/v1/*   (REST API) |
                   |  - /ws/*       (WebSocket)|
                   |  - /health, /metrics      |
                   +--+-------+-------+--------+
                      |       |       |
            +---------+   +---+---+   +----------+
            |             |       |              |
            v             v       v              v
     +------+------+ +---+---+ +-+--------+ +---+----------+
     |  PostgreSQL  | | Redis | | File     | | Signed URL   |
     |  (db:5432)   | | :6379 | | Storage  | | Generator    |
     |              | |       | | (Volume) | | (internal)   |
     | - receipts   | | - job | | /data/   | +---+----------+
     | - users      | |  queue| |  {t_id}/ |     |
     | - tenants    | | - cache|  {u_id}/ |     | (presigned
     | - audit_logs | |       | |  YYYY-MM/|     |  paths)
     +--------------+ +---+---+ +----+-----+     |
                          |          |            |
                      +---+----------+---+        |
                      |   OCR Worker     |        |
                      |  (worker:8001)   |--------+
                      |                  |
                      | - Quality gate   |
                      | - OCR pipeline   |
                      | - Confidence     |
                      |   scoring        |
                      +------------------+

          FUTURE MODULE BOUNDARIES (not built in MVP)
          =============================================

     +-------------------+  +-------------------+  +-------------------+
     | Expert Pool       |  | Payments/Payout   |  | ML Training       |
     | Service           |  | Service            |  | Pipeline          |
     |                   |  |                   |  |                   |
     | - Expert mgmt     |  | - Stripe/Mollie   |  | - Correction      |
     | - Task queue      |  | - Payout calc     |  |   aggregation     |
     | - Assignment algo |  | - Fraud detection |  | - Model retraining|
     | - Secure viewer   |  | - Ledger          |  | - A/B deployment  |
     +-------------------+  +-------------------+  +-------------------+
              |                       |                       |
              +-----------+-----------+-----------+-----------+
                          |
                 +--------+--------+
                 | Expert          |
                 | Adjudication    |
                 | Engine          |
                 |                 |
                 | - Conflict      |
                 |   detection     |
                 | - Dual-review   |
                 |   comparison    |
                 | - Adjudicator   |
                 |   workflow      |
                 +-----------------+
```

---

## 2. End-to-End Request Flow

```
Step  Actor/Component          Action
----  ----------------------   --------------------------------------------------
 1    WhatsApp User            Sends photo of invoice to ScanbonAI WhatsApp number

 2    Meta Cloud API           Receives message, triggers webhook POST to
                               https://scanbonai.example.com/hook/whatsapp

 3    Traefik Reverse Proxy    Terminates TLS, routes /hook/* to api:8000

 4    FastAPI /hook/whatsapp   Validates webhook signature (X-Hub-Signature-256)
                               Parses message: sender phone, media URL, media type

 5    FastAPI (auth resolver)  Looks up sender phone in `user_phone_links` table
                               Resolves tenant_id + user_id
                               If unknown phone: triggers registration flow (US-09)

 6    FastAPI (file handler)   Downloads media from Meta's CDN (using temp token)
                               Computes SHA-256 hash for dedup check
                               Stores file: /data/{tenant_id}/{user_id}/YYYY-MM/{uuid}.ext

 7    FastAPI (job enqueue)    Creates `receipt` row in PostgreSQL:
                               {id, tenant_id, user_id, file_path, file_hash,
                                status=RECEIVED, created_at}
                               Enqueues job to Redis: {receipt_id, file_path, tenant_id}

 8    FastAPI (WhatsApp reply) Sends acknowledgment via WhatsApp Business API:
                               "Receipt received (#REC-...). Processing your invoice..."

 9    OCR Worker               Picks job from Redis queue (BRPOP with visibility timeout)
                               Updates receipt status -> PROCESSING

10    OCR Worker (quality)     Runs image quality assessment:
                               - Blur detection (Laplacian variance)
                               - Contrast analysis
                               - Orientation/skew correction
                               Computes readability_score

11a   OCR Worker (reject)      IF readability_score < 0.40:
                               - Updates status -> REJECTED_QUALITY
                               - Sends WhatsApp rejection with tips (US-03)
                               - DONE (exits pipeline)

11b   OCR Worker (extract)     IF readability_score >= 0.40:
                               - Runs OCR engine (Tesseract + ML post-processing)
                               - Extracts structured fields with confidence scores
                               - Normalizes: dates, currencies, amounts
                               - Validates: line items sum to subtotal, VAT calculation

12    OCR Worker (persist)     Writes extraction results to PostgreSQL:
                               - receipt_fields table (field_name, raw_value,
                                 normalized_value, confidence, needs_review)
                               - Updates receipt: status -> EXTRACTED,
                                 overall_confidence, extracted_at

13    OCR Worker (notify)      Generates signed review URL (HMAC-SHA256, 72h TTL)
                               Sends WhatsApp confirmation with summary + link (US-02)
                               Updates status -> REVIEW_PENDING

14    WhatsApp User            Taps signed URL on phone

15    Traefik                  Routes to frontend (React SPA)

16    React Frontend           Loads review page, calls API with signed token:
                               GET /api/v1/receipts/{id}?token={signed_token}

17    FastAPI (token validator) Validates HMAC signature + expiry
                               Returns receipt data + signed image URL

18    React Frontend           Renders side-by-side: image + editable fields
                               Highlights needs_review fields

19    WhatsApp User            Edits fields, clicks "Confirm"

20    React Frontend           POST /api/v1/receipts/{id}/confirm
                               Body: {field_overrides: [{field, old_value, new_value}]}

21    FastAPI (confirm handler) Validates input, persists overrides to
                               `receipt_field_overrides` table
                               Updates receipt status -> REVIEWED
                               Emits event to admin notification channel

22    Admin (browser)          Opens audit dashboard, sees receipt in REVIEWED queue

23    React Frontend (admin)   GET /api/v1/tenants/{tid}/receipts?status=REVIEWED
                               Renders receipt list with diff counts

24    Admin                    Clicks receipt, reviews AI vs. user diffs

25    Admin                    Clicks "Finalize" (or overrides + finalizes)

26    FastAPI (finalize)       Updates status -> FINALIZED
                               Logs admin action to audit_log table
                               Fires outbound webhook (if configured)

27    Admin (later)            GET /api/v1/tenants/{tid}/receipts/export?format=ubl
                               Downloads finalized receipts as UBL 2.1 XML in ZIP
```

---

## 3. Service Decomposition

### 3.1 `api` -- FastAPI Backend

| Attribute              | Detail |
|------------------------|--------|
| **Responsibility**     | HTTP API (REST + WebSocket), WhatsApp webhook handler, authentication/authorization, signed URL generation, business logic orchestration |
| **Framework**          | Python 3.12 + FastAPI + Uvicorn (ASGI) |
| **Scaling**            | Horizontal: stateless; run N replicas behind Traefik. Session state in Redis. File storage on shared volume or S3. |
| **Container Config**   | `python:3.12-slim` base. Exposed port 8000. Health check: `GET /health`. Resource limits: 512MB RAM, 0.5 CPU (MVP). Env vars: `DATABASE_URL`, `REDIS_URL`, `WHATSAPP_API_TOKEN`, `HMAC_SECRET`, `STORAGE_PATH`. |
| **Key Dependencies**   | `fastapi`, `uvicorn`, `sqlalchemy`, `asyncpg`, `redis[hiredis]`, `pydantic`, `python-jose` (JWT/HMAC), `httpx` (WhatsApp API calls) |

### 3.2 `worker` -- OCR Processing Worker

| Attribute              | Detail |
|------------------------|--------|
| **Responsibility**     | Consumes jobs from Redis queue. Runs image quality assessment, OCR extraction, confidence scoring, field normalization. Writes results to PostgreSQL. Triggers WhatsApp notifications via API service (or directly). |
| **Framework**          | Python 3.12 + custom worker loop (or ARQ/RQ) |
| **Scaling**            | Horizontal: run N workers, each consuming from the same Redis queue. CPU-bound (OCR), so scale based on CPU. Consider GPU workers for ML-heavy post-processing (FUTURE). |
| **Container Config**   | `python:3.12-slim` + system deps (`tesseract-ocr`, `libgl1`, `poppler-utils`). No exposed port (or optional 8001 for health only). Resource limits: 1GB RAM, 1.0 CPU (MVP). Mounts: `/data` volume (read/write). |
| **Key Dependencies**   | `tesseract` (system), `pytesseract`, `Pillow`, `opencv-python-headless`, `pdf2image`, `sqlalchemy`, `asyncpg`, `redis[hiredis]` |

### 3.3 `db` -- PostgreSQL Database

| Attribute              | Detail |
|------------------------|--------|
| **Responsibility**     | Persistent storage for all structured data: tenants, users, receipts, fields, overrides, audit logs. |
| **Version**            | PostgreSQL 16 |
| **Scaling**            | Vertical for MVP. Read replicas for dashboard queries (FUTURE). Connection pooling via PgBouncer if >50 concurrent connections. |
| **Container Config**   | `postgres:16-alpine`. Exposed port 5432 (internal network only). Volume: `pgdata:/var/lib/postgresql/data`. Resource limits: 1GB RAM, 0.5 CPU (MVP). `POSTGRES_DB=scanbonai`, `POSTGRES_USER`, `POSTGRES_PASSWORD` from secrets. |
| **Backup**             | pg_dump cron daily to `/backups` volume. FUTURE: WAL streaming to S3. |

### 3.4 `redis` -- Redis Queue & Cache

| Attribute              | Detail |
|------------------------|--------|
| **Responsibility**     | Job queue for OCR pipeline. Caching layer for session data, rate limiting counters, signed URL nonces. |
| **Version**            | Redis 7 |
| **Scaling**            | Single instance for MVP. Redis Sentinel or Cluster for HA (FUTURE). |
| **Container Config**   | `redis:7-alpine`. Exposed port 6379 (internal only). Volume: `redisdata:/data` (AOF persistence). `maxmemory 256mb`, `maxmemory-policy allkeys-lru`. |

### 3.5 `frontend` -- React SPA

| Attribute              | Detail |
|------------------------|--------|
| **Responsibility**     | User-facing review/edit page (mobile-first). Admin audit dashboard. Tenant management UI. |
| **Framework**          | React 18 + TypeScript + Vite. TailwindCSS for styling. React Query for data fetching. |
| **Scaling**            | Static files served by Nginx inside container. Infinitely scalable (CDN-ready). |
| **Container Config**   | Multi-stage build: `node:20-alpine` (build) -> `nginx:alpine` (serve). Exposed port 80 (internal). Nginx config serves SPA with `try_files $uri /index.html`. Resource limits: 128MB RAM, 0.25 CPU. |

### 3.6 `proxy` -- Traefik Reverse Proxy

| Attribute              | Detail |
|------------------------|--------|
| **Responsibility**     | TLS termination, routing, rate limiting, request logging, Let's Encrypt certificate management. |
| **Version**            | Traefik 3.x |
| **Scaling**            | Single instance for MVP. Pair with keepalived for HA (FUTURE). |
| **Container Config**   | `traefik:3`. Exposed ports: 80 (redirect to 443), 443. Volume: `letsencrypt:/acme`. Labels-based routing from docker-compose. Rate limiting: 100 req/min per IP for API, 10 req/min for webhook. |

---

## 4. Multi-Tenant Strategy

### 4.1 Tenant ID in All Tables

Every table that contains tenant-scoped data includes a `tenant_id UUID NOT NULL` column with a foreign key to `tenants(id)`.

```sql
-- Core tables with tenant_id
tenants             (id, name, subscription_tier, settings, created_at)
users               (id, tenant_id, email, phone, role, created_at)
user_phone_links    (id, tenant_id, user_id, phone_number, linked_at)
receipts            (id, tenant_id, user_id, file_path, file_hash, status,
                     readability_score, overall_confidence, created_at, ...)
receipt_fields      (id, receipt_id, tenant_id, field_name, raw_value,
                     normalized_value, confidence, needs_review)
receipt_overrides   (id, receipt_field_id, tenant_id, old_value, new_value,
                     override_by, override_role, reason, created_at)
audit_logs          (id, tenant_id, actor_id, action, entity_type, entity_id,
                     details_json, ip_address, created_at)
```

All application queries include `WHERE tenant_id = :tenant_id`. The API layer extracts `tenant_id` from the authenticated session and injects it into every database call. There is no code path that queries across tenants except for platform-operator endpoints.

### 4.2 Row-Level Security (RLS)

PostgreSQL RLS is used as defense-in-depth (belt and suspenders with application-level filtering):

```sql
-- Enable RLS on all tenant-scoped tables
ALTER TABLE receipts ENABLE ROW LEVEL SECURITY;

-- Policy: application role can only see rows for the current tenant
CREATE POLICY tenant_isolation ON receipts
    USING (tenant_id = current_setting('app.current_tenant_id')::uuid);

-- Set tenant context at the beginning of each request
SET LOCAL app.current_tenant_id = '{tenant_uuid}';
```

This ensures that even if application code has a bug that omits the tenant filter, the database will enforce isolation. The `platform_admin` role bypasses RLS for cross-tenant operations.

### 4.3 File Storage Isolation

```
/data/
  {tenant_id}/                    # UUID directory per tenant
    {user_id}/                    # UUID directory per user
      2026-02/                    # Year-month partitioning
        {receipt_uuid}.jpg        # Original file
        {receipt_uuid}_thumb.jpg  # Thumbnail (generated)
        {receipt_uuid}_ocr.json   # Raw OCR output (debug)
```

**Access rules:**
- Files are never served directly via static file serving.
- All access goes through the API, which validates the signed URL and tenant context.
- S3 migration path: replace volume mount with S3 client; path structure becomes S3 key prefix. No schema or API changes needed.

---

## 5. Security Boundaries

### 5.1 Network Segmentation

```
EXTERNAL (Internet-facing)
===========================
  [proxy]  -- ports 80, 443 exposed to host/internet

INTERNAL (Docker bridge network: scanbonai_internal)
====================================================
  [api]       -- port 8000, reachable from proxy only
  [worker]    -- no exposed port (connects outbound to redis, db, storage)
  [frontend]  -- port 80, reachable from proxy only
  [db]        -- port 5432, reachable from api and worker only
  [redis]     -- port 6379, reachable from api and worker only

NO service on the internal network is reachable from the internet
except through the proxy.
```

Docker Compose network configuration:

```yaml
networks:
  external:       # proxy connects here + to internal
    driver: bridge
  internal:       # all services connect here
    driver: bridge
    internal: true # no external access
```

Traefik is the only container attached to both networks.

### 5.2 Authentication Boundaries

| Actor              | Auth Method                        | Token Type              | Scope                    |
|--------------------|------------------------------------|-------------------------|--------------------------|
| **WhatsApp User**  | Phone number (verified by Meta)    | Signed URL (HMAC)       | Single receipt, 72h TTL  |
| **Admin**          | Email + password + optional TOTP   | JWT (HttpOnly cookie)   | Tenant-scoped, 8h TTL   |
| **Platform Operator** | Email + password + mandatory TOTP | JWT (HttpOnly cookie)  | Cross-tenant, 4h TTL    |
| **API Integration**| API key (header: `X-API-Key`)      | Opaque key              | Tenant-scoped, no expiry (revocable) |
| **FUTURE: Expert** | Email + password + mandatory TOTP  | JWT (HttpOnly cookie)   | Task-scoped, 2h TTL     |

### 5.3 Signed URL Generation & Validation

```python
# Generation (in api service)
import hmac, hashlib, time, base64

def generate_signed_url(receipt_id: str, tenant_id: str, ttl_hours: int = 72) -> str:
    expires = int(time.time()) + (ttl_hours * 3600)
    payload = f"{receipt_id}:{tenant_id}:{expires}"
    signature = hmac.new(
        HMAC_SECRET.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()
    token = base64.urlsafe_b64encode(f"{payload}:{signature}".encode()).decode()
    return f"https://scanbonai.example.com/review/{receipt_id}?token={token}"

# Validation (in api service, on every signed URL request)
def validate_signed_url(receipt_id: str, token: str) -> bool:
    decoded = base64.urlsafe_b64decode(token).decode()
    payload, received_sig = decoded.rsplit(":", 1)
    r_id, t_id, expires = payload.split(":")

    # Check expiry
    if int(expires) < time.time():
        raise HTTPException(403, "Link expired. Request a new one via WhatsApp.")

    # Check receipt match
    if r_id != receipt_id:
        raise HTTPException(403, "Invalid link.")

    # Verify HMAC
    expected_sig = hmac.new(
        HMAC_SECRET.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(received_sig, expected_sig):
        raise HTTPException(403, "Invalid link.")

    return t_id  # Return tenant_id for RLS context
```

### 5.4 Additional Security Measures

- **Webhook Verification:** WhatsApp webhook requests are validated using `X-Hub-Signature-256` header with the app secret.
- **Rate Limiting:** Traefik middleware enforces per-IP rate limits. Application-level rate limiting via Redis for per-user/per-tenant limits.
- **Input Validation:** Pydantic models validate all API inputs. File uploads validated by content type (magic bytes, not just extension).
- **SQL Injection:** SQLAlchemy ORM with parameterized queries. No raw SQL.
- **XSS/CSRF:** React frontend with CSP headers. CSRF tokens for state-changing operations from the admin dashboard.
- **Secrets Management:** All secrets via environment variables. Docker secrets for production. No secrets in code or config files.
- **Dependency Scanning:** `pip-audit` and `npm audit` in CI pipeline.

---

## 6. FUTURE Module Boundaries

### 6.1 Expert Pool Service

```
Interface Contract:
  INPUT:  POST /expert-pool/tasks
          {receipt_id, tenant_id, fields_to_review[], priority, sla_hours}

  OUTPUT: POST /api/v1/receipts/{id}/expert-review  (callback to main API)
          {receipt_id, expert_id, field_reviews[{field, value, confidence}]}

Internal Responsibilities:
  - Expert registration, onboarding, KYC verification
  - Task queue with priority scoring and assignment algorithm
  - Secure image viewer (watermarked, time-limited sessions)
  - Expert performance tracking (accuracy, speed, volume)
  - SLA monitoring and escalation

Database: Separate schema or database (expert_pool.*)
  - experts (id, email, status, accuracy_score, tier, domains[])
  - expert_tasks (id, receipt_id, expert_id, status, assigned_at, completed_at)
  - expert_reviews (id, task_id, field_name, value, confidence)

Communication: Async via Redis pub/sub or dedicated message queue (RabbitMQ)
Isolation: Experts never access main tenant database directly
```

### 6.2 Payments/Payout Service

```
Interface Contract:
  INPUT:  Consumes expert_review_completed events
          GET /payments/experts/{id}/balance
          POST /payments/experts/{id}/payout

  OUTPUT: Webhook to expert: payout confirmation
          POST /expert-pool/experts/{id}/payment-status (callback)

Internal Responsibilities:
  - Per-review earning calculation (base rate * tier multiplier * bonuses)
  - Running balance ledger (double-entry bookkeeping)
  - Payout scheduling (weekly, minimum threshold)
  - Payment provider integration (Stripe Connect / Mollie)
  - Tax document generation (invoices to experts, W-8/W-9 for non-EU)
  - Fraud detection: unusual patterns, velocity checks

Database: Separate schema (payments.*)
  - ledger_entries (id, expert_id, type, amount, currency, description, created_at)
  - payouts (id, expert_id, amount, status, provider_ref, created_at)

Communication: Event-driven via message queue
Isolation: No access to receipt content; only receipt_id and review metadata
```

### 6.3 Expert Adjudication Engine

```
Interface Contract:
  INPUT:  Triggered by Expert Pool Service when:
          - Dual-review disagreement detected
          - User disputes expert review
          - Automated anomaly detection flags a review

          POST /adjudication/conflicts
          {receipt_id, reviews[{expert_id, field_reviews[]}], conflict_type}

  OUTPUT: POST /expert-pool/tasks/{id}/resolution (callback)
          {receipt_id, final_values[{field, value}], adjudicator_id, reasoning}

Internal Responsibilities:
  - Conflict detection algorithm (field-level diff comparison)
  - Adjudicator assignment (round-robin among available adjudicators)
  - Resolution UI (shows all conflicting reviews side-by-side, anonymized)
  - Expert score impact calculation
  - Appeal workflow

Database: Separate schema (adjudication.*)
  - conflicts (id, receipt_id, conflict_type, status, created_at)
  - conflict_reviews (id, conflict_id, expert_id_anonymized, field_reviews_json)
  - resolutions (id, conflict_id, adjudicator_id, final_values_json, reasoning)

Communication: Sync API calls for resolution submission; async events for score updates
```

### 6.4 ML Training Pipeline

```
Interface Contract:
  INPUT:  Batch job (nightly or weekly)
          Consumes: receipt_fields + receipt_overrides (aggregated, anonymized)
          Only fields where user/admin/expert corrected the AI

  OUTPUT: New model artifacts uploaded to model registry
          POST /api/v1/admin/models/{version}/deploy (deployment trigger)

Internal Responsibilities:
  - Data aggregation: collect correction pairs (AI_value, correct_value)
  - Data anonymization: strip PII, use only structural patterns
  - Feature engineering: vendor patterns, layout features, field positions
  - Model training: fine-tune OCR post-processing model
  - Evaluation: compare new model vs. current on holdout set
  - A/B deployment: route X% of traffic to new model, compare live accuracy
  - Rollback: if new model accuracy < current - 2%, auto-rollback

Infrastructure:
  - Runs on separate compute (GPU instance or serverless batch)
  - Reads from read-replica of main DB (never writes to production)
  - Model artifacts stored in versioned object storage
  - Training logs and metrics to MLflow or similar

Database: Separate schema (ml_pipeline.*)
  - training_runs (id, model_version, dataset_hash, metrics_json, created_at)
  - model_artifacts (id, run_id, artifact_path, status, deployed_at)
  - ab_experiments (id, model_a_version, model_b_version, traffic_split, results_json)

Communication: Batch/cron triggered; deployment via API callback
Isolation: Read-only access to anonymized correction data; no live traffic access
```

---

## Appendix A: Docker Compose Structure (MVP)

```yaml
# docker-compose.yml (simplified)
version: "3.9"

services:
  proxy:
    image: traefik:3
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - letsencrypt:/acme
    networks:
      - external
      - internal

  api:
    build: ./services/api
    environment:
      - DATABASE_URL=postgresql+asyncpg://scanbonai:${DB_PASS}@db:5432/scanbonai
      - REDIS_URL=redis://redis:6379/0
      - WHATSAPP_API_TOKEN=${WHATSAPP_API_TOKEN}
      - HMAC_SECRET=${HMAC_SECRET}
      - STORAGE_PATH=/data
    volumes:
      - filedata:/data
    depends_on:
      - db
      - redis
    networks:
      - internal
    labels:
      - "traefik.http.routers.api.rule=PathPrefix(`/api`) || PathPrefix(`/hook`) || PathPrefix(`/ws`)"

  worker:
    build: ./services/worker
    environment:
      - DATABASE_URL=postgresql+asyncpg://scanbonai:${DB_PASS}@db:5432/scanbonai
      - REDIS_URL=redis://redis:6379/0
      - STORAGE_PATH=/data
    volumes:
      - filedata:/data
    depends_on:
      - db
      - redis
    networks:
      - internal

  frontend:
    build: ./services/frontend
    networks:
      - internal
    labels:
      - "traefik.http.routers.frontend.rule=PathPrefix(`/`)"

  db:
    image: postgres:16-alpine
    environment:
      - POSTGRES_DB=scanbonai
      - POSTGRES_USER=scanbonai
      - POSTGRES_PASSWORD=${DB_PASS}
    volumes:
      - pgdata:/var/lib/postgresql/data
    networks:
      - internal

  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes --maxmemory 256mb --maxmemory-policy allkeys-lru
    volumes:
      - redisdata:/data
    networks:
      - internal

volumes:
  pgdata:
  redisdata:
  filedata:
  letsencrypt:

networks:
  external:
    driver: bridge
  internal:
    driver: bridge
    internal: true
```

---

## Appendix B: Database Schema (MVP Core Tables)

```sql
-- Tenants
CREATE TABLE tenants (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(255) NOT NULL,
    subscription_tier VARCHAR(50) NOT NULL DEFAULT 'free',
    settings        JSONB NOT NULL DEFAULT '{}',
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Users
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    email           VARCHAR(255),
    phone           VARCHAR(20),
    password_hash   VARCHAR(255),
    role            VARCHAR(20) NOT NULL DEFAULT 'USER'
                    CHECK (role IN ('OWNER','ADMIN','USER')),
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_users_tenant ON users(tenant_id);

-- Phone number links (WhatsApp)
CREATE TABLE user_phone_links (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    user_id         UUID NOT NULL REFERENCES users(id),
    phone_number    VARCHAR(20) NOT NULL,
    registration_code VARCHAR(20),
    linked_at       TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(tenant_id, phone_number)
);
CREATE INDEX idx_phone_links_phone ON user_phone_links(phone_number);

-- Receipts
CREATE TABLE receipts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id),
    user_id             UUID NOT NULL REFERENCES users(id),
    file_path           VARCHAR(512) NOT NULL,
    file_hash           VARCHAR(64) NOT NULL,
    media_type          VARCHAR(50) NOT NULL,
    status              VARCHAR(30) NOT NULL DEFAULT 'RECEIVED'
                        CHECK (status IN (
                            'RECEIVED','PROCESSING','EXTRACTED',
                            'REVIEW_PENDING','REVIEWED','FINALIZED',
                            'REJECTED_QUALITY','REJECTED_DUPLICATE'
                        )),
    readability_score   FLOAT,
    rejection_reason    VARCHAR(50),
    overall_confidence  FLOAT,
    extracted_at        TIMESTAMPTZ,
    reviewed_at         TIMESTAMPTZ,
    finalized_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_receipts_tenant_status ON receipts(tenant_id, status);
CREATE INDEX idx_receipts_tenant_user ON receipts(tenant_id, user_id);
CREATE INDEX idx_receipts_hash ON receipts(tenant_id, user_id, file_hash);

-- Receipt fields (OCR extraction results)
CREATE TABLE receipt_fields (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    receipt_id      UUID NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    field_name      VARCHAR(100) NOT NULL,
    raw_value       TEXT,
    normalized_value TEXT,
    confidence      FLOAT NOT NULL DEFAULT 0.0,
    needs_review    BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_fields_receipt ON receipt_fields(receipt_id);
CREATE INDEX idx_fields_review ON receipt_fields(tenant_id, needs_review)
    WHERE needs_review = true;

-- Receipt field overrides (user/admin corrections)
CREATE TABLE receipt_overrides (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    receipt_field_id UUID NOT NULL REFERENCES receipt_fields(id),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    old_value       TEXT,
    new_value       TEXT NOT NULL,
    override_by     UUID NOT NULL REFERENCES users(id),
    override_role   VARCHAR(20) NOT NULL,
    reason          TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_overrides_field ON receipt_overrides(receipt_field_id);

-- Audit logs
CREATE TABLE audit_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    actor_id        UUID REFERENCES users(id),
    action          VARCHAR(100) NOT NULL,
    entity_type     VARCHAR(50) NOT NULL,
    entity_id       UUID,
    details         JSONB,
    ip_address      INET,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_tenant_time ON audit_logs(tenant_id, created_at DESC);

-- Row-Level Security
ALTER TABLE receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE receipt_fields ENABLE ROW LEVEL SECURITY;
ALTER TABLE receipt_overrides ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_phone_links ENABLE ROW LEVEL SECURITY;

-- RLS Policies (applied per table, example for receipts)
CREATE POLICY tenant_isolation_receipts ON receipts
    USING (tenant_id = current_setting('app.current_tenant_id')::uuid);
CREATE POLICY tenant_isolation_fields ON receipt_fields
    USING (tenant_id = current_setting('app.current_tenant_id')::uuid);
CREATE POLICY tenant_isolation_overrides ON receipt_overrides
    USING (tenant_id = current_setting('app.current_tenant_id')::uuid);
CREATE POLICY tenant_isolation_audit ON audit_logs
    USING (tenant_id = current_setting('app.current_tenant_id')::uuid);
CREATE POLICY tenant_isolation_users ON users
    USING (tenant_id = current_setting('app.current_tenant_id')::uuid);
CREATE POLICY tenant_isolation_phone ON user_phone_links
    USING (tenant_id = current_setting('app.current_tenant_id')::uuid);
```

---

## Appendix C: Project Directory Structure (MVP)

```
scanbonAI/
  docker-compose.yml
  docker-compose.override.yml    # Local dev overrides
  .env.example
  README.md

  services/
    api/
      Dockerfile
      pyproject.toml
      app/
        main.py                  # FastAPI app factory
        config.py                # Settings (pydantic-settings)
        dependencies.py          # DI: db session, redis, current_tenant
        models/                  # SQLAlchemy ORM models
          tenant.py
          user.py
          receipt.py
          audit_log.py
        schemas/                 # Pydantic request/response schemas
          receipt.py
          user.py
          export.py
        routers/
          webhook.py             # /hook/whatsapp
          receipts.py            # /api/v1/receipts/*
          tenants.py             # /api/v1/tenants/*
          users.py               # /api/v1/users/*
          export.py              # /api/v1/export/*
          admin.py               # /api/v1/admin/*
          health.py              # /health, /metrics
        services/
          whatsapp.py            # WhatsApp API client
          file_storage.py        # File I/O abstraction (local + S3-ready)
          signed_url.py          # HMAC URL generation/validation
          auth.py                # JWT, API key, signed token validation
        middleware/
          tenant_context.py      # Sets RLS tenant_id per request
          rate_limit.py          # Redis-based rate limiting
      migrations/                # Alembic migrations
        versions/
      tests/

    worker/
      Dockerfile
      pyproject.toml
      app/
        main.py                  # Worker entry point (job loop)
        config.py
        pipeline/
          quality_gate.py        # Image quality assessment
          ocr_engine.py          # Tesseract + post-processing
          field_extractor.py     # Structured field extraction
          normalizer.py          # Date, currency, amount normalization
          confidence.py          # Confidence score calculation
        jobs/
          process_receipt.py     # Main job handler
          send_notification.py   # WhatsApp notification job
      tests/

    frontend/
      Dockerfile
      package.json
      vite.config.ts
      src/
        App.tsx
        pages/
          ReviewPage.tsx         # User receipt review/edit
          AdminDashboard.tsx     # Admin audit dashboard
          DiffView.tsx           # AI vs user diff viewer
          ExportPage.tsx         # Export configuration
          LoginPage.tsx          # Admin login
        components/
          ReceiptViewer.tsx      # Side-by-side image + fields
          FieldEditor.tsx        # Editable field with confidence indicator
          DiffTable.tsx          # Field-level diff table
          StatusBadge.tsx
        hooks/
          useReceipt.ts
          useAuth.ts
          useSignedUrl.ts
        api/
          client.ts              # Axios/fetch wrapper
        types/
          receipt.ts
          user.ts

  data/                          # Mounted volume (gitignored)
    {tenant_id}/
      {user_id}/
        YYYY-MM/

  scripts/
    seed_dev_data.py             # Development seed data
    create_tenant.py             # CLI tenant onboarding
```
