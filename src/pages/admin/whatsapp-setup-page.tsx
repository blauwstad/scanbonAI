import { useState, useEffect, useCallback } from "react";
import { adminApi } from "@/api/client";
import { Header } from "@/components/layout/header";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/spinner";
import {
  CheckCircle,
  AlertCircle,
  Wifi,
  WifiOff,
  Phone,
  Globe,
  RefreshCw,
  Info,
} from "lucide-react";

interface OpenClawStatus {
  gateway_reachable: boolean;
  gateway_url: string;
  phone_number: string;
  last_seen?: string;
  session_active?: boolean;
}

interface TestResult {
  success: boolean;
  message: string;
  latency_ms?: number;
}

export function WhatsAppSetupPage() {
  const [loading, setLoading] = useState(true);
  const [testing, setTesting] = useState(false);
  const [status, setStatus] = useState<OpenClawStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  const [testError, setTestError] = useState<string | null>(null);

  const fetchStatus = useCallback(async () => {
    setStatusError(null);
    try {
      const response = await adminApi.getOpenClawStatus();
      setStatus(response.data);
    } catch (err: any) {
      setStatusError(err?.message ?? "Failed to fetch OpenClaw status");
    } finally {
      setLoading(false);
    }
  }, []);

  // Fetch status on mount
  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  const handleTestConnection = useCallback(async () => {
    setTesting(true);
    setTestResult(null);
    setTestError(null);

    try {
      const response = await adminApi.testOpenClawConnection();
      setTestResult(response.data);
    } catch (err: any) {
      setTestError(err?.message ?? "Connection test failed");
    } finally {
      setTesting(false);
    }
  }, []);

  if (loading) {
    return (
      <div>
        <Header
          title="WhatsApp Connection"
          description="OpenClaw gateway status and diagnostics"
        />
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      </div>
    );
  }

  const isConnected = status?.gateway_reachable ?? false;

  return (
    <div>
      <Header
        title="WhatsApp Connection"
        description="OpenClaw gateway status and diagnostics"
      />

      <div className="p-6 max-w-2xl space-y-6">
        {/* Connection Status Card */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              {isConnected ? (
                <Wifi className="h-5 w-5 text-green-600" />
              ) : (
                <WifiOff className="h-5 w-5 text-destructive" />
              )}
              Connection Status
            </CardTitle>
            <CardDescription>
              Current state of the WhatsApp connection via OpenClaw
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {statusError ? (
              <div className="flex items-center gap-3 text-destructive">
                <AlertCircle className="h-5 w-5 flex-shrink-0" />
                <div>
                  <p className="font-medium">Unable to fetch status</p>
                  <p className="text-sm">{statusError}</p>
                </div>
              </div>
            ) : (
              <>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-muted-foreground">Status</span>
                  <Badge variant={isConnected ? "success" : "destructive"}>
                    {isConnected ? "Connected" : "Disconnected"}
                  </Badge>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-sm text-muted-foreground flex items-center gap-2">
                    <Phone className="h-4 w-4" />
                    Phone Number
                  </span>
                  <span className="text-sm font-medium font-mono">
                    {status?.phone_number ?? "+31618395043"}
                  </span>
                </div>

                {status?.last_seen && (
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-muted-foreground">
                      Last Seen
                    </span>
                    <span className="text-sm font-medium">
                      {status.last_seen}
                    </span>
                  </div>
                )}
              </>
            )}
          </CardContent>
        </Card>

        {/* Gateway Health Card */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Globe className="h-5 w-5" />
              Gateway Health
            </CardTitle>
            <CardDescription>
              OpenClaw gateway endpoint information
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">Gateway URL</span>
              <span className="text-sm font-medium font-mono">
                {status?.gateway_url ?? "N/A"}
              </span>
            </div>

            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">Reachable</span>
              <Badge variant={isConnected ? "success" : "destructive"}>
                {isConnected ? "Yes" : "No"}
              </Badge>
            </div>

            {status?.session_active !== undefined && (
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">
                  Session Active
                </span>
                <Badge variant={status.session_active ? "success" : "warning"}>
                  {status.session_active ? "Active" : "Inactive"}
                </Badge>
              </div>
            )}

            {/* Test Connection */}
            <div className="pt-2">
              <Button
                onClick={handleTestConnection}
                disabled={testing}
                className="w-full"
              >
                {testing ? (
                  <>
                    <Spinner className="h-4 w-4 mr-2" />
                    Testing...
                  </>
                ) : (
                  <>
                    <RefreshCw className="h-4 w-4 mr-2" />
                    Test Connection
                  </>
                )}
              </Button>
            </div>

            {/* Test Result */}
            {testResult && (
              <div
                className={`flex items-center gap-3 rounded-lg border p-3 ${
                  testResult.success
                    ? "border-green-200 bg-green-50 text-green-800"
                    : "border-red-200 bg-red-50 text-red-800"
                }`}
              >
                {testResult.success ? (
                  <CheckCircle className="h-5 w-5 flex-shrink-0" />
                ) : (
                  <AlertCircle className="h-5 w-5 flex-shrink-0" />
                )}
                <div>
                  <p className="text-sm font-medium">{testResult.message}</p>
                  {testResult.latency_ms !== undefined && (
                    <p className="text-xs mt-0.5">
                      Latency: {testResult.latency_ms}ms
                    </p>
                  )}
                </div>
              </div>
            )}

            {testError && (
              <div className="flex items-center gap-3 rounded-lg border border-red-200 bg-red-50 p-3 text-red-800">
                <AlertCircle className="h-5 w-5 flex-shrink-0" />
                <div>
                  <p className="text-sm font-medium">Test Failed</p>
                  <p className="text-xs mt-0.5">{testError}</p>
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        {/* How to Re-pair Instructions */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Info className="h-5 w-5" />
              How to Re-pair
            </CardTitle>
            <CardDescription>
              Instructions for reconnecting WhatsApp via OpenClaw
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-3 text-sm text-muted-foreground">
              <p>
                WhatsApp is connected through the{" "}
                <span className="font-medium text-foreground">
                  OpenClaw gateway
                </span>
                , which bridges your WhatsApp number to ScanbonAI without
                requiring Meta Business API credentials.
              </p>
              <p>If the connection drops, follow these steps to re-pair:</p>
              <ol className="list-decimal list-inside space-y-2 pl-2">
                <li>
                  Open the OpenClaw dashboard and navigate to the device pairing
                  section.
                </li>
                <li>
                  Scan the QR code with the WhatsApp app on the phone linked to{" "}
                  <span className="font-mono font-medium text-foreground">
                    +31618395043
                  </span>
                  .
                </li>
                <li>
                  Wait for the session to initialize (usually 10-30 seconds).
                </li>
                <li>
                  Return to this page and click{" "}
                  <span className="font-medium text-foreground">
                    Test Connection
                  </span>{" "}
                  to verify.
                </li>
              </ol>
              <p className="pt-1">
                If issues persist, check that the OpenClaw gateway service is
                running and the phone has an active internet connection.
              </p>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
