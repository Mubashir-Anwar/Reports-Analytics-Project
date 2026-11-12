import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import ServerApp from './ServerApp.jsx';
import '../portal/portal.css';

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <ServerApp />
  </StrictMode>,
);
