import { useState, useCallback } from "react";
import { Receipt, Mail } from "lucide-react";
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
import { authApi } from "@/api/client";
import toast from "react-hot-toast";

export function LoginPage() {
  const [email, setEmail] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isLinkSent, setIsLinkSent] = useState(false);

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!email.trim()) return;

      setIsSubmitting(true);
      try {
        await authApi.login({ email: email.trim() });
        setIsLinkSent(true);
        toast.success("Magic link sent! Check your email.");
      } catch {
        toast.error("Failed to send login link. Please try again.");
      } finally {
        setIsSubmitting(false);
      }
    },
    [email],
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
            <CardTitle>Sign in</CardTitle>
            <CardDescription>
              {isLinkSent
                ? "Check your inbox for the magic link"
                : "Enter your email to receive a magic link"}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {isLinkSent ? (
              <div className="space-y-4 text-center">
                <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-green-100">
                  <Mail className="h-6 w-6 text-green-600" />
                </div>
                <p className="text-sm text-muted-foreground">
                  We sent a login link to{" "}
                  <span className="font-medium text-foreground">{email}</span>.
                  Click the link in the email to sign in.
                </p>
                <Button
                  variant="ghost"
                  onClick={() => {
                    setIsLinkSent(false);
                    setEmail("");
                  }}
                >
                  Use a different email
                </Button>
              </div>
            ) : (
              <form onSubmit={handleSubmit} className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="email">Email address</Label>
                  <Input
                    id="email"
                    type="email"
                    placeholder="you@company.com"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    required
                    autoFocus
                  />
                </div>
                <Button
                  type="submit"
                  className="w-full"
                  disabled={isSubmitting || !email.trim()}
                >
                  {isSubmitting ? "Sending..." : "Send Magic Link"}
                </Button>
              </form>
            )}
          </CardContent>
        </Card>

        <p className="mt-6 text-center text-xs text-muted-foreground">
          Simply send your invoice photos to our WhatsApp number and manage them
          here.
        </p>
      </div>
    </div>
  );
}
