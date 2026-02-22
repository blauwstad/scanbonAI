import {
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
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
import type { AdminMetrics } from "@/types";

// ─────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────

interface AdminMetricsViewProps {
  metrics: AdminMetrics | undefined;
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
  trend?: string;
}

function SummaryCard({
  title,
  value,
  description,
  icon: Icon,
  trend,
}: SummaryCardProps) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        <Icon className="h-4 w-4 text-muted-foreground" />
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{value}</div>
        <p className="text-xs text-muted-foreground">
          {description}
          {trend && (
            <span className="ml-1 text-green-600">{trend}</span>
          )}
        </p>
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

  const { summary, accuracy_over_time, field_corrections, processing_volume } =
    metrics;

  return (
    <div className="space-y-6">
      {/* Summary Cards */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <SummaryCard
          title="Total Invoices"
          value={summary.total_invoices.toLocaleString()}
          description="All time processed"
          icon={FileText}
        />
        <SummaryCard
          title="Pending Review"
          value={summary.pending_review}
          description="Awaiting admin action"
          icon={Clock}
        />
        <SummaryCard
          title="Accuracy Rate"
          value={`${(summary.accuracy_rate * 100).toFixed(1)}%`}
          description="AI extraction accuracy"
          icon={Target}
        />
        <SummaryCard
          title="Unreadable Rate"
          value={`${((summary.unreadable / Math.max(summary.total_invoices, 1)) * 100).toFixed(1)}%`}
          description={`${summary.unreadable} unreadable invoices`}
          icon={AlertCircle}
        />
      </div>

      {/* Additional stats row */}
      <div className="grid gap-4 md:grid-cols-3">
        <SummaryCard
          title="Approved"
          value={summary.approved}
          description="Successfully processed"
          icon={CheckCircle}
        />
        <SummaryCard
          title="Avg. Confidence"
          value={`${(summary.average_confidence * 100).toFixed(1)}%`}
          description="Mean extraction confidence"
          icon={TrendingUp}
        />
        <SummaryCard
          title="Avg. Processing Time"
          value={`${(summary.average_processing_time_ms / 1000).toFixed(1)}s`}
          description="End-to-end extraction"
          icon={Clock}
        />
      </div>

      {/* Charts row */}
      <div className="grid gap-6 lg:grid-cols-2">
        {/* Accuracy over time */}
        <Card>
          <CardHeader>
            <CardTitle>Extraction Accuracy Over Time</CardTitle>
            <CardDescription>
              Percentage of fields extracted correctly without user correction
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="h-[300px]">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={accuracy_over_time}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis
                    dataKey="date"
                    tick={{ fontSize: 11 }}
                    tickFormatter={(v) => {
                      const d = new Date(v);
                      return `${d.getMonth() + 1}/${d.getDate()}`;
                    }}
                  />
                  <YAxis
                    domain={[0, 1]}
                    tickFormatter={(v) => `${(v * 100).toFixed(0)}%`}
                    tick={{ fontSize: 11 }}
                  />
                  <Tooltip
                    formatter={(value) => [
                      `${(Number(value) * 100).toFixed(1)}%`,
                      "Accuracy",
                    ]}
                  />
                  <Legend />
                  <Line
                    type="monotone"
                    dataKey="accuracy"
                    stroke="#4f46e5"
                    strokeWidth={2}
                    dot={{ r: 3 }}
                    name="Accuracy"
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>

        {/* Processing volume */}
        <Card>
          <CardHeader>
            <CardTitle>Processing Volume</CardTitle>
            <CardDescription>
              Number of invoices processed per day
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="h-[300px]">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={processing_volume}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis
                    dataKey="date"
                    tick={{ fontSize: 11 }}
                    tickFormatter={(v) => {
                      const d = new Date(v);
                      return `${d.getMonth() + 1}/${d.getDate()}`;
                    }}
                  />
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
      </div>

      {/* Field corrections bar chart (full width) */}
      <Card>
        <CardHeader>
          <CardTitle>Most Corrected Fields</CardTitle>
          <CardDescription>
            Which fields get corrected by users most frequently
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="h-[300px]">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={field_corrections}
                layout="vertical"
                margin={{ left: 100 }}
              >
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis
                  type="number"
                  tickFormatter={(v) => `${(v * 100).toFixed(0)}%`}
                  domain={[0, 1]}
                  tick={{ fontSize: 11 }}
                />
                <YAxis
                  type="category"
                  dataKey="field_name"
                  tick={{ fontSize: 11 }}
                  width={90}
                />
                <Tooltip
                  formatter={(value) => [
                    `${(Number(value) * 100).toFixed(1)}%`,
                    "Correction Rate",
                  ]}
                />
                <Bar
                  dataKey="correction_rate"
                  fill="#f59e0b"
                  radius={[0, 4, 4, 0]}
                  name="Correction Rate"
                />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
