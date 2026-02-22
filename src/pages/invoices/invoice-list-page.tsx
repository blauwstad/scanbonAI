import { useState } from "react";
import { Header } from "@/components/layout/header";
import { InvoiceList } from "@/components/invoice/invoice-list";
import { useInvoices } from "@/hooks/use-invoices";
import type { InvoiceFilters } from "@/types";

export function InvoiceListPage() {
  const [filters, setFilters] = useState<InvoiceFilters>({
    page: 1,
    page_size: 20,
  });

  const { data, isLoading } = useInvoices(filters);

  return (
    <div>
      <Header
        title="My Invoices"
        description="View and review your extracted invoices"
      />
      <div className="p-6">
        <InvoiceList
          invoices={data?.data}
          total={data?.total ?? 0}
          totalPages={data?.total_pages ?? 0}
          currentPage={data?.page ?? 1}
          isLoading={isLoading}
          filters={filters}
          onFiltersChange={setFilters}
          basePath="/invoices"
        />
      </div>
    </div>
  );
}
