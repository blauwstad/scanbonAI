import { useState, useEffect, useCallback } from "react";
import apiClient from "@/api/client";
import { Header } from "@/components/layout/header";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { CheckCircle, AlertCircle, Copy, ExternalLink } from "lucide-react";

interface WhatsAppSettings {
  phone_number_id: string;
  access_token: string;
  display_phone_number?: string;
  waba_id?: string;
  meta_app_id?: string;
}

interface WebhookInfo {
  webhook_url: string;
  verify_token: string;
}

interface ConnectionTestResult {
  verified_name: string;
  display_phone_number: string;
  quality_rating: string;
}

export function WhatsAppSetupPage() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [hasExisting, setHasExisting] = useState(false);

  // Form state
  const [phoneNumberId, setPhoneNumberId] = useState("");
  const [accessToken, setAccessToken] = useState("");
  const [displayPhoneNumber, setDisplayPhoneNumber] = useState("");
  const [wabaId, setWabaId] = useState("");
  const [metaAppId, setMetaAppId] = useState("");

  // Webhook info
  const [webhookInfo, setWebhookInfo] = useState<WebhookInfo | null>(null);

  // Connection test
  const [testResult, setTestResult] = useState<ConnectionTestResult | null>(
    null,
  );
  const [testError, setTestError] = useState<string | null>(null);

  // Save feedback
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Clipboard feedback
  const [copiedField, setCopiedField] = useState<string | null>(null);

  // Fetch current settings on mount
  useEffect(() => {
    async function fetchSettings() {
      try {
        const response = await apiClient.get("/admin/whatsapp-settings");
        const settings = response.data?.data;
        if (settings) {
          setPhoneNumberId(settings.phone_number_id ?? "");
          setAccessToken(settings.access_token ?? "");
          setDisplayPhoneNumber(settings.display_phone_number ?? "");
          setWabaId(settings.waba_id ?? "");
          setMetaAppId(settings.meta_app_id ?? "");
          setHasExisting(true);

          // Also fetch webhook info if settings exist
          fetchWebhookInfo();
        }
      } catch {
        // No settings exist yet, that's fine
      } finally {
        setLoading(false);
      }
    }
    fetchSettings();
  }, []);

  const fetchWebhookInfo = useCallback(async () => {
    try {
      const response = await apiClient.get(
        "/admin/whatsapp-settings/webhook-info",
      );
      const info = response.data?.data;
      if (info) {
        setWebhookInfo(info);
      }
    } catch {
      // Webhook info not available yet
    }
  }, []);

  const handleSave = useCallback(async () => {
    setSaving(true);
    setSaveSuccess(false);
    setSaveError(null);

    const payload: WhatsAppSettings = {
      phone_number_id: phoneNumberId,
      access_token: accessToken,
      ...(displayPhoneNumber && { display_phone_number: displayPhoneNumber }),
      ...(wabaId && { waba_id: wabaId }),
      ...(metaAppId && { meta_app_id: metaAppId }),
    };

    try {
      if (hasExisting) {
        await apiClient.put("/admin/whatsapp-settings", payload);
      } else {
        await apiClient.post("/admin/whatsapp-settings", payload);
        setHasExisting(true);
      }
      setSaveSuccess(true);
      // Fetch webhook info after saving
      fetchWebhookInfo();
    } catch (err: any) {
      setSaveError(err?.message ?? "Failed to save settings");
    } finally {
      setSaving(false);
    }
  }, [
    phoneNumberId,
    accessToken,
    displayPhoneNumber,
    wabaId,
    metaAppId,
    hasExisting,
    fetchWebhookInfo,
  ]);

  const handleTestConnection = useCallback(async () => {
    setTesting(true);
    setTestResult(null);
    setTestError(null);

    try {
      const response = await apiClient.post(
        "/admin/whatsapp-settings/test",
      );
      const result = response.data?.data;
      setTestResult(result);
    } catch (err: any) {
      setTestError(err?.message ?? "Connection test failed");
    } finally {
      setTesting(false);
    }
  }, []);

  const copyToClipboard = useCallback(
    async (text: string, field: string) => {
      try {
        await navigator.clipboard.writeText(text);
        setCopiedField(field);
        setTimeout(() => setCopiedField(null), 2000);
      } catch {
        // Fallback: do nothing
      }
    },
    [],
  );

  const isFormValid = phoneNumberId.trim() !== "" && accessToken.trim() !== "";

  if (loading) {
    return (
      <div>
        <Header
          title="WhatsApp Business Setup"
          description="Configure your WhatsApp Business API integration"
        />
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      </div>
    );
  }

  return (
    <div>
      <Header
        title="WhatsApp Business Setup"
        description="Configure your WhatsApp Business API integration"
      />

      <div className="p-6 max-w-2xl space-y-6">
        {/* Settings Form */}
        <Card>
          <CardHeader>
            <CardTitle>
              {hasExisting ? "Update Settings" : "Configure Settings"}
            </CardTitle>
            <CardDescription>
              Enter your WhatsApp Business API credentials from the Meta
              Developer Console
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="phone-number-id">
                Phone Number ID <span className="text-destructive">*</span>
              </Label>
              <Input
                id="phone-number-id"
                type="text"
                placeholder="e.g. 123456789012345"
                value={phoneNumberId}
                onChange={(e) => setPhoneNumberId(e.target.value)}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="access-token">
                Access Token <span className="text-destructive">*</span>
              </Label>
              <Input
                id="access-token"
                type="password"
                placeholder="Your permanent access token"
                value={accessToken}
                onChange={(e) => setAccessToken(e.target.value)}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="display-phone-number">
                Display Phone Number
              </Label>
              <Input
                id="display-phone-number"
                type="text"
                placeholder="e.g. +49 123 456789"
                value={displayPhoneNumber}
                onChange={(e) => setDisplayPhoneNumber(e.target.value)}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="waba-id">WABA ID</Label>
              <Input
                id="waba-id"
                type="text"
                placeholder="WhatsApp Business Account ID"
                value={wabaId}
                onChange={(e) => setWabaId(e.target.value)}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="meta-app-id">Meta App ID</Label>
              <Input
                id="meta-app-id"
                type="text"
                placeholder="Your Meta App ID"
                value={metaAppId}
                onChange={(e) => setMetaAppId(e.target.value)}
              />
            </div>

            {/* Save feedback */}
            {saveSuccess && (
              <div className="flex items-center gap-2 text-sm text-green-600">
                <CheckCircle className="h-4 w-4" />
                Settings saved successfully
              </div>
            )}
            {saveError && (
              <div className="flex items-center gap-2 text-sm text-destructive">
                <AlertCircle className="h-4 w-4" />
                {saveError}
              </div>
            )}

            <div className="flex gap-3 pt-2">
              <Button
                onClick={handleSave}
                disabled={!isFormValid || saving}
              >
                {saving ? (
                  <>
                    <Spinner className="h-4 w-4 mr-2" />
                    Saving...
                  </>
                ) : hasExisting ? (
                  "Update Settings"
                ) : (
                  "Save Settings"
                )}
              </Button>

              {hasExisting && (
                <Button
                  variant="outline"
                  onClick={handleTestConnection}
                  disabled={testing}
                >
                  {testing ? (
                    <>
                      <Spinner className="h-4 w-4 mr-2" />
                      Testing...
                    </>
                  ) : (
                    "Test Connection"
                  )}
                </Button>
              )}
            </div>
          </CardContent>
        </Card>

        {/* Webhook Info */}
        {webhookInfo && (
          <Card>
            <CardHeader>
              <CardTitle>Webhook Configuration</CardTitle>
              <CardDescription>
                Configure these values in your{" "}
                <a
                  href="https://developers.facebook.com/"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-primary underline underline-offset-4 hover:text-primary/80"
                >
                  Meta Developer Console
                  <ExternalLink className="h-3 w-3" />
                </a>
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label>Webhook URL</Label>
                <div className="flex gap-2">
                  <Input
                    readOnly
                    value={webhookInfo.webhook_url}
                    className="font-mono text-sm bg-muted"
                  />
                  <Button
                    variant="outline"
                    size="icon"
                    onClick={() =>
                      copyToClipboard(webhookInfo.webhook_url, "webhook_url")
                    }
                    title="Copy to clipboard"
                  >
                    {copiedField === "webhook_url" ? (
                      <CheckCircle className="h-4 w-4 text-green-600" />
                    ) : (
                      <Copy className="h-4 w-4" />
                    )}
                  </Button>
                </div>
              </div>

              <div className="space-y-2">
                <Label>Verify Token</Label>
                <div className="flex gap-2">
                  <Input
                    readOnly
                    value={webhookInfo.verify_token}
                    className="font-mono text-sm bg-muted"
                  />
                  <Button
                    variant="outline"
                    size="icon"
                    onClick={() =>
                      copyToClipboard(
                        webhookInfo.verify_token,
                        "verify_token",
                      )
                    }
                    title="Copy to clipboard"
                  >
                    {copiedField === "verify_token" ? (
                      <CheckCircle className="h-4 w-4 text-green-600" />
                    ) : (
                      <Copy className="h-4 w-4" />
                    )}
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Connection Status */}
        {(testResult || testError) && (
          <Card>
            <CardHeader>
              <CardTitle>Connection Status</CardTitle>
            </CardHeader>
            <CardContent>
              {testError ? (
                <div className="flex items-center gap-3 text-destructive">
                  <AlertCircle className="h-5 w-5 flex-shrink-0" />
                  <div>
                    <p className="font-medium">Connection Failed</p>
                    <p className="text-sm">{testError}</p>
                  </div>
                </div>
              ) : testResult ? (
                <div className="space-y-3">
                  <div className="flex items-center gap-2 text-green-600">
                    <CheckCircle className="h-5 w-5" />
                    <span className="font-medium">Connected Successfully</span>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 pt-2">
                    <div>
                      <p className="text-xs text-muted-foreground">
                        Verified Name
                      </p>
                      <p className="text-sm font-medium">
                        {testResult.verified_name}
                      </p>
                    </div>
                    <div>
                      <p className="text-xs text-muted-foreground">
                        Phone Number
                      </p>
                      <p className="text-sm font-medium">
                        {testResult.display_phone_number}
                      </p>
                    </div>
                    <div>
                      <p className="text-xs text-muted-foreground">
                        Quality Rating
                      </p>
                      <p className="text-sm font-medium">
                        {testResult.quality_rating}
                      </p>
                    </div>
                  </div>
                </div>
              ) : null}
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
