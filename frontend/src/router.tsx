import { BrowserRouter, Navigate, Route, Routes } from 'react-router';
import UsagePage from '@/pages/UsagePage';
import QuotaPage from '@/pages/QuotaPage';

export function AppRouter() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<UsagePage />} />
        <Route path="/quota" element={<QuotaPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
