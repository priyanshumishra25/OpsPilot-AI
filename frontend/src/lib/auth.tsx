import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, use, useCallback, useMemo, useState, type ReactNode } from "react";
import { api, configureApi } from "./api";
import type { Me } from "./types";

const STORAGE_KEY = "opspilot.session";

interface Session { token: string; expiresAt: string }

interface AuthState {
  session: Session | null;
  me: Me | undefined;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  can: (permission: string) => boolean;
}

const AuthContext = createContext<AuthState | null>(null);

function loadSession(): Session | null {
  // sessionStorage: the token survives reloads but not the browser tab; see SECURITY.md for the trade-off.
  const raw = sessionStorage.getItem(STORAGE_KEY);
  if (!raw) return null;
  try {
    const s = JSON.parse(raw) as Session;
    return new Date(s.expiresAt).getTime() > Date.now() ? s : null;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [session, setSession] = useState<Session | null>(loadSession);

  const logout = useCallback(() => {
    sessionStorage.removeItem(STORAGE_KEY);
    setSession(null);
    queryClient.clear();
  }, [queryClient]);

  configureApi(() => session?.token ?? null, logout);

  const meQuery = useQuery({
    queryKey: ["me", session?.token],
    queryFn: () => api<Me>("/api/v1/me"),
    enabled: session !== null,
    staleTime: 60_000,
  });

  const login = useCallback(async (email: string, password: string) => {
    const res = await api<{ access_token: string; expires_at: string }>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    const s = { token: res.access_token, expiresAt: res.expires_at };
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(s));
    setSession(s);
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      session,
      me: meQuery.data,
      login,
      logout,
      can: (p: string) => meQuery.data?.permissions.includes(p) ?? false,
    }),
    [session, meQuery.data, login, logout],
  );
  return <AuthContext value={value}>{children}</AuthContext>;
}

export function useAuth(): AuthState {
  const ctx = use(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
