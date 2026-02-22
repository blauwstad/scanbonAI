import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Header } from "@/components/layout/header";
import { AdminMetricsView } from "@/components/admin/admin-metrics";
import { StatusBadge } from "@/components/common/status-badge";
import { ConfidenceBadge } from "@/components/common/confidence-badge";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { formatCurrency, formatDate } from "@/lib/utils";
import { useAdminMetrics, useAdminInvoices } from "@/hooks/use-admin";

type Period = "day" | "week" | "month" | "quarter";

export function AdminDashboardPage() {
  const navigate = useNavigate();
  const [period, setPeriod] = useState<Period>("month");

  const { data: metricsRes, isLoading: metricsLoading } = useAdminMetrics({
    period,
  });
  const { data: recentInvoices, isLoading: invoicesLoading } =
    useAdminInvoices({
      page: 1,
      page_size: 10,
      sort_by: "created_at",
      sort_order: "desc",
    });

  return (
    <div>
      <Header
        title="Admin Dashboard"
        description="Overview of invoice processing and accuracy metrics"
        actions={
          <Select
            value={period}
            onValueChange={(v) => setPeriod(v as Period)}
          >
            <SelectTrigger className="w-[140px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="day">Daily</SelectItem>
              <SelectItem value="week">Weekly</SelectItem>
              <SelectItem value="month">Monthly</SelectItem>
              <SelectItem value="quarter">Quarterly</SelectItem>
            </SelectContent>
          </Select>
        }
      />

      <div className="p-6 space-y-6">
        {/* Metrics */}
        <AdminMetricsView
          metrics={metricsRes?.data}
          isLoading={metricsLoading}
        />

        {/* Recent invoices */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>Recent Invoices</CardTitle>
            <Button
              variant="outline"
              size="sm"
              onClick={() => navigate("/admin/invoices")}
            >
              View All
            </Button>
          </CardHeader>
          <CardContent>
            {invoicesLoading ? (
              <div className="flex justify-center py-8">
                <Spinner />
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Date</TableHead>
                    <TableHead>Supplier</TableHead>
                    <TableHead className="text-right">Amount</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Confidence</TableHead>
                    <TableHead>Corrections</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {recentInvoices?.data.map((inv) => {
                    const meta = inv.extracted_data?.metadata;
                    const conf = inv.extracted_data?.confidence;
                    return (
                      <TableRow
                        key={inv.id}
                        className="cursor-pointer"
                        onClick={() =>
                          navigate(`/admin/invoices/${inv.id}`)
                        }
                      >
                        <TableCell>
                          {formatDate(inv.created_at)}
                        </TableCell>
                        <TableCell>
                          {meta?.supplier_name ?? "---"}
                        </TableCell>
                        <TableCell className="text-right font-medium">
                          {meta
                            ? formatCurrency(
                                meta.total_amount,
                                meta.currency,
                              )
                            : "---"}
                        </TableCell>
                        <TableCell>
                          <StatusBadge status={inv.status} />
                        </TableCell>
                        <TableCell>
                          {conf ? (
                            <ConfidenceBadge
                              score={conf.overall}
                              showLabel={false}
                            />
                          ) : (
                            "---"
                          )}
                        </TableCell>
                        <TableCell>
                          {inv.user_corrections ? (
                            <span className="text-xs text-yellow-600">
                              {inv.user_corrections.field_corrections.length}{" "}
                              field(s)
                            </span>
                          ) : (
                            <span className="text-xs text-muted-foreground">
                              None
                            </span>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
