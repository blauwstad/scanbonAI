import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { Header } from "@/components/layout/header";
import { InvoiceViewer } from "@/components/invoice/invoice-viewer";
import { AdminDiffView } from "@/components/admin/diff-view";
import { StatusBadge } from "@/components/common/status-badge";
import { ConfidenceBadge } from "@/components/common/confidence-badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { FullPageSpinner } from "@/components/ui/spinner";
import { useAdminInvoice, useAdminReview } from "@/hooks/use-admin";
import { useInvoiceImageUrl } from "@/hooks/use-invoices";
import type { AdminAction, FieldCorrection } from "@/types";

export function AdminInvoiceDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const { data: invoiceRes, isLoading } = useAdminInvoice(id!);
  const { data: imageRes, isLoading: imageLoading } = useInvoiceImageUrl(id!);
  const reviewMutation = useAdminReview(id!);

  if (isLoading) return <FullPageSpinner />;

  const invoice = invoiceRes?.data;
  if (!invoice) {
    return (
      <div className="flex flex-col items-center justify-center h-full py-20">
        <p className="text-lg text-muted-foreground">Invoice not found</p>
        <Button
          variant="ghost"
          className="mt-4"
          onClick={() => navigate("/admin/invoices")}
        >
          Back to list
        </Button>
      </div>
    );
  }

  const extracted = invoice.extracted_data;
  const metadata = extracted?.metadata;
  const confidence = extracted?.confidence;

  const handleAction = (
    action: AdminAction,
    overrides?: FieldCorrection[],
    notes?: string,
  ) => {
    reviewMutation.mutate(
      {
        action,
        overridden_fields: overrides,
        notes,
      },
      {
        onSuccess: () => {
          if (action === "approve" || action === "reject") {
            navigate("/admin/invoices");
          }
        },
      },
    );
  };

  return (
    <div>
      <Header
        title="Invoice Detail"
        description={
          metadata
            ? `${metadata.supplier_name} - ${metadata.invoice_number}`
            : `Invoice ${id}`
        }
        actions={
          <Button
            variant="ghost"
            size="sm"
            onClick={() => navigate("/admin/invoices")}
          >
            <ArrowLeft className="h-4 w-4 mr-1" />
            Back
          </Button>
        }
      />

      <div className="p-6">
        {/* Status bar */}
        <div className="flex items-center gap-4 mb-6">
          <StatusBadge status={invoice.status} />
          {confidence && (
            <div className="flex items-center gap-2 text-sm">
              <span className="text-muted-foreground">Overall:</span>
              <ConfidenceBadge score={confidence.overall} />
            </div>
          )}
          {invoice.admin_review && (
            <span className="text-sm text-muted-foreground">
              Reviewed at{" "}
              {new Date(invoice.admin_review.reviewed_at).toLocaleString()}
            </span>
          )}
        </div>

        {/* Two-column: Image + Diff */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <InvoiceViewer
            imageUrl={imageRes?.data.signed_url}
            isLoading={imageLoading}
            className="sticky top-6 h-[calc(100vh-12rem)]"
          />

          <Card>
            <CardHeader>
              <CardTitle>AI vs User Comparison</CardTitle>
            </CardHeader>
            <CardContent>
              {metadata && confidence ? (
                <AdminDiffView
                  aiMetadata={metadata}
                  userMetadata={
                    invoice.user_corrections?.corrected_metadata ?? null
                  }
                  confidence={confidence.fields}
                  fieldCorrections={
                    invoice.user_corrections?.field_corrections ?? []
                  }
                  onAction={handleAction}
                  isSubmitting={reviewMutation.isPending}
                />
              ) : (
                <p className="text-center py-8 text-muted-foreground">
                  No extracted data available for comparison
                </p>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
