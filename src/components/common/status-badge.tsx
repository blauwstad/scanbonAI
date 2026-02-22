import { Badge, type BadgeProps } from "@/components/ui/badge";
import type { InvoiceStatus } from "@/types";

const statusConfig: Record<
  InvoiceStatus,
  { label: string; variant: BadgeProps["variant"] }
> = {
  uploaded: { label: "Uploaded", variant: "secondary" },
  processing: { label: "Processing", variant: "info" },
  extracted: { label: "Extracted", variant: "info" },
  user_review: { label: "Needs Review", variant: "warning" },
  user_confirmed: { label: "User Confirmed", variant: "success" },
  admin_review: { label: "Admin Review", variant: "warning" },
  approved: { label: "Approved", variant: "success" },
  rejected: { label: "Rejected", variant: "destructive" },
  unreadable: { label: "Unreadable", variant: "destructive" },
  expert_review: { label: "Expert Review", variant: "outline" },
};

interface StatusBadgeProps {
  status: InvoiceStatus;
  className?: string;
}

export function StatusBadge({ status, className }: StatusBadgeProps) {
  const config = statusConfig[status];
  return (
    <Badge variant={config.variant} className={className}>
      {config.label}
    </Badge>
  );
}
