import { useState, useCallback } from "react";
import { Download, FileSpreadsheet, FileText, Code } from "lucide-react";
import { Header } from "@/components/layout/header";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useExportInvoices } from "@/hooks/use-admin";
import type { ExportRequest } from "@/types";
import { format } from "date-fns";

const FORMAT_OPTIONS = [
  {
    value: "csv" as const,
    label: "CSV",
    description: "Comma-separated values, compatible with Excel",
    icon: FileSpreadsheet,
  },
  {
    value: "datev" as const,
    label: "DATEV",
    description: "German tax accounting standard format",
    icon: FileText,
  },
  {
    value: "xml" as const,
    label: "XML",
    description: "Structured XML for system integration",
    icon: Code,
  },
];

export function AdminExportPage() {
  const currentMonth = format(new Date(), "yyyy-MM");
  const [exportFormat, setExportFormat] = useState<ExportRequest["format"]>("csv");
  const [month, setMonth] = useState(currentMonth);
  const [includeLineItems, setIncludeLineItems] = useState(true);

  const exportMutation = useExportInvoices();

  const handleExport = useCallback(() => {
    exportMutation.mutate({
      format: exportFormat,
      month,
      status_filter: ["approved"],
      include_line_items: includeLineItems,
    });
  }, [exportFormat, month, includeLineItems, exportMutation]);

  return (
    <div>
      <Header
        title="Export"
        description="Export approved invoices for tax accounting"
      />

      <div className="p-6 max-w-2xl space-y-6">
        {/* Format selection */}
        <Card>
          <CardHeader>
            <CardTitle>Export Format</CardTitle>
            <CardDescription>
              Choose the file format for your export
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              {FORMAT_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => setExportFormat(opt.value)}
                  className={`flex flex-col items-center gap-2 rounded-lg border-2 p-4 text-center transition-colors ${
                    exportFormat === opt.value
                      ? "border-primary bg-primary/5"
                      : "border-border hover:border-primary/50"
                  }`}
                >
                  <opt.icon className="h-6 w-6" />
                  <span className="font-medium text-sm">{opt.label}</span>
                  <span className="text-xs text-muted-foreground">
                    {opt.description}
                  </span>
                </button>
              ))}
            </div>
          </CardContent>
        </Card>

        {/* Options */}
        <Card>
          <CardHeader>
            <CardTitle>Options</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="export-month">Month</Label>
              <Input
                id="export-month"
                type="month"
                value={month}
                onChange={(e) => setMonth(e.target.value)}
                className="w-[200px]"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="export-status">Status Filter</Label>
              <Select defaultValue="approved">
                <SelectTrigger className="w-[200px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="approved">Approved Only</SelectItem>
                  <SelectItem value="all">All Statuses</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={includeLineItems}
                onChange={(e) => setIncludeLineItems(e.target.checked)}
                className="rounded border-input"
              />
              Include line items in export
            </label>
          </CardContent>
        </Card>

        {/* Export button */}
        <Button
          size="lg"
          onClick={handleExport}
          disabled={exportMutation.isPending}
          className="w-full"
        >
          <Download className="h-4 w-4 mr-2" />
          {exportMutation.isPending
            ? "Generating export..."
            : `Export as ${exportFormat.toUpperCase()}`}
        </Button>

        {/* Download link after export */}
        {exportMutation.data && (
          <Card>
            <CardContent className="pt-6">
              <div className="flex items-center justify-between">
                <div>
                  <p className="font-medium">Export Ready</p>
                  <p className="text-sm text-muted-foreground">
                    {exportMutation.data.data.record_count} records exported on{" "}
                    {new Date(
                      exportMutation.data.data.generated_at,
                    ).toLocaleString()}
                  </p>
                </div>
                <Button
                  variant="outline"
                  asChild
                >
                  <a
                    href={exportMutation.data.data.download_url}
                    download
                  >
                    <Download className="h-4 w-4 mr-1" />
                    Download
                  </a>
                </Button>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
