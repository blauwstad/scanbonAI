import { useState, useCallback } from "react";
import {
  Check,
  X,
  PenLine,
  AlertTriangle,
  ArrowRight,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { ConfidenceBadge } from "@/components/common/confidence-badge";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { cn } from "@/lib/utils";
import type {
  InvoiceMetadata,
  FieldConfidenceScores,
  FieldCorrection,
  AdminAction,
} from "@/types";

// ─────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────

interface AdminDiffViewProps {
  aiMetadata: InvoiceMetadata;
  userMetadata: Partial<InvoiceMetadata> | null;
  confidence: FieldConfidenceScores;
  fieldCorrections: FieldCorrection[];
  onAction: (action: AdminAction, overrides?: FieldCorrection[], notes?: string) => void;
  isSubmitting: boolean;
}

type FieldKey = keyof Omit<InvoiceMetadata, "line_items">;

const FIELD_LABELS: Record<FieldKey, string> = {
  invoice_number: "Invoice Number",
  invoice_date: "Invoice Date",
  due_date: "Due Date",
  supplier_name: "Supplier Name",
  supplier_address: "Supplier Address",
  supplier_tax_id: "Supplier Tax ID",
  supplier_iban: "Supplier IBAN",
  recipient_name: "Recipient Name",
  recipient_address: "Recipient Address",
  recipient_tax_id: "Recipient Tax ID",
  subtotal: "Subtotal",
  vat_amount: "VAT Amount",
  total_amount: "Total Amount",
  currency: "Currency",
  category: "Category",
  payment_terms: "Payment Terms",
  notes: "Notes",
};

const DISPLAY_FIELDS = Object.keys(FIELD_LABELS) as FieldKey[];

// ─────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────

export function AdminDiffView({
  aiMetadata,
  userMetadata,
  confidence,
  fieldCorrections,
  onAction,
  isSubmitting,
}: AdminDiffViewProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [overrideValues, setOverrideValues] = useState<
    Record<string, string>
  >({});
  const [adminNotes, setAdminNotes] = useState("");

  // Build a set of changed field names for quick lookup
  const changedFields = new Set(
    fieldCorrections.map((c) => c.field_name),
  );

  const getDisplayValue = (value: unknown): string => {
    if (value === null || value === undefined) return "---";
    if (typeof value === "number") return value.toString();
    return String(value);
  };

  const handleApprove = useCallback(() => {
    onAction("approve", undefined, adminNotes || undefined);
  }, [onAction, adminNotes]);

  const handleReject = useCallback(() => {
    onAction("reject", undefined, adminNotes || undefined);
  }, [onAction, adminNotes]);

  const handleFlag = useCallback(() => {
    onAction("flag_for_review", undefined, adminNotes || undefined);
  }, [onAction, adminNotes]);

  const handleOverride = useCallback(() => {
    const overrides: FieldCorrection[] = Object.entries(overrideValues)
      .filter(([, v]) => v !== "")
      .map(([field, value]) => ({
        field_name: field,
        original_value:
          userMetadata?.[field as FieldKey] ?? aiMetadata[field as FieldKey],
        corrected_value: value,
        ai_confidence:
          (confidence[field as keyof FieldConfidenceScores] as number) ?? 0,
      }));

    onAction("override", overrides, adminNotes || undefined);
    setIsEditing(false);
  }, [overrideValues, adminNotes, userMetadata, aiMetadata, confidence, onAction]);

  return (
    <div className="space-y-6">
      {/* Diff Table */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between">
            <span>Field Comparison</span>
            <Button
              variant={isEditing ? "default" : "outline"}
              size="sm"
              onClick={() => setIsEditing(!isEditing)}
            >
              <PenLine className="h-3 w-3 mr-1" />
              {isEditing ? "Cancel Edit" : "Override"}
            </Button>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-0 divide-y">
            {/* Header row */}
            <div className="grid grid-cols-12 gap-2 py-2 text-xs font-semibold text-muted-foreground uppercase">
              <div className="col-span-3">Field</div>
              <div className="col-span-3">AI Extracted</div>
              <div className="col-span-1 text-center">Conf.</div>
              <div className="col-span-1 text-center" />
              <div className="col-span-3">
                {userMetadata ? "User Corrected" : "No User Corrections"}
              </div>
              {isEditing && (
                <div className="col-span-1">Override</div>
              )}
            </div>

            {/* Data rows */}
            {DISPLAY_FIELDS.map((field) => {
              const aiValue = getDisplayValue(aiMetadata[field]);
              const userValue = userMetadata
                ? getDisplayValue(userMetadata[field])
                : null;
              const isChanged = changedFields.has(field);
              const confScore = confidence[
                field as keyof FieldConfidenceScores
              ] as number | undefined;

              return (
                <div
                  key={field}
                  className={cn(
                    "grid grid-cols-12 gap-2 py-2.5 items-center text-sm",
                    isChanged && "bg-yellow-50",
                  )}
                >
                  {/* Field name */}
                  <div className="col-span-3 font-medium text-xs">
                    {FIELD_LABELS[field]}
                  </div>

                  {/* AI value */}
                  <div className="col-span-3 font-mono text-xs">
                    {aiValue}
                  </div>

                  {/* Confidence */}
                  <div className="col-span-1 text-center">
                    {confScore !== undefined ? (
                      <ConfidenceBadge score={confScore} showLabel={false} />
                    ) : (
                      <span className="text-xs text-muted-foreground">--</span>
                    )}
                  </div>

                  {/* Arrow */}
                  <div className="col-span-1 text-center">
                    {isChanged && (
                      <ArrowRight className="h-3 w-3 mx-auto text-yellow-600" />
                    )}
                  </div>

                  {/* User value */}
                  <div
                    className={cn(
                      "col-span-3 font-mono text-xs",
                      isChanged && "font-semibold text-yellow-800",
                    )}
                  >
                    {userValue ?? (
                      <span className="text-muted-foreground italic">
                        unchanged
                      </span>
                    )}
                  </div>

                  {/* Override input */}
                  {isEditing && (
                    <div className="col-span-1">
                      <Input
                        className="h-7 text-xs"
                        placeholder="..."
                        value={overrideValues[field] ?? ""}
                        onChange={(e) =>
                          setOverrideValues((prev) => ({
                            ...prev,
                            [field]: e.target.value,
                          }))
                        }
                      />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      {/* Admin Notes */}
      <div className="space-y-2">
        <Label htmlFor="admin-notes">Admin Notes</Label>
        <Textarea
          id="admin-notes"
          placeholder="Add optional notes about this review..."
          value={adminNotes}
          onChange={(e) => setAdminNotes(e.target.value)}
          rows={3}
        />
      </div>

      {/* Actions */}
      <div className="flex items-center gap-3 border-t pt-4">
        <Button
          variant="success"
          onClick={handleApprove}
          disabled={isSubmitting}
        >
          <Check className="h-4 w-4 mr-1" />
          Approve
        </Button>

        {isEditing && (
          <Button
            variant="warning"
            onClick={handleOverride}
            disabled={
              isSubmitting ||
              Object.values(overrideValues).every((v) => v === "")
            }
          >
            <PenLine className="h-4 w-4 mr-1" />
            Save Override
          </Button>
        )}

        <Button
          variant="outline"
          onClick={handleFlag}
          disabled={isSubmitting}
        >
          <AlertTriangle className="h-4 w-4 mr-1" />
          Flag for Review
        </Button>

        <Button
          variant="destructive"
          onClick={handleReject}
          disabled={isSubmitting}
          className="ml-auto"
        >
          <X className="h-4 w-4 mr-1" />
          Reject
        </Button>
      </div>
    </div>
  );
}
