import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '@xterm/xterm/css/xterm.css';
import { AgentPage } from './pages/Agent';
import './tokens.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AgentPage />
  </StrictMode>
);
