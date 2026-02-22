import { useState, useCallback } from "react";
import { useNavigate, Link } from "react-router-dom";
import { Receipt, Phone, Lock, User, Shield, KeyRound } from "lucide-react";
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

export function RegisterPage() {
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<"user" | "admin">("user");
  const [adminCode, setAdminCode] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const navigate = useNavigate();
  const { setAuth } = useAuthStore();

  const handleRegister = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!name || !phone || !password) return;

      setIsSubmitting(true);
      try {
        const payload: Record<string, string> = { name, phone, password, role };
        if (role === "admin" && adminCode) {
          payload.admin_invite_code = adminCode;
        }

        const { data: res } = await apiClient.post("/auth/register", payload);
        const { user, access_token, refresh_token } = res.data;
        localStorage.setItem("refresh_token", refresh_token);
        setAuth(user, access_token);
        toast.success(`Account created! Welcome, ${user.name}!`);
        navigate(user.role === "admin" ? "/admin" : "/invoices", {
          replace: true,
        });
      } catch (err: any) {
        const msg = err?.message || "Registration failed. Please try again.";
        toast.error(msg);
      } finally {
        setIsSubmitting(false);
      }
    },
    [name, phone, password, role, adminCode, setAuth, navigate],
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
            Create your account
          </p>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Register</CardTitle>
            <CardDescription>
              Fill in your details to create an account
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleRegister} className="space-y-4">
              {/* Role selector */}
              <div className="grid grid-cols-2 gap-2">
                <Button
                  type="button"
                  variant={role === "user" ? "default" : "outline"}
                  className="gap-2"
                  onClick={() => setRole("user")}
                  disabled={isSubmitting}
                >
                  <User className="h-4 w-4" />
                  User
                </Button>
                <Button
                  type="button"
                  variant={role === "admin" ? "default" : "outline"}
                  className="gap-2"
                  onClick={() => setRole("admin")}
                  disabled={isSubmitting}
                >
                  <Shield className="h-4 w-4" />
                  Admin
                </Button>
              </div>

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
                <Label htmlFor="phone">Phone Number</Label>
                <div className="relative">
                  <Phone className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    id="phone"
                    type="tel"
                    placeholder="+31600000001"
                    value={phone}
                    onChange={(e) => setPhone(e.target.value)}
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

              {role === "admin" && (
                <div className="space-y-2">
                  <Label htmlFor="adminCode">Admin Invite Code</Label>
                  <div className="relative">
                    <KeyRound className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      id="adminCode"
                      type="text"
                      placeholder="Enter invite code"
                      value={adminCode}
                      onChange={(e) => setAdminCode(e.target.value)}
                      className="pl-10"
                      required
                      disabled={isSubmitting}
                    />
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Contact your organization to get the admin invite code.
                  </p>
                </div>
              )}

              <Button
                type="submit"
                className="w-full"
                disabled={
                  isSubmitting ||
                  !name ||
                  !phone ||
                  !password ||
                  (role === "admin" && !adminCode)
                }
              >
                {isSubmitting ? "Creating account..." : "Create Account"}
              </Button>
            </form>
          </CardContent>
        </Card>

        <p className="mt-6 text-center text-sm text-muted-foreground">
          Already have an account?{" "}
          <Link to="/" className="text-primary hover:underline font-medium">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
