import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { AnalyticsPage } from './pages/Analytics';
import './tokens.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AnalyticsPage />
  </StrictMode>
);
