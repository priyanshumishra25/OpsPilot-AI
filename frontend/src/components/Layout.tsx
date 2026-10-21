import clsx from "clsx";
import { BookOpenText, LayoutDashboard, LogOut, MessageSquareText, Moon, ScrollText, Sun } from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router";
import { useAuth } from "../lib/auth";

const NAV = [
  { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/ask", label: "Ask", icon: MessageSquareText },
  { to: "/knowledge", label: "Knowledge Base", icon: BookOpenText },
  { to: "/activity", label: "Activity", icon: ScrollText, permission: "audit:read" },
] as const;

function useTheme(): [string, () => void] {
  const [theme, setTheme] = useState(() => localStorage.getItem("opspilot.theme") ?? "dark");
  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    localStorage.setItem("opspilot.theme", theme);
  }, [theme]);
  return [theme, () => { setTheme((t) => (t === "dark" ? "light" : "dark")); }];
}

export function Layout() {
  const { me, logout, can } = useAuth();
  const [theme, toggle] = useTheme();
  const workspace = me?.workspaces.find((w) => w.id === me.active_workspace_id);
  return (
    <div className="flex h-full flex-col md:flex-row">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-panel focus:px-3 focus:py-2">Skip to content</a>
      <aside className="flex shrink-0 flex-col border-b border-line bg-panel md:w-56 md:border-b-0 md:border-r">
        <div className="flex items-center gap-2 px-4 py-3">
          <div className="grid size-7 place-items-center rounded-md bg-accent text-[12px] font-bold text-accent-ink" aria-hidden>OP</div>
          <div className="min-w-0">
            <p className="text-[13px] font-semibold">OpsPilot AI</p>
            <p className="truncate text-[11px] text-ink-3">{workspace?.name ?? "…"}</p>
          </div>
        </div>
        <nav aria-label="Primary" className="flex gap-1 overflow-x-auto px-2 pb-2 md:flex-col md:pb-0">
          {NAV.filter((n) => !("permission" in n) || can(n.permission)).map(({ to, label, icon: Icon, ...rest }) => (
            <NavLink
              key={to}
              to={to}
              end={"end" in rest}
              className={({ isActive }) =>
                clsx(
                  "flex items-center gap-2 whitespace-nowrap rounded-md px-2.5 py-1.5 text-[13px]",
                  isActive ? "bg-panel-2 font-medium text-ink" : "text-ink-2 hover:bg-panel-2 hover:text-ink",
                )
              }
            >
              <Icon className="size-4" aria-hidden />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto hidden border-t border-line px-3 py-3 md:block">
          <p className="truncate text-[12px] text-ink-2">{me?.email}</p>
          <p className="text-[11px] text-ink-3">{me?.role} · scopes: {me?.access_scopes.join(", ")}</p>
          <div className="mt-2 flex gap-1">
            <button onClick={toggle} className="rounded p-1.5 text-ink-3 hover:bg-panel-2 hover:text-ink" aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}>
              {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
            </button>
            <button onClick={logout} className="rounded p-1.5 text-ink-3 hover:bg-panel-2 hover:text-ink" aria-label="Sign out">
              <LogOut className="size-4" />
            </button>
          </div>
        </div>
      </aside>
      <main id="main" className="min-w-0 flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3 border-b border-line px-6 py-4">
      <div>
        <h1 className="text-lg font-semibold">{title}</h1>
        {description && <p className="mt-0.5 text-[13px] text-ink-3">{description}</p>}
      </div>
      {actions}
    </div>
  );
}
