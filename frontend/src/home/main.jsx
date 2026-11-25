import '../shared/downloads.js'; // report downloads carry on across pages
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import HomeApp from './HomeApp.jsx';
import '../shared/theme.css';
import '../portal/portal.css';
import './home.css';

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <HomeApp />
  </StrictMode>,
);
