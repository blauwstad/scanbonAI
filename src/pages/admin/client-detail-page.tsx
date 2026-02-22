import { useState, useEffect, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { Header } from "@/components/layout/header";
import { adminApi } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { FullPageSpinner } from "@/components/ui/spinner";
import {
  ArrowLeft,
  CreditCard,
  Building2,
  Search,
  FileText,
  Loader2,
  CheckCircle2,
  XCircle,
  AlertCircle,
} from "lucide-react";
import { formatDate, cn } from "@/lib/utils";
import type { ClientDetail } from "@/types";

type ActiveTab = "billing" | "company" | "enrichment" | "invoices";

function getStatusBadge(status: string) {
  switch (status) {
    case "active":
      return <Badge variant="success">Active</Badge>;
    case "pending":
      return <Badge variant="warning">Pending</Badge>;
    case "inactive":
      return <Badge variant="secondary">Inactive</Badge>;
    case "suspended":
      return <Badge variant="destructive">Suspended</Badge>;
    default:
      return <Badge variant="outline">{status}</Badge>;
  }
}

// ─────────────────────────────────────────────────────────────
// Billing Tab
// ─────────────────────────────────────────────────────────────

function BillingSection({
  client,
  onRefresh,
}: {
  client: ClientDetail;
  onRefresh: () => void;
}) {
  const [grantAmount, setGrantAmount] = useState("");
  const [grantReason, setGrantReason] = useState("");
  const [granting, setGranting] = useState(false);
  const [suspending, setSuspending] = useState(false);

  const handleGrantCredits = async () => {
    const amount = parseInt(grantAmount, 10);
    if (!amount || amount <= 0) return;

    setGranting(true);
    try {
      await adminApi.grantCredits(
        client.id,
        amount,
        grantReason.trim() || undefined,
      );
      setGrantAmount("");
      setGrantReason("");
      onRefresh();
    } catch {
      // Error handled by interceptor
    } finally {
      setGranting(false);
    }
  };

  const handleSuspendToggle = async () => {
    setSuspending(true);
    try {
      if (client.status === "suspended") {
        await adminApi.reactivateClient(client.id);
      } else {
        await adminApi.suspendClient(client.id);
      }
      onRefresh();
    } catch {
      // Error handled by interceptor
    } finally {
      setSuspending(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Plan overview */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Subscription Details</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <div>
              <p className="text-xs text-muted-foreground">Plan</p>
              <p className="mt-1 font-medium">
                {client.plan_code ?? "No plan"}
              </p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">
                Subscription Status
              </p>
              <p className="mt-1 font-medium">
                {client.subscription_status ?? "-"}
              </p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Credits Remaining</p>
              <p className="mt-1 font-medium">
                {client.remaining_credits != null
                  ? client.remaining_credits
                  : "-"}
              </p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Period End</p>
              <p className="mt-1 font-medium">
                {client.current_period_end
                  ? formatDate(client.current_period_end)
                  : "-"}
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Grant credits */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Grant Credits</CardTitle>
          <CardDescription>
            Manually add credits to this client's balance
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-end gap-3">
            <div className="space-y-1">
              <Label htmlFor="grant-amount">Amount</Label>
              <Input
                id="grant-amount"
                type="number"
                min="1"
                placeholder="e.g. 50"
                value={grantAmount}
                onChange={(e) => setGrantAmount(e.target.value)}
                className="w-32"
              />
            </div>
            <div className="flex-1 min-w-[200px] space-y-1">
              <Label htmlFor="grant-reason">Reason (optional)</Label>
              <Input
                id="grant-reason"
                placeholder="Compensation, bonus, etc."
                value={grantReason}
                onChange={(e) => setGrantReason(e.target.value)}
              />
            </div>
            <Button
              onClick={handleGrantCredits}
              disabled={!grantAmount || parseInt(grantAmount, 10) <= 0 || granting}
            >
              {granting ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <CreditCard className="mr-2 h-4 w-4" />
              )}
              Grant Credits
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Suspend / Reactivate */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Account Actions</CardTitle>
        </CardHeader>
        <CardContent>
          <Button
            variant={client.status === "suspended" ? "default" : "destructive"}
            onClick={handleSuspendToggle}
            disabled={suspending}
          >
            {suspending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {client.status === "suspended" ? "Reactivate Client" : "Suspend Client"}
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Company Profile Tab
// ─────────────────────────────────────────────────────────────

function CompanySection({
  client,
  onRefresh,
}: {
  client: ClientDetail;
  onRefresh: () => void;
}) {
  const [form, setForm] = useState({
    company_name: client.company_name ?? "",
    legal_name: client.legal_name ?? "",
    contact_name: client.contact_name ?? "",
    address_street: client.address_street ?? "",
    address_postal_code: client.address_postal_code ?? "",
    address_city: client.address_city ?? "",
    address_country: client.address_country ?? "",
    vat_number: client.vat_number ?? "",
    kvk_number: client.kvk_number ?? "",
    kbo_number: client.kbo_number ?? "",
    admin_notes: client.admin_notes ?? "",
  });
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  // Reset form when client changes
  useEffect(() => {
    setForm({
      company_name: client.company_name ?? "",
      legal_name: client.legal_name ?? "",
      contact_name: client.contact_name ?? "",
      address_street: client.address_street ?? "",
      address_postal_code: client.address_postal_code ?? "",
      address_city: client.address_city ?? "",
      address_country: client.address_country ?? "",
      vat_number: client.vat_number ?? "",
      kvk_number: client.kvk_number ?? "",
      kbo_number: client.kbo_number ?? "",
      admin_notes: client.admin_notes ?? "",
    });
  }, [client]);

  const updateField = (field: string, value: string) => {
    setForm((prev) => ({ ...prev, [field]: value }));
    setSaved(false);
  };

  const handleSave = async () => {
    setSaving(true);
    setSaved(false);
    try {
      // Send only non-empty values, null for cleared fields
      const payload: Record<string, string | null> = {};
      for (const [key, value] of Object.entries(form)) {
        payload[key] = value.trim() || null;
      }
      await adminApi.updateClient(client.id, payload);
      setSaved(true);
      onRefresh();
    } catch {
      // Error handled by interceptor
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Company Profile</CardTitle>
        <CardDescription>
          Edit client company details and admin notes
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor="company_name">Company Name</Label>
            <Input
              id="company_name"
              value={form.company_name}
              onChange={(e) => updateField("company_name", e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="legal_name">Legal Name</Label>
            <Input
              id="legal_name"
              value={form.legal_name}
              onChange={(e) => updateField("legal_name", e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="contact_name">Contact Name</Label>
            <Input
              id="contact_name"
              value={form.contact_name}
              onChange={(e) => updateField("contact_name", e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="vat_number">VAT Number</Label>
            <Input
              id="vat_number"
              value={form.vat_number}
              onChange={(e) => updateField("vat_number", e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="kvk_number">KVK Number</Label>
            <Input
              id="kvk_number"
              value={form.kvk_number}
              onChange={(e) => updateField("kvk_number", e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="kbo_number">KBO Number</Label>
            <Input
              id="kbo_number"
              value={form.kbo_number}
              onChange={(e) => updateField("kbo_number", e.target.value)}
            />
          </div>

          <div className="sm:col-span-2">
            <p className="mb-2 text-sm font-medium">Address</p>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="sm:col-span-2 space-y-1">
                <Label htmlFor="address_street">Street</Label>
                <Input
                  id="address_street"
                  value={form.address_street}
                  onChange={(e) =>
                    updateField("address_street", e.target.value)
                  }
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="address_postal_code">Postal Code</Label>
                <Input
                  id="address_postal_code"
                  value={form.address_postal_code}
                  onChange={(e) =>
                    updateField("address_postal_code", e.target.value)
                  }
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="address_city">City</Label>
                <Input
                  id="address_city"
                  value={form.address_city}
                  onChange={(e) => updateField("address_city", e.target.value)}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="address_country">Country</Label>
                <Input
                  id="address_country"
                  value={form.address_country}
                  onChange={(e) =>
                    updateField("address_country", e.target.value)
                  }
                />
              </div>
            </div>
          </div>

          <div className="sm:col-span-2 space-y-1">
            <Label htmlFor="admin_notes">Admin Notes</Label>
            <Textarea
              id="admin_notes"
              rows={3}
              value={form.admin_notes}
              onChange={(e) => updateField("admin_notes", e.target.value)}
              placeholder="Internal notes about this client..."
            />
          </div>
        </div>

        <div className="mt-4 flex items-center gap-3">
          <Button onClick={handleSave} disabled={saving}>
            {saving ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Building2 className="mr-2 h-4 w-4" />
            )}
            Save Changes
          </Button>
          {saved && (
            <span className="flex items-center gap-1 text-sm text-green-600">
              <CheckCircle2 className="h-4 w-4" />
              Saved
            </span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// ─────────────────────────────────────────────────────────────
// Enrichment Tab
// ─────────────────────────────────────────────────────────────

function EnrichmentSection({
  client,
  onRefresh,
}: {
  client: ClientDetail;
  onRefresh: () => void;
}) {
  const [enriching, setEnriching] = useState(false);
  const [togglingConsent, setTogglingConsent] = useState(false);

  const registryType = client.kbo_number ? "KBO" : client.kvk_number ? "KVK" : null;

  const handleEnrich = async () => {
    if (!registryType) return;
    setEnriching(true);
    try {
      await adminApi.enrichClient(client.id, registryType);
      onRefresh();
    } catch {
      // Error handled by interceptor
    } finally {
      setEnriching(false);
    }
  };

  const handleToggleConsent = async () => {
    setTogglingConsent(true);
    try {
      await adminApi.setClientConsent(
        client.id,
        !client.consent_registry_enrichment,
      );
      onRefresh();
    } catch {
      // Error handled by interceptor
    } finally {
      setTogglingConsent(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Consent + Actions */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Registry Enrichment</CardTitle>
          <CardDescription>
            Look up client data from KVK (Netherlands) or KBO (Belgium) registry
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between rounded-lg border p-3">
            <div>
              <p className="text-sm font-medium">Consent for Registry Lookup</p>
              <p className="text-xs text-muted-foreground">
                Client has{" "}
                {client.consent_registry_enrichment
                  ? "given consent"
                  : "not given consent"}{" "}
                to registry enrichment
              </p>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={handleToggleConsent}
              disabled={togglingConsent}
            >
              {togglingConsent && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              )}
              {client.consent_registry_enrichment ? "Revoke" : "Grant"} Consent
            </Button>
          </div>

          <div className="flex items-center gap-3">
            <Button
              onClick={handleEnrich}
              disabled={!registryType || enriching}
            >
              {enriching ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Search className="mr-2 h-4 w-4" />
              )}
              {registryType
                ? `Enrich from ${registryType}`
                : "No registry number set"}
            </Button>
            {client.enrichment_last_fetched_at && (
              <span className="text-xs text-muted-foreground">
                Last enriched: {formatDate(client.enrichment_last_fetched_at)}
              </span>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Enrichment history */}
      {client.enrichment_history.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Enrichment History</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Registry</TableHead>
                  <TableHead>Identifier</TableHead>
                  <TableHead>Date</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Applied Fields</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {client.enrichment_history.map((entry) => (
                  <TableRow key={entry.id}>
                    <TableCell>
                      <Badge variant="outline">{entry.registry_type}</Badge>
                    </TableCell>
                    <TableCell className="font-mono text-sm">
                      {entry.identifier}
                    </TableCell>
                    <TableCell>
                      {entry.requested_at
                        ? formatDate(entry.requested_at)
                        : "-"}
                    </TableCell>
                    <TableCell>
                      {entry.success ? (
                        <span className="flex items-center gap-1 text-green-600">
                          <CheckCircle2 className="h-3.5 w-3.5" />
                          Success
                        </span>
                      ) : (
                        <span className="flex items-center gap-1 text-destructive">
                          <XCircle className="h-3.5 w-3.5" />
                          {entry.error_message ?? "Failed"}
                        </span>
                      )}
                    </TableCell>
                    <TableCell>
                      {entry.applied_fields
                        ? Object.keys(entry.applied_fields).join(", ")
                        : "-"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Invoices Tab
// ─────────────────────────────────────────────────────────────

function InvoicesSection({ client }: { client: ClientDetail }) {
  if (client.recent_invoices.length === 0) {
    return (
      <Card>
        <CardContent className="py-8 text-center text-muted-foreground">
          No invoices yet.
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Recent Invoices</CardTitle>
        <CardDescription>Last {client.recent_invoices.length} invoices</CardDescription>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>ID</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Month</TableHead>
              <TableHead>Created</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {client.recent_invoices.map((inv) => (
              <TableRow key={inv.id}>
                <TableCell className="font-mono text-sm">
                  {inv.id.slice(0, 8)}...
                </TableCell>
                <TableCell>
                  <Badge variant="outline">{inv.status}</Badge>
                </TableCell>
                <TableCell>{inv.month_partition}</TableCell>
                <TableCell>
                  {inv.created_at ? formatDate(inv.created_at) : "-"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

// ─────────────────────────────────────────────────────────────
// Main Client Detail Page
// ─────────────────────────────────────────────────────────────

const TABS: { key: ActiveTab; label: string; icon: React.ElementType }[] = [
  { key: "billing", label: "Billing", icon: CreditCard },
  { key: "company", label: "Company Profile", icon: Building2 },
  { key: "enrichment", label: "Registry Enrichment", icon: Search },
  { key: "invoices", label: "Recent Invoices", icon: FileText },
];

export function ClientDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [client, setClient] = useState<ClientDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<ActiveTab>("billing");

  const fetchClient = useCallback(async () => {
    if (!id) return;
    try {
      const res = await adminApi.getClient(id);
      setClient(res.data);
      setError(null);
    } catch (err: any) {
      setError(err.message ?? "Failed to load client");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    fetchClient();
  }, [fetchClient]);

  if (loading) return <FullPageSpinner />;

  if (error || !client) {
    return (
      <div>
        <Header title="Client Detail" />
        <div className="flex flex-col items-center gap-3 p-12">
          <AlertCircle className="h-8 w-8 text-destructive" />
          <p className="text-destructive">{error ?? "Client not found"}</p>
          <Button variant="outline" onClick={() => navigate("/admin/clients")}>
            <ArrowLeft className="mr-2 h-4 w-4" />
            Back to Clients
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div>
      <Header
        title="Client Detail"
        actions={
          <Button
            variant="ghost"
            size="sm"
            onClick={() => navigate("/admin/clients")}
          >
            <ArrowLeft className="mr-1 h-4 w-4" />
            Back
          </Button>
        }
      />

      <div className="p-6 space-y-6">
        {/* Client header */}
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-xl font-semibold">
            {client.display_name ?? client.whatsapp_phone}
          </h2>
          <span className="font-mono text-sm text-muted-foreground">
            {client.whatsapp_phone}
          </span>
          {getStatusBadge(client.status)}
        </div>

        {/* Tab navigation */}
        <div className="flex gap-1 border-b">
          {TABS.map((tab) => (
            <button
              key={tab.key}
              type="button"
              onClick={() => setActiveTab(tab.key)}
              className={cn(
                "flex items-center gap-2 border-b-2 px-4 py-2 text-sm font-medium transition-colors",
                activeTab === tab.key
                  ? "border-primary text-primary"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              <tab.icon className="h-4 w-4" />
              {tab.label}
            </button>
          ))}
        </div>

        {/* Tab content */}
        {activeTab === "billing" && (
          <BillingSection client={client} onRefresh={fetchClient} />
        )}
        {activeTab === "company" && (
          <CompanySection client={client} onRefresh={fetchClient} />
        )}
        {activeTab === "enrichment" && (
          <EnrichmentSection client={client} onRefresh={fetchClient} />
        )}
        {activeTab === "invoices" && <InvoicesSection client={client} />}
      </div>
    </div>
  );
}
