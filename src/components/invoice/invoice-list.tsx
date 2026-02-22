import { useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { format, startOfMonth } from "date-fns";
import { Search, FileText, ChevronLeft, ChevronRight } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { StatusBadge } from "@/components/common/status-badge";
import { ConfidenceBadge } from "@/components/common/confidence-badge";
import { EmptyState } from "@/components/common/empty-state";
import { Spinner } from "@/components/ui/spinner";
import { formatCurrency, formatDate } from "@/lib/utils";
import type { Invoice, InvoiceFilters, InvoiceStatus } from "@/types";

// ─────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────

interface InvoiceListProps {
  invoices: Invoice[] | undefined;
  total: number;
  totalPages: number;
  currentPage: number;
  isLoading: boolean;
  filters: InvoiceFilters;
  onFiltersChange: (filters: InvoiceFilters) => void;
  /** Base path for navigation (e.g., "/invoices" or "/admin/invoices") */
  basePath: string;
  showTenantColumn?: boolean;
}

const STATUS_OPTIONS: { value: InvoiceStatus | "all"; label: string }[] = [
  { value: "all", label: "All Statuses" },
  { value: "uploaded", label: "Uploaded" },
  { value: "processing", label: "Processing" },
  { value: "extracted", label: "Extracted" },
  { value: "user_review", label: "Needs Review" },
  { value: "user_confirmed", label: "User Confirmed" },
  { value: "admin_review", label: "Admin Review" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
  { value: "unreadable", label: "Unreadable" },
];

// ─────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────

export function InvoiceList({
  invoices,
  total,
  totalPages,
  currentPage,
  isLoading,
  filters,
  onFiltersChange,
  basePath,
}: InvoiceListProps) {
  const navigate = useNavigate();
  const [searchInput, setSearchInput] = useState(filters.search ?? "");

  const currentMonth =
    filters.month ?? format(startOfMonth(new Date()), "yyyy-MM");

  const handleSearch = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      onFiltersChange({ ...filters, search: searchInput || undefined, page: 1 });
    },
    [filters, searchInput, onFiltersChange],
  );

  const handleStatusChange = useCallback(
    (value: string) => {
      onFiltersChange({
        ...filters,
        status: value === "all" ? undefined : (value as InvoiceStatus),
        page: 1,
      });
    },
    [filters, onFiltersChange],
  );

  const handleMonthChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      onFiltersChange({ ...filters, month: e.target.value, page: 1 });
    },
    [filters, onFiltersChange],
  );

  const handlePageChange = useCallback(
    (page: number) => {
      onFiltersChange({ ...filters, page });
    },
    [filters, onFiltersChange],
  );

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <form onSubmit={handleSearch} className="flex gap-2 flex-1 min-w-[200px]">
          <div className="relative flex-1">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search supplier or invoice number..."
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              className="pl-8"
            />
          </div>
          <Button type="submit" variant="secondary" size="default">
            Search
          </Button>
        </form>

        <Input
          type="month"
          value={currentMonth}
          onChange={handleMonthChange}
          className="w-[180px]"
        />

        <Select
          value={filters.status ?? "all"}
          onValueChange={handleStatusChange}
        >
          <SelectTrigger className="w-[180px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {STATUS_OPTIONS.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Table */}
      <div className="rounded-md border bg-card">
        {isLoading ? (
          <div className="flex items-center justify-center py-16">
            <Spinner size="lg" />
          </div>
        ) : !invoices || invoices.length === 0 ? (
          <EmptyState
            icon={FileText}
            title="No invoices found"
            description="Try adjusting your filters or uploading an invoice via WhatsApp."
          />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Date</TableHead>
                <TableHead>Supplier</TableHead>
                <TableHead>Invoice #</TableHead>
                <TableHead className="text-right">Amount</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Confidence</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {invoices.map((invoice) => {
                const meta = invoice.extracted_data?.metadata;
                const conf = invoice.extracted_data?.confidence;
                return (
                  <TableRow
                    key={invoice.id}
                    className="cursor-pointer"
                    onClick={() => navigate(`${basePath}/${invoice.id}`)}
                  >
                    <TableCell className="font-medium">
                      {meta?.invoice_date
                        ? formatDate(meta.invoice_date)
                        : formatDate(invoice.created_at)}
                    </TableCell>
                    <TableCell>{meta?.supplier_name ?? "---"}</TableCell>
                    <TableCell className="font-mono text-xs">
                      {meta?.invoice_number ?? "---"}
                    </TableCell>
                    <TableCell className="text-right font-medium">
                      {meta
                        ? formatCurrency(meta.total_amount, meta.currency)
                        : "---"}
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={invoice.status} />
                    </TableCell>
                    <TableCell>
                      {conf ? (
                        <ConfidenceBadge
                          score={conf.overall}
                          showLabel={false}
                        />
                      ) : (
                        <span className="text-xs text-muted-foreground">
                          N/A
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={(e) => {
                          e.stopPropagation();
                          navigate(`${basePath}/${invoice.id}`);
                        }}
                      >
                        View
                      </Button>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <p className="text-sm text-muted-foreground">
            Showing page {currentPage} of {totalPages} ({total} total)
          </p>
          <div className="flex gap-1">
            <Button
              variant="outline"
              size="icon"
              disabled={currentPage <= 1}
              onClick={() => handlePageChange(currentPage - 1)}
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            {Array.from({ length: Math.min(totalPages, 5) }, (_, i) => {
              // Show pages around current
              let page: number;
              if (totalPages <= 5) {
                page = i + 1;
              } else if (currentPage <= 3) {
                page = i + 1;
              } else if (currentPage >= totalPages - 2) {
                page = totalPages - 4 + i;
              } else {
                page = currentPage - 2 + i;
              }
              return (
                <Button
                  key={page}
                  variant={page === currentPage ? "default" : "outline"}
                  size="icon"
                  onClick={() => handlePageChange(page)}
                >
                  {page}
                </Button>
              );
            })}
            <Button
              variant="outline"
              size="icon"
              disabled={currentPage >= totalPages}
              onClick={() => handlePageChange(currentPage + 1)}
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
