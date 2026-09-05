import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { WorkPage } from './pages/Work';
import './tokens.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <WorkPage />
  </StrictMode>,
);
