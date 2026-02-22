import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  FileText,
  Clock,
  CheckCircle,
  AlertCircle,
  TrendingUp,
  Target,
} from "lucide-react";
import { Spinner } from "@/components/ui/spinner";

// ─────────────────────────────────────────────────────────────
// Types – matches the backend MetricsResponse
// ─────────────────────────────────────────────────────────────

export interface BackendMetrics {
  total_invoices: number;
  invoices_by_status: Record<string, number>;
  avg_confidence: number | null;
  corrections_submitted: number;
  approvals: number;
  rejections: number;
  period_start: string | null;
  period_end: string | null;
}

interface AdminMetricsViewProps {
  metrics: BackendMetrics | undefined;
  isLoading: boolean;
}

// ─────────────────────────────────────────────────────────────
// Summary Card helper
// ─────────────────────────────────────────────────────────────

interface SummaryCardProps {
  title: string;
  value: string | number;
  description: string;
  icon: React.ElementType;
}

function SummaryCard({
  title,
  value,
  description,
  icon: Icon,
}: SummaryCardProps) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        <Icon className="h-4 w-4 text-muted-foreground" />
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{value}</div>
        <p className="text-xs text-muted-foreground">{description}</p>
      </CardContent>
    </Card>
  );
}

// ─────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────

export function AdminMetricsView({
  metrics,
  isLoading,
}: AdminMetricsViewProps) {
  if (isLoading || !metrics) {
    return (
      <div className="flex items-center justify-center py-16">
        <Spinner size="lg" />
      </div>
    );
  }

  const total = metrics.total_invoices;
  const byStatus = metrics.invoices_by_status ?? {};
  const pending =
    (byStatus["extracted"] ?? 0) + (byStatus["reviewed"] ?? 0);
  const approved = metrics.approvals;
  const qualityFailed = byStatus["quality_failed"] ?? 0;
  const avgConf = metrics.avg_confidence ?? 0;
  const corrections = metrics.corrections_submitted;

  // Build a simple status breakdown for the bar chart
  const statusData = Object.entries(byStatus).map(([status, count]) => ({
    status: status.replace(/_/g, " "),
    count,
  }));

  return (
    <div className="space-y-6">
      {/* Summary Cards */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <SummaryCard
          title="Total Invoices"
          value={total.toLocaleString()}
          description="All time processed"
          icon={FileText}
        />
        <SummaryCard
          title="Pending Review"
          value={pending}
          description="Awaiting admin action"
          icon={Clock}
        />
        <SummaryCard
          title="Approved"
          value={approved}
          description="Successfully processed"
          icon={CheckCircle}
        />
        <SummaryCard
          title="Quality Failed"
          value={qualityFailed}
          description="Unreadable images"
          icon={AlertCircle}
        />
      </div>

      {/* Additional stats row */}
      <div className="grid gap-4 md:grid-cols-3">
        <SummaryCard
          title="Avg. Confidence"
          value={avgConf ? `${(avgConf * 100).toFixed(1)}%` : "N/A"}
          description="Mean extraction confidence"
          icon={TrendingUp}
        />
        <SummaryCard
          title="Corrections"
          value={corrections}
          description="User-submitted corrections"
          icon={Target}
        />
        <SummaryCard
          title="Rejections"
          value={metrics.rejections}
          description="Rejected by admin"
          icon={AlertCircle}
        />
      </div>

      {/* Status breakdown chart */}
      {statusData.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Invoices by Status</CardTitle>
            <CardDescription>
              Distribution of invoice processing statuses
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="h-[300px]">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={statusData}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="status" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} />
                  <Tooltip />
                  <Bar
                    dataKey="count"
                    fill="#6366f1"
                    radius={[4, 4, 0, 0]}
                    name="Invoices"
                  />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>
      )}

      {total === 0 && (
        <Card>
          <CardContent className="py-12 text-center text-muted-foreground">
            No invoices yet. Invoices will appear here once users start
            submitting them via WhatsApp.
          </CardContent>
        </Card>
      )}
    </div>
  );
}
