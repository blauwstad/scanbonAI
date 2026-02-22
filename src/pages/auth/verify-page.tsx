import { useEffect, useRef } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import { authApi } from "@/api/client";
import { useAuthStore } from "@/stores/auth-store";
import { FullPageSpinner } from "@/components/ui/spinner";
import toast from "react-hot-toast";

export function VerifyPage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { setAuth } = useAuthStore();
  const hasVerified = useRef(false);

  useEffect(() => {
    const token = searchParams.get("token");
    if (!token || hasVerified.current) return;

    hasVerified.current = true;

    authApi
      .verifyToken({ token })
      .then((res) => {
        const { user, access_token, refresh_token } = res.data;
        localStorage.setItem("refresh_token", refresh_token);
        setAuth(user, access_token);

        toast.success(`Welcome, ${user.name}!`);

        // Route based on role
        if (user.role === "admin") {
          navigate("/admin", { replace: true });
        } else {
          navigate("/invoices", { replace: true });
        }
      })
      .catch(() => {
        toast.error("Invalid or expired link. Please request a new one.");
        navigate("/", { replace: true });
      });
  }, [searchParams, setAuth, navigate]);

  return <FullPageSpinner />;
}
