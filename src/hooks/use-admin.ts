import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { adminApi } from "@/api/client";
import type {
  AdminInvoiceFilters,
  AdminAction,
  FieldCorrection,
  ExportRequest,
} from "@/types";
import toast from "react-hot-toast";

export function useAdminInvoices(filters: AdminInvoiceFilters = {}) {
  return useQuery({
    queryKey: ["admin-invoices", filters],
    queryFn: () => adminApi.listInvoices(filters),
    staleTime: 15_000,
  });
}

export function useAdminInvoice(id: string) {
  return useQuery({
    queryKey: ["admin-invoice", id],
    queryFn: () => adminApi.getInvoice(id),
    enabled: !!id,
  });
}

export function useAdminMetrics(params?: {
  period?: "day" | "week" | "month" | "quarter";
  from_date?: string;
  to_date?: string;
}) {
  return useQuery({
    queryKey: ["admin-metrics", params],
    queryFn: () => adminApi.getMetrics(params),
    staleTime: 60_000,
  });
}

export function useAdminReview(invoiceId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (review: {
      action: AdminAction;
      overridden_fields?: FieldCorrection[];
      notes?: string;
    }) => adminApi.submitReview(invoiceId, review),
    onSuccess: (_, variables) => {
      const actionLabels: Record<AdminAction, string> = {
        approve: "Invoice approved",
        override: "Invoice overridden and saved",
        reject: "Invoice rejected",
        flag_for_review: "Invoice flagged for further review",
      };
      toast.success(actionLabels[variables.action]);
      queryClient.invalidateQueries({
        queryKey: ["admin-invoice", invoiceId],
      });
      queryClient.invalidateQueries({ queryKey: ["admin-invoices"] });
      queryClient.invalidateQueries({ queryKey: ["admin-metrics"] });
    },
    onError: () => {
      toast.error("Failed to submit review. Please try again.");
    },
  });
}

export function useExportInvoices() {
  return useMutation({
    mutationFn: (request: ExportRequest) => adminApi.exportInvoices(request),
    onSuccess: (data) => {
      toast.success(
        `Export ready: ${data.data.record_count} records exported`,
      );
    },
    onError: () => {
      toast.error("Export failed. Please try again.");
    },
  });
}
