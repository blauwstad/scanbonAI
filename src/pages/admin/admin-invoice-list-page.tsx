import { useState } from "react";
import { Header } from "@/components/layout/header";
import { InvoiceList } from "@/components/invoice/invoice-list";
import { useAdminInvoices } from "@/hooks/use-admin";
import type { AdminInvoiceFilters } from "@/types";

export function AdminInvoiceListPage() {
  const [filters, setFilters] = useState<AdminInvoiceFilters>({
    page: 1,
    page_size: 25,
  });

  const { data, isLoading } = useAdminInvoices(filters);

  return (
    <div>
      <Header
        title="All Invoices"
        description="Manage and review all tenant invoices"
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
          basePath="/admin/invoices"
          showTenantColumn
        />
      </div>
    </div>
  );
}
