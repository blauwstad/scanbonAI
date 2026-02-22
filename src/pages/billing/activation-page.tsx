import { useState, useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import axios from "axios";
import { Receipt, Loader2, AlertCircle, CreditCard, Infinity } from "lucide-react";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { ActivationInfo, BillingPlan } from "@/types";

// Standalone axios instance without auth headers
const publicClient = axios.create({
  baseURL: "",
  headers: { "Content-Type": "application/json" },
  timeout: 30_000,
});

function PlanCard({
  plan,
  selected,
  onSelect,
}: {
  plan: BillingPlan;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        "w-full rounded-lg border-2 p-4 text-left transition-all",
        selected
          ? "border-primary bg-primary/5 ring-1 ring-primary"
          : "border-border hover:border-primary/50",
      )}
    >
      <div className="flex items-center justify-between">
        <div>
          <h3 className="font-semibold">{plan.name}</h3>
          <p className="mt-1 text-sm text-muted-foreground">
            {plan.plan_type === "unlimited" ? (
              <span className="flex items-center gap-1">
                <Infinity className="h-3.5 w-3.5" />
                Unlimited invoices
              </span>
            ) : (
              <span className="flex items-center gap-1">
                <CreditCard className="h-3.5 w-3.5" />
                {plan.credits_amount} credits
              </span>
            )}
          </p>
        </div>
        {selected && (
          <Badge variant="default">Selected</Badge>
        )}
      </div>
    </button>
  );
}

export function ActivationPage() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token");
  const canceled = searchParams.get("canceled") === "true";

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activationInfo, setActivationInfo] = useState<ActivationInfo | null>(null);
  const [selectedPlan, setSelectedPlan] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!token) {
      setError("Missing activation token. Please use the link sent to your WhatsApp.");
      setLoading(false);
      return;
    }

    publicClient
      .get("/api/v1/billing/activate", { params: { token } })
      .then((res) => {
        const info = res.data.data as ActivationInfo;
        setActivationInfo(info);
        // Auto-select first plan if only one is available
        if (info.plans.length === 1) {
          setSelectedPlan(info.plans[0].code);
        }
      })
      .catch((err) => {
        const message =
          err.response?.data?.detail ??
          err.response?.data?.message ??
          "Invalid or expired activation link. Please request a new one from WhatsApp.";
        setError(message);
      })
      .finally(() => setLoading(false));
  }, [token]);

  const handleActivate = async () => {
    if (!token || !selectedPlan) return;

    setSubmitting(true);
    setError(null);

    try {
      const res = await publicClient.post("/api/v1/billing/checkout-session", {
        token,
        plan_code: selectedPlan,
      });
      const { checkout_url } = res.data.data;
      window.location.href = checkout_url;
    } catch (err: any) {
      const message =
        err.response?.data?.detail ??
        err.response?.data?.message ??
        "Failed to create checkout session. Please try again.";
      setError(message);
      setSubmitting(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/30 p-4">
      <div className="w-full max-w-md">
        {/* Brand header */}
        <div className="mb-6 flex items-center justify-center gap-2">
          <Receipt className="h-8 w-8 text-primary" />
          <span className="text-2xl font-bold">ScanbonAI</span>
        </div>

        {/* Canceled message */}
        {canceled && (
          <div className="mb-4 flex items-center gap-2 rounded-lg border border-yellow-200 bg-yellow-50 px-4 py-3 text-sm text-yellow-800">
            <AlertCircle className="h-4 w-4 flex-shrink-0" />
            Payment was canceled. You can select a plan and try again.
          </div>
        )}

        <Card>
          <CardHeader className="text-center">
            <CardTitle className="text-xl">Activate Your Account</CardTitle>
            <CardDescription>
              Choose a plan to start processing invoices with ScanbonAI
            </CardDescription>
          </CardHeader>

          <CardContent>
            {loading ? (
              <div className="flex flex-col items-center gap-3 py-8">
                <Loader2 className="h-8 w-8 animate-spin text-primary" />
                <p className="text-sm text-muted-foreground">
                  Validating your activation link...
                </p>
              </div>
            ) : error && !activationInfo ? (
              <div className="flex flex-col items-center gap-3 py-8 text-center">
                <AlertCircle className="h-8 w-8 text-destructive" />
                <p className="text-sm text-destructive">{error}</p>
              </div>
            ) : activationInfo ? (
              <div className="space-y-4">
                {/* User info */}
                <div className="rounded-lg bg-muted/50 p-3 text-center">
                  {activationInfo.user_name && (
                    <p className="font-medium">{activationInfo.user_name}</p>
                  )}
                  <p className="text-sm text-muted-foreground">
                    {activationInfo.phone_masked}
                  </p>
                </div>

                {/* Plan selection */}
                <div className="space-y-2">
                  <p className="text-sm font-medium">Select a plan</p>
                  {activationInfo.plans.map((plan) => (
                    <PlanCard
                      key={plan.code}
                      plan={plan}
                      selected={selectedPlan === plan.code}
                      onSelect={() => setSelectedPlan(plan.code)}
                    />
                  ))}
                </div>

                {/* Error during checkout */}
                {error && (
                  <div className="flex items-center gap-2 rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-sm text-destructive">
                    <AlertCircle className="h-4 w-4 flex-shrink-0" />
                    {error}
                  </div>
                )}
              </div>
            ) : null}
          </CardContent>

          {activationInfo && (
            <CardFooter>
              <Button
                className="w-full"
                size="lg"
                disabled={!selectedPlan || submitting}
                onClick={handleActivate}
              >
                {submitting ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Redirecting to payment...
                  </>
                ) : (
                  "Activate & Pay"
                )}
              </Button>
            </CardFooter>
          )}
        </Card>

        <p className="mt-4 text-center text-xs text-muted-foreground">
          Secure payment powered by Stripe. You can manage your subscription at
          any time.
        </p>
      </div>
    </div>
  );
}
