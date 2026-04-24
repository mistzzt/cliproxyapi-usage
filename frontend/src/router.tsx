import { BrowserRouter, Navigate, Route, Routes } from 'react-router';
import UsagePage from '@/pages/UsagePage';
import QuotaPage from '@/pages/QuotaPage';
import { runtimeConfig } from '@/services/runtimeConfig';

export function AppRouter() {
  return (
    <BrowserRouter basename={runtimeConfig.basePath}>
      <Routes>
        <Route path="/" element={<UsagePage />} />
        <Route path="/quota" element={<QuotaPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
