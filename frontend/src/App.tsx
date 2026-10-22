import { Navigate, Route, Routes } from "react-router";
import { Layout } from "./components/Layout";
import { useAuth } from "./lib/auth";
import { ActivityPage } from "./pages/ActivityPage";
import { ApprovalsPage } from "./pages/ApprovalsPage";
import { IncidentsPage } from "./pages/IncidentsPage";
import { IntegrationsPage } from "./pages/IntegrationsPage";
import { InvestigatePage } from "./pages/InvestigatePage";
import { AskPage } from "./pages/AskPage";
import { KnowledgePage } from "./pages/KnowledgePage";
import { LoginPage } from "./pages/LoginPage";
import { OverviewPage } from "./pages/OverviewPage";

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { session } = useAuth();
  return session ? children : <Navigate to="/login" replace />;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth><Layout /></RequireAuth>}>
        <Route index element={<OverviewPage />} />
        <Route path="ask" element={<AskPage />} />
        <Route path="investigate" element={<InvestigatePage />} />
        <Route path="investigate/:id" element={<InvestigatePage />} />
        <Route path="incidents" element={<IncidentsPage />} />
        <Route path="incidents/:id" element={<IncidentsPage />} />
        <Route path="approvals" element={<ApprovalsPage />} />
        <Route path="integrations" element={<IntegrationsPage />} />
        <Route path="knowledge" element={<KnowledgePage />} />
        <Route path="activity" element={<ActivityPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
