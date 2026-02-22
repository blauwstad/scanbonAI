// ─────────────────────────────────────────────────────────────
// Core Domain Types
// ─────────────────────────────────────────────────────────────

export type InvoiceStatus =
  | "uploaded"
  | "processing"
  | "extracted"
  | "user_review"
  | "user_confirmed"
  | "admin_review"
  | "approved"
  | "rejected"
  | "unreadable"
  | "expert_review";

export type InvoiceCategory =
  | "office_supplies"
  | "travel"
  | "meals"
  | "utilities"
  | "rent"
  | "insurance"
  | "professional_services"
  | "equipment"
  | "telecommunications"
  | "vehicle"
  | "other";

export type UserRole = "user" | "admin" | "expert";

// ─────────────────────────────────────────────────────────────
// Line Items
// ─────────────────────────────────────────────────────────────

export interface LineItem {
  id: string;
  description: string;
  quantity: number;
  unit_price: number;
  total: number;
  vat_rate: number;
  vat_amount: number;
}

export interface LineItemConfidence {
  description: number;
  quantity: number;
  unit_price: number;
  total: number;
  vat_rate: number;
}

// ─────────────────────────────────────────────────────────────
// Invoice Metadata & Confidence
// ─────────────────────────────────────────────────────────────

export interface InvoiceMetadata {
  invoice_number: string;
  invoice_date: string;
  due_date: string | null;
  supplier_name: string;
  supplier_address: string;
  supplier_tax_id: string;
  supplier_iban: string | null;
  recipient_name: string;
  recipient_address: string;
  recipient_tax_id: string | null;
  subtotal: number;
  vat_amount: number;
  total_amount: number;
  currency: string;
  category: InvoiceCategory;
  payment_terms: string | null;
  notes: string | null;
  line_items: LineItem[];
}

export interface FieldConfidenceScores {
  invoice_number: number;
  invoice_date: number;
  due_date: number;
  supplier_name: number;
  supplier_address: number;
  supplier_tax_id: number;
  supplier_iban: number;
  recipient_name: number;
  recipient_address: number;
  recipient_tax_id: number;
  subtotal: number;
  vat_amount: number;
  total_amount: number;
  currency: number;
  category: number;
  payment_terms: number;
  line_items: LineItemConfidence[];
}

export interface ConfidenceScores {
  overall: number;
  fields: FieldConfidenceScores;
}

// ─────────────────────────────────────────────────────────────
// OCR & Extraction
// ─────────────────────────────────────────────────────────────

export interface OCRResult {
  raw_text: string;
  ocr_engine: string;
  ocr_confidence: number;
  language_detected: string;
  processing_time_ms: number;
}

export interface ExtractedData {
  metadata: InvoiceMetadata;
  confidence: ConfidenceScores;
  ocr_result: OCRResult;
  extraction_model: string;
  extraction_time_ms: number;
}

// ─────────────────────────────────────────────────────────────
// Quality Check
// ─────────────────────────────────────────────────────────────

export interface QualityCheckItem {
  check: string;
  passed: boolean;
  details: string | null;
}

export interface QualityCheck {
  is_readable: boolean;
  quality_score: number;
  checks: QualityCheckItem[];
  flagged_issues: string[];
}

// ─────────────────────────────────────────────────────────────
// Invoice (Main Entity)
// ─────────────────────────────────────────────────────────────

export interface Invoice {
  id: string;
  tenant_id: string;
  user_id: string;
  status: InvoiceStatus;
  image_url: string;
  image_signed_url?: string;
  whatsapp_message_id: string | null;
  extracted_data: ExtractedData | null;
  quality_check: QualityCheck | null;
  user_corrections: UserCorrection | null;
  admin_review: AdminReview | null;
  created_at: string;
  updated_at: string;
  processing_started_at: string | null;
  processing_completed_at: string | null;
}

// ─────────────────────────────────────────────────────────────
// Corrections & Reviews
// ─────────────────────────────────────────────────────────────

export interface FieldCorrection {
  field_name: string;
  original_value: unknown;
  corrected_value: unknown;
  ai_confidence: number;
}

export interface UserCorrection {
  id: string;
  invoice_id: string;
  user_id: string;
  corrected_metadata: Partial<InvoiceMetadata>;
  field_corrections: FieldCorrection[];
  submitted_at: string;
}

export type AdminAction = "approve" | "override" | "reject" | "flag_for_review";

export interface AdminReview {
  id: string;
  invoice_id: string;
  admin_id: string;
  action: AdminAction;
  overridden_fields: FieldCorrection[] | null;
  notes: string | null;
  reviewed_at: string;
}

// ─────────────────────────────────────────────────────────────
// Users & Tenants
// ─────────────────────────────────────────────────────────────

export interface User {
  id: string;
  email: string;
  phone: string | null;
  name: string;
  role: UserRole;
  tenant_id: string;
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
}

export interface Tenant {
  id: string;
  name: string;
  tax_id: string;
  plan: "free" | "basic" | "professional" | "enterprise";
  is_active: boolean;
  created_at: string;
  settings: TenantSettings;
}

export interface TenantSettings {
  default_currency: string;
  default_language: string;
  auto_approve_threshold: number;
  require_admin_review: boolean;
  export_format: "csv" | "datev" | "xml";
}

// ─────────────────────────────────────────────────────────────
// API Response Wrappers
// ─────────────────────────────────────────────────────────────

export interface ApiResponse<T> {
  data: T;
  message?: string;
}

export interface PaginatedResponse<T> {
  data: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface ApiError {
  status: number;
  code: string;
  message: string;
  details?: Record<string, string[]>;
}

// ─────────────────────────────────────────────────────────────
// Metrics & Analytics
// ─────────────────────────────────────────────────────────────

export interface MetricsSummary {
  total_invoices: number;
  pending_review: number;
  approved: number;
  rejected: number;
  unreadable: number;
  accuracy_rate: number;
  average_confidence: number;
  average_processing_time_ms: number;
}

export interface AccuracyOverTime {
  date: string;
  accuracy: number;
  sample_size: number;
}

export interface FieldCorrectionStats {
  field_name: string;
  correction_count: number;
  correction_rate: number;
}

export interface ProcessingVolume {
  date: string;
  count: number;
}

export interface AdminMetrics {
  summary: MetricsSummary;
  accuracy_over_time: AccuracyOverTime[];
  field_corrections: FieldCorrectionStats[];
  processing_volume: ProcessingVolume[];
}

// ─────────────────────────────────────────────────────────────
// Filter & Query Params
// ─────────────────────────────────────────────────────────────

export interface InvoiceFilters {
  page?: number;
  page_size?: number;
  status?: InvoiceStatus;
  month?: string; // YYYY-MM format
  search?: string;
  sort_by?: string;
  sort_order?: "asc" | "desc";
}

export interface AdminInvoiceFilters extends InvoiceFilters {
  tenant_id?: string;
  user_id?: string;
  confidence_min?: number;
  confidence_max?: number;
  has_corrections?: boolean;
}

// ─────────────────────────────────────────────────────────────
// Auth
// ─────────────────────────────────────────────────────────────

export interface AuthState {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
}

export interface LoginRequest {
  email: string;
}

export interface VerifyTokenRequest {
  token: string;
}

export interface AuthResponse {
  user: User;
  access_token: string;
  refresh_token: string;
  expires_at: string;
}

// ─────────────────────────────────────────────────────────────
// Export
// ─────────────────────────────────────────────────────────────

export interface ExportRequest {
  format: "csv" | "datev" | "xml";
  month: string; // YYYY-MM
  status_filter?: InvoiceStatus[];
  include_line_items?: boolean;
}

export interface ExportResult {
  id: string;
  download_url: string;
  format: string;
  record_count: number;
  generated_at: string;
}

// ─────────────────────────────────────────────────────────────
// FUTURE: Expert Portal Types
// ─────────────────────────────────────────────────────────────

export interface Expert {
  id: string;
  user_id: string;
  specializations: InvoiceCategory[];
  reputation_score: number;
  total_reviews: number;
  accuracy_rate: number;
  is_available: boolean;
  hourly_rate: number;
  created_at: string;
}

export interface ExpertAssignment {
  id: string;
  invoice_id: string;
  expert_id: string;
  priority: "low" | "medium" | "high" | "urgent";
  status: "assigned" | "in_progress" | "completed" | "disputed" | "expired";
  assigned_at: string;
  deadline: string;
  completed_at: string | null;
}

export interface ExpertReview {
  id: string;
  assignment_id: string;
  expert_id: string;
  invoice_id: string;
  corrected_metadata: Partial<InvoiceMetadata>;
  field_corrections: FieldCorrection[];
  strategy_notes: string;
  confidence_assessment: number;
  time_spent_minutes: number;
  submitted_at: string;
}

export interface Payout {
  id: string;
  expert_id: string;
  amount: number;
  currency: string;
  status: "pending" | "processing" | "completed" | "failed";
  period_start: string;
  period_end: string;
  reviews_count: number;
  created_at: string;
  paid_at: string | null;
}
