import { useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { Receipt, User, Shield } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useAuthStore } from "@/stores/auth-store";
import apiClient from "@/api/client";
import toast from "react-hot-toast";

export function LoginPage() {
  const [isSubmitting, setIsSubmitting] = useState("");
  const navigate = useNavigate();
  const { setAuth } = useAuthStore();

  const handleDemoLogin = useCallback(
    async (role: "user" | "admin") => {
      setIsSubmitting(role);
      try {
        const { data: res } = await apiClient.post("/auth/demo-login", { role });
        const { user, access_token, refresh_token } = res.data;
        localStorage.setItem("refresh_token", refresh_token);
        setAuth(user, access_token);
        toast.success(`Welcome, ${user.name}!`);
        navigate(role === "admin" ? "/admin" : "/invoices", { replace: true });
      } catch {
        toast.error("Demo login failed. Is the API running?");
      } finally {
        setIsSubmitting("");
      }
    },
    [setAuth, navigate],
  );

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/30 px-4">
      <div className="w-full max-w-md">
        {/* Brand */}
        <div className="mb-8 text-center">
          <div className="flex items-center justify-center gap-2 mb-2">
            <Receipt className="h-8 w-8 text-primary" />
            <h1 className="text-2xl font-bold">ScanbonAI</h1>
          </div>
          <p className="text-muted-foreground">
            Tax administration via WhatsApp invoices
          </p>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Demo Login</CardTitle>
            <CardDescription>
              Choose a role to explore the application
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <Button
              className="w-full justify-start gap-3 h-auto py-4"
              variant="outline"
              disabled={!!isSubmitting}
              onClick={() => handleDemoLogin("user")}
            >
              <User className="h-5 w-5 text-blue-500 shrink-0" />
              <div className="text-left">
                <div className="font-medium">
                  {isSubmitting === "user" ? "Logging in..." : "Jan de Vries (User)"}
                </div>
                <div className="text-xs text-muted-foreground">
                  View invoices, submit corrections, confirm extractions
                </div>
              </div>
            </Button>

            <Button
              className="w-full justify-start gap-3 h-auto py-4"
              variant="outline"
              disabled={!!isSubmitting}
              onClick={() => handleDemoLogin("admin")}
            >
              <Shield className="h-5 w-5 text-amber-500 shrink-0" />
              <div className="text-left">
                <div className="font-medium">
                  {isSubmitting === "admin" ? "Logging in..." : "Admin Demo (Admin)"}
                </div>
                <div className="text-xs text-muted-foreground">
                  Review all invoices, approve/reject, view metrics, export
                </div>
              </div>
            </Button>
          </CardContent>
        </Card>

        <p className="mt-6 text-center text-xs text-muted-foreground">
          Demo environment with 6 sample invoices from Dutch suppliers.
        </p>
      </div>
    </div>
  );
}
