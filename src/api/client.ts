import axios, {
  type AxiosInstance,
  type AxiosError,
  type InternalAxiosRequestConfig,
} from "axios";
import type {
  ApiResponse,
  PaginatedResponse,
  Invoice,
  InvoiceMetadata,
  InvoiceFilters,
  AdminInvoiceFilters,
  AuthResponse,
  LoginRequest,
  RegisterRequest,
  VerifyTokenRequest,
  UserCorrection,
  AdminReview,
  AdminAction,
  FieldCorrection,
  ExportRequest,
  ExportResult,
  User,
  ApiError,
} from "@/types";

// ─────────────────────────────────────────────────────────────
// Axios Instance
// ─────────────────────────────────────────────────────────────

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

const apiClient: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    "Content-Type": "application/json",
  },
  timeout: 30_000,
});

// ─────────────────────────────────────────────────────────────
// Request Interceptor: Auth Token Injection
// ─────────────────────────────────────────────────────────────

apiClient.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const token = localStorage.getItem("access_token");
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error: AxiosError) => Promise.reject(error),
);

// ─────────────────────────────────────────────────────────────
// Response Interceptor: Error Normalization & Token Refresh
// ─────────────────────────────────────────────────────────────

apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<ApiError>) => {
    const status = error.response?.status;

    if (status === 401) {
      // Attempt token refresh
      const refreshToken = localStorage.getItem("refresh_token");
      if (refreshToken && !error.config?.url?.includes("/auth/refresh")) {
        try {
          const { data } = await axios.post<AuthResponse>(
            `${API_BASE_URL}/auth/refresh`,
            { refresh_token: refreshToken },
          );
          localStorage.setItem("access_token", data.access_token);
          localStorage.setItem("refresh_token", data.refresh_token);

          // Retry original request with new token
          if (error.config) {
            error.config.headers.Authorization = `Bearer ${data.access_token}`;
            return apiClient.request(error.config);
          }
        } catch {
          // Refresh failed, clear tokens and redirect to login
          localStorage.removeItem("access_token");
          localStorage.removeItem("refresh_token");
          window.location.href = "/";
        }
      } else {
        localStorage.removeItem("access_token");
        localStorage.removeItem("refresh_token");
        window.location.href = "/";
      }
    }

    const apiError: ApiError = {
      status: status ?? 500,
      code: error.response?.data?.code ?? "UNKNOWN_ERROR",
      message:
        error.response?.data?.message ?? error.message ?? "An error occurred",
      details: error.response?.data?.details,
    };

    return Promise.reject(apiError);
  },
);

// ─────────────────────────────────────────────────────────────
// Auth API
// ─────────────────────────────────────────────────────────────

export const authApi = {
  login(data: LoginRequest) {
    return apiClient.post("/auth/login", data).then((r) => r.data);
  },

  register(data: RegisterRequest) {
    return apiClient.post("/auth/register", data).then((r) => r.data);
  },

  verifyToken(data: VerifyTokenRequest): Promise<ApiResponse<AuthResponse>> {
    return apiClient.post("/auth/verify", data).then((r) => r.data);
  },

  logout(): Promise<void> {
    return apiClient.post("/auth/logout").then(() => {
      localStorage.removeItem("access_token");
      localStorage.removeItem("refresh_token");
    });
  },

  getMe(): Promise<ApiResponse<User>> {
    return apiClient.get("/auth/me").then((r) => r.data);
  },
};

// ─────────────────────────────────────────────────────────────
// Invoice API (User)
// ─────────────────────────────────────────────────────────────

export const invoiceApi = {
  list(
    filters: InvoiceFilters = {},
  ): Promise<PaginatedResponse<Invoice>> {
    return apiClient
      .get("/invoices", { params: filters })
      .then((r) => r.data);
  },

  getById(id: string): Promise<ApiResponse<Invoice>> {
    return apiClient.get(`/invoices/${id}`).then((r) => r.data);
  },

  getImageUrl(id: string): Promise<ApiResponse<{ signed_url: string }>> {
    return apiClient.get(`/invoices/${id}/image`).then((r) => r.data);
  },

  submitCorrections(
    id: string,
    corrections: {
      corrected_metadata: Partial<InvoiceMetadata>;
      field_corrections: FieldCorrection[];
    },
  ): Promise<ApiResponse<UserCorrection>> {
    return apiClient
      .put(`/invoices/${id}/corrections`, corrections)
      .then((r) => r.data);
  },

  confirmAsCorrect(id: string): Promise<ApiResponse<Invoice>> {
    return apiClient
      .post(`/invoices/${id}/confirm`)
      .then((r) => r.data);
  },
};

// ─────────────────────────────────────────────────────────────
// Admin API
// ─────────────────────────────────────────────────────────────

export const adminApi = {
  listInvoices(
    filters: AdminInvoiceFilters = {},
  ): Promise<PaginatedResponse<Invoice>> {
    return apiClient
      .get("/admin/invoices", { params: filters })
      .then((r) => r.data);
  },

  getInvoice(id: string): Promise<ApiResponse<Invoice>> {
    return apiClient.get(`/admin/invoices/${id}`).then((r) => r.data);
  },

  submitReview(
    invoiceId: string,
    review: {
      action: AdminAction;
      overridden_fields?: FieldCorrection[];
      notes?: string;
    },
  ): Promise<ApiResponse<AdminReview>> {
    return apiClient
      .post(`/admin/invoices/${invoiceId}/review`, review)
      .then((r) => r.data);
  },

  getMetrics(params?: {
    period?: "day" | "week" | "month" | "quarter";
    from_date?: string;
    to_date?: string;
  }): Promise<ApiResponse<any>> {
    return apiClient
      .get("/admin/metrics", { params })
      .then((r) => r.data);
  },

  exportInvoices(
    request: ExportRequest,
  ): Promise<ApiResponse<ExportResult>> {
    return apiClient
      .post("/admin/export", request)
      .then((r) => r.data);
  },

  // Client management
  listClients(params?: { page?: number; page_size?: number; search?: string; status?: string; plan?: string }) {
    return apiClient.get("/admin/clients", { params }).then((r) => r.data);
  },
  getClient(id: string) {
    return apiClient.get(`/admin/clients/${id}`).then((r) => r.data);
  },
  updateClient(id: string, data: any) {
    return apiClient.put(`/admin/clients/${id}`, data).then((r) => r.data);
  },
  enrichClient(id: string, registryType: string) {
    return apiClient.post(`/admin/clients/${id}/enrich`, { registry_type: registryType }).then((r) => r.data);
  },
  setClientConsent(id: string, consent: boolean) {
    return apiClient.post(`/admin/clients/${id}/consent`, { consent }).then((r) => r.data);
  },
  suspendClient(id: string) {
    return apiClient.post(`/admin/clients/${id}/suspend`).then((r) => r.data);
  },
  reactivateClient(id: string) {
    return apiClient.post(`/admin/clients/${id}/reactivate`).then((r) => r.data);
  },
  grantCredits(id: string, amount: number, reason?: string) {
    return apiClient.post(`/admin/clients/${id}/grant-credits`, { amount, reason }).then((r) => r.data);
  },

  // OpenClaw WhatsApp gateway
  getOpenClawStatus(): Promise<ApiResponse<any>> {
    return apiClient.get("/admin/openclaw/status").then((r) => r.data);
  },
  testOpenClawConnection(): Promise<ApiResponse<any>> {
    return apiClient.post("/admin/openclaw/test").then((r) => r.data);
  },
};

// ─────────────────────────────────────────────────────────────
// FUTURE: Expert API (stubs)
// ─────────────────────────────────────────────────────────────

export const expertApi = {
  getQueue: (_params?: {
    status?: string;
    priority?: string;
  }) => {
    throw new Error("Expert API not yet implemented");
  },

  getAssignment: (_id: string) => {
    throw new Error("Expert API not yet implemented");
  },

  submitReview: (
    _assignmentId: string,
    _review: {
      corrected_metadata: Partial<InvoiceMetadata>;
      field_corrections: FieldCorrection[];
      strategy_notes: string;
      confidence_assessment: number;
      time_spent_minutes: number;
    },
  ) => {
    throw new Error("Expert API not yet implemented");
  },

  getDashboard: () => {
    throw new Error("Expert API not yet implemented");
  },

  getPayouts: (_params?: { page?: number; page_size?: number }) => {
    throw new Error("Expert API not yet implemented");
  },
};

export default apiClient;
