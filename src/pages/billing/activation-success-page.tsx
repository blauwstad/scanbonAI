import { CheckCircle2, Receipt, MessageSquare } from "lucide-react";
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
} from "@/components/ui/card";

export function ActivationSuccessPage() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/30 p-4">
      <div className="w-full max-w-md">
        {/* Brand header */}
        <div className="mb-6 flex items-center justify-center gap-2">
          <Receipt className="h-8 w-8 text-primary" />
          <span className="text-2xl font-bold">ScanbonAI</span>
        </div>

        <Card>
          <CardHeader className="text-center">
            <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-green-100">
              <CheckCircle2 className="h-10 w-10 text-green-600" />
            </div>
            <CardTitle className="text-xl">
              Your Scanbon account is now active!
            </CardTitle>
          </CardHeader>

          <CardContent className="space-y-4 text-center">
            <p className="text-muted-foreground">
              Your payment has been processed and your account is ready to use.
            </p>

            <div className="flex items-center justify-center gap-2 rounded-lg bg-muted/50 p-4">
              <MessageSquare className="h-5 w-5 text-primary" />
              <p className="text-sm font-medium">
                You can close this page and return to WhatsApp to start
                processing invoices.
              </p>
            </div>

            <p className="text-xs text-muted-foreground">
              Simply send a photo of an invoice to get started. ScanbonAI will
              automatically extract and organize the data for you.
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
