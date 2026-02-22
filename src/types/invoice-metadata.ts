// ============================================================
// ScanbonAI - Extracted Invoice Metadata Types (with confidence)
// ============================================================
//
// These types represent the *extraction-layer* data model where every
// field carries a confidence score (0.0-1.0). They complement the
// flatter UI-facing types in src/types/index.ts.
//
// Naming convention: prefixed with "Extracted" to avoid collisions
// with the simpler UI-layer types in index.ts (e.g. LineItem,
// InvoiceMetadata).
// ============================================================

/** Confidence score for any extracted field: 0.0 (no confidence) to 1.0 (certain) */
export type Confidence = number; // 0.0 - 1.0

/** Per-field confidence wrapper -- the core building block */
export interface FieldWithConfidence<T> {
  value: T | null;
  confidence: Confidence;
}

/** Supplier / vendor information (extraction layer, with per-field confidence) */
export interface ExtractedSupplier {
  name: FieldWithConfidence<string>;
  address: FieldWithConfidence<string>;
  country: FieldWithConfidence<string>;      // ISO 3166-1 alpha-2
  vat_id: FieldWithConfidence<string>;       // e.g. "NL123456789B01"
  kvk_coc: FieldWithConfidence<string>;      // Dutch KVK / Chamber of Commerce number
  iban: FieldWithConfidence<string>;
}

/** Individual line item on the invoice (extraction layer, with per-field confidence) */
export interface ExtractedLineItem {
  description: FieldWithConfidence<string>;
  quantity: FieldWithConfidence<number>;
  unit_price: FieldWithConfidence<number>;
  vat_rate: FieldWithConfidence<number>;     // e.g. 21.0 for 21%
  amount: FieldWithConfidence<number>;       // quantity * unit_price
}

/** AI-suggested expense category */
export interface CategorySuggestion {
  category: string;           // e.g. "office_supplies", "travel", "professional_services"
  confidence: Confidence;
}

/** AI-suggested booking / accounting treatment */
export interface BookingSuggestion {
  account_code: string;       // e.g. "4200" (general ledger code)
  cost_center: string | null; // e.g. "DEPT-ENGINEERING"
  tax_treatment: string;      // e.g. "input_vat_deductible", "reverse_charge", "exempt"
  confidence: Confidence;
}

/** Processing metadata attached to every extraction */
export interface ExtractionMetadata {
  ocr_model: string;          // e.g. "donut-v2"
  extraction_version: string; // e.g. "v1.2.0"
  processing_timestamp: string; // ISO 8601
}

/**
 * Top-level extracted invoice metadata (stored in extracted_data.extracted_json).
 *
 * This is the "rich" model with per-field confidence. The simpler
 * InvoiceMetadata in src/types/index.ts is the UI-friendly version
 * with flat values (no confidence wrappers).
 */
export interface ExtractedInvoiceMetadata {
  supplier: ExtractedSupplier;
  invoice_number: FieldWithConfidence<string>;
  invoice_date: FieldWithConfidence<string>;    // ISO 8601 date: "YYYY-MM-DD"
  due_date: FieldWithConfidence<string>;        // ISO 8601 date: "YYYY-MM-DD"
  payment_terms: FieldWithConfidence<string>;   // e.g. "Net 30"
  currency: FieldWithConfidence<string>;        // ISO 4217: "EUR", "USD"
  subtotal: FieldWithConfidence<number>;
  vat_amount: FieldWithConfidence<number>;
  vat_rate: FieldWithConfidence<number>;        // primary VAT rate, e.g. 21.0
  total_amount: FieldWithConfidence<number>;
  line_items: ExtractedLineItem[];
  category_suggestion: CategorySuggestion;
  booking_suggestion: BookingSuggestion;
  metadata: ExtractionMetadata;
}

// ============================================================
// Correction types (used when user submits corrections)
// ============================================================

/** Partial correction: only the fields the user changed */
export interface CorrectionPayload {
  extracted_data_id: string;
  corrected_fields: DeepPartial<ExtractedInvoiceMetadata>;
}

/** Diff entry showing old and new values for a changed field */
export interface DiffEntry<T = unknown> {
  old: FieldWithConfidence<T>;
  new: FieldWithConfidence<T>;
}

/** Utility type for deep partial objects */
export type DeepPartial<T> = {
  [P in keyof T]?: T[P] extends object ? DeepPartial<T[P]> : T[P];
};
