import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { invoiceApi } from "@/api/client";
import type {
  InvoiceFilters,
  InvoiceMetadata,
  FieldCorrection,
} from "@/types";
import toast from "react-hot-toast";

export function useInvoices(filters: InvoiceFilters = {}) {
  return useQuery({
    queryKey: ["invoices", filters],
    queryFn: () => invoiceApi.list(filters),
    staleTime: 30_000,
  });
}

export function useInvoice(id: string) {
  return useQuery({
    queryKey: ["invoice", id],
    queryFn: () => invoiceApi.getById(id),
    enabled: !!id,
  });
}

export function useInvoiceImageUrl(id: string) {
  return useQuery({
    queryKey: ["invoice-image", id],
    queryFn: () => invoiceApi.getImageUrl(id),
    enabled: !!id,
    staleTime: 5 * 60 * 1000, // signed URLs valid for ~15 min
  });
}

export function useSubmitCorrections(invoiceId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: {
      corrected_metadata: Partial<InvoiceMetadata>;
      field_corrections: FieldCorrection[];
    }) => invoiceApi.submitCorrections(invoiceId, data),
    onSuccess: () => {
      toast.success("Corrections submitted successfully");
      queryClient.invalidateQueries({ queryKey: ["invoice", invoiceId] });
      queryClient.invalidateQueries({ queryKey: ["invoices"] });
    },
    onError: () => {
      toast.error("Failed to submit corrections. Please try again.");
    },
  });
}

export function useConfirmInvoice(invoiceId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: () => invoiceApi.confirmAsCorrect(invoiceId),
    onSuccess: () => {
      toast.success("Invoice confirmed as correct");
      queryClient.invalidateQueries({ queryKey: ["invoice", invoiceId] });
      queryClient.invalidateQueries({ queryKey: ["invoices"] });
    },
    onError: () => {
      toast.error("Failed to confirm invoice. Please try again.");
    },
  });
}
