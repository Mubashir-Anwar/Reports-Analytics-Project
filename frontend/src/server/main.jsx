import '../shared/downloads.js'; // report downloads carry on across pages
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import ServerApp from './ServerApp.jsx';
import '../shared/theme.css';
import '../portal/portal.css';

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <ServerApp />
  </StrictMode>,
);
