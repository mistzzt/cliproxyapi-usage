import { BrowserRouter, Navigate, Outlet, Route, Routes } from 'react-router';
import AppHeader from '@/components/ui/AppHeader';
import UsagePage from '@/pages/UsagePage';
import QuotaPage from '@/pages/QuotaPage';
import { runtimeConfig } from '@/services/runtimeConfig';

function AppLayout() {
  return (
    <>
      <AppHeader />
      <Outlet />
    </>
  );
}

export function AppRouter() {
  return (
    <BrowserRouter basename={runtimeConfig.basePath}>
      <Routes>
        <Route element={<AppLayout />}>
          <Route path="/" element={<UsagePage />} />
          <Route path="/limits" element={<QuotaPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
