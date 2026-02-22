import { useState, useCallback, useMemo } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ConfidenceBadge } from "@/components/common/confidence-badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { getConfidenceBorderColor } from "@/lib/utils";
import type {
  InvoiceMetadata,
  FieldConfidenceScores,
  LineItem,
  InvoiceCategory,
  FieldCorrection,
} from "@/types";

// ─────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────

interface MetadataFormProps {
  metadata: InvoiceMetadata;
  confidence: FieldConfidenceScores;
  readOnly?: boolean;
  onSubmit?: (data: {
    corrected_metadata: Partial<InvoiceMetadata>;
    field_corrections: FieldCorrection[];
  }) => void;
  isSubmitting?: boolean;
}

type FieldKey = keyof Omit<InvoiceMetadata, "line_items">;

interface FieldConfig {
  key: FieldKey;
  label: string;
  type: "text" | "number" | "date" | "currency" | "dropdown";
  required: boolean;
  options?: { value: string; label: string }[];
  section: "invoice" | "supplier" | "recipient" | "amounts";
}

// ─────────────────────────────────────────────────────────────
// Category options
// ─────────────────────────────────────────────────────────────

const CATEGORY_OPTIONS: { value: InvoiceCategory; label: string }[] = [
  { value: "office_supplies", label: "Office Supplies" },
  { value: "travel", label: "Travel" },
  { value: "meals", label: "Meals & Entertainment" },
  { value: "utilities", label: "Utilities" },
  { value: "rent", label: "Rent" },
  { value: "insurance", label: "Insurance" },
  { value: "professional_services", label: "Professional Services" },
  { value: "equipment", label: "Equipment" },
  { value: "telecommunications", label: "Telecommunications" },
  { value: "vehicle", label: "Vehicle" },
  { value: "other", label: "Other" },
];

// ─────────────────────────────────────────────────────────────
// Field definitions
// ─────────────────────────────────────────────────────────────

const FIELD_CONFIGS: FieldConfig[] = [
  // Invoice section
  { key: "invoice_number", label: "Invoice Number", type: "text", required: true, section: "invoice" },
  { key: "invoice_date", label: "Invoice Date", type: "date", required: true, section: "invoice" },
  { key: "due_date", label: "Due Date", type: "date", required: false, section: "invoice" },
  { key: "currency", label: "Currency", type: "text", required: true, section: "invoice" },
  {
    key: "category",
    label: "Category",
    type: "dropdown",
    required: true,
    section: "invoice",
    options: CATEGORY_OPTIONS,
  },
  { key: "payment_terms", label: "Payment Terms", type: "text", required: false, section: "invoice" },
  { key: "notes", label: "Notes", type: "text", required: false, section: "invoice" },

  // Supplier section
  { key: "supplier_name", label: "Supplier Name", type: "text", required: true, section: "supplier" },
  { key: "supplier_address", label: "Supplier Address", type: "text", required: true, section: "supplier" },
  { key: "supplier_tax_id", label: "Supplier Tax ID", type: "text", required: true, section: "supplier" },
  { key: "supplier_iban", label: "Supplier IBAN", type: "text", required: false, section: "supplier" },

  // Recipient section
  { key: "recipient_name", label: "Recipient Name", type: "text", required: true, section: "recipient" },
  { key: "recipient_address", label: "Recipient Address", type: "text", required: true, section: "recipient" },
  { key: "recipient_tax_id", label: "Recipient Tax ID", type: "text", required: false, section: "recipient" },

  // Amounts section
  { key: "subtotal", label: "Subtotal", type: "currency", required: true, section: "amounts" },
  { key: "vat_amount", label: "VAT Amount", type: "currency", required: true, section: "amounts" },
  { key: "total_amount", label: "Total Amount", type: "currency", required: true, section: "amounts" },
];

const SECTIONS = [
  { key: "invoice" as const, label: "Invoice Details" },
  { key: "supplier" as const, label: "Supplier" },
  { key: "recipient" as const, label: "Recipient" },
  { key: "amounts" as const, label: "Amounts" },
];

// ─────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────

export function MetadataForm({
  metadata,
  confidence,
  readOnly = false,
  onSubmit,
  isSubmitting = false,
}: MetadataFormProps) {
  const [formData, setFormData] = useState<InvoiceMetadata>({ ...metadata });
  const [modifiedFields, setModifiedFields] = useState<Set<string>>(new Set());

  // Track which fields have been modified compared to original
  const updateField = useCallback(
    (key: FieldKey, value: unknown) => {
      setFormData((prev) => ({ ...prev, [key]: value }));
      setModifiedFields((prev) => {
        const next = new Set(prev);
        const original = metadata[key];
        if (value !== original) {
          next.add(key);
        } else {
          next.delete(key);
        }
        return next;
      });
    },
    [metadata],
  );

  // Line items management
  const updateLineItem = useCallback(
    (index: number, field: keyof LineItem, value: string | number) => {
      setFormData((prev) => {
        const items = [...prev.line_items];
        items[index] = { ...items[index], [field]: value };
        // Recalculate total if quantity or unit_price changed
        if (field === "quantity" || field === "unit_price") {
          items[index].total = items[index].quantity * items[index].unit_price;
        }
        return { ...prev, line_items: items };
      });
      setModifiedFields((prev) => new Set(prev).add(`line_items[${index}].${field}`));
    },
    [],
  );

  const addLineItem = useCallback(() => {
    setFormData((prev) => ({
      ...prev,
      line_items: [
        ...prev.line_items,
        {
          id: `new-${Date.now()}`,
          description: "",
          quantity: 1,
          unit_price: 0,
          total: 0,
          vat_rate: 19,
          vat_amount: 0,
        },
      ],
    }));
  }, []);

  const removeLineItem = useCallback((index: number) => {
    setFormData((prev) => ({
      ...prev,
      line_items: prev.line_items.filter((_, i) => i !== index),
    }));
    setModifiedFields((prev) => new Set(prev).add("line_items"));
  }, []);

  // Build corrections diff for submission
  const handleSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      if (!onSubmit) return;

      const corrected_metadata: Partial<InvoiceMetadata> = {};
      const field_corrections: FieldCorrection[] = [];

      for (const key of modifiedFields) {
        // Handle top-level fields only (line items handled separately)
        if (key.startsWith("line_items")) continue;

        const fieldKey = key as FieldKey;
        const confScore =
          confidence[fieldKey as keyof FieldConfidenceScores] ?? 0;

        corrected_metadata[fieldKey] = formData[fieldKey] as never;
        field_corrections.push({
          field_name: fieldKey,
          original_value: metadata[fieldKey],
          corrected_value: formData[fieldKey],
          ai_confidence: typeof confScore === "number" ? confScore : 0,
        });
      }

      // If any line item changed, include entire line_items array
      const hasLineItemChanges = Array.from(modifiedFields).some((k) =>
        k.startsWith("line_items"),
      );
      if (hasLineItemChanges) {
        corrected_metadata.line_items = formData.line_items;
        field_corrections.push({
          field_name: "line_items",
          original_value: metadata.line_items,
          corrected_value: formData.line_items,
          ai_confidence: 0,
        });
      }

      onSubmit({ corrected_metadata, field_corrections });
    },
    [formData, metadata, confidence, modifiedFields, onSubmit],
  );

  const hasChanges = modifiedFields.size > 0;

  // Group fields by section
  const fieldsBySection = useMemo(() => {
    const map: Record<string, FieldConfig[]> = {};
    for (const f of FIELD_CONFIGS) {
      (map[f.section] ??= []).push(f);
    }
    return map;
  }, []);

  // ───────────────────────────────────────────────────────────
  // Render helpers
  // ───────────────────────────────────────────────────────────

  function renderField(config: FieldConfig) {
    const value = formData[config.key];
    const confScore =
      confidence[config.key as keyof FieldConfidenceScores];
    const numericConf = typeof confScore === "number" ? confScore : undefined;
    const isModified = modifiedFields.has(config.key);

    return (
      <div key={config.key} className="space-y-1.5">
        <div className="flex items-center justify-between">
          <Label htmlFor={config.key} className="flex items-center gap-1">
            {config.label}
            {config.required && (
              <span className="text-destructive">*</span>
            )}
          </Label>
          {numericConf !== undefined && (
            <ConfidenceBadge score={numericConf} showLabel={false} />
          )}
        </div>

        {config.type === "dropdown" && config.options ? (
          <Select
            value={String(value ?? "")}
            onValueChange={(v) => updateField(config.key, v)}
            disabled={readOnly}
          >
            <SelectTrigger
              className={cn(
                isModified && "ring-2 ring-blue-400",
                numericConf !== undefined &&
                  numericConf < 0.9 &&
                  getConfidenceBorderColor(numericConf),
              )}
            >
              <SelectValue placeholder={`Select ${config.label}`} />
            </SelectTrigger>
            <SelectContent>
              {config.options.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : (
          <Input
            id={config.key}
            type={
              config.type === "currency" || config.type === "number"
                ? "number"
                : config.type === "date"
                  ? "date"
                  : "text"
            }
            step={config.type === "currency" ? "0.01" : undefined}
            value={value ?? ""}
            onChange={(e) => {
              const v =
                config.type === "currency" || config.type === "number"
                  ? parseFloat(e.target.value) || 0
                  : e.target.value;
              updateField(config.key, v);
            }}
            readOnly={readOnly}
            required={config.required}
            className={cn(
              isModified && "ring-2 ring-blue-400",
              numericConf !== undefined &&
                numericConf < 0.9 &&
                getConfidenceBorderColor(numericConf),
            )}
          />
        )}

        {isModified && (
          <p className="text-xs text-blue-600">Modified from original</p>
        )}
      </div>
    );
  }

  // ───────────────────────────────────────────────────────────
  // Main render
  // ───────────────────────────────────────────────────────────

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      {/* Sections */}
      {SECTIONS.map((section) => (
        <div key={section.key}>
          <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide mb-3">
            {section.label}
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {(fieldsBySection[section.key] ?? []).map(renderField)}
          </div>
        </div>
      ))}

      {/* Line Items */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
            Line Items
          </h3>
          {!readOnly && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={addLineItem}
            >
              <Plus className="h-3 w-3 mr-1" />
              Add Item
            </Button>
          )}
        </div>

        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[30%]">Description</TableHead>
                <TableHead className="w-[10%]">Qty</TableHead>
                <TableHead className="w-[15%]">Unit Price</TableHead>
                <TableHead className="w-[12%]">VAT %</TableHead>
                <TableHead className="w-[12%]">VAT</TableHead>
                <TableHead className="w-[15%]">Total</TableHead>
                {!readOnly && <TableHead className="w-[6%]" />}
              </TableRow>
            </TableHeader>
            <TableBody>
              {formData.line_items.length === 0 ? (
                <TableRow>
                  <TableCell
                    colSpan={readOnly ? 6 : 7}
                    className="h-16 text-center text-muted-foreground"
                  >
                    No line items
                  </TableCell>
                </TableRow>
              ) : (
                formData.line_items.map((item, index) => (
                  <TableRow key={item.id}>
                    <TableCell>
                      <Input
                        value={item.description}
                        onChange={(e) =>
                          updateLineItem(index, "description", e.target.value)
                        }
                        readOnly={readOnly}
                        className="h-8 text-xs"
                      />
                    </TableCell>
                    <TableCell>
                      <Input
                        type="number"
                        value={item.quantity}
                        onChange={(e) =>
                          updateLineItem(
                            index,
                            "quantity",
                            parseFloat(e.target.value) || 0,
                          )
                        }
                        readOnly={readOnly}
                        className="h-8 text-xs"
                      />
                    </TableCell>
                    <TableCell>
                      <Input
                        type="number"
                        step="0.01"
                        value={item.unit_price}
                        onChange={(e) =>
                          updateLineItem(
                            index,
                            "unit_price",
                            parseFloat(e.target.value) || 0,
                          )
                        }
                        readOnly={readOnly}
                        className="h-8 text-xs"
                      />
                    </TableCell>
                    <TableCell>
                      <Input
                        type="number"
                        step="0.01"
                        value={item.vat_rate}
                        onChange={(e) =>
                          updateLineItem(
                            index,
                            "vat_rate",
                            parseFloat(e.target.value) || 0,
                          )
                        }
                        readOnly={readOnly}
                        className="h-8 text-xs"
                      />
                    </TableCell>
                    <TableCell>
                      <span className="text-xs text-muted-foreground">
                        {item.vat_amount.toFixed(2)}
                      </span>
                    </TableCell>
                    <TableCell>
                      <span className="text-xs font-medium">
                        {item.total.toFixed(2)}
                      </span>
                    </TableCell>
                    {!readOnly && (
                      <TableCell>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          onClick={() => removeLineItem(index)}
                        >
                          <Trash2 className="h-3 w-3 text-destructive" />
                        </Button>
                      </TableCell>
                    )}
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      </div>

      {/* Submit */}
      {!readOnly && onSubmit && (
        <div className="flex items-center justify-between border-t pt-4">
          <p className="text-sm text-muted-foreground">
            {hasChanges
              ? `${modifiedFields.size} field(s) modified`
              : "No changes made"}
          </p>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                setFormData({ ...metadata });
                setModifiedFields(new Set());
              }}
              disabled={!hasChanges || isSubmitting}
            >
              Reset
            </Button>
            <Button
              type="submit"
              disabled={!hasChanges || isSubmitting}
            >
              {isSubmitting ? "Submitting..." : "Submit Corrections"}
            </Button>
          </div>
        </div>
      )}
    </form>
  );
}
