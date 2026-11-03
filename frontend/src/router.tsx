import { BrowserRouter, Navigate, Route, Routes } from 'react-router';
import AppLayout from '@/components/ui/AppLayout';
import UsagePage from '@/pages/UsagePage';
import QuotaPage from '@/pages/QuotaPage';
import { runtimeConfig } from '@/services/runtimeConfig';

export function AppRouter() {
  return (
    <BrowserRouter basename={runtimeConfig.basePath}>
      <Routes>
        <Route element={<AppLayout />}>
          <Route path="/usage" element={<UsagePage />} />
          <Route path="/limits" element={<QuotaPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/usage" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
