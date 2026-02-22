import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, CheckCircle } from "lucide-react";
import { Header } from "@/components/layout/header";
import { InvoiceViewer } from "@/components/invoice/invoice-viewer";
import { MetadataForm } from "@/components/invoice/metadata-form";
import { ConfidenceBadge } from "@/components/common/confidence-badge";
import { StatusBadge } from "@/components/common/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { FullPageSpinner } from "@/components/ui/spinner";
import {
  useInvoice,
  useInvoiceImageUrl,
  useSubmitCorrections,
  useConfirmInvoice,
} from "@/hooks/use-invoices";

export function InvoiceReviewPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const { data: invoiceRes, isLoading: invoiceLoading } = useInvoice(id!);
  const { data: imageRes, isLoading: imageLoading } = useInvoiceImageUrl(id!);
  const submitCorrections = useSubmitCorrections(id!);
  const confirmInvoice = useConfirmInvoice(id!);

  if (invoiceLoading) return <FullPageSpinner />;

  const invoice = invoiceRes?.data;
  if (!invoice) {
    return (
      <div className="flex flex-col items-center justify-center h-full py-20">
        <p className="text-lg text-muted-foreground">Invoice not found</p>
        <Button
          variant="ghost"
          className="mt-4"
          onClick={() => navigate("/invoices")}
        >
          Back to list
        </Button>
      </div>
    );
  }

  const extractedData = invoice.extracted_data;
  const metadata = extractedData?.metadata;
  const confidence = extractedData?.confidence;

  const canEdit =
    invoice.status === "extracted" ||
    invoice.status === "user_review";

  return (
    <div>
      <Header
        title="Invoice Review"
        description={
          metadata
            ? `${metadata.supplier_name} - ${metadata.invoice_number}`
            : "Review extracted data"
        }
        actions={
          <Button
            variant="ghost"
            size="sm"
            onClick={() => navigate("/invoices")}
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
              <span className="text-muted-foreground">Overall confidence:</span>
              <ConfidenceBadge score={confidence.overall} />
            </div>
          )}
          {canEdit && (
            <Button
              variant="success"
              size="sm"
              className="ml-auto"
              onClick={() => confirmInvoice.mutate()}
              disabled={confirmInvoice.isPending}
            >
              <CheckCircle className="h-4 w-4 mr-1" />
              {confirmInvoice.isPending ? "Confirming..." : "Confirm as Correct"}
            </Button>
          )}
        </div>

        {/* Main two-column layout */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Left: Invoice image */}
          <InvoiceViewer
            imageUrl={imageRes?.data.signed_url}
            isLoading={imageLoading}
            className="sticky top-6 h-[calc(100vh-12rem)]"
          />

          {/* Right: Metadata form */}
          <Card>
            <CardHeader>
              <CardTitle>Extracted Metadata</CardTitle>
            </CardHeader>
            <CardContent>
              {metadata && confidence ? (
                <MetadataForm
                  metadata={metadata}
                  confidence={confidence.fields}
                  readOnly={!canEdit}
                  onSubmit={
                    canEdit
                      ? (data) => submitCorrections.mutate(data)
                      : undefined
                  }
                  isSubmitting={submitCorrections.isPending}
                />
              ) : (
                <div className="py-8 text-center text-muted-foreground">
                  {invoice.status === "processing"
                    ? "Invoice is currently being processed..."
                    : invoice.status === "unreadable"
                      ? "This invoice could not be read. Please upload a clearer photo."
                      : "No extracted data available."}
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Quality check info */}
        {invoice.quality_check && (
          <Card className="mt-6">
            <CardHeader>
              <CardTitle className="text-sm">Quality Check</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                <div>
                  <span className="text-muted-foreground">Readable:</span>{" "}
                  <span
                    className={
                      invoice.quality_check.is_readable
                        ? "text-green-600"
                        : "text-red-600"
                    }
                  >
                    {invoice.quality_check.is_readable ? "Yes" : "No"}
                  </span>
                </div>
                <div>
                  <span className="text-muted-foreground">Quality Score:</span>{" "}
                  {(invoice.quality_check.quality_score * 100).toFixed(0)}%
                </div>
                <div className="col-span-2">
                  {invoice.quality_check.flagged_issues.length > 0 && (
                    <div>
                      <span className="text-muted-foreground">Issues: </span>
                      {invoice.quality_check.flagged_issues.join(", ")}
                    </div>
                  )}
                </div>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
