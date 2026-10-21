import { useState, type SyntheticEvent } from "react";
import { Navigate } from "react-router";
import { Button, ErrorBanner } from "../components/ui";
import { useAuth } from "../lib/auth";

export function LoginPage() {
  const { session, login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  if (session) return <Navigate to="/" replace />;

  async function submit(e: SyntheticEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-full place-items-center px-4">
      <form onSubmit={(e) => void submit(e)} className="w-full max-w-sm space-y-4 rounded-lg border border-line bg-panel p-6" aria-labelledby="login-title">
        <div>
          <h1 id="login-title" className="text-lg font-semibold">Sign in to OpsPilot</h1>
          <p className="text-[13px] text-ink-3">Engineering operations copilot</p>
        </div>
        {error !== null && <ErrorBanner error={error} />}
        <label className="block text-[13px]">
          <span className="text-ink-2">Email</span>
          <input type="email" required autoComplete="username" value={email} onChange={(e) => { setEmail(e.target.value); }}
            className="mt-1 w-full rounded-md border border-line bg-canvas px-3 py-2 text-[14px]" />
        </label>
        <label className="block text-[13px]">
          <span className="text-ink-2">Password</span>
          <input type="password" required autoComplete="current-password" value={password} onChange={(e) => { setPassword(e.target.value); }}
            className="mt-1 w-full rounded-md border border-line bg-canvas px-3 py-2 text-[14px]" />
        </label>
        <Button type="submit" loading={busy} className="w-full">Sign in</Button>
      </form>
    </div>
  );
}
