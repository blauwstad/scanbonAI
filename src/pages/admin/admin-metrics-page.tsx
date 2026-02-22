import { useState } from "react";
import { Header } from "@/components/layout/header";
import { AdminMetricsView } from "@/components/admin/admin-metrics";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useAdminMetrics } from "@/hooks/use-admin";

type Period = "day" | "week" | "month" | "quarter";

export function AdminMetricsPage() {
  const [period, setPeriod] = useState<Period>("month");
  const { data, isLoading } = useAdminMetrics({ period });

  return (
    <div>
      <Header
        title="Metrics"
        description="Detailed analytics on extraction accuracy and processing"
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
      <div className="p-6">
        <AdminMetricsView metrics={data?.data} isLoading={isLoading} />
      </div>
    </div>
  );
}
