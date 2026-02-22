import { useEffect } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuthStore } from "@/stores/auth-store";
import { authApi } from "@/api/client";
import { FullPageSpinner } from "@/components/ui/spinner";
import type { UserRole } from "@/types";

interface AuthGuardProps {
  children: React.ReactNode;
  requiredRole?: UserRole;
}

export function AuthGuard({ children, requiredRole }: AuthGuardProps) {
  const { isAuthenticated, isLoading, user, setAuth, setLoading, logout } =
    useAuthStore();
  const location = useLocation();

  useEffect(() => {
    const token = localStorage.getItem("access_token");
    if (token && !isAuthenticated && !user) {
      setLoading(true);
      authApi
        .getMe()
        .then((res) => {
          setAuth(res.data, token);
        })
        .catch(() => {
          logout();
        });
    } else if (!token) {
      setLoading(false);
    }
  }, [isAuthenticated, user, setAuth, setLoading, logout]);

  if (isLoading) {
    return <FullPageSpinner />;
  }

  if (!isAuthenticated) {
    return <Navigate to="/" state={{ from: location }} replace />;
  }

  if (requiredRole && user?.role !== requiredRole) {
    // Redirect non-admin users away from admin routes
    return <Navigate to="/invoices" replace />;
  }

  return <>{children}</>;
}
