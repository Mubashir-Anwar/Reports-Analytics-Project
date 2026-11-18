import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import AnalyticsApp from './AnalyticsApp.jsx';
import '../shared/theme.css';
import '../portal/portal.css';
import './analytics.css';

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <AnalyticsApp />
  </StrictMode>,
);
