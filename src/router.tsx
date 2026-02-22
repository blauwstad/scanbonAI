import { createBrowserRouter } from "react-router-dom";
import { AppLayout } from "@/components/layout/app-layout";
import { AuthGuard } from "@/components/common/auth-guard";

// Auth pages
import { LoginPage } from "@/pages/auth/login-page";
import { RegisterPage } from "@/pages/auth/register-page";
import { VerifyPage } from "@/pages/auth/verify-page";
import { RegisterWhatsAppPage } from "@/pages/auth/register-whatsapp-page";

// User pages
import { InvoiceListPage } from "@/pages/invoices/invoice-list-page";
import { InvoiceReviewPage } from "@/pages/invoices/invoice-review-page";

// Admin pages
import { AdminDashboardPage } from "@/pages/admin/admin-dashboard-page";
import { AdminInvoiceListPage } from "@/pages/admin/admin-invoice-list-page";
import { AdminInvoiceDetailPage } from "@/pages/admin/admin-invoice-detail-page";
import { AdminMetricsPage } from "@/pages/admin/admin-metrics-page";
import { AdminExportPage } from "@/pages/admin/admin-export-page";
import { WhatsAppSetupPage } from "@/pages/admin/whatsapp-setup-page";

export const router = createBrowserRouter([
  // Public routes
  {
    path: "/",
    element: <LoginPage />,
  },
  {
    path: "/register",
    element: <RegisterPage />,
  },
  {
    path: "/register/whatsapp",
    element: <RegisterWhatsAppPage />,
  },
  {
    path: "/auth/verify",
    element: <VerifyPage />,
  },

  // Authenticated user routes
  {
    element: (
      <AuthGuard>
        <AppLayout />
      </AuthGuard>
    ),
    children: [
      {
        path: "/invoices",
        element: <InvoiceListPage />,
      },
      {
        path: "/invoices/:id",
        element: <InvoiceReviewPage />,
      },
    ],
  },

  // Admin routes
  {
    element: (
      <AuthGuard requiredRole="admin">
        <AppLayout />
      </AuthGuard>
    ),
    children: [
      {
        path: "/admin",
        element: <AdminDashboardPage />,
      },
      {
        path: "/admin/invoices",
        element: <AdminInvoiceListPage />,
      },
      {
        path: "/admin/invoices/:id",
        element: <AdminInvoiceDetailPage />,
      },
      {
        path: "/admin/metrics",
        element: <AdminMetricsPage />,
      },
      {
        path: "/admin/export",
        element: <AdminExportPage />,
      },
      {
        path: "/admin/whatsapp",
        element: <WhatsAppSetupPage />,
      },
    ],
  },

  // FUTURE: Expert routes (stubs)
  // {
  //   element: (
  //     <AuthGuard requiredRole="expert">
  //       <AppLayout />
  //     </AuthGuard>
  //   ),
  //   children: [
  //     { path: "/expert", element: <ExpertDashboard /> },
  //     { path: "/expert/queue", element: <ExpertQueue /> },
  //     { path: "/expert/review/:id", element: <ExpertReviewPage /> },
  //   ],
  // },
]);
