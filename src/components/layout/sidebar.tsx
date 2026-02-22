import { Link, useLocation } from "react-router-dom";
import { cn } from "@/lib/utils";
import { useAuthStore } from "@/stores/auth-store";
import {
  FileText,
  LayoutDashboard,
  BarChart3,
  Download,
  LogOut,
  MessageSquare,
  Receipt,
  Users,
} from "lucide-react";
import { Button } from "@/components/ui/button";

interface NavItem {
  label: string;
  href: string;
  icon: React.ElementType;
}

const userNav: NavItem[] = [
  { label: "Invoices", href: "/invoices", icon: FileText },
];

const adminNav: NavItem[] = [
  { label: "Dashboard", href: "/admin", icon: LayoutDashboard },
  { label: "Invoices", href: "/admin/invoices", icon: Receipt },
  { label: "Metrics", href: "/admin/metrics", icon: BarChart3 },
  { label: "Export", href: "/admin/export", icon: Download },
  { label: "WhatsApp", href: "/admin/whatsapp", icon: MessageSquare },
  { label: "Clients", href: "/admin/clients", icon: Users },
];

export function Sidebar() {
  const location = useLocation();
  const { user, logout } = useAuthStore();

  const isAdmin = user?.role === "admin";
  const navItems = isAdmin ? adminNav : userNav;

  return (
    <aside className="flex h-screen w-64 flex-col border-r bg-card">
      {/* Brand */}
      <div className="flex h-16 items-center gap-2 border-b px-6">
        <Receipt className="h-6 w-6 text-primary" />
        <span className="text-lg font-bold">ScanbonAI</span>
      </div>

      {/* Navigation */}
      <nav className="flex-1 space-y-1 p-4">
        {navItems.map((item) => {
          const isActive =
            location.pathname === item.href ||
            (item.href !== "/admin" &&
              location.pathname.startsWith(item.href));

          return (
            <Link
              key={item.href}
              to={item.href}
              className={cn(
                "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                isActive
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
              )}
            >
              <item.icon className="h-4 w-4" />
              {item.label}
            </Link>
          );
        })}
      </nav>

      {/* User info + logout */}
      <div className="border-t p-4">
        <div className="mb-2 text-sm">
          <div className="font-medium">{user?.name}</div>
          <div className="text-muted-foreground text-xs">{user?.email}</div>
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="w-full justify-start gap-2"
          onClick={logout}
        >
          <LogOut className="h-4 w-4" />
          Sign out
        </Button>
      </div>
    </aside>
  );
}
