import { useState, useCallback } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Receipt, User, Lock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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

export function RegisterWhatsAppPage() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token");

  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const navigate = useNavigate();
  const { setAuth } = useAuthStore();

  const handleRegister = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!token || !name || !password || !confirmPassword) return;

      if (password !== confirmPassword) {
        toast.error("Passwords do not match.");
        return;
      }

      setIsSubmitting(true);
      try {
        const { data: res } = await apiClient.post(
          "/auth/register/whatsapp",
          { token, name, password },
        );
        const { user, access_token, refresh_token } = res.data;
        localStorage.setItem("refresh_token", refresh_token);
        setAuth(user, access_token);
        toast.success(`Account created! Welcome, ${user.display_name ?? user.name}!`);
        navigate("/invoices", { replace: true });
      } catch (err: any) {
        const msg = err?.message || "Registration failed. Please try again.";
        toast.error(msg);
      } finally {
        setIsSubmitting(false);
      }
    },
    [token, name, password, confirmPassword, setAuth, navigate],
  );

  // No token in URL -- invalid link
  if (!token) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-muted/30 px-4">
        <div className="w-full max-w-md">
          <div className="mb-8 text-center">
            <div className="flex items-center justify-center gap-2 mb-2">
              <Receipt className="h-8 w-8 text-primary" />
              <h1 className="text-2xl font-bold">ScanbonAI</h1>
            </div>
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Invalid Link</CardTitle>
              <CardDescription>
                Invalid registration link. Please use the link sent to your
                WhatsApp.
              </CardDescription>
            </CardHeader>
          </Card>
        </div>
      </div>
    );
  }

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
            Complete your registration
          </p>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Create Your Account</CardTitle>
            <CardDescription>
              You were invited via WhatsApp. Fill in your details below to
              complete registration.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleRegister} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="name">Full Name</Label>
                <div className="relative">
                  <User className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    id="name"
                    type="text"
                    placeholder="Jan de Vries"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    className="pl-10"
                    required
                    disabled={isSubmitting}
                  />
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="password">Password</Label>
                <div className="relative">
                  <Lock className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    id="password"
                    type="password"
                    placeholder="Min. 6 characters"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="pl-10"
                    required
                    minLength={6}
                    disabled={isSubmitting}
                  />
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="confirmPassword">Confirm Password</Label>
                <div className="relative">
                  <Lock className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    id="confirmPassword"
                    type="password"
                    placeholder="Re-enter your password"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    className="pl-10"
                    required
                    minLength={6}
                    disabled={isSubmitting}
                  />
                </div>
              </div>

              <Button
                type="submit"
                className="w-full"
                disabled={
                  isSubmitting || !name || !password || !confirmPassword
                }
              >
                {isSubmitting ? "Creating account..." : "Create Account"}
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
